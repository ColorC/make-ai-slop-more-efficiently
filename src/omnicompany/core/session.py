# [OMNI] origin=ai-ide domain=omnicompany/core ts=2026-05-01T00:00:00Z type=infrastructure status=active
# [OMNI] summary="omnicompany 当前 session 身份的 Python 接口, 读 hook 写的 _current.txt"
# [OMNI] why="后续注册中心/沙盒/罚单/OmniMark 注入都要拿当前 session ID, 集中走这一份模块避免散落"
# [OMNI] tags=session,registry,identity,omnicompany
# [OMNI] material_id="material:omnicompany.core.session.identity.resolver.py"
"""omnicompany session 身份接口.

跟 ~/.claude/hooks/omnicompany_session_track.py 配合使用. hook 在每次工具调用前
把当前 session ID 写到 `<workspace>/.omni/sessions/_current.txt`, 本模块提供 Python
读取接口.

设计要点:
- 多并发安全 — 每个 Claude Code session 的工具调用各自触发自己的 hook 各自写 _current.txt,
  紧接着 AI IDE 自己跑 Python 时读到的就是自己 session 的 ID.
- 跨 compact 不变 — session ID 在 Claude Code 层面跨 compact 不变, hook 每次刷新
  写的都是同一个 ID.
- 容错 — 找不到文件 / hook 没装 / 读失败时返回 None, 不抛异常 (调用方决定怎么 fallback).
- 路径解析按"workspace 级"走 — 项目内代码用 `find_workspace_root()` 找含 `.omni/sessions/`
  的祖先目录, 跟 hook 写入位置 (`$CLAUDE_PROJECT_DIR`) 对齐.

跟 OmniMark 的关系: stamp 文件头时, `origin` 字段填 `ai-ide`, `agent` 字段填
`ai-ide-<session_id_short>` (调 `current_writer_identity()` 拿). 这样每个文件头能
回溯到具体 session.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


_SESSIONS_SUBPATH = Path(".omni") / "sessions"
_CURRENT_FILENAME = "_current.txt"


def find_workspace_root(start: Path | str | None = None) -> Optional[Path]:
    """从 start 路径向上找含 .omni/sessions/ 的祖先目录, 找不到返回 None.

    优先用环境变量 CLAUDE_PROJECT_DIR (hook 进程里有, 普通 Python 进程一般没有).
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        p = Path(env_dir)
        if (p / _SESSIONS_SUBPATH).is_dir():
            return p

    here = Path(start) if start else Path.cwd()
    here = here.resolve()
    for candidate in [here, *here.parents]:
        if (candidate / _SESSIONS_SUBPATH).is_dir():
            return candidate
    return None


def get_sessions_dir(start: Path | str | None = None) -> Optional[Path]:
    """拿到 .omni/sessions/ 目录的绝对路径. 没找到 workspace root 时返回 None."""
    root = find_workspace_root(start)
    if root is None:
        return None
    return root / _SESSIONS_SUBPATH


def get_current_session_id(start: Path | str | None = None) -> Optional[str]:
    """读当前 session ID. 找不到 workspace 根 / 文件不存在 / 内容为空时返回 None.

    实务上 AI IDE 自己跑这个函数, hook 刚刚写过 _current.txt, 永远拿到当前 session ID.
    """
    sessions_dir = get_sessions_dir(start)
    if sessions_dir is None:
        return None
    current_file = sessions_dir / _CURRENT_FILENAME
    if not current_file.is_file():
        return None
    try:
        content = current_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return content or None


def get_session_info(session_id: str, start: Path | str | None = None) -> Optional[dict]:
    """读已归档 session 的元信息 (SessionStart hook 写的 <session_id>.json).

    找不到对应文件返回 None.
    """
    sessions_dir = get_sessions_dir(start)
    if sessions_dir is None:
        return None
    info_file = sessions_dir / f"{session_id}.json"
    if not info_file.is_file():
        return None
    try:
        return json.loads(info_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_archived_sessions(start: Path | str | None = None) -> list[str]:
    """列出所有已归档的 session ID (按文件修改时间倒序, 最新的在前)."""
    sessions_dir = get_sessions_dir(start)
    if sessions_dir is None:
        return []
    files = [f for f in sessions_dir.glob("*.json") if f.is_file()]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [f.stem for f in files]


def current_writer_identity(start: Path | str | None = None) -> str:
    """返回适合给 OmniMark `agent` / `origin` 字段用的写入者标识.

    格式: `ai-ide-<session_id_short>` 其中 short 是 session ID 的前 8 位.
    Session ID 拿不到时返回 `ai-ide-unknown`.
    """
    sid = get_current_session_id(start)
    if sid is None:
        return "ai-ide-unknown"
    short = sid.split("-")[0] if "-" in sid else sid[:8]
    return f"ai-ide-{short}"
