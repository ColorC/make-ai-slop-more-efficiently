# [OMNI] origin=claude-code domain=services/absorption_runtime_test/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.absorption.cross_run_stability_verifier.agent.py"
"""CrossRunStabilityVerifierWorker — Worker #3 (路 1).

文件层重叠 (确定性集合) + 主题层重叠 (LLM 单步 submit), 综合判稳定性.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import SingleToolRouter, ToolContext
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM_PROMPT = """你是 absorption_runtime_test 路 1 跨次稳定性验证器.

输入是 N 次跑同一目标团队产出的 proposals 列表. 任务:
1. 看每次的 proposals 主题分布
2. 判跨次主题是否一致 (反复说"代码冗余""错误处理"等同样话题)
3. 输出 stability_observation 自然语言句子 + divergence_signals 列表

文件层重叠率已经程序化算好, 在 prompt 里给你. 你只判主题层 + 综合.

调 submit_cross_run_evidence 提交.

反模式: 禁打分 / 标签. 全字段自然语言句子."""


class _PromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        # fan-in 后 input_data 含 sample_runs + target_metadata 内容
        runs_mirror = biz_input.get("_from_SampleRunsExecutorWorker") or {}
        runs = runs_mirror.get("runs") or biz_input.get("runs", [])
        target_id = biz_input.get("target_team_id", "?")

        # 边界: 成功跑 < 2 时无法判跨次稳定 (2026-04-26 加)
        successful = [r for r in runs if r.get("verdict") in ("PASS", "PARTIAL")]
        if len(successful) < 2:
            failure_reasons = []
            for r in runs:
                if r.get("verdict") not in ("PASS", "PARTIAL"):
                    failure_reasons.append(
                        f"run {r.get('run_id')}: {r.get('verdict')} ({(r.get('diagnosis','') or '')[:200]})"
                    )
            task = f"""## 跨次稳定性 · 不可判定 (成功跑 {len(successful)}/{len(runs)} < 2)

只有 {len(successful)} 次成功跑, 无法对比跨次稳定性. 失败的跑:
{chr(10).join(failure_reasons) or '(无)'}

直接调 **submit_cross_run_evidence** 提交:
- file_overlap_pct: 0 (默认值, 但实际不可计算 — 已在 stability_observation 标明)
- topic_overlap_pct: 0 (同上)
- file_intersection: []
- file_union_size: 0
- stability_observation: "成功跑数 {len(successful)} 次 < 2, 跨次稳定性不可判. target 在 N 次取样中有 {{N - successful}} 次失败, 主要原因: {{briefly state from runs above}}"
- divergence_signals: ["跨次稳定性路不可判定 — 仅 {len(successful)}/{len(runs)} 成功"]
"""
            return [{"role": "user", "content": task}]

        # 算文件层重叠 (程序化)
        file_sets = []
        proposals_list = []
        for r in runs:
            if r.get("verdict") not in ("PASS", "PARTIAL"):
                continue
            output = r.get("output") or {}
            props = output.get("proposals") or []
            proposals_list.append({"run_id": r.get("run_id"), "proposals": props})
            files = {p.get("reference_code", {}).get("file") for p in props if p.get("reference_code")}
            files.discard(None)
            file_sets.append(files)

        if len(file_sets) < 2:
            file_overlap = 0.0
            file_intersection: list[str] = []
            file_union_size = 0
        else:
            inter = file_sets[0].copy()
            for s in file_sets[1:]:
                inter &= s
            uni: set = set()
            for s in file_sets:
                uni |= s
            file_overlap = len(inter) / len(uni) if uni else 0.0
            file_intersection = sorted(inter)
            file_union_size = len(uni)

        # 给 LLM 看主题
        topic_brief = []
        for entry in proposals_list:
            topic_brief.append({
                "run_id": entry["run_id"],
                "titles": [
                    f"{p.get('id','?')}: {p.get('title','')[:120]} | problem: {p.get('problem','')[:120]}"
                    for p in entry["proposals"][:8]
                ],
            })

        task = f"""## 跨次稳定性验证 · target={target_id}

### 已算文件层重叠 (程序化, 不是 LLM 判)

- file_overlap_pct: {file_overlap:.2f}
- file_intersection: {file_intersection}
- file_union_size: {file_union_size}

### N 次 proposals 摘要 (供你判主题层)

```json
{json.dumps(topic_brief, ensure_ascii=False, indent=2)}
```

### 你的任务

调 **submit_cross_run_evidence** 提交:
- file_overlap_pct (透传 {file_overlap:.4f})
- topic_overlap_pct (你判 0-1): N 次 proposals 主题层重叠率. 若多次反复说同样话题 (代码冗余/错误处理/上帝类等) → 高; 若每次说完全不同的话题 → 低
- file_intersection (透传)
- file_union_size (透传)
- stability_observation (≥30 字符): 综合判断, 含**矛盾信号** (e.g. "主题层 80% 但文件层只 25%, 这是假稳定 — 同样的话贴在不同代码上")
- divergence_signals (列表, 句子): 不稳定信号"""

        return [{"role": "user", "content": task}]


class SubmitCrossRunEvidenceRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "submit_cross_run_evidence"
    DESCRIPTION: ClassVar[str] = (
        "Submit cross-run stability evidence. file_overlap_pct passed through. "
        "topic_overlap_pct is your LLM judgment 0-1. stability_observation is a sentence."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_overlap_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "topic_overlap_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "file_intersection": {"type": "array", "items": {"type": "string"}},
            "file_union_size": {"type": "integer", "minimum": 0},
            "stability_observation": {"type": "string", "minLength": 30},
            "divergence_signals": {
                "type": "array",
                "items": {"type": "string", "minLength": 10},
            },
        },
        "required": [
            "file_overlap_pct",
            "topic_overlap_pct",
            "stability_observation",
        ],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        return f"submitted cross_run_evidence: file={args.get('file_overlap_pct')} topic={args.get('topic_overlap_pct')}"


class _ExtractResult(ExtractResultRouter):
    def extract(self, *, final_text, messages, turn_count, stop_reason) -> Verdict:
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "submit_cross_run_evidence"
                    ):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            for forbidden in ("score", "rating", "level", "tier"):
                                if forbidden in inp:
                                    return Verdict(
                                        kind=VerdictKind.FAIL,
                                        output=dict(inp),
                                        diagnosis=f"反模式禁字段: {forbidden}",
                                    )
                            return Verdict(
                                kind=VerdictKind.PASS,
                                output=dict(inp),
                                diagnosis=f"路 1 evidence: file={inp.get('file_overlap_pct'):.0%} topic={inp.get('topic_overlap_pct'):.0%}",
                                confidence=0.9,
                            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output={},
            diagnosis=f"未调 submit_cross_run_evidence (turns={turn_count}, stop={stop_reason})",
        )


class CrossRunStabilityVerifierWorker(AgentNodeLoop):
    DESCRIPTION: ClassVar[str] = "路 1 跨次稳定性 · 文件层程序化 + 主题层 LLM 判 + 综合句子."
    FORMAT_IN: ClassVar[list[str]] = [
        "absorption_runtime_test.sample_runs",
        "absorption_runtime_test.target_metadata",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "absorption_runtime_test.cross_run_evidence"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [SubmitCrossRunEvidenceRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus
        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any):
        return _PromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any):
        return _ExtractResult(bus=bus)
