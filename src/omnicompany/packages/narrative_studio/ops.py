"""结构化操作(纯函数):全局查找替换、场景拆分/合并、项目 diff。

都在 Project 的 dump dict 上操作并 re-validate 回 Project,保证不破坏 schema。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .models import Project


def _dump(p: Project) -> Dict[str, Any]:
    return p.model_dump(by_alias=True, mode="json")


def _load(d: Dict[str, Any]) -> Project:
    return Project.model_validate(d)


# --------------------------------------------------------------------------- #
# 全局查找替换(遍历所有文本字段)
# --------------------------------------------------------------------------- #
def text_replace(project: Project, find: str, replace: str, dry_run: bool = False) -> Dict[str, Any]:
    """在所有字符串字段里把 find 替换成 replace。

    返回 {count, hits:[{path, before}], project: <dict|None>}。
    dry_run=True 时只返回命中数与样例,不产出新 project。
    """
    if not find:
        return {"count": 0, "hits": [], "project": None}
    data = _dump(project)
    hits: List[Dict[str, str]] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, str):
            if find in node:
                hits.append({"path": path, "before": node[:120]})
                return node.replace(find, replace)
            return node
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}") for k, v in node.items()}
        return node

    new_data = walk(data, "")
    out: Dict[str, Any] = {"count": len(hits), "hits": hits[:50]}
    out["project"] = None if dry_run else _dump(_load(new_data))
    return out


# --------------------------------------------------------------------------- #
# 场景拆分
# --------------------------------------------------------------------------- #
def scene_split(project: Project, scene_id: str, at: int = 1) -> Tuple[Dict[str, Any], List[str]]:
    """把一场按 objective_events 的第 at 个边界切成两场。

    第一场保留 events[:at] 与 line_refs[:at];第二场拿 events[at:]、line_refs[at:]。
    links/tags/intent/value_shift 复制到第二场;为第二场新建 ending? 否——新建 scene 型 node;
    原节点的出边改挂到第二场节点,并新增 第一场→第二场 一条边。
    返回 (new_project_dict, warnings)。
    """
    data = _dump(project)
    scenes = data.get("scenes", [])
    idx = next((i for i, s in enumerate(scenes) if s.get("id") == scene_id), -1)
    warnings: List[str] = []
    if idx < 0:
        return data, [f"场景 {scene_id} 不存在"]
    s = scenes[idx]
    events = list(s.get("objective_events") or [])
    line_refs = list(s.get("line_refs") or [])
    at = max(1, min(at, max(1, len(events) - 0)))

    new_id = f"{scene_id}-b"
    new_node_id = f"{(s.get('node_ref') or scene_id)}-b"

    # 第二场
    s2 = dict(s)
    s2["id"] = new_id
    s2["node_ref"] = new_node_id
    s2["title"] = (s.get("title") or scene_id) + "(续)"
    s2["objective_events"] = events[at:]
    s2["line_refs"] = line_refs[at:]
    s2["status"] = "todo"

    # 第一场缩到前半
    s["objective_events"] = events[:at]
    s["line_refs"] = line_refs[:at]

    scenes.insert(idx + 1, s2)
    data["scenes"] = scenes

    # 节点:为第二场建 scene 型 node
    nodes = data.get("nodes", [])
    src_node = next((n for n in nodes if n.get("id") == s.get("node_ref")), None)
    base_x = (src_node or {}).get("x", 0.0)
    base_y = (src_node or {}).get("y", 0.0)
    nodes.append({
        "id": new_node_id, "type": "scene", "title": s2["title"],
        "route": (src_node or {}).get("route"),
        "x": float(base_x) + 160.0, "y": float(base_y) + 80.0,
        "condition": [], "target": None, "provenance": None,
    })
    data["nodes"] = nodes

    # 连接:原节点的出边改挂到第二场节点;新增 第一场→第二场
    conns = data.get("connections", [])
    orig_node = s.get("node_ref")
    for c in conns:
        if c.get("source") == orig_node:
            c["source"] = new_node_id
    if orig_node:
        conns.append({
            "id": f"{scene_id}-split", "source": orig_node, "target": new_node_id,
            "condition": [], "effects": [], "label": "拆分续接",
        })
    data["connections"] = conns

    new_project = _load(data)
    # 事后断头校验
    from . import health
    dangling = [h for h in health.health_check(new_project) if h["code"] == "dangling_connection"]
    if dangling:
        warnings.extend(h["message"] for h in dangling)
    return _dump(new_project), warnings


# --------------------------------------------------------------------------- #
# 场景合并
# --------------------------------------------------------------------------- #
def scene_merge(project: Project, a_id: str, b_id: str) -> Tuple[Dict[str, Any], List[str]]:
    """把 b 合并进 a:事件/行/标签/链接并入 a,指向 b 的连接改指 a,b 的出边改从 a 出,删除 b 及其节点。"""
    data = _dump(project)
    scenes = data.get("scenes", [])
    a = next((s for s in scenes if s.get("id") == a_id), None)
    b = next((s for s in scenes if s.get("id") == b_id), None)
    warnings: List[str] = []
    if a is None or b is None:
        return data, [f"场景 {a_id} 或 {b_id} 不存在"]

    a["objective_events"] = (a.get("objective_events") or []) + (b.get("objective_events") or [])
    a["line_refs"] = (a.get("line_refs") or []) + (b.get("line_refs") or [])
    a["tags"] = list(dict.fromkeys((a.get("tags") or []) + (b.get("tags") or [])))
    # links 并集(characters/places/lines)
    la, lb = a.get("links") or {}, b.get("links") or {}
    for k in ("characters", "places", "lines"):
        la[k] = list(dict.fromkeys((la.get(k) or []) + (lb.get(k) or [])))
    a["links"] = la

    a_node, b_node = a.get("node_ref"), b.get("node_ref")
    conns = data.get("connections", [])
    kept = []
    for c in conns:
        if c.get("target") == b_node:
            c["target"] = a_node
        if c.get("source") == b_node:
            c["source"] = a_node
        # 删自环
        if c.get("source") == a_node and c.get("target") == a_node:
            continue
        kept.append(c)
    data["connections"] = kept

    data["scenes"] = [s for s in scenes if s.get("id") != b_id]
    data["nodes"] = [n for n in data.get("nodes", []) if n.get("id") != b_node]

    new_project = _load(data)
    from . import health
    dangling = [h for h in health.health_check(new_project) if h["code"] == "dangling_connection"]
    if dangling:
        warnings.extend(h["message"] for h in dangling)
    return _dump(new_project), warnings


# --------------------------------------------------------------------------- #
# 项目 diff(版本对照)
# --------------------------------------------------------------------------- #
_ID_FIELD = {"endings": "node_id"}
_LIST_CARRIERS = [
    "reveal_layers", "world", "characters", "relationships",
    "variables", "stat_blocks", "pressures", "failure_levels",
    "beats", "storylines", "pacing", "nodes", "connections", "endings",
    "scenes", "prose_lines", "voices", "registers", "style_matrix", "tags", "notes",
    "game_texts", "rejected_archive", "comments",
]


def _ids(items: List[dict], carrier: str) -> set:
    f = _ID_FIELD.get(carrier, "id")
    out = set()
    for it in items or []:
        if carrier == "variables":
            out.add(f"{it.get('namespace')}.{it.get('name')}")
        elif f in it:
            out.add(it[f])
    return out


def project_diff(a: Project, b: Project) -> Dict[str, Any]:
    """逐载体比较 a→b:每载体 {added, removed, a_count, b_count}。"""
    da, db = _dump(a), _dump(b)
    out: Dict[str, Any] = {"carriers": {}}
    for carrier in _LIST_CARRIERS:
        ia, ib = _ids(da.get(carrier, []), carrier), _ids(db.get(carrier, []), carrier)
        added = sorted(ib - ia)
        removed = sorted(ia - ib)
        if added or removed or len(ia) != len(ib):
            out["carriers"][carrier] = {
                "added": added, "removed": removed,
                "a_count": len(ia), "b_count": len(ib),
            }
    # premise 文本差异(粗粒度)
    if da.get("premise") != db.get("premise"):
        out["premise_changed"] = True
    return out
