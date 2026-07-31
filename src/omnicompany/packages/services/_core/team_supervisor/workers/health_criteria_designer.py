# [OMNI] origin=claude-code domain=services/team_supervisor/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.health_criteria_engine.py"
"""HealthCriteriaDesignerWorker — team_supervisor Worker #4 (SOFT).

Worker 协议:
  FORMAT_IN  = [team_supervisor.product_form_brief, team_supervisor.design_purpose_brief]
  FORMAT_OUT = team_supervisor.health_criteria
  FORMAT_IN_MODE = and

职责: Q3 健康判据. 综合 Q1+Q2 brief, 用 LLM 一次性产 health_criteria.
      不需多轮探索 (briefs 已在 prompt 内), 单 Submit 工具单步终结.

铁律:
- 全字段自然语言句子, 禁分类 / 打分 / 标签
- oracle 用语义描述 + implementation_hint, 不用 metric=value 形式
- 末步必调 submit_health_criteria 工具
"""
from __future__ import annotations

import json
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM_PROMPT = """你是 team_supervisor 的 HealthCriteriaDesigner.

## 你的任务

回答**第三个基本问题**: 给 target team 产物如何验证健康?

输入是 Q1 product_form_brief + Q2 design_purpose_brief. 你需要综合两份 brief 产出 health_criteria.

不需用工具探索 — 你看到的两份 brief 已是 Q1+Q2 的全部输出. 直接调 submit_health_criteria 提交.

## 反模式禁令 (绝对不要)

❌ 不要给判据"打分" (`severity: high`)
❌ 不要写 `oracle: {metric: completeness, threshold: 0.8}` (这是 metric=value 形式)
❌ 不要用枚举类标签

## 正确姿势

✅ `key_observations` (≥3 条): 看产物时主动观察什么 · 句子
   例: "查看 verdict.output.proposals 的数量, 应 ≥3 条以体现多样性"

✅ `red_flags` (≥2 条): 出现什么就该警惕 · 具体特征句
   例: "全部 proposals 引用同一文件 (说明只看了 1 个模块)"

✅ `oracle_strategies` (≥3 条): 验证策略 · 每条 {what_to_check, implementation_hint}
   - what_to_check (≥15 字符): 验证什么 · 语义描述
     例: "检查 reference_code.file 字段对应行号是否真在源文件中存在"
   - implementation_hint (≥15 字符): 怎么程序化实现这个 oracle · 语义 hint
     例: "对每条 proposal 取 file + line_start, 用 ReadFile 读对应文件然后看 splitlines()[line_start-1] 是否非空"

## 关键约束

1. **所有 oracle 必须可程序化判定** — 给下游 HypothesisGenerator 的 hint 越具体越好
2. **基于 Q1 失败信号派生 oracle** — Q1 的 failure_signals 是直接的 oracle 来源
3. **基于 Q2 non_goals 派生 red_flags** — 它不该做的事如果做了就是红旗
4. **末步必调 submit_health_criteria 工具**"""


class _HealthCriteriaPromptBuilder(PromptBuilderRouter):
    """把 Q1+Q2 brief 注入 agent 首轮会话."""

    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        # FORMAT_IN_MODE = "and" 的 fan-in: 两个 brief 平铺到 input_data 顶层
        # Q1 brief 字段
        q1_essence = biz_input.get("essence", "")  # Q1
        q1_evidence = biz_input.get("minimal_passing_evidence", "")
        q1_signals = biz_input.get("failure_signals", [])
        q1_examples = biz_input.get("concrete_examples", [])
        q1_schema = biz_input.get("schema_fields_observed", [])

        # Q2 brief 字段; 但 Q1 也有 essence — fan-in 时后到的会覆盖前到的
        # TeamRunner._merge_inputs 提供 _from_<wid> 镜像, 用它解决冲突
        q2_essence = ""
        q2_replaces = biz_input.get("replaces", "")
        q2_non_goals = biz_input.get("non_goals", [])
        q2_stakeholder = biz_input.get("stakeholder_use", "")
        q2_evidence_sources = biz_input.get("evidence_sources", [])

        # 从 _from_PurposeInterpreterWorker 镜像里拿 q2_essence (避免被 q1_essence 覆盖)
        q2_mirror = biz_input.get("_from_PurposeInterpreterWorker", {})
        if isinstance(q2_mirror, dict):
            q2_essence = q2_mirror.get("essence", q1_essence)
        else:
            q2_essence = q1_essence  # fallback

        # 同样, 从 _from_ProductFormAnalyzerWorker 镜像里拿 q1_essence (确保是 Q1 而非 Q2)
        q1_mirror = biz_input.get("_from_ProductFormAnalyzerWorker", {})
        if isinstance(q1_mirror, dict):
            q1_essence = q1_mirror.get("essence", q1_essence)
            q1_evidence = q1_mirror.get("minimal_passing_evidence", q1_evidence)
            q1_signals = q1_mirror.get("failure_signals", q1_signals)
            q1_examples = q1_mirror.get("concrete_examples", q1_examples)
            q1_schema = q1_mirror.get("schema_fields_observed", q1_schema)

        q1_brief = {
            "essence": q1_essence,
            "minimal_passing_evidence": q1_evidence,
            "failure_signals": q1_signals,
            "concrete_examples": q1_examples,
            "schema_fields_observed": q1_schema,
        }
        q2_brief = {
            "essence": q2_essence,
            "replaces": q2_replaces,
            "non_goals": q2_non_goals,
            "stakeholder_use": q2_stakeholder,
            "evidence_sources": q2_evidence_sources,
        }

        task = f"""## 任务: 综合 Q1+Q2 产 Q3 健康判据

### Q1 产物形式 brief

```json
{json.dumps(q1_brief, ensure_ascii=False, indent=2)}
```

### Q2 设计目的 brief

```json
{json.dumps(q2_brief, ensure_ascii=False, indent=2)}
```

### 综合派生路径

- **key_observations**: 从 Q1 essence + Q2 stakeholder_use 派生 — "看产物时应主动观察 ..."
- **red_flags**: 从 Q1 failure_signals + Q2 non_goals 派生 — "出现 ... 就该警惕"
- **oracle_strategies**: 从 Q1 failure_signals 直接转换成 oracle (failure_signals 本身就是 negative oracle 的反向)

### 提交要求

调 **submit_health_criteria** 提交:

- key_observations (list[str], ≥3 条): 应观察什么 · 每条 ≥10 字符句子
- red_flags (list[str], ≥2 条): 红旗信号 · 每条 ≥10 字符句子
- oracle_strategies (list[obj], ≥3 条): 每条 {{what_to_check (≥15 字符), implementation_hint (≥15 字符)}}

请提交."""

        return [{"role": "user", "content": task}]


class SubmitHealthCriteriaRouter(SingleToolRouter):
    """提交 Q3 健康判据 · 结构化 schema · 终结 agent loop."""

    TOOL_NAME: ClassVar[str] = "submit_health_criteria"
    DESCRIPTION: ClassVar[str] = (
        "Submit the health criteria (Q3 answer). All fields must be natural language sentences. "
        "oracle_strategies must use semantic description + implementation_hint, NOT metric=value form."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "key_observations": {
                "type": "array",
                "minItems": 3,
                "items": {"type": "string", "minLength": 10},
                "description": "应观察什么 · 句子列表",
            },
            "red_flags": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "string", "minLength": 10},
                "description": "红旗信号 · 具体特征句",
            },
            "oracle_strategies": {
                "type": "array",
                "minItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "what_to_check": {"type": "string", "minLength": 15},
                        "implementation_hint": {"type": "string", "minLength": 15},
                    },
                    "required": ["what_to_check", "implementation_hint"],
                },
                "description": "验证策略 · 全句子",
            },
        },
        "required": ["key_observations", "red_flags", "oracle_strategies"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        oracles = args.get("oracle_strategies", [])
        return f"submitted: health_criteria with {len(oracles)} oracle strategies"


class _HealthCriteriaExtractResult(ExtractResultRouter):
    """从 messages 中提取 submit_health_criteria 的 tool_use input."""

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        result_json: dict | None = None
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "submit_health_criteria"
                    ):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            result_json = dict(inp)
                            break
            if result_json:
                break

        if not isinstance(result_json, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"final_text": final_text[:500], "turn_count": turn_count},
                diagnosis=(
                    f"HealthCriteriaDesigner 未调用 submit_health_criteria "
                    f"(turns={turn_count}, stop={stop_reason})"
                ),
            )

        # 反模式自检
        for forbidden in ("severity", "priority_level", "metric", "threshold", "score"):
            if forbidden in result_json:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=result_json,
                    diagnosis=f"反模式: 输出含禁字段 '{forbidden}'",
                )

        oracles = result_json.get("oracle_strategies", [])
        if not isinstance(oracles, list) or len(oracles) < 3:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis="oracle_strategies < 3",
            )

        if stop_reason == "max_turns":
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=result_json,
                diagnosis=f"预算耗尽: {turn_count} turns",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output=result_json,
            diagnosis=f"Q3 health_criteria 提交: {len(oracles)} oracle strategies",
            confidence=0.9,
        )


class HealthCriteriaDesignerWorker(AgentNodeLoop):
    """Q3 健康判据 · SOFT (用 AgentNodeLoop 单步走 submit)."""

    DESCRIPTION: ClassVar[str] = (
        "Q3 健康判据 · SOFT. 综合 Q1+Q2 brief 一次性产 health_criteria 全自然语言句子."
    )
    FORMAT_IN: ClassVar[list[str]] = [
        "team_supervisor.product_form_brief",
        "team_supervisor.design_purpose_brief",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_supervisor.health_criteria"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [SubmitHealthCriteriaRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus

        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _HealthCriteriaPromptBuilder:
        return _HealthCriteriaPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _HealthCriteriaExtractResult:
        return _HealthCriteriaExtractResult(bus=bus)
