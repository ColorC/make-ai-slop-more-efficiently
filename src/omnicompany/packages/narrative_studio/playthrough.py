"""演练求值器(纯结构,无 AI):沿节点图走一遍并维护 state。

从起点节点出发,沿 connections 走。每一步:
  1. 若当前 node 有对应 scene(scene.node_ref == node.id),把 scene.effects 应用到 state;
  2. 找从当前 node 出发、condition 通过的 connections(可走边);
  3. 多条通过时用 choices(按出现顺序消费的 connection.id 或 target)选一条,否则取第一条;
  4. 应用所选 connection 的 effects,移动到 target;
  5. 每步后检查 reveal_layers 与 endings 的 trigger 是否满足。
到 ending 类型节点 / 无可走边 / 触发某 ending 的 trigger 时停止。

求值复用 expr.build_initial_state / eval_condition / apply_effects。
所有返回值均为 JSON 可序列化的基本类型(Enum 取 .value)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import expr, graph
from .models import Connection, Node, NodeType, Project, Scene


# --------------------------------------------------------------------------- #
# 内部索引辅助
# --------------------------------------------------------------------------- #
def _node_index(project: Project) -> Dict[str, Node]:
    """node id -> Node。"""
    return {n.id: n for n in (project.nodes or [])}


def _scene_by_node(project: Project) -> Dict[str, Scene]:
    """node id -> 绑定该节点的第一个 Scene(按 node_ref)。"""
    out: Dict[str, Scene] = {}
    for s in project.scenes or []:
        if s.node_ref and s.node_ref not in out:
            out[s.node_ref] = s
    return out


def _outgoing(project: Project, node_id: str) -> List[Connection]:
    """从 node_id 出发的全部 connections(保持声明顺序)。"""
    return [c for c in (project.connections or []) if c.source == node_id]


def _pick_start(project: Project) -> Optional[str]:
    """默认起点:与 health 共用 graph.entry_node_id 的唯一入口判定。"""
    return graph.entry_node_id(project)


def _edge_summary(c: Connection) -> Dict[str, Any]:
    """一条边的对外摘要(JSON 安全)。"""
    return {"edge_id": c.id, "target": c.target, "label": c.label}


# --------------------------------------------------------------------------- #
# 公共:某节点当前可走边
# --------------------------------------------------------------------------- #
def available_choices(project: Project, state: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    """从 node_id 出发、condition 通过的 connections 摘要列表。

    返回 [{edge_id, target, label}, ...];输入缺字段/空时返回 []。
    """
    if not node_id:
        return []
    out: List[Dict[str, Any]] = []
    for c in _outgoing(project, node_id):
        if expr.eval_condition(c.condition or [], state):
            out.append(_edge_summary(c))
    return out


# --------------------------------------------------------------------------- #
# 选边逻辑:消费 choices
# --------------------------------------------------------------------------- #
def _choose_edge(
    passable: List[Connection],
    choices: List[Any],
    choice_cursor: int,
) -> "tuple[Optional[Connection], int]":
    """在通过的边中选一条。

    若 choices 还有未消费项,按出现顺序取一个 token,匹配 edge.id 或 edge.target;
    匹配到则消费该 token 并返回对应边;匹配不到也消费(视为一次无效选择)再回退首条。
    无 choices 可消费时取第一条。返回 (选中边, 新 cursor)。
    """
    if not passable:
        return None, choice_cursor
    if choice_cursor < len(choices):
        token = choices[choice_cursor]
        choice_cursor += 1
        for c in passable:
            if c.id == token or c.target == token:
                return c, choice_cursor
        # token 无法匹配任何通过边:回退首条(token 已消费)
        return passable[0], choice_cursor
    return passable[0], choice_cursor


# --------------------------------------------------------------------------- #
# trigger 检查:reveal_layers / endings
# --------------------------------------------------------------------------- #
def _check_reveals(project: Project, state: Dict[str, Any], fired: set) -> List[str]:
    """返回本次新触发的 reveal_layer id 列表(已触发的不重复)。"""
    newly: List[str] = []
    for r in project.reveal_layers or []:
        if r.id in fired:
            continue
        if r.trigger and expr.eval_condition(r.trigger, state):
            fired.add(r.id)
            newly.append(r.id)
    return newly


def _triggered_ending(project: Project, state: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    """若当前 state 满足某 ending 的 trigger,返回 {node_id, name};多个取 priority 最高。

    优先匹配 node_id 对应的 ending;否则取所有 trigger 通过的里 priority 最高者。
    """
    candidates = []
    for en in project.endings or []:
        # 无 trigger 的结局只能靠"走到该 ending 节点"触发,不能靠 trigger 恒真误判
        if not en.trigger:
            continue
        if not expr.eval_condition(en.trigger, state):
            continue
        candidates.append(en)
    if not candidates:
        return None
    # 先看是否有正落在当前节点上的结局
    here = [en for en in candidates if en.node_id == node_id]
    pool = here if here else candidates
    best = max(pool, key=lambda e: e.priority)
    return {"node_id": best.node_id, "name": best.name}


# --------------------------------------------------------------------------- #
# 主求值器
# --------------------------------------------------------------------------- #
def playthrough(
    project: Project,
    choices: Optional[List[Any]] = None,
    start: Optional[str] = None,
    max_steps: int = 500,
) -> Dict[str, Any]:
    """从 start 节点出发沿 connections 走一遍,返回演练轨迹(JSON 安全 dict)。

    返回结构:
      {
        visited: [node_id, ...],
        log: [{node_id, title, applied_effects, chosen_edge}, ...],
        state: {var: val},
        available: [{edge_id, target, label}, ...]  # 停止时当前节点可走边
        ending: {node_id, name} | None,
        reveals_triggered: [reveal_id, ...],
        stopped_reason: str,
      }
    """
    choices = list(choices or [])
    nodes = _node_index(project)
    scene_at = _scene_by_node(project)
    state = expr.build_initial_state(project)

    visited: List[str] = []
    log: List[Dict[str, Any]] = []
    reveals_triggered: List[str] = []
    fired_reveals: set = set()
    ending: Optional[Dict[str, Any]] = None
    stopped_reason = "no_edges"

    # 防死循环:同一节点重复进入次数阈值
    enter_count: Dict[str, int] = {}
    LOOP_THRESHOLD = 50

    cur = start or _pick_start(project)
    choice_cursor = 0
    steps = 0

    if cur is None or cur not in nodes:
        return {
            "visited": [],
            "log": [],
            "state": _jsonable_state(state),
            "available": [],
            "ending": None,
            "reveals_triggered": [],
            "stopped_reason": "no_start",
        }

    while True:
        if steps >= max_steps:
            stopped_reason = "max_steps"
            break

        node = nodes.get(cur)
        if node is None:
            stopped_reason = "missing_node"
            break

        # 死循环保护
        enter_count[cur] = enter_count.get(cur, 0) + 1
        if enter_count[cur] > LOOP_THRESHOLD:
            stopped_reason = "loop"
            break

        visited.append(cur)
        steps += 1

        # 1) 应用当前节点 scene 的 effects
        applied: List[Dict[str, Any]] = []
        scene = scene_at.get(cur)
        if scene is not None and scene.effects:
            expr.apply_effects(scene.effects, state)
            applied = [_expr_json(e) for e in scene.effects]

        # 每步后(应用 scene effects 后)检查 reveals
        for rid in _check_reveals(project, state, fired_reveals):
            reveals_triggered.append(rid)

        # 到 ending 类型节点 -> 停
        if node.type == NodeType.ending:
            log.append({
                "node_id": cur,
                "title": node.title,
                "applied_effects": applied,
                "chosen_edge": None,
            })
            ending = _triggered_ending(project, state, cur) or {
                "node_id": cur,
                "name": _ending_name(project, cur) or (node.title or cur),
            }
            stopped_reason = "ending"
            break

        # 2) 找可走边
        passable = [c for c in _outgoing(project, cur)
                    if expr.eval_condition(c.condition or [], state)]

        if not passable:
            log.append({
                "node_id": cur,
                "title": node.title,
                "applied_effects": applied,
                "chosen_edge": None,
            })
            # 无可走边:看是否恰好满足某 ending 的 trigger(非 ending 节点上的跨路线门控)
            trig = _triggered_ending(project, state, cur)
            if trig is not None:
                ending = trig
                stopped_reason = "ending_trigger"
            else:
                stopped_reason = "no_edges"
            break

        # 3) 选边
        chosen, choice_cursor = _choose_edge(passable, choices, choice_cursor)

        # 4) 应用所选边 effects
        if chosen.effects:
            expr.apply_effects(chosen.effects, state)

        log.append({
            "node_id": cur,
            "title": node.title,
            "applied_effects": applied,
            "chosen_edge": _edge_summary(chosen),
        })

        # 选边后再次检查 reveals(边 effects 可能触发)
        for rid in _check_reveals(project, state, fired_reveals):
            reveals_triggered.append(rid)

        # 是否已满足某 ending trigger(可在到达 ending 节点前就停)
        trig = _triggered_ending(project, state, chosen.target)
        if trig is not None and nodes.get(chosen.target) is not None \
                and nodes[chosen.target].type == NodeType.ending:
            # 让它走到 ending 节点,由循环下一步正常收尾
            pass

        cur = chosen.target

    # 停止时当前节点的可走边摘要
    available = available_choices(project, state, cur) if cur in nodes else []

    return {
        "visited": visited,
        "log": log,
        "state": _jsonable_state(state),
        "available": available,
        "ending": ending,
        "reveals_triggered": reveals_triggered,
        "stopped_reason": stopped_reason,
    }


# --------------------------------------------------------------------------- #
# JSON 安全化辅助
# --------------------------------------------------------------------------- #
def _expr_json(e) -> Dict[str, Any]:
    """单条 Expr -> JSON dict。"""
    if hasattr(e, "model_dump"):
        return e.model_dump(mode="json")
    return {"var": e.var, "op": e.op, "value": e.value}


def _jsonable_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """state 已是基本类型;保留为浅拷贝以免外部改动。"""
    return dict(state)


def _ending_name(project: Project, node_id: str) -> Optional[str]:
    for en in project.endings or []:
        if en.node_id == node_id:
            return en.name
    return None
