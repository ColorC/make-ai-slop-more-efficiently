# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.intent_analyzer.llm_extractor.py"
"""IntentAnalyzerWorker — agent-first 第一阶段 (2026-04-23).

Worker 协议:
  FORMAT_IN  = team_builder.material.origin_request
  FORMAT_OUT = team_builder.material.intent_analysis

**职责**: 独立上下文 LLM 调用 · 从用户原始请求提炼结构化意图.
产出 domain / purpose / scope / key_capabilities / constraints / ambiguities.

**独立上下文理由** (agent-first 方法论):
  - 与 ReferenceScout 认知独立, 可并行; 不需要知道 standards / similar teams
  - 专注"语言理解 + 意图归纳", prompt 集中
  - 失败独立重试, 不连累其他 agent

**实现状态** (agent-first 骨架):
  - V0: 简单 SOFT worker (一次 LLM 调用)
  - 本版实现为 stub + prompt 模板, LLM 接入占位 TODO
  - 观测后如发现需要多轮 tool use, 升级为 AGENT worker (继承 AgentNodeLoop)
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.buses import WebBus

from ._llm_client import call_llm_json


_SYSTEM_PROMPT = """你是 team_builder 团队中的 IntentAnalyzer · agent-first 第一阶段.

职责: 从用户的自然语言请求提炼**结构化意图**, 供下游 TeamArchitect 规划 Team 骨架.

**不要做**:
- 不做参考资料搜索 (那是 ReferenceScout 的事)
- 不做 Team 设计 (那是 TeamArchitect 的事)
- 不猜测用户没说的东西 (ambiguities 如实列出)

**产出 JSON 必须符合 schema**:
- domain (string): 业务领域, 如 'software_engineering' / 'data_pipeline' / 'game_config'
- purpose (string): 1-2 句概括要解决的核心问题
- scope.in_scope / scope.out_of_scope (list[string]): 明确边界
- key_capabilities (list[string]): 必须有的能力清单
- constraints (list[string]): 硬约束
- ambiguities (list[string]): 需要人类澄清的歧义点

诚实第一: 用户没说清楚的**必须**进 ambiguities, 不允许脑补."""


def _build_user_prompt(input_data: dict) -> str:
    request = input_data.get("request_text", "").strip() or input_data.get("text", "").strip()
    triggered_at = input_data.get("triggered_at", "")
    tags = input_data.get("tags", [])
    tags_str = ", ".join(tags) if tags else "(无)"
    target_package_path = input_data.get("target_package_path")
    target_line = (
        f"**已指定目标包路径**: {target_package_path}\n"
        if target_package_path
        else "**已指定目标包路径**: (未指定；必须列入 ambiguities)\n"
    )
    return (
        f"# 原始请求\n\n{request}\n\n"
        f"---\n\n"
        f"**触发时间**: {triggered_at}\n"
        f"**Tags**: {tags_str}\n"
        f"{target_line}\n"
        f"请输出 JSON 格式的 intent_analysis."
    )


class IntentAnalyzerWorker(Worker):
    """独立上下文 LLM · 提炼用户意图为 intent_analysis.

    构造: `IntentAnalyzerWorker(web_bus=bus)` · web_bus=None 时 LLM 调用不走审计.
    """

    DESCRIPTION = (
        "agent-first 第一阶段 · 从 origin_request 提炼 domain/purpose/scope/"
        "key_capabilities/constraints/ambiguities 结构化意图, 供 TeamArchitect 消费."
    )
    FORMAT_IN = "team_builder.material.origin_request"
    FORMAT_OUT = "team_builder.material.intent_analysis"

    def __init__(self, *, web_bus: WebBus | None = None, model: str | None = None):
        self._web_bus = web_bus
        self._model = model  # None = 默认 role runtime_main → qwen3.6-plus

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )

        request_text = input_data.get("request_text") or input_data.get("text")
        if not request_text or not isinstance(request_text, str):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="origin_request.request_text is empty or missing",
            )

        user_prompt = _build_user_prompt(input_data)
        try:
            parsed = call_llm_json(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                web_bus=self._web_bus,
                caller="team_builder.intent_analyzer",
                model=self._model,
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"LLM call failed: {type(e).__name__}: {e}",
            )

        if "_parse_error" in parsed:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=parsed,
                diagnosis=f"LLM output not JSON: {parsed['_parse_error']}",
            )

        # 要求字段: domain / purpose / key_capabilities
        missing = [k for k in ("domain", "purpose", "key_capabilities") if k not in parsed]
        if missing:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=parsed,
                diagnosis=f"intent_analysis missing required fields: {missing}",
            )

        # Exact package location is request metadata, not an LLM decision.  Copy
        # it after parsing so the model cannot rewrite the caller's choice.
        explicit_target = input_data.get("target_package_path")
        if explicit_target:
            parsed["target_package_path"] = explicit_target

        parsed.setdefault("_meta", {}).update(
            {
                "worker": "IntentAnalyzerWorker",
                "stage": "v1_llm",
                "prompt_chars": len(user_prompt),
                # Keep the original request as a reference.  Team Builder
                # downstreams consume the reference; they do not copy the
                # request into TeamSpec.
                "origin_request_ref": str(input_data.get("body_path") or "").strip(),
            }
        )
        return Verdict(
            kind=VerdictKind.PASS,
            output=parsed,
        )
