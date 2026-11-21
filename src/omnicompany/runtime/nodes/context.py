# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.nodes.context_injection.tracking_routers.py"
"""上下文与追踪节点 — 真相注入、镜像、任务意图、路由积累

从 semantic.py 拆分。
"""

from __future__ import annotations

import logging
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.storage.db_access import open_db, open_db_rw

logger = logging.getLogger(__name__)


class TruthInjectRouter(Router):
    """真相注入节点 — 将系统知识拼接到 prompt。

    注入三类知识：
    1. MirrorNode 的自我认知（如果可用）
    2. 语义类型匹配的处理策略（替代 conditional_rules 的类型驱动分发）
       - 如果 SemanticTypeClassifierRouter 已在上游运行，
         使用 semantic_type_guidance（结构化类型处理）
       - 否则 fallback 到旧的 conditional_rules（向后兼容）
    3. B3: 选中路由节点的 node_guidance（节点级进化产出的局部指导语）

    这个节点是可进化的——元进化可以改变注入策略。
    """

    INPUT_KEYS = ["system_prompt"]

    def __init__(self, mirror: Any = None, mutation_state: Any = None, route_graph: Any = None):
        self.mirror = mirror
        self.mutation_state = mutation_state
        self.route_graph = route_graph

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        base_prompt = input_data.get("system_prompt", "")
        enriched = base_prompt
        truth_injected = False

        # 1. MirrorNode 自我认知
        if self.mirror is not None:
            try:
                concept = self.mirror.get_current_concept()
                if concept:
                    enriched = f"## System Self-Awareness\n\n{concept}\n\n---\n\n{enriched}"
                    truth_injected = True
            except Exception as e:
                logger.debug("Mirror injection failed: %s", e)

        # 2. 语义类型策略 OR 条件规则（向后兼容）
        semantic_type_id = input_data.get("semantic_type_id", "")
        if semantic_type_id:
            # 语义类型已由 SemanticTypeClassifierRouter 匹配
            # guidance 已注入到 system_prompt，这里不需要重复
            truth_injected = True
            logger.debug("SemanticType %s already enriched prompt upstream", semantic_type_id)
        elif self.mutation_state is not None:
            # fallback: 旧式 conditional_rules 注入
            try:
                active_rules = [
                    r for r in getattr(self.mutation_state, "conditional_rules", [])
                    if getattr(r, "active", True)
                ]
                if active_rules:
                    rules_text = "\n\n## Learned Conditional Rules (from past failures)\n\n"
                    for i, rule in enumerate(active_rules, 1):
                        validated = (
                            f" [validated by {len(rule.validated_by)} tasks]"
                            if rule.validated_by else ""
                        )
                        rules_text += (
                            f"### Rule {i}{validated}\n"
                            f"**When**: {rule.pattern}\n"
                            f"**Do**: {rule.guidance}\n\n"
                        )
                    enriched = enriched + rules_text
                    truth_injected = True
                    logger.debug("Injected %d conditional rules (legacy fallback)", len(active_rules))
            except Exception as e:
                logger.debug("Rule injection failed: %s", e)

        # 3. B3: 节点级指导语注入
        selected_nid = input_data.get("selected_route_node_id", "")
        if selected_nid and self.route_graph is not None:
            try:
                node = self.route_graph.get_node(selected_nid)
                if node and node.node_guidance:
                    enriched = enriched + (
                        f"\n\n## Route Node Guidance (for current path)\n\n"
                        f"{node.node_guidance}\n"
                    )
                    truth_injected = True
                    logger.debug("Injected node_guidance for %s", selected_nid)
            except Exception as e:
                logger.debug("Node guidance injection failed: %s", e)

        # 4. 工作区目录规范（静态注入，始终存在）
        workspace_rule = (
            "\n\n## 工作区写入规范（强制）\n\n"
            f"工作区根目录：`{omni_workspace_root().as_posix()}/`\n"
            "文件只能写入以下子目录，**严禁在工作区外创建任何文件**：\n"
            "- `data/autonomous/` — 运行时数据、数据库、演化日志\n"
            "- `data/autonomous/reports/` — 分析报告、诊断文档（Markdown/txt）\n"
            "- `tmp/` — 临时脚本、一次性验证文件（可随时清理）\n"
            "- `scripts/` — 持久化工具脚本\n"
            "- `src/` — 源代码（谨慎修改）\n\n"
            "**违规示例**（禁止）：`/e/solution.py`、`e:/task.py`、`C:/tmp/output.txt`\n"
            "**合规示例**（允许）：`data/autonomous/reports/analysis.md`、`tmp/test_snippet.py`\n"
        )
        enriched = enriched + workspace_rule

        return Verdict(
            kind=VerdictKind.PASS,
            output={**input_data, "system_prompt": enriched, "truth_injected": truth_injected},
        )


class MirrorRouter(Router):
    """自我认知生成节点 — 调用 MirrorNode 生成 self_concept。

    独立节点，可被进化替换。
    当前为简化实现（检查 mirror 是否可用）。
    """

    INPUT_KEYS = ["system_prompt"]

    def __init__(self, mirror: Any = None):
        self.mirror = mirror

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        has_concept = False
        if self.mirror is not None:
            try:
                concept = self.mirror.get_current_concept()
                has_concept = bool(concept)
            except Exception:
                pass

        return Verdict(
            kind=VerdictKind.PASS,
            output={**input_data, "mirror_available": has_concept},
        )


class TaskIntentRouter(Router):
    """任务意图解析节点 — 在 DAG 执行前解析用户请求为结构化意图。

    替代原来的 _parse_user_intent 函数。
    接收 user_input，输出解析后的意图并记录到 tracer。
    """

    INPUT_KEYS = ["user_input", "system_prompt"]

    def __init__(
        self,
        tracer: Any = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        semantic_network_db_path: str | None = None,
    ):
        self.tracer = tracer
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self._semantic_network_db_path = semantic_network_db_path
        self._logger = logging.getLogger(__name__)

    async def run(self, input_data: Any) -> Verdict:
        """解析用户意图并记录到 tracer。"""
        import asyncio
        import json as _json

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        task = input_data.get("user_input", "")
        if not task:
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        # Build prompt for intent parsing
        prompt = f"""You are an intent parser for an autonomous agent system.

USER REQUEST:
{task}

Parse this request into structured intent. Output valid JSON only, no explanation.

{{
  "provided_info": ["<what the user already has or provides>"],
  "desired_output_types": ["<output_type_1>", "<output_type_2>"],
  "goals": [
    {{
      "desc": "<goal description>",
      "depends_on": [],
      "output_type": "<expected output type>"
    }}
  ]
}}

Example:
{{
  "provided_info": ["user has a chat_id"],
  "desired_output_types": ["message_id"],
  "goals": [
    {{
      "desc": "Send a Feishu message",
      "depends_on": [],
      "output_type": "message_id"
    }}
  ]
}}
"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
            )

            # Phase 2.5: read processing_prompt from DB node (meta-evolvable)
            _task_intent_system = "Parse user intent into structured JSON. Respond with JSON only."
            _sndb = getattr(self, "_semantic_network_db_path", None)
            if _sndb:
                try:
                    with open_db(_sndb, readonly=True) as _nc:
                        _row = _nc.execute(
                            "SELECT processing_prompt FROM semantic_nodes WHERE node_id='routing.task_intent'"
                        ).fetchone()
                        if _row and _row[0]:
                            _task_intent_system = f"{_row[0]}\n\n{_task_intent_system}"
                except Exception:
                    pass

            response = await asyncio.to_thread(
                client.call,
                messages=[{"role": "user", "content": prompt}],
                system=_task_intent_system,
            )
            raw = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw += block.text

            # Parse JSON from response
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].strip()
                if raw.startswith("json"):
                    raw = raw[4:].strip()

            parsed = _json.loads(raw)

            # Record to tracer (step=-1 for intent parse)
            if self.tracer is not None:
                goals_summary = "; ".join(g.get("desc", "") for g in parsed.get("goals", []))
                self.tracer.record_step(
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
                # Update held_types for downstream nodes
                for t in parsed.get("desired_output_types", []):
                    self.tracer._held_types.add(f"__intent__{t}")

                self._logger.info(
                    "Intent parsed: %d goals, output_types=%s",
                    len(parsed.get("goals", [])),
                    parsed.get("desired_output_types", []),
                )

            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "parsed_intent": parsed,
                },
            )

        except Exception as e:
            self._logger.warning("TaskIntentRouter failed: %s", e)
            return Verdict(kind=VerdictKind.PASS, output=input_data)


class TraceAccumulateRouter(Router):
    """路由积累节点 — 在 DAG 执行后积累路由图。

    替代原来的 _accumulate_trace_route 函数。
    读取 intent_db 中的 trace，积累到 route_graph。
    trace_id 从 tracer 实例获取（不再要求上游传入）。
    """

    INPUT_KEYS = None  # accept any input type (str from llm→pass or dict from dispatch path)

    def __init__(
        self,
        tracer: Any = None,
        intent_db_path: str | None = None,
        route_db_path: str | None = None,
        route_graph: Any = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.tracer = tracer
        self.intent_db_path = intent_db_path
        self.route_db_path = route_db_path
        self.route_graph = route_graph
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self._logger = logging.getLogger(__name__)

    async def run(self, input_data: Any) -> Verdict:
        """积累路由图并进行痛觉后处理。"""

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        trace_id = input_data.get("trace_id", "")
        if not trace_id and self.tracer is not None:
            trace_id = getattr(self.tracer, "trace_id", "")
        if not trace_id or not self.intent_db_path or not self.route_db_path:
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        try:
            from omnicompany.runtime.route_graph import RouteGraph, RouteClassifier
            from omnicompany.runtime.llm.embedding_client import TextEmbeddingClient
            from omnicompany.runtime.llm.llm import LLMClient

            # Read steps from intent_db
            conn = open_db_rw(self.intent_db_path)
            rows = conn.execute(
                "SELECT * FROM intent_steps WHERE trace_id = ? ORDER BY step_num",
                (trace_id,),
            ).fetchall()
            conn.close()

            if not rows:
                return Verdict(kind=VerdictKind.PASS, output=input_data)

            steps = [dict(r) for r in rows]
            total_steps = len(steps)

            # Initialize route graph and classifier
            graph = self.route_graph if self.route_graph else RouteGraph(self.route_db_path)
            llm = LLMClient(model=self.model, base_url=self.base_url, api_key=self.api_key)
            emb = TextEmbeddingClient("http://localhost:8000/api/embeddings")
            classifier = RouteClassifier(graph, llm, emb)

            # Accumulate trace
            await classifier.accumulate_trace(steps)

            # Pain post-processing
            from omnicompany.runtime.signals.pain_system import PainPropagator
            propagator = PainPropagator(graph)

            for step in steps:
                node_id = step.get("node_id")
                raw_intent = step.get("intent")
                try:
                    if isinstance(raw_intent, str):
                        import json as _json
                        raw_intent = _json.loads(raw_intent)
                except Exception:
                    raw_intent = {}

                exit_code = step.get("exit_code")
                token_cost = step.get("token_cost", 0) or 0
                violations = step.get("violations", 0) or 0
                is_success = step.get("is_success", False)
                step_num = step.get("step_num", 0)

                enriched_step = {
                    **step,
                    "intent": raw_intent or {},
                    "tool_args": step.get("tool_args", {}),
                }

                event = classifier.classify(
                    trace_step=enriched_step,
                    exit_code=exit_code,
                    token_cost=token_cost,
                    violations=violations,
                    is_success=is_success,
                    steps_used=step_num,
                    steps_budget=total_steps,
                )

                if event and event.node_id:
                    updated = propagator.propagate(event, steps)
                    self._logger.debug(
                        "Pain propagated: tier=%d intensity=%.3f depth=%d nodes=%s",
                        event.pain_tier, event.pain_intensity, len(updated), [n[:8] for n in updated],
                    )
                    for uid in updated:
                        graph.check_deprecation(uid)
                elif is_success and node_id:
                    propagator.heal(node_id)
                    graph.update_energy(node_id, delta=+0.05)

            self._logger.info("Trace accumulated: %d steps, route_graph updated", total_steps)

            # Lightweight periodic maintenance: semantic node maturity only
            try:
                maint = graph._maintain_semantic_nodes()
                promos = maint.get("promotions", [])
                depr = maint.get("deprecations", [])
                if promos or depr:
                    self._logger.info(
                        "SemanticNode maintenance: %d promotions, %d deprecations",
                        len(promos), len(depr),
                    )
            except Exception as e:
                self._logger.debug("Maintenance skipped: %s", e)

            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "trace_accumulated": True,
                    "steps_count": total_steps,
                },
            )

        except Exception as e:
            self._logger.warning("TraceAccumulateRouter failed: %s", e)
            return Verdict(kind=VerdictKind.PASS, output=input_data)
