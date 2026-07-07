"""叙事工程健康检查:对单一真源 Project 做静态体检,产出问题清单。

设计与 models.py 同级:纯函数、无副作用、输入缺字段/空列表不报错。
返回值全部 JSON 可序列化(基本类型 + Enum.value 字符串),给 HTTP API 直接透传。

每条问题形如:
    {
      "code":     str,                       # 稳定问题码,如 "dangling_connection"
      "severity": "high" | "medium" | "low", # 严重度
      "message":  str,                       # 人读描述
      "location": str,                       # 在哪个载体哪个 id
      "ref":      str,                       # 引用的设计依据/判据
    }

覆盖 12 类检查,见 health_check 文档串。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

from . import expr, graph
from .models import NodeType, Project


# --------------------------------------------------------------------------- #
# 内部小工具
# --------------------------------------------------------------------------- #
def _finding(code: str, severity: str, message: str, location: str, ref: str) -> Dict[str, str]:
    """构造一条标准问题记录(全字段都是 str,天然 JSON 可序列化)。"""
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "location": location,
        "ref": ref,
    }


def _var_keys(project: Project) -> Set[str]:
    """所有"已定义"变量的 key 集合(variables + meta_progress.fields)。"""
    keys: Set[str] = set()
    for v in list(project.variables) + list(project.meta_progress.fields):
        keys.add(f"{v.namespace}.{v.name}")
    return keys


def _node_ids(project: Project) -> Set[str]:
    return {n.id for n in project.nodes}


def _inbound_count(project: Project) -> Dict[str, int]:
    """每个 node id 的入边数(只数 target 落在已知节点上的连接)。"""
    known = _node_ids(project)
    inbound: Dict[str, int] = defaultdict(int)
    for c in project.connections:
        if c.target in known:
            inbound[c.target] += 1
    return inbound


def _tag_occurrence_count(project: Project) -> Dict[str, int]:
    """统计每个 tag id 的出现次数(当前真源里 tag 只挂在 scene.tags 上)。

    包内尚无 projections.tag_occurrences 模块(__init__ 里仅列为规划),
    故此处自己数,口径与未来 projection 一致:scene.tags 引用即一次出现。
    """
    counts: Dict[str, int] = defaultdict(int)
    for s in project.scenes:
        for t in s.tags:
            counts[t] += 1
    return counts


def _scene_by_node(project: Project) -> Dict[str, Any]:
    """node_ref -> Scene,便于由节点反查场景。"""
    out: Dict[str, Any] = {}
    for s in project.scenes:
        if s.node_ref:
            out[s.node_ref] = s
    return out


def _prose_by_id(project: Project) -> Dict[str, Any]:
    return {pl.id: pl for pl in project.prose_lines}


# --------------------------------------------------------------------------- #
# 1 断头连接
# --------------------------------------------------------------------------- #
def _check_dangling_connections(project: Project) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    known = _node_ids(project)
    for c in project.connections:
        if c.source not in known:
            out.append(_finding(
                "dangling_connection", "high",
                f"连接 {c.id} 的 source='{c.source}' 指向不存在的节点",
                f"connection:{c.id}", "节点图完整性",
            ))
        if c.target not in known:
            out.append(_finding(
                "dangling_connection", "high",
                f"连接 {c.id} 的 target='{c.target}' 指向不存在的节点",
                f"connection:{c.id}", "节点图完整性",
            ))
    return out


# --------------------------------------------------------------------------- #
# 2 不可达节点
# --------------------------------------------------------------------------- #
def _check_unreachable_nodes(project: Project) -> List[Dict[str, str]]:
    """无任何入边的节点视为不可达;但放过入口。

    模型无显式入口标记,故采用约定:nodes[0] 为入口候选,
    再加上"全图都没有任何连接"时不报(尚未接线,渐进补全友好)。
    """
    out: List[Dict[str, str]] = []
    nodes = list(project.nodes)
    if len(nodes) <= 1 or not project.connections:
        return out
    inbound = _inbound_count(project)
    entry_id = graph.entry_node_id(project)   # 与 playthrough 共用同一入口判定
    for n in nodes:
        if n.id == entry_id:
            continue
        # 结局型节点由 _check_unreachable_endings 专门处理(含未接线占位的渐进豁免),此处跳过
        if n.type == NodeType.ending:
            continue
        if inbound.get(n.id, 0) == 0:
            out.append(_finding(
                "unreachable_node", "high",
                f"节点 {n.id}({n.title or ''}) 无任何入边,从入口不可达",
                f"node:{n.id}", "节点图可达性",
            ))
    return out


# --------------------------------------------------------------------------- #
# 3 结局优先级冲突
# --------------------------------------------------------------------------- #
def _check_ending_priority_conflict(project: Project) -> List[Dict[str, str]]:
    """两个 ending 的 trigger 变量集合有交叠且 priority 相等 → 可能同时为真且无法区分。"""
    out: List[Dict[str, str]] = []
    endings = list(project.endings)
    for i in range(len(endings)):
        for j in range(i + 1, len(endings)):
            ei, ej = endings[i], endings[j]
            vars_i = {e.var for e in ei.trigger}
            vars_j = {e.var for e in ej.trigger}
            if not (vars_i & vars_j):
                continue
            if ei.priority == ej.priority:
                shared = ", ".join(sorted(vars_i & vars_j))
                out.append(_finding(
                    "ending_priority_conflict", "medium",
                    f"结局 '{ei.name}' 与 '{ej.name}' 触发变量交叠({shared})"
                    f"且 priority 相等({ei.priority}),可能同时为真且无法择一",
                    f"ending:{ei.node_id}|ending:{ej.node_id}", "多结局门控可判定性",
                ))
    return out


# --------------------------------------------------------------------------- #
# 4 不可达/不可满足结局
# --------------------------------------------------------------------------- #
def _check_unreachable_endings(project: Project) -> List[Dict[str, str]]:
    """区分'图里有连接指向它却接不上'(真问题)与'整条路线尚未接线'(渐进补全)。

    - node_id 缺失:若被某连接引用为 target = 真断头(已由 dangling_connection 报,此处不重复);
      否则该结局所在路线尚未导入,渐进补全期跳过。
    - 节点存在但无入边:仅当它本应被接入(被某连接引用)却没接上才报;
      从未被任何连接引用 = 占位/未接线,跳过。
    """
    out: List[Dict[str, str]] = []
    known = _node_ids(project)
    inbound = _inbound_count(project)
    referenced = {c.target for c in project.connections}
    reachable = graph.reachable_node_ids(project)
    for en in project.endings:
        if en.node_id not in known:
            continue  # 缺节点:dangling_connection 已覆盖真断头;未引用者属未接线,跳过
        # 已接线(有入边或被引用)却从入口 BFS 不可达 = 真"走不到的结局"
        wired = inbound.get(en.node_id, 0) > 0 or en.node_id in referenced
        if wired and en.node_id not in reachable:
            out.append(_finding(
                "unreachable_ending", "high",
                f"结局 '{en.name}' 的节点 {en.node_id} 已接线却从入口不可达",
                f"ending:{en.node_id}", "结局可达性",
            ))
        # 未接线的结局(无入边、未被引用)= 该路线尚未导入的占位,渐进补全期跳过
    return out


# --------------------------------------------------------------------------- #
# 5 孤儿变量
# --------------------------------------------------------------------------- #
def _check_orphan_variables(project: Project) -> List[Dict[str, str]]:
    """定义了但既不被任何条件读、也不被任何效果写的变量。

    不漏报,但不刷屏:某命名空间整组(≥3 个)全未使用 = 该角色/路线尚未接线,
    聚合成一条 finding(而非逐字段几十条);其余按字段逐条报。
    """
    out: List[Dict[str, str]] = []
    read = {v for v, _ in expr.all_condition_vars(project)}
    written = {v for v, _ in expr.all_effect_vars(project)}
    used = read | written

    # 按命名空间归集变量与其未用项
    by_ns: Dict[str, List[str]] = defaultdict(list)
    unused_by_ns: Dict[str, List[str]] = defaultdict(list)
    for v in list(project.variables) + list(project.meta_progress.fields):
        key = f"{v.namespace}.{v.name}"
        by_ns[v.namespace].append(key)
        if key not in used:
            unused_by_ns[v.namespace].append(key)

    for ns, unused in unused_by_ns.items():
        total = len(by_ns[ns])
        # 整组全未使用且 ≥3 个 → 聚合一条(典型:某条路线/角色尚未接线)
        if len(unused) == total and total >= 3:
            out.append(_finding(
                "orphan_variable", "low",
                f"命名空间 {ns} 的 {total} 个变量整组从不被读写(该角色/路线尚未接线)",
                f"variable_namespace:{ns}", "状态最小化",
            ))
        else:
            for key in unused:
                out.append(_finding(
                    "orphan_variable", "low",
                    f"变量 {key} 从不被任何条件读取、也不被任何效果写入(死变量)",
                    f"variable:{key}", "状态最小化",
                ))
    return out


# --------------------------------------------------------------------------- #
# 6 未定义变量
# --------------------------------------------------------------------------- #
def _check_undefined_variables(project: Project) -> List[Dict[str, str]]:
    """被条件/效果引用,但不在 variables/meta_progress 里声明的变量。"""
    out: List[Dict[str, str]] = []
    defined = _var_keys(project)
    seen: Set[str] = set()
    for var, src in list(expr.all_condition_vars(project)) + list(expr.all_effect_vars(project)):
        if var in defined:
            continue
        if var in seen:
            continue
        seen.add(var)
        out.append(_finding(
            "undefined_variable", "high",
            f"变量 {var} 被引用(首见于 {src})但未在 variables/meta_progress 声明",
            f"variable:{var}", "变量声明完整性",
        ))
    return out


# --------------------------------------------------------------------------- #
# 7 必填为空
# --------------------------------------------------------------------------- #
def _check_required_empty(project: Project) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    # 立意:proposition 不能空
    if not (project.premise.proposition or "").strip():
        out.append(_finding(
            "required_empty", "high",
            "立意 premise.proposition(因果命题)为空",
            "premise", "立意=真源之首",
        ))

    # 场景:objective_events 不能空(无文风事实是 scene 的骨)
    for s in project.scenes:
        if not s.objective_events:
            out.append(_finding(
                "required_empty", "medium",
                f"场景 {s.id} 的 objective_events(客观事件)为空",
                f"scene:{s.id}", "场景必填:客观事实",
            ))

    # 角色:arc 四元(want/need/wound/lie)全空。
    # 豁免 alter/persona 切面型角色(同一主角的人格切面,不承载独立人物弧;
    # 用语义字段 custom_fields.alter 判,不用脆弱的 id 前缀)。
    for c in project.characters:
        if c.custom_fields.get("alter"):
            continue
        a = c.arc
        if not any([a.want, a.need, a.wound, a.lie]):
            out.append(_finding(
                "required_empty", "low",
                f"角色 {c.id}({c.name}) 的人物弧 arc 四元全空",
                f"character:{c.id}", "人物弧四元",
            ))

    return out


# --------------------------------------------------------------------------- #
# 8 伏笔只埋未收
# --------------------------------------------------------------------------- #
def _check_unpaid_foreshadow(project: Project) -> List[Dict[str, str]]:
    """kind=='foreshadow' 的 tag 只在一处出现 → 埋了没收。"""
    out: List[Dict[str, str]] = []
    counts = _tag_occurrence_count(project)
    for t in project.tags:
        if t.kind != "foreshadow":
            continue
        n = counts.get(t.id, 0)
        if n == 1:
            out.append(_finding(
                "unpaid_foreshadow", "medium",
                f"伏笔标签 '{t.name}'({t.id}) 只出现 1 处,埋了未收(setup 无 payoff)",
                f"tag:{t.id}", "Chekhov 之枪:伏笔须有回收",
            ))
    return out


# --------------------------------------------------------------------------- #
# 9 场景不"转"
# --------------------------------------------------------------------------- #
def _check_scene_no_turn(project: Project) -> List[Dict[str, str]]:
    """Story Grid:每场必须有价值翻转;value_shift 的 from/to 任一为空即未转。"""
    out: List[Dict[str, str]] = []
    for s in project.scenes:
        vs = s.value_shift
        frm = vs.from_
        to = vs.to
        if not (frm or "").strip() or not (to or "").strip():
            out.append(_finding(
                "scene_no_turn", "medium",
                f"场景 {s.id} 的 value_shift 未填全(from='{frm}', to='{to}'),该场不'转'",
                f"scene:{s.id}", "Story Grid:每场须有价值翻转",
            ))
    return out


# --------------------------------------------------------------------------- #
# 10 成文空
# --------------------------------------------------------------------------- #
def _check_empty_prose(project: Project) -> List[Dict[str, str]]:
    """scene.line_refs 指向的 prose_line 文本为空(或引用根本不存在)。"""
    out: List[Dict[str, str]] = []
    prose = _prose_by_id(project)
    for s in project.scenes:
        for lid in s.line_refs:
            pl = prose.get(lid)
            if pl is None:
                out.append(_finding(
                    "empty_prose", "medium",
                    f"场景 {s.id} 的 line_ref='{lid}' 指向不存在的 prose_line",
                    f"scene:{s.id}|prose_line:{lid}", "成文层引用完整性",
                ))
                continue
            if not (pl.text or "").strip():
                out.append(_finding(
                    "empty_prose", "low",
                    f"场景 {s.id} 引用的 prose_line {lid} 尚无成文(text 为空)",
                    f"prose_line:{lid}", "先语义后文风:待成文",
                ))
    return out


# --------------------------------------------------------------------------- #
# 11 揭示层触发不可达
# --------------------------------------------------------------------------- #
def _check_reveal_trigger_unreachable(project: Project) -> List[Dict[str, str]]:
    """reveal_layer.trigger 引用的变量从不被任何效果写 → 永远到不了阈值。"""
    out: List[Dict[str, str]] = []
    written = {v for v, _ in expr.all_effect_vars(project)}
    for r in project.reveal_layers:
        for e in r.trigger:
            if e.var not in written:
                out.append(_finding(
                    "reveal_trigger_unreachable", "high",
                    f"揭示层 {r.id} 的触发变量 {e.var} 从不被任何效果写入,阈值永远到不了",
                    f"reveal_layer:{r.id}", "三层叙事:揭示触发须可达",
                ))
    return out


# --------------------------------------------------------------------------- #
# 12 揭示层无重写
# --------------------------------------------------------------------------- #
def _check_reveal_no_rewrite(project: Project) -> List[Dict[str, str]]:
    """reveal_layer 既无 rewrites 也无 rewrites_controlling_idea → 翻转后没有解读变化。"""
    out: List[Dict[str, str]] = []
    for r in project.reveal_layers:
        # 基线/表面层(surface)是默认解读,本就无"翻转后改写",豁免
        if getattr(r.order, "value", r.order) == "surface":
            continue
        if not (r.rewrites or "").strip() and not (r.rewrites_controlling_idea or "").strip():
            out.append(_finding(
                "reveal_no_rewrite", "medium",
                f"揭示层 {r.id}({r.title or ''}) 既无 rewrites 也无 rewrites_controlling_idea,翻转后无解读变化",
                f"reveal_layer:{r.id}", "三层叙事:揭示须改写解读框架",
            ))
    return out


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def health_check(project: Project) -> List[Dict[str, str]]:
    """对 Project 做全量健康检查,返回 JSON 可序列化的问题清单。

    覆盖 12 类:
      1  断头连接(source/target 指向不存在节点)
      2  不可达节点(非入口且无入边)
      3  结局优先级冲突(trigger 变量交叠且 priority 相等)
      4  不可达/不可满足结局(node_id 缺失或无入边)
      5  孤儿变量(定义了但从不读也不写)
      6  未定义变量(被引用但未声明)
      7  必填为空(立意命题/场景客观事件/人物弧)
      8  伏笔只埋未收(foreshadow tag 仅 1 处出现)
      9  场景不"转"(value_shift from/to 任一为空)
      10 成文空(line_refs 指向的 prose_line.text 为空)
      11 揭示层触发不可达(trigger 变量从不被写)
      12 揭示层无重写(无 rewrites 与 rewrites_controlling_idea)

    任一子检查内部对缺字段/空列表都安全;返回顺序按检查序稳定。
    """
    findings: List[Dict[str, str]] = []
    findings += _check_dangling_connections(project)
    findings += _check_unreachable_nodes(project)
    findings += _check_ending_priority_conflict(project)
    findings += _check_unreachable_endings(project)
    findings += _check_orphan_variables(project)
    findings += _check_undefined_variables(project)
    findings += _check_required_empty(project)
    findings += _check_unpaid_foreshadow(project)
    findings += _check_scene_no_turn(project)
    findings += _check_empty_prose(project)
    findings += _check_reveal_trigger_unreachable(project)
    findings += _check_reveal_no_rewrite(project)
    return findings
