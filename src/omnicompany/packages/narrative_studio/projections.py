"""纯投影函数:把单一真源 Project 算成各视图(timeline/outline/route 图/关系图/
钻取/分布/溯源等)需要的数据。

设计约束:
- 全部为纯函数,无副作用(不改 project)。
- 返回值 100% JSON 可序列化(基本类型 / Enum→.value 字符串 / Pydantic→model_dump(mode="json"))。
- 输入缺字段、空列表都不报错(渐进补全友好)。

场景如何归属 beat(本模块统一口径):
- Scene 没有直接 beat 字段。它经 ``node_ref`` 指向 Node,Node 的 ``route``
  视作其所属 beat(顶层路线分组 == beat id)。
- 找不到 node、node 无 route、或 route 不是已知 beat id → 该场景无 beat(进 unplaced)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import expr
from .models import Project


# --------------------------------------------------------------------------- #
# 内部 helper:索引 + 叙述顺序排序
# --------------------------------------------------------------------------- #
def _enum_val(x: Any) -> Any:
    """Enum→其 .value 字符串;其余原样返回。"""
    return x.value if hasattr(x, "value") else x


def _beat_by_id(project: Project) -> Dict[str, Any]:
    return {b.id: b for b in project.beats}


def _node_by_id(project: Project) -> Dict[str, Any]:
    return {n.id: n for n in project.nodes}


def _scene_beat_id(project: Project, scene: Any, nodes: Dict[str, Any],
                   beats: Dict[str, Any]) -> Optional[str]:
    """求一个场景所属的 beat id。

    优先用 Scene.beat 直接关联;为空时回退 node_ref→node.route(route 须等于某 beat id)。
    """
    direct = getattr(scene, "beat", None)
    if direct and direct in beats:
        return direct
    if not scene.node_ref:
        return None
    node = nodes.get(scene.node_ref)
    if node is None or not node.route:
        return None
    route = node.route
    return route if route in beats else None


def _beat_chain_positions(beat_id: Optional[str], beats: Dict[str, Any]) -> Tuple[int, ...]:
    """beat 的父链 position 元组(顶层在前),用于层级排序。

    无 beat 返回一个极大键,排到末尾。
    """
    if beat_id is None or beat_id not in beats:
        return (10**9,)
    chain: List[int] = []
    cur = beats.get(beat_id)
    seen = set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        chain.append(int(cur.position or 0))
        cur = beats.get(cur.parent) if cur.parent else None
    chain.reverse()
    return tuple(chain)


def _narrative_order_key(project: Project):
    """返回一个 scene_id -> 排序键 的函数式映射构造。

    叙述顺序:先按场景所属 beat 的 position(含父链),再按场景在 scenes 中的
    出现序;无 beat 的排末尾。
    """
    nodes = _node_by_id(project)
    beats = _beat_by_id(project)
    order_index = {s.id: i for i, s in enumerate(project.scenes)}

    def key(scene_id: str) -> Tuple[Any, int]:
        scene = _scene_by_id(project).get(scene_id)
        if scene is None:
            return ((10**9,), order_index.get(scene_id, 10**9))
        bid = _scene_beat_id(project, scene, nodes, beats)
        return (_beat_chain_positions(bid, beats), order_index.get(scene_id, 10**9))

    return key


_SCENE_INDEX_CACHE_KEY = "_scene_index"


def _scene_by_id(project: Project) -> Dict[str, Any]:
    return {s.id: s for s in project.scenes}


def _sort_scene_ids(project: Project, scene_ids: List[str]) -> List[str]:
    """按统一叙述顺序对 scene_id 列表排序(去重保稳)。"""
    key = _narrative_order_key(project)
    # stable:同键保持传入相对序
    return sorted(scene_ids, key=key)


# --------------------------------------------------------------------------- #
# 1. timeline:beat × storyline 网格
# --------------------------------------------------------------------------- #
def timeline(project: Project) -> dict:
    """Plottr 风时间线投影。

    返回 {beats, lines, cells: {beat_id: {line_id: [scene_id...]}}, unplaced}。
    场景按其所属 beat(node_ref→route)与 links.lines 归格;
    无 beat 的进 unplaced;无 line 的也仍会进所属 beat 行(line_id="")。
    """
    nodes = _node_by_id(project)
    beats = _beat_by_id(project)

    beats_out = [
        {
            "id": b.id,
            "parent": b.parent,
            "title": b.title,
            "position": int(b.position or 0),
        }
        for b in project.beats
    ]
    lines_out = [
        {"id": sl.id, "title": sl.title, "color": sl.color}
        for sl in project.storylines
    ]

    cells: Dict[str, Dict[str, List[str]]] = {}
    unplaced: List[str] = []

    for s in project.scenes:
        bid = _scene_beat_id(project, s, nodes, beats)
        if bid is None:
            unplaced.append(s.id)
            continue
        line_ids = list(s.links.lines) if s.links and s.links.lines else [""]
        for lid in line_ids:
            cells.setdefault(bid, {}).setdefault(lid, []).append(s.id)

    # 各格内排序
    for bid, lines_map in cells.items():
        for lid in lines_map:
            lines_map[lid] = _sort_scene_ids(project, lines_map[lid])
    unplaced = _sort_scene_ids(project, unplaced)

    return {
        "beats": beats_out,
        "lines": lines_out,
        "cells": cells,
        "unplaced": unplaced,
    }


# --------------------------------------------------------------------------- #
# 2. outline:beat 线性展开,每 beat 附其场景
# --------------------------------------------------------------------------- #
def _beats_linearized(project: Project) -> List[Tuple[Any, int]]:
    """beats 按层级线性展开,返回 (beat, depth) 列表。

    顶层按 position;每个 beat 下挂其子(按 position)递归。
    孤儿(parent 指向不存在)按顶层处理。
    """
    beats = _beat_by_id(project)
    children: Dict[Optional[str], List[Any]] = {}
    for b in project.beats:
        parent = b.parent if (b.parent and b.parent in beats) else None
        children.setdefault(parent, []).append(b)
    for plist in children.values():
        plist.sort(key=lambda b: int(b.position or 0))

    out: List[Tuple[Any, int]] = []

    def walk(parent: Optional[str], depth: int) -> None:
        for b in children.get(parent, []):
            out.append((b, depth))
            walk(b.id, depth + 1)

    walk(None, 0)
    return out


def outline(project: Project) -> list:
    """Plottr outline:beats 按层级线性展开,每个 beat 附其场景(线性投影)。

    每项 {id, parent, depth, title, function, status, summary, scenes:[{scene_id,title,status}]}。
    """
    nodes = _node_by_id(project)
    beats = _beat_by_id(project)

    # beat_id -> [scene...] (按叙述顺序)
    scenes_by_beat: Dict[str, List[Any]] = {}
    for s in project.scenes:
        bid = _scene_beat_id(project, s, nodes, beats)
        if bid is not None:
            scenes_by_beat.setdefault(bid, []).append(s)

    result: List[dict] = []
    for b, depth in _beats_linearized(project):
        scene_list = scenes_by_beat.get(b.id, [])
        ordered_ids = _sort_scene_ids(project, [s.id for s in scene_list])
        sidx = _scene_by_id(project)
        result.append(
            {
                "id": b.id,
                "parent": b.parent,
                "depth": depth,
                "title": b.title,
                "function": b.function,
                "status": _enum_val(b.status),
                "lane": b.lane,
                "edges": list(b.edges or []),
                "authority": b.authority,
                "summary": b.summary.model_dump(mode="json") if b.summary else None,
                "scenes": [
                    {
                        "scene_id": sid,
                        "title": sidx[sid].title,
                        "status": _enum_val(sidx[sid].status),
                    }
                    for sid in ordered_ids
                ],
            }
        )
    return result


# --------------------------------------------------------------------------- #
# 3. route_graph:供 reactflow
# --------------------------------------------------------------------------- #
def route_graph(project: Project) -> dict:
    """节点图(reactflow)。

    {nodes:[{id,type,title,x,y,route}], edges:[{id,source,target,label,has_condition,has_effects}]}。
    """
    nodes_out = [
        {
            "id": n.id,
            "type": _enum_val(n.type),
            "title": n.title,
            "x": float(n.x or 0.0),
            "y": float(n.y or 0.0),
            "route": n.route,
        }
        for n in project.nodes
    ]
    edges_out = [
        {
            "id": c.id,
            "source": c.source,
            "target": c.target,
            "label": c.label,
            "has_condition": bool(c.condition),
            "has_effects": bool(c.effects),
        }
        for c in project.connections
    ]
    return {"nodes": nodes_out, "edges": edges_out}


# --------------------------------------------------------------------------- #
# 4. relationship_graph
# --------------------------------------------------------------------------- #
def relationship_graph(project: Project) -> dict:
    """人物关系图。

    {nodes:[{id,label,importance,color}], edges:[{id,from,to,label,nature,projection}]}。
    nodes = characters;edges = relationships(a→b)。
    """
    nodes_out = [
        {
            "id": ch.id,
            "label": ch.name,
            "importance": ch.importance,
            "color": ch.color,
        }
        for ch in project.characters
    ]
    edges_out = [
        {
            "id": r.id,
            "from": r.a,
            "to": r.b,
            "label": r.label or r.nature,
            "nature": r.nature,
            "projection": r.projection,
        }
        for r in project.relationships
    ]
    return {"nodes": nodes_out, "edges": edges_out}


# --------------------------------------------------------------------------- #
# 5. character_scenes
# --------------------------------------------------------------------------- #
def character_scenes(project: Project, char_id: str) -> list:
    """该角色出场的场景(links.characters 含 char_id 或 pov==char_id),按叙述顺序。

    每项 {scene_id, title, beat, pov}。
    """
    nodes = _node_by_id(project)
    beats = _beat_by_id(project)
    sidx = _scene_by_id(project)

    matched_ids: List[str] = []
    for s in project.scenes:
        links = s.links
        chars = list(links.characters) if links and links.characters else []
        pov = links.pov if links else None
        if char_id in chars or pov == char_id:
            matched_ids.append(s.id)

    ordered = _sort_scene_ids(project, matched_ids)
    out: List[dict] = []
    for sid in ordered:
        s = sidx[sid]
        out.append(
            {
                "scene_id": sid,
                "title": s.title,
                "beat": _scene_beat_id(project, s, nodes, beats),
                "pov": s.links.pov if s.links else None,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 6. variable_refs
# --------------------------------------------------------------------------- #
def variable_refs(project: Project, var_key: str) -> dict:
    """某变量的读/写引用。

    {reads:[{where,kind}], writes:[{where,kind}]}。
    用 expr.all_condition_vars / all_effect_vars 过滤 var==var_key。
    where 即来源描述(如 connection:e2),kind 取来源前缀(connection/node/scene/ending/reveal)。
    """

    def _kind(where: str) -> str:
        return where.split(":", 1)[0] if where else ""

    reads = [
        {"where": where, "kind": _kind(where)}
        for (v, where) in expr.all_condition_vars(project)
        if v == var_key
    ]
    writes = [
        {"where": where, "kind": _kind(where)}
        for (v, where) in expr.all_effect_vars(project)
        if v == var_key
    ]
    return {"reads": reads, "writes": writes}


# --------------------------------------------------------------------------- #
# 7. tag_occurrences
# --------------------------------------------------------------------------- #
def tag_occurrences(project: Project, tag_id: str) -> list:
    """挂该 tag 的场景与成文行,按叙述顺序。

    每项 {kind:'scene'|'line', id, title, order}。
    - scene:Scene.tags 含 tag_id;order = 其在叙述顺序中的位次。
    - line:ProseLine 的 scene_ref 指向命中场景,且该场景挂了此 tag(成文行随场景);
      ProseLine 自身无 tags 字段,故成文行的 tag 归属经其所属场景推断。
    """
    sidx = _scene_by_id(project)

    # 命中场景(挂了此 tag),按叙述顺序
    scene_ids = [s.id for s in project.scenes if tag_id in (s.tags or [])]
    ordered_scene_ids = _sort_scene_ids(project, scene_ids)
    scene_order = {sid: i for i, sid in enumerate(ordered_scene_ids)}

    out: List[dict] = []
    for sid in ordered_scene_ids:
        s = sidx[sid]
        out.append(
            {
                "kind": "scene",
                "id": sid,
                "title": s.title,
                "order": scene_order[sid],
            }
        )

    # 成文行:直接挂了此 tag(pl.tags),或其 scene_ref 命中上述场景集(经场景推断)
    hit = set(ordered_scene_ids)
    for pl in project.prose_lines:
        if tag_id in (getattr(pl, "tags", None) or []) or pl.scene_ref in hit:
            out.append(
                {
                    "kind": "line",
                    "id": pl.id,
                    "title": pl.text,
                    "order": scene_order.get(pl.scene_ref, 10**9),
                }
            )

    out.sort(key=lambda item: (item["order"], 0 if item["kind"] == "scene" else 1))
    return out


# --------------------------------------------------------------------------- #
# 8. idea_alignment
# --------------------------------------------------------------------------- #
def idea_alignment(project: Project, idea: str) -> list:
    """scenes 中 serves_ideas 含该 idea 的场景,按叙述顺序。

    每项 {scene_id, title, status}。
    """
    sidx = _scene_by_id(project)
    matched = [s.id for s in project.scenes if idea in (s.serves_ideas or [])]
    ordered = _sort_scene_ids(project, matched)
    return [
        {
            "scene_id": sid,
            "title": sidx[sid].title,
            "status": _enum_val(sidx[sid].status),
        }
        for sid in ordered
    ]


# --------------------------------------------------------------------------- #
# 9. drilldown:钻取链
# --------------------------------------------------------------------------- #
def drilldown(project: Project, scene_id: str) -> dict:
    """场景钻取:beat → 语义规格 → 成文行。

    {beat, scene_semantic:{objective_events,causality,value_shift,intent,render_constraints},
     prose:[行...]}。
    场景不存在时返回空骨架(不报错)。
    """
    nodes = _node_by_id(project)
    beats = _beat_by_id(project)
    sidx = _scene_by_id(project)

    s = sidx.get(scene_id)
    if s is None:
        return {
            "beat": None,
            "scene_semantic": {
                "objective_events": [],
                "causality": {},
                "value_shift": {},
                "intent": {},
                "render_constraints": {},
            },
            "prose": [],
        }

    bid = _scene_beat_id(project, s, nodes, beats)
    beat_out = None
    if bid is not None:
        b = beats[bid]
        beat_out = {"id": b.id, "title": b.title, "function": b.function}

    semantic = {
        "objective_events": list(s.objective_events or []),
        "causality": s.causality.model_dump(mode="json") if s.causality else {},
        "value_shift": s.value_shift.model_dump(mode="json", by_alias=True) if s.value_shift else {},
        "intent": s.intent.model_dump(mode="json") if s.intent else {},
        "render_constraints": s.render_constraints.model_dump(mode="json") if s.render_constraints else {},
    }

    # 成文行:优先按 scene.line_refs 顺序;补 scene_ref 反指但未列入 line_refs 的
    pl_idx = {pl.id: pl for pl in project.prose_lines}
    prose: List[dict] = []
    seen: set = set()
    for lid in (s.line_refs or []):
        pl = pl_idx.get(lid)
        if pl is not None and pl.id not in seen:
            seen.add(pl.id)
            prose.append(pl.model_dump(mode="json"))
    for pl in project.prose_lines:
        if pl.scene_ref == scene_id and pl.id not in seen:
            seen.add(pl.id)
            prose.append(pl.model_dump(mode="json"))

    return {"beat": beat_out, "scene_semantic": semantic, "prose": prose}


# --------------------------------------------------------------------------- #
# 10. distribution
# --------------------------------------------------------------------------- #
def distribution(project: Project) -> dict:
    """统计分布。

    {
      character_matrix: {char_id: {beat_id: count}},  # 角色×beat 出场计数
      pov_ratio: {pov_char_id: ratio},                # 各 pov 占场景比(0~1)
      line_supply: {storyline_id: count},             # 各 storyline 关联场景数
      act_lengths: {top_beat_id: count},              # 各顶层 beat 的场景数(含子)
    }。
    """
    nodes = _node_by_id(project)
    beats = _beat_by_id(project)

    # 角色×beat 出场计数(以 links.characters 为准)
    character_matrix: Dict[str, Dict[str, int]] = {}
    # pov 计数
    pov_counts: Dict[str, int] = {}
    pov_total = 0
    # storyline 关联场景数
    line_supply: Dict[str, int] = {}
    # 顶层 beat 场景数
    act_lengths: Dict[str, int] = {}

    def _top_beat(bid: Optional[str]) -> Optional[str]:
        cur = beats.get(bid) if bid else None
        seen = set()
        while cur is not None and cur.parent and cur.parent in beats and cur.id not in seen:
            seen.add(cur.id)
            cur = beats.get(cur.parent)
        return cur.id if cur is not None else None

    for s in project.scenes:
        bid = _scene_beat_id(project, s, nodes, beats)
        links = s.links

        chars = list(links.characters) if links and links.characters else []
        for cid in chars:
            character_matrix.setdefault(cid, {})
            if bid is not None:
                character_matrix[cid][bid] = character_matrix[cid].get(bid, 0) + 1

        pov = links.pov if links else None
        if pov:
            pov_counts[pov] = pov_counts.get(pov, 0) + 1
            pov_total += 1

        line_ids = list(links.lines) if links and links.lines else []
        for lid in line_ids:
            line_supply[lid] = line_supply.get(lid, 0) + 1

        if bid is not None:
            top = _top_beat(bid)
            if top is not None:
                act_lengths[top] = act_lengths.get(top, 0) + 1

    pov_ratio = (
        {pov: cnt / pov_total for pov, cnt in pov_counts.items()}
        if pov_total
        else {}
    )

    return {
        "character_matrix": character_matrix,
        "pov_ratio": pov_ratio,
        "line_supply": line_supply,
        "act_lengths": act_lengths,
    }


# --------------------------------------------------------------------------- #
# 11. provenance_forward
# --------------------------------------------------------------------------- #
def provenance_forward(project: Project, source: str) -> list:
    """所有 provenance.source==source 的实体。

    每项 {kind, id, title}。覆盖带 provenance 字段的全部载体:
    reveal_layers / world(递归) / characters / relationships / pressures /
    beats / nodes / endings / scenes;以及 premise(单对象,id 取 'premise')。
    """
    out: List[dict] = []

    def _match(entity: Any) -> bool:
        prov = getattr(entity, "provenance", None)
        return bool(prov and prov.source == source)

    # premise(单对象,无 id)
    if project.premise and project.premise.provenance and project.premise.provenance.source == source:
        out.append({"kind": "premise", "id": "premise", "title": project.premise.proposition})

    def _emit(kind: str, entity: Any, title: Any) -> None:
        if _match(entity):
            out.append({"kind": kind, "id": getattr(entity, "id", None) or getattr(entity, "node_id", None),
                        "title": title})

    for r in project.reveal_layers:
        _emit("reveal_layer", r, r.title)
    for ch in project.characters:
        _emit("character", ch, ch.name)
    for rel in project.relationships:
        _emit("relationship", rel, rel.label or rel.nature)
    for pr in project.pressures:
        _emit("pressure", pr, pr.name)
    for b in project.beats:
        _emit("beat", b, b.title)
    for n in project.nodes:
        _emit("node", n, n.title)
    for en in project.endings:
        _emit("ending", en, en.name)
    for s in project.scenes:
        _emit("scene", s, s.title)

    # world 树递归
    def _walk_world(node: Any) -> None:
        _emit("world", node, node.name)
        for c in node.children or []:
            _walk_world(c)

    for w in project.world:
        _walk_world(w)

    return out
