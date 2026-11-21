# [OMNI] origin=claude-code domain=omnicompany/omnicompany ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:omnicompany.worker_activation.dispatcher.engine.py"
"""MaterialDispatcher — run-scoped Material ledger subscription dispatcher.

Authority boundary:
- Material bodies and activation order live in the private run ledger.
- EventBus is an optional, bodyless audit projection only.
- A missing or failed EventBus cannot change Material execution results.

对齐黑板架构 plan:
- Q1: 单次激活 (trace_id, worker_id) → 每 job 激活一次
- run ledger = 临时 Material stock, FactoryEvent = 兼容返回信封
- Worker.FORMAT_IN = 订阅的 event_type
- Worker.FORMAT_OUT = 产出的 event_type
- trace_id = job_id（贯穿所有 material）
- parent_id = 触发本激活的 material event.id（因果链 / Q1.C child job 基础）

运行模型（最小版）:
1. 发布初始 material event
2. 循环读取 run ledger 里的 Material
3. 按 event_type 匹配订阅 worker → 激活 → 产 Verdict
4. Verdict.output 的每条 {format_id: data} 发布为新 event
5. 直到 stock 内无新 event（或达 max_iterations）

composite FORMAT_IN (list[str]) 支持:
- 按 worker 维护"待齐 material 集", 所有订阅的 material 都在 ledger 里才激活
- 单次激活语义不变

本 dispatcher 是旧 MaterialDispatcher API 的兼容执行器；跨 run 的 canonical
Material 查询/订阅由 Material authority/BlackboardCoordinator 提供，不由 EventBus 提供。
验证路径:
- Team 1 guardian 4 Worker 通过 dispatcher 跑 scan_request → audit_log.entry 全链路
- 跑通 = 证明 ledger 订阅图自洽，与 EventBus 可用性无关
- 跑不通 = 暴露之前不严谨 (F-15 透传 / F-16 kind 错标 / 孤儿 worker / 冗余 material)

不做的事:
- 不替 TeamRunner（TeamRunner 是显式 DAG 编排, MaterialDispatcher 是材料黑板激活）
- 不做 fan-out 并发（当前串行激活, Q3 需求 Phase 2 再说）
- 不做 Q2 预算 (单 job 硬 max_iterations, 后续扩 max_workers_per_job 等)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any

from omnicompany.bus.base import EventBus
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.protocol.events import FactoryEvent
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _RunMaterialLedger:
    """Canonical run-local Material bodies; never backed by EventBus."""

    events: list[FactoryEvent] = field(default_factory=list)

    def append(self, event: FactoryEvent) -> None:
        self.events.append(event)

    def at(self, cursor: int) -> FactoryEvent:
        return self.events[cursor]

    def __len__(self) -> int:
        return len(self.events)


def _body_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _audit_projection(material: FactoryEvent) -> FactoryEvent:
    """Project Material identity/provenance without copying its body."""
    return FactoryEvent(
        trace_id=material.trace_id,
        event_type="material.published",
        source="material_dispatcher.audit",
        payload={
            "material_ref": f"material-event:{material.id}",
            "format_id": material.event_type,
            "producer": material.source,
            "causation_ref": material.parent_id,
            "body_sha256": _body_digest(material.payload),
        },
        tags=["omni.material.projection", "material.published"],
    )


def _worker_id(worker: Router) -> str:
    """Worker 标识符 (用于 activation 去重 + source 字段)."""
    return worker.__class__.__name__


def _format_in_set(worker: Router) -> set[str] | None:
    """获取 worker 订阅的 FORMAT_IN 集合."""
    fmt_in = getattr(worker, "FORMAT_IN", None)
    if not fmt_in:
        return None
    if isinstance(fmt_in, str):
        return {fmt_in}
    if isinstance(fmt_in, (list, tuple)):
        return set(fmt_in)
    return None


def _format_in_mode(worker: Router) -> str:
    """FORMAT_IN 多值时的语义 (composite AND / alternative OR).

    - "and" (默认, composite fan-in): 所有 FORMAT_IN 都到齐才激活
    - "or" (alternative): 任一 FORMAT_IN 到达即激活

    Agent Team 的 ContextScript 订阅 agent.request OR agent.tool_result,
    需 FORMAT_IN_MODE="or". 普通 fan-in (workflow_factory 等) 默认 "and".
    """
    return getattr(worker, "FORMAT_IN_MODE", "and")


class MaterialDispatcher:
    """Worker × direct Material ledger activation driver.

    用法:
        dispatcher = MaterialDispatcher(workers=[W1, W2, W3, W4])
        events = await dispatcher.run_job(
            initial_material_id="guardian.scan_request",
            initial_payload={"scan_mode": "diff", ...},
        )
        # events 含所有产生的 FactoryEvent (1 source + N internal + M sink)
    """

    def __init__(
        self,
        workers: list[Router],
        bus: EventBus | None = None,
        *,
        max_iterations: int = 100,
    ):
        self._workers = workers
        self._audit_bus = bus
        self._max_iterations = max_iterations
        # (trace_id, worker_id) → 已激活 (Q1 单次激活)
        self._activated: set[tuple[str, str]] = set()

    async def run_job(
        self,
        initial_material_id: str,
        initial_payload: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> list[FactoryEvent]:
        """运行一个 job: 写入 run ledger → 驱动订阅激活 → 返回 material 信封."""
        from ulid import ULID

        if not job_id:
            job_id = str(ULID())

        initial_event = FactoryEvent(
            trace_id=job_id,
            event_type=initial_material_id,
            source="dispatcher.initial",
            payload=initial_payload,
        )
        ledger = _RunMaterialLedger([initial_event])
        # Worker 等齐 material 用的累计字典: (job_id, worker_id) → {format_id: event}
        # 按 job 分键: 子 job 独立累计 (agent 多轮不串味)
        pending: dict[tuple[str, str], dict[str, FactoryEvent]] = {}

        cursor = 0
        iter_count = 0

        while cursor < len(ledger) and iter_count < self._max_iterations:
            current = ledger.at(cursor)
            cursor += 1
            iter_count += 1

            # 当前 event 所属的 job = event.trace_id (可能是主 job 或子 job)
            current_job = current.trace_id

            for worker in self._workers:
                wid = _worker_id(worker)
                fmt_in = _format_in_set(worker)

                if fmt_in is None or current.event_type not in fmt_in:
                    continue

                # Q1: (job_id, worker_id) 单次激活 — 每个 job 里 worker 激活一次
                # 子 job 自带独立 trace_id, 因此 agent 每轮循环 LLM Worker 能再激活
                key = (current_job, wid)
                if key in self._activated:
                    continue

                # 按 FORMAT_IN_MODE 决定累计策略
                mode = _format_in_mode(worker)
                buf_key = (current_job, wid)

                if mode == "or":
                    # alternative: 任一 material 激活, 只用当前 event
                    buf = {current.event_type: current}
                else:
                    # and (composite fan-in): 累计到齐才激活
                    buf = pending.setdefault(buf_key, {})
                    buf[current.event_type] = current
                    if not fmt_in.issubset(buf.keys()):
                        continue  # 未到齐

                # 激活
                self._activated.add(key)
                # OR 模式下 only current event 作为输入; AND 模式下齐全集
                input_data: dict[str, Any] = {
                    fmt: ev.payload for fmt, ev in buf.items() if fmt in fmt_in
                }
                # 确定 parent_id: 最后一个凑齐的 event
                parent_id = current.id
                # 继承 job_id 用于子输出 (非子 job 场景)
                job_id = current_job

                logger.info("activate: %s on job=%s (FORMAT_IN=%s)", wid, current_job, fmt_in)
                verdict = await self._invoke_worker_async(worker, input_data)

                if verdict.kind != VerdictKind.PASS:
                    logger.warning("worker %s FAIL: %s", wid, verdict.diagnosis)
                    continue

                # 发布产出 material
                # Protocol 约定: verdict.output 是 FORMAT_OUT 对应 Format 的 payload 本体
                # (平铺字段 dict), event_type = worker.FORMAT_OUT
                #
                # 子 job 机制 (Agent Team 轮次因果): output 带特殊字段 `_emit_as_new_job: True`
                # → 产出事件用新 job_id (parent_job_id = 当前 job), agent 每轮循环 = 新子 job
                fmt_out = getattr(worker, "FORMAT_OUT", None)
                output = verdict.output
                if fmt_out and output is not None:
                    if isinstance(output, dict):
                        payload = dict(output)  # 拷贝防止 mutate caller
                    else:
                        payload = {"_value": output}

                    emit_as_new_job = bool(payload.pop("_emit_as_new_job", False))
                    if emit_as_new_job:
                        from ulid import ULID
                        child_job_id = str(ULID())
                        event_trace = child_job_id
                        event_parent = current.id  # 触发子 job 的 material event id
                        payload["_parent_job_id"] = job_id  # 记录链(Q1.C)
                    else:
                        event_trace = job_id
                        event_parent = parent_id

                    new_event = FactoryEvent(
                        trace_id=event_trace,
                        parent_id=event_parent,
                        event_type=fmt_out,
                        source=f"worker.{wid}",
                        payload=payload,
                    )
                    ledger.append(new_event)
                # 清理 pending (本 worker 本 job 已激活)
                pending.pop(buf_key, None)

        produced = ledger.events
        await self._project_audit(produced)

        if iter_count >= self._max_iterations:
            logger.warning(
                "dispatcher hit max_iterations=%d (job=%s, produced=%d)",
                self._max_iterations, job_id, len(produced),
            )

        return produced

    async def _project_audit(self, materials: list[FactoryEvent]) -> None:
        """Best-effort bodyless projection; never an execution dependency."""
        if self._audit_bus is None:
            return
        try:
            await self._audit_bus.connect()
            for material in materials:
                await self._audit_bus.publish(_audit_projection(material))
        except Exception as error:  # noqa: BLE001 - audit loss cannot replace Material truth
            logger.warning("material audit projection failed: %s", error)
        finally:
            try:
                await self._audit_bus.close()
            except Exception as error:  # noqa: BLE001 - best-effort audit lifecycle
                logger.warning("material audit bus close failed: %s", error)

    # ──────────────────────────────────────────────────────────
    # 激活 (处理 sync/async worker.run)
    # ──────────────────────────────────────────────────────────

    async def _invoke_worker_async(self, worker: Router, input_data: dict[str, Any]) -> Verdict:
        """激活 worker, 支持 async Worker (AgentNodeLoop 子类).

        2026-04-20: 从 sync 版升级, 解决 asyncio.run 嵌套挂问题 (event loop already running).
        dispatcher.run_job 在 asyncio.run 里, 内部 await 是正确姿势.
        """
        import inspect

        result = worker.run(input_data)
        if inspect.iscoroutine(result):
            result = await result
        assert isinstance(result, Verdict), f"worker {worker.__class__.__name__} did not return Verdict"
        return result

    def _invoke_worker(self, worker: Router, input_data: dict[str, Any]) -> Verdict:
        """Sync alias — 仅供 dispatcher 外部 smoke test / 同步路径 fallback 使用."""
        import asyncio
        return asyncio.run(self._invoke_worker_async(worker, input_data))

    # ──────────────────────────────────────────────────────────
    # 诊断辅助
    # ──────────────────────────────────────────────────────────

    def unconsumed_materials(self, events: list[FactoryEvent]) -> list[FactoryEvent]:
        """Q4 辅助: 列出"疑似冗余 material" (无 worker 订阅的非 sink event).

        注: sink kind 的 material 无 consumer 合法, 这里只查 internal / source
        被产出但无任何 worker 订阅的情况.
        """
        subscribed: set[str] = set()
        for w in self._workers:
            s = _format_in_set(w) or set()
            subscribed |= s

        redundant: list[FactoryEvent] = []
        for ev in events:
            if ev.event_type in subscribed:
                continue
            # 简化: 按 payload 中不显式 kind.sink 都算可疑 (真实判定要从 Format registry 查)
            if "kind.sink" in ev.tags or ev.event_type.endswith(".entry") or ev.event_type in {"stdout", "client_output"}:
                continue
            redundant.append(ev)
        return redundant

    def orphan_workers(self, events: list[FactoryEvent]) -> list[Router]:
        """Q4 辅助: 列出"孤儿 worker" (订阅的 material 无 producer, 且非 source kind).

        如果一个 worker 订阅 X, 但 X 从未在 events 里出现, 且 X 不在 source material 清单 → 孤儿.
        """
        produced_types: set[str] = {ev.event_type for ev in events}
        orphans: list[Router] = []
        for w in self._workers:
            fmt_in = _format_in_set(w)
            if not fmt_in:
                continue
            if not (fmt_in & produced_types):
                orphans.append(w)
        return orphans
