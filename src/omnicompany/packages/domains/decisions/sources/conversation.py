# [OMNI] origin=ai-ide domain=decisions ts=2026-06-18T00:00:00Z type=source status=active
# [OMNI] summary="claude/codex 会话 jsonl → 精简人读对话(只留用户+助手正文,丢工具调用/结果/文件读取等噪声)。供独立抽取 agent 炼决策。"
# [OMNI] why="会话 jsonl 动辄数百 MB,90% 是 tool_result/文件块;喂模型前必须流式抽出人话。确定性解析,不判决策。"
# [OMNI] tags=decisions,sources,conversation
"""会话源读取器 —— 流式把 Claude/Codex 会话 jsonl 精简成人读对话。

condense(path)        → [{role, text, ts}](只 user/assistant 的纯文本块,丢工具噪声)
condense_text(path)   → 带【用户】/【助手】标记的单串(可截断)
vilo_signal(path)     → 粗数 vilo 信号词,判会话是否 vilo 相关(挑批用)
scan_sessions()       → 列本机 Claude/Codex 主会话,供统一抽取
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

# 本机 claude 会话根(各项目按 cwd 编码成目录)
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"

# vilo 相关信号词(粗判会话主题)
_VILO_SIGNALS = ("vilo", "薇洛", "vilo-wants-to-know", "tabletop", "密教模拟", "又一天", "苏丹的游戏", "recipe骨架")


def _blocks_text(content) -> str:
    """从 message.content 取纯文本。content 可能是 str 或 block 列表;只留 type=text,丢 tool_use/tool_result/thinking/image。"""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text"):
            t = (b.get("text") or "").strip()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _looks_like_injected(text: str) -> bool:
    """像系统注入/工具回显的整块(<system-reminder>/<local-command…>/纯 caveat),非真实人话。"""
    head = text.lstrip()[:60]
    return head.startswith((
        "<system-reminder", "<local-command", "<command-", "Caveat:",
        "<environment_context", "<recommended_plugins", "<in-app-browser-context",
        "<permissions instructions", "<skills_instructions", "<apps_instructions",
        "<plugins_instructions", "<multi_agent_mode",
    ))


def _message_text(content) -> str:
    """逐 block 丢掉环境/插件等注入,保留同一 message 中真实用户文字。"""
    if isinstance(content, str):
        return "" if _looks_like_injected(content) else content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in ("text", "input_text", "output_text"):
            continue
        text = (block.get("text") or "").strip()
        if text and not _looks_like_injected(text):
            parts.append(text)
    return "\n".join(parts)


def _parse_message(d: dict, *, source: str) -> dict | None:
    """把单行 Claude/Codex 事件归一成 {role,text,ts};非正文事件返回 None。"""
    if source == "codex":
        if d.get("type") != "response_item":
            return None
        msg = d.get("payload") or {}
        if msg.get("type") != "message" or msg.get("role") not in ("user", "assistant"):
            return None
        text = _message_text(msg.get("content"))
        if not text:
            return None
        return {"role": msg["role"], "text": text, "ts": d.get("timestamp", "")}

    if d.get("type") not in ("user", "assistant"):
        return None
    msg = d.get("message")
    if not isinstance(msg, dict):
        return None
    text = _message_text(msg.get("content"))
    if not text:
        return None
    return {"role": msg.get("role") or d.get("type"), "text": text, "ts": d.get("timestamp", "")}


def _detect_source(path: Path) -> str:
    return "codex" if "rollout-" in path.name or ".codex" in str(path).lower() else "claude"


def condense_from_offset(jsonl_path: str | Path, start_offset: int = 0) -> tuple[list[dict], int]:
    """从 JSONL 字节偏移增量读取正文,返回(轮次,读取结束偏移)。"""
    p = Path(jsonl_path)
    if not p.is_file():
        return [], 0
    source = _detect_source(p)
    size = p.stat().st_size
    offset = start_offset if 0 <= start_offset <= size else 0
    out: list[dict] = []
    with p.open("rb") as f:
        f.seek(offset)
        for raw in f:
            try:
                d = json.loads(raw.decode("utf-8", errors="ignore"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            msg = _parse_message(d, source=source)
            if msg:
                out.append(msg)
        end_offset = f.tell()
    return out, end_offset


def condense(jsonl_path: str | Path) -> list[dict]:
    """流式读 Claude/Codex 会话,只产出真实用户与助手正文。"""
    return condense_from_offset(jsonl_path)[0]


def condense_text(jsonl_path: str | Path, max_chars: int = 0) -> str:
    """精简成带角色标记的单串。max_chars>0 时截断(超长会话交给上层分块)。"""
    lines = [f"【{'用户' if t['role'] == 'user' else '助手'}】{t['text']}" for t in condense(jsonl_path)]
    s = "\n\n".join(lines)
    return s[:max_chars] if (max_chars and len(s) > max_chars) else s


def condense_text_from_offset(jsonl_path: str | Path, start_offset: int = 0) -> tuple[str, int]:
    """增量版 condense_text,供 append-only 会话断点续抽。"""
    turns, end_offset = condense_from_offset(jsonl_path, start_offset)
    text = "\n\n".join(
        f"【{'用户' if t['role'] == 'user' else '助手'}】{t['text']}" for t in turns
    )
    return text, end_offset


def vilo_signal(jsonl_path: str | Path) -> int:
    """流式粗数 vilo 信号词命中行数(判会话是否 vilo 相关)。"""
    p = Path(jsonl_path)
    if not p.is_file():
        return 0
    n = 0
    with p.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            low = line.lower()
            if any(s in low for s in _VILO_SIGNALS):
                n += 1
    return n


def scan_claude_sessions(project_dir: str | Path | None = None) -> Iterator[dict]:
    """列 claude 会话:{path, session_id, size, vilo_signal}。project_dir 缺省扫全部项目目录。"""
    roots = [Path(project_dir)] if project_dir else (
        [d for d in CLAUDE_PROJECTS.iterdir() if d.is_dir()] if CLAUDE_PROJECTS.is_dir() else [])
    for root in roots:
        for jf in root.glob("*.jsonl"):
            try:
                size = jf.stat().st_size
            except OSError:
                continue
            yield {"path": str(jf), "session_id": jf.stem, "project_dir": root.name,
                   "size": size, "source": "claude"}


def _codex_meta(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            first = json.loads(next(f))
        if first.get("type") == "session_meta":
            return first.get("payload") or {}
    except (OSError, StopIteration, json.JSONDecodeError):
        pass
    return {}


def scan_codex_sessions() -> Iterator[dict]:
    """列 Codex 根会话;排除子 agent rollout,避免把同一工作重复抽成多份。"""
    if not CODEX_SESSIONS.is_dir():
        return
    for jf in CODEX_SESSIONS.rglob("rollout-*.jsonl"):
        meta = _codex_meta(jf)
        if meta.get("thread_source") == "subagent" or isinstance(meta.get("source"), dict):
            continue
        try:
            size = jf.stat().st_size
        except OSError:
            continue
        session_id = meta.get("id") or meta.get("session_id") or jf.stem
        yield {"path": str(jf), "session_id": session_id, "project_dir": meta.get("cwd", ""),
               "size": size, "source": "codex"}


def scan_sessions(*, source: str = "claude") -> Iterator[dict]:
    """统一列会话。source=claude|codex|all。"""
    if source in ("claude", "all"):
        yield from scan_claude_sessions()
    if source in ("codex", "all"):
        yield from scan_codex_sessions()
