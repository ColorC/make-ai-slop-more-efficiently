# [OMNI] origin=ai-ide domain=services/_core/identity ts=2026-05-02T00:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="omnicompany 身份模块, 统一 claude code session 身份解析跟写过文件追溯, hook + CLI + web 共用一份逻辑"
# [OMNI] why="dashboard cc_wrapper 已实施 session 自动注册到 SQLite event bus, 但 CLI 端没共用同一身份链, 容易出现 cli/web 两套身份. 这层把身份解析/写入抽出来共用"
# [OMNI] tags=identity,session,claude-code,foundation
# [OMNI] material_id="material:core.identity.module_aggregate.exports.py"
"""omnicompany 身份模块 — claude code session 统一身份.

**问题**:
dashboard cc_wrapper 通过 hook 把 session 自动注册到 `data/ide_events.db` SQLite
event bus, 用 `OMNI_CC_PTY_ID` (dashboard PTY 启动) 或 `cc_<claude_session_id>` 作
trace_id. CLI 端如果另立一套身份, 同一个 claude session 在 cli `omni register`
跟 web dashboard 看到的身份会不一致.

**解法**:
本模块把"当前 session 是谁 + 写过哪些文件"抽到公共函数, hook / CLI / web 三方都走
同一份 `resolve_active_trace_id()` + `current_session_meta()`, 触发方式不同但走的
逻辑一致.

**身份解析优先级** (高→低):
1. `OMNI_CC_TRACE_ID` 环境变量 (CLI 显式 / 脚本 / 测试场景设)
2. `OMNI_CC_PTY_ID` 环境变量 (dashboard PTY 启动 claude 时传)
3. `data/cc_session_active.json` 里的 trace_id (SessionStart hook 写的)
4. fallback: `cc_unknown_<timestamp>` (warn)

**双轨制**:
- 自动: SessionStart hook 触发时调 `record_active_session()` 写 active 文件 (默认)
- 显式: CLI `omni session bind --trace-id=<>` 调同一函数兜底覆盖 (脚本 / 测试用)

走的逻辑同一, 只是触发方式不同.
"""
from __future__ import annotations

from omnicompany.packages.services._core.identity.resolver import (
    resolve_active_trace_id,
    current_session_meta,
    record_active_session,
    all_session_bindings,
    get_session_binding,
    bindings_by_session_key,
    bind_task_for_session,
    sessions_for_task,
    update_session_binding,
    reconcile_bindings,
    link_record_to_session,
    records_for_session,
)
from omnicompany.packages.services._core.identity.writes import (
    session_writes,
)

__all__ = [
    "resolve_active_trace_id",
    "current_session_meta",
    "record_active_session",
    "all_session_bindings",
    "get_session_binding",
    "bindings_by_session_key",
    "bind_task_for_session",
    "sessions_for_task",
    "update_session_binding",
    "reconcile_bindings",
    "link_record_to_session",
    "records_for_session",
    "session_writes",
]
