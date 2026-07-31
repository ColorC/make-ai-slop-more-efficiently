# [OMNI] origin=ai-ide domain=decisions ts=2026-06-18T00:00:00Z type=source status=active
# [OMNI] summary="claude/codex 会话 jsonl → 精简人读对话(只留用户+助手正文,丢工具调用/结果/文件读取等噪声)。供独立抽取 agent 炼决策。"
# [OMNI] why="会话 jsonl 动辄数百 MB,90% 是 tool_result/文件块;喂模型前必须流式抽出人话。确定性解析,不判决策。"
# [OMNI] tags=decisions,sources,conversation
"""会话源读取器 —— 流式把会话 jsonl 精简成人读对话。

condense(path)        → [{role, text, ts}](只 user/assistant 的纯文本块,丢工具噪声)
condense_text(path)   → 带【用户】/【助手】标记的单串(可截断)
vilo_signal(path)     → 粗数 vilo 信号词,判会话是否 vilo 相关(挑批用)
scan_claude_sessions()→ 列本机 claude 会话(路径/大小/信号数),供挑批
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

# 本机 claude 会话根(各项目按 cwd 编码成目录)
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

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
        if isinstance(b, dict) and b.get("type") == "text":
            t = (b.get("text") or "").strip()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _looks_like_injected(text: str) -> bool:
    """像系统注入/工具回显的整块(<system-reminder>/<local-command…>/纯 caveat),非真实人话。"""
    head = text.lstrip()[:60]
    return head.startswith(("<system-reminder", "<local-command", "<command-", "Caveat:"))


def condense(jsonl_path: str | Path) -> list[dict]:
    """流式读 claude 会话 jsonl,产出人读轮次 [{role, text, ts}]。丢工具噪声/系统注入/空块。"""
    p = Path(jsonl_path)
    out: list[dict] = []
    if not p.is_file():
        return out
    with p.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") not in ("user", "assistant"):
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            text = _blocks_text(msg.get("content"))
            if not text or _looks_like_injected(text):
                continue
            out.append({"role": msg.get("role") or d.get("type"),
                        "text": text, "ts": d.get("timestamp", "")})
    return out


def condense_text(jsonl_path: str | Path, max_chars: int = 0) -> str:
    """精简成带角色标记的单串。max_chars>0 时截断(超长会话交给上层分块)。"""
    lines = [f"【{'用户' if t['role'] == 'user' else '助手'}】{t['text']}" for t in condense(jsonl_path)]
    s = "\n\n".join(lines)
    return s[:max_chars] if (max_chars and len(s) > max_chars) else s


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
            yield {"path": str(jf), "session_id": jf.stem, "project_dir": root.name, "size": size}
