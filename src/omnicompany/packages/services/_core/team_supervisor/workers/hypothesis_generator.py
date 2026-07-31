# [OMNI] origin=claude-code domain=services/team_supervisor/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.hypothesis_generator.engine.py"
"""HypothesisGeneratorWorker — team_supervisor Worker #5 (AGENT).

Worker 协议:
  FORMAT_IN  = [product_form_brief, design_purpose_brief, health_criteria]
  FORMAT_OUT = team_supervisor.hypothesis_set
  FORMAT_IN_MODE = and

职责: 综合三问 brief + 实读 target 代码后, 产 ≥10 条 (条件→预期) 假设.
      每条带 condition/expectation/oracle_code_hint/rationale 全自然语言句子.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    GlobRouter,
    GrepRouter,
    ListDirRouter,
    ReadFileRouter,
    SingleToolRouter,
    ToolContext,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM_PROMPT = """你是 team_supervisor 的 HypothesisGenerator · 一个推演 Agent.

## 你的任务

综合三问 brief 后, 产 **≥10 条** (条件→预期) 假设. 每条带可程序化判定的 oracle hint.

输入是:
- Q1 product_form_brief (essence / minimal_passing_evidence / failure_signals / schema)
- Q2 design_purpose_brief (essence / replaces / non_goals / stakeholder_use)
- Q3 health_criteria (key_observations / red_flags / oracle_strategies)

可选: 用 ReadFile/Glob/Grep 进一步查 target 代码补充洞察.

## 反模式禁令 (绝对不要)

❌ 不要给假设"打优先级" (`priority: 1`)
❌ 不要"分类"假设 (`category: "data_integrity"`)
❌ 不要写 `severity: high`

## 正确姿势

每条假设结构:
- `id`: H-001, H-002, ... (唯一递增, ^H-\\d{3}$)
- `condition` (≥20 字符): "什么情况下应观察 ..." (具体)
   例: "当 target dispatch 用 sample_input X 跑完后, 末节点产出 Y"
- `expectation` (≥20 字符): "期望什么具体特征 ..."
   例: "verdict.output.proposals 数组长度应 ≥ 3 且每条 reference_code.file 在源码中存在"
- `oracle_code_hint` (≥15 字符): "怎么程序化判定 ..."
   例: "对每条 proposal 取 reference_code.file + line_start, ReadFile 后 splitlines()[line_start-1] 应非空"
- `rationale` (≥15 字符): "这个假设来自哪个 Q · 为什么有意义"
   例: "派生自 Q1.failure_signals[2] 'reference 行号不存在'; Q2 non_goals[0] 强调不允许虚构"

## 派生路径建议

每个 Q 都能派生若干假设:

- **从 Q1.failure_signals**: 每条失败信号反向变正向假设 (≥3 条)
- **从 Q1.minimal_passing_evidence**: 翻译成"应观察 ..." (≥1 条)
- **从 Q2.non_goals**: 每条变"不应该出现 X" (≥2 条)
- **从 Q2.stakeholder_use**: 翻译成"消费时应能 ..." (≥1 条)
- **从 Q3.oracle_strategies**: 每条直接转换 (≥3 条)
- **从 Q3.red_flags**: 每条变红旗假设 (≥2 条)

加起来轻松 ≥10 条.

## 关键约束

1. **id 唯一无重复** · 严格 H-NNN 格式
2. **condition + expectation 必须具体** · 含具体字段名/数量/关系
3. **oracle_code_hint 必须可程序化** · 给下游 TestExecutor 真能写出判定代码
4. **末步必调 submit_hypothesis_set 工具**

## 工具

- **read_file / glob / grep / list_dir**: 可选, 进一步看 target 代码补充洞察
- **submit_hypothesis_set**: 终结性提交 (最关键)"""


class _HypothesisPromptBuilder(PromptBuilderRouter):
    """把三问 brief 注入 agent."""

    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        # fan-in 后的 input_data 含三问产物 + 三个 _from_<wid> 镜像
        q1 = biz_input.get("_from_ProductFormAnalyzerWorker") or {}
        q2 = biz_input.get("_from_PurposeInterpreterWorker") or {}
        q3 = biz_input.get("_from_HealthCriteriaDesignerWorker") or {}

        # fallback: 平铺顶层字段 (q1+q3 都有 essence 但 q3 没有 — q3 的 essence 不存在)
        if not q1:
            q1 = {
                "essence": biz_input.get("essence", ""),
                "minimal_passing_evidence": biz_input.get("minimal_passing_evidence", ""),
                "failure_signals": biz_input.get("failure_signals", []),
                "schema_fields_observed": biz_input.get("schema_fields_observed", []),
                "concrete_examples": biz_input.get("concrete_examples", []),
            }
        if not q2:
            q2 = {
                "essence": biz_input.get("essence", ""),
                "replaces": biz_input.get("replaces", ""),
                "non_goals": biz_input.get("non_goals", []),
                "stakeholder_use": biz_input.get("stakeholder_use", ""),
                "evidence_sources": biz_input.get("evidence_sources", []),
            }
        if not q3:
            q3 = {
                "key_observations": biz_input.get("key_observations", []),
                "red_flags": biz_input.get("red_flags", []),
                "oracle_strategies": biz_input.get("oracle_strategies", []),
            }

        task = f"""## 任务: 综合三问 brief 产 ≥10 条假设

### Q1 产物形式 brief

```json
{json.dumps(q1, ensure_ascii=False, indent=2)}
```

### Q2 设计目的 brief

```json
{json.dumps(q2, ensure_ascii=False, indent=2)}
```

### Q3 健康判据

```json
{json.dumps(q3, ensure_ascii=False, indent=2)}
```

### 目标

产 ≥10 条假设, 每条 {{id, condition, expectation, oracle_code_hint, rationale}}.
全字段自然语言句子, 禁标签/打分.

可选用 read_file/grep 看 target 代码补充洞察, 但不是必需.

请最终调 **submit_hypothesis_set** 提交结果."""

        return [{"role": "user", "content": task}]


class SubmitHypothesisSetRouter(SingleToolRouter):
    """提交假设集合 · 结构化 schema."""

    TOOL_NAME: ClassVar[str] = "submit_hypothesis_set"
    DESCRIPTION: ClassVar[str] = (
        "Submit the hypothesis set (≥10 hypotheses). Each hypothesis is a "
        "(condition→expectation) pair with programmatic oracle hint. "
        "All fields are natural language sentences; do NOT use priority/severity/category."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "hypotheses": {
                "type": "array",
                "minItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "pattern": "^H-\\d{3}$",
                            "description": "形如 H-001 唯一 id",
                        },
                        "condition": {"type": "string", "minLength": 20},
                        "expectation": {"type": "string", "minLength": 20},
                        "oracle_code_hint": {"type": "string", "minLength": 15},
                        "rationale": {"type": "string", "minLength": 15},
                    },
                    "required": [
                        "id",
                        "condition",
                        "expectation",
                        "oracle_code_hint",
                        "rationale",
                    ],
                },
            },
        },
        "required": ["hypotheses"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        hyps = args.get("hypotheses", [])
        return f"submitted: {len(hyps)} hypotheses"


class _HypothesisExtractResult(ExtractResultRouter):
    """从 messages 中提取 submit_hypothesis_set."""

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
                        and block.get("name") == "submit_hypothesis_set"
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
                    f"HypothesisGenerator 未调用 submit_hypothesis_set "
                    f"(turns={turn_count}, stop={stop_reason})"
                ),
            )

        # 反模式自检
        for forbidden in ("priority", "severity", "category", "tier", "score"):
            if forbidden in result_json:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=result_json,
                    diagnosis=f"反模式: 输出含禁字段 '{forbidden}'",
                )

        hyps = result_json.get("hypotheses", [])
        if not isinstance(hyps, list) or len(hyps) < 10:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis=f"假设数量 {len(hyps) if isinstance(hyps, list) else 0} < 10",
            )

        # id 唯一性检查
        ids = [h.get("id") for h in hyps]
        if len(set(ids)) != len(ids):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis="假设 id 重复",
            )

        # 每条假设禁内嵌反模式字段
        for i, h in enumerate(hyps):
            if not isinstance(h, dict):
                continue
            for forbidden in ("priority", "severity", "category", "tier", "score"):
                if forbidden in h:
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output=result_json,
                        diagnosis=f"假设 #{i} ({h.get('id')}) 含禁字段 '{forbidden}'",
                    )

        if stop_reason == "max_turns":
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=result_json,
                diagnosis=f"预算耗尽: {turn_count} turns; 已产 {len(hyps)} 假设",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output=result_json,
            diagnosis=f"假设集提交: {len(hyps)} 条",
            confidence=0.9,
        )


class HypothesisGeneratorWorker(AgentNodeLoop):
    """假设产生 · AGENT."""

    DESCRIPTION: ClassVar[str] = (
        "假设产生 · AGENT. 综合三问 brief + 可选 target 代码探索, 产 ≥10 条 "
        "(条件→预期) 假设, 全字段自然语言句子."
    )
    FORMAT_IN: ClassVar[list[str]] = [
        "team_supervisor.product_form_brief",
        "team_supervisor.design_purpose_brief",
        "team_supervisor.health_criteria",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_supervisor.hypothesis_set"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter,
        GlobRouter,
        GrepRouter,
        ListDirRouter,
        SubmitHypothesisSetRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus

        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _HypothesisPromptBuilder:
        return _HypothesisPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _HypothesisExtractResult:
        return _HypothesisExtractResult(bus=bus)
