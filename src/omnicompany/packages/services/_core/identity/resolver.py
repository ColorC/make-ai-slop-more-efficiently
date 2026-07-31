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
import time
from pathlib import Path
from typing import Any


# active session 元数据落盘位置, 跟 cc_wrapper hook 共用
_ACTIVE_FILE_REL = "data/cc_session_active.json"


def _repo_root() -> Path:
    """跟 cc_wrapper/hooks/_shared.repo_root() 同算法 (避免反向 import dashboard)."""
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / "src" / "omnicompany").is_dir() and (d / "docs").is_dir():
            return d
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
# cc_session_bindings.json = 一会话一条, 累积权威绑定 (plan/project/task), 合并语义。
# dashboard 聚合 / agent_registry join 读这一份; 见
# docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.1)。
_BINDINGS_FILE_REL = "data/cc_session_bindings.json"


def _bindings_file() -> Path:
    return _repo_root() / _BINDINGS_FILE_REL


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
    p = _bindings_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=p.stem + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(store, ensure_ascii=False, indent=1))
        os.replace(tmp_path, p)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _upsert_binding(trace_id: str, fields: dict[str, Any]) -> None:
    """合并式 upsert: 只写入非 None 字段, 让 hook(cwd/project) 与后续 bind(plan/task)累积不互相清空。"""
    if not trace_id:
        return
    store = _read_bindings()
    rec = dict(store.get(trace_id, {}))
    rec["trace_id"] = trace_id
    for k, v in fields.items():
        if v is not None:
            rec[k] = v
    rec["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    store[trace_id] = rec
    _write_bindings(store)


def all_session_bindings() -> dict[str, dict[str, Any]]:
    """全部按会话绑定记录 (key = trace_id)。"""
    return _read_bindings()


def get_session_binding(trace_id: str | None = None) -> dict[str, Any]:
    """某会话的绑定记录; 不给 trace_id 则取当前会话。"""
    if trace_id is None:
        trace_id = resolve_active_trace_id()
    return _read_bindings().get(trace_id, {})


def bindings_by_session_key() -> dict[str, dict[str, Any]]:
    """按 ``provider:session_id`` 索引；兼容旧 ``claude_session_id`` 台账。"""
    out: dict[str, dict[str, Any]] = {}
    for rec in _read_bindings().values():
        sid = rec.get("session_id") or rec.get("claude_session_id")
        if sid:
            out[f"{rec.get('provider')}:{sid}"] = rec
    return out


def bind_task_for_session(
    provider: str | None,
    session_id: str | None,
    *,
    task_id: str | None = None,
    plan_id: str | None = None,
    project: str | None = None,
) -> None:
    """给已知 (provider, session_id) 的会话绑定 task/plan —— 收敛入口。

    task↔会话唯一真源就是这份台账;dispatch(task_bindings.json)只保留投递态
    (carrier/status/agent 名),把"哪些会话在做这个 task"镜像到这里,自绑定与派发同源。
    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.6)。
    """
    if not session_id:
        return
    rec = bindings_by_session_key().get(f"{provider}:{session_id}")
    trace_prefix = "codex" if provider == "codex" else "cc"
    trace_id = (rec or {}).get("trace_id") or f"{trace_prefix}_{session_id}"
    _upsert_binding(trace_id, {
        "session_id": session_id,
        "claude_session_id": session_id if str(provider or "").startswith("claude") else None,
        "provider": provider,
        "task_id": task_id,
        "active_plan": plan_id,
        "project": project,
    })


def sessions_for_task(task_id: str) -> list[dict[str, Any]]:
    """某 task 绑到的所有会话记录(唯一真源查询)。"""
    return [r for r in _read_bindings().values() if r.get("task_id") == task_id]


def update_session_binding(trace_id: str, **fields: Any) -> None:
    """只合并更新按会话台账(不碰 active 指针文件)。

    给 hook 每轮轻量记账用(如 turns 计数 / reminded 标记 / universal capture),
    避免像 record_active_session 那样整档覆盖 active 指针而清掉当前 plan。
    """
    _upsert_binding(trace_id, dict(fields))


def link_record_to_session(trace_id: str | None, *, kind: str, record_id: str,
                           ref_id: str | None = None) -> None:
    """把一条产出记录(progress / decision / task-note)挂到会话台账,反向可列"这个会话产出了啥"。

    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.4)。不改记录本身的存储,
    只在会话侧记一条引用(kind/id/ref),幂等去重。
    """
    if trace_id is None:
        trace_id = resolve_active_trace_id()
    if not trace_id or not record_id:
        return
    store = _read_bindings()
    rec = dict(store.get(trace_id, {}))
    rec["trace_id"] = trace_id
    records = list(rec.get("records", []))
    if any(r.get("kind") == kind and r.get("id") == str(record_id) for r in records):
        return  # 已挂过, 幂等
    records.append({"kind": kind, "id": str(record_id), "ref": ref_id,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
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
    store = _read_bindings()
    if not store:
        return 0
    now = time.time()
    cutoff = max_age_days * 86400.0
    keep: dict[str, dict[str, Any]] = {}
    removed = 0
    for k, rec in store.items():
        ts = _parse_iso(rec.get("updated_at"))
        if ts is not None and (now - ts) > cutoff:
            removed += 1
            continue
        keep[k] = rec
    if removed:
        _write_bindings(keep)
    return removed


def resolve_active_trace_id() -> str:
    """返回当前 claude code session 的 trace_id.

    解析优先级:
    1. `OMNI_CC_TRACE_ID` env (CLI 显式设 / 测试 / 脚本)
    2. `CODEX_THREAD_ID` env (Codex Desktop 当前原生会话; 不读全局 active 指针)
    3. `OMNI_CC_PTY_ID` env (dashboard PTY 启动 claude 时传给子进程)
    4. `data/cc_session_active.json` 里 active_trace_id (SessionStart hook 写的)
    5. fallback `cc_unknown_<unix_ts>` (warn 级缺省, 仍能跑但跟 dashboard 对不上)
    """
    explicit = os.environ.get("OMNI_CC_TRACE_ID")
    if explicit:
        return explicit
    codex_thread_id = os.environ.get("CODEX_THREAD_ID")
    if codex_thread_id:
        return f"codex_{codex_thread_id}"
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
    - session_id: provider-neutral native session id (可能 None)
    - claude_session_id: legacy Claude alias (可能 None)
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
        trace_id, source = f"codex_{codex_thread_id}", "env_codex"
    elif pty_id:
        trace_id, source = pty_id, "env_pty"
    elif active.get("trace_id") or active.get("active_trace_id"):
        trace_id = active.get("trace_id") or active.get("active_trace_id")
        source = "active_file"
    else:
        trace_id, source = f"cc_unknown_{int(time.time())}", "fallback"

    # 叠加按会话台账: active 指针文件可能被切 plan 时整档覆盖而丢掉 project/task,
    # 台账是合并累积的, 用它兜底当前会话的累积字段。
    binding = _read_bindings().get(trace_id, {})
    # CODEX_THREAD_ID 已经精确指明当前会话; 全局 Claude active 指针可能被别的终端/
    # pytest 覆盖, 不能再把它的 plan/project/session 字段叠到 Codex 会话上。
    session_active = {} if codex_thread_id else active

    def _pick(key: str) -> Any:
        return session_active.get(key) if session_active.get(key) is not None else binding.get(key)

    return {
        "trace_id": trace_id,
        "source": source,
        "session_id": codex_thread_id or _pick("session_id") or _pick("claude_session_id"),
        "claude_session_id": _pick("claude_session_id"),
        "pty_id": pty_id or _pick("pty_id"),
        "active_plan": _pick("active_plan"),
        "project": _pick("project"),
        "task_id": _pick("task_id"),
        "provider": "codex" if codex_thread_id else _pick("provider"),
        "started_at": session_active.get("started_at"),
        "cwd": session_active.get("cwd") or os.getcwd(),
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

    provider_session_id = session_id or claude_session_id
    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "session_id": provider_session_id,
        "claude_session_id": claude_session_id,
        "pty_id": pty_id,
        "active_plan": active_plan,
        "project": project,
        "task_id": task_id,
        "provider": provider,
        "cwd": cwd or os.getcwd(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source,
    }
    if extra:
        payload.update(extra)

    # 按会话台账: 合并 upsert (只写非 None, 让不同触发点累积)。
    _upsert_binding(trace_id, {
        "session_id": provider_session_id,
        "claude_session_id": claude_session_id,
        "pty_id": pty_id,
        "active_plan": active_plan,
        "project": project,
        "task_id": task_id,
        "provider": provider,
        "cwd": cwd or os.getcwd(),
        "source": source,
    })

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
