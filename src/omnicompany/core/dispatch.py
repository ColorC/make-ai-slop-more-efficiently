# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.core.dispatch.pipeline_scheduler.engine.py"
"""omnicompany.core.dispatch — 统一执行调度器（基础设施）

提供三种执行模式:
1. dispatch()      — 按名称执行完整管线
2. exec_nodes()    — 点名节点自由串接 / 单点执行
3. replay_trace()  — 从历史 trace 重放执行

所有模式共享相同的 bootstrap 流程:
    load_dotenv() → resolve_db → SQLiteBus → TeamRunner → EventBus

CLI 和外部程序均可直接 import 调用。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _call_build_bindings(entry, input_dict: dict) -> dict:
    """兼容两种 build_bindings 签名：无参 () 和带参 (input_dict)。

    也处理 _lazy wrapper 的 (*args, **kwargs) 签名——wrapper 本身接受任意参数,
    但被包装的真实函数可能是 0 参数的,此时透传 input_dict 会 TypeError。
    """
    try:
        result = entry.build_bindings(input_dict)
        return result if isinstance(result, dict) else {}
    except TypeError:
        # 0-arg function (directly or via _lazy wrapper)
        return entry.build_bindings()


def _load_format_registry_for_domain(domain: str):
    """尝试加载 domain 的 FormatRegistry（供 TeamRunner 的 composite fan-in 使用）。

    依次尝试：
      - omnicompany.packages.services.<domain>.formats
      - omnicompany.packages.domains.<domain>.formats
      - omnicompany.packages.<domain>.formats
      - omnicompany.runtime.<domain>.formats
    任一 module 存在 register_formats(registry) 则调用。
    全部失败返回 None（TeamRunner 以 None 运行，composite fan-in 退化为 _from_<src_id>）。
    """
    try:
        from omnicompany.protocol.format import create_builtin_registry
        registry = create_builtin_registry()

        import importlib
        candidates = [
            f"omnicompany.packages.services.{domain}.formats",
            f"omnicompany.packages.domains.{domain}.formats",
            f"omnicompany.packages.{domain}.formats",
            f"omnicompany.runtime.{domain}.formats",
        ]
        loaded = False
        for path in candidates:
            try:
                mod = importlib.import_module(path)
            except ImportError:
                continue
            register_fn = getattr(mod, "register_formats", None)
            if register_fn:
                register_fn(registry)
                loaded = True
        return registry if loaded else None
    except Exception:
        return None


# ── 事件型引擎执行 (E1): 按名字跑 MaterialDispatcher 形态的 team ──────────────

async def _run_event_pipeline(
    entry, input_dict: dict[str, Any], *, max_iterations: int,
) -> dict[str, Any]:
    """运行 engine="event" 的管线: build_team()->worker 清单, 用 MaterialDispatcher 跑。

    入口 material: entry.entry_material; 未给则从 worker 清单推导(被消费但无人产出的那块 = source)。
    返回: {"sinks": [sink material payload...], "sink_types": [...], "entry_material": str, "events": [...]}。
    sink = 被某 worker 以 FORMAT_OUT 产出、却无任何 worker 以 FORMAT_IN 消费的 material。

    注(E1 范围): 当前用 MaterialDispatcher 默认的内存总线跑(与已验证的 WS0 原型一致);
    把事件流持久化到 SQLiteBus 是后续硬化(E2)的事, 不在 E1。
    """
    from omnicompany.packages.services._core.omnicompany.material_dispatcher import (
        MaterialDispatcher,
        _format_in_set,
    )

    workers = entry.build_team()
    if not isinstance(workers, (list, tuple)):
        raise TypeError(
            f"event-engine pipeline '{entry.name}': build_team() 必须返回 worker 清单 "
            f"(list[Router]), 实际得到 {type(workers).__name__}"
        )
    workers = list(workers)

    produced: set[str] = set()
    consumed: set[str] = set()
    for w in workers:
        fo = getattr(w, "FORMAT_OUT", None)
        if fo:
            produced.add(fo)
        consumed |= (_format_in_set(w) or set())

    entry_material = entry.entry_material
    if entry_material is None:
        sources = sorted(consumed - produced)
        if len(sources) != 1:
            raise ValueError(
                f"event pipeline '{entry.name}': 无法唯一推导入口 material "
                f"(候选 source={sources or '[]'}); 请在 PipelineEntry 显式设 entry_material。"
            )
        entry_material = sources[0]

    sink_types = sorted(produced - consumed)

    logger.info(
        "dispatch[event]: pipeline=%s entry=%s sinks=%s workers=%d",
        entry.name, entry_material, sink_types, len(workers),
    )

    dispatcher = MaterialDispatcher(workers, max_iterations=max_iterations)
    events = await dispatcher.run_job(entry_material, input_dict)

    sinks = [ev.payload for ev in events if ev.event_type in set(sink_types)]
    return {
        "sinks": sinks,
        "sink_types": sink_types,
        "entry_material": entry_material,
        "events": events,
    }


# ── 1. 完整管线执行 ──────────────────────────────────────────────────────────

async def dispatch(
    pipeline_name: str,
    input_dict: dict[str, Any],
    *,
    db_path: str | None = None,
    max_steps: int | None = None,
) -> Any:
    """按注册名称执行完整管线。

    Args:
        pipeline_name: 注册表中的管线名称（如 "agent", "lap-audit"）
        input_dict:    管线初始输入
        db_path:       可选的 events.db 路径覆盖（默认走 config.resolve_db_path）
        max_steps:     可选的最大决策步数覆盖

    Returns:
        管线执行结果
    """
    load_dotenv()

    from omnicompany.core.registry import get_or_raise
    from omnicompany.core.config import resolve_db_path
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.runtime.exec.runner import TeamRunner

    entry = get_or_raise(pipeline_name)

    # ── 运行级上下文钩子 (阶段一 1-5): 如 voxelcraft 的 eternal-war worktree 隔离 ──
    from contextlib import ExitStack

    with ExitStack() as _run_stack:
        _ctx_factory = getattr(entry, "run_context", None)
        if _ctx_factory is not None:
            _run_stack.enter_context(_ctx_factory(input_dict))

        # ── E1: 事件型引擎分支 (MaterialDispatcher), 默认 teamrunner 路径不变 ──
        if getattr(entry, "engine", "teamrunner") == "event":
            return await _run_event_pipeline(
                entry, input_dict, max_iterations=(max_steps or entry.default_max_steps),
            )

        return await _dispatch_teamrunner(
            entry, input_dict, db_path=db_path, max_steps=max_steps,
        )


async def _dispatch_teamrunner(
    entry, input_dict: dict[str, Any], *, db_path: str | None, max_steps: int | None,
) -> Any:
    """teamrunner 引擎主体 (自 dispatch() 原地提取, 行为不变; 1-5 为 run_context 包裹重构)."""
    from omnicompany.core.config import resolve_db_path
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.runtime.exec.runner import TeamRunner

    pipeline = entry.build_team()
    bindings = _call_build_bindings(entry, input_dict)
    resolved_db = Path(db_path) if db_path else resolve_db_path(entry.domain)
    resolved_db.parent.mkdir(parents=True, exist_ok=True)
    steps = max_steps or entry.default_max_steps

    # ── 信息充分性体检 (probe baseline, 仅告知不阻塞, 缓存 7 天) ──
    try:
        from omnicompany.runtime.info_audit.pipeline_health import maybe_probe_baseline
        maybe_probe_baseline(pipeline, domain=entry.domain)
    except Exception:
        pass  # 永不阻塞主流程

    logger.info(
        "dispatch: pipeline=%s domain=%s db=%s max_steps=%d",
        getattr(entry, "name", entry.domain), entry.domain, resolved_db, steps,
    )

    # 尝试为 domain 加载 FormatRegistry（composite fan-in 需要）
    format_registry = _load_format_registry_for_domain(entry.domain)
    if format_registry is None:
        logger.debug("dispatch: no format registry loaded for domain=%s (composite fan-in degraded)", entry.domain)

    async with SQLiteBus(resolved_db) as bus:
        runner = TeamRunner(
            pipeline, bindings, bus,
            max_steps=steps,
            source=entry.domain,
            format_registry=format_registry,
        )
        result = await runner.run(input_dict)

    return result


# ── 2. 自由执行（点名节点 / 单点）────────────────────────────────────────────

async def exec_nodes(
    pipeline_name: str,
    node_ids: list[str],
    input_dict: dict[str, Any],
    *,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """点名节点自由串接执行。

    按 node_ids 的顺序依次执行，每个节点的输出作为下一个节点的输入。
    不走 TeamRunner 的路由表，而是直接调用 Router.run()。

    Args:
        pipeline_name: 注册表中的管线名称
        node_ids:      要执行的节点 ID 列表（按顺序）
        input_dict:    第一个节点的初始输入

    Returns:
        每个节点的 [{"node_id": ..., "verdict": ..., "output": ...}] 列表
    """
    load_dotenv()

    import inspect
    from omnicompany.core.registry import get_or_raise
    from omnicompany.core.config import resolve_db_path
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.protocol.events import FactoryEvent

    entry = get_or_raise(pipeline_name)
    bindings = _call_build_bindings(entry, input_dict)
    resolved_db = Path(db_path) if db_path else resolve_db_path(entry.domain)
    resolved_db.parent.mkdir(parents=True, exist_ok=True)

    from ulid import ULID
    trace_id = str(ULID())

    results: list[dict[str, Any]] = []
    current_input = input_dict

    async with SQLiteBus(resolved_db) as bus:
        for node_id in node_ids:
            router = bindings.get(node_id)
            if router is None:
                raise KeyError(
                    f"Node '{node_id}' not found in bindings for pipeline '{pipeline_name}'. "
                    f"Available: {sorted(bindings.keys())}"
                )

            logger.info("exec_nodes: running %s (trace=%s)", node_id, trace_id[:16])

            if inspect.iscoroutinefunction(router.run):
                verdict = await router.run(current_input)
            else:
                verdict = await asyncio.to_thread(router.run, current_input)
                if inspect.isawaitable(verdict):
                    verdict = await verdict

            await bus.publish(FactoryEvent(
                trace_id=trace_id,
                event_type="exec.node",
                source=f"exec/{pipeline_name}",
                payload={
                    "node": node_id,
                    "verdict": verdict.kind.value,
                    "diagnosis": verdict.diagnosis or "",
                },
            ))

            results.append({
                "node_id": node_id,
                "verdict_kind": verdict.kind.value,
                "diagnosis": verdict.diagnosis,
                "output": verdict.output,
            })

            # 链式传递：上一个节点的输出作为下一个节点的输入
            current_input = verdict.output

    return results


# ── 3. Trace 重放 ────────────────────────────────────────────────────────────

async def replay_trace(
    trace_id: str,
    *,
    from_step: int = 0,
    only_node: str | None = None,
    db_path: str | None = None,
    domain: str = "default",
) -> list[dict[str, Any]]:
    """从历史 trace 重放执行。

    从 events.db 读取指定 trace_id 的事件链，
    提取每步的输入，重新执行 Router。

    Args:
        trace_id:   要重放的 trace ID
        from_step:  从第几步开始重放（默认 0 = 全部）
        only_node:  只重放指定节点（忽略其他步骤）
        db_path:    events.db 路径
        domain:     领域标识

    Returns:
        重放结果列表
    """
    load_dotenv()

    from omnicompany.core.config import resolve_db_path
    from omnicompany.bus.sqlite import SQLiteBus

    resolved_db = Path(db_path) if db_path else resolve_db_path(domain)

    async with SQLiteBus(resolved_db) as bus:
        events = await bus.read_trace(trace_id)

    if not events:
        logger.warning("replay: no events found for trace %s", trace_id)
        return []

    # 筛选需要重放的步骤
    replay_steps = []
    for ev in events:
        payload = ev.payload
        step = payload.get("step")
        node = payload.get("node", "")
        if step is None:
            continue
        if step < from_step:
            continue
        if only_node and node != only_node:
            continue
        replay_steps.append(ev)

    logger.info(
        "replay: trace=%s total_events=%d replay_steps=%d",
        trace_id[:16], len(events), len(replay_steps),
    )

    # 返回重放计划（实际重执行需要 Router 绑定，留给调用者决定）
    return [
        {
            "step": ev.payload.get("step"),
            "node_id": ev.payload.get("node", ""),
            "event_type": ev.event_type,
            "verdict": ev.payload.get("verdict", ""),
            "diagnosis": ev.payload.get("diagnosis", ""),
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
        }
        for ev in replay_steps
    ]


# ── CLI 入口辅助 ─────────────────────────────────────────────────────────────

def dispatch_cli(pipeline_name: str) -> None:
    """为旧入口文件提供极简 shim。

    Usage:
        if __name__ == "__main__":
            from omnicompany.core.dispatch import dispatch_cli
            dispatch_cli("agent")
    """
    import sys

    # 简单参数：第一个位置参数作为任务描述
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    if not task:
        print(f"Usage: python -m omnicompany.{pipeline_name} <task>")
        sys.exit(1)

    try:
        result = asyncio.run(dispatch(pipeline_name, {"task": task}))
        print(result)
    except KeyboardInterrupt:
        print("\n中断")
    except Exception as e:
        print(f"错误: {e}")
        raise
