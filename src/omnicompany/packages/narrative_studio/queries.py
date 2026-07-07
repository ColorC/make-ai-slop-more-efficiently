"""项目级只读查询:完成度统计、空字段下钻、跨载体全文检索。

设计要点:
- 全部纯函数,无副作用;输入缺字段/空列表都不报错。
- 返回值一律 JSON 可序列化(基本类型 / Status.value 字符串);供 HTTP API 直接透传。
- 字段名严格对齐 models.py 契约,不臆造。

三态约定(models.Status):todo / tocomplete / done。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import Project, Status

# --------------------------------------------------------------------------- #
# 内部小工具
# --------------------------------------------------------------------------- #
# 带 status 字段的载体:载体名 -> Project 上的属性名
_STATUS_CARRIERS = {
    "characters": "characters",
    "scenes": "scenes",
    "beats": "beats",
    "prose_lines": "prose_lines",
    "reveal_layers": "reveal_layers",
    # dossier 是挂在 character 下的子载体,单独处理(见 completeness)。
}


def _status_value(obj: Any) -> Optional[str]:
    """取实体的 status 并归一成字符串;无 status 返回 None。"""
    st = getattr(obj, "status", None)
    if st is None:
        return None
    if isinstance(st, Status):
        return st.value
    # 容错:已是字符串
    return str(st)


def _is_blank(x: Any) -> bool:
    """判定某个关键字段是否"空"。None / 空串 / 空列表 / 空字典 都算空。"""
    if x is None:
        return True
    if isinstance(x, str):
        return x.strip() == ""
    if isinstance(x, (list, dict, tuple, set)):
        return len(x) == 0
    return False


# 每类实体的关键字段判定:返回缺失的字段名列表(空=不缺)。
# 字段名严格对齐 models.py。
def _missing_scene(s: Any) -> List[str]:
    missing: List[str] = []
    if _is_blank(getattr(s, "objective_events", None)):
        missing.append("objective_events")
    vs = getattr(s, "value_shift", None)
    # ValueShift 的 from_ 用 alias "from";to 直读。两端都空才算 value_shift 缺。
    vs_from = getattr(vs, "from_", None) if vs is not None else None
    vs_to = getattr(vs, "to", None) if vs is not None else None
    if _is_blank(vs_from) and _is_blank(vs_to):
        missing.append("value_shift")
    intent = getattr(s, "intent", None)
    intent_emotion = getattr(intent, "emotion", None) if intent is not None else None
    if _is_blank(intent_emotion):
        missing.append("intent.emotion")
    return missing


def _missing_character(c: Any) -> List[str]:
    missing: List[str] = []
    arc = getattr(c, "arc", None)
    arc_want = getattr(arc, "want", None) if arc is not None else None
    if _is_blank(arc_want):
        missing.append("arc.want")
    summary = getattr(c, "summary", None)
    summary_sentence = getattr(summary, "sentence", None) if summary is not None else None
    if _is_blank(summary_sentence):
        missing.append("summary.sentence")
    return missing


def _missing_premise(pr: Any) -> List[str]:
    missing: List[str] = []
    if _is_blank(getattr(pr, "proposition", None)):
        missing.append("proposition")
    if _is_blank(getattr(pr, "controlling_ideas", None)):
        missing.append("controlling_ideas")
    return missing


def _missing_prose_line(pl: Any) -> List[str]:
    missing: List[str] = []
    if _is_blank(getattr(pl, "text", None)):
        missing.append("text")
    return missing


def _missing_reveal_layer(rl: Any) -> List[str]:
    missing: List[str] = []
    if _is_blank(getattr(rl, "trigger", None)):
        missing.append("trigger")
    if _is_blank(getattr(rl, "rewrites", None)):
        missing.append("rewrites")
    return missing


# 实体种类 -> (Project 属性名, 缺失字段检查器, 标题取法)
def _entity_title(obj: Any) -> str:
    """统一标题取法:title -> name -> sentence -> id -> ''。"""
    for attr in ("title", "name"):
        v = getattr(obj, attr, None)
        if not _is_blank(v):
            return str(v)
    summary = getattr(obj, "summary", None)
    if summary is not None:
        sent = getattr(summary, "sentence", None)
        if not _is_blank(sent):
            return str(sent)
    v = getattr(obj, "id", None)
    return str(v) if v is not None else ""


# --------------------------------------------------------------------------- #
# 1) 完成度统计
# --------------------------------------------------------------------------- #
def completeness(project: Project) -> Dict[str, Any]:
    """统计各带 status 载体的三态分布 + 全局汇总。

    返回:
      {
        "by_carrier": {
          "<carrier>": {todo, tocomplete, done, empty, total},
          ...
        },
        "overall": {todo, tocomplete, done, total, percent_done}
      }

    - empty:该载体里"关键字段为空"的实体数(有专用关键字段检查器的载体才统计,
      其余为 0)。
    - percent_done:done / total * 100,四舍五入到 1 位小数;total=0 时为 0.0。
    """
    by_carrier: Dict[str, Dict[str, int]] = {}
    overall = {"todo": 0, "tocomplete": 0, "done": 0, "total": 0}

    # 各载体的"关键字段空"检查器(没有的载体 empty 记 0)
    empty_checkers = {
        "scenes": _missing_scene,
        "characters": _missing_character,
        "prose_lines": _missing_prose_line,
        "reveal_layers": _missing_reveal_layer,
    }

    for carrier, attr in _STATUS_CARRIERS.items():
        items = getattr(project, attr, None) or []
        bucket = {"todo": 0, "tocomplete": 0, "done": 0, "empty": 0, "total": 0}
        checker = empty_checkers.get(carrier)
        for it in items:
            sv = _status_value(it)
            if sv in ("todo", "tocomplete", "done"):
                bucket[sv] += 1
                overall[sv] += 1
            bucket["total"] += 1
            overall["total"] += 1
            if checker is not None and len(checker(it)) > 0:
                bucket["empty"] += 1
        by_carrier[carrier] = bucket

    # dossier 是挂在 character 下的带 status 子载体,单列一格便于下钻。
    dossier_bucket = {"todo": 0, "tocomplete": 0, "done": 0, "empty": 0, "total": 0}
    for c in (getattr(project, "characters", None) or []):
        for df in (getattr(c, "dossier", None) or []):
            sv = _status_value(df)
            if sv in ("todo", "tocomplete", "done"):
                dossier_bucket[sv] += 1
                overall[sv] += 1
            dossier_bucket["total"] += 1
            overall["total"] += 1
    by_carrier["dossier"] = dossier_bucket

    total = overall["total"]
    percent_done = round(overall["done"] / total * 100, 1) if total > 0 else 0.0
    overall_out = {
        "todo": overall["todo"],
        "tocomplete": overall["tocomplete"],
        "done": overall["done"],
        "total": total,
        "percent_done": percent_done,
    }
    return {"by_carrier": by_carrier, "overall": overall_out}


# --------------------------------------------------------------------------- #
# 2) 空字段下钻
# --------------------------------------------------------------------------- #
def empties(project: Project) -> List[Dict[str, Any]]:
    """列出关键字段为空之处,供"渐进补全"逐项下钻。

    返回 [{entity_kind, entity_id, title, missing_fields:[...]}, ...]。
    仅返回确有缺失的实体。

    关键字段:
      scene        -> objective_events / value_shift / intent.emotion
      character    -> arc.want / summary.sentence
      premise      -> proposition / controlling_ideas
      prose_line   -> text
      reveal_layer -> trigger / rewrites
    """
    out: List[Dict[str, Any]] = []

    # premise 是单对象载体
    premise = getattr(project, "premise", None)
    if premise is not None:
        miss = _missing_premise(premise)
        if miss:
            out.append({
                "entity_kind": "premise",
                "entity_id": getattr(project.meta, "id", "premise") if getattr(project, "meta", None) else "premise",
                "title": getattr(premise, "proposition", None) or "立意",
                "missing_fields": miss,
            })

    list_specs = [
        ("scene", "scenes", _missing_scene),
        ("character", "characters", _missing_character),
        ("prose_line", "prose_lines", _missing_prose_line),
        ("reveal_layer", "reveal_layers", _missing_reveal_layer),
    ]
    for kind, attr, checker in list_specs:
        for it in (getattr(project, attr, None) or []):
            miss = checker(it)
            if miss:
                out.append({
                    "entity_kind": kind,
                    "entity_id": getattr(it, "id", None),
                    "title": _entity_title(it),
                    "missing_fields": miss,
                })
    return out


# --------------------------------------------------------------------------- #
# 3) 跨载体全文检索
# --------------------------------------------------------------------------- #
def _snippet(text: str, q_lower: str, span: int = 30) -> str:
    """围绕命中位置截一段上下文片段。"""
    low = text.lower()
    idx = low.find(q_lower)
    if idx < 0:
        return text[:span * 2].strip()
    start = max(0, idx - span)
    end = min(len(text), idx + len(q_lower) + span)
    snip = text[start:end].strip()
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip = snip + "…"
    return snip


def _gather_texts(obj: Any) -> List[str]:
    """从一个实体里收集所有可搜索的文本字段(扁平化)。"""
    texts: List[str] = []

    def push(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, str):
            if v.strip():
                texts.append(v)
        elif isinstance(v, (list, tuple)):
            for x in v:
                push(x)

    # 直接文本字段(各载体的常见可读字段;不存在的 getattr 安全返回 None)
    for attr in (
        "title", "name", "text", "label", "intent_summary", "proposition",
        "stance", "rewrites", "rewrites_controlling_idea", "nature",
        "projection", "description", "freetext", "secret", "speaker",
    ):
        push(getattr(obj, attr, None))

    # 列表文本字段
    for attr in ("objective_events", "controlling_ideas", "serves_ideas"):
        push(getattr(obj, attr, None))

    # summary 子对象(sentence/paragraph/full)
    summary = getattr(obj, "summary", None)
    if summary is not None:
        for a in ("sentence", "paragraph", "full"):
            push(getattr(summary, a, None))

    # arc 子对象
    arc = getattr(obj, "arc", None)
    if arc is not None:
        for a in ("want", "need", "wound", "lie"):
            push(getattr(arc, a, None))

    # intent 子对象
    intent = getattr(obj, "intent", None)
    if intent is not None:
        for a in ("emotion", "punch", "resonance", "afterglow"):
            push(getattr(intent, a, None))

    # value_shift 子对象
    vs = getattr(obj, "value_shift", None)
    if vs is not None:
        push(getattr(vs, "from_", None))
        push(getattr(vs, "to", None))

    return texts


def search(project: Project, q: str) -> List[Dict[str, Any]]:
    """跨载体大小写不敏感全文检索。

    返回 [{kind, id, title, snippet}, ...];q 为空(或纯空白)返回 []。
    搜索 title / name / 各文本字段 / objective_events / summary / arc / intent 等。
    """
    if q is None:
        return []
    q_lower = q.strip().lower()
    if not q_lower:
        return []

    out: List[Dict[str, Any]] = []

    # 待扫描的列表载体:kind -> 属性名
    list_carriers = {
        "scene": "scenes",
        "character": "characters",
        "beat": "beats",
        "prose_line": "prose_lines",
        "reveal_layer": "reveal_layers",
        "node": "nodes",
        "connection": "connections",
        "ending": "endings",
        "relationship": "relationships",
        "storyline": "storylines",
        "world_node": "world",
        "tag": "tags",
        "note": "notes",
        "voice": "voices",
        "register": "registers",
        "pressure": "pressures",
    }

    def scan_entity(kind: str, obj: Any) -> None:
        texts = _gather_texts(obj)
        for t in texts:
            if q_lower in t.lower():
                # ending 用 node_id 作 id;其余用 id
                eid = getattr(obj, "id", None)
                if eid is None:
                    eid = getattr(obj, "node_id", None)
                out.append({
                    "kind": kind,
                    "id": eid,
                    "title": _entity_title(obj),
                    "snippet": _snippet(t, q_lower),
                })
                return  # 每个实体只记一条命中

    for kind, attr in list_carriers.items():
        for it in (getattr(project, attr, None) or []):
            scan_entity(kind, it)

    # premise 单对象载体
    premise = getattr(project, "premise", None)
    if premise is not None:
        texts = _gather_texts(premise)
        for t in texts:
            if q_lower in t.lower():
                meta = getattr(project, "meta", None)
                out.append({
                    "kind": "premise",
                    "id": getattr(meta, "id", "premise") if meta is not None else "premise",
                    "title": getattr(premise, "proposition", None) or "立意",
                    "snippet": _snippet(t, q_lower),
                })
                break

    # world 是树,需递归子节点
    def scan_world(nodes: Any) -> None:
        for wn in (nodes or []):
            # 顶层已在 list_carriers["world_node"] 扫过,这里只补子节点
            for child in (getattr(wn, "children", None) or []):
                scan_entity("world_node", child)
                scan_world([child])

    scan_world(getattr(project, "world", None))

    return out
