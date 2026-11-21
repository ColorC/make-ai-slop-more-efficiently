# [OMNI] origin=omnicompany domain=services/doctor ts=2026-04-12T00:00:00Z
# [OMNI] material_id="material:diagnosis.doctor.pipeline_topology.check_engine.legacy.py"
# OMNI-024 ALLOW: PipelineTopologyCheckRouter 是拓扑检查引擎的公共接口，与检查逻辑同文件是正确设计
# [OMNI] PARTIALLY-DEPRECATED 2026-04-22 — 2 个 Router 类 (PipelineTopologyCheckRouter/PipelineLineageRouter)
#   已迁到 workers/pipeline/*.py 独立文件. 但拓扑检查引擎本体 (Finding/CheckContext/PipelineCheckSpec/
#   PIPELINE_CHECKS/run_pipeline_checks/load_pipeline_from_file/extract_pipeline_lineage/discover_all_pipelines)
#   是基础设施代码, 继续保留在本文件, 由 doctor/pipeline_topology.py re-export.
"""Pipeline 拓扑诊断（B1/B2）— 检查注册表模式

每条检查是独立的 PipelineCheckSpec，可按 ID 开启/关闭。
主入口：run_pipeline_checks(spec, enabled=None, disabled=None) -> list[Finding]

已注册检查（默认全开）：
  no_entry           — entry 节点存在性（CRITICAL）
  isolated           — 孤立节点（从 entry 不可达）
  dead_end           — 终端悬空（有入边无出边但不是合法终端）
  format_break       — 相邻边 format_out ≠ format_in（fan-in 节点豁免）
  cycle              — 非 feedback 边构成的有向环
  composite_missing  — composite Format fan-in 覆盖缺失（需 format_registry）
  soft_hard_pairing   — LLM 节点无 RULE/ANCHOR 直接下游（P-07）
  granted_tag_chain   — format_in.required_tags 被上游 format_out.tags 覆盖（需 format_registry）
  maturity_consistency— CRYSTALLIZED 节点直接依赖 GROWING/HYPOTHETICAL 上游（短板原则）
  purpose_quality     — pipeline.purpose 字段非空且有实质内容（≥20 字符）
  duplicate_edge      — 同一 source→target 重复边（会导致多次触发）

Finding.level 语义：
  blocking  → 结构性问题，阻止正确执行
  degrading → 质量问题，不阻止执行但降低可靠性
  advisory  → 建议改进，不影响当前执行

向后兼容：
  check_pipeline_topology(spec) -> list[TopologyIssue]  ← 旧接口，保留
"""
from __future__ import annotations

import importlib.util
import inspect
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from omnicompany.protocol.pipeline import PipelineSpec, PipelineNode, PipelineEdge
from omnicompany.runtime.routing.router import Router


# ══════════════════════════════════════════════════════════════════════════════
# Finding — 语义化诊断结果
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    """一条诊断发现。语义化描述，不是数字评分。"""

    check_id: str
    """检查项 ID（no_entry / isolated / format_break / ...）"""

    level: str
    """blocking | degrading | advisory | info"""

    location: str
    """定位字符串，如 'node:script_generator' 或 'edge:a→b'"""

    observation: str
    """人类可读的现象描述"""

    implication: str = ""
    """问题的语义含义 / 影响（可选）"""

    cross_refs: list[str] = field(default_factory=list)
    """关联的其他实体 ID（Router / Format / edge 等）"""

    @property
    def severity(self) -> str:
        """向后兼容属性：level → CRITICAL/HIGH/MEDIUM/INFO。"""
        return {
            "blocking":  "CRITICAL",
            "degrading": "HIGH",
            "advisory":  "MEDIUM",
            "info":      "INFO",
        }.get(self.level, "INFO")

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check_id} @ {self.location}: {self.observation}"


# ══════════════════════════════════════════════════════════════════════════════
# CheckContext — 图结构，一次计算，所有检查共享
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CheckContext:
    """从 PipelineSpec 预计算出的图结构，传给每个检查函数避免重复扫描。"""

    spec: PipelineSpec
    node_map: dict[str, PipelineNode]
    node_ids: list[str]
    out_edges: dict[str, list[str]]      # src → [tgt, ...]
    in_edges: dict[str, list[str]]       # tgt → [src, ...]
    feedback_pairs: set[tuple[str, str]] # (src, tgt) 标记为 feedback
    reachable: set[str]                  # 从 entry 可达的节点集
    fan_in_nodes: set[str]               # in_degree > 1（不计 feedback 边）
    entry: str | None
    format_registry: Any = None          # 可选 FormatRegistry


def _build_context(spec: PipelineSpec, format_registry: Any = None) -> CheckContext:
    node_map = {n.id: n for n in spec.nodes}
    node_ids = list(node_map)

    out_edges: dict[str, list[str]] = {n: [] for n in node_ids}
    in_edges:  dict[str, list[str]] = {n: [] for n in node_ids}
    feedback_pairs: set[tuple[str, str]] = set()

    for e in spec.edges:
        out_edges.setdefault(e.source, [])
        in_edges.setdefault(e.target, [])
        out_edges[e.source].append(e.target)
        in_edges[e.target].append(e.source)
        if e.feedback:
            feedback_pairs.add((e.source, e.target))

    entry = getattr(spec, "entry", None) or (node_ids[0] if node_ids else None)

    reachable: set[str] = set()
    if entry and entry in node_map:
        queue: deque[str] = deque([entry])
        while queue:
            n = queue.popleft()
            if n in reachable:
                continue
            reachable.add(n)
            for nxt in out_edges.get(n, []):
                if nxt not in reachable:
                    queue.append(nxt)

    # fan-in：非 feedback 入边 > 1
    in_degree = {n: 0 for n in node_ids}
    for e in spec.edges:
        if not e.feedback and e.target in in_degree:
            in_degree[e.target] += 1
    fan_in_nodes = {n for n, d in in_degree.items() if d > 1}

    return CheckContext(
        spec=spec,
        node_map=node_map,
        node_ids=node_ids,
        out_edges=out_edges,
        in_edges=in_edges,
        feedback_pairs=feedback_pairs,
        reachable=reachable,
        fan_in_nodes=fan_in_nodes,
        entry=entry,
        format_registry=format_registry,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 检查函数
# ══════════════════════════════════════════════════════════════════════════════

def _check_no_entry(ctx: CheckContext) -> list[Finding]:
    if ctx.entry and ctx.entry in ctx.node_map:
        return []
    return [Finding(
        check_id="no_entry",
        level="blocking",
        location="pipeline",
        observation=(
            f"pipeline.entry='{ctx.entry}' 不在节点列表中，无法确定执行起点。"
            if ctx.entry else
            "pipeline 未定义 entry 字段且节点列表为空。"
        ),
        implication="后续所有检查无法执行，需先修复 entry 定义。",
    )]


def _check_isolated(ctx: CheckContext) -> list[Finding]:
    findings = []
    for n in ctx.node_ids:
        if n not in ctx.reachable:
            findings.append(Finding(
                check_id="isolated",
                level="degrading",
                location=f"node:{n}",
                observation=f"节点 '{n}' 从 entry '{ctx.entry}' 不可达（无入边或入边来源也是孤立节点）。",
                implication="该节点永远不会被执行，占用声明空间但无实际作用。",
            ))
    return findings


def _check_dead_end(ctx: CheckContext) -> list[Finding]:
    findings = []
    terminal_candidates = [n for n in ctx.reachable if not ctx.out_edges.get(n)]
    if len(terminal_candidates) <= 1:
        return []
    for n in terminal_candidates:
        node = ctx.node_map.get(n)
        if node and node.kind.value.upper() != "ANCHOR":
            findings.append(Finding(
                check_id="dead_end",
                level="advisory",
                location=f"node:{n}",
                observation=(
                    f"节点 '{n}' 无出边，但存在多个终端节点。"
                    "如果这是合法的 EMIT 短路点，请声明为 Anchor 节点类型。"
                ),
                implication="可能是遗漏了下游边，或需要改为 Anchor 节点声明 EMIT 意图。",
            ))
    return findings


def _format_in_accepts_output(src_out: Any, tgt_in: Any) -> bool:
    """Return True when a target accepts the source format.

    Anchor nodes can declare multiple input formats as a list. Some generated
    specs also use a compact string such as "fmt.a + fmt.b". Treat those as
    accepted alternatives instead of flagging a format_break on the first edge.
    """
    if src_out == tgt_in:
        return True
    if isinstance(tgt_in, (list, tuple, set)):
        return any(_format_in_accepts_output(src_out, item) for item in tgt_in)
    if isinstance(tgt_in, str):
        normalized = tgt_in
        for sep in (" + ", "+", ",", "|", "\n", "\r", "\t"):
            normalized = normalized.replace(sep, ",")
        return str(src_out) in {item.strip() for item in normalized.split(",") if item.strip()}
    return False


def _check_format_break(ctx: CheckContext) -> list[Finding]:
    findings = []
    for edge in ctx.spec.edges:
        if edge.feedback:
            continue
        if edge.target in ctx.fan_in_nodes:
            continue  # fan-in 节点由 Runner._merge_inputs 合并，不逐边检查
        src = ctx.node_map.get(edge.source)
        tgt = ctx.node_map.get(edge.target)
        if not src or not tgt:
            continue
        try:
            src_out = src.format_out
            tgt_in  = tgt.format_in
        except (ValueError, AttributeError):
            continue
        if src_out in ("any", None) or tgt_in in ("any", None):
            continue
        if not _format_in_accepts_output(src_out, tgt_in):
            findings.append(Finding(
                check_id="format_break",
                level="blocking",
                location=f"edge:{edge.source}→{edge.target}",
                observation=(
                    f"Format 链断裂：'{edge.source}'.format_out='{src_out}' "
                    f"≠ '{edge.target}'.format_in='{tgt_in}'。"
                ),
                implication=(
                    "Runner 将传递错误类型的数据到下游节点，"
                    "导致 KeyError 或语义错误。如需多源输入请使用 fan-in 拓扑。"
                ),
                cross_refs=[f"format:{src_out}", f"format:{tgt_in}"],
            ))
    return findings


def _check_cycle(ctx: CheckContext) -> list[Finding]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in ctx.node_ids}
    parent: dict[str, str | None] = {n: None for n in ctx.node_ids}
    findings = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        for v in ctx.out_edges.get(u, []):
            if (u, v) in ctx.feedback_pairs:
                continue
            if color[v] == GRAY:
                cycle = [v, u]
                cur = u
                while cur != v and parent[cur] is not None:
                    cur = parent[cur]  # type: ignore
                    cycle.append(cur)
                cycle_str = " → ".join(reversed(cycle))
                findings.append(Finding(
                    check_id="cycle",
                    level="blocking",
                    location=f"nodes:{','.join(reversed(cycle))}",
                    observation=f"非 feedback 边构成有向环：{cycle_str} → {cycle[-1]}。",
                    implication="管线将无限循环执行，需将反馈边标记 feedback=True 或拆解环路。",
                    cross_refs=[f"node:{n}" for n in reversed(cycle)],
                ))
            elif color[v] == WHITE:
                parent[v] = u
                dfs(v)
        color[u] = BLACK

    for n in ctx.node_ids:
        if color[n] == WHITE:
            dfs(n)
    return findings


def _check_composite_missing(ctx: CheckContext) -> list[Finding]:
    if ctx.format_registry is None:
        return []
    findings = []
    for node_id, sources in ctx.in_edges.items():
        if len(sources) < 2:
            continue
        node = ctx.node_map.get(node_id)
        if not node:
            continue
        try:
            fmt_in = node.format_in
        except (ValueError, AttributeError):
            continue
        if not ctx.format_registry.is_composite(fmt_in):
            continue
        required = set(ctx.format_registry.get(fmt_in).components)
        upstream_outs: set[str] = set()
        for src_id in sources:
            src_node = ctx.node_map.get(src_id)
            if src_node:
                try:
                    upstream_outs.add(src_node.format_out)
                except (ValueError, AttributeError):
                    pass
        missing = required - upstream_outs
        if missing:
            findings.append(Finding(
                check_id="composite_missing",
                level="degrading",
                location=f"node:{node_id}",
                observation=(
                    f"节点 '{node_id}' 的 format_in='{fmt_in}' 是 composite Format，"
                    f"但 component(s) {sorted(missing)} 没有对应上游生产者（按 format_out 匹配）。"
                ),
                implication="Runner._merge_inputs 将缺少对应 key，下游访问时 KeyError。",
                cross_refs=[f"format:{fmt_in}"] + [f"format:{m}" for m in sorted(missing)],
            ))
    return findings


def _check_soft_hard_pairing(ctx: CheckContext) -> list[Finding]:
    """P-07：LLM 节点的直接下游中应有 RULE 或 ANCHOR 节点作为验证。"""
    findings = []
    for node in ctx.spec.nodes:
        # 识别 LLM 节点：TransformerSpec.method == LLM
        is_llm = False
        try:
            if node.transformer and node.transformer.method.value.upper() == "LLM":
                is_llm = True
        except AttributeError:
            pass
        if not is_llm:
            continue

        # 查找直接下游节点
        downstream_ids = [
            tgt for tgt in ctx.out_edges.get(node.id, [])
            if (node.id, tgt) not in ctx.feedback_pairs
        ]
        if not downstream_ids:
            continue  # 终端节点，不做配对检查

        # 下游中是否有 RULE/ANCHOR
        has_hard = False
        for ds_id in downstream_ids:
            ds_node = ctx.node_map.get(ds_id)
            if not ds_node:
                continue
            try:
                # ANCHOR 节点
                if ds_node.kind.value.upper() == "ANCHOR":
                    has_hard = True
                    break
                # TRANSFORMER 节点中 method=RULE
                if ds_node.transformer and ds_node.transformer.method.value.upper() == "RULE":
                    has_hard = True
                    break
            except AttributeError:
                pass

        if not has_hard:
            findings.append(Finding(
                check_id="soft_hard_pairing",
                level="degrading",
                location=f"node:{node.id}",
                observation=(
                    f"LLM 节点 '{node.id}' 的直接下游（{downstream_ids}）中"
                    "没有 RULE 或 ANCHOR 节点作为验证器。"
                ),
                implication=(
                    "LLM 输出无确定性验证，语义错误将静默传递到下游。"
                    "建议在 LLM 节点后接 RULE 验证节点（SOFT+HARD 配对模式）。"
                ),
                cross_refs=[f"node:{node.id}"] + [f"node:{d}" for d in downstream_ids],
            ))
    return findings


def _check_granted_tag_chain(ctx: CheckContext) -> list[Finding]:
    """检查 format_in.required_tags 能否被上游 format_out.tags 静态覆盖。

    required_tags 表示"数据必须已被上游 Validator 授予这些标签"。
    静态检查策略：向上游 BFS 收集所有 format_out.tags，
    验证每个 required_tag 至少在某个上游 Format 的 tags 中出现。

    注意：这是静态启发式检查，不追踪运行时 Verdict.granted_tags 流动。
    未被覆盖的 required_tag 说明上游 Format 的 tags 声明不完整，
    或管线中缺少应有的 Validator 节点。
    """
    if ctx.format_registry is None:
        return []

    findings = []

    for node_id in ctx.node_ids:
        node = ctx.node_map[node_id]
        try:
            fmt_in_id = node.format_in
        except (ValueError, AttributeError):
            continue
        if not fmt_in_id or fmt_in_id == "any":
            continue

        try:
            fmt_in = ctx.format_registry.get(fmt_in_id)
        except Exception:
            continue
        if not fmt_in or not fmt_in.required_tags:
            continue

        # BFS 向上游收集所有 format_out.tags
        upstream_tags: set[str] = set()
        visited: set[str] = set()
        queue: deque[str] = deque(ctx.in_edges.get(node_id, []))
        while queue:
            src_id = queue.popleft()
            if src_id in visited:
                continue
            visited.add(src_id)
            src_node = ctx.node_map.get(src_id)
            if not src_node:
                continue
            try:
                src_fmt_id = src_node.format_out
                src_fmt = ctx.format_registry.get(src_fmt_id)
                if src_fmt:
                    upstream_tags.update(src_fmt.tags)
            except Exception:
                pass
            for parent_id in ctx.in_edges.get(src_id, []):
                if parent_id not in visited:
                    queue.append(parent_id)

        missing_tags = [t for t in fmt_in.required_tags if t not in upstream_tags]
        if missing_tags:
            findings.append(Finding(
                check_id="granted_tag_chain",
                level="degrading",
                location=f"node:{node_id}",
                observation=(
                    f"节点 '{node_id}' 的 format_in='{fmt_in_id}' "
                    f"要求标签 {missing_tags}，"
                    "但上游所有 format_out 的 tags 字段均未包含这些标签。"
                ),
                implication=(
                    "运行时 Validator 将因缺少已授予标签而 FAIL。"
                    "请确认：① 上游确有 Anchor/Validator 节点会 granted_tags 这些标签；"
                    "② 对应上游 Format 的 tags 字段已声明这些标签；"
                    "③ 管线拓扑上该节点的所有入边路径都经过了该 Validator。"
                ),
                cross_refs=[f"format:{fmt_in_id}"] + [f"tag:{t}" for t in missing_tags],
            ))

    return findings


def _check_maturity_consistency(ctx: CheckContext) -> list[Finding]:
    """短板原则：CRYSTALLIZED 节点不应直接依赖 GROWING/HYPOTHETICAL 节点的输出。

    若 CRYSTALLIZED 节点的直接上游（非 feedback 边）中存在 GROWING 或 HYPOTHETICAL 节点，
    则该 CRYSTALLIZED 声明具有误导性——实际可靠性受上游制约，达不到 CRYSTALLIZED 语义。

    只检查直接上游（一跳），避免全链路传播导致全局噪音。
    """
    findings = []
    _UNSTABLE = {"growing", "hypothetical"}

    for node_id in ctx.node_ids:
        node = ctx.node_map[node_id]
        if node.maturity.value != "crystallized":
            continue

        # 直接上游（非 feedback）
        direct_upstream = [
            src for src in ctx.in_edges.get(node_id, [])
            if (src, node_id) not in ctx.feedback_pairs
        ]

        weak_upstream = []
        for src_id in direct_upstream:
            src = ctx.node_map.get(src_id)
            if src and src.maturity.value in _UNSTABLE:
                weak_upstream.append((src_id, src.maturity.value))

        if weak_upstream:
            weak_str = ", ".join(f"'{s}'({m})" for s, m in weak_upstream)
            findings.append(Finding(
                check_id="maturity_consistency",
                level="degrading",
                location=f"node:{node_id}",
                observation=(
                    f"CRYSTALLIZED 节点 '{node_id}' 的直接上游中有不稳定节点：{weak_str}。"
                    "CRYSTALLIZED 声明具有误导性，实际可靠性被上游节点限制。"
                ),
                implication=(
                    f"建议：① 降低 '{node_id}' 的 maturity 到 MATURE 以反映真实状态；"
                    "② 或先将上游不稳定节点升至 MATURE/CRYSTALLIZED。"
                ),
                cross_refs=[f"node:{s}" for s, _ in weak_upstream],
            ))

    return findings


def _check_purpose_quality(ctx: CheckContext) -> list[Finding]:
    """Pipeline 应有实质性的 purpose 声明（非空、非占位）。

    purpose 是 LLM 和诊断系统理解管线意图的唯一来源，
    空 purpose 意味着诊断工具和自动修复系统无法推断管线设计意图。
    """
    purpose = getattr(ctx.spec, "purpose", "") or ""
    if len(purpose.strip()) >= 20:
        return []
    return [Finding(
        check_id="purpose_quality",
        level="advisory",
        location="pipeline",
        observation=(
            f"pipeline '{ctx.spec.id}' 的 purpose 字段为空或过短（当前: {len(purpose.strip())} 字符，建议 ≥20）。"
        ),
        implication=(
            "purpose 是 LLM 诊断和自动修复系统推断管线意图的唯一来源。"
            "缺少 purpose 会降低 L4 叙事审计和 Phase 3 manifest 的质量。"
        ),
    )]


def _check_duplicate_edge(ctx: CheckContext) -> list[Finding]:
    """检测完全重复的边（source / target / condition / feedback 完全相同，超过一次）。

    注：同一 source→target 方向但不同 condition 值（如 PASS 和 PARTIAL）属于合法多条件路由，
    不视为重复边。
    """
    seen: dict[tuple[str, str, str | None, bool], int] = {}
    for e in ctx.spec.edges:
        cond_key = str(e.condition) if e.condition is not None else None
        key = (e.source, e.target, cond_key, e.feedback)
        seen[key] = seen.get(key, 0) + 1

    findings = []
    for (src, tgt, cond, fb), count in seen.items():
        if count > 1:
            findings.append(Finding(
                check_id="duplicate_edge",
                level="advisory",
                location=f"edge:{src}→{tgt}",
                observation=(
                    f"边 '{src}→{tgt}'（condition={cond}, feedback={fb}）出现了 {count} 次。"
                    "重复边在当前 Runner 实现中会导致该下游节点被多次调用或收到重复输入。"
                ),
                implication="删除重复边，保留一条即可。",
                cross_refs=[f"node:{src}", f"node:{tgt}"],
            ))
    return findings


# ══════════════════════════════════════════════════════════════════════════════
# 检查注册表
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineCheckSpec:
    """一条已注册的 Pipeline 检查。"""
    id: str
    description: str
    default_on: bool
    fn: Callable[[CheckContext], list[Finding]]


#: 全局检查注册表，按声明顺序执行。
#: no_entry 必须最先执行（其余检查依赖 entry 存在）。
PIPELINE_CHECKS: list[PipelineCheckSpec] = [
    PipelineCheckSpec(
        id="no_entry",
        description="pipeline.entry 存在且在节点列表中",
        default_on=True,
        fn=_check_no_entry,
    ),
    PipelineCheckSpec(
        id="isolated",
        description="所有节点从 entry 可达（无孤立节点）",
        default_on=True,
        fn=_check_isolated,
    ),
    PipelineCheckSpec(
        id="dead_end",
        description="无出边的非 Anchor 节点（疑似悬空终端）",
        default_on=True,
        fn=_check_dead_end,
    ),
    PipelineCheckSpec(
        id="format_break",
        description="相邻边 format_out = format_in（非 fan-in 节点）",
        default_on=True,
        fn=_check_format_break,
    ),
    PipelineCheckSpec(
        id="cycle",
        description="非 feedback 边无有向环",
        default_on=True,
        fn=_check_cycle,
    ),
    PipelineCheckSpec(
        id="composite_missing",
        description="composite Format fan-in 的所有 component 有对应上游（需 format_registry）",
        default_on=True,
        fn=_check_composite_missing,
    ),
    PipelineCheckSpec(
        id="soft_hard_pairing",
        description="LLM 节点的直接下游有 RULE/ANCHOR 验证节点（P-07）",
        default_on=True,
        fn=_check_soft_hard_pairing,
    ),
    PipelineCheckSpec(
        id="granted_tag_chain",
        description="format_in.required_tags 被上游 format_out.tags 静态覆盖（需 format_registry）",
        default_on=True,
        fn=_check_granted_tag_chain,
    ),
    PipelineCheckSpec(
        id="maturity_consistency",
        description="CRYSTALLIZED 节点的直接上游不含 GROWING/HYPOTHETICAL 节点（短板原则）",
        default_on=True,
        fn=_check_maturity_consistency,
    ),
    PipelineCheckSpec(
        id="purpose_quality",
        description="pipeline.purpose 字段非空且有实质内容（建议 ≥20 字符）",
        default_on=False,  # advisory-only，默认关闭，按需开启
        fn=_check_purpose_quality,
    ),
    PipelineCheckSpec(
        id="duplicate_edge",
        description="同一 source→target 重复边（会导致多次触发）",
        default_on=True,
        fn=_check_duplicate_edge,
    ),
]

#: 检查 ID → PipelineCheckSpec 快速查找
_CHECK_INDEX: dict[str, PipelineCheckSpec] = {c.id: c for c in PIPELINE_CHECKS}


def run_pipeline_checks(
    spec: PipelineSpec,
    *,
    enabled: list[str] | None = None,
    disabled: list[str] | None = None,
    format_registry: Any = None,
) -> list[Finding]:
    """对 PipelineSpec 执行注册的检查，返回 Finding 列表。

    Args:
        spec:             被检查的 PipelineSpec
        enabled:          若提供，只运行这些 ID 的检查（None = 全部默认开启的检查）
        disabled:         若提供，跳过这些 ID 的检查
        format_registry:  可选 FormatRegistry，用于 composite_missing 检查

    Returns:
        Finding 列表，空列表表示全部通过。
    """
    disabled_set = set(disabled or [])

    if enabled is not None:
        checks_to_run = [c for c in PIPELINE_CHECKS if c.id in enabled]
    else:
        checks_to_run = [c for c in PIPELINE_CHECKS if c.default_on]

    checks_to_run = [c for c in checks_to_run if c.id not in disabled_set]

    ctx = _build_context(spec, format_registry)

    findings: list[Finding] = []
    for check in checks_to_run:
        try:
            result = check.fn(ctx)
            findings.extend(result)
        except Exception as exc:
            # 检查本身崩溃，降级为 advisory Finding
            findings.append(Finding(
                check_id=check.id,
                level="advisory",
                location="check-runner",
                observation=f"检查 '{check.id}' 执行时发生异常: {type(exc).__name__}: {exc}",
            ))
        # no_entry BLOCKING 时，后续检查无意义
        if check.id == "no_entry" and findings and findings[-1].level == "blocking":
            break

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# 向后兼容：TopologyIssue + check_pipeline_topology
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TopologyIssue:
    """向后兼容类型。新代码请使用 Finding。"""

    check: str
    severity: str
    node_ids: list[str] = field(default_factory=list)
    edge: tuple[str, str] | None = None
    observation: str = ""

    def __str__(self) -> str:
        loc = ""
        if self.node_ids:
            loc = f" [{', '.join(self.node_ids)}]"
        if self.edge:
            loc = f" [{self.edge[0]} → {self.edge[1]}]"
        return f"[{self.severity}] {self.check}{loc}: {self.observation}"


def _finding_to_issue(f: Finding) -> TopologyIssue:
    """Finding → TopologyIssue 转换（向后兼容）。"""
    node_ids: list[str] = []
    edge: tuple[str, str] | None = None
    loc = f.location
    if loc.startswith("node:"):
        node_ids = [loc[5:]]
    elif loc.startswith("nodes:"):
        node_ids = loc[6:].split(",")
    elif loc.startswith("edge:"):
        parts = loc[5:].split("→")
        if len(parts) == 2:
            edge = (parts[0], parts[1])
            node_ids = list(parts)
    return TopologyIssue(
        check=f.check_id,
        severity=f.severity,
        node_ids=node_ids,
        edge=edge,
        observation=f.observation,
    )


def check_pipeline_topology(
    spec: PipelineSpec,
    format_registry: Any = None,
) -> list[TopologyIssue]:
    """向后兼容接口。返回 TopologyIssue 列表。

    新代码请使用 run_pipeline_checks() 获取语义化 Finding。
    """
    findings = run_pipeline_checks(spec, format_registry=format_registry)
    return [_finding_to_issue(f) for f in findings]


# ══════════════════════════════════════════════════════════════════════════════
# 报告生成
# ══════════════════════════════════════════════════════════════════════════════

def format_topology_report(
    spec: PipelineSpec,
    issues: list[TopologyIssue] | list[Finding],
) -> str:
    """生成可读报告（兼容 TopologyIssue 和 Finding）。"""
    lines: list[str] = [
        f"Pipeline: {spec.id} ({spec.name})",
        f"  节点数: {len(spec.nodes)}  边数: {len(spec.edges)}",
    ]
    if not issues:
        lines.append("  ✓ 全部检查通过")
        return "\n".join(lines)

    counts: dict[str, int] = {}
    for iss in issues:
        sev = iss.severity if isinstance(iss, TopologyIssue) else iss.severity
        counts[sev] = counts.get(sev, 0) + 1
    dist = "  ".join(f"{s}:{counts[s]}" for s in ["CRITICAL", "HIGH", "MEDIUM", "INFO"] if s in counts)
    lines.append(f"  ✗ 发现 {len(issues)} 个问题：{dist}")
    lines.append("")
    for iss in issues:
        if isinstance(iss, Finding):
            lines.append(f"  [{iss.severity}] {iss.check_id} @ {iss.location}")
            lines.append(f"    {iss.observation}")
            if iss.implication:
                lines.append(f"    → {iss.implication}")
        else:
            lines.append(f"  [{iss.severity}] {iss.check}")
            lines.append(f"    {iss.observation}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PipelineTopologyCheckRouter — Router 形式封装
# ══════════════════════════════════════════════════════════════════════════════

class PipelineTopologyCheckRouter(Router):
    """Pipeline 拓扑静态检查 Router。

    输入 (diag.pipeline.request):
        pipeline_file:        str       — pipeline.py 路径
        pipeline_id:          str|None  — 只检查同名 pipeline
        use_format_registry:  bool      — 是否用 FormatRegistry 做 composite 检查（默认 True）
        enabled_checks:       list[str] — 只运行这些检查 ID（默认全部默认开启的检查）
        disabled_checks:      list[str] — 跳过这些检查 ID

    输出 (diag.pipeline.topology-report):
        pipelines: list[dict]  — 每个管线的检查结果
        total_findings: int
        summary: str
    """
    DESCRIPTION = (
        "对 pipeline.py 文件执行静态拓扑分析（检查注册表模式，可按 ID 开关）。"
        "内置检查（默认开）：no_entry / isolated / dead_end / format_break / cycle / "
        "composite_missing / soft_hard_pairing / granted_tag_chain / "
        "maturity_consistency / duplicate_edge。"
        "可选检查（默认关）：purpose_quality。"
        "输入文件路径，输出结构化 Finding 列表 + 每管线健康状态（PASS/WARN/FAIL）。"
    )
    FORMAT_IN  = "diag.pipeline.request"
    FORMAT_OUT = "diag.pipeline.topology-report"

    def run(self, input_data: dict) -> "Verdict":  # type: ignore[name-defined]
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        pipeline_file    = input_data.get("pipeline_file", "")
        filter_id        = input_data.get("pipeline_id")
        use_fmt_reg      = input_data.get("use_format_registry", True)
        enabled_checks   = input_data.get("enabled_checks")   # list[str] | None
        disabled_checks  = input_data.get("disabled_checks", [])

        if not pipeline_file:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": "pipeline_file 未提供"},
                diagnosis="PipelineTopologyCheck: pipeline_file 为空",
            )

        format_registry = None
        if use_fmt_reg:
            try:
                from omnicompany.core.registry import discover
                from omnicompany.protocol.format import _default_registry  # type: ignore
                discover()
                format_registry = _default_registry
            except Exception:
                pass

        try:
            specs = load_pipeline_from_file(pipeline_file)
        except FileNotFoundError:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": f"文件不存在: {pipeline_file}"},
                diagnosis=f"PipelineTopologyCheck: {pipeline_file} 不存在",
            )
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": str(exc)},
                diagnosis=f"PipelineTopologyCheck: 加载失败 — {exc}",
            )

        if not specs:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": "文件中未找到 PipelineSpec"},
                diagnosis=f"PipelineTopologyCheck: {pipeline_file} 无 build_* 函数",
            )

        if filter_id:
            specs = [s for s in specs if s.id == filter_id]
            if not specs:
                return Verdict(
                    kind=VerdictKind.FAIL, confidence=1.0,
                    output={"error": f"未找到 pipeline_id='{filter_id}'"},
                    diagnosis=f"PipelineTopologyCheck: {filter_id} 不在文件中",
                )

        _LEVEL_ORDER = {"blocking": 0, "degrading": 1, "advisory": 2, "info": 3}
        pipeline_results = []
        total_findings = 0

        for spec in specs:
            findings = run_pipeline_checks(
                spec,
                enabled=enabled_checks,
                disabled=disabled_checks,
                format_registry=format_registry,
            )
            findings.sort(key=lambda x: _LEVEL_ORDER.get(x.level, 9))
            has_blocking  = any(f.level == "blocking"  for f in findings)
            has_degrading = any(f.level == "degrading" for f in findings)
            grade = (
                "FAIL" if has_blocking else
                "WARN" if has_degrading else
                "PASS" if not findings else "INFO"
            )
            total_findings += len(findings)
            pipeline_results.append({
                "pipeline_id":   spec.id,
                "pipeline_name": spec.name,
                "node_count":    len(spec.nodes),
                "edge_count":    len(spec.edges),
                "health_grade":  grade,
                "finding_count": len(findings),
                "findings": [
                    {
                        "check_id":    f.check_id,
                        "level":       f.level,
                        "severity":    f.severity,
                        "location":    f.location,
                        "observation": f.observation,
                        "implication": f.implication,
                        "cross_refs":  f.cross_refs,
                    }
                    for f in findings
                ],
            })

        any_fail = any(r["health_grade"] == "FAIL" for r in pipeline_results)
        any_warn = any(r["health_grade"] == "WARN" for r in pipeline_results)
        summary = (
            f"检查 {len(specs)} 个管线，共 {total_findings} 个 Finding。"
            f"{'有 blocking 问题（FAIL）' if any_fail else '有 degrading 问题（WARN）' if any_warn else '全部健康（PASS）'}"
        )

        return Verdict(
            kind=VerdictKind.FAIL if any_fail else VerdictKind.PASS,
            confidence=1.0,
            output={
                "pipeline_file":  pipeline_file,
                "pipelines":      pipeline_results,
                "total_findings": total_findings,
                "summary":        summary,
                "checks_run":     enabled_checks or [c.id for c in PIPELINE_CHECKS if c.default_on],
                "checks_skipped": disabled_checks,
            },
            diagnosis=f"PipelineTopologyCheck: {summary}",
        )


# ══════════════════════════════════════════════════════════════════════════════
# 从源文件加载 PipelineSpec
# ══════════════════════════════════════════════════════════════════════════════

def load_pipeline_from_file(pipeline_file: str) -> list[PipelineSpec]:
    """从 pipeline.py 文件加载所有 PipelineSpec（调用所有无必填参数的 build_* 函数）。"""
    path = Path(pipeline_file)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {pipeline_file}")

    spec = importlib.util.spec_from_file_location("_pipeline_module", path)
    if not spec or not spec.loader:
        raise ImportError(f"无法加载模块: {pipeline_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore

    results: list[PipelineSpec] = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("build_"):
            continue
        sig = inspect.signature(obj)
        required_params = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        ]
        if required_params:
            continue
        try:
            result = obj()
            if isinstance(result, PipelineSpec):
                results.append(result)
        except Exception:
            pass
    return results


# ══════════════════════════════════════════════════════════════════════════════
# B2 — Pipeline Lineage（跨管线 format 产消图）
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FormatEdge:
    """一条 format 传递边：pipeline 某节点的 format_in → format_out。"""
    pipeline_id: str
    pipeline_name: str
    node_id: str
    node_kind: str
    format_in: str | list[str] | None
    format_out: str | None


@dataclass
class PipelineLineage:
    """一条管线的 lineage 信息。"""
    pipeline_id: str
    pipeline_name: str
    source_file: str
    format_edges: list[FormatEdge] = field(default_factory=list)


def extract_pipeline_lineage(spec: PipelineSpec, source_file: str = "") -> PipelineLineage:
    edges: list[FormatEdge] = []
    for node in spec.nodes:
        try:
            fmt_in: str | list[str] | None = node.format_in
        except (ValueError, AttributeError):
            fmt_in = None
        try:
            fmt_out: str | None = node.format_out
        except (ValueError, AttributeError):
            fmt_out = None
        edges.append(FormatEdge(
            pipeline_id=spec.id,
            pipeline_name=spec.name,
            node_id=node.id,
            node_kind=node.kind.value if hasattr(node.kind, "value") else str(node.kind),
            format_in=fmt_in,
            format_out=fmt_out,
        ))
    return PipelineLineage(
        pipeline_id=spec.id,
        pipeline_name=spec.name,
        source_file=source_file,
        format_edges=edges,
    )


def discover_all_pipelines(source_root: str | Path) -> list[tuple[str, PipelineSpec]]:
    """发现并加载 source_root 下所有 *pipeline*.py 中的 PipelineSpec。"""
    root = Path(source_root)
    results: list[tuple[str, PipelineSpec]] = []
    seen_ids: set[str] = set()

    for pat in ("**/pipeline.py", "**/*_pipeline.py", "**/pipeline_*.py"):
        for path in sorted(root.rglob(pat)):
            if "__pycache__" in str(path) or "_graveyard" in str(path):
                continue
            try:
                specs = load_pipeline_from_file(str(path))
                for spec in specs:
                    if spec.id not in seen_ids:
                        results.append((str(path), spec))
                        seen_ids.add(spec.id)
            except Exception:
                pass
    return results


class PipelineLineageRouter(Router):
    """跨管线 format 产消图提取 Router（B2）。

    扫描 source_root 下所有注册管线，提取每个节点的 format_in / format_out，
    构建跨管线 format 产消图，识别跨管线 Format 交接点。

    输入 (diag.lineage.request):
        source_root:  str       — 源码根目录
        format_id:    str|None  — 只展示涉及此 Format 的条目
        pipeline_id:  str|None  — 只展示指定管线

    输出 (diag.lineage.report):
        pipeline_count, format_count, pipelines, formats, cross_pipeline_handoffs
    """
    DESCRIPTION = (
        "扫描 source_root 下所有注册管线，提取节点-Format 映射边（lineage）。"
        "构建跨管线 format 产消图，识别 A 产出 → B 消费的跨管线交接点。"
        "支持 format_id / pipeline_id 过滤。"
    )
    FORMAT_IN  = "diag.lineage.request"
    FORMAT_OUT = "diag.lineage.report"

    def run(self, input_data: dict) -> "Verdict":  # type: ignore[name-defined]
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        source_root   = input_data.get("source_root", "src/omnicompany")
        filter_format = input_data.get("format_id")
        filter_pip    = input_data.get("pipeline_id")

        try:
            all_specs = discover_all_pipelines(source_root)
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": f"discover_all_pipelines 失败: {exc}"},
                diagnosis=f"PipelineLineage: source_root={source_root} 扫描失败",
            )

        lineages: list[PipelineLineage] = []
        for src_file, spec in all_specs:
            if filter_pip and spec.id != filter_pip:
                continue
            lineages.append(extract_pipeline_lineage(spec, src_file))

        format_producers: dict[str, list[dict]] = {}
        format_consumers: dict[str, list[dict]] = {}

        for lin in lineages:
            for edge in lin.format_edges:
                ref = {"pipeline_id": lin.pipeline_id, "node_id": edge.node_id,
                       "node_kind": edge.node_kind}
                if edge.format_out and edge.format_out != "any":
                    format_producers.setdefault(edge.format_out, []).append(ref)
                if edge.format_in:
                    fins = edge.format_in if isinstance(edge.format_in, list) else [edge.format_in]
                    for fin in fins:
                        if fin and fin != "any":
                            format_consumers.setdefault(fin, []).append(ref)

        all_format_ids = sorted(set(format_producers) | set(format_consumers))
        if filter_format:
            all_format_ids = [f for f in all_format_ids if f == filter_format]

        cross_pipeline: list[dict] = []
        for fmt_id in all_format_ids:
            prod_pips = {p["pipeline_id"] for p in format_producers.get(fmt_id, [])}
            cons_pips = {c["pipeline_id"] for c in format_consumers.get(fmt_id, [])}
            if prod_pips and cons_pips and prod_pips != cons_pips:
                cross_pipeline.append({
                    "format_id":   fmt_id,
                    "produced_by": sorted(prod_pips),
                    "consumed_by": sorted(cons_pips),
                })

        output = {
            "source_root":            source_root,
            "pipeline_count":         len(lineages),
            "format_count":           len(all_format_ids),
            "pipelines": [
                {
                    "pipeline_id":   lin.pipeline_id,
                    "pipeline_name": lin.pipeline_name,
                    "source_file":   lin.source_file,
                    "node_count":    len(lin.format_edges),
                    "format_flow": [
                        {"node_id": e.node_id, "node_kind": e.node_kind,
                         "format_in": e.format_in, "format_out": e.format_out}
                        for e in lin.format_edges
                    ],
                }
                for lin in lineages
            ],
            "formats": {
                fmt_id: {
                    "producers": format_producers.get(fmt_id, []),
                    "consumers": format_consumers.get(fmt_id, []),
                }
                for fmt_id in all_format_ids
            },
            "cross_pipeline_handoffs": cross_pipeline,
        }

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output=output,
            diagnosis=(
                f"PipelineLineage: {len(lineages)} 管线，"
                f"{len(all_format_ids)} 个 Format，"
                f"{len(cross_pipeline)} 个跨管线交接点"
            ),
        )
