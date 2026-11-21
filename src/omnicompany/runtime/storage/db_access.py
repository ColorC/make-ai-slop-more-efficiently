# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.storage.sqlite_connection_manager.implementation.py"
"""集中式 SQLite 访问层 — 所有 omnicompany 模块的 DB 入口。

## 设计原则

所有访问 omnicompany 托管数据库（semantic_network.db、intent_db 等）的代码
必须通过本模块的接口，而不是直接调用 sqlite3.connect()。

理由：
  - 统一 WAL 模式 + busy_timeout=30s（消除锁竞争）
  - 统一 row_factory = sqlite3.Row（防止索引访问出错）
  - 强制连接短暂持有：打开→读写→立即关闭，绝不在 LLM 调用期间持有

## 用法

```python
from omnicompany.runtime.storage.db_access import open_db, open_db_rw

# 推荐：上下文管理器，with 块结束自动 commit + close
with open_db(db_path) as conn:
    row = conn.execute("SELECT ...").fetchone()

# 只读查询
with open_db(db_path, readonly=True) as conn:
    rows = conn.execute("SELECT ...").fetchall()

# 长生命周期连接（class 内部，需手动 commit/close）
self._conn = open_db_rw(db_path)
```

## 强制执行

调用 `install_connect_guard()` 后，任何绕过本模块直接调用
sqlite3.connect() 访问托管数据库的代码都会触发 RuntimeWarning。
在 agent_loop.py 的启动路径中已自动安装。

## 禁止事项

  ❌ conn = sqlite3.connect(db_path)            # 绕过所有设置
  ❌ sqlite3.connect(path, timeout=5)           # timeout 太短且没有 busy_timeout
  ✅ with open_db(db_path) as conn: ...         # 正确
  ✅ conn = open_db_rw(db_path)                 # 长生命周期连接
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import warnings
from typing import Generator

# ── 标准连接参数 ───────────────────────────────────────────────────────────

# SQLite busy_timeout（毫秒）：等待写锁时最多等待 30 秒
_DEFAULT_BUSY_TIMEOUT_MS = 30_000

# sqlite3.connect() 的 timeout 参数（秒）：Python 层等待
_DEFAULT_CONNECT_TIMEOUT = 30.0

# 受托管的数据库文件名关键词（用于 guard 检测）
_MANAGED_DB_KEYWORDS: tuple[str, ...] = (
    "semantic_network",
    "intent_db",
    "semantic_network.db",
)


# ── 主接口 ────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def open_db(
    path: str,
    *,
    readonly: bool = False,
    timeout: float = _DEFAULT_CONNECT_TIMEOUT,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> Generator[sqlite3.Connection, None, None]:
    """打开 SQLite 连接（上下文管理器），退出时自动 commit + close。

    这是访问托管数据库的**首选方式**。

    Args:
        path: SQLite 数据库文件路径。
        readonly: 若为 True，以只读模式打开（不会自动 commit）。
        timeout: sqlite3.connect() 的 timeout 参数（秒）。
        busy_timeout_ms: SQLite busy_timeout pragma 值（毫秒）。

    Yields:
        sqlite3.Connection，已设置 WAL、busy_timeout、row_factory=sqlite3.Row。

    Example:
        with open_db(db_path) as conn:
            conn.execute("UPDATE ... SET ... WHERE ...")
        # 自动 commit 并 close
    """
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        conn.row_factory = sqlite3.Row
        if readonly:
            conn.execute("PRAGMA query_only=ON")
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()


def open_db_rw(
    path: str,
    *,
    timeout: float = _DEFAULT_CONNECT_TIMEOUT,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """打开可写连接（非上下文管理器）。调用方负责 commit / close。

    仅在以下情况使用：
    - 需要在类的生命周期内持有连接（如 SemanticRouter、IntentTracer）
    - 需要跨多个操作共享同一事务

    ⚠️  持有此连接时绝不能执行 LLM 调用或任何耗时操作。

    Example:
        self._conn = open_db_rw(db_path)
        # ... 使用 self._conn ...
        self._conn.commit()
        self._conn.close()
    """
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.row_factory = sqlite3.Row
    return conn


# ── 强制执行：guard 机制 ──────────────────────────────────────────────────

def install_connect_guard() -> None:
    """安装运行时 guard：检测绕过 db_access 的 sqlite3.connect() 调用。

    在 agent_loop.py 的启动路径中自动调用。
    发现绕过行为时发出 RuntimeWarning，日志中会显示调用位置。

    Guard 只检测访问托管数据库（_MANAGED_DB_KEYWORDS）的调用，
    不影响 bus/sqlite.py 对 events.db 的直连。
    """
    if getattr(sqlite3, "_omni_guarded", False):
        return  # 已安装，幂等

    _real_connect = sqlite3.connect

    def _guarded_connect(database, *args, **kwargs):
        db_str = str(database)
        # 只检查托管数据库（events.db 等不受控）
        if any(kw in db_str for kw in _MANAGED_DB_KEYWORDS):
            # 允许 db_access.py 自身调用（避免递归）
            frame = sys._getframe(1)
            caller_file = frame.f_code.co_filename or ""
            if "db_access" not in caller_file:
                caller_info = f"{caller_file}:{frame.f_lineno} in {frame.f_code.co_name}()"
                warnings.warn(
                    f"\n[DB Guard] 检测到绕过托管访问层的直连：\n"
                    f"  sqlite3.connect('{db_str}')\n"
                    f"  调用位置：{caller_info}\n"
                    f"  请改用：from omnicompany.runtime.storage.db_access import open_db, open_db_rw",
                    RuntimeWarning,
                    stacklevel=2,
                )
        return _real_connect(database, *args, **kwargs)

    sqlite3.connect = _guarded_connect  # type: ignore[method-assign]
    sqlite3._omni_guarded = True  # type: ignore[attr-defined]
