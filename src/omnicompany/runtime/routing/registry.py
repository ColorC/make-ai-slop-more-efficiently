# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.routing.registry.introspection_engine.py"
"""RouterRegistry — Router 注册观察基建

提供三层能力：
1. Router 子类自动发现 — 扫描所有已加载模块中的 Router 子类
2. DAG 内省 — 给定 GraphSpec + bindings，输出完整节点-Router 映射表
3. 全局视图 — 一次性输出三层 DAG (Runtime/Evolution/Meta-Evolution) 的注册状态

使用场景：
  - 人类观察系统当前所有 Router 的注册和绑定情况
  - AI 自省（IsomorphicScheduler 内省进化空间）
  - 调试：快速定位缺失绑定或类型不匹配
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any

from omnicompany.protocol.team import GraphSpec
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


@dataclass
class RouterInfo:
    """单个 Router 类的元信息"""

    class_name: str
    module: str
    input_keys: list[str] | None
    output_keys: list[str] | None
    is_async: bool
    docstring: str


@dataclass
class NodeBinding:
    """DAG 中单个节点的绑定详情"""

    node_id: str
    kind: str  # anchor / transformer / scatter
    validator_kind: str  # hard / soft / none
    format_in: str | list[str]
    format_out: str
    description: str
    routes: dict[str, str]  # VerdictKind -> "action:target"
    router_class: str  # bound Router 类名
    router_module: str
    input_keys: list[str] | None
    output_keys: list[str] | None
    is_async: bool
    is_decision: bool  # 是否消耗 decision_count


@dataclass
class DAGSnapshot:
    """某一层 DAG 的完整快照"""

    dag_id: str
    dag_name: str
    layer: str  # runtime / evolution / meta-evolution
    entry: str
    node_count: int
    edge_count: int
    decision_nodes: list[str]
    nodes: list[NodeBinding]
    edges: list[dict[str, str]]
    unbound_nodes: list[str]


@dataclass
class SystemSnapshot:
    """Runtime DAG 全局快照"""

    runtime: DAGSnapshot
    all_router_classes: list[RouterInfo]
    total_nodes: int
    total_routers: int


class RouterRegistry:
    """Router 注册与内省中心"""

    @staticmethod
    def discover_router_classes(
        root_package: str = "omnicompany",
    ) -> list[RouterInfo]:
        """扫描 package 下所有 Router 子类"""
        router_classes: dict[str, RouterInfo] = {}

        _ensure_modules_loaded(root_package)

        for cls in _all_subclasses(Router):
            if cls is Router or inspect.isabstract(cls):
                continue
            key = f"{cls.__module__}.{cls.__name__}"
            if key in router_classes:
                continue

            is_async = inspect.iscoroutinefunction(cls.run)
            doc = (cls.__doc__ or "").strip().split("\n")[0]

            router_classes[key] = RouterInfo(
                class_name=cls.__name__,
                module=cls.__module__,
                input_keys=cls.INPUT_KEYS if hasattr(cls, "INPUT_KEYS") else None,
                output_keys=cls.OUTPUT_KEYS if hasattr(cls, "OUTPUT_KEYS") else None,
                is_async=is_async,
                docstring=doc[:120],
            )

        return sorted(router_classes.values(), key=lambda r: (r.module, r.class_name))

    @staticmethod
    def inspect_dag(
        graph: GraphSpec,
        bindings: dict[str, Router],
        layer: str = "unknown",
    ) -> DAGSnapshot:
        """内省一个 DAG — 输出节点-Router 映射表"""
        nodes_info: list[NodeBinding] = []
        unbound: list[str] = []
        decision_nodes: list[str] = []

        for node in graph.nodes:
            router = bindings.get(node.id)

            # 节点基本信息
            kind = node.kind.value
            validator_kind = "none"
            fmt_in = ""
            fmt_out = ""
            description = ""
            routes: dict[str, str] = {}

            if node.anchor:
                validator_kind = node.anchor.validator.kind.value
                fmt_in = node.anchor.format_in
                fmt_out = node.anchor.format_out
                description = node.anchor.validator.description or node.anchor.name
                for vk, route in node.anchor.routes.items():
                    target_str = f"{route.action.value}"
                    if route.target:
                        target_str += f":{route.target}"
                    routes[vk.value] = target_str
            elif node.transformer:
                fmt_in = node.transformer.from_format
                fmt_out = node.transformer.to_format
                description = node.transformer.description or node.transformer.name

            is_decision = validator_kind == "soft"
            if is_decision:
                decision_nodes.append(node.id)

            if router is None:
                unbound.append(node.id)
                nodes_info.append(NodeBinding(
                    node_id=node.id, kind=kind, validator_kind=validator_kind,
                    format_in=fmt_in, format_out=fmt_out, description=description,
                    routes=routes, router_class="UNBOUND", router_module="",
                    input_keys=None, output_keys=None,
                    is_async=False, is_decision=is_decision,
                ))
                continue

            router_cls = type(router)
            is_async = inspect.iscoroutinefunction(router_cls.run)
            input_keys = getattr(router, "INPUT_KEYS", None)
            output_keys = getattr(router, "OUTPUT_KEYS", None)

            nodes_info.append(NodeBinding(
                node_id=node.id,
                kind=kind,
                validator_kind=validator_kind,
                format_in=fmt_in,
                format_out=fmt_out,
                description=description,
                routes=routes,
                router_class=router_cls.__name__,
                router_module=router_cls.__module__,
                input_keys=input_keys,
                output_keys=output_keys,
                is_async=is_async,
                is_decision=is_decision,
            ))

        edges_info = []
        for edge in graph.edges:
            edges_info.append({
                "source": edge.source,
                "target": edge.target,
                "condition": edge.condition.value if edge.condition else "always",
                "label": edge.label or "",
            })

        return DAGSnapshot(
            dag_id=graph.id,
            dag_name=graph.name or graph.id,
            layer=layer,
            entry=graph.entry,
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            decision_nodes=decision_nodes,
            nodes=nodes_info,
            edges=edges_info,
            unbound_nodes=unbound,
        )

    @classmethod
    def full_system_snapshot(
        cls,
        *,
        mutation_state: Any = None,
        mirror: Any = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> SystemSnapshot:
        """一次性生成三层 DAG 全局快照"""
        from omnicompany.runtime.exec.graph_builder import (
            build_runtime_graph,
            build_runtime_bindings,
        )

        # Runtime
        rt_graph = build_runtime_graph()
        rt_bindings = build_runtime_bindings(
            model=model, base_url=base_url, api_key=api_key,
            mutation_state=mutation_state, mirror=mirror,
        )
        rt_snap = cls.inspect_dag(rt_graph, rt_bindings, layer="runtime")

        all_classes = cls.discover_router_classes()

        bound_routers = {nb.router_class for nb in rt_snap.nodes if nb.router_class != "UNBOUND"}

        return SystemSnapshot(
            runtime=rt_snap,
            all_router_classes=all_classes,
            total_nodes=rt_snap.node_count,
            total_routers=len(bound_routers),
        )


def format_dag_table(snap: DAGSnapshot, *, verbose: bool = False) -> str:
    """将 DAGSnapshot 格式化为人类可读表格"""
    lines: list[str] = []
    lines.append(f"{'='*80}")
    lines.append(f"  {snap.layer.upper()} DAG: {snap.dag_name}")
    lines.append(f"  ID: {snap.dag_id}")
    lines.append(f"  Entry: {snap.entry}")
    lines.append(f"  Nodes: {snap.node_count}  Edges: {snap.edge_count}  "
                 f"Decision nodes: {len(snap.decision_nodes)}")
    if snap.unbound_nodes:
        lines.append(f"  ⚠ UNBOUND: {snap.unbound_nodes}")
    lines.append(f"{'='*80}")

    # 表头
    if verbose:
        hdr = f"  {'Node ID':<24} {'Kind':<6} {'V':<5} {'Router Class':<30} {'INPUT_KEYS':<35} {'Async'}"
    else:
        hdr = f"  {'Node ID':<24} {'V':<5} {'Router Class':<30} {'ℱ_in → ℱ_out':<35} {'Dec'}"
    lines.append(hdr)
    lines.append(f"  {'-'*len(hdr.strip())}")

    for nb in snap.nodes:
        dec_mark = " ★" if nb.is_decision else ""
        if verbose:
            keys_str = str(nb.input_keys) if nb.input_keys else "[]"
            lines.append(
                f"  {nb.node_id:<24} {nb.kind:<6} {nb.validator_kind:<5} "
                f"{nb.router_class:<30} {keys_str:<35} {'✓' if nb.is_async else '·'}"
            )
        else:
            fi = " + ".join(nb.format_in) if isinstance(nb.format_in, list) else nb.format_in
            fmt_str = f"{fi} → {nb.format_out}" if fi else "—"
            lines.append(
                f"  {nb.node_id:<24} {nb.validator_kind:<5} "
                f"{nb.router_class:<30} {fmt_str:<35}{dec_mark}"
            )

    # 路由表
    lines.append("")
    lines.append("  Routes:")
    for nb in snap.nodes:
        if nb.routes:
            route_strs = [f"{k}→{v}" for k, v in nb.routes.items()]
            lines.append(f"    {nb.node_id:<22} {' | '.join(route_strs)}")

    # 边
    if verbose:
        lines.append("")
        lines.append("  Edges:")
        for e in snap.edges:
            cond = f" [{e['condition']}]" if e["condition"] != "always" else ""
            lines.append(f"    {e['source']} → {e['target']}{cond}  {e['label']}")

    return "\n".join(lines)


def format_system_snapshot(snapshot: SystemSnapshot, *, verbose: bool = False) -> str:
    """格式化三层 DAG 全局视图"""
    parts: list[str] = []

    parts.append(f"╔{'═'*78}╗")
    parts.append("║  OMNICOMPANY SYSTEM DAG REGISTRY")
    parts.append(f"║  Total: {snapshot.total_nodes} nodes, "
                 f"{snapshot.total_routers} unique Router classes bound")
    parts.append(f"║  All discovered Router subclasses: {len(snapshot.all_router_classes)}")
    parts.append(f"╚{'═'*78}╝")
    parts.append("")

    parts.append(format_dag_table(snapshot.runtime, verbose=verbose))
    parts.append("")

    # 所有 Router 类列表
    parts.append(f"{'='*80}")
    parts.append(f"  ALL DISCOVERED ROUTER CLASSES ({len(snapshot.all_router_classes)})")
    parts.append(f"{'='*80}")
    parts.append(f"  {'Class':<35} {'Module':<45} {'Async'}")
    parts.append(f"  {'-'*85}")
    for ri in snapshot.all_router_classes:
        parts.append(
            f"  {ri.class_name:<35} {ri.module:<45} {'✓' if ri.is_async else '·'}"
        )

    # 绑定 vs 未绑定统计
    bound_set = {nb.router_class for nb in snapshot.runtime.nodes if nb.router_class != "UNBOUND"}

    all_names = {ri.class_name for ri in snapshot.all_router_classes}
    unbound_classes = all_names - bound_set

    if unbound_classes:
        parts.append("")
        parts.append(f"  Not bound to any DAG node ({len(unbound_classes)}):")
        for name in sorted(unbound_classes):
            parts.append(f"    · {name}")

    return "\n".join(parts)


def _all_subclasses(cls: type) -> set[type]:
    """递归获取所有子类"""
    result = set()
    work = list(cls.__subclasses__())
    while work:
        c = work.pop()
        if c not in result:
            result.add(c)
            work.extend(c.__subclasses__())
    return result


def _ensure_modules_loaded(root_package: str) -> None:
    """确保 Router 子类所在的模块都已加载（含领域管线）"""
    target_modules = [
        # 核心运行时
        f"{root_package}.runtime.router",
        f"{root_package}.runtime.graph_builder",
        # 运行时节点（拆分后的子模块 + 兼容层）
        f"{root_package}.runtime.nodes.semantic",
        f"{root_package}.runtime.nodes.safety",
        f"{root_package}.runtime.nodes.pain",
        f"{root_package}.runtime.nodes.guardian",
        f"{root_package}.runtime.nodes.context",
        f"{root_package}.runtime.nodes.routing",
        f"{root_package}.runtime.nodes.tools",
        # 领域管线 Router (post-2026-04-08: under packages/domains/<domain>/)
        f"{root_package}.packages.domains.software_engineering.lang_rewrite.routers",
        f"{root_package}.packages.skill_importer.routers",
        f"{root_package}.packages.mcp_builder.routers",
    ]
    for mod_name in target_modules:
        try:
            importlib.import_module(mod_name)
        except ImportError:
            logger.debug("Could not import %s", mod_name)
