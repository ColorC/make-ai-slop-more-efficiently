# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.decomposition_planner.large_split.py"
"""DecompositionPlannerWorker — Phase 2 · AgentNodeLoop · conditional (2026-04-23).

Worker 协议 (composite fan-in and):
  FORMAT_IN  = [scale_assessment, intent_analysis]
  FORMAT_OUT = team_builder.material.decomposition_plan

**仅 size=large 激活**. 若 scale=small/medium, 该 Worker 不应被触发 (路由器层保证).

**职责**: AgentNodeLoop · 对 large 需求提出拆分方案:
    - sub_teams: 每个子 team 的 name + purpose + input_contract + output_contract
    - inter_team_contracts: 跨子 team 的 material 契约 (producer/consumer/material/semantics)

工具: ReadFile / Glob / Grep / ListDir / Finish
可读: packages/services/*/DESIGN.md (参考 similar 拆分模式)

**为什么 AgentNodeLoop**: 大需求拆分需:
- 读 similar 跨包协作案例
- 反复权衡拆分维度 (capability/domain/phase)
- 推敲契约 material schema
这些都是**信息不确定 + 高复杂度**的典型.

**不做** (用户明示): 不自动拆超过 3 层; 过复杂 → 返回当前 agent 对话请 L1 裁定.
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


_SYSTEM_PROMPT = """你是 team_builder 第 2 阶段 · DecompositionPlanner agent.

## 职责 (仅 size=large 激活)
将一个大需求拆分成 2-4 个子 team + 声明跨子 team 的**契约 material**.
每个子 team 后续将**递归**启动 team-builder 独立生成代码.

## 工具
- read_file / grep / list_dir: 读 packages/services/ 下 similar 跨包协作案例
- finish: 提交 JSON

## 拆分原则 (scale_assessment.decompose_axis 指定)
- **by_capability**: 按能力边界 (例: 分析/生成/验证 三子 team)
- **by_domain**: 按业务子域 (例: demogame/voxelcraft 三子 team)
- **by_phase**: 按阶段 (例: ingest/process/output 三子 team)

## 契约 material 设计要点
- 每对相邻子 team 用 **一个 material** 作接口
- material schema 要**两侧都能理解** (命名中立, 不偏向任何一方)
- material lifecycle 是 internal (跨 team 但在父 team 内)

## 不做
- 不拆超 4 个子 team (过度拆分)
- 不拆超 3 层 (递归深度)
- 子 team 职责模糊 / 重叠 → 合并

## 产出 JSON
```json
{
  "sub_teams": [
    {
      "name": "子 team 名 (snake_case, 后续作 package name)",
      "purpose": "1-2 句职责描述",
      "input_contract": "契约 material id (入)",
      "output_contract": "契约 material id (出)"
    }
  ],
  "inter_team_contracts": [
    {
      "producer": "上游子 team name",
      "consumer": "下游子 team name",
      "material": "契约 material id (格式: <parent_team>.<name>)",
      "semantics": "1-2 句 material 语义 + 必须字段概述"
    }
  ]
}
```

**诚实**: sub_teams ≥ 2 且 ≤ 4 · inter_team_contracts 至少覆盖所有相邻对."""


class _DecompositionPlannerPromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        scale = biz_input.get("_from_scale_assessor") or biz_input
        intent = biz_input.get("_from_intent_analyzer")
        if not intent:
            intent_keys = ("domain", "purpose", "key_capabilities", "constraints")
            intent = {k: biz_input[k] for k in intent_keys if k in biz_input}

        task = f"""## scale_assessment (上游判大)

```json
{json.dumps(scale, ensure_ascii=False, indent=2)[:1500]}
```

## intent_analysis

```json
{json.dumps(intent, ensure_ascii=False, indent=2)[:2000]}
```

---

请:
1. 参照 scale_assessment.decompose_axis 的建议 (by_capability / by_domain / by_phase)
2. 可选 grep 1-2 个 similar 跨包案例 (`packages/services/*/DESIGN.md` 含"依赖"或"下游"的)
3. 提出 2-4 个 sub_teams + 对应 inter_team_contracts
4. 用 finish 提交 JSON
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


class _DecompositionPlannerExtractResult(ExtractResultRouter):
    def extract(self, *, final_text: str, messages: list, turn_count: int, stop_reason: str) -> Verdict:
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
        if not result_json:
            result_json = _parse_json_loose(final_text)

        if not isinstance(result_json, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"final_text": final_text[:500], "turn_count": turn_count},
                diagnosis=f"DecompositionPlanner 未产出 JSON (turns={turn_count}, stop={stop_reason})",
            )

        sub_teams = result_json.get("sub_teams") or []
        contracts = result_json.get("inter_team_contracts") or []

        if not isinstance(sub_teams, list) or not (2 <= len(sub_teams) <= 4):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis=f"sub_teams 必须 2-4 个 (got {len(sub_teams) if isinstance(sub_teams, list) else 'n/a'})",
            )

        if not isinstance(contracts, list) or len(contracts) < 1:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis="inter_team_contracts 至少 1 条 (覆盖相邻子 team)",
            )

        # 每 sub_team 必须 name + purpose
        for i, st in enumerate(sub_teams):
            if not isinstance(st, dict) or not st.get("name") or not st.get("purpose"):
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=result_json,
                    diagnosis=f"sub_teams[{i}] 缺 name 或 purpose",
                )

        result_json.setdefault("_meta", {}).update({
            "worker": "DecompositionPlannerWorker",
            "stage": "v1_agent_loop",
            "turn_count": turn_count,
            "stop_reason": stop_reason,
        })
        return Verdict(kind=VerdictKind.PASS, output=result_json)


class DecompositionPlannerWorker(AgentNodeLoop):
    """Phase 2 (conditional size=large) · AgentNodeLoop · 大需求拆分 + 契约 material 声明."""

    FORMAT_IN: ClassVar = [
        "team_builder.material.scale_assessment",
        "team_builder.material.intent_analysis",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.decomposition_plan"
    DESCRIPTION: ClassVar[str] = (
        "Phase 2 · AgentNodeLoop · conditional size=large 激活 · 大需求拆 2-4 子 team + "
        "跨 team 契约 material. 子 team 后续递归启动 team-builder."
    )
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter, FinishRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _DecompositionPlannerPromptBuilder:
        return _DecompositionPlannerPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _DecompositionPlannerExtractResult:
        return _DecompositionPlannerExtractResult(bus=bus)

    async def run(self, input_data: Any) -> Verdict:
        # conditional · 仅 size=large 激活: dispatcher 看到 output=None 不 emit event,
        # 下游自然 skip. 这是 bus-driven 的"可选 Worker"机制.
        if isinstance(input_data, dict):
            scale = input_data.get("_from_scale_assessor") or input_data
            size = scale.get("size") if isinstance(scale, dict) else None
            if size and size != "large":
                return Verdict(
                    kind=VerdictKind.PASS,
                    output=None,  # 不 emit, 下游 decomposition_plan 订阅者自然不激活
                    diagnosis=f"skip (size={size}, non-large 无需拆分)",
                )
        return await super().run(input_data)
