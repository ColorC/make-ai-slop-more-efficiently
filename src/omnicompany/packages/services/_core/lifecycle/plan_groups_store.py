# [OMNI] origin=kimi-code domain=services/_core/lifecycle ts=2026-08-13 type=infra status=active
# [OMNI] summary="plan-groups 文件真源读写层(M3b): data/services/plan-groups/*/tasks.json 的读/原子写/board 视图/状态归一化/完成硬闸等效链, 供 TaskStore 后端与各 whatnow 消费方换源复用"
# [OMNI] why="NOTES-CANVAS-CONVERGENCE M3b: 任务真源从 whatnow(:8230 单 JSON) 切到仓内纯文件模型; 所有消费方共用这一层, 不各自重造文件 IO 与 board 拼装"
# [OMNI] tags=lifecycle,task,plan-groups,ssot,migration
"""plan-groups 文件真源读写层（M3b）。

数据形态（M3a 迁移产物，schema plan-groups/v1）：
  data/services/plan-groups/<group_id>/group.yaml + tasks.json
  tasks.json = {schema, group_id, milestones[], tasks[], progress[], pins[], meta}
  task 条目 = 一等字段(id/title/status/completion/plan_id/milestone_id/parent_task_id/
              dependencies/priority/due_date/description/archived/created_at/updated_at)
              + legacy(whatnow 原始全量记录)

本层职责：
  - 原子写（tmp + os.replace，沿用 M3a 惯例）；进程内写锁
  - 状态归一化（STATUS_MAP，原文永留 legacy.status）
  - whatnow /api/board 兼容视图（QuestBoard / whatnow_advance / recorder / resolver 共用）
  - 完成硬闸等效链：patch_task 把「计划级 task（无 parent_task_id、带 plan_id）从非 done
    类改为 done 类」时，同步调 plan_completion_gate.evaluate，拒绝则不落盘（对齐
    progress-service main.rs patch_task 的 gate_plan_for_done_patch + fail-open 语义）。
    切换后 whatnow 侧 patch_task 钩子不再触发，这是唯一的替代触发点。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

# ───────────────────────── 状态归一化（用户 2026-08-13 拍板口径）─────────────────────────
# 目标七态 = lifecycle/task.py VALID_STATUS。原文不丢：legacy.status 永久保留原值。
STATUS_MAP: dict[str, str] = {
    # 直映
    "pending": "pending", "in_progress": "in_progress", "review": "review",
    "done": "done", "blocked": "blocked", "deferred": "deferred", "cancelled": "cancelled",
    # 拍板映射
    "未开始": "pending", "todo": "pending",
    "doing": "in_progress", "进行中": "in_progress", "处理中": "in_progress",
    "需求方待验收": "review", "待验证": "review", "in_review": "review",
    # 历史怪值 → 最近合理态
    "paused": "deferred",        # 主动挂起 ≈ deferred(可恢复), 不是 blocked(被依赖卡)
    "重新打开": "pending",
    "暂无法进行": "blocked",
    "state_0": "pending",        # meego 工作流初始态
    "state_3": "in_progress",    # 开发阶段
    "state_5": "in_progress",    # 细化案终版
    "state_8": "in_progress",    # 配置档中版
    "state_13": "in_progress",   # 推进排期
    "state_16": "review",        # 开发宣讲(接近验收)
    "": "pending",
}

# 与 progress-service main.rs is_done_status 同口径（完成硬闸触发判定 + board isDone 同源）
_DONE_WORDS = (
    "done", "closed", "cancelled", "canceled", "completed", "resolved", "merged",
    "已完成", "已关闭", "已取消", "已解决", "完成", "关闭", "取消", "解决", "下线", "已上线", "已发布",
)


def normalize_status(s: Any) -> str:
    """whatnow 原始状态 → 七态。未知值 → pending（原文在 legacy.status，不丢）。"""
    key = str(s or "").strip()
    if key in STATUS_MAP:
        return STATUS_MAP[key]
    low = key.lower()
    if low in STATUS_MAP:
        return STATUS_MAP[low]
    return "pending"


def is_done_status(s: Any) -> bool:
    t = str(s or "").strip().lower()
    return any(t == d or d in t for d in _DONE_WORDS)


def _repo_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root()


def groups_root() -> Path:
    return _repo_root() / "data" / "services" / "plan-groups"


# ───────────────────────── 文件 IO（原子写）─────────────────────────

_write_lock = threading.Lock()


def list_group_ids() -> list[str]:
    root = groups_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if (d / "tasks.json").is_file())


def load_group(group_id: str) -> dict[str, Any] | None:
    p = groups_root() / group_id / "tasks.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def iter_groups() -> list[tuple[str, dict[str, Any]]]:
    out = []
    for gid in list_group_ids():
        payload = load_group(gid)
        if payload is not None:
            out.append((gid, payload))
    return out


def save_group(group_id: str, payload: dict[str, Any]) -> None:
    """原子写 tasks.json（tmp + os.replace）。调用方持 _write_lock 或自行保证串行。"""
    p = groups_root() / group_id / "tasks.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ───────────────────────── 条目级操作 ─────────────────────────

# task 条目一等字段（其余在 legacy）
_ENTRY_FIELDS = (
    "id", "title", "status", "completion", "plan_id", "milestone_id", "parent_task_id",
    "dependencies", "priority", "due_date", "description", "archived",
    "created_at", "updated_at",
)


def find_task(task_id: str) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """全组查 task 条目。返回 (group_id, payload, entry)。"""
    for gid, payload in iter_groups():
        for entry in payload.get("tasks") or []:
            if entry.get("id") == task_id:
                return (gid, payload, entry)
    return None


class GateRefused(RuntimeError):
    """完成硬闸拒绝：计划级 task 转 done 被 plan_completion_gate 拦下（不落盘）。"""

    def __init__(self, plan_id: str, reason: str) -> None:
        super().__init__(f"plan_completion_gate 拒绝 (plan_id={plan_id}): {reason}")
        self.plan_id = plan_id
        self.reason = reason


def _completion_for_status(status: str) -> int:
    return {"done": 100, "review": 80, "in_progress": 40}.get(status, 0)


def _gate_plan_for_done(entry: dict[str, Any], new_status: str) -> str | None:
    """对齐 main.rs gate_plan_for_done_patch: 计划级(无 parent)+带 plan_id+非 done→done 类。"""
    if not is_done_status(new_status):
        return None
    if entry.get("parent_task_id"):
        return None
    if is_done_status(entry.get("status")):
        return None
    pid = (entry.get("plan_id") or "").strip()
    return pid or None


def _run_gate(plan_id: str) -> None:
    """调完成硬闸判定；拒绝 → GateRefused；判定本身异常 → fail-open 放行（对齐 Rust 语义）。"""
    try:
        from omnicompany.packages.services._governance.plan_completion_gate import evaluate

        result = evaluate(plan_id)
    except Exception:  # noqa: BLE001 — fail-open: 闸管纪律不管安全, 基建故障不许砸任务管理
        return
    if not result.get("allow"):
        raise GateRefused(plan_id, str(result.get("reason") or "完成硬闸拒绝"))


def patch_task(task_id: str, _gate: bool = True, **fields: Any) -> dict[str, Any]:
    """局部 patch 一条 task（对齐 whatnow POST /api/task/patch 语义 + 完成硬闸等效链）。

    可写字段：status/completion/latest_progress/goal_id(→milestone_id)/line/assignee/
    title/plan_id/due_date/description/priority/dependencies/archived/channel。
    一等字段与 legacy 同步更新；updated_at 自动刷新(ms)。返回更新后的条目。
    GateRefused 时不落盘任何改动。
    """
    with _write_lock:
        found = find_task(task_id)
        if not found:
            raise KeyError(f"task 不存在: {task_id}")
        gid, payload, entry = found
        legacy = entry.setdefault("legacy", {})

        new_status = fields.get("status")
        if _gate and new_status is not None:
            plan_id = _gate_plan_for_done(entry, str(new_status))
            if plan_id:
                _run_gate(plan_id)

        for k, v in fields.items():
            if k == "goal_id":
                entry["milestone_id"] = v
                legacy["goal_id"] = v
            elif k in _ENTRY_FIELDS:
                entry[k] = v
                if k in ("title", "status", "completion", "plan_id", "parent_task_id",
                         "dependencies", "priority", "due_date", "description", "archived"):
                    legacy[k] = v
            else:
                legacy[k] = v  # latest_progress/line/assignee/channel 等 whatnow 面字段
        entry["updated_at"] = _now_ms()
        legacy["updated_at"] = entry["updated_at"]
        save_group(gid, payload)
        return entry


def set_archived(task_id: str, archived: bool) -> dict[str, Any]:
    """对齐 whatnow POST /api/task/archive。"""
    return patch_task(task_id, archived=bool(archived))


def add_task(group_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """新建 task 条目（对齐 whatnow POST /api/tasks）。record 用 whatnow 字段面
    （id/title/status/completion/plan_id/goal_id/parent_task_id/line/channel/...）。"""
    with _write_lock:
        payload = load_group(group_id)
        if payload is None:
            raise KeyError(f"计划组不存在: {group_id}")
        tasks = payload.setdefault("tasks", [])
        tid = str(record.get("id") or "")
        if not tid:
            raise ValueError("add_task 需要 id")
        existing = next((t for t in tasks if t.get("id") == tid), None)
        now = _now_ms()
        legacy = dict(record)  # goal_id 等 whatnow 原面字段整体保留在 legacy
        legacy.setdefault("created_at", now)
        legacy["updated_at"] = now
        legacy.setdefault("archived", False)
        if existing is not None:
            # 幂等：同 id 已存在 = 更新（对齐 whatnow POST /api/tasks 的 upsert 语义）
            for k, v in record.items():
                if k == "goal_id":
                    existing["milestone_id"] = v
                elif k in _ENTRY_FIELDS:
                    existing[k] = v
            existing["legacy"].update(legacy)
            existing["updated_at"] = now
            existing["legacy"]["updated_at"] = now
            save_group(group_id, payload)
            return existing
        entry = {
            "id": tid,
            "title": record.get("title") or "",
            "status": record.get("status") or "pending",
            "completion": int(record.get("completion") or 0),
            "plan_id": record.get("plan_id") or None,
            "milestone_id": record.get("goal_id") or None,
            "parent_task_id": record.get("parent_task_id") or None,
            "dependencies": record.get("dependencies") or None,
            "priority": record.get("priority") or None,
            "due_date": record.get("due_date") or None,
            "description": record.get("description") or None,
            "archived": bool(record.get("archived") or False),
            "created_at": legacy["created_at"],
            "updated_at": now,
            "legacy": legacy,
        }
        tasks.append(entry)
        save_group(group_id, payload)
        return entry


def append_progress(subject_kind: str, subject_id: str, text: str, source: str = "omni") -> dict[str, Any]:
    """追加一条进度（对齐 whatnow POST /api/progress），并同步 latest_progress。"""
    with _write_lock:
        gid: str | None = None
        payload: dict[str, Any] | None = None
        if subject_kind == "task":
            found = find_task(subject_id)
            if found:
                gid, payload, entry = found
                entry["legacy"]["latest_progress"] = text
                entry["updated_at"] = _now_ms()
                entry["legacy"]["updated_at"] = entry["updated_at"]
        else:
            for g, p in iter_groups():
                if any(m.get("id") == subject_id for m in p.get("milestones") or []):
                    gid, payload = g, p
                    break
        if gid is None or payload is None:
            raise KeyError(f"progress 主体不存在: {subject_kind}/{subject_id}")
        prog = payload.setdefault("progress", [])
        next_id = max([int(p.get("id") or 0) for p in prog] + [0]) + 1
        rec = {"id": next_id, "subject_kind": subject_kind, "subject_id": subject_id,
               "ts": _now_ms(), "text": text, "source": source}
        prog.append(rec)
        save_group(gid, payload)
        return rec


def group_of_milestone(milestone_id: str) -> str | None:
    for gid, payload in iter_groups():
        if any(m.get("id") == milestone_id for m in payload.get("milestones") or []):
            return gid
    return None


def ensure_milestone(group_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """里程碑幂等自建（对齐 whatnow POST /api/goals；plans-sync 的 uncat-plans 兜底用）。"""
    with _write_lock:
        payload = load_group(group_id)
        if payload is None:
            raise KeyError(f"计划组不存在: {group_id}")
        milestones = payload.setdefault("milestones", [])
        mid = str(record.get("id") or "")
        existing = next((m for m in milestones if m.get("id") == mid), None)
        if existing is not None:
            return existing
        entry = {
            "id": mid,
            "group_id": group_id,
            "title": record.get("title") or mid,
            "kind": record.get("kind"),
            "line": record.get("line"),
            "status": record.get("status"),
            "objective": record.get("objective"),
            "detail": record.get("detail"),
            "plan_id": record.get("plan_id") or None,
            "ord": record.get("ord", 99),
            "legacy": dict(record),
        }
        milestones.append(entry)
        save_group(group_id, payload)
        return entry


def set_pin(subject_kind: str, subject_id: str, pinned: bool, note: str = "") -> dict[str, Any]:
    """置顶/取消置顶（对齐 whatnow POST /api/pin）。"""
    with _write_lock:
        # 先定位主体所在组（找不到时 goal 退到 omnicompany 组，task 必须存在）
        gid: str | None = None
        payload: dict[str, Any] | None = None
        for g, p in iter_groups():
            hit = (any(m.get("id") == subject_id for m in p.get("milestones") or [])
                   if subject_kind == "goal"
                   else any(t.get("id") == subject_id for t in p.get("tasks") or []))
            if hit:
                gid, payload = g, p
                break
        if gid is None:
            raise KeyError(f"pin 主体不存在: {subject_kind}/{subject_id}")
        pins = payload.setdefault("pins", [])
        pins[:] = [x for x in pins
                   if not (x.get("subject_kind") == subject_kind and x.get("subject_id") == subject_id)]
        if pinned:
            pins.append({"subject_kind": subject_kind, "subject_id": subject_id,
                         "note": note, "set_at": _now_ms()})
        save_group(gid, payload)
        return {"ok": True, "pinned": pinned}


# ───────────────────────── whatnow /api/board 兼容视图 ─────────────────────────


def _merged(entry: dict[str, Any]) -> dict[str, Any]:
    """legacy(whatnow 原面) + 一等字段（非 None 覆盖）→ 展平记录。"""
    merged = dict(entry.get("legacy") or {})
    for k in _ENTRY_FIELDS:
        v = entry.get(k)
        if v is not None:
            merged[k] = v
    merged["goal_id"] = entry.get("milestone_id")
    return merged


def _bug_grade(title: str, status: str) -> int:
    """与 main.rs bug_grade 同口径：必现/P0/high=3，偶现/medium=2，其它=1。"""
    t = (title or "").lower()
    if "必现" in title or "p0" in t or "high" in t:
        return 3
    if "偶现" in title or "medium" in t:
        return 2
    return 1


def board(include_archived: bool = False) -> dict[str, Any]:
    """拼 whatnow GET /api/board 同形同口径视图（对齐 main.rs board()）。

    口径逐条对齐 Rust：
      - goal 任务: goal_id 匹配 + 无 parent + (未归档 | include_archived | 已置顶)，按 updated_at 倒序；
      - subtasks: 全局按 parent_task_id 扫（子任务可能与父不同组），归档子任务永不返回；
      - loose_tasks: 无 goal + 无 parent + 未归档 + 非 done 类 + source_active!=false
        （include_archived 不影响），按 (无排期在后, due 升序, bug 分级降序) 排序；
      - clusters 按 ord、goals 按 (非 main 在后, ord) 排序；
      - counts.tasks 只计未归档；附 focus 兼容数组；updated_at=调用时刻。
    status 保持原始值（前端 isDone 等判定不受影响）；归一化只在 TaskStore 层做。
    """
    groups = iter_groups()
    all_entries: list[tuple[str, dict[str, Any]]] = []  # (group_id, entry)
    prog_index: dict[str, list[dict[str, Any]]] = {}
    for gid, payload in groups:
        for entry in payload.get("tasks") or []:
            all_entries.append((gid, entry))
        for p in payload.get("progress") or []:
            prog_index.setdefault(str(p.get("subject_id")), []).append(p)
    for lst in prog_index.values():
        lst.sort(key=lambda x: x.get("ts") or 0, reverse=True)

    children_index: dict[str, list[dict[str, Any]]] = {}
    for _gid, entry in all_entries:
        par = entry.get("parent_task_id")
        if par:
            children_index.setdefault(str(par), []).append(entry)

    pinned_tasks = {str(pin.get("subject_id")) for _gid, payload in groups
                    for pin in (payload.get("pins") or [])
                    if pin.get("subject_kind") == "task"}

    def prog_of(sid: str) -> list[dict[str, Any]]:
        return [{"ts": p.get("ts"), "text": p.get("text"), "source": p.get("source")}
                for p in prog_index.get(sid, [])]

    def task_node(entry: dict[str, Any], with_subs: bool) -> dict[str, Any]:
        merged = _merged(entry)
        node = {
            "id": merged.get("id"),
            "title": merged.get("title") or "",
            "status": merged.get("status") or "pending",
            "completion": int(merged.get("completion") or 0),
            "line": merged.get("line") or "side",
            "channel": merged.get("channel") or "local",
            "external_refs": merged.get("external_refs") or [],
            "assignee": merged.get("assignee"),
            "source_owner": merged.get("source_owner"),
            "source_project": merged.get("source_project"),
            "source_active": merged.get("source_active"),
            "routed_skills": merged.get("routed_skills") or [],
            "dispatch_status": merged.get("dispatch_status"),
            "team_id": merged.get("team_id"),
            "position_id": merged.get("position_id"),
            "due_date": merged.get("due_date"),
            "plan_id": merged.get("plan_id"),
            "latest_progress": merged.get("latest_progress"),
            "updated_at": merged.get("updated_at"),
            "archived": bool(merged.get("archived")),
            "progress": prog_of(str(entry.get("id"))),
        }
        # Rust: subtasks 恒排除归档; 子节点 JSON 不带 subtasks/updated_at/archived/line(单层)
        node["subtasks"] = ([
            {k: v for k, v in task_node(c, False).items()
             if k not in ("subtasks", "updated_at", "archived", "line")}
            for c in children_index.get(str(entry.get("id")), [])
            if not c.get("archived")
        ] if with_subs else [])
        return node

    clusters: list[dict[str, Any]] = []
    total_goals = 0
    for gid, payload in groups:
        if gid == "_inbox":
            continue  # 收件箱走 loose_tasks
        meta = _load_group_meta(gid)
        goals: list[dict[str, Any]] = []
        for m in payload.get("milestones") or []:
            mid = str(m.get("id"))
            mtasks = [e for e in (payload.get("tasks") or [])
                      if e.get("milestone_id") == mid and not e.get("parent_task_id")]
            shown = [e for e in mtasks
                     if not e.get("archived") or include_archived or str(e.get("id")) in pinned_tasks]
            shown.sort(key=lambda e: -(int(e.get("updated_at") or 0)))
            total_goals += 1
            goals.append({
                "id": mid,
                "title": m.get("title") or mid,
                "kind": m.get("kind") or "",
                "line": m.get("line") or "side",
                "status": m.get("status") or "",
                "objective": m.get("objective") or "",
                "detail": m.get("detail") or "",
                "source": (m.get("legacy") or {}).get("source") or "",
                "cluster_id": gid,
                "plan_id": m.get("plan_id") or None,
                "archived_count": sum(1 for e in mtasks if e.get("archived")),
                "tasks": [task_node(e, True) for e in shown],
                "progress": prog_of(mid),
                "_ord": m.get("ord", 0),
            })
        goals.sort(key=lambda g: (g["line"] != "main", g.pop("_ord")))
        clusters.append({
            "id": gid,
            "title": meta.get("title") or gid,
            "note": meta.get("description") or "",
            "goals": goals,
            "_ord": meta.get("ord", 0),
        })
    clusters.sort(key=lambda c: c.pop("_ord"))

    # loose_tasks = _inbox 组（whatnow 里 goal_id=null 的任务）
    loose_tasks: list[dict[str, Any]] = []
    inbox = load_group("_inbox")
    if inbox:
        for entry in inbox.get("tasks") or []:
            if entry.get("parent_task_id") or entry.get("archived"):
                continue
            merged = _merged(entry)
            if is_done_status(merged.get("status")) or merged.get("source_active") is False:
                continue
            loose_tasks.append(task_node(entry, True))
        loose_tasks.sort(key=lambda t: (
            bool(not t.get("due_date")), str(t.get("due_date") or ""),
            -_bug_grade(str(t["title"]), str(t["status"])),
        ))

    # pins 解析（对齐 Rust pin_json：goal 附 line/completion 均值/task_count/done_count）
    task_lookup = {str(e.get("id")): e for _gid, e in all_entries}
    goal_lookup: dict[str, tuple[dict[str, Any], str]] = {}
    for gid, payload in groups:
        for m in payload.get("milestones") or []:
            goal_lookup[str(m.get("id"))] = (m, gid)
    pins_out: list[dict[str, Any]] = []
    focus_out: list[dict[str, Any]] = []
    for _gid, payload in groups:
        for pin in payload.get("pins") or []:
            skind = str(pin.get("subject_kind") or "")
            sid = str(pin.get("subject_id") or "")
            base = {"subject_kind": skind, "subject_id": sid,
                    "note": pin.get("note") or "", "set_at": pin.get("set_at")}
            if skind == "goal" and sid in goal_lookup:
                m, mgid = goal_lookup[sid]
                gtasks = [e for e in task_lookup.values()
                          if e.get("milestone_id") == sid and not e.get("parent_task_id")
                          and not e.get("archived")]
                pct = (sum(int(e.get("completion") or 0) for e in gtasks) // len(gtasks)) if gtasks else 0
                pins_out.append({**base, "title": m.get("title"), "kind": m.get("kind"),
                                 "line": m.get("line"), "completion": pct,
                                 "task_count": len(gtasks),
                                 "done_count": sum(1 for e in gtasks if is_done_status(e.get("status"))),
                                 "missing": False})
            elif skind == "task" and sid in task_lookup:
                merged = _merged(task_lookup[sid])
                pins_out.append({**base, "title": merged.get("title"), "line": merged.get("line"),
                                 "channel": merged.get("channel"), "completion": merged.get("completion"),
                                 "status": merged.get("status"), "plan_id": merged.get("plan_id"),
                                 "external_refs": merged.get("external_refs") or [],
                                 "latest_progress": merged.get("latest_progress"),
                                 "archived": bool(merged.get("archived")), "missing": False})
            else:
                pins_out.append({**base, "title": None, "missing": True})
            if skind == "task":  # focus 兼容旧消费方
                entry = task_lookup.get(sid)
                merged = _merged(entry) if entry else {}
                focus_out.append({"task_id": sid, "note": pin.get("note") or "",
                                  "set_at": pin.get("set_at"),
                                  "title": merged.get("title"), "channel": merged.get("channel")})

    non_archived = sum(1 for _g, e in all_entries if not e.get("archived"))
    return {
        "clusters": clusters,
        "orphan_goals": [],
        "loose_tasks": loose_tasks,
        "pins": pins_out,
        "focus": focus_out,
        "counts": {"clusters": len(clusters), "goals": total_goals,
                   "tasks": non_archived, "archived": len(all_entries) - non_archived},
        "updated_at": _now_ms(),
    }


def _load_group_meta(group_id: str) -> dict[str, Any]:
    p = groups_root() / group_id / "group.yaml"
    if not p.is_file():
        return {}
    try:
        import yaml

        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


__all__ = [
    "STATUS_MAP",
    "normalize_status",
    "is_done_status",
    "GateRefused",
    "groups_root",
    "list_group_ids",
    "load_group",
    "iter_groups",
    "save_group",
    "find_task",
    "patch_task",
    "set_archived",
    "add_task",
    "append_progress",
    "set_pin",
    "group_of_milestone",
    "ensure_milestone",
    "board",
]
