# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:cli.db_helpers.connection_and_formatting.utils.py"
"""CLI 共享工具：DB 连接、格式化辅助。

这里的库是旧的 routing_events / semantic network 记录库(``semantic_network.db``)。
它的写入端已经退役, 历史文件归档在 ``data/_archive/agent_loop/``; 读它的命令
(``omni open-loops`` / ``omni evo`` 等)在文件不存在时要说清楚, 不能直接抛
sqlite 的堆栈。
"""
import os
import sqlite3
import json
from pathlib import Path

import click

#: 归档位置, 只用来在报错里指路。
ARCHIVED_DB_HINT = "data/_archive/agent_loop/semantic_network.db"


def _default_db() -> str:
    """默认库路径: ``OMNI_DB`` 直接指定文件; 否则按 ``OMNICOMPANY_DB_DIR``
    (与驾驶舱 controlplane 同一条规则)找目录, 再默认到项目根 ``data/autonomous/``。
    """
    explicit = os.environ.get("OMNI_DB", "").strip()
    if explicit:
        return explicit
    root = os.environ.get("OMNICOMPANY_DB_DIR", "").strip()
    base = Path(root) if root else Path(__file__).resolve().parents[3] / "data" / "autonomous"
    return str(base / "semantic_network.db")


DEFAULT_DB = _default_db()


def open_db(path: str, *, must_exist: bool = True) -> sqlite3.Connection:
    """打开 sqlite 库。默认要求文件已存在: 只读命令不该悄悄建一个空库再报"没有表"。"""
    if must_exist and not Path(path).is_file():
        raise click.ClickException(
            f"找不到数据库文件: {path}\n"
            "  这是旧的 routing_events / semantic network 记录库, 写入端已退役, 现在不再生成。\n"
            f"  历史文件归档在 {ARCHIVED_DB_HINT}(旧表结构, 本命令不一定读得了)。\n"
            "  确有别的库要看时用 --db <路径> 或设 OMNI_DB 环境变量。"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def resolve_db(db_option: str | None) -> str:
    return db_option or DEFAULT_DB


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "-"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def fmt_bool(v: int | None, true_str="ok", false_str="FAIL") -> str:
    if v is None:
        return "?"
    return true_str if v else false_str


def truncate(s: str | None, n: int = 80) -> str:
    if not s:
        return "-"
    s = s.replace("\n", " ")
    return s[:n] + "..." if len(s) > n else s


def type_ids(json_str: str | None) -> str:
    if not json_str:
        return "-"
    try:
        items = json.loads(json_str)
        return ", ".join(t.get("type_id", str(t)) for t in items)
    except Exception:
        return json_str or "-"


def parse_json_list(s: str | None) -> list:
    if not s:
        return []
    try:
        return json.loads(s)
    except Exception:
        return []
