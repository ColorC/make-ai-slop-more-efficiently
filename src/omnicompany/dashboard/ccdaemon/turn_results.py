"""本轮结果: 从会话转录里取「最后一轮」产出的 Workshop 画布和本机文件。

2026-11-20 业主：「每次会话结束后会在旁边显示本次的结果画布和内容，也可以远程访问
本机的内容（而不是发了本地路径就只能干瞪眼）」。

一轮 = 最后一条真正由人提交的消息之后的全部记录。画布链接从助手正文和工具输出里都认
(wsh 写完卡会把链接打在工具输出里); 本机路径只认助手正文 —— 工具调用里读写过的文件
成百上千, 那些不是给人看的结果。

这里只做纯解析, 不碰 HTTP 和鉴权; 签发文件令牌由路由按机主设备决定。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlsplit

# 转录可能有几十 MB; 一轮的内容几乎总在末尾几 MB 里。
TAIL_BYTES = 6 * 1024 * 1024
MAX_CANVASES = 24
MAX_FILES = 24
MAX_PATH_CANDIDATES = 64

_STOP = "\\s\"'<>`|\u3000-\u303f\uff00-\uffef"
_CANVAS_URL = re.compile(
    r"https?://[^" + _STOP + r"()\[\]]+?/canvas(?:/notes-web\.html)?\?[^" + _STOP + r"()\[\]]+"
)
# 本机绝对路径: 盘符开头; 遇到空白、引号、反引号、全角标点即止。
_WIN_PATH = re.compile(r"(?<![A-Za-z0-9_/\\])(?:file:///)?([A-Za-z]:[\\/][^" + _STOP + r"*?\r\n]*)")
_BACKTICK = re.compile(r"`([^`\r\n]{3,400})`")
_TRAILING = ".,;:!?)]}>*_~"


@dataclass
class TurnTexts:
    assistant: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    prompt: str = ""
    turn_key: str = ""


# ── 转录 → 最后一轮 ────────────────────────────────────────────────────────

def _read_tail_lines(path: Path, tail_bytes: int = TAIL_BYTES) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > tail_bytes:
            handle.seek(size - tail_bytes)
            handle.readline()  # 丢掉被截断的半行
        data = handle.read()
    return data.decode("utf-8", errors="replace").splitlines()


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _claude_user_prompt(record: dict) -> str | None:
    """Claude Code: 人提交的消息(不是工具结果、不是注入的元消息)。"""
    if record.get("type") != "user" or record.get("isMeta") or record.get("isSidechain"):
        return None
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(
            str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return None
    text = text.strip()
    # 后台任务完成通知、本地命令回显、系统提醒都是注入的, 不是人这一轮说的话。
    if not text or text.startswith(("<local-command-stdout>", "<system-reminder>", "<task-notification>")):
        return None
    return text


def _codex_user_prompt(record: dict) -> str | None:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else None
    if not payload:
        return None
    if record.get("type") == "event_msg" and payload.get("type") == "user_message":
        return str(payload.get("message") or "").strip() or None
    return None


def _assistant_texts(record: dict) -> list[str] | None:
    """助手正文; 不是助手消息时返回 None。"""
    if record.get("type") == "assistant":
        content = (record.get("message") or {}).get("content")
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            return [
                str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            ]
        return []
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else None
    if payload and record.get("type") == "response_item" and payload.get("type") == "message" \
            and payload.get("role") == "assistant":
        return [
            str(b.get("text") or "")
            for b in payload.get("content") or []
            if isinstance(b, dict) and b.get("type") in {"output_text", "text"}
        ]
    if payload and record.get("type") == "event_msg" and payload.get("type") == "agent_message":
        return [str(payload.get("message") or "")]
    return None


def last_turn_from_lines(lines: list[str], *, whole: bool = False) -> TurnTexts:
    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    start = 0
    prompt = ""
    turn_key = ""
    for index in range(len(records) - 1, -1, -1):
        record = records[index]
        text = _claude_user_prompt(record) or _codex_user_prompt(record)
        if text:
            start = index + 1
            prompt = text
            turn_key = str(record.get("uuid") or record.get("timestamp") or index)
            break
    if whole:
        # 整个会话(转录末尾那几 MB): 结果页的「关键内容」要跨轮保留。
        start = 0
    turn = TurnTexts(prompt=prompt[:400], turn_key=turn_key)
    seen_assistant: set[str] = set()
    wsh_calls: set[str] = set()
    for record in records[start:]:
        texts = _assistant_texts(record)
        if texts is not None:
            for text in texts:
                # Codex 同一句话会在 response_item 和 agent_message 各出现一次。
                if text and text not in seen_assistant:
                    seen_assistant.add(text)
                    turn.assistant.append(text)
            if record.get("type") == "assistant":
                content = (record.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            if _is_wsh_write(" ".join(_strings(block.get("input")))):
                                wsh_calls.add(str(block.get("id") or ""))
            continue
        # 工具输出里的画布链接只认 wsh 写入命令打出来的(写完卡给的链接);
        # 读文件、grep 记忆翻出来的旧链接不是这一轮的结果。
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if payload.get("type") in {"function_call", "custom_tool_call", "local_shell_call"}:
            if _is_wsh_write(" ".join(_strings(payload.get("arguments") or payload.get("input") or payload.get("action")))):
                wsh_calls.add(str(payload.get("call_id") or ""))
            continue
        if payload.get("type") in {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}:
            if str(payload.get("call_id") or "") in wsh_calls:
                turn.tools.extend(_strings(payload.get("output")))
            continue
        content = (record.get("message") or {}).get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" \
                        and str(block.get("tool_use_id") or "") in wsh_calls:
                    turn.tools.extend(_strings(block.get("content")))
    return turn


_WSH_WRITE = re.compile(r"\bwsh(?:\.mjs)?\b[^\n]*\b(?:apply|add|put|set|create|write|new|update|card|upload|bind|patch|design)\b")


def _is_wsh_write(command: str) -> bool:
    return bool(_WSH_WRITE.search(command or ""))


def last_turn(path: Path, *, whole: bool = False) -> TurnTexts:
    return last_turn_from_lines(_read_tail_lines(path), whole=whole)


# ── 文字 → 画布 / 路径 ─────────────────────────────────────────────────────

def _strip_trailing(value: str) -> str:
    while value and value[-1] in _TRAILING:
        value = value[:-1]
    return value


_GLUED_URL = re.compile(r"(?<=\S)(https?://)")
_IDENT = re.compile(r"^[A-Za-z0-9_.:\-]+")


def _ident(value: str) -> str:
    match = _IDENT.match(value or "")
    return match.group(0) if match else ""


def canvas_refs(texts: Iterable[str]) -> list[dict[str, str]]:
    """Workshop 画布链接 → {project, canvas, card, url}; 同一张卡只留最后一次提到的。"""
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    for text in texts:
        # 转录里常有两条链接首尾相连(JSON 串、表格): 先在每个 http(s):// 前补空格拆开。
        text = _GLUED_URL.sub(lambda m: " " + m.group(1), text)
        for match in _CANVAS_URL.finditer(text):
            url = _strip_trailing(match.group(0))
            try:
                parts = urlsplit(url)
            except ValueError:
                continue
            query = parse_qs(parts.query)
            canvas = (query.get("canvas") or query.get("session") or [""])[0].strip()
            if not canvas:
                continue
            project = (query.get("project") or [""])[0].strip()
            card = (query.get("card") or query.get("focus") or [""])[0].strip()
            fragment = unquote(parts.fragment or "")
            if not card and fragment:
                card = fragment[5:] if fragment.startswith("card=") else fragment
            canvas, project, card = (_ident(v) for v in (canvas, project, card))
            if not canvas:
                continue
            key = (project, canvas, card)
            found.pop(key, None)
            found[key] = {"project": project, "canvas": canvas, "card": card, "url": url}
    refs = list(found.values())
    refs.reverse()  # 最近提到的在前
    return refs[:MAX_CANVASES]


def _path_candidates(text: str) -> list[str]:
    out: list[str] = []
    for match in _BACKTICK.finditer(text):
        inner = match.group(1).strip()
        if re.match(r"^(?:file:///)?[A-Za-z]:[\\/]", inner):
            out.append(inner)
    for match in _WIN_PATH.finditer(text):
        out.append(match.group(1))
    return out


def normalize_local_path(raw: str) -> str | None:
    value = (raw or "").strip().strip("\"'`")
    if value.lower().startswith("file:///"):
        value = unquote(value[8:])
    value = _strip_trailing(value)
    # 行号后缀 path:12 / path:12:3
    value = re.sub(r"(\.[A-Za-z0-9]{1,8}):\d+(?::\d+)?$", lambda m: m.group(1), value)
    if not re.match(r"^[A-Za-z]:[\\/]", value) or "\x00" in value:
        return None
    return os.path.normpath(value)


def _existing(path: str) -> str | None:
    # 路径后面紧跟中文正文时(「见 E:\\a\\b.png然后…」)逐字退掉非 ASCII 尾巴再试。
    candidate = path
    for _ in range(24):
        if os.path.exists(candidate):
            return candidate
        if not candidate or ord(candidate[-1]) < 128:
            return None
        candidate = _strip_trailing(candidate[:-1])
    return None


def local_paths(texts: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    checked = 0
    for text in texts:
        for raw in _path_candidates(text):
            normalized = normalize_local_path(raw)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                seen[key] = seen.pop(key)  # 挪到最后 = 最近提到
                continue
            checked += 1
            if checked > MAX_PATH_CANDIDATES:
                break
            existing = _existing(normalized)
            if existing and existing.lower() not in seen:
                seen[existing.lower()] = existing
    paths = list(seen.values())
    paths.reverse()
    # 同时提到目录和目录里的文件时, 文件更具体, 目录排后。
    paths.sort(key=lambda p: os.path.isdir(p))
    return paths[:MAX_FILES]


def _merge_canvases(primary: list[dict[str, str]], secondary: list[dict[str, str]]) -> list[dict[str, str]]:
    """助手正文里提到的排前面, wsh 输出里补充的排后面。"""
    seen = {(c["project"], c["canvas"], c["card"]) for c in primary}
    merged = list(primary)
    for ref in secondary:
        key = (ref["project"], ref["canvas"], ref["card"])
        if key not in seen:
            seen.add(key)
            merged.append(ref)
    return merged[:MAX_CANVASES]


_MEDIA_URL = re.compile(
    r"https?://[^" + _STOP + r"()\[\]]+?\.(?:png|jpe?g|gif|webp|svg|avif|mp4|webm|mov|m4v)(?:\?[^" + _STOP + r"()\[\]]*)?",
    re.IGNORECASE,
)
_VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")
MAX_MEDIA = 24


def media_urls(texts: Iterable[str]) -> list[dict[str, str]]:
    """回复里直接给出的图片 / 视频网址; 最近提到的在前。"""
    seen: dict[str, dict[str, str]] = {}
    for text in texts:
        text = _GLUED_URL.sub(lambda m: " " + m.group(1), text)
        for match in _MEDIA_URL.finditer(text):
            url = _strip_trailing(match.group(0))
            path = url.split("?", 1)[0].lower()
            seen.pop(url, None)
            seen[url] = {"url": url, "kind": "video" if path.endswith(_VIDEO_EXT) else "image"}
    items = list(seen.values())
    items.reverse()
    return items[:MAX_MEDIA]


def turn_results_from_texts(turn: TurnTexts, *, limit: int | None = None) -> dict[str, Any]:
    canvases = _merge_canvases(canvas_refs(turn.assistant), canvas_refs(turn.tools))
    return {
        "turn_key": turn.turn_key,
        "prompt": turn.prompt,
        "canvases": canvases,
        "paths": local_paths(turn.assistant),
        "media": media_urls(turn.assistant),
    }


# ── 会话 → 本轮结果(给路由用) ─────────────────────────────────────────────

_TURN_RESULTS_CACHE: dict[str, tuple[tuple[str, float, int], dict[str, Any]]] = {}


def claude_session_for_pid(pid: int | None, sessions_dir: Path | None = None) -> str | None:
    """Claude Code 把「进程号 → 会话号」写在 ~/.claude/sessions/<pid>.json。

    会话钩子没装时登记表里没有原生会话号; 这里按终端子进程及其后代的进程号去找。
    """
    if not pid:
        return None
    root = sessions_dir or Path.home() / ".claude" / "sessions"
    pids = [int(pid)]
    try:
        import psutil

        pids.extend(child.pid for child in psutil.Process(int(pid)).children(recursive=True))
    except Exception:
        pass
    for candidate in pids:
        try:
            record = json.loads((root / f"{candidate}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        session_id = str(record.get("sessionId") or "").strip() if isinstance(record, dict) else ""
        if session_id:
            return session_id
    return None


def resolve_session_transcript(sid: str) -> tuple[str, Path | None, str]:
    """会话 → (provider, 转录路径, 读不到的原因)。本轮结果与本轮复核共用。"""
    from .pty import _conversation_jsonl_for, _read_meta_store, get_manager

    meta = dict((_read_meta_store() or {}).get(sid) or {})
    # 运行中的会话先认内存里的会话对象: 原生会话号是启动后才链上的, 注册表里常常还没有。
    try:
        live = get_manager().get(sid)
    except Exception:
        live = None
    if live is not None:
        for name in ("provider", "cwd", "provider_session_id", "claude_session_id"):
            value = getattr(live, name, None)
            if value:
                meta[name] = value
    provider = str(meta.get("provider") or "")
    native_id = str(meta.get("provider_session_id") or meta.get("claude_session_id") or "")
    # Claude 续接 / 压缩后会换会话号, 登记里的可能是旧号: 进程当前写的会话号优先。
    if live is not None and provider.startswith("claude"):
        native_id = claude_session_for_pid(getattr(live, "child_pid", None)) or native_id
    if not meta:
        return provider, None, "session-unknown"
    transcript = _conversation_jsonl_for(provider or None, str(meta.get("cwd") or ""), native_id or None)
    if transcript is None or not Path(transcript).is_file():
        return provider, None, "transcript-missing"
    path = Path(transcript)
    if path.suffix.lower() != ".jsonl":
        return provider, None, "transcript-unsupported"
    return provider, path, ""


def session_turn_results(sid: str, owner_device: bool, scope: str = "turn") -> dict[str, Any]:
    whole = scope == "session"
    provider, path, unavailable = resolve_session_transcript(sid)
    base: dict[str, Any] = {
        "session_id": sid,
        "provider": provider,
        "owner_device": owner_device,
        "turn_key": "",
        "prompt": "",
        "canvases": [],
        "files": [],
        "media": [],
        "scope": "session" if whole else "turn",
    }
    if path is None:
        return {**base, "unavailable": unavailable}
    stat = path.stat()
    key = (str(path), stat.st_mtime, stat.st_size)
    cache_key = f"{sid}:{'session' if whole else 'turn'}"
    cached = _TURN_RESULTS_CACHE.get(cache_key)
    if cached and cached[0] == key:
        parsed = cached[1]
    else:
        parsed = turn_results_from_texts(last_turn(path, whole=whole))
        _TURN_RESULTS_CACHE[cache_key] = (key, parsed)

    from omnicompany.dashboard.controlplane import overlay_notes as on

    files: list[dict[str, Any]] = []
    for raw in parsed["paths"]:
        item_path = Path(raw)
        try:
            is_dir = item_path.is_dir()
            mime = "inode/directory" if is_dir else on._file_mime(item_path)
            item: dict[str, Any] = {
                "name": item_path.name or raw,
                "path": raw,
                "kind": "folder" if is_dir else "file",
                "preview": "folder" if is_dir else on._file_preview_kind(item_path, mime),
                "mime": mime,
                "size": 0 if is_dir else item_path.stat().st_size,
                "open_token": None,
            }
        except OSError:
            continue
        if owner_device:
            try:
                item["open_token"] = on._sign_file_path(raw)
            except Exception:  # 本机没有文件索引令牌时只给路径
                item["open_token"] = None
        files.append(item)
    return {
        **base,
        "turn_key": parsed["turn_key"],
        "prompt": parsed["prompt"],
        "canvases": parsed["canvases"],
        "files": files,
        "media": parsed["media"],
        "transcript_mtime": stat.st_mtime,
    }
