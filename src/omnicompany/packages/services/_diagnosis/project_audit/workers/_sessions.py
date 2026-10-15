# [OMNI] origin=claude-code domain=services/_diagnosis/project_audit/workers ts=2026-06-20T00:00:00Z type=lib status=active
# [OMNI] summary="会话日志读取工具 — 统一从 ~/.claude 与 ~/.codex 日志里抽出'我亲口给 agent 的原始 prompt'(A 类真源),过滤系统注入的包装。"
# [OMNI] material_id="material:services._diagnosis.project_audit.workers._sessions"
"""会话日志读取工具(信任层级 A 类真源的入口)。

只认两件事:
1. **我亲口给 agent 的原始 prompt** —— role=user 且不是系统注入的包装。
2. **每条 prompt 所属会话的 cwd** —— 判断它属于哪个项目的硬信号。

两种日志格式统一处理:
- claude code:`{"type":"user","cwd":"...","message":{"role":"user","content": str | [blocks]}}`
- codex:`{"timestamp":..,"type":"response_item|event_msg|session_meta","payload":{...}}`

绝不把以下当成"我的 prompt"(它们是工具/系统/斜杠命令注入的):
以 `<` 开头的标签块、`Caveat:`、`[Request interrupted`、tool_result、压缩续接摘要、命令回显。
"""
from __future__ import annotations

import glob
import json
import os
import re
from typing import Iterator

# 系统注入 / 非"我亲口"的前缀,命中即丢
_WRAPPER_PREFIXES = (
    "<", "caveat:", "[request interrupted", "this session is being continued",
    "请继续", "command-name", "api error", "[image]",
)
_WRAPPER_SUBSTR = (
    "local-command-stdout", "command-message", "<command-name>",
    "<local-command-caveat>", "tool_use_error",
)

_CWD_TAG = re.compile(r"<cwd>\s*(.+?)\s*</cwd>", re.S)


def default_session_roots() -> list[str]:
    return [
        os.path.expanduser("~/.claude/projects"),
        os.path.expanduser("~/.codex/sessions"),
        os.path.expanduser("~/.codex/archived_sessions"),
    ]


def iter_session_files(roots: list[str] | None = None) -> Iterator[str]:
    """枚举全部会话 jsonl(递归)。"""
    for root in (roots or default_session_roots()):
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        for fp in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            yield fp


def _norm_path(p: str | None) -> str:
    return (p or "").replace("\\", "/").rstrip("/").lower()


def _is_human_prompt(text: str) -> bool:
    """判断一段文本是否是'我亲口给 agent 的原始 prompt'(而非系统/工具注入)。"""
    if not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 4:
        return False
    low = t.lower()
    if low.startswith(_WRAPPER_PREFIXES):
        return False
    if any(s in low[:200] for s in _WRAPPER_SUBSTR):
        return False
    return True


def _content_to_text(content) -> str:
    """把 message.content(str 或 block 列表)归一成纯文本,跳过 tool_result 等非文本块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt in ("text", "input_text"):
                parts.append(b.get("text") or "")
            # 故意跳过 tool_result / tool_use / image
        return "\n".join(p for p in parts if p)
    return ""


def _claude_records(fp: str) -> Iterator[dict]:
    cwd = ""
    for ln in _read_lines(fp):
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if isinstance(o, dict) and o.get("cwd"):
            cwd = o["cwd"]
        if not (isinstance(o, dict) and o.get("type") == "user"):
            continue
        msg = o.get("message") or {}
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _content_to_text(msg.get("content"))
        if _is_human_prompt(text):
            yield {"text": text.strip(), "cwd": o.get("cwd") or cwd, "ts": o.get("timestamp"), "source": fp}


def _codex_records(fp: str) -> Iterator[dict]:
    cwd = ""
    for ln in _read_lines(fp):
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if not isinstance(o, dict):
            continue
        payload = o.get("payload") if isinstance(o.get("payload"), dict) else o
        # 抓会话 cwd(session_meta 或 environment_context)
        if o.get("type") == "session_meta" or payload.get("type") == "session_meta":
            cwd = payload.get("cwd") or payload.get("cwd_path") or cwd
        role = payload.get("role")
        ptype = payload.get("type")
        if role == "user" or ptype in ("user_message", "message"):
            text = _content_to_text(payload.get("content") or payload.get("message"))
            # environment_context 里夹带 cwd,顺手抓
            m = _CWD_TAG.search(text or "")
            if m:
                cwd = m.group(1)
            if role == "user" and _is_human_prompt(text):
                yield {"text": text.strip(), "cwd": cwd, "ts": o.get("timestamp"), "source": fp}


def _read_lines(fp: str) -> Iterator[str]:
    try:
        with open(fp, encoding="utf-8", errors="ignore") as f:
            for ln in f:
                yield ln
    except Exception:
        return


def iter_user_prompts(fp: str) -> Iterator[dict]:
    """从一个会话文件抽出全部'我的原始 prompt',自动识别 claude / codex 格式。"""
    base = os.path.basename(fp).lower()
    is_codex = base.startswith("rollout-") or os.sep + "sessions" in fp or "archived_sessions" in fp
    yield from (_codex_records(fp) if is_codex else _claude_records(fp))


def first_cwd(fp: str, max_lines: int = 60) -> str:
    """廉价地从一个会话文件早段读出它的 cwd(claude 顶层 cwd / codex session_meta / environment_context)。"""
    try:
        with open(fp, encoding="utf-8", errors="ignore") as f:
            for i, ln in enumerate(f):
                if i >= max_lines:
                    break
                if "cwd" not in ln:
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                if isinstance(o, dict):
                    if o.get("cwd"):
                        return o["cwd"]
                    payload = o.get("payload") if isinstance(o.get("payload"), dict) else {}
                    if payload.get("cwd"):
                        return payload["cwd"]
                    # codex environment_context 文本里的 <cwd>
                    content = payload.get("content")
                    txt = _content_to_text(content) if content else ""
                    m = _CWD_TAG.search(txt)
                    if m:
                        return m.group(1)
    except Exception:
        return ""
    return ""


def file_mentions(fp: str, tokens: list[str], cap_bytes: int = 8_000_000) -> bool:
    """廉价预筛:整文件原始字节里是否出现任一 token(避免对 1.6GB 全量 json 解析)。"""
    toks = [t.lower() for t in tokens if t]
    if not toks:
        return True
    try:
        with open(fp, encoding="utf-8", errors="ignore") as f:
            chunk = f.read(cap_bytes)
        low = chunk.lower()
        return any(t in low for t in toks)
    except Exception:
        return False
