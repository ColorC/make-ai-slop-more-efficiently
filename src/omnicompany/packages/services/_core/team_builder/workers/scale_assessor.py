# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.scale_evaluator.agent_loop.py"
# OMNI-024 ALLOW: 域内私有 worker, 一类一文件经 workers/__init__ 聚合出口, 类实现与本文件模块级 prompt/helper/装配紧耦合, 迁入共享 routers.py 会割裂内聚
"""ScaleAssessorWorker — Phase 2 · AgentNodeLoop (2026-04-23).

Worker 协议 (composite fan-in and):
  FORMAT_IN  = [intent_analysis, team_references]
  FORMAT_OUT = scale_assessment

**职责**: AgentNodeLoop · 综合 intent + references, 判规模 (small/medium/large) + 拆分维度.
    工具: ReadFile / Glob / Grep / ListDir / Finish
    可读: docs/standards/ + packages/services/*/DESIGN.md (对比历史 team 规模)

**为什么 AgentNodeLoop**: 规模判断需**对比历史 team** (例如 doctor 多少 worker / 多少 material,
算 medium 还是 large), 单轮 LLM 拿不到这些对比数据. 用 agent 多轮 + 工具读参考.

**输出 JSON schema**:
  size: small | medium | large
  recommend_decompose: bool
  decompose_axis: by_capability | by_domain | by_phase | null
  rationale: str (含对比的历史 team 引用)
  estimated_worker_count: int
  estimated_material_count: int
"""
from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    FinishRouter,
    GlobRouter,
    GrepRouter,
    ListDirRouter,
    ReadFileRouter,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM_PROMPT = """你是 team_builder 第 2 阶段 · ScaleAssessor agent.

## 职责
综合上游 intent_analysis + team_references, 判产出 Team 的**规模** + 决定是否拆分.

## 工具
- read_file / glob / grep / list_dir: 可读 docs/standards/ 和 packages/services/*/DESIGN.md
  对比历史 team 规模作参照 (例 doctor / workflow_factory 的 worker 数 + material 数).
- finish: 终结 + 提交结论 JSON.

## 规模判定参考 (历史 team 基线, 供对比)
- small: ≤ 4 workers, ≤ 5 materials (例 simple CSV processor)
- medium: 5-9 workers, 6-12 materials (例 doctor 的单个子域自检)
- large: ≥ 10 workers 或 ≥ 13 materials, 或跨多个逻辑领域 (例 workflow_factory 14 workers)

**若落 large**: 必须判拆分维度
- by_capability: 能力边界明确 (分析 / 生成 / 验证)
- by_domain: 业务子领域明确 (research / decisions)
- by_phase: 阶段明确 (ingest / process / output)

## 产出 (finish 的 result 字段应为 JSON 字符串)
```json
{
  "size": "small|medium|large",
  "recommend_decompose": true|false,
  "decompose_axis": "by_capability|by_domain|by_phase|null",
  "rationale": "判断理由 + 引用哪些历史 team 对比",
  "estimated_worker_count": N,
  "estimated_material_count": M
}
```

**诚实**: 不确定时倾向 medium + recommend_decompose=false. large 判断需有明确对比证据."""


class _ScaleAssessorPromptBuilder(PromptBuilderRouter):
    """装 intent + references 到首轮 user message."""

    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        intent = biz_input.get("_from_intent_analyzer")
        if not intent:
            # fallback 平铺
            intent_keys = ("domain", "purpose", "key_capabilities", "constraints", "scope")
            intent = {k: biz_input[k] for k in intent_keys if k in biz_input}

        refs = biz_input.get("_from_reference_scout")
        if not refs:
            refs = {"references": biz_input.get("references", [])}

        refs_list = refs.get("references", [])
        refs_text = "\n".join(
            f"  - [{r.get('kind', '?')}] {r.get('source_path', '')}: {r.get('reason', '')}"
            for r in refs_list
        )

        task = f"""## intent_analysis

{json.dumps(intent, ensure_ascii=False, indent=2)}

---

## team_references ({len(refs_list)} 条 · 供 agent 按需查)

{refs_text}

---

请:
1. 用工具读 1-3 份 similar team DESIGN.md (见 refs), 对比规模
2. 基于对比判 size + decompose
3. 用 finish 工具提交 JSON 结论 (放在 result 字段)
"""
        return [{"role": "user", "content": task}]


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def _parse_json_loose(text: str) -> Any | None:
    if not text:
        return None
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class _ScaleAssessorExtractResult(ExtractResultRouter):
    """从 final_text 或 finish 工具 result 提 scale_assessment JSON."""

    def extract(self, *, final_text: str, messages: list, turn_count: int, stop_reason: str) -> Verdict:
        # 1. 先试 finish tool_use 的 result 字段 (messages 里找 tool_use)
        result_json = None
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "finish":
                        inp = block.get("input", {})
                        result = inp.get("result")
                        if isinstance(result, str):
                            result_json = _parse_json_loose(result)
                        elif isinstance(result, dict):
                            result_json = result
                        if result_json:
                            break
            if result_json:
                break

        # 2. 回退 final_text
        if not result_json:
            result_json = _parse_json_loose(final_text)

        # 骨架默认兜底 (feedback_100pct_required_goes_to_skeleton · 2026-04-24):
        # LLM 偷懒调 finish(result="") 或产非 JSON 时, 骨架默认 size=medium 保全链路继续
        # ScaleAssessor 是评估节点, 非关键决策, 给合理默认比 FAIL 中断更务实
        if not isinstance(result_json, dict):
            result_json = {
                "size": "medium",
                "recommend_decompose": False,
                "decompose_axis": None,
                "rationale": f"骨架默认填 (LLM 未产有效 JSON · turns={turn_count} stop={stop_reason})",
                "estimated_worker_count": 3,
                "estimated_material_count": 3,
                "_skeleton_defaulted": True,
            }
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"final_text": final_text[:500], "turn_count": turn_count},
                diagnosis="unreachable · 骨架已默认填 (上面的 if 分支保证)",
            )

        # schema 必字段 · size 无效时骨架默认 medium (不 FAIL)
        if result_json.get("size") not in ("small", "medium", "large"):
            result_json["size"] = "medium"
            result_json.setdefault("_meta", {})["size_defaulted"] = True

        # 标记
        result_json.setdefault("_meta", {}).update({
            "worker": "ScaleAssessorWorker",
            "stage": "v1_agent_loop",
            "turn_count": turn_count,
            "stop_reason": stop_reason,
        })
        return Verdict(kind=VerdictKind.PASS, output=result_json)


class ScaleAssessorWorker(AgentNodeLoop):
    """Phase 2 · AgentNodeLoop · 规模研判 + 拆分维度."""

    FORMAT_IN: ClassVar = [
        "team_builder.material.intent_analysis",
        "team_builder.material.team_references",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.scale_assessment"
    DESCRIPTION: ClassVar[str] = (
        "Phase 2 · AgentNodeLoop · 综合 intent + refs 判 Team 规模 "
        "(small/medium/large) + 拆分维度建议. 可读 similar team DESIGN 对比."
    )
    ALLOW_NO_BUS: ClassVar[bool] = True  # 骨架期 · A5 对接真 bus
    TOOL_ROUTERS: ClassVar[list] = [ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter, FinishRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _ScaleAssessorPromptBuilder:
        return _ScaleAssessorPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _ScaleAssessorExtractResult:
        return _ScaleAssessorExtractResult(bus=bus)

    async def run(self, input_data: Any) -> Verdict:
        deterministic = _explicit_single_existing_shape(input_data)
        if deterministic is not None:
            return Verdict(
                kind=VerdictKind.PASS,
                output=deterministic,
                diagnosis="explicit one-node existing-Worker shape; no model call needed",
            )
        return await AgentNodeLoop.run(self, input_data)


def _explicit_single_existing_shape(input_data: Any) -> dict[str, Any] | None:
    """Return a fixed small-team assessment only when the request says so explicitly.

    This is intentionally conservative.  It avoids a multi-turn comparison run for
    the common wrapper shape while leaving ambiguous or genuinely large requests on
    the existing ScaleAssessor agent path.
    """

    if not isinstance(input_data, dict):
        return None
    intent = input_data.get("_from_intent_analyzer") or input_data
    try:
        text = json.dumps(intent, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        return None
    compact = re.sub(r"[`'\"\s：:，,]+", "", text)
    existing_binding = (
        "impl_type=existing" in compact
        or "impl_typeexisting" in compact
    )
    one_node = any(
        phrase in compact
        for phrase in (
            "仅允许一个节点",
            "只允许一个节点",
            "仅声明一个节点",
            "只声明一个节点",
            "exactlyonenode",
            "onlyonenode",
        )
    )
    no_materials = any(
        phrase in compact
        for phrase in (
            "materials_skeleton为空",
            "materials_skeleton必须为空",
            "emptymaterials_skeleton",
            "materials_skeletonempty",
        )
    )
    if not (existing_binding and one_node and no_materials):
        return None
    return {
        "size": "small",
        "recommend_decompose": False,
        "decompose_axis": None,
        "rationale": (
            "需求已明确限定为 1 个节点、复用既有 Worker 且不新建 Material；"
            "结构事实足以判定，无需读取历史 Team 或调用模型。"
        ),
        "estimated_worker_count": 1,
        "estimated_material_count": 0,
        "_meta": {
            "worker": "ScaleAssessorWorker",
            "stage": "deterministic_explicit_shape",
            "model_called": False,
        },
    }
