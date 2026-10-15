# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-07-05T00:00:00Z type=infra status=active
# [OMNI] summary="task 一等对象 + TaskStore(progress-service 客户端): 统一待办/状态机/依赖图/next选取; 存储唯一真源=progressd(:8230), 执行task=计划task的子task"
# [OMNI] why="TASK-SSOT-UNIFICATION: 用户拍板任务必须唯一来源, data/lifecycle/tasks/ 第二存储废除, TaskStore 降级为 progressd 的客户端库"
# [OMNI] tags=lifecycle,task,state-machine,dependency,next,ssot,progress-service
# [OMNI] material_id="material:services._core.lifecycle.task.py"
"""task 一等对象 + progress-service 客户端存储。

数据模型抄 claude-task-master(全行业最成熟): id/plan_id/title/description/details/
dependencies/status/priority/test_strategy/subtasks/assignee + 复杂度/并行标记。
状态机: pending → in_progress → review → done; 另有 blocked/deferred/cancelled。
next 选取: 在 plan 内挑"状态 pending 且依赖全 done"的, 按 优先级→依赖数→id 排序选首个。

存储(TASK-SSOT-UNIFICATION 2026-07-05): 唯一真源 = progress-service(:8230, whatnow.json)。
存储(M3b 2026-08-13): 真源由 OMNI_TASK_SSOT 开关——whatnow(:8230, 默认) / plan-groups
(data/services/plan-groups/ 纯文件, 读写过 plan_groups_store, 状态读出归一化到七态)。
执行 task 以"计划级 task 的子 task"形态存进真源:
  parent = 该 plan 的计划级 task(确定性 id `p_<sanitized plan_id>`, 与 plan_progress_recorder 同约定,
  不存在时幂等自建、goal 落 uncat-plans 待归类); 子 task 服务端 id = `<parent_id>.<局部序号>`。
本模块保留原 API 面与全部确定性语义(状态机/依赖/next/环检测), 只换了持久化层。

测试隔离: `TaskStore(root=<tmp_path>)` 显式传 root 时用本地文件后备(仅测试用);
生产代码一律默认构造 `TaskStore()` 走 HTTP。严禁在生产路径传 root 造出第二套任务存储。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

VALID_STATUS = {"pending", "in_progress", "review", "done", "blocked", "deferred", "cancelled"}
VALID_PRIORITY = {"high", "medium", "low"}
DONE_STATUS = {"done", "cancelled"}

# ── M3b 真源开关 ──
# OMNI_TASK_SSOT=whatnow|plan-groups；默认 plan-groups（M3b 已验证切真源，
# env 设回 whatnow 可瞬时回滚到 :8230）。plan-groups = data/services/plan-groups/
# 纯文件模型（M3a 迁移产物）。whatnow.json 自此为冻结旧档：新写不再回流它。
SSOT_ENV = "OMNI_TASK_SSOT"
DEFAULT_SSOT = "plan-groups"


def current_ssot() -> str:
    v = (os.environ.get(SSOT_ENV) or "").strip().lower()
    return v if v in ("whatnow", "plan-groups") else DEFAULT_SSOT


def ssot_is_plan_groups() -> bool:
    return current_ssot() == "plan-groups"

# 合法状态迁移 (None=任意起点; 防乱跳)
_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress", "blocked", "deferred", "cancelled"},
    "in_progress": {"review", "done", "blocked", "deferred", "cancelled", "pending"},
    "review": {"done", "in_progress", "blocked", "cancelled"},
    "blocked": {"pending", "in_progress", "cancelled", "deferred"},
    "deferred": {"pending", "in_progress", "cancelled"},
    "done": {"in_progress"},  # 返工
    "cancelled": {"pending"},  # 复活
}

_SERVICE_URL = os.environ.get("PROGRESS_SERVICE_URL") or os.environ.get("WHATNOW_URL") or "http://127.0.0.1:8230"

# 子 task 在任务窗口进度环上的粗略完成度(纯展示, 真状态以 status 为准)
_STATUS_COMPLETION = {"done": 100, "review": 80, "in_progress": 40}


class TaskServiceUnavailable(RuntimeError):
    """progress-service(:8230) 连不上。任务唯一真源在它那里, 不降级到本地文件。"""

    def __init__(self, cause: str = "") -> None:
        super().__init__(
            "progress-service(:8230) 未运行, 无法读写任务(任务唯一真源在它那里)。\n"
            "启动: C:/workspace/omnicompany\\services\\_progress\\progress_service\\start-progress-service.cmd\n"
            "或:   venv\\Scripts\\python.exe services\\_progress\\progress_service\\ensure_progress_service_running.py"
            + (f"\n底层错误: {cause}" if cause else "")
        )


def _plan_task_id(plan_id: str) -> str:
    """计划级 task 的确定性 id(与 plan_progress_recorder 的 p_<sanitized> 约定一致, 幂等不重复建)。"""
    return "p_" + re.sub(r"[^A-Za-z0-9]+", "_", plan_id).strip("_")


def _plan_short_title(plan_id: str) -> str:
    """从 plan_id(docs/plans 相对路径)取个短标题: 末段去掉 [日期] 前缀。"""
    seg = plan_id.rstrip("/").split("/")[-1]
    return re.sub(r"^\[\d{4}-\d{2}-\d{2}\]", "", seg) or plan_id


def canonical_task_id(plan_id: str | None, task_ref: str | None) -> str | None:
    """把 task 引用规范成 progress-service 线上 id(`p_<plan>.<n>`)。

    前端 QuestBoard 用的就是这个线上 id,会话自绑定 / dispatch 镜像都要对齐到它,
    by_task 才能分组对齐。见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.6)。
      - 已是 `p_..._.<n>` 线上子 id → 原样
      - 纯本地号 `<n>`(+ 已知 plan)→ `p_<sanitized_plan>.<n>`
      - 无法规范 → 原样
    """
    if not task_ref:
        return task_ref
    s = str(task_ref).strip()
    if s.startswith("p_") and "." in s:
        return s  # 已是线上子 id
    if plan_id:
        local = s.rsplit(".", 1)[-1]  # 容忍 "<plan>.n" / "n" 各种写法, 取末段本地号
        if local:
            return f"{_plan_task_id(plan_id)}.{local}"
    return s


def local_task_id(plan_id: str, task_ref: str) -> str:
    """把任务引用解析为本地号，并拒绝“完整任务号属于另一个计划”的情况。"""
    normalized_plan_id = str(plan_id or "").strip()
    value = str(task_ref or "").strip()
    if not normalized_plan_id or not value:
        raise ValueError("plan_id 和 task_id 不能为空")
    if value.startswith("p_") and "." in value:
        expected_prefix = _plan_task_id(normalized_plan_id) + "."
        if not value.startswith(expected_prefix):
            raise ValueError("完整任务号不属于指定计划")
        value = value[len(expected_prefix):]
    if not value:
        raise ValueError("task_id 不能为空")
    return value


@dataclass
class Task:
    id: str
    plan_id: str
    title: str
    description: str = ""
    details: str = ""              # 自包含执行细节 (BMAD story 理念)
    test_strategy: str = ""        # 怎么验证它做完了
    dependencies: list[str] = field(default_factory=list)
    subtasks: list[str] = field(default_factory=list)
    status: str = "pending"
    priority: str = "medium"
    complexity: int | None = None  # 1-10 (task-master 复杂度评分; 保留向后兼容)
    parallel: bool = False         # spec-kit [P] 并行标记
    reasoning: str = ""            # splitter 给出的拆分理由
    # 「动手前先写好」(用户 2026-06-27): 做什么/在哪做/产出什么/工作量/难度 —— 计划阶段就估清
    file_scope: list[str] = field(default_factory=list)   # 这件事会碰哪些文件/目录(也用于判并行: 范围不重叠才能同时跑)
    expected_outputs: list[str] = field(default_factory=list)  # 预计产出(文件/命令/物料)
    workload: int | None = None    # 工作量估分 1-10(规模/体量)
    difficulty: int | None = None  # 难度估分 1-10(不确定性/技术难度)
    assignee: str | None = None    # agent key / 身份
    team_id: str | None = None     # 此任务路由到的 canonical TeamSpec.id
    position_id: str | None = None # 此任务在 TeamSpec.positions 中的目标岗位
    notes: list[dict[str, Any]] = field(default_factory=list)  # 边做边记的进度(抄 task-master update_subtask)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# ───────────────────────── 持久化后备 ─────────────────────────


class _HttpBackend:
    """生产后备: progress-service(:8230) 是任务唯一真源。

    映射: 一个 plan → 一条计划级 task(parent, id=p_<sanitized>) + N 条子 task(id=<parent>.<局部序号>)。
    Python 侧 Task.id 保持局部序号("1"/"2"/...), 存取时在这里做前缀映射与 秒↔毫秒 换算。
    删除语义: progressd 无删除, replace() 把不在新集合里的旧子 task 置 archived(读取时排除)。
    """

    def __init__(self, base: str = "") -> None:
        self.base = (base or _SERVICE_URL).rstrip("/")

    # ── HTTP 基础 ──
    def _req(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.base + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"progress-service {method} {path} → HTTP {e.code}: "
                               f"{e.read().decode('utf-8', 'replace')[:300]}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            raise TaskServiceUnavailable(str(e)) from e

    def _get_plan(self, plan_id: str) -> dict | None:
        """该 plan 的 {parent, tasks}(tasks 含 archived), 无计划级 task 时 None。"""
        q = urllib.parse.quote(plan_id, safe="")
        resp = self._req("GET", f"/api/plan-tasks?plan_id={q}")
        plans = resp.get("plans") or []
        return plans[0] if plans else None

    # ── 映射 ──
    def _from_server(self, d: dict, plan_id: str, parent_id: str) -> Task:
        sid = str(d.get("id") or "")
        local = sid[len(parent_id) + 1:] if sid.startswith(parent_id + ".") else sid
        t = Task.from_dict({**d, "id": local, "plan_id": d.get("plan_id") or plan_id})
        t.created_at = float(d.get("created_at") or 0) / 1000.0
        t.updated_at = float(d.get("updated_at") or 0) / 1000.0
        if not t.status:
            t.status = "pending"
        if t.priority not in VALID_PRIORITY:
            t.priority = "medium"
        return t

    def _to_server(self, t: Task, parent_id: str) -> dict:
        d = t.to_dict()
        d.pop("subtasks", None)  # 局部概念, 服务端层级用 parent_task_id 表达
        d["id"] = f"{parent_id}.{t.id}"
        d["parent_task_id"] = parent_id
        d["plan_id"] = t.plan_id
        d["channel"] = "local"
        d["completion"] = _STATUS_COMPLETION.get(t.status, 0)
        d["created_at"] = int((t.created_at or time.time()) * 1000)
        d["updated_at"] = int((t.updated_at or time.time()) * 1000)
        return d

    def _ensure_parent(self, plan_id: str) -> str:
        """计划级 task 不存在时幂等自建(错误样本②: 拆分先于 plans-sync 纳管)。返回 parent id。"""
        plan = self._get_plan(plan_id)
        if plan:
            return str(plan["parent"]["id"])
        pid = _plan_task_id(plan_id)
        now = int(time.time() * 1000)
        self._req("POST", "/api/tasks", {
            "id": pid, "plan_id": plan_id, "title": _plan_short_title(plan_id),
            "status": "in_progress", "channel": "local", "goal_id": "uncat-plans",
            "created_at": now, "updated_at": now,
        })
        return pid

    # ── 后备接口 ──
    def load(self, plan_id: str) -> list[Task]:
        plan = self._get_plan(plan_id)
        if not plan:
            return []
        parent_id = str(plan["parent"]["id"])
        tasks = [self._from_server(d, plan_id, parent_id)
                 for d in plan.get("tasks", []) if not d.get("archived")]
        tasks.sort(key=lambda t: (0, int(t.id)) if t.id.isdigit() else (1, 0))
        return tasks

    def load_all(self) -> list[Task]:
        resp = self._req("GET", "/api/plan-tasks")
        out: list[Task] = []
        for plan in resp.get("plans") or []:
            pid = str(plan.get("plan_id") or "")
            parent_id = str((plan.get("parent") or {}).get("id") or "")
            out.extend(self._from_server(d, pid, parent_id)
                       for d in plan.get("tasks", []) if not d.get("archived"))
        return out

    def upsert(self, plan_id: str, task: Task) -> None:
        parent_id = self._ensure_parent(plan_id)
        self._req("POST", "/api/tasks", self._to_server(task, parent_id))

    def replace(self, plan_id: str, tasks: list[Task]) -> None:
        parent_id = self._ensure_parent(plan_id)
        keep = {f"{parent_id}.{t.id}" for t in tasks}
        plan = self._get_plan(plan_id) or {}
        for d in plan.get("tasks", []):
            sid = str(d.get("id") or "")
            if sid and sid not in keep and not d.get("archived"):
                self._req("POST", "/api/task/archive", {"id": sid, "archived": True})
        for t in tasks:
            self._req("POST", "/api/tasks", self._to_server(t, parent_id))

    def claim_for_position(
        self,
        plan_id: str,
        task_id: str,
        *,
        assignee: str,
        team_id: str,
        position_id: str,
    ) -> dict[str, Any]:
        """通过 progress-service 的原子端点认领并记录目标岗位。"""
        plan = self._get_plan(plan_id)
        if not plan:
            return {"ok": False, "status": "not_found", "error": "plan_not_found"}
        parent_id = str(plan["parent"]["id"])
        server_task_id = (
            task_id
            if task_id.startswith(parent_id + ".")
            else f"{parent_id}.{task_id}"
        )
        payload = {
            "id": server_task_id,
            "assignee": assignee,
            "team_id": team_id,
            "position_id": position_id,
        }
        url = self.base + "/api/task/claim"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code in {400, 404, 409}:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = {"ok": False, "error": body or f"HTTP {exc.code}"}
                parsed.setdefault("status", "conflict" if exc.code == 409 else "rejected")
                return parsed
            raise RuntimeError(
                f"progress-service POST /api/task/claim → HTTP {exc.code}: {body[:300]}"
            ) from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise TaskServiceUnavailable(str(exc)) from exc


class _PlanGroupsBackend:
    """plan-groups 文件真源后备（M3b）：与 _HttpBackend 同接口，读写
    data/services/plan-groups/*/tasks.json（经 plan_groups_store 层，原子写）。

    与 HTTP 后备的映射差异：
      - 计划级 task 按 plan_id + 无 parent_task_id 定位（优先 id==p_<sanitized> 约定）；
      - 读出的 status 过 normalize_status 归一化到七态（原文永留 legacy.status）；
      - 新建计划级 task 落 milestone `uncat-plans` 所在组（与 whatnow goal_id=uncat-plans 同口径）；
      - 删除语义同 HTTP 后备：replace() 把不在新集合里的旧子 task 置 archived。
    """

    def __init__(self) -> None:
        from . import plan_groups_store as pgs

        self._pgs = pgs
        self._lock = threading.Lock()

    # ── 计划级 task 定位 ──
    def _find_parent(self, plan_id: str) -> tuple[str, dict, dict] | None:
        want = _plan_task_id(plan_id)
        fallback = None
        for gid, payload in self._pgs.iter_groups():
            for e in payload.get("tasks") or []:
                if (e.get("plan_id") or "") != plan_id or e.get("parent_task_id"):
                    continue
                if e.get("id") == want:
                    return (gid, payload, e)
                if fallback is None:
                    fallback = (gid, payload, e)
        return fallback

    def _ensure_parent(self, plan_id: str) -> tuple[str, dict, str]:
        """返回 (group_id, payload, parent_id)；不存在时幂等自建（对齐 HTTP 后备的 uncat-plans 口径）。"""
        found = self._find_parent(plan_id)
        if found:
            gid, payload, parent = found
            return (gid, payload, str(parent["id"]))
        pid = _plan_task_id(plan_id)
        # uncat-plans milestone 所在组（whatnow 时代 goal_id=uncat-plans 的等价落点）
        gid = "omnicompany"
        for g, payload in self._pgs.iter_groups():
            if any(m.get("id") == "uncat-plans" for m in payload.get("milestones") or []):
                gid = g
                break
        now = int(time.time() * 1000)
        self._pgs.add_task(gid, {
            "id": pid, "plan_id": plan_id, "title": _plan_short_title(plan_id),
            "status": "in_progress", "channel": "local", "goal_id": "uncat-plans",
            "created_at": now, "updated_at": now,
        })
        payload = self._pgs.load_group(gid) or {}
        return (gid, payload, pid)

    # ── 映射 ──
    def _from_entry(self, entry: dict, plan_id: str, parent_id: str) -> Task:
        merged = dict(entry.get("legacy") or {})
        for k in ("id", "title", "status", "completion", "plan_id", "parent_task_id",
                  "dependencies", "priority", "due_date", "description", "archived",
                  "created_at", "updated_at"):
            v = entry.get(k)
            if v is not None:
                merged[k] = v
        sid = str(merged.get("id") or "")
        local = sid[len(parent_id) + 1:] if sid.startswith(parent_id + ".") else sid
        merged["id"] = local
        merged["plan_id"] = merged.get("plan_id") or plan_id
        merged["status"] = self._pgs.normalize_status(merged.get("status"))
        t = Task.from_dict(merged)
        t.created_at = float(merged.get("created_at") or 0) / 1000.0
        t.updated_at = float(merged.get("updated_at") or 0) / 1000.0
        if t.priority not in VALID_PRIORITY:
            t.priority = "medium"
        return t

    def _write_child(self, gid: str, payload: dict, parent_id: str, t: Task) -> None:
        server_id = f"{parent_id}.{t.id}"
        d = t.to_dict()
        d.pop("subtasks", None)  # 局部概念, 层级用 parent_task_id 表达
        now_ms = int(time.time() * 1000)
        tasks = payload.setdefault("tasks", [])
        existing = next((e for e in tasks if e.get("id") == server_id), None)
        legacy_base = dict(existing.get("legacy") or {}) if existing else {}
        legacy = {
            **legacy_base, **d,
            "id": server_id,
            "parent_task_id": parent_id,
            "plan_id": t.plan_id,
            "channel": legacy_base.get("channel") or "local",
            "completion": _STATUS_COMPLETION.get(t.status, 0),
            "created_at": int((t.created_at or time.time()) * 1000),
            "updated_at": now_ms,
            "archived": False,
        }
        entry = existing if existing is not None else {}
        entry.update({
            "id": server_id,
            "title": t.title,
            "status": t.status,
            "completion": legacy["completion"],
            "plan_id": t.plan_id,
            "milestone_id": entry.get("milestone_id"),
            "parent_task_id": parent_id,
            "dependencies": list(t.dependencies or []) or None,
            "priority": t.priority,
            "due_date": legacy_base.get("due_date"),
            "description": t.description or None,
            "archived": False,
            "created_at": legacy["created_at"],
            "updated_at": now_ms,
            "legacy": legacy,
        })
        if existing is None:
            tasks.append(entry)
        self._pgs.save_group(gid, payload)

    # ── 后备接口 ──
    def _children_across_groups(self, parent_id: str) -> list[tuple[str, dict, dict]]:
        """子 task 可能因 goal_id=null 落在与父不同的组（_inbox）——必须跨组全局找。"""
        out = []
        for gid, payload in self._pgs.iter_groups():
            for e in payload.get("tasks") or []:
                if e.get("parent_task_id") == parent_id:
                    out.append((gid, payload, e))
        return out

    def load(self, plan_id: str) -> list[Task]:
        found = self._find_parent(plan_id)
        if not found:
            return []
        _gid, _payload, parent = found
        parent_id = str(parent["id"])
        tasks = [self._from_entry(e, plan_id, parent_id)
                 for _g, _p, e in self._children_across_groups(parent_id)
                 if not e.get("archived")]
        tasks.sort(key=lambda t: (0, int(t.id)) if t.id.isdigit() else (1, 0))
        return tasks

    def load_all(self) -> list[Task]:
        out: list[Task] = []
        groups = self._pgs.iter_groups()
        # 先建全局父表(parent_id → plan_id), 再跨组挂子任务
        parents: dict[str, str] = {}
        for _gid, payload in groups:
            for e in payload.get("tasks") or []:
                if not e.get("parent_task_id") and (e.get("plan_id") or ""):
                    parents[str(e["id"])] = str(e.get("plan_id"))
        for _gid, payload in groups:
            for c in payload.get("tasks") or []:
                pid_ref = c.get("parent_task_id")
                if not pid_ref or pid_ref not in parents or c.get("archived"):
                    continue
                out.append(self._from_entry(c, parents[pid_ref], str(pid_ref)))
        return out

    def upsert(self, plan_id: str, task: Task) -> None:
        with self._lock:
            gid, payload, parent_id = self._ensure_parent(plan_id)
            self._write_child(gid, payload, parent_id, task)

    def replace(self, plan_id: str, tasks: list[Task]) -> None:
        with self._lock:
            gid, payload, parent_id = self._ensure_parent(plan_id)
            keep = {f"{parent_id}.{t.id}" for t in tasks}
            now_ms = int(time.time() * 1000)
            # 归档跨组: 旧子 task 可能落在 _inbox 等其它组
            touched: dict[str, dict] = {}
            for cgid, cpayload, e in self._children_across_groups(parent_id):
                sid = str(e.get("id") or "")
                if sid and sid not in keep and not e.get("archived"):
                    e["archived"] = True
                    e["updated_at"] = now_ms
                    leg = e.setdefault("legacy", {})
                    leg["archived"] = True
                    leg["updated_at"] = now_ms
                    touched[cgid] = cpayload
            for cgid, cpayload in touched.items():
                if cgid != gid:
                    self._pgs.save_group(cgid, cpayload)
            for t in tasks:
                self._write_child(gid, payload, parent_id, t)

    def claim_for_position(
        self,
        plan_id: str,
        task_id: str,
        *,
        assignee: str,
        team_id: str,
        position_id: str,
    ) -> dict[str, Any]:
        """文件真源下的原子认领（进程内锁 + 原子落盘；语义与 _FileBackend 一致）。"""
        with self._lock:
            found = self._find_parent(plan_id)
            if not found:
                return {"ok": False, "status": "not_found", "error": "plan_not_found"}
            gid, payload, parent = found
            parent_id = str(parent["id"])
            server_id = task_id if task_id.startswith(parent_id + ".") else f"{parent_id}.{task_id}"
            # 子 task 跨组查找（可能在 _inbox）
            entry = None
            for cgid, cpayload, e in self._children_across_groups(parent_id):
                if e.get("id") == server_id and not e.get("archived"):
                    gid, payload, entry = cgid, cpayload, e
                    break
            if entry is None:
                return {"ok": False, "status": "not_found", "error": "task_not_found"}
            legacy = entry.setdefault("legacy", {})

            exact_retry = (
                legacy.get("assignee") == assignee
                and legacy.get("team_id") == team_id
                and legacy.get("position_id") == position_id
            )
            if exact_retry:
                return {"ok": True, "status": "already_claimed", "changed": False}
            if self._pgs.normalize_status(entry.get("status")) in DONE_STATUS:
                return {"ok": False, "status": "conflict", "error": "task_closed"}
            if legacy.get("assignee") not in {None, assignee}:
                return {"ok": False, "status": "conflict", "error": "assignee_conflict",
                        "current_assignee": legacy.get("assignee")}
            if legacy.get("team_id") not in {None, team_id}:
                return {"ok": False, "status": "conflict", "error": "team_conflict",
                        "current_team_id": legacy.get("team_id")}
            if legacy.get("position_id") not in {None, position_id}:
                return {"ok": False, "status": "conflict", "error": "position_conflict",
                        "current_position_id": legacy.get("position_id")}

            legacy["assignee"] = assignee
            legacy["team_id"] = team_id
            legacy["position_id"] = position_id
            entry["updated_at"] = int(time.time() * 1000)
            legacy["updated_at"] = entry["updated_at"]
            self._pgs.save_group(gid, payload)
            return {"ok": True, "status": "claimed", "changed": True}


class _FileBackend:
    """测试隔离后备(仅显式传 root 时启用): 一个 plan 一个 json, 与旧盘上格式一致。

    生产严禁使用 —— 任务唯一真源是 progress-service, 本后备只为确定性单测隔离而留。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._claim_lock = threading.Lock()

    def _path(self, plan_id: str) -> Path:
        return self.root / (re.sub(r"[^A-Za-z0-9._-]", "_", plan_id) + ".json")

    def load(self, plan_id: str) -> list[Task]:
        p = self._path(plan_id)
        if not p.is_file():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [Task.from_dict(t) for t in raw.get("tasks", [])]

    def load_all(self) -> list[Task]:
        out: list[Task] = []
        for f in self.root.glob("*.json"):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.extend(Task.from_dict(t) for t in raw.get("tasks", []))
        return out

    def upsert(self, plan_id: str, task: Task) -> None:
        tasks = [t for t in self.load(plan_id) if t.id != task.id]
        tasks.append(task)
        self.replace(plan_id, tasks)

    def replace(self, plan_id: str, tasks: list[Task]) -> None:
        p = self._path(plan_id)
        tmp = p.with_suffix(".json.tmp")
        payload = {"plan_id": plan_id, "tasks": [t.to_dict() for t in tasks]}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def claim_for_position(
        self,
        plan_id: str,
        task_id: str,
        *,
        assignee: str,
        team_id: str,
        position_id: str,
    ) -> dict[str, Any]:
        """测试后备的进程内原子实现；生产原子性由 progress-service 提供。"""
        with self._claim_lock:
            tasks = self.load(plan_id)
            task = next((item for item in tasks if item.id == task_id), None)
            if task is None:
                return {"ok": False, "status": "not_found", "error": "task_not_found"}

            exact_retry = (
                task.assignee == assignee
                and task.team_id == team_id
                and task.position_id == position_id
            )
            if exact_retry:
                return {"ok": True, "status": "already_claimed", "changed": False}
            if task.status in DONE_STATUS:
                return {"ok": False, "status": "conflict", "error": "task_closed"}
            if task.assignee not in {None, assignee}:
                return {
                    "ok": False,
                    "status": "conflict",
                    "error": "assignee_conflict",
                    "current_assignee": task.assignee,
                }
            if task.team_id not in {None, team_id}:
                return {
                    "ok": False,
                    "status": "conflict",
                    "error": "team_conflict",
                    "current_team_id": task.team_id,
                }
            if task.position_id not in {None, position_id}:
                return {
                    "ok": False,
                    "status": "conflict",
                    "error": "position_conflict",
                    "current_position_id": task.position_id,
                }

            task.assignee = assignee
            task.team_id = team_id
            task.position_id = position_id
            task.updated_at = time.time()
            self.replace(plan_id, tasks)
            return {"ok": True, "status": "claimed", "changed": True}


# ───────────────────────── TaskStore ─────────────────────────


class TaskStore:
    """task 的统一存取(确定性语义在这层, 持久化在后备层)。

    默认构造 = progress-service HTTP 客户端(生产唯一形态);
    `root=` 仅供测试隔离(本地文件后备), 生产代码不得传。
    """

    def __init__(self, root: Path | None = None) -> None:
        if root is not None:
            self._backend = _FileBackend(root)  # 测试隔离
        elif ssot_is_plan_groups():
            self._backend = _PlanGroupsBackend()  # M3b 文件真源
        else:
            self._backend = _HttpBackend()

    def _load(self, plan_id: str) -> list[Task]:
        return self._backend.load(plan_id)

    def list_tasks(
        self,
        plan_id: str | None = None,
        *,
        team_id: str | None = None,
        position_id: str | None = None,
        assignee: str | None = None,
    ) -> list[Task]:
        """列出 canonical Task；岗位收件箱只是这里的筛选视图，不另建队列。"""
        tasks = self._load(plan_id) if plan_id else self._backend.load_all()
        if team_id is not None:
            tasks = [task for task in tasks if task.team_id == team_id]
        if position_id is not None:
            tasks = [task for task in tasks if task.position_id == position_id]
        if assignee is not None:
            tasks = [task for task in tasks if task.assignee == assignee]
        return tasks

    def get(self, task_id: str, plan_id: str | None = None) -> Task | None:
        for t in self.list_tasks(plan_id):
            if t.id == task_id:
                return t
        return None

    def _next_id(self, tasks: list[Task]) -> str:
        nums = [int(t.id) for t in tasks if t.id.isdigit()]
        return str((max(nums) + 1) if nums else 1)

    def add(self, plan_id: str, **fields: Any) -> Task:
        tasks = self._load(plan_id)
        tid = fields.pop("id", None) or self._next_id(tasks)
        now = time.time()
        task = Task(id=str(tid), plan_id=plan_id, created_at=now, updated_at=now,
                    title=fields.pop("title", "(无标题 task)"), **fields)
        if task.status not in VALID_STATUS:
            raise ValueError(f"非法 status: {task.status}")
        if task.priority not in VALID_PRIORITY:
            task.priority = "medium"
        self._backend.upsert(plan_id, task)
        return task

    def replace_all(self, plan_id: str, tasks: list[Task]) -> None:
        self._backend.replace(plan_id, tasks)

    def update(self, task_id: str, plan_id: str | None = None, **fields: Any) -> Task:
        t = self.get(task_id, plan_id)
        if not t:
            raise KeyError(f"task 不存在: {task_id}")
        if fields.get("status") == "done":
            from .managed_agent_completion import assert_task_completion_allowed
            assert_task_completion_allowed(task_id, t.plan_id)
        for k, v in fields.items():
            if hasattr(t, k):
                setattr(t, k, v)
        t.updated_at = time.time()
        self._backend.upsert(t.plan_id, t)
        return t

    def claim_for_position(
        self,
        task_id: str,
        *,
        plan_id: str,
        assignee: str,
        team_id: str,
        position_id: str,
    ) -> dict[str, Any]:
        """首次认领并路由到岗位；同值重试幂等，任何已有异值都拒绝。"""
        values = {
            "plan_id": plan_id,
            "task_id": task_id,
            "assignee": assignee,
            "team_id": team_id,
            "position_id": position_id,
        }
        empty = [name for name, value in values.items() if not str(value or "").strip()]
        if empty:
            raise ValueError(f"认领字段不能为空: {empty}")
        normalized_plan_id = str(plan_id).strip()
        try:
            normalized_task_id = local_task_id(normalized_plan_id, task_id)
        except ValueError:
            return {
                "ok": False,
                "status": "rejected",
                "error": "task_plan_mismatch",
                "task": None,
            }
        result = self._backend.claim_for_position(
            normalized_plan_id,
            normalized_task_id,
            assignee=str(assignee).strip(),
            team_id=str(team_id).strip(),
            position_id=str(position_id).strip(),
        )
        task = self.get(normalized_task_id, normalized_plan_id)
        return {
            **result,
            "task": task.to_dict() if task is not None else None,
        }

    def add_note(self, task_id: str, text: str, plan_id: str | None = None) -> Task:
        """给任务追加一条带时间戳的进度记录(抄 task-master update_subtask: 边做边记)。"""
        t = self.get(task_id, plan_id)
        if not t:
            raise KeyError(f"task 不存在: {task_id}")
        notes = list(t.notes)
        notes.append({"ts": time.time(), "text": text})
        return self.update(task_id, plan_id=t.plan_id, notes=notes)

    def set_status(self, task_id: str, status: str, plan_id: str | None = None) -> Task:
        if status not in VALID_STATUS:
            raise ValueError(f"非法 status: {status} (合法: {sorted(VALID_STATUS)})")
        t = self.get(task_id, plan_id)
        if not t:
            raise KeyError(f"task 不存在: {task_id}")
        if t.status != status:
            allowed = _TRANSITIONS.get(t.status, VALID_STATUS)
            if status not in allowed:
                raise ValueError(
                    f"非法状态迁移 {t.status} → {status} (允许: {sorted(allowed)})"
                )
        return self.update(task_id, plan_id=t.plan_id, status=status)

    def next_task(self, plan_id: str) -> Task | None:
        """挑下一个可做 task: 状态 pending 且依赖全 done, 按 优先级→依赖数→id。"""
        tasks = self._load(plan_id)
        done_ids = {t.id for t in tasks if t.status in DONE_STATUS}
        prio_rank = {"high": 0, "medium": 1, "low": 2}
        ready = [
            t for t in tasks
            if t.status == "pending" and all(d in done_ids for d in t.dependencies)
        ]
        if not ready:
            return None
        ready.sort(key=lambda t: (
            prio_rank.get(t.priority, 1),
            len(t.dependencies),
            int(t.id) if t.id.isdigit() else 1_000_000,
        ))
        return ready[0]

    def detect_dependency_cycle(self, plan_id: str) -> list[str]:
        """返回涉及循环依赖的 task id (空=无环)。"""
        tasks = {t.id: t for t in self._load(plan_id)}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in tasks}
        bad: set[str] = set()

        def dfs(u: str) -> None:
            color[u] = GRAY
            for v in tasks[u].dependencies:
                if v not in tasks:
                    continue
                if color.get(v) == GRAY:
                    bad.add(u)
                    bad.add(v)
                elif color.get(v) == WHITE:
                    dfs(v)
            color[u] = BLACK

        for tid in tasks:
            if color[tid] == WHITE:
                dfs(tid)
        return sorted(bad)


__all__ = [
    "Task",
    "TaskStore",
    "TaskServiceUnavailable",
    "VALID_STATUS",
    "VALID_PRIORITY",
    "DONE_STATUS",
    "canonical_task_id",
    "local_task_id",
    "SSOT_ENV",
    "DEFAULT_SSOT",
    "current_ssot",
    "ssot_is_plan_groups",
]
