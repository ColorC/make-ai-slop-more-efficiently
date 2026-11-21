# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.agent.intent_parser.entrypoint.py"
"""Agent Intent Router — _parse_user_intent + run_agent_with_intent

此文件包含：
  _parse_user_intent         — 意图解析（结构化任务分解，写入 IntentTracer step=-1）
  run_agent_with_intent      — 主入口（GraphSpec DAG + TeamRunner，带意图解析前置）

2026-04-18 rename：去除版本后缀（命名铁律；旧名可从 git log 查到）。

已归档（→ _graveyard/semantic_routing/）：
  _accumulate_trace_route  — 路由图积累（RouteGraph 已退役）
  _observe_semantic_route  — M3 语义路由观测
  _maybe_consolidate       — M1 语义蒸馏
  _record_semantic_outcome — M3 结果反馈写回节点成熟度
  _try_path_executor       — PathExecutor 成熟路径切换
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from omnicompany.runtime.agent.agent_constants import DEFAULT_SYSTEM_PROMPT

if TYPE_CHECKING:
    from omnicompany.tracing.intent_tracer import IntentTracer
    from omnicompany.runtime.signals.mirror_node import MirrorNode


async def _parse_user_intent(
    task: str,
    tracer: "IntentTracer",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict | None:
    """在 agent 执行前解析用户意图，写入 step=-1。

    输出结构：
    {
      "provided_info":        ["user has X", "user has Y"],
      "desired_output_types": ["file_path", "message_id"],
      "goals": [
        {
          "desc":       "Send a Feishu message",
          "depends_on": [],
          "output_type": "message_id"
        },
        ...
      ]
    }
    """
    import json as _json
    import re
    from omnicompany.runtime.llm.llm import LLMClient
    _logger = logging.getLogger(__name__)

    def _robust_json_parse(text: str) -> dict | None:
        if not text:
            return None
        text = text.strip()
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            pass
        markdown_pattern = r'^```(?:json|JSON|javascript|js)?\s*(.*?)\s*```$'
        match = re.search(markdown_pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()
            try:
                return _json.loads(text)
            except _json.JSONDecodeError:
                pass
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
            if text.startswith("json"):
                text = text[4:].strip()
            try:
                return _json.loads(text)
            except _json.JSONDecodeError:
                pass
        json_object_pattern = r'\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}'
        for m in re.findall(json_object_pattern, text, re.DOTALL):
            try:
                result = _json.loads(m)
                if isinstance(result, dict):
                    return result
            except _json.JSONDecodeError:
                pass
        fixed = re.sub(r',\s*([\}\]])', r'\1', text)
        fixed += '}' * (fixed.count('{') - fixed.count('}'))
        fixed += ']' * (fixed.count('[') - fixed.count(']'))
        try:
            return _json.loads(fixed)
        except _json.JSONDecodeError:
            pass
        _logger.debug("All JSON parsing strategies failed for text: %s...", text[:200])
        return None

    prompt = f"""You are an intent parser for an autonomous agent system.

USER REQUEST:
{task}

Parse this request into structured intent. Output valid JSON only, no explanation.

{{
  "provided_info": ["<what the user already has or provides>", ...],
  "desired_output_types": ["<semantic type of each thing user wants>", ...],
  "goals": [
    {{
      "desc": "<specific actionable goal in one sentence>",
      "depends_on": [<indices of prior goals this depends on, or empty>],
      "output_type": "<single semantic type this goal produces>"
    }}
  ]
}}

Rules:
- provided_info: concrete facts given in the request (file paths, IDs, credentials, etc.)
- desired_output_types: the leaf outputs — what the user ultimately wants as results
- goals: decompose into atomic steps; if step B requires result of step A, set depends_on=[0]
- If goals are independent/parallel, all have depends_on=[]
- Be specific in desc: name actual tools, files, APIs mentioned or implied by context
- Output ONLY the JSON object, no markdown fences"""

    try:
        client = LLMClient(model=model, base_url=base_url, api_key=api_key)
        response = await asyncio.to_thread(
            client.call,
            messages=[{"role": "user", "content": prompt}],
            system="You are a precise intent parser. Output only valid JSON.",
        )
        raw = ""
        for block in response.content:
            if hasattr(block, "type") and block.type == "text":
                raw = block.text.strip()
                break

        parsed = _robust_json_parse(raw)
        if parsed is None:
            _logger.warning("Failed to parse intent from LLM response: %s...", raw[:200])
            return None

        goals_summary = "; ".join(g.get("desc", "") for g in parsed.get("goals", []))
        tracer.record_step(
            tool_name="__intent_parse__",
            intent={
                "input_types": ["user_request"],
                "output_types": parsed.get("desired_output_types", []),
                "action_class": "think",
                "desc": f"User intent: {goals_summary[:80]}",
                "rationale": (
                    f"Provided info: {parsed.get('provided_info', [])}. "
                    f"Goals: {_json.dumps(parsed.get('goals', []), ensure_ascii=False)}"
                ),
            },
        )
        _logger.info("Intent parsed: %d goals, output_types=%s",
                     len(parsed.get("goals", [])),
                     parsed.get("desired_output_types", []))
        return parsed

    except Exception as e:
        _logger.warning("_parse_user_intent failed: %s", e)
        return None


async def run_agent_with_intent(
    task: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_steps: int = 50,
    db_path: str | None = None,
    intent_db_path: str | None = None,
    route_db_path: str | None = None,
    parent_task_id: str = "",
    origin: str = "human",
    mirror: "MirrorNode | None" = None,
    mutation_state: "Any | None" = None,
    param_registry: "Any | None" = None,
    guardian: "Any | None" = None,
    round_num: int | None = None,
    node_filter: "callable | None" = None,
) -> Any:
    """v2 Agent — 使用节点化 GraphSpec DAG 运行

    与 run_agent 兼容的接口，内部使用：
      - 显式节点图（context → llm → death_zone → tool_dispatch → pain_classify）
      - 所有语义操作都是图节点，可被替换/进化
      - 底座（TeamRunner）不含语义逻辑
    """
    from pathlib import Path
    from ulid import ULID
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.tracing.intent_tracer import IntentTracer
    from omnicompany.runtime.exec.runner import TeamRunner
    from omnicompany.runtime.exec.graph_builder import build_runtime_graph, build_runtime_bindings

    # Move 8: SQLiteBus engine routes to unified data/events.db when db_path is None.
    intent_db = Path(intent_db_path) if intent_db_path else Path("data/intent_traces.db")
    trace_id = str(ULID())

    async with SQLiteBus(db_path) as bus:
        tracer = IntentTracer(
            db_path=intent_db,
            trace_id=trace_id,
            parent_task_id=parent_task_id,
            origin=origin,
            type_discovery=None,
            event_bus=bus,
        )

        effective_system_prompt = system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT

        graph_spec = build_runtime_graph()
        bindings = build_runtime_bindings(
            model=model,
            base_url=base_url,
            api_key=api_key,
            route_db_path=route_db_path,
            semantic_network_db_path=None,
            mirror=mirror,
            route_graph=None,
            mutation_state=mutation_state,
            param_registry=param_registry,
            guardian=guardian,
        )

        if mutation_state is not None:
            from omnicompany.runtime.exec.graph_builder import apply_topology_mutations
            graph_spec, topo_bindings = apply_topology_mutations(
                graph_spec, mutation_state,
                model=model, base_url=base_url, api_key=api_key,
            )
            bindings.update(topo_bindings)

        bindings["llm"].tracer = tracer  # type: ignore[attr-defined]
        bindings["tool_dispatch"].tracer = tracer  # type: ignore[attr-defined]
        if "route_accumulate" in bindings:
            bindings["route_accumulate"].tracer = tracer  # type: ignore[attr-defined]

        runner = TeamRunner(graph_spec, bindings, bus, max_steps=max_steps)
        result = await runner.run({
            "system_prompt": effective_system_prompt,
            "user_input": task,
            "messages": [],
        })

        tracer.close()

        if isinstance(result, dict) and result.get("budget_exhausted"):
            worst = result.get("worst_node", "")
            escalation_target = worst if worst and worst != "none" else "runtime"
            return {
                "result": None,
                "escalate": True,
                "budget_exhausted": True,
                "escalation_target": escalation_target,
                "escalation_pain": result.get("escalation_pain", 0.6),
                "pain_by_node": result.get("pain_by_node", {}),
                "worst_node": worst,
            }

        last = runner.last_output or {}
        if isinstance(last, dict) and last.get("escalate"):
            return {
                "result": result,
                "escalate": last.get("escalate", False),
                "escalation_target": last.get("escalation_target", ""),
                "escalation_pain": last.get("escalation_pain", 0.0),
                "pain_by_node": last.get("pain_by_node", {}),
                "worst_node": last.get("worst_node", ""),
            }
        return result
