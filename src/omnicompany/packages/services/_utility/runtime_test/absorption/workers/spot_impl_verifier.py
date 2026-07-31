# [OMNI] origin=claude-code domain=services/absorption_runtime_test/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.absorption.spot_impl_verifier.agent.py"
"""SpotImplVerifierWorker — Worker #5 (路 3).

挑 spot_impl_count 条提案, 让 LLM 真写实施代码 + 二轮 LLM 判是否解决.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    ReadFileRouter,
    SingleToolRouter,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM_PROMPT = """你是 absorption_runtime_test 路 3 抽样落地验证器.

任务: 拿 N 条 proposal, 对每条:
1. 读对应源码 (用 read_file)
2. 真**写出实施代码** (diff 或修改后代码块) — 你必须给可应用的具体变更
3. 判这次实施是否真解决了 proposal 的 problem (不是表面应付)

如果 proposal 太空泛 LLM 改不出 → 标 implementable=false + 一句话说原因.
如果改出来但只是表面 → truly_solves=false.

最后调 **submit_spot_impl_evidence** 提交所有 attempts.

反模式: 全字段自然语言句子. 禁打分/标签."""


class _PromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        runs_mirror = biz_input.get("_from_SampleRunsExecutorWorker") or {}
        runs = runs_mirror.get("runs") or biz_input.get("runs", [])
        meta_mirror = biz_input.get("_from_TargetIngressWorker") or {}
        sample_input = meta_mirror.get("sample_input") or biz_input.get("sample_input", {})
        spot_count = meta_mirror.get("spot_impl_count") or biz_input.get("spot_impl_count", 2)

        repo_path = sample_input.get("repo_path") or sample_input.get("path") or ""

        # 找第一次成功跑的 proposals 取前 N 条
        proposals: list = []
        for r in runs:
            if r.get("verdict") in ("PASS", "PARTIAL"):
                output = r.get("output") or {}
                proposals = output.get("proposals", [])[:spot_count]
                break

        if not proposals:
            return [{
                "role": "user",
                "content": "## 任务: 抽样落地验证\n\n但 sample_runs 里没成功跑或没 proposals. 直接调 submit_spot_impl_evidence 提交 attempts=[] + groundedness_observation 说明这种情况.",
            }]

        prop_brief = json.dumps(
            [{
                "id": p.get("id"),
                "title": p.get("title"),
                "problem": p.get("problem", "")[:300],
                "proposed_change": p.get("proposed_change", "")[:300],
                "reference_code": p.get("reference_code", {}),
                "risk": p.get("risk", "")[:200],
            } for p in proposals],
            ensure_ascii=False,
            indent=2,
        )

        task = f"""## 抽样落地 · {len(proposals)} 条提案

### 仓库根
- repo_path: `{repo_path}`

### 提案

```json
{prop_brief}
```

### 操作 (对每条 proposal)

1. read_file 读 reference_code.file 完整源码 (file_path = repo_path + "/" + reference_code.file)
2. 真写出修改 (整段或 diff)
3. 判: implementable (写得出来吗) + truly_solves (真解决 problem 吗 vs 表面应付)
4. 收齐所有后, 调 **submit_spot_impl_evidence**

提交字段:
- attempts: list[{{proposal_id, title, implementable, reason_if_not, implementation_excerpt, truly_solves, judge_reason}}]
- implementable_pct, truly_solves_pct, combined_pct (你自己算)
- groundedness_observation (≥30 字符 句子): 提案具体性总评"""

        return [{"role": "user", "content": task}]


class SubmitSpotImplEvidenceRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "submit_spot_impl_evidence"
    DESCRIPTION: ClassVar[str] = "Submit spot implementation evidence."
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "attempts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "title": {"type": "string"},
                        "implementable": {"type": "boolean"},
                        "reason_if_not": {"type": "string"},
                        "implementation_excerpt": {"type": "string"},
                        "truly_solves": {"type": "boolean"},
                        "judge_reason": {"type": "string"},
                    },
                    "required": ["proposal_id", "implementable", "truly_solves"],
                },
            },
            "implementable_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "truly_solves_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "combined_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "groundedness_observation": {"type": "string", "minLength": 30},
        },
        "required": ["attempts", "groundedness_observation"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args, ctx) -> str:
        return f"submitted spot_impl_evidence: {len(args.get('attempts', []))} attempts"


class _ExtractResult(ExtractResultRouter):
    def extract(self, *, final_text, messages, turn_count, stop_reason) -> Verdict:
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "submit_spot_impl_evidence"
                    ):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            attempts = inp.get("attempts", [])
                            # 自动算 pct (容错)
                            if attempts:
                                impl = sum(1 for a in attempts if a.get("implementable")) / len(attempts)
                                solv = sum(1 for a in attempts if a.get("truly_solves")) / len(attempts)
                                inp.setdefault("implementable_pct", impl)
                                inp.setdefault("truly_solves_pct", solv)
                                inp.setdefault("combined_pct", (impl + solv) / 2)
                            else:
                                inp.setdefault("implementable_pct", 0.0)
                                inp.setdefault("truly_solves_pct", 0.0)
                                inp.setdefault("combined_pct", 0.0)
                            return Verdict(
                                kind=VerdictKind.PASS,
                                output=dict(inp),
                                diagnosis=f"路 3 evidence: combined={inp.get('combined_pct'):.0%}",
                                confidence=0.9,
                            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output={},
            diagnosis=f"未调 submit_spot_impl_evidence (turns={turn_count})",
        )


class SpotImplVerifierWorker(AgentNodeLoop):
    DESCRIPTION: ClassVar[str] = "路 3 抽样落地 · LLM 真写实施 + 判是否解决."
    FORMAT_IN: ClassVar[list[str]] = [
        "absorption_runtime_test.sample_runs",
        "absorption_runtime_test.target_metadata",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "absorption_runtime_test.spot_impl_evidence"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [ReadFileRouter, SubmitSpotImplEvidenceRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus
        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any):
        return _PromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any):
        return _ExtractResult(bus=bus)
