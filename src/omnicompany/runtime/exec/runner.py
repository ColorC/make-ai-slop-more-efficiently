# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.exec.dag_execution_engine.runner.py"
"""TeamRunner — TeamSpec + EventBus 驱动的通用 DAG 执行器

读入 TeamSpec + Router 绑定，从 entry 节点开始，
按路由表自动循环，每一步都通过 EventBus 发射事件。

支持:
- 线性链（A → B → C）
- 条件分支（A →[PASS] B, A →[FAIL] C）
- 反馈循环（A → B → ... → A，通过 feedback 边标记）
- Fan-out（一个节点输出触发多个下游并发执行）
- Fan-in / Join（多个上游全部完成后合并输入，再执行下游）
- Scatter / Map-Reduce（已有功能保持不变）
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from typing import Any

# 全量 I/O 记录的单条上限（bytes，JSON 序列化后）
_IO_MAX_BYTES = 512 * 1024  # 512 KB


def _check_required_context(input_data: Any, required_keys: list[str]) -> list[str]:
    """M4 H1: 检查 input_data 是否包含 required_keys 声明的字段.

    支持 dotted key (如 "foo.bar") 访问嵌套字段. 返回缺失字段列表.
    """
    if not required_keys:
        return []
    if not isinstance(input_data, dict):
        return list(required_keys)
    missing: list[str] = []
    for key in required_keys:
        parts = key.split(".")
        cur: Any = input_data
        found = True
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                found = False
                break
            val = cur[p]
            # 空值视为缺失 (None / 空串 / 空列表)
            if val is None or val == "" or val == []:
                found = False
                break
            cur = val
        if not found:
            missing.append(key)
    return missing


def _safe_serialize(data: Any) -> Any:
    """将节点 I/O 安全地序列化为 JSON-compatible 对象。

    - dict/list/str/int/float/bool/None 直接返回（已是 JSON-compatible）
    - 超过 _IO_MAX_BYTES 则截断并加标记
    - 无法序列化的对象转为字符串
    """
    try:
        raw = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        raw = str(data)
    if len(raw.encode()) > _IO_MAX_BYTES:
        # 按字节截断，保留前 _IO_MAX_BYTES 并标记
        truncated = raw.encode()[:_IO_MAX_BYTES].decode(errors="replace")
        return {"__truncated__": True, "__size_bytes__": len(raw.encode()), "data": truncated}
    try:
        return json.loads(raw)
    except Exception:
        return raw

logger = logging.getLogger(__name__)

from ulid import ULID

from omnicompany.bus.base import EventBus
from omnicompany.protocol.signal import Signal
from omnicompany.protocol.anchor import RouteAction, Verdict, VerdictKind
from omnicompany.protocol.events import EventMetadata, FactoryEvent
from omnicompany.protocol.format import FormatRegistry
from omnicompany.protocol.info_audit import InfoAuditReport, Sufficiency
from omnicompany.protocol.team import (
    InfoAuditMode,
    NodeKind,
    TeamExecutionMode,
    TeamNode,
    TeamSpec,
)
from omnicompany.protocol.registry import EventType
from omnicompany.runtime.llm.llm import get_last_info_audit, use_audit_context
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.signals.stuck import StuckDetector


class NodeMetrics:
    """单节点的运行时度量"""
    __slots__ = ("node_id", "call_count", "total_ms", "total_tokens")

    def __init__(self, node_id: str):
        self.node_id = node_id
        self.call_count = 0
        self.total_ms = 0.0
        self.total_tokens = 0

    def record(self, elapsed_ms: float, tokens: int = 0) -> None:
        self.call_count += 1
        self.total_ms += elapsed_ms
        self.total_tokens += tokens

    def snapshot(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "call_count": self.call_count,
            "total_ms": round(self.total_ms, 1),
            "avg_ms": round(self.total_ms / max(self.call_count, 1), 1),
            "total_tokens": self.total_tokens,
        }


# ── Sticky keys: 后续节点不得覆盖前序节点写入的升级标志 ──
_STICKY_KEYS = {
    "escalate", "escalation_target", "escalation_pain",
    "pain_by_node", "worst_node", "accumulated_pain", "max_node_blame",
}


class TeamRunner:
    """TeamSpec + EventBus 驱动的 DAG 执行器

    每一步 Router.run() 都发射事件到总线，
    完整的执行过程可通过 trace_id 回溯。
    集成 StuckDetector 检测循环。

    DAG 支持:
        - Fan-out: 一个节点的输出触发多个下游并发执行 (asyncio.gather)
        - Fan-in: join barrier 等待所有上游完成后合并输入
        - 反馈边: feedback=True 的边不计入 in-degree，cycle 可正常运行
        - 自动 back-edge 检测: 未声明 feedback 的管线自动用 DFS 检测

    预算控制：
        max_steps 只计算 decision_nodes（默认为 SOFT 类型节点，即 LLM 调用）。
        其他节点（基础设施/观测节点）不消耗预算，但仍有硬上限
        (max_steps * 20) 防止无限循环。
    """

    def __init__(
        self,
        pipeline: TeamSpec,
        bindings: dict[str, Router],
        bus: EventBus,
        *,
        max_steps: int = 50,
        source: str = "pipeline",
        stuck_threshold: int = 6,
        conceptual_threshold: int = 4,
        sub_pipelines: dict[str, TeamSpec] | None = None,
        decision_nodes: set[str] | None = None,
        format_registry: FormatRegistry | None = None,
        record_io: bool = True,
        execution_mode: TeamExecutionMode = TeamExecutionMode.NORMAL,
        info_audit_mode: InfoAuditMode | None = None,
        fallback_handler: Any | None = None,
    ):
        self.pipeline = pipeline
        self.bindings = bindings
        self.bus = bus
        self.format_registry = format_registry
        self.max_steps = max_steps
        self.source = source
        self.record_io = record_io

        # ── Phase 3: 执行模式 + info_audit 模式 ──
        self.execution_mode: TeamExecutionMode = execution_mode
        """NORMAL: 正常跑真 Router.run(); DRY_RUN: 跳过真执行, 只跑 isolated probe
        审计每节点; REPLAY: (预留)用历史 llm_calls 回放对比。"""

        self.info_audit_mode: InfoAuditMode = info_audit_mode or self._resolve_default_audit_mode()
        """OFF / PIGGYBACK / STRICT. 默认按 pipeline 历史跑数次数自动选 (D1)。
        前 10 次 run 走 STRICT, 之后降 PIGGYBACK。"""

        # Phase 3 收集每节点的 info_audit 供横向分析 (不注入 LLM prompt)
        self.node_audit_reports: dict[str, InfoAuditReport] = {}
        """trace_id → node_id → InfoAuditReport 的快照。跨节点聚合分析用, 不喂下游 LLM。"""

        # Phase 3 记录触发的 fallback 意图
        self.fallback_triggers: list[dict[str, Any]] = []
        """每项 = {trace_id, node_id, missing_critical, reason, ts}"""

        # Phase 4 fallback handler (可选)
        # 默认 None = 只记录 trigger 不执行兜底, 和 Phase 3 行为一致
        # 传 UniversalFallbackLoop 实例 = 真正执行兜底 (会烧 token)
        self.fallback_handler: Any | None = fallback_handler
        self.fallback_results: list[dict[str, Any]] = []
        """每项 = {trace_id, node_id, status, output_path, summary, elapsed_s}"""
        self.stuck_detector = StuckDetector(
            repeat_threshold=stuck_threshold,
            conceptual_threshold=conceptual_threshold,
        )
        self.sub_pipelines = sub_pipelines or {}

        self._nodes: dict[str, TeamNode] = {n.id: n for n in pipeline.nodes}

        # ── 边存储（支持 fan-out: 同 key 多 target）──
        self._edges: dict[tuple[str, VerdictKind | None], list[str]] = {}
        self._feedback_pairs: set[tuple[str, str]] = set()
        for edge in pipeline.edges:
            key = (edge.source, edge.condition)
            self._edges.setdefault(key, []).append(edge.target)
            if getattr(edge, "feedback", False):
                self._feedback_pairs.add((edge.source, edge.target))

        # 自动检测 back-edge（如果管线未显式声明任何 feedback 边）
        if not self._feedback_pairs:
            self._feedback_pairs = self._detect_back_edges()

        # ── 计算 in-degree（排除 feedback 边）——用于 join barrier ──
        # 注意：同源不同条件的边只算一次（如 PASS 和 PARTIAL 都去同一 target）
        self._in_degree: dict[str, int] = {n.id: 0 for n in pipeline.nodes}
        seen_pairs: set[tuple[str, str]] = set()
        for edge in pipeline.edges:
            pair = (edge.source, edge.target)
            if pair not in self._feedback_pairs and pair not in seen_pairs:
                seen_pairs.add(pair)
                self._in_degree[edge.target] = self._in_degree.get(edge.target, 0) + 1

        if decision_nodes is not None:
            self._decision_nodes = decision_nodes
        else:
            self._decision_nodes = {
                n.id for n in pipeline.nodes
                if n.anchor and n.anchor.validator.kind.value == "soft"
            }

        self.node_metrics: dict[str, NodeMetrics] = {}
        self.decision_count: int = 0
        self.total_step_count: int = 0
        self.last_output: Any = None
        self.signals: list[Signal] = []      # V1.1: 全链路 Signal 流
        self.format_checks: list[dict] = []  # V1.1: 每节点 Format 校验结果

        if pipeline.min_core_version:
            from omnicompany._core_version import check_compat
            check_compat(pipeline.group or pipeline.id, pipeline.min_core_version)

        _ensure_guardian_running()

    # ── Phase 3 审计辅助 ────────────────────────────────────────────────────

    def _resolve_default_audit_mode(self) -> InfoAuditMode:
        """D1: 每条 pipeline 前 10 次 run 默认 STRICT, 之后 PIGGYBACK。

        实际用 events.db 统计管线 run 次数成本较高, 当前简化: 读环境变量
        OMNICOMPANY_INFO_AUDIT 决定 PIGGYBACK / OFF, STRICT 需显式传参触发。
        """
        env = os.environ.get("OMNICOMPANY_INFO_AUDIT", "").strip().lower()
        if env in ("strict", "2"):
            return InfoAuditMode.STRICT
        if env in ("1", "true", "piggyback"):
            return InfoAuditMode.PIGGYBACK
        return InfoAuditMode.OFF

    def _maybe_trigger_fallback(
        self,
        node_id: str,
        verdict: Verdict,
        trace_id: str,
    ) -> dict | None:
        """规则化判定是否应当触发 fallback; 满足则返回 trigger dict, 否则返回 None。

        规则:
          1. info_audit 存在
          2. missing_info 里有 critical=True 的项
          3. sufficiency ∈ {INSUFFICIENT, PARTIAL}
          4. 全局开关开 (self.info_audit_mode != OFF)

        满足时记录到 self.fallback_triggers + emit NODE_FALLBACK_TRIGGERED 事件,
        **但不执行真实兜底** (真实兜底由 _execute_fallback_if_configured 处理, 需 async)。
        """
        if self.info_audit_mode == InfoAuditMode.OFF:
            return None
        ia = getattr(verdict, "info_audit", None)
        if not ia:
            return None
        critical = ia.missing_critical
        if not critical:
            return None
        if ia.sufficiency not in (Sufficiency.INSUFFICIENT, Sufficiency.PARTIAL):
            return None

        trigger = {
            "trace_id": trace_id,
            "node_id": node_id,
            "missing_critical": list(critical),
            "sufficiency": ia.sufficiency.value,
            "confidence_self": ia.confidence_self,
            "reason": "missing_critical non-empty + global audit enabled",
            "ts": time.time(),
        }
        self.fallback_triggers.append(trigger)
        # 异步 emit 不阻塞: 若当前在 running loop 内就创建 task,
        # 否则(同步测试/离线调用)只记录到 fallback_triggers, 不抛异步事件
        try:
            loop = asyncio.get_running_loop()
            coro = self._emit(
                trace_id, EventType.NODE_FALLBACK_TRIGGERED,
                payload=trigger,
            )
            loop.create_task(coro)
        except RuntimeError:
            pass
        except Exception:
            pass
        return trigger

    async def _execute_fallback_if_configured(
        self,
        trigger: dict,
    ) -> None:
        """如果配置了 fallback_handler, 异步执行一次 UniversalFallbackLoop。

        handler 为 None 时什么都不做 (默认行为)。handler 存在时:
          - 构造 FallbackConfig
          - 调用 handler.handle(config) 拿到 FallbackResult
          - 结果追加到 self.fallback_results

        所有异常被吃掉只记日志, 永不阻塞主管线。
        """
        if self.fallback_handler is None:
            return
        try:
            from omnicompany.runtime.info_audit.fallback import FallbackConfig
            cfg = FallbackConfig(trigger=trigger)
            result = await self.fallback_handler.handle(cfg)
            self.fallback_results.append({
                "trace_id": trigger.get("trace_id"),
                "node_id": trigger.get("node_id"),
                "status": result.status,
                "output_path": result.output_path,
                "summary": result.summary,
                "elapsed_s": result.elapsed_s,
                "turns_used": result.turns_used,
                "reject_log": result.reject_log,
            })
            logger.info(
                "[fallback] node=%s status=%s output=%s elapsed=%.1fs",
                trigger.get("node_id"), result.status, result.output_path, result.elapsed_s,
            )
        except Exception as e:
            logger.warning("[fallback] handler 执行异常: %s", e)

    async def _dry_run_single_node(
        self,
        node: TeamNode,
        router: Router,
        input_data: Any,
    ) -> Verdict:
        """DRY_RUN 模式的单节点执行: 不真的跑 Router.run(), 只跑 isolated probe。

        设计原则 (D2 + 2026-04-09 修正):
          - 跳过真执行, 避免副作用 + 省 token
          - 用 isolated probe 审计每个节点: 读类变量 FORMAT_IN/OUT/DESCRIPTION
          - 如果 llm_audit 有历史真 prompt, 优先喂历史 (P2.5.5)
          - 产出一个 Verdict(kind=PASS, output=input_data, info_audit=probe_report)
            透传 input_data 让下游继续 dry run, 不产真产物

        这让 dry-run 成为一种"信息充分度 CT 扫描": 跑一遍管线只为收 audit 报告,
        不花费真实推理成本, 不产真副作用。
        """
        from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict
        from omnicompany.runtime.info_audit.audit_store import load_historical_llm_calls

        fmt_in, fmt_out = self._get_node_format(node)
        description = getattr(router, "DESCRIPTION", "") or node.id

        # 读历史真 prompt (P2.5.5)
        history = load_historical_llm_calls(
            pipeline_id=self.pipeline.id,
            node_id=node.id,
            last_n=1,
        )
        sys_prev = ""
        user_prev = ""
        resp_prev = ""
        if history:
            h0 = history[0]
            sys_prev = (h0.get("system_prompt") or "")[:2500]
            msgs = h0.get("messages") or []
            for m in msgs:
                if m.get("role") == "user":
                    user_prev = (m.get("content_preview") or "")[:2500]
                    break
            resp_prev = (h0.get("response_text") or "")[:2500]

        try:
            report = await asyncio.to_thread(
                run_info_audit_probe_strict,
                format_in=str(fmt_in) or "(unknown)",
                format_out=str(fmt_out) or "(unknown)",
                description=description,
                original_system=sys_prev,
                original_user_preview=user_prev,
                original_response_preview=resp_prev,
            )
        except Exception as e:
            report = InfoAuditReport.parse_failed(f"dry_run probe failed: {e}")

        return Verdict(
            kind=VerdictKind.PASS,  # DRY_RUN 永远 PASS, 让 runner 继续遍历
            output=input_data,       # 输入透传, 下游节点看到同样的输入
            info_audit=report,
            diagnosis=f"[DRY_RUN] isolated probe report: sufficiency={report.sufficiency.value}",
        )

    def collect_audit_summary(self) -> dict[str, Any]:
        """跨节点聚合分析: 返回本次 run 所有节点的 info_audit 报告 + 衍生观察。

        设计原则 (2026-04-09 用户反馈 #1): 节点是自治的, 这里只做**横向观察**,
        不反向塞数据回任何 LLM prompt。输出给 guardian / CI / 人类审计看。
        """
        reports: list[dict[str, Any]] = []
        all_missing: list[dict[str, Any]] = []
        for node_id, rep in self.node_audit_reports.items():
            d = rep.model_dump()
            d["node_id"] = node_id
            reports.append(d)
            for m in rep.missing_info:
                all_missing.append({
                    "from_node": node_id,
                    "description": m.description,
                    "critical": m.critical,
                    "suggested_source": m.suggested_source,
                })
        return {
            "per_node_reports": reports,
            "all_observed_missing": all_missing,
            "fallback_triggers": list(self.fallback_triggers),
            "node_count_with_audit": len(self.node_audit_reports),
        }

    # ── 拓扑分析 ──────────────────────────────────────────────────────────────

    def _detect_back_edges(self) -> set[tuple[str, str]]:
        """DFS 染色法自动检测 back-edge（cycle 中的回路边）。

        WHITE=未访问, GRAY=在栈中, BLACK=已完成。
        GRAY→GRAY 就是 back-edge。
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._nodes}
        back_edges: set[tuple[str, str]] = set()

        # 构建邻接表
        adj: dict[str, list[str]] = {n: [] for n in self._nodes}
        for edge in self.pipeline.edges:
            if edge.source in adj:
                adj[edge.source].append(edge.target)

        def dfs(u: str) -> None:
            color[u] = GRAY
            for v in adj.get(u, []):
                if color.get(v) == GRAY:
                    back_edges.add((u, v))
                elif color.get(v) == WHITE:
                    dfs(v)
            color[u] = BLACK

        if self.pipeline.entry in color:
            dfs(self.pipeline.entry)

        return back_edges

    # ── 工具方法 ──────────────────────────────────────────────────────────────

    def _get_metrics(self, node_id: str) -> NodeMetrics:
        if node_id not in self.node_metrics:
            self.node_metrics[node_id] = NodeMetrics(node_id)
        return self.node_metrics[node_id]

    def metrics_summary(self) -> dict[str, Any]:
        """返回全部节点度量快照"""
        return {
            "decision_count": self.decision_count,
            "total_step_count": self.total_step_count,
            "nodes": {nid: m.snapshot() for nid, m in self.node_metrics.items()},
        }

    def _get_node_format(self, node: TeamNode) -> tuple[str, str]:
        """从 TeamNode 或 Router 提取 format_in/format_out。

        format_in 可能是 str 或 list[str]（多入）。
        返回时将 list 序列化为 "fmt_a + fmt_b" 形式便于日志/事件展示。
        """
        fmt_in: str | list[str] = ""
        fmt_out: str = ""
        if node.anchor:
            fmt_in, fmt_out = node.anchor.format_in, node.anchor.format_out
        elif node.transformer:
            fmt_in, fmt_out = node.transformer.from_format, node.transformer.to_format
        else:
            router = self.bindings.get(node.id)
            if router:
                fmt_in, fmt_out = router.FORMAT_IN, router.FORMAT_OUT
        # 将 list[str] 序列化为展示字符串
        if isinstance(fmt_in, list):
            fmt_in = " + ".join(fmt_in)
        return fmt_in, fmt_out

    def _get_description(self, node: TeamNode, router: Router) -> str:
        """获取节点的人类可读描述。"""
        return router.DESCRIPTION or node.id

    def _check_format(self, node: TeamNode, verdict: Verdict) -> dict:
        """V1.1: 运行时 Format 校验（WARN 模式）。"""
        _, format_out = self._get_node_format(node)
        if not self.format_registry or not format_out:
            return {"status": "SKIP", "reason": "no registry or format"}
        if not self.format_registry.is_registered(format_out):
            return {"status": "SKIP", "reason": f"{format_out} not registered"}

        fmt = self.format_registry.get(format_out)
        if fmt.json_schema and isinstance(verdict.output, dict):
            required = fmt.json_schema.get("required", [])
            missing = [k for k in required if k not in verdict.output]
            if missing:
                logger.warning("[format_check] %s: missing %s for %s", node.id, missing, format_out)
                return {"status": "WARN", "format": format_out, "missing": missing}

        return {"status": "PASS", "format": format_out}

    # ── 路由 ──────────────────────────────────────────────────────────────────

    def _resolve_next(self, node_id: str, verdict_kind: VerdictKind) -> str | None:
        """返回单个下游 target（向后兼容，用于 RETRY/JUMP 等显式单目标路由）。"""
        targets = self._edges.get((node_id, verdict_kind), [])
        if targets:
            return targets[0]
        targets = self._edges.get((node_id, None), [])
        return targets[0] if targets else None

    def _resolve_next_all(self, node_id: str, verdict_kind: VerdictKind) -> list[str]:
        """返回所有下游 target（支持 fan-out）。"""
        targets = self._edges.get((node_id, verdict_kind), [])
        if targets:
            return list(targets)
        return list(self._edges.get((node_id, None), []))

    # ── 输入合并（fan-in）────────────────────────────────────────────────────

    def _get_format_out_by_id(self, node_id: str) -> str | None:
        """返回指定节点的 format_out（单字符串），用于 composite fan-in key 命名。"""
        node = self._nodes.get(node_id)
        if not node:
            return None
        if node.anchor:
            return node.anchor.format_out or None
        if node.transformer:
            return node.transformer.to_format or None
        router = self.bindings.get(node_id)
        if router:
            fo = router.FORMAT_OUT
            return fo if isinstance(fo, str) and fo else None
        return None

    def _get_raw_format_in(self, node_id: str) -> str | list[str] | None:
        """返回指定节点的 format_in 原始值（不做 join），用于 composite 判断。"""
        node = self._nodes.get(node_id)
        if not node:
            return None
        if node.anchor:
            return node.anchor.format_in or None
        if node.transformer:
            return node.transformer.from_format or None
        router = self.bindings.get(node_id)
        if router:
            fi = router.FORMAT_IN
            return fi if fi else None
        return None

    def _merge_inputs(self, node_id: str, received: dict[str, Verdict]) -> Any:
        """合并多路上游输入。

        单上游: 直传（向后兼容）。
        多上游: 扁平 merge (last-write-wins) + 按源节点 ID 命名空间。
        **特殊处理**: 顶层 `reports` 字典会被**深合并** (P7.3 reports 容器模式),
        让多个验证节点 (compile_checker / lap_verifier / ...) 在 fan-out 后能保留各自报告。

        当目标节点的 format_in 指向已注册的 composite Format 时，改用上游节点的
        format_out 作为 key（即 component Format ID），使 Router.run() 能通过
        input_data["api.spec"] 精确访问各路输入，而非猜测 _from_{node_id}。
        """
        if len(received) == 1:
            return next(iter(received.values())).output

        # 判断目标节点的 format_in 是否是 composite Format
        raw_fmt_in = self._get_raw_format_in(node_id)
        use_format_keys = (
            self.format_registry is not None
            and isinstance(raw_fmt_in, str)
            and self.format_registry.is_composite(raw_fmt_in)
        )

        merged: dict[str, Any] = {}
        accumulated_reports: dict[str, Any] = {}
        for src_id, verdict in received.items():
            output = verdict.output
            # composite Format：用上游的 format_out 作 key；否则沿用 _from_{src_id}
            if use_format_keys:
                fmt_out = self._get_format_out_by_id(src_id)
                key = fmt_out if fmt_out else f"_from_{src_id}"
            else:
                key = f"_from_{src_id}"
            if isinstance(output, dict):
                # 先抽出 reports 做深合并 (其他键走 last-write-wins)
                src_reports = output.get("reports") if isinstance(output.get("reports"), dict) else None
                merged.update(output)
                if src_reports:
                    accumulated_reports.update(src_reports)
                merged[key] = output
            else:
                merged[key] = output
        if accumulated_reports:
            merged["reports"] = accumulated_reports
        return merged

    # ── 事件发射 ──────────────────────────────────────────────────────────────

    async def _emit(
        self,
        trace_id: str,
        event_type: EventType,
        payload: dict[str, Any],
        parent_id: str | None = None,
        metadata: EventMetadata | None = None,
    ) -> FactoryEvent:
        event = FactoryEvent(
            trace_id=trace_id,
            parent_id=parent_id,
            event_type=event_type.value,
            source=self.source,
            payload=payload,
            tags=list(self.pipeline.tags),
            metadata=metadata,
        )
        await self.bus.publish(event)
        return event

    # ── 单节点执行 ────────────────────────────────────────────────────────────

    async def _execute_single_node(
        self,
        node_id: str,
        input_data: Any,
        input_signal: Signal,
        trace_id: str,
        intent_event: FactoryEvent,
        step: int,
        budget_lock: asyncio.Lock | None = None,
    ) -> tuple[Verdict, Signal, str | None]:
        """执行单个节点，发射事件，返回 (verdict, output_signal, route_action)。

        route_action:
            "emit"   — 管线成功完成
            "halt"   — 管线显式停止
            "retry"  — 重试当前节点
            "budget" — 预算耗尽
            "stuck"  — StuckDetector 检测到循环
            None     — 正常继续到下游
        """
        node = self._nodes[node_id]
        router = self.bindings[node_id]

        is_hard = node.anchor and node.anchor.validator.kind.value == "hard"
        is_soft = node.anchor and node.anchor.validator.kind.value == "soft"

        if is_hard:
            enter_type = EventType.TOOL_CALL
        elif is_soft:
            enter_type = EventType.LLM_REQUEST
        else:
            enter_type = EventType.STATE_CHANGE

        format_in, format_out = self._get_node_format(node)
        description = self._get_description(node, router)

        input_summary_text = router.summarize_input(input_data) if isinstance(input_data, dict) else ""

        enter_payload: dict[str, Any] = {
            "step": step,
            "node": node_id,
            "description": description,
            "format_in": format_in,
            "format_out": format_out,
            "node_kind": node.kind.value if node.kind else None,
            "input_signal": {
                "format": input_signal.format,
                "text": input_signal.text[:200],
                "node_id": input_signal.node_id,
            },
            "input_summary": input_summary_text,
        }
        if self.record_io:
            enter_payload["input_data"] = _safe_serialize(input_data)

        node_event = await self._emit(
            trace_id, enter_type,
            payload=enter_payload,
            parent_id=intent_event.id,
        )

        # ── 节点自校验: validate_input ──
        validation_fail = router.validate_input(input_data)
        if validation_fail is not None:
            logger.warning(
                "[step %d] %s INPUT VALIDATION FAILED: %s",
                step, node_id, validation_fail.diagnosis,
            )
            verdict = validation_fail
            duration_ms = 0.0
        else:
            # ── 节点执行 ──
            t0 = time.monotonic()

            # 单一运行时绑定合同。复合 Router 负责把同一上下文递归传给
            # 内部 Router，避免 AgentNodeLoop 的 LLM/tool 事件滞留在构造期
            # placeholder MemoryBus。
            router.bind_runtime_context(
                bus=self.bus,
                parent_event_id=node_event.id,
                trace_id=trace_id,
            )

            from omnicompany.runtime.exec.sub_pipeline import SubTeamWorker
            # SubTeamWorker 之前的专属注入现在由上面的通用路径覆盖，保留 isinstance
            # 检查仅作语义标识（无效果，留作将来 SubPipeline 专属字段扩展钩子）。
            _ = isinstance(router, SubTeamWorker)

            # 运行元数据属于 Worker 实例，不得写进业务 Material。旧实现把
            # `_llm_caller` 塞进 input_data，既污染下游 schema，也会被透传进
            # 最终产物。
            router._llm_caller = (
                f"pipeline.{self.pipeline.name}.{node_id}.step_{step}"
            )

            # ── Phase 2.5 关联机制: 设置 audit_context, 节点内所有 LLM 调用自动继承 ──
            audit_ctx = {
                "trace_id": trace_id,
                "pipeline_id": self.pipeline.id,
                "node_id": node_id,
            }

            fallback_info_audit = None
            with use_audit_context(audit_ctx):
                # ── Phase 3 DRY_RUN 模式: 跳过真 Router.run, 跑 isolated probe ──
                if self.execution_mode == TeamExecutionMode.DRY_RUN:
                    verdict = await self._dry_run_single_node(
                        node, router, input_data,
                    )
                elif node.kind == NodeKind.SCATTER:
                    verdict = await self._run_scatter(node, input_data, node_event)
                elif node.kind == NodeKind.SUB_PIPELINE:
                    verdict = await self._run_sub_pipeline(node, input_data, node_event)
                else:
                    # ── M4 H1 (2026-04-15): REQUIRED_CONTEXT 事前拦截 ──
                    required_ctx = getattr(router, "REQUIRED_CONTEXT", []) or []
                    missing_keys = _check_required_context(input_data, required_ctx)
                    if missing_keys:
                        verdict = Verdict(
                            kind=VerdictKind.FAIL,
                            output=dict(input_data) if isinstance(input_data, dict) else {},
                            diagnosis=(
                                f"REQUIRED_CONTEXT 缺失: {missing_keys}. "
                                f"节点 {type(router).__name__} 声明需要 {required_ctx}, "
                                f"但 input_data 未提供. (M4 事前拦截)"
                            ),
                            error_detail={
                                "missing_required_context": missing_keys,
                                "declared_required": required_ctx,
                                "router": type(router).__name__,
                            },
                        )
                    elif inspect.iscoroutinefunction(router.run):
                        verdict = await router.run(input_data)
                    else:
                        verdict = await asyncio.to_thread(router.run, input_data)
                        if inspect.isawaitable(verdict):
                            verdict = await verdict

                # 在 use_audit_context 作用域内读取最后一次 LLM 调用的 audit
                # (给 14 个不自主提取 info_audit 的 Router 子类的兜底入口)
                fallback_info_audit = get_last_info_audit()

            duration_ms = (time.monotonic() - t0) * 1000

            # ── Phase 3: 收集 info_audit + 规则化触发 fallback 意图 ──
            ia = getattr(verdict, "info_audit", None)
            if ia is None and fallback_info_audit is not None:
                # 14 个 Router 子类不读 resp.info_audit, runner 从 contextvar 兜底补上
                ia = fallback_info_audit
                try:
                    verdict.info_audit = ia
                except Exception:
                    pass

            # ── Phase 3b: post-hoc 兜底 (真实任务 + 专门输出模式) ──
            # 当 piggyback 失败 (长输出忘记追加 JSON 块 / 格式冲突) 时,
            # 读实际 LLM 调用记录, 用完整执行 context 独立调 probe 做审计.
            # 成本: 1 次额外 LLM 调用 / 节点 (仅对 LLM 节点有效; RULE 节点无 LLM 记录,
            # find_last_llm_call 返回 None, 自动跳过).
            # 开关: info_audit_mode != OFF.
            if ia is None and self.info_audit_mode != InfoAuditMode.OFF:
                try:
                    from omnicompany.runtime.info_audit.post_hoc import run_post_hoc_audit
                    anchor = node.anchor
                    fmt_in = anchor.format_in if anchor else (node.transformer.from_format if node.transformer else "")
                    fmt_out = anchor.format_out if anchor else (node.transformer.to_format if node.transformer else "")
                    desc = getattr(router, "DESCRIPTION", "") or (
                        anchor.validator.description if anchor and anchor.validator else ""
                    )
                    post_hoc_report = await asyncio.to_thread(
                        run_post_hoc_audit,
                        trace_id=trace_id,
                        node_id=node_id,
                        format_in=str(fmt_in) if fmt_in else "",
                        format_out=str(fmt_out) if fmt_out else "",
                        description=str(desc)[:500],
                    )
                    if post_hoc_report is not None:
                        ia = post_hoc_report
                        try:
                            verdict.info_audit = ia
                        except Exception:
                            pass
                        logger.info(
                            "[post_hoc] %s: sufficiency=%s missing=%d concerns=%d",
                            node_id,
                            post_hoc_report.sufficiency.value,
                            len(post_hoc_report.missing_info),
                            len(post_hoc_report.concerns),
                        )
                except Exception as _post_hoc_err:
                    logger.debug("post_hoc_audit failed for %s: %s", node_id, _post_hoc_err)

            # ── M3 (2026-04-15): AgentNodeLoop 经验沉淀 (可拔插) ──
            # 若本节点是 AgentNodeLoop (或持有 _last_agent_loop) 且 OMNICOMPANY_CRYSTALLIZE 开启,
            # 从 loop 实例读 trace → 跑 crystallizer → SpecPatch 落 pending/
            try:
                from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
                agent_loop_obj = None
                if isinstance(router, AgentNodeLoop):
                    agent_loop_obj = router
                elif hasattr(router, "_last_agent_loop"):
                    inner = getattr(router, "_last_agent_loop", None)
                    if isinstance(inner, AgentNodeLoop):
                        agent_loop_obj = inner
                if agent_loop_obj is not None:
                    from omnicompany.runtime.agent_crystallize import (
                        get_enabled_crystallizers, run_crystallize, build_agent_loop_trace,
                        TraceSummarizer, FormatEdgeInferrer, DescriptionRefiner,
                    )
                    from omnicompany.runtime.agent_crystallize.trace_accumulator import (
                        increment_trace_count,
                    )
                    # 累积 trace 计数，驱动 N≥3 自动开启
                    trace_count = increment_trace_count(self.pipeline.id, node_id)
                    env_crystallizers = get_enabled_crystallizers()
                    if env_crystallizers:
                        # env var 显式指定优先
                        crystallizers = env_crystallizers
                    elif trace_count >= 3:
                        # 积累够了 → 开全套（含 DescriptionRefiner + self-judge）
                        crystallizers = [TraceSummarizer(), FormatEdgeInferrer(), DescriptionRefiner()]
                        logger.info("[crystallize] %s: trace_count=%d >= 3, 自动开启全套 crystallizer",
                                    node_id, trace_count)
                    else:
                        # 还不够 → 只记录，不产 patch
                        crystallizers = [TraceSummarizer()]
                        logger.debug("[crystallize] %s: trace_count=%d, 只记录 trace",
                                     node_id, trace_count)
                    if crystallizers:
                        anchor = node.anchor
                        fmt_in = anchor.format_in if anchor else (node.transformer.from_format if node.transformer else "")
                        fmt_out = anchor.format_out if anchor else (node.transformer.to_format if node.transformer else "")
                        desc = getattr(router, "DESCRIPTION", "") or ""
                        trace = build_agent_loop_trace(
                            agent_loop_obj,  # 用 loop 实例 (不是 outer router)
                            node_id=node_id,
                            format_in=str(fmt_in or ""),
                            format_out=str(fmt_out or ""),
                            description=str(desc),
                            input_data=input_data if isinstance(input_data, dict) else {},
                            finished_reason=str(getattr(verdict, "diagnosis", "") or "unknown")[:80],
                        )
                        downstream_eval = {
                            "node_verdict": verdict.kind.value if hasattr(verdict, "kind") else "?",
                            "confidence": getattr(verdict, "confidence", None),
                        }
                        patches = run_crystallize(
                            crystallizers, trace,
                            downstream_eval=downstream_eval,
                            output_dir=None,  # 用默认 data/crystallize/
                        )
                        # 落盘: write_pending_patch 在 run_crystallize 里对每个 patch 已调
                        from omnicompany.runtime.agent_crystallize.pending_queue import write_pending_patch
                        for p in patches:
                            try:
                                write_pending_patch(p)
                            except Exception:
                                pass
                        logger.info(
                            "[crystallize] %s: %d patches from %d crystallizers",
                            node_id, len(patches), len(crystallizers),
                        )
            except Exception as _crystallize_err:
                logger.debug("crystallize failed for %s: %s", node_id, _crystallize_err)

            # ── Phase 4: 真实 fallback 执行 (如果配置了 handler) ──
            # _maybe_trigger_fallback 会在 node_audit_reports 之后被调用, 这里先
            # 把 verdict 的 audit 记录好, 然后调用规则判定+执行
            # (注意: _maybe_trigger_fallback 已经在下面 ia is not None 分支里调用了,
            #  这里不重复, 只追加 Phase 4 执行)
            if ia is not None:
                self.node_audit_reports[node_id] = ia
                try:
                    await self._emit(
                        trace_id, EventType.NODE_INFO_AUDIT_REPORTED,
                        payload={
                            "node_id": node_id,
                            "sufficiency": ia.sufficiency.value,
                            "missing_critical": ia.missing_critical,
                            "confidence_self": ia.confidence_self,
                            "mode": self.info_audit_mode.value,
                        },
                        parent_id=node_event.id,
                    )
                except Exception:
                    pass  # 永不阻塞主路径
                trigger = self._maybe_trigger_fallback(node_id, verdict, trace_id)
                if trigger is not None:
                    # Phase 4: 如果配了 handler, 真执行兜底 (否则只记录)
                    await self._execute_fallback_if_configured(trigger)

            # ── 节点自校验: validate_output ──
            output_fail = router.validate_output(verdict)
            if output_fail is not None:
                logger.warning(
                    "[step %d] %s OUTPUT VALIDATION FAILED: %s",
                    step, node_id, output_fail.diagnosis,
                )
                verdict = output_fail
                duration_ms = 0.0

        # ── Sticky keys 保留 ──
        if isinstance(verdict.output, dict):
            prev = self.last_output if isinstance(self.last_output, dict) else {}
            preserved = {
                k: v for k, v in prev.items()
                if k in _STICKY_KEYS and verdict.output.get(k) in (None, False, "", 0, 0.0, {})
            }
            self.last_output = {**preserved, **verdict.output}

        # ── 度量记录（并发安全）──
        is_passthrough = getattr(router, "PASSTHROUGH", False)
        if budget_lock:
            async with budget_lock:
                self.total_step_count += 1
                is_decision = node_id in self._decision_nodes and not is_passthrough
                if is_decision:
                    self.decision_count += 1
                local_decision_count = self.decision_count
                local_max_steps = self.max_steps
        else:
            self.total_step_count += 1
            is_decision = node_id in self._decision_nodes and not is_passthrough
            if is_decision:
                self.decision_count += 1
            local_decision_count = self.decision_count
            local_max_steps = self.max_steps

        token_count = 0
        if is_soft:
            if isinstance(verdict.output, dict):
                token_count = verdict.output.get("_token_count", 0)
            if not token_count:
                token_count = getattr(router, "last_token_count", 0)
        self._get_metrics(node_id).record(duration_ms, token_count)

        # ── 结构化进度日志 ──
        diag_short = (verdict.diagnosis or "")[:80]
        dc_tag = f" [D{local_decision_count}/{local_max_steps}]" if is_decision else ""
        logger.info(
            "[step %d] %s → %s (%.0fms)%s %s",
            step, node_id, verdict.kind.value,
            duration_ms, dc_tag, diag_short,
        )

        # ── 发射节点结果事件 ──
        _MAX_CONTENT = 2000

        output_summary_text = router.summarize_output(verdict)
        format_check = self._check_format(node, verdict)
        self.format_checks.append({"node": node_id, **format_check})

        output_signal = Signal(
            format=format_out or "unknown",
            text=output_summary_text or verdict.diagnosis or "",
            node_id=node_id,
            meta=verdict.output if isinstance(verdict.output, dict) else {},
        )
        self.signals.append(output_signal)

        result_payload: dict[str, Any] = {
            "step": step,
            "node": node_id,
            "description": description,
            "verdict": verdict.kind.value,
            "format_in": format_in,
            "format_out": format_out,
            "output_signal": {
                "format": output_signal.format,
                "text": output_signal.text[:200],
                "node_id": output_signal.node_id,
            },
            "output_summary": output_summary_text,
            "format_check": format_check,
        }
        if verdict.diagnosis:
            result_payload["diagnosis"] = verdict.diagnosis

        if self.record_io:
            result_payload["output_data"] = _safe_serialize(verdict.output)

        if is_soft and isinstance(verdict.output, dict):
            tool_calls_detail = []
            for tc in verdict.output.get("tool_calls", []):
                tc_info = {
                    "tool_name": tc.get("tool_name", "?"),
                    "tool_use_id": tc.get("tool_use_id", ""),
                }
                args = tc.get("tool_args", {})
                if isinstance(args, dict):
                    for k, v in args.items():
                        tc_info[k] = str(v)[:_MAX_CONTENT] if isinstance(v, str) else v
                tool_calls_detail.append(tc_info)
            if tool_calls_detail:
                result_payload["tool_calls"] = tool_calls_detail
            text = verdict.output.get("text", "")
            if text:
                result_payload["llm_text"] = text[:_MAX_CONTENT]
        elif is_soft and isinstance(verdict.output, str):
            result_payload["llm_text"] = verdict.output[:_MAX_CONTENT]

        if is_hard and isinstance(verdict.output, dict):
            tool_results_detail = []
            for tr in verdict.output.get("tool_results", []):
                tr_info = {
                    "tool_use_id": tr.get("tool_use_id", ""),
                    "content": str(tr.get("content", ""))[:_MAX_CONTENT],
                }
                tool_results_detail.append(tr_info)
            if tool_results_detail:
                result_payload["tool_results"] = tool_results_detail

        if is_hard:
            exit_type = EventType.TOOL_RESULT
            meta = EventMetadata(duration_ms=duration_ms, tool_name=node_id)
        elif is_soft:
            exit_type = EventType.LLM_RESPONSE
            meta = EventMetadata(latency_ms=duration_ms)
        else:
            exit_type = EventType.STATE_CHANGE
            meta = EventMetadata(duration_ms=duration_ms)

        await self._emit(
            trace_id, exit_type,
            payload=result_payload,
            parent_id=node_event.id,
            metadata=meta,
        )

        # ── StuckDetector ──
        _stuck_record_nodes = {"llm", "tool_dispatch"}
        if is_soft and node_id in _stuck_record_nodes:
            output = verdict.output
            if verdict.kind == VerdictKind.FAIL and isinstance(output, dict):
                self.stuck_detector.record({
                    "tool_calls": output.get("tool_calls"),
                    "text_output": output.get("text"),
                })
            elif verdict.kind == VerdictKind.PASS:
                self.stuck_detector.record({
                    "tool_calls": None,
                    "text_output": str(output)[:200] if output else "",
                })
        elif is_hard:
            output = verdict.output
            if isinstance(output, dict) and self.stuck_detector._history:
                self.stuck_detector._history[-1]["tool_results"] = output.get("tool_results")

            if self.stuck_detector.is_stuck():
                analysis = self.stuck_detector.stuck_analysis
                error_msg = f"Agent stuck in loop: {analysis.loop_type} ({analysis.repeat_times}x)"
                await self._emit(
                    trace_id, EventType.TASK_ERROR,
                    payload={"error": error_msg, "node": node_id},
                    parent_id=intent_event.id,
                )
                logger.warning("[runner] %s — skipping CL (returning FAIL)", error_msg)
                return (
                    Verdict(kind=VerdictKind.FAIL, diagnosis=error_msg),
                    output_signal,
                    "stuck",
                )

        # ── 预算检查 ──
        if local_decision_count >= local_max_steps:
            budget_msg = (
                f"Decision budget exhausted: {local_decision_count}/{local_max_steps} "
                f"decisions in {self.total_step_count} total steps"
            )
            await self._emit(
                trace_id, EventType.TASK_ERROR,
                payload={"error": budget_msg, "node": node_id,
                         "metrics": self.metrics_summary()},
                parent_id=intent_event.id,
            )
            return verdict, output_signal, "budget"

        # ── 路由动作解析 ──
        anchor = node.anchor
        if anchor and verdict.kind in anchor.routes:
            route = anchor.routes[verdict.kind]

            if route.action == RouteAction.EMIT:
                await self._emit(
                    trace_id, EventType.TASK_FINISH,
                    payload={"step": step, "node": node_id},
                    parent_id=intent_event.id,
                )
                return verdict, output_signal, "emit"

            if route.action == RouteAction.HALT:
                await self._emit(
                    trace_id, EventType.TASK_ERROR,
                    payload={"error": verdict.diagnosis or "halted", "node": node_id},
                    parent_id=intent_event.id,
                )
                return verdict, output_signal, "halt"

            if route.action == RouteAction.RETRY:
                return verdict, output_signal, "retry"

            # NEXT / JUMP — 由 _run_dag 处理下游分发
            return verdict, output_signal, None

        # Transformer / 无 anchor 路由 — 正常继续
        return verdict, output_signal, None

    # ── Scatter 子执行 ────────────────────────────────────────────────────────

    async def _run_sub_pipeline(
        self, node: TeamNode, input_data: Any, node_event: FactoryEvent,
    ) -> Verdict:
        """递归执行 SUB_PIPELINE 节点（嵌套管线）。

        共享父管线的 bindings / bus / format_registry，
        以 node_event.id 为 parent_event_id，使事件树层次清晰可审计。
        """
        sub_spec = node.sub_pipeline
        if sub_spec is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"SUB_PIPELINE node '{node.id}' has no sub_pipeline spec attached",
                error_detail={"node_id": node.id},
            )

        sub_runner = TeamRunner(
            pipeline=sub_spec,
            bindings=self.bindings,
            bus=self.bus,
            format_registry=self.format_registry,
            record_io=self.record_io,
            sub_pipelines=self.sub_pipelines,
            max_steps=self.max_steps,
            source=self.source,
            execution_mode=self.execution_mode,
        )

        try:
            result = await sub_runner.run(input_data, parent_event_id=node_event.id)
            return Verdict(
                kind=VerdictKind.PASS,
                output=result,
                confidence=1.0,
                diagnosis=f"Sub-pipeline '{sub_spec.id}' 完成",
            )
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"Sub-pipeline '{sub_spec.id}' 执行异常: {exc}",
                error_detail={"exception": str(exc), "node_id": node.id},
            )

    async def _run_scatter(
        self, node: TeamNode, current_input: Any, node_event: FactoryEvent,
    ) -> Verdict:
        """执行 Scatter 节点：并发子管线 + 结果汇总。"""
        scatter_spec = node.scatter
        items = current_input.get(scatter_spec.iterable_key, []) if isinstance(current_input, dict) else []
        if not isinstance(items, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"Iterable '{scatter_spec.iterable_key}' not found or not a list",
            )
        if scatter_spec.sub_pipeline not in self.sub_pipelines:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"Sub-pipeline '{scatter_spec.sub_pipeline}' not found in sub_pipelines",
            )

        sub_spec = self.sub_pipelines[scatter_spec.sub_pipeline]
        semaphore = asyncio.Semaphore(scatter_spec.max_concurrency)

        async def _run_sub_item(idx: int, item: Any) -> Any:
            async with semaphore:
                child_runner = TeamRunner(
                    pipeline=sub_spec,
                    bindings=self.bindings,
                    bus=self.bus,
                    max_steps=self.max_steps,
                    source=f"{self.source}/{node.id}[{idx}]",
                    sub_pipelines=self.sub_pipelines,
                )
                try:
                    return await child_runner.run(item, parent_event_id=node_event.id)
                except Exception as e:
                    return {"_error": str(e), "_item_index": idx}

        coros = [_run_sub_item(i, item) for i, item in enumerate(items)]
        scatter_results = await asyncio.gather(*coros)

        if isinstance(current_input, dict):
            out_dict = dict(current_input)
            out_dict["scatter_results"] = scatter_results
        else:
            out_dict = {"original_input": current_input, "scatter_results": scatter_results}

        return Verdict(kind=VerdictKind.PASS, output=out_dict)

    # ── DAG 执行器（核心）────────────────────────────────────────────────────

    async def run(self, initial_input: Any, parent_event_id: str | None = None) -> Any:
        """执行管线（统一 DAG 执行器，线性管线自然退化）。

        预算规则：
            - decision_count 只在 decision_nodes（默认 SOFT/LLM 节点）+1
            - decision_count >= max_steps 时停止
            - hard_limit = max_steps * 20 防止无限循环
        """
        trace_id = str(ULID())
        self.last_trace_id = trace_id
        hard_limit = self.max_steps * 20

        # 从 initial_input 中提取可序列化的摘要
        input_summary = {}
        if isinstance(initial_input, dict):
            for k, v in initial_input.items():
                if isinstance(v, (str, int, float, bool)):
                    input_summary[k] = v
                elif isinstance(v, list) and len(v) <= 10:
                    input_summary[k] = v
                elif callable(v):
                    input_summary[k] = "<callable>"
                else:
                    input_summary[k] = f"<{type(v).__name__}>"

        intent_event = await self._emit(
            trace_id, EventType.TASK_INTENT,
            payload={
                "pipeline": self.pipeline.id,
                "entry": self.pipeline.entry,
                "input_summary": input_summary,
            },
            parent_id=parent_event_id,
        )

        # V1.1: 初始 Signal
        entry_node = self._nodes.get(self.pipeline.entry)
        entry_format_in = self._get_node_format(entry_node)[0] if entry_node else ""
        entry_signal = Signal(
            format=entry_format_in or "pipeline.input",
            text=f"Pipeline {self.pipeline.id} 启动",
            node_id="entry",
            meta=initial_input if isinstance(initial_input, dict) else {},
        )

        # ── DAG 执行状态 ──
        budget_lock = asyncio.Lock()
        join_received: dict[str, dict[str, Verdict]] = {}  # join_node → {src_id: Verdict}
        join_signals: dict[str, Signal] = {}  # join_node → last arriving signal
        step_counter = [0]  # mutable counter shared across concurrent branches
        retry_counter: dict[str, int] = {}  # per-node retry tracking (enforces Route.max_retries)
        final_result: list[Any] = [None]  # mutable container for EMIT result
        pipeline_terminated = asyncio.Event()

        async def execute_node(
            node_id: str,
            input_data: Any,
            input_signal: Signal,
        ) -> None:
            """递归执行节点，fan-out 时 gather 多个子任务。"""
            # 硬上限保护
            if step_counter[0] >= hard_limit or pipeline_terminated.is_set():
                return

            step = step_counter[0]
            step_counter[0] += 1

            verdict, out_signal, action = await self._execute_single_node(
                node_id, input_data, input_signal,
                trace_id, intent_event, step, budget_lock,
            )

            if pipeline_terminated.is_set():
                return

            # 处理终止动作
            if action == "emit":
                final_result[0] = verdict.output
                pipeline_terminated.set()
                return

            if action == "halt":
                pipeline_terminated.set()
                raise RuntimeError(
                    f"Pipeline halted at '{node_id}': {verdict.diagnosis}"
                )

            if action == "budget":
                last = self.last_output if isinstance(self.last_output, dict) else {}
                final_result[0] = {
                    **last,
                    "budget_exhausted": True,
                    "budget_msg": verdict.diagnosis or "budget exhausted",
                    "decision_count": self.decision_count,
                    "max_steps": self.max_steps,
                }
                pipeline_terminated.set()
                return

            if action == "stuck":
                final_result[0] = Verdict(kind=VerdictKind.FAIL, diagnosis=verdict.diagnosis)
                pipeline_terminated.set()
                return

            if action == "retry":
                # Enforce per-node retry budget (Route.max_retries) — prevent infinite loop
                node = self._nodes[node_id]
                anchor = node.anchor
                max_retries = 3  # default
                if anchor and verdict.kind in anchor.routes:
                    rt = anchor.routes[verdict.kind]
                    if hasattr(rt, "max_retries") and rt.max_retries is not None:
                        max_retries = rt.max_retries
                retry_counter[node_id] = retry_counter.get(node_id, 0) + 1
                if retry_counter[node_id] > max_retries:
                    logger.warning(
                        "[runner] node=%s exceeded max_retries=%d, treating as terminal FAIL",
                        node_id, max_retries,
                    )
                    final_result[0] = Verdict(
                        kind=VerdictKind.FAIL,
                        diagnosis=f"Node {node_id} exceeded max_retries={max_retries}: {verdict.diagnosis}",
                    )
                    pipeline_terminated.set()
                    return
                # 重试当前节点
                await execute_node(node_id, input_data, input_signal)
                return

            # ── 正常继续：分发到下游 ──
            # 对于 NEXT/JUMP 路由，优先用 route.target
            anchor = self._nodes[node_id].anchor
            explicit_target = None
            if anchor and verdict.kind in anchor.routes:
                route = anchor.routes[verdict.kind]
                if route.action in (RouteAction.NEXT, RouteAction.JUMP) and route.target:
                    explicit_target = route.target

            if explicit_target:
                targets = [explicit_target]
            else:
                targets = self._resolve_next_all(node_id, verdict.kind)

            if not targets:
                # 终端节点 — 无下游
                return

            tasks: list[Any] = []
            for target_id in targets:
                is_fb = (node_id, target_id) in self._feedback_pairs

                if self._in_degree.get(target_id, 0) <= 1 or is_fb:
                    # 无需 join — 直接调度
                    tasks.append(execute_node(target_id, verdict.output, out_signal))
                else:
                    # ── Join barrier ──
                    if target_id not in join_received:
                        join_received[target_id] = {}
                    join_received[target_id][node_id] = verdict
                    join_signals[target_id] = out_signal

                    if len(join_received[target_id]) >= self._in_degree[target_id]:
                        # 所有上游到齐 — 合并输入，执行
                        merged = self._merge_inputs(target_id, join_received.pop(target_id))
                        merged_signal = join_signals.pop(target_id, out_signal)
                        tasks.append(execute_node(target_id, merged, merged_signal))

            if len(tasks) == 1:
                # 单下游：直接 await（避免 gather 开销，保持顺序语义）
                await tasks[0]
            elif len(tasks) > 1:
                # Fan-out：并发执行
                await asyncio.gather(*tasks)

        # ── 启动执行 ──
        try:
            await execute_node(self.pipeline.entry, initial_input, entry_signal)
        except RuntimeError:
            raise  # HALT 等 RuntimeError 直接抛出

        # 硬上限检查
        if step_counter[0] >= hard_limit and final_result[0] is None:
            hard_msg = (
                f"Hard step limit reached ({hard_limit} total steps, "
                f"{self.decision_count}/{self.max_steps} decisions used)"
            )
            await self._emit(
                trace_id, EventType.TASK_ERROR,
                payload={"error": hard_msg, "metrics": self.metrics_summary()},
                parent_id=intent_event.id,
            )
            raise RuntimeError(hard_msg)

        # ── 接入点 2: 汇聚节点信息审计结果到 pipeline_health.jsonl ──
        if self.node_audit_reports:
            try:
                from omnicompany.runtime.info_audit.pipeline_health import (
                    append_pipeline_health, _domain_from_pipeline_id,
                )
                append_pipeline_health(
                    pipeline_id=self.pipeline.id,
                    domain=_domain_from_pipeline_id(self.pipeline.id),
                    trace_id=trace_id,
                    node_reports=self.node_audit_reports,
                )
            except Exception:
                pass  # 永不阻塞主流程

        return final_result[0] if final_result[0] is not None else self.last_output


# ── OmniSentinel 自启动（P5）────────────────────────────────────────────────

def _ensure_guardian_running() -> None:
    """Ping sentinel activity_ts. **不再自动 spawn daemon**.

    Sentinel 是独立长驻进程 (2026-04-10 重构), 但当前阶段由用户/运维**手动**
    启动 (`omni guardian daemon`). 本函数只负责"告诉 sentinel 有新活动":
    写 .omni/core_activity_ts.json 一下. 如果 sentinel 在跑, 它会读到并
    在冷却期过后做增量 patrol; 如果 sentinel 没在跑, 这次写入就是无损的.

    重要: 之前版本 (2026-04-10 更早) 在这里调用 sentinel.ensure_daemon_running()
    会 spawn detached 子进程. 但 run_patrol 内部的 LLM Judge 链路会创建
    TeamRunner → 再次调用 _ensure_guardian_running → 再次 spawn, 造成
    **递归 spawn 风暴** (被杀的 44 个进程). 已取消自动 spawn, 改由 user 控制.

    如遇任何异常, 静默降级 —— 不阻塞主管线运行.
    """
    try:
        from omnicompany.packages.services._core.guardian import sentinel
        sentinel.write_activity_ts(source="pipeline-runner")
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(
            "[sentinel] write_activity_ts 跳过 (guardian 未安装或出错): %s", e
        )


# ── 过渡期别名 (命名迁移 B 层, 2026-04-22) ──
PipelineRunner = TeamRunner
