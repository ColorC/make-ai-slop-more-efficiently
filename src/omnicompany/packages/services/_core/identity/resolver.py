# [OMNI] origin=ai-ide domain=services/_core/identity ts=2026-05-02T00:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="trace_id 解析跟 active session 写入, hook / CLI / web 共用一份"
# [OMNI] why="不能让 cli 跟 web 各算各的 trace_id 导致同一 claude session 看到两个身份"
# [OMNI] tags=identity,resolver,session,foundation
# [OMNI] material_id="material:core.identity.session_resolver.implementation.py"
"""身份解析: 当前 claude code session 的 trace_id 从哪来.

`resolve_active_trace_id()` 是单一查询入口. 优先级链:

  OMNI_CC_TRACE_ID env   (CLI 显式 / 测试)
  > OMNI_CC_PTY_ID env   (dashboard PTY 启动 claude 时传)
  > active_file 的 trace_id  (SessionStart hook 写的)
  > cc_unknown_<ts>      (fallback warn)

`record_active_session()` 是写入入口, hook + CLI 共用.

`current_session_meta()` 返回完整元数据 (供 omni who / dashboard / 调试).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# active session 元数据落盘位置, 跟 cc_wrapper hook 共用
_ACTIVE_FILE_REL = "data/cc_session_active.json"

CONFIRMED_BINDING_SOURCES = frozenset({
    "cli_bind",
    "cli_plan_use",
    "dispatch",
    "retrospective_explicit",
    "agent_control_plane",
    "dashboard_agent_worker_compat",
})


def _repo_root() -> Path:
    """omnicompany 仓库根 — 走 core.config 的唯一权威解析器。

    原实现复制了 hooks/_shared.repo_root() 的旧 cwd 走查算法, 理由是"避免反向
    import dashboard"。但权威解析器在 `omnicompany.core.config`, 不在 dashboard,
    引它不会产生那条反向依赖, 所以没有理由继续维护副本。

    旧算法从 cwd 向上找 `src/omnicompany` + `docs` 双命中, 会被工作区根的游离
    同名目录劫持。后果不止路径不对: 本模块解析的是 cc_session_active.json /
    cc_session_bindings.json 的位置, 而 hooks/_shared 早已切到权威解析器, 于是
    CLI 写一个根、hook 读另一个根 —— `omni plan use` 会落到错误文件上, 表现为
    把 active_plan 绑到别的会话头上。
    """
    try:
        from omnicompany.core.config import omni_workspace_root

        return omni_workspace_root()
    except Exception:
        return Path(__file__).resolve().parents[6]


def _active_file() -> Path:
    return _repo_root() / _ACTIVE_FILE_REL


def _read_active() -> dict[str, Any]:
    p = _active_file()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ---- 按会话 keyed 的绑定台账 (多会话并行) ----
# cc_session_active.json = "我现在是哪个会话" 的单份指针 (整档覆盖, 当前语义不变)。
# cc_session_bindings.json = 一会话一条, 累积权威绑定 (project/workstream/plan/task), 合并语义。
# dashboard 聚合 / agent_registry join 读这一份; 见
# docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.1)。
_BINDINGS_FILE_REL = "data/cc_session_bindings.json"
_SCOPE_FIELDS = ("project", "workstream", "active_plan", "task_id")


def _bindings_file() -> Path:
    return _repo_root() / _BINDINGS_FILE_REL


_BINDINGS_THREAD_LOCK = threading.RLock()
_BINDINGS_INDEX_CACHE_LOCK = threading.RLock()
_BINDINGS_INDEX_CACHE_TOKEN: tuple[str, int, int, int] | None = None
_BINDINGS_INDEX_CACHE: dict[str, dict[str, Any]] = {}


@contextmanager
def _bindings_write_guard() -> Iterator[None]:
    """Serialize binding read-modify-write transactions across CLI processes."""
    lock_path = _bindings_file().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _BINDINGS_THREAD_LOCK, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_bindings() -> dict[str, dict[str, Any]]:
    p = _bindings_file()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_bindings(store: dict[str, dict[str, Any]]) -> None:
    global _BINDINGS_INDEX_CACHE_TOKEN
    p = _bindings_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=p.stem + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(store, ensure_ascii=False, indent=1))
        os.replace(tmp_path, p)
        with _BINDINGS_INDEX_CACHE_LOCK:
            _BINDINGS_INDEX_CACHE_TOKEN = None
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_values(record: dict[str, Any]) -> dict[str, str]:
    return {key: str(record.get(key) or "").strip() for key in _SCOPE_FIELDS}


def _track_scope_transition(
    record: dict[str, Any], fields: dict[str, Any], *, changed_at: str
) -> None:
    """Preserve live Project/Plan/Task changes as ordered scope segments.

    Top-level fields remain the latest/default binding used by existing callers.
    ``scope_segments`` is the durable history used when one conversation crosses
    Project, Plan or Task boundaries. Internal states stay stable; user-facing
    surfaces must translate ``confirmed``/``observed`` into Chinese.
    """

    supplied = {key for key in _SCOPE_FIELDS if fields.get(key) is not None}
    if not supplied:
        return
    old_scope = _scope_values(record)
    merged = dict(record)
    merged.update({key: fields[key] for key in supplied})
    new_scope = _scope_values(merged)
    if old_scope == new_scope or not any(new_scope.values()):
        return

    segments = [dict(item) for item in record.get("scope_segments") or [] if isinstance(item, dict)]
    if segments and not segments[-1].get("ended_at"):
        segments[-1]["ended_at"] = changed_at
    source = str(fields.get("source") or record.get("source") or "unknown")
    binding_state = "confirmed" if source in CONFIRMED_BINDING_SOURCES else "observed"
    segments.append(
        {
            "segment_id": f"scope-{len(segments) + 1:04d}",
            "started_at": changed_at,
            "ended_at": "",
            **new_scope,
            "binding_state": binding_state,
            "source": source,
            "evidence_refs": [],
        }
    )
    record["scope_segments"] = segments


#: 属于"当前占用者"的身份字段。PTY 复用后这些必须整组换人, 不能与前任合并——
#: 残留一个前任的 session_id 会让 session_companion 去读前任的 transcript, 把前任的
#: project/plan 反写回本会话, 进而让消息信封与目标解析都指向错误的会话 (2026-08-26 实证)。
OCCUPANT_SCOPED_FIELDS = (
    "session_id",
    "claude_session_id",
    "project",
    "workstream",
    "active_plan",
    "task_id",
    "topic",
)


def _occupant_changed(previous: dict[str, Any], fields: dict[str, Any]) -> bool:
    """本次写入是否来自与记录中不同的 provider 会话 (同一 PTY 换人)。"""
    for key in ("claude_session_id", "session_id"):
        incoming = str(fields.get(key) or "").strip()
        existing = str(previous.get(key) or "").strip()
        if incoming and existing and incoming != existing:
            return True
    return False


def _upsert_binding(trace_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """合并式 upsert: 只写入非 None 字段, 让 hook(cwd/project) 与后续 bind(plan/task)累积不互相清空。

    例外: 当来者是同一 PTY 上的**另一个** provider 会话时, 前任的身份字段整组作废,
    只保留本次显式带来的值 —— 合并语义会让前任身份永久残留 (见 OCCUPANT_SCOPED_FIELDS)。
    """
    if not trace_id:
        return {}
    with _bindings_write_guard():
        store = _read_bindings()
        previous = dict(store.get(trace_id, {}))
        rec = dict(previous)
        if _occupant_changed(previous, fields):
            for key in OCCUPANT_SCOPED_FIELDS:
                rec.pop(key, None)
            rec["scope_segments"] = []
            rec["occupant_reset_at"] = _now_iso()
        rec["trace_id"] = trace_id
        now = _now_iso()
        _track_scope_transition(rec, fields, changed_at=now)
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        rec["updated_at"] = now
        store[trace_id] = rec
        _write_bindings(store)
    return previous


def all_session_bindings() -> dict[str, dict[str, Any]]:
    """全部按会话绑定记录 (key = trace_id)。"""
    return _read_bindings()


def get_session_binding(trace_id: str | None = None) -> dict[str, Any]:
    """某会话的绑定记录; 不给 trace_id 则取当前会话。"""
    if trace_id is None:
        trace_id = resolve_active_trace_id()
    return _read_bindings().get(trace_id, {})


def session_scope_segments(trace_id: str | None = None) -> list[dict[str, Any]]:
    """Return the ordered Project/Plan/Task scopes for one conversation."""

    return [
        dict(item)
        for item in get_session_binding(trace_id).get("scope_segments") or []
        if isinstance(item, dict)
    ]


def upsert_session_scope_segment(
    trace_id: str,
    segment_id: str,
    *,
    project: str | None = None,
    workstream: str | None = None,
    active_plan: str | None = None,
    task_id: str | None = None,
    start_event_ref: str | None = None,
    end_event_ref: str | None = None,
    start_source_line: int | None = None,
    end_source_line: int | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    binding_state: str = "candidate",
    source: str = "retrospective_semantic",
    rationale: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Upsert one evidence-bounded historical scope into the authority ledger.

    This is the convergence entry for retrospective attribution. It stores only
    scope identifiers and source coordinates, never a copied conversation body.
    ``binding_state`` accepts ``confirmed`` (explicit authority), ``observed``
    (deterministic runtime observation), or ``candidate`` (semantic proposal).
    """

    if not trace_id or not segment_id:
        raise ValueError("trace_id 和 segment_id 不能为空")
    if binding_state not in {"confirmed", "observed", "candidate"}:
        raise ValueError("binding_state 必须是 confirmed、observed 或 candidate")
    scope = {
        "project": str(project or "").strip(),
        "workstream": str(workstream or "").strip(),
        "active_plan": str(active_plan or "").strip(),
        "task_id": str(task_id or "").strip(),
    }
    if not any(scope.values()):
        raise ValueError("分段至少要绑定 Project、Plan 或 Task 之一")

    with _bindings_write_guard():
        store = _read_bindings()
        rec = dict(store.get(trace_id, {}))
        rec["trace_id"] = trace_id
        segments = [
            dict(item) for item in rec.get("scope_segments") or [] if isinstance(item, dict)
        ]
        row = {
            "segment_id": segment_id,
            "started_at": str(started_at or ""),
            "ended_at": str(ended_at or ""),
            "start_event_ref": str(start_event_ref or ""),
            "end_event_ref": str(end_event_ref or ""),
            "start_source_line": start_source_line,
            "end_source_line": end_source_line,
            **scope,
            "binding_state": binding_state,
            "source": source,
            "rationale": rationale,
            "evidence_refs": list(dict.fromkeys(str(ref) for ref in evidence_refs or [] if ref)),
        }
        replaced = False
        for index, item in enumerate(segments):
            if str(item.get("segment_id") or "") == segment_id:
                segments[index] = row
                replaced = True
                break
        if not replaced:
            segments.append(row)
        rec["scope_segments"] = segments
        rec["updated_at"] = _now_iso()
        store[trace_id] = rec
        _write_bindings(store)


def set_session_default_scope_from_history(
    trace_id: str,
    *,
    project: str | None = None,
    workstream: str | None = None,
    active_plan: str | None = None,
    task_id: str | None = None,
) -> None:
    """Set the latest/default scope after a confirmed retrospective recovery.

    The matching historical range must already exist in ``scope_segments``.
    This helper deliberately avoids creating a second segment at recovery time.
    """

    if not trace_id:
        raise ValueError("trace_id 不能为空")
    fields = {
        "project": project,
        "workstream": workstream,
        "active_plan": active_plan,
        "task_id": task_id,
    }
    if not any(value is not None for value in fields.values()):
        return
    with _bindings_write_guard():
        store = _read_bindings()
        rec = dict(store.get(trace_id, {}))
        rec["trace_id"] = trace_id
        for key, value in fields.items():
            if value is not None:
                rec[key] = value
        rec["updated_at"] = _now_iso()
        store[trace_id] = rec
        _write_bindings(store)


def prune_generated_session_scope_segments(
    trace_id: str,
    *,
    sources: set[str],
    keep_segment_ids: set[str],
) -> int:
    """Remove obsolete rebuildable segments while preserving live/manual history."""

    with _bindings_write_guard():
        store = _read_bindings()
        rec = dict(store.get(trace_id, {}))
        before = [dict(item) for item in rec.get("scope_segments") or [] if isinstance(item, dict)]
        after = [
            item
            for item in before
            if str(item.get("source") or "") not in sources
            or str(item.get("segment_id") or "") in keep_segment_ids
        ]
        removed = len(before) - len(after)
        if removed:
            rec["scope_segments"] = after
            rec["updated_at"] = _now_iso()
            store[trace_id] = rec
            _write_bindings(store)
        return removed


def session_binding_is_confirmed(rec: dict[str, Any]) -> bool:
    """Return whether a binding contains explicit/control-plane authority."""

    confirmed_segment = any(
        str(item.get("binding_state") or "") == "confirmed"
        for item in rec.get("scope_segments") or []
        if isinstance(item, dict)
    )
    return confirmed_segment or str(rec.get("source") or "") in CONFIRMED_BINDING_SOURCES


def validate_session_binding_scope(
    project: str | None,
    workstream: str | None,
) -> tuple[str | None, str | None]:
    """Validate an explicit project/workstream scope before writing the ledger.

    Project identity is registry-only. Workspace paths, provider output, and
    free-form labels are evidence for a companion, never valid write targets.
    """
    project_id = str(project or "").strip() or None
    workstream_id = str(workstream or "").strip() or None
    if workstream_id:
        from omnicompany.core.workstreams import get_workstream

        workstream_row = get_workstream(workstream_id)
        if workstream_row is None:
            raise ValueError(f"unregistered workstream: {workstream_id}")
        workstream_project = str(workstream_row["project"])
        if project_id and project_id != workstream_project:
            raise ValueError(
                f"project={project_id} conflicts with workstream={workstream_id} "
                f"(project {workstream_project})"
            )
        project_id = workstream_project

    if project_id:
        from omnicompany.core.projects_registry import list_projects

        registered_projects = {
            str(item.get("id") or "").strip()
            for item in list_projects()
        }
        if project_id not in registered_projects:
            raise ValueError(
                f"unregistered project: {project_id}; register it first with `omni project register`"
            )
    return project_id, workstream_id


def bindings_by_session_key() -> dict[str, dict[str, Any]]:
    """Return binding projections keyed by conversation identity.

    ``provider:session_id`` remains as a compatibility alias for transcript
    lookup, but it is not a scope identity.  The provider-neutral
    ``session:<id>`` and ``pty:<id>`` aliases are the join keys used when a
    conversation is resumed by another runtime/account.
    """
    global _BINDINGS_INDEX_CACHE, _BINDINGS_INDEX_CACHE_TOKEN
    path = _bindings_file()
    try:
        stat = path.stat()
        token = (
            str(path),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
            int(stat.st_size),
        )
    except OSError:
        token = (str(path), 0, 0, 0)
    with _BINDINGS_INDEX_CACHE_LOCK:
        if token == _BINDINGS_INDEX_CACHE_TOKEN:
            return _BINDINGS_INDEX_CACHE

    out: dict[str, dict[str, Any]] = {}
    # One PTY may outlive a provider-native session (resume/new thread).  Index
    # both identities.  Provider/session remains exact.  For a reused PTY,
    # prefer a durable scope over a newer unbound child hook: nested provider
    # sessions inherit OMNI_CC_PTY_ID and must not erase the parent's project.
    rows = sorted(
        _read_bindings().values(),
        key=lambda rec: str(rec.get("updated_at") or ""),
    )

    def _binding_strength(rec: dict[str, Any]) -> int:
        has_scope = any(
            str(rec.get(field) or "").strip()
            for field in ("project", "workstream", "active_plan", "task_id", "topic")
        )
        if not has_scope:
            return 0
        return 2 if session_binding_is_confirmed(rec) else 1

    def _source_rank(rec: dict[str, Any]) -> int:
        """Rank authority carried by a record or one of its scope segments.

        A PTY hook can be newer than an explicit ``omni session bind`` while
        still carrying only the plan portion of the scope.  Treating equal
        strength records as last-write-wins made that hook erase the explicit
        project from the index.  The rank is only a tie breaker; it never lets
        an unconfirmed record beat a confirmed one.
        """

        ranks = {
            "cli_bind": 60,
            "cli_plan_use": 55,
            "dispatch": 50,
            "retrospective_explicit": 45,
            "agent_control_plane": 40,
            "dashboard_agent_worker_compat": 35,
            "session_companion": 30,
            "hook": 10,
        }
        values = [str(rec.get("source") or "").strip()]
        values.extend(
            str(item.get("source") or "").strip()
            for item in rec.get("scope_segments") or []
            if isinstance(item, dict)
        )
        return max((ranks.get(value, 0) for value in values), default=0)

    def _merge_equal_strength(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        """Merge complementary scopes emitted for one PTY/native identity.

        Explicit binds and plan-use hooks often arrive as separate trace rows
        for the same provider session.  Keep the newest value for a field when
        it is equally authoritative, but do not let a low-authority hook
        replace an existing explicit project/workstream/task.
        """

        merged = dict(previous)
        previous_rank = _source_rank(previous)
        current_rank = _source_rank(current)
        for field in ("project", "workstream", "active_plan", "task_id", "topic"):
            value = str(current.get(field) or "").strip()
            if not value:
                continue
            old = str(merged.get(field) or "").strip()
            if not old or current_rank >= previous_rank or field == "active_plan":
                merged[field] = current.get(field)

        # Keep the identity and transport metadata from the higher-authority
        # record, while allowing a newer record to refresh operational fields.
        if current_rank >= previous_rank:
            for field in ("trace_id", "session_id", "claude_session_id", "pty_id", "provider"):
                if current.get(field) is not None:
                    merged[field] = current[field]
        for field in ("cwd", "updated_at", "last_plan_inject_id"):
            if current.get(field) is not None:
                merged[field] = current[field]
        if current_rank >= previous_rank or not merged.get("source"):
            merged["source"] = current.get("source") or merged.get("source")
        if current.get("binding_kind") and (
            current_rank >= previous_rank or not merged.get("binding_kind")
        ):
            merged["binding_kind"] = current["binding_kind"]

        # Preserve scope history for audit consumers, de-duplicating rows with
        # the same segment id.  The top-level fields above are the projection
        # consumed by the dashboard and retain both complementary values.
        segments: list[dict[str, Any]] = []
        seen_segments: set[str] = set()
        for record in (previous, current):
            for item in record.get("scope_segments") or []:
                if not isinstance(item, dict):
                    continue
                segment_id = str(item.get("segment_id") or "")
                key = segment_id or repr(sorted(item.items()))
                if key in seen_segments:
                    continue
                seen_segments.add(key)
                segments.append(dict(item))
        if segments:
            merged["scope_segments"] = segments
        return merged

    def _put_strongest(key: str, rec: dict[str, Any]) -> None:
        previous = out.get(key)
        if previous is None:
            out[key] = rec
            return
        current_strength = _binding_strength(rec)
        previous_strength = _binding_strength(previous)
        if current_strength > previous_strength:
            out[key] = rec
        elif current_strength == previous_strength:
            out[key] = _merge_equal_strength(previous, rec)

    for rec in rows:
        provider = str(rec.get("provider") or "").strip()
        session_ids = {
            str(value).strip()
            for value in (rec.get("session_id"), rec.get("claude_session_id"))
            if str(value or "").strip()
        }
        # Older explicit CLI binds only persisted trace_id + PTY.  Recover the
        # native id from the trace so a later digest observation for the same
        # provider session cannot shadow the user's confirmed project.
        trace_id = str(rec.get("trace_id") or "").strip()
        for prefix in (f"{provider}_" if provider else "", "cc_"):
            if prefix and trace_id.startswith(prefix) and len(trace_id) > len(prefix):
                session_ids.add(trace_id[len(prefix) :])
        for sid in session_ids:
            # Scope belongs to the conversation, not to the provider that
            # happens to host it.  Keep a neutral alias so a Claude/Codex
            # spelling change cannot make a valid project disappear.
            _put_strongest(f"session:{sid}", rec)
            if provider:
                _put_strongest(f"{provider}:{sid}", rec)
        pty_id = rec.get("pty_id")
        if pty_id:
            _put_strongest(f"pty:{pty_id}", rec)
    with _BINDINGS_INDEX_CACHE_LOCK:
        _BINDINGS_INDEX_CACHE_TOKEN = token
        _BINDINGS_INDEX_CACHE = out
    return out


def binding_for_session(
    *,
    trace_id: str | None = None,
    provider: str | None = None,
    session_id: str | None = None,
    pty_id: str | None = None,
) -> dict[str, Any]:
    """Resolve one conversation's binding without making provider authoritative.

    PTY identity is preferred because it survives native-session resume.  The
    provider-neutral native-session alias is next; provider-specific and exact
    trace aliases are compatibility fallbacks for older ledger rows.
    """

    index = bindings_by_session_key()
    exact = {}
    raw_trace = str(trace_id or "").strip()
    if raw_trace:
        exact = _read_bindings().get(raw_trace, {})
    sid = str(session_id or "").strip()
    keys = [
        f"pty:{str(pty_id).strip()}" if str(pty_id or "").strip() else "",
        f"session:{sid}" if sid else "",
        f"{str(provider).strip()}:{sid}" if str(provider or "").strip() and sid else "",
        str(trace_id or "").strip(),
    ]
    for key in keys:
        if key and key in index:
            candidate = index[key]
            # A PTY can be reused by a new native conversation. The PTY alias
            # is provider-neutral only while it still carries the same native
            # session; otherwise it must not leak the previous occupant's
            # project/plan into the new conversation.
            if key.startswith("pty:") and sid:
                candidate_ids = {
                    str(candidate.get(field) or "").strip()
                    for field in ("session_id", "claude_session_id")
                    if str(candidate.get(field) or "").strip()
                }
                if candidate_ids and sid not in candidate_ids:
                    continue
            return candidate
    return exact


def bind_task_for_session(
    provider: str | None,
    session_id: str | None,
    *,
    task_id: str | None = None,
    plan_id: str | None = None,
    project: str | None = None,
    workstream: str | None = None,
) -> None:
    """给已知 (provider, session_id) 的会话绑定 task/plan —— 收敛入口。

    task↔会话唯一真源就是这份台账;dispatch(task_bindings.json)只保留投递态
    (carrier/status/agent 名),把"哪些会话在做这个 task"镜像到这里,自绑定与派发同源。
    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.6)。
    """
    if not session_id:
        return
    rec = binding_for_session(provider=provider, session_id=session_id)
    trace_id = (rec or {}).get("trace_id") or f"cc_{session_id}"
    _upsert_binding(
        trace_id,
        {
            "claude_session_id": session_id,
            "provider": provider,
            "task_id": task_id,
            "active_plan": plan_id,
            "project": project,
            "workstream": workstream,
        },
    )


def sessions_for_task(task_id: str) -> list[dict[str, Any]]:
    """某 task 绑到的所有会话记录(唯一真源查询)。"""
    rows: list[dict[str, Any]] = []
    for record in _read_bindings().values():
        if record.get("task_id") == task_id or any(
            item.get("task_id") == task_id
            for item in record.get("scope_segments") or []
            if isinstance(item, dict)
        ):
            rows.append(record)
    return rows


def update_session_binding(trace_id: str, **fields: Any) -> None:
    """只合并更新按会话台账(不碰 active 指针文件)。

    给 hook 每轮轻量记账用(如 turns 计数 / reminded 标记 / universal capture),
    避免像 record_active_session 那样整档覆盖 active 指针而清掉当前 plan。
    """
    _upsert_binding(trace_id, dict(fields))


def link_record_to_session(
    trace_id: str | None, *, kind: str, record_id: str, ref_id: str | None = None
) -> None:
    """把一条产出记录(progress / decision / task-note)挂到会话台账,反向可列"这个会话产出了啥"。

    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.4)。不改记录本身的存储,
    只在会话侧记一条引用(kind/id/ref),幂等去重。
    """
    if trace_id is None:
        trace_id = resolve_active_trace_id()
    if not trace_id or not record_id:
        return
    with _bindings_write_guard():
        store = _read_bindings()
        rec = dict(store.get(trace_id, {}))
        rec["trace_id"] = trace_id
        records = list(rec.get("records", []))
        if any(r.get("kind") == kind and r.get("id") == str(record_id) for r in records):
            return  # 已挂过, 幂等
        records.append(
            {
                "kind": kind,
                "id": str(record_id),
                "ref": ref_id,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )
        rec["records"] = records[-200:]  # 上限, 防无限增长
        rec["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        store[trace_id] = rec
        _write_bindings(store)


def records_for_session(trace_id: str | None = None) -> list[dict[str, Any]]:
    """某会话产出的记录引用(progress / decision / task-note)。"""
    if trace_id is None:
        trace_id = resolve_active_trace_id()
    return list(_read_bindings().get(trace_id, {}).get("records", []))


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    from datetime import datetime

    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError):
        return None


def reconcile_bindings(max_age_days: float = 14.0) -> int:
    """清理陈旧会话绑定(updated_at 超过 max_age_days 天没更新 = 会话早死了)。

    活会话每轮都会被 hook/CLI 触碰 updated_at,所以长期没更新的就是死会话。返回删除条数。
    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.3 对账)。
    """
    with _bindings_write_guard():
        store = _read_bindings()
        if not store:
            return 0
        now = time.time()
        cutoff = max_age_days * 86400.0
        keep: dict[str, dict[str, Any]] = {}
        removed = 0
        for k, rec in store.items():
            ts = _parse_iso(rec.get("updated_at"))
            durable_scope = bool(
                rec.get("project")
                or rec.get("workstream")
                or rec.get("active_plan")
                or rec.get("task_id")
                or rec.get("scope_segments")
                or rec.get("records")
            )
            if ts is not None and (now - ts) > cutoff and not durable_scope:
                removed += 1
                continue
            keep[k] = rec
        if removed:
            _write_bindings(keep)
        return removed


def _codex_runtime_provider() -> str:
    """Return the installed Codex runtime id for this process.

    ``CODEX_THREAD_ID`` is shared by all Codex accounts, while the dashboard
    keeps native stores separate (for example ``codex`` and ``codex_2``).
    Prefer the launcher-provided runtime id so CLI lookups join the same ledger
    row as the SessionStart hook; retain ``codex`` for direct/legacy launches.
    """

    value = str(
        os.environ.get("OMNI_AGENT_PROVIDER")
        or os.environ.get("OMNI_HOOK_PROVIDER")
        or "codex"
    ).strip()
    return value if value.startswith("codex") else "codex"


def resolve_active_trace_id() -> str:
    """返回当前 claude code session 的 trace_id.

    解析优先级:
    1. `OMNI_CC_TRACE_ID` env (CLI 显式设 / 测试 / 脚本)
    2. `CODEX_THREAD_ID` env (Codex 原生线程 id; provider 前缀取
       `OMNI_AGENT_PROVIDER`/`OMNI_HOOK_PROVIDER`, 兼容 codex_2 等独立账号)
    3. `OMNI_CC_PTY_ID` env (dashboard PTY 启动 agent 时传给子进程)
    4. `data/cc_session_active.json` 里 active_trace_id (SessionStart hook 写的)
    5. fallback `cc_unknown_<unix_ts>` (warn 级缺省, 仍能跑但跟 dashboard 对不上)
    """
    explicit = os.environ.get("OMNI_CC_TRACE_ID")
    if explicit:
        return explicit
    codex_thread_id = os.environ.get("CODEX_THREAD_ID")
    if codex_thread_id:
        return f"{_codex_runtime_provider()}_{codex_thread_id}"
    pty_id = os.environ.get("OMNI_CC_PTY_ID")
    if pty_id:
        return pty_id
    active = _read_active()
    tid = active.get("trace_id") or active.get("active_trace_id")
    if tid:
        return tid
    return f"cc_unknown_{int(time.time())}"


def current_session_meta() -> dict[str, Any]:
    """完整 session 元数据 (供 omni who / dashboard / 调试).

    返回 dict, 字段含:
    - trace_id: 当前解析出的 trace_id
    - source: 'env_explicit' / 'env_pty' / 'active_file' / 'fallback'
    - claude_session_id: hook 抓到的 (可能 None)
    - pty_id: dashboard PTY id (可能 None)
    - active_plan: 当前 active plan 路径 (hook 抓的)
    - started_at: ISO 时间戳
    - cwd: 当前工作目录
    - active_file_path: cc_session_active.json 绝对路径
    """
    explicit = os.environ.get("OMNI_CC_TRACE_ID")
    codex_thread_id = os.environ.get("CODEX_THREAD_ID")
    pty_id = os.environ.get("OMNI_CC_PTY_ID")
    active = _read_active()

    if explicit:
        trace_id, source = explicit, "env_explicit"
    elif codex_thread_id:
        trace_id, source = f"{_codex_runtime_provider()}_{codex_thread_id}", "env_codex"
    elif pty_id:
        trace_id, source = pty_id, "env_pty"
    elif active.get("trace_id") or active.get("active_trace_id"):
        trace_id = active.get("trace_id") or active.get("active_trace_id")
        source = "active_file"
    else:
        trace_id, source = f"cc_unknown_{int(time.time())}", "fallback"

    # 叠加按会话台账: active 指针文件可能被切 plan 时整档覆盖而丢掉 project/task,
    # 台账是合并累积的, 用它兜底当前会话的累积字段。这里必须走 provider-neutral
    # join；同一原生会话换 provider/account 后仍应看到原来的 scope，而不是新建
    # 一条 provider 专属的空记录。
    binding = binding_for_session(
        trace_id=trace_id,
        provider=_codex_runtime_provider() if codex_thread_id else None,
        session_id=codex_thread_id,
        pty_id=pty_id,
    )
    canonical_binding_trace = str(binding.get("trace_id") or "").strip()
    if canonical_binding_trace:
        trace_id = canonical_binding_trace

    active_trace_id = active.get("trace_id") or active.get("active_trace_id")
    active_matches = active_trace_id == trace_id

    def _pick(key: str) -> Any:
        if binding.get(key) is not None:
            return binding.get(key)
        if active_matches and active.get(key) is not None:
            return active.get(key)
        return None

    return {
        "trace_id": trace_id,
        "source": source,
        "session_id": codex_thread_id or _pick("session_id") or _pick("claude_session_id"),
        "claude_session_id": None if codex_thread_id else _pick("claude_session_id"),
        "pty_id": pty_id or _pick("pty_id"),
        "active_plan": _pick("active_plan"),
        "project": _pick("project"),
        "workstream": _pick("workstream"),
        "task_id": _pick("task_id"),
        "topic": _pick("topic"),
        "provider": _codex_runtime_provider() if codex_thread_id else _pick("provider"),
        "started_at": active.get("started_at"),
        "cwd": active.get("cwd") or os.getcwd(),
        "active_file_path": str(_active_file()),
    }


def record_active_session(
    trace_id: str,
    *,
    session_id: str | None = None,
    claude_session_id: str | None = None,
    pty_id: str | None = None,
    active_plan: str | None = None,
    project: str | None = None,
    workstream: str | None = None,
    task_id: str | None = None,
    provider: str | None = None,
    cwd: str | None = None,
    source: str = "hook",
    extra: dict[str, Any] | None = None,
) -> Path:
    """写当前 session 元数据到 `data/cc_session_active.json` + upsert 按会话绑定台账.

    hook + CLI 共用: SessionStart hook 调 (source='hook'), `omni session bind` CLI 调
    (source='cli_bind'). 走同一份函数, 走的逻辑一致, 只是触发方式不同.

    返回写入的文件路径 (active 指针文件)。

    两处落盘:
    - `cc_session_active.json`: 整文件覆盖 (当前会话指针, 一份一份切, 不累积)。
    - `cc_session_bindings.json`: 按 trace_id 合并 upsert (plan/project/task 累积不互相清空),
      给 dashboard 多会话聚合 / agent_registry join 读。
    """
    if not trace_id:
        raise ValueError("trace_id 不能为空")

    incoming_session_id = session_id or claude_session_id
    existing_binding = binding_for_session(
        trace_id=trace_id,
        provider=provider,
        session_id=incoming_session_id,
        pty_id=pty_id,
    )
    # Native session ids are conversation identity, not provider identity. A
    # resumed conversation may be observed under another provider/account
    # spelling; reuse its trace so the binding ledger remains one authority.
    existing_trace_id = str(existing_binding.get("trace_id") or "").strip()
    existing_native_ids = {
        str(existing_binding.get(field) or "").strip()
        for field in ("session_id", "claude_session_id")
        if str(existing_binding.get(field) or "").strip()
    }
    same_native_session = bool(
        incoming_session_id and incoming_session_id in existing_native_ids
    )
    same_pty_without_native = bool(
        pty_id
        and existing_binding.get("pty_id") == pty_id
        and not incoming_session_id
        and not existing_native_ids
    )
    # Do not collapse a legacy trace-only row merely because its provider and
    # synthetic trace happen to contain the same native id. Keep that older
    # explicit row available for strength-based projection; normal hooks carry
    # session_id and take the provider-neutral path above.
    canonical_trace_id = (
        existing_trace_id
        if existing_trace_id and (same_native_session or same_pty_without_native)
        else trace_id
    )

    payload: dict[str, Any] = {
        "trace_id": canonical_trace_id,
        "session_id": incoming_session_id,
        "claude_session_id": claude_session_id,
        "pty_id": pty_id,
        "active_plan": active_plan,
        "project": project,
        "workstream": workstream,
        "task_id": task_id,
        "provider": provider,
        "cwd": cwd or os.getcwd(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source,
    }
    if extra:
        payload.update(extra)

    # 按会话台账: 合并 upsert (只写非 None, 让不同触发点累积)。
    previous_binding = _upsert_binding(
        canonical_trace_id,
        {
            "session_id": incoming_session_id,
            "claude_session_id": claude_session_id,
            "pty_id": pty_id,
            "active_plan": active_plan,
            "project": project,
            "workstream": workstream,
            "task_id": task_id,
            "provider": provider,
            "cwd": cwd or os.getcwd(),
            "source": source,
        },
    )
    plan_changed = active_plan is not None and str(
        previous_binding.get("active_plan") or ""
    ) != str(active_plan or "")

    # Prompt hooks are latency-sensitive. Publish a tiny derived handoff marker
    # only when the bound plan actually changes, instead of making every prompt
    # parse the complete cc_sessions.json registry. SessionStart has already
    # injected its plan context, so that generation is born acknowledged.
    if pty_id and plan_changed:
        try:
            from omnicompany.core.plan_context_trigger import publish_plan_change

            publish_plan_change(
                pty_id,
                active_plan,
                already_injected=(source == "hook"),
                state_dir=Path(
                    os.environ.get("OMNI_CC_DAEMON_STATE_DIR") or (_repo_root() / "data")
                ),
            )
        except OSError:
            # The binding is authoritative; failure of its derived trigger must
            # not prevent session identity from being recorded.
            pass

    p = _active_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    # atomic write — 防 hook / pytest 中断时留半截文件 (历史 dogfood 时这里被 pytest 测试污染过).
    # tempfile + os.replace 在 Windows + POSIX 都 atomic (Python 3.3+).
    fd, tmp_path = tempfile.mkstemp(prefix=p.stem + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        os.replace(tmp_path, p)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return p
