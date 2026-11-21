# [OMNI] origin=claude domain=services/_focus ts=2026-09-04 type=service status=active
# [OMNI] summary="收件箱核心: 订阅矩阵解析 + 来源适配器 + 实时取源。消息不物化, 列表现问适配器; 只有处置(忽略/延后/已处理)落存储, 分诊出箱才落成任务。"
# [OMNI] why="用户 2026-09-03: 专注就完成全部工作的地方。放 services/_focus(专注系统那一层)而不是 dashboard —— CLI 不得依赖 dashboard 层(arch_gate R2), 而且这本来就不是 dashboard 的关切。"
# [OMNI] material_id="material:packages.services._focus.inbox"
"""收件箱核心 —— 个人消息总线。

原口径是「一条消息 = 一条还没被提升的任务」，靠 `sync` 把候选物化成总线任务。
2026-09-05 用户推翻：**「这为啥要定时同步，这应该直接取源，定时也太离谱了」**——
物化的代价是一条提审得先被同步才看得见（于是要有人定时推，而且推进去删不掉），
而真正需要持久的只有「你对它做了什么」。

所以现在只有三样东西：

  1. **适配器**：把各来源拍成候选消息（一个来源一个 list()）；
  2. **订阅矩阵**：决定哪一条允许进箱（配置，落个人附加层，不是形状）；
  3. **处置层**：dedupeKey → 忽略 / 延后到某时 / 已处理（inbox_dispositions）。

列表每次现算，延后到点自己回来。只有 `triage`（分诊出箱）会往总线写东西 ——
那一刻它才是一件活。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from omnicompany.cli.commands import tbus as bridge
from omnicompany.packages.services._focus import inbox_dispositions as dispo

_ROOT = Path(__file__).resolve().parents[5]
_PROFILE_PATH = _ROOT / "data" / "task_core_profile.json"

# surface 词表。off < log < inbox < interrupt，越往右越吵。
SURFACES = ("off", "log", "inbox", "interrupt")


# ─── 订阅矩阵（配置, 来自个人附加层）────────────────────────────────────

def profile() -> dict[str, Any]:
    try:
        return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_surface(source: str, kind: str, prof: dict[str, Any] | None = None) -> dict[str, Any]:
    """(来源, 事件类型) → {surface, priority}。

    精确匹配优先于通配 `*`；都没命中走 default。**新来源默认从严（default 是 log）**——
    接一个新来源不该让收件箱当天被灌爆，要进箱得显式写一条规则。
    """
    subs = (prof if prof is not None else profile()).get("subscriptions") or {}
    rules = subs.get("rules") or []
    exact = None
    wildcard = None
    for rule in rules:
        if str(rule.get("source")) != source:
            continue
        rule_kind = str(rule.get("kind") or "*")
        if rule_kind == kind and exact is None:
            exact = rule
        elif rule_kind == "*" and wildcard is None:
            wildcard = rule
    hit = exact or wildcard or subs.get("default") or {"surface": "log", "priority": None}
    surface = str(hit.get("surface") or "log")
    return {
        "surface": surface if surface in SURFACES else "log",
        "priority": hit.get("priority"),
    }


def dedupe_key(source: str, remote_id: str) -> str:
    return f"{source}:{remote_id}"


# ─── 适配器（桥）────────────────────────────────────────────────────────
# 每个来源只需返回候选消息。去重、定级、落盘一律不归适配器管。

def _candidate(
    source: str, kind: str, remote_id: str, title: str, *,
    body: str = "", project_id: str | None = None, session_id: str | None = None,
    url: str = "",
) -> dict[str, Any]:
    return {
        "source": source,
        "kind": kind,
        "remoteId": str(remote_id),
        "title": (title.strip()[:200] or f"{source} 消息"),
        "body": body,
        "projectId": project_id,
        "sessionId": session_id,
        "url": url,
    }


def adapter_review() -> list[dict[str, Any]]:
    """内容提审 —— 待裁决的材料卡。必验收单单列一类, 好让订阅矩阵给它更高的 surface。"""
    try:
        from omnicompany.packages.services._core import review_store
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for material in review_store.list_materials(include_archived=False, limit=200):
        if str(material.get("status") or "") != "pending":
            continue
        mandatory = str(material.get("tier") or "") == "mandatory"
        material_id = str(material.get("id") or "")
        if not material_id:
            continue
        out.append(_candidate(
            "review",
            "mandatory-pending" if mandatory else "pending",
            material_id,
            str(material.get("title") or "待裁决材料"),
            body=str(material.get("type") or ""),
            project_id=(str(material.get("project") or "") or None),
            session_id=(str(material.get("session_id") or "") or None),
            url="/review-stage?material=" + material_id,
        ))
    return out


def adapter_agent() -> list[dict[str, Any]]:
    """agent 举手 —— 「干完一段活, 请你过目本对话」。"""
    try:
        from omnicompany.packages.services.boss_sight.services import agent_attention
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for req in agent_attention.list_attention():
        session_id = str(req.get("session_id") or "")
        if not session_id:
            continue
        out.append(_candidate(
            "agent",
            str(req.get("kind") or "conversation"),
            session_id,
            str(req.get("headline") or "agent 请你过目本对话"),
            body=str(req.get("identity") or ""),
            project_id=(str(req.get("project") or "") or None),
            session_id=session_id,
        ))
    return out


def adapter_session() -> list[dict[str, Any]]:
    """会话结束 —— 「跑完一轮却还没被你看」。

    队列里最重要的一类(用户 2026-09-04:「消息队列的关键是未回复或者说观看的会话」)。
    判据在 session_attention: ccdaemon 发布投影, 那里存已看记录, 这里只是把未看的那些
    拍成候选 —— 适配器不认识指纹怎么算, 也不该认识。

    remoteId 用**会话 id + 指纹**: 同一个会话又跑完一轮就是一条新消息(老的已被处理掉),
    只用会话 id 的话第二轮会被去重索引当成"已经存在"而永远不出现。
    """
    try:
        from omnicompany.packages.services._focus import session_attention
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in session_attention.list_unseen():
        session_id = str(row.get("sessionId") or "")
        fingerprint = str(row.get("fingerprint") or "")
        if not session_id or not fingerprint:
            continue
        out.append(_candidate(
            "session",
            "session-ended",
            f"{session_id}@{fingerprint}",
            str(row.get("title") or "会话跑完了, 等你看一眼"),
            body=str(row.get("provider") or ""),
            project_id=(str(row.get("project") or "") or None),
            session_id=session_id,
        ))
    return out


ADAPTERS = {"review": adapter_review, "agent": adapter_agent, "session": adapter_session}


def collect() -> list[dict[str, Any]]:
    """跑一遍所有适配器并附上订阅判定。off 的直接不要 —— 连读都不该读的不进内存。"""
    prof = profile()
    out: list[dict[str, Any]] = []
    for name, fn in ADAPTERS.items():
        try:
            found = fn()
        except Exception as exc:  # 一个来源坏掉不该让整个收件箱空掉
            broken = _candidate(name, "adapter-error", "error:" + name,
                                f"{name} 来源读取失败: {exc}")
            broken["surface"] = "inbox"
            broken["priority"] = None
            out.append(broken)
            continue
        for cand in found:
            rule = resolve_surface(cand["source"], cand["kind"], prof)
            if rule["surface"] == "off":
                continue
            cand["surface"] = rule["surface"]
            cand["priority"] = rule["priority"]
            out.append(cand)
    return out


# ─── 同步 ───────────────────────────────────────────────────────────────

def _run(args: list[str]):
    code, payload, stderr = bridge._run_cli(bridge._resolve_dir(None), args)
    failure = bridge._envelope_error(payload)
    if failure or code != 0 or not payload:
        message = failure[1] if failure else ((stderr or "").strip() or f"exit={code}")
        return None, (failure[0] if failure else "runtime_error"), message
    return payload, None, None


def _result(payload) -> dict:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    return result if isinstance(result, dict) else {}


def board(now: float | None = None) -> dict[str, Any]:
    """收件箱一屏 —— **实时取源**, 不读总线, 不需要任何人来推。

    用户 2026-09-05:「这应该直接取源, 定时也太离谱了」。所以这里现问适配器,
    再拿处置层把你已经忽略/处理掉的滤掉、把还没到点的算进延后计数。
    延后到点自己就回来了 —— 列表本来就是现算的。

    **延后的不在这里出现**: startAt 还没到就不该占视野, 这正是「往后拖」的意思。
    """
    now = now or time.time()
    prof = profile()
    rows: list[dict[str, Any]] = []
    deferred = 0
    dispositions = dispo.all_dispositions()
    live_keys: set[str] = set()
    for cand in collect():
        if str(cand.get("surface")) not in ("inbox", "interrupt"):
            continue
        key = dedupe_key(str(cand["source"]), str(cand["remoteId"]))
        live_keys.add(key)
        state = dispo.visibility(key, dispositions, now)
        if state == "hidden":
            continue
        if state == "deferred":
            deferred += 1
            continue
        rows.append({
            "key": key,
            # taskId 只有被提升过的才有 —— 消息本身不是任务, 不再为了能看见而物化。
            "taskId": None,
            "title": cand.get("title"),
            "body": cand.get("body") or "",
            "priority": cand.get("priority"),
            "revision": None,
            "projectId": cand.get("projectId") or "未分组",
            "sessionId": cand.get("sessionId") or None,
            "source": sourceLabel(str(cand.get("source"))),
            "rawSource": cand.get("source"),
            "kind": cand.get("kind"),
            "url": cand.get("url") or "",
            "orderKey": None,
            "createdAt": None,
            "surface": cand.get("surface"),
        })
    dispo.prune(live_keys)

    # 项目按「这个项目里最高的一级」排, 项目内部也按阶梯排 —— 与待办面同一条口径。
    by_project: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_project.setdefault(str(row["projectId"]), []).append(row)
    projects = []
    for project_id, items in by_project.items():
        items.sort(key=lambda r: -(r["priority"] if r["priority"] is not None else -1))
        by_session: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_session.setdefault(str(item["sessionId"] or ""), []).append(item)
        projects.append({
            "projectId": project_id,
            "top": max((r["priority"] if r["priority"] is not None else -1) for r in items),
            "count": len(items),
            "sessions": [
                {"sessionId": sid or None, "items": group}
                for sid, group in sorted(by_session.items(), key=lambda kv: kv[0])
            ],
        })
    projects.sort(key=lambda p: (-p["top"], p["projectId"]))
    return {"ok": True, "projects": projects, "total": len(rows), "deferred": deferred}


SOURCE_LABELS = {"review": "提审", "agent": "Agent", "session": "会话"}


def sourceLabel(source: str) -> str:  # noqa: N802 - 与前端字段名对齐
    return SOURCE_LABELS.get(source, source)


# ─── 动作（全落 task 信封, 收件箱没有第二套状态机）──────────────────────

def act(key: str, action: str, *, until: str | None = None) -> dict[str, Any]:
    """处理 / 往后拖 / 忽略 / 撤销 —— 只写处置层。

    键是 dedupeKey(source:remoteId), 不是任务 id: 消息本身不是任务。只有 `triage`
    才把它落成一条正经待办 —— 那一刻它就不再是消息了。
    """
    if action == "done":
        return dispo.set_disposition(key, "done")
    if action == "dismiss":
        return dispo.set_disposition(key, "dismissed")
    if action == "defer":
        return dispo.set_disposition(key, "deferred", until=until)
    if action == "undo":
        return {"ok": True, "key": key, "cleared": dispo.clear(key)}
    if action == "triage":
        return triage(key)
    return {"ok": False, "code": "invalid_argument", "message": f"不认识的动作: {action}"}


def triage(key: str) -> dict[str, Any]:
    """分诊出箱: 把这条消息落成一条正经待办, 并在处置层记为已处理。

    这是**唯一**会往总线写东西的路径 —— 只有你决定「这是要干的活」时才物化。
    """
    for cand in collect():
        if dedupe_key(str(cand["source"]), str(cand["remoteId"])) != key:
            continue
        args = ["task", "add", "--title", str(cand.get("title") or "消息"), "--json"]
        if cand.get("projectId"):
            args += ["--project", str(cand["projectId"])]
        if cand.get("priority") is not None:
            args += ["--priority", str(cand["priority"])]
        payload, code, message = _run(args)
        if code:
            return {"ok": False, "code": code, "message": message}
        dispo.set_disposition(key, "done")
        task = _result(payload).get("task") or {}
        return {"ok": True, "key": key, "taskId": task.get("id")}
    return {"ok": False, "code": "not_found", "message": f"源里已经没有这条消息: {key}"}


def mute(source: str, kind: str, surface: str = "off") -> dict[str, Any]:
    """就地退订「以后不要这类」—— 学 Paradox 的 Ctrl+点消息。

    直接改个人附加层那份 JSON：它本来就是用户的配置，改它天经地义；去设置页翻找
    才是这类功能死掉的原因。
    """
    if surface not in SURFACES:
        return {"ok": False, "code": "invalid_argument", "message": f"不认识的档位: {surface}"}
    data = profile()
    subs = data.setdefault("subscriptions", {"default": {"surface": "log"}, "rules": []})
    rules = subs.setdefault("rules", [])
    for rule in rules:
        if str(rule.get("source")) == source and str(rule.get("kind") or "*") == kind:
            rule["surface"] = surface
            rule["why"] = str(rule.get("why") or "") + f"（{time.strftime('%Y-%m-%d')} 就地改为 {surface}）"
            break
    else:
        rules.append({"source": source, "kind": kind, "surface": surface,
                      "priority": None, "why": f"{time.strftime('%Y-%m-%d')} 就地退订"})
    data["updatedAt"] = time.strftime("%Y-%m-%d")
    _PROFILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "source": source, "kind": kind, "surface": surface}
