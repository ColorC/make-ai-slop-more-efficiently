# [OMNI] origin=claude domain=dashboard/controlplane ts=2026-11-16 type=route status=active
# [OMNI] summary="胡闹游戏 Studio 页的后端: 员工配置、群聊/私聊读写、hunao-games 名下会话列表与归属、老板消息投递到员工会话。"
# [OMNI] why="Studio 页(iframe /api/embed/studio)与员工命令 hn.py 共用 company/chat/messages.jsonl; 约定见 hunao-games/docs/studio-api.md 第 2 节。"
"""/api/studio/* —— 胡闹游戏 Studio 的后端接口。

约定的唯一出处: ``C:/workspace/hunao-games/docs/studio-api.md``。

数据来源:
  · 员工配置与聊天文件: ``HUNAO_COMPANY_DIR``(默认 hunao-games/company)。聊天文件的另一个
    写方是 ``hunao-games/bin/hn.py``, 这里的写法(追加模式、一次一整行、LF、立即刷新)与它一致。
  · 会话运行态: ccdaemon ``GET /cc/sessions``(与会话列表页 / ``/api/cc/tab-states`` 同一份
    进程内快照); ``working/done/ended/unknown`` 的判定复用 ``session_attention.pty_run_state``
    (与前端 tabStatus.ts 对齐), 「跑完一轮还没被看」复用收件箱的已看记录。
  · 项目归属: 绑定台账 ``data/cc_session_bindings.json`` 的 ``project`` 字段(identity resolver)。
  · 投递: ccdaemon ``POST /cc/sessions/{id}/input``, 纯文本 + submit, 不是 agent 信封。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

studio_router = APIRouter(prefix="/api/studio", tags=["studio"])

DEFAULT_COMPANY_DIR = "C:/workspace/hunao-games/company"
DEFAULT_PROJECT = "hunao-games"
TZ = _dt.timezone(_dt.timedelta(hours=8))
STUDIO_TOPIC_PREFIX = "studio:"
SESSIONS_LIMIT = 50
CHAT_LIMIT_DEFAULT = 200
CHAT_LIMIT_MAX = 1000
#: ccdaemon 输入接口上限 12000 字符, 前缀另占几个字。
TEXT_MAX = 8000
_MESSAGE_ID = re.compile(r"^m-\d+-[0-9a-f]+$")
_append_lock = threading.Lock()


# ─── 数据文件 ────────────────────────────────────────────────────────────

def company_dir() -> Path:
    return Path(os.environ.get("HUNAO_COMPANY_DIR") or DEFAULT_COMPANY_DIR)


def chat_path() -> Path:
    return company_dir() / "chat" / "messages.jsonl"


def load_company() -> dict[str, Any]:
    return json.loads((company_dir() / "staff.json").read_text(encoding="utf-8"))


def project_id(company: dict[str, Any]) -> str:
    return str(company.get("project") or DEFAULT_PROJECT)


def staff_list(company: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in company.get("staff") or [] if isinstance(s, dict) and s.get("id")]


def valid_channel(channel: str, company: dict[str, Any]) -> bool:
    if channel == "group":
        return True
    if channel.startswith("dm:"):
        return channel[3:] in {str(s["id"]) for s in staff_list(company)}
    return False


def read_messages(channel: str) -> list[dict[str, Any]]:
    """读某频道全部消息, 按 id 正序。最后一行写到一半(或任何坏行)直接跳过。"""
    path = chat_path()
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("channel") == channel:
            out.append(msg)
    out.sort(key=lambda m: str(m.get("id") or ""))
    return out


def append_boss_message(channel: str, text: str) -> dict[str, Any]:
    now = _dt.datetime.now(TZ)
    msg = {
        "id": f"m-{int(time.time() * 1000)}-{secrets.token_hex(2)}",
        "ts": now.isoformat(timespec="seconds"),
        "channel": channel,
        "from": "boss",
        "text": text,
        "session": None,
        "via": "web",
    }
    path = chat_path()
    with _append_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(msg, ensure_ascii=False) + "\n")
            handle.flush()
    return msg


def mentioned_staff(text: str, company: dict[str, Any]) -> list[str]:
    """群聊点名: ``@<name>``(不分大小写) 或 ``@所有人``。按 staff.json 顺序返回员工 id。"""
    staff = staff_list(company)
    if "@所有人" in text:
        return [str(s["id"]) for s in staff]
    out: list[str] = []
    for s in staff:
        name = str(s.get("name") or "").strip()
        # 前面紧挨字母数字的不算(邮箱 a@gpt.com), 后面紧跟字母数字的也不算(@Claudette)。
        if name and re.search(r"(?<![A-Za-z0-9_.])@" + re.escape(name) + r"(?![A-Za-z0-9_])", text, re.IGNORECASE):
            out.append(str(s["id"]))
    return out


def delivery_plan(channel: str, text: str, company: dict[str, Any]) -> list[tuple[str, str]]:
    """(员工 id, 投进会话输入的文字)。群聊没点名就不投。"""
    if channel.startswith("dm:"):
        return [(channel[3:], f"【老板私聊】{text}")]
    return [(sid, f"【群聊 @你】{text}") for sid in mentioned_staff(text, company)]


# ─── 会话 ──────────────────────────────────────────────────────────────

def _iso(value: Any) -> str | None:
    """epoch 秒或 ISO 串 → 东八区 ISO; 拿不到就 None。"""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            if value <= 0:
                return None
            moment = _dt.datetime.fromtimestamp(float(value), TZ)
        else:
            moment = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=_dt.timezone.utc)
            moment = moment.astimezone(TZ)
    except (ValueError, OverflowError, OSError):
        return None
    return moment.isoformat(timespec="seconds")


def _first_line(value: Any) -> str:
    for part in str(value or "").splitlines():
        part = " ".join(part.split())
        if part:
            return part
    return ""


def _title(item: dict[str, Any], binding: dict[str, Any], provider: str, sid: str) -> str:
    """同前端 sessionProjectionTitle 的退化链(topic → 摘要标题 → 原生标题), 但
    ``studio:<员工>`` 这种机器 topic 不当标题。"""
    topic = item.get("topic") or binding.get("topic")
    candidates = [] if str(topic or "").startswith(STUDIO_TOPIC_PREFIX) else [topic]
    candidates += [item.get("summary_title"), item.get("display_title"), item.get("provider_title")]
    for value in candidates:
        line = _first_line(value)
        if line:
            return line[:200]
    return f"{provider or 'Agent'} · {sid[:8]}"


def _model_from_cmd(cmd: Any) -> str | None:
    if not isinstance(cmd, list):
        return None
    for index, arg in enumerate(cmd[:-1]):
        if arg in ("--model", "-m"):
            return str(cmd[index + 1])
    return None


def _staff_for(topic: str, provider: str, company: dict[str, Any]) -> str | None:
    staff = staff_list(company)
    if topic.startswith(STUDIO_TOPIC_PREFIX):
        wanted = topic[len(STUDIO_TOPIC_PREFIX):]
        if any(str(s["id"]) == wanted for s in staff):
            return wanted
    for s in staff:
        if provider and str(s.get("provider") or "") == provider:
            return str(s["id"])
    return None


def _status(item: dict[str, Any] | None, *, daemon_ok: bool, seen: dict[str, str]) -> str:
    """working / waiting / idle / dead / unknown。

    底层四态与会话页签同源(session_attention.pty_run_state ≡ 前端 ptyTabRunState):
      ended → dead; unknown → unknown; working → working;
      done 再分: 跑完过一轮且这一轮还没被人点开看过(收件箱已看记录) → waiting, 其余 → idle。
    """
    if item is None:
        return "dead" if daemon_ok else "unknown"
    from omnicompany.packages.services._focus import session_attention as attention

    state = attention.pty_run_state(item)
    if state == "ended":
        return "dead"
    if state in ("working", "unknown"):
        return state
    if not item.get("has_user_turn"):
        return "idle"
    sid = str(item.get("id") or "")
    fingerprint = attention.completion_fingerprint(sid, {
        "kind": "pty",
        "startedAt": item.get("started_at"),
        "hasUserTurn": True,
        "lastSubmitAt": item.get("last_submit_at"),
        "lastMessage": "",
    })
    if not fingerprint:
        return "idle"
    seen_fp = seen.get(sid)
    if seen_fp == fingerprint:
        return "idle"
    legacy_row = {"kind": "pty", "completionVersion": item.get("last_submit_at"), "startedAt": item.get("started_at")}
    if attention._legacy_pty_seen_covers(sid, legacy_row, seen_fp):  # noqa: SLF001 - same seen-ledger rule as the inbox
        return "idle"
    return "waiting"


def _last_active(item: dict[str, Any]) -> float:
    values = [item.get(key) for key in ("last_submit_at", "last_output_at", "last_activity_at", "started_at")]
    numbers = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]
    return max(numbers) if numbers else 0.0


async def _fetch_daemon_sessions() -> list[dict[str, Any]] | None:
    """ccdaemon 的会话目录(活的 + 可恢复 + 记得的已结束); daemon 不可用返回 None。"""
    from .cc_proxy import _daemon_http_base, _daemon_http_client

    try:
        base = await asyncio.to_thread(_daemon_http_base)
        client = await _daemon_http_client()
        resp = await client.request(method="GET", url=f"{base}/cc/sessions")
        if not resp.is_success:
            return None
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - daemon 缺席时列表退化成只读台账
        logger.info("studio: ccdaemon session list unavailable: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for key in ("items", "recoverable", "remembered"):
        for item in payload.get(key) or []:
            sid = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                out.append(item)
    return out


def _binding_for(item: dict[str, Any]) -> dict[str, Any]:
    from omnicompany.packages.services._core.identity import binding_for_session

    sid = str(item.get("id") or "")
    return binding_for_session(
        trace_id=sid,
        pty_id=sid,
        provider=str(item.get("provider") or "") or None,
        session_id=str(item.get("provider_session_id") or item.get("claude_session_id") or "") or None,
    ) or {}


def collect_sessions(
    daemon_items: list[dict[str, Any]] | None,
    company: dict[str, Any],
) -> list[dict[str, Any]]:
    """绑定到本项目的会话: 活着的在前, 再按最近活跃倒序, 最多 50 条。"""
    from omnicompany.packages.services._core.identity import all_session_bindings
    from omnicompany.packages.services._focus import session_attention as attention

    project = project_id(company)
    daemon_ok = daemon_items is not None
    seen = attention.read_seen()
    rows: list[tuple[bool, float, dict[str, Any]]] = []
    known: set[str] = set()

    for item in daemon_items or []:
        sid = str(item.get("id") or "")
        known.add(sid)
        binding = _binding_for(item)
        if str(binding.get("project") or "") != project:
            continue
        provider = str(item.get("provider") or binding.get("provider") or "")
        topic = str(item.get("topic") or binding.get("topic") or "")
        alive = bool(item.get("alive"))
        last = _last_active(item)
        rows.append((alive, last, {
            "id": sid,
            "title": _title(item, binding, provider, sid),
            "staff": _staff_for(topic, provider, company),
            "provider": provider or None,
            "model": binding.get("model") or item.get("model") or _model_from_cmd(item.get("cmd")),
            "alive": alive,
            "status": _status(item, daemon_ok=True, seen=seen),
            "from_studio": topic.startswith(STUDIO_TOPIC_PREFIX),
            "started_at": _iso(item.get("started_at")),
            "last_active_at": _iso(last) or _iso(binding.get("updated_at")),
            "cwd": item.get("cwd") or binding.get("cwd"),
        }))

    # 台账里归到本项目、但 daemon 已不记得的会话: 只剩台账能说的那些字段。
    for record in all_session_bindings().values():
        if not isinstance(record, dict) or str(record.get("project") or "") != project:
            continue
        sid = str(record.get("pty_id") or record.get("trace_id") or "")
        if not sid or sid in known:
            continue
        known.add(sid)
        provider = str(record.get("provider") or "")
        topic = str(record.get("topic") or "")
        updated = _iso(record.get("updated_at"))
        try:
            last = _dt.datetime.fromisoformat(updated).timestamp() if updated else 0.0
        except ValueError:
            last = 0.0
        rows.append((False, last, {
            "id": sid,
            "title": _title({}, record, provider, sid),
            "staff": _staff_for(topic, provider, company),
            "provider": provider or None,
            "model": record.get("model"),
            "alive": False,
            "status": _status(None, daemon_ok=daemon_ok, seen=seen),
            "from_studio": topic.startswith(STUDIO_TOPIC_PREFIX),
            "started_at": None,
            "last_active_at": updated,
            "cwd": record.get("cwd"),
        }))

    rows.sort(key=lambda row: (not row[0], -row[1]))
    return [row[2] for row in rows[:SESSIONS_LIMIT]]


def pick_delivery_session(staff_id: str, sessions: list[dict[str, Any]]) -> str | None:
    """这名员工最近活跃的、活着的 Studio 会话。"""
    best: tuple[str, str] | None = None
    for s in sessions:
        if s.get("staff") != staff_id or not s.get("alive") or not s.get("from_studio"):
            continue
        key = str(s.get("last_active_at") or "")
        if best is None or key > best[0]:
            best = (key, str(s["id"]))
    return best[1] if best else None


async def _post_session_input(session_id: str, text: str) -> dict[str, Any]:
    """像人在终端里打字那样提交一段纯文本(ccdaemon 先写正文、再单独补回车并确认开跑)。

    不能是 ``[OMNI AGENT MESSAGE v1 …]`` 信封: 那种会被静默协调中间件收进收件箱、
    不进 provider。这里的文字以「【老板私聊】/【群聊 @你】」开头, 不会命中。
    """
    from .cc_proxy import _daemon_http_base, _daemon_http_client

    base = await asyncio.to_thread(_daemon_http_base)
    client = await _daemon_http_client()
    # 守护端确认链最长约 11 秒(突发窗 + 确认 + 补回车 + 再确认)。
    resp = await client.request(
        method="POST",
        url=f"{base}/cc/sessions/{session_id}/input",
        headers={"content-type": "application/json"},
        content=json.dumps({"data": text, "submit": True}, ensure_ascii=False).encode("utf-8"),
        timeout=30.0,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if not resp.is_success:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        return {"ok": False, "reason": f"HTTP {resp.status_code}: {detail or resp.text[:200]}"}
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "unexpected response"}


async def _deliver_one(staff_id: str, session_id: str, text: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"staff": staff_id, "session": session_id}
    try:
        result = await _post_session_input(session_id, text)
    except Exception as exc:  # noqa: BLE001 - 投递失败不影响消息入库
        return {**entry, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    submission = str(result.get("submission") or "")
    if result.get("ok") is False:
        reason = result.get("reason") or result.get("detail") or submission or "rejected"
        return {**entry, "ok": False, "reason": str(reason)}
    if submission in ("stored", "rejected"):
        return {**entry, "ok": False, "reason": f"intercepted as coordination message ({submission})"}
    return {**entry, "ok": True}


# ─── 路由 ──────────────────────────────────────────────────────────────

def _bad(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _company_or_error() -> dict[str, Any] | JSONResponse:
    try:
        company = load_company()
    except FileNotFoundError:
        return _bad("staff.json not found", 404)
    except (OSError, ValueError) as exc:
        return _bad(f"staff.json unreadable: {exc}", 500)
    return company if isinstance(company, dict) else _bad("staff.json is not an object", 500)


@studio_router.get("/company")
async def company_config() -> Response:
    """staff.json 原样返回。"""
    path = company_dir() / "staff.json"
    try:
        raw = path.read_bytes()
        json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return _bad("staff.json not found", 404)
    except (OSError, ValueError) as exc:
        return _bad(f"staff.json unreadable: {exc}", 500)
    return Response(content=raw, media_type="application/json; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


@studio_router.get("/chat")
async def chat_read(channel: str = "group", after: str | None = None, limit: str | None = None):
    company = _company_or_error()
    if isinstance(company, JSONResponse):
        return company
    if not valid_channel(channel, company):
        return _bad(f"invalid channel: {channel}")
    if after is not None and after != "" and not _MESSAGE_ID.match(after):
        return _bad(f"invalid after: {after}")
    try:
        size = CHAT_LIMIT_DEFAULT if limit in (None, "") else int(limit)
    except ValueError:
        return _bad(f"invalid limit: {limit}")
    if not 1 <= size <= CHAT_LIMIT_MAX:
        return _bad(f"limit out of range 1..{CHAT_LIMIT_MAX}: {size}")
    messages = await asyncio.to_thread(read_messages, channel)
    if after:
        messages = [m for m in messages if str(m.get("id") or "") > after][:size]
    else:
        messages = messages[-size:]
    last_id = messages[-1].get("id") if messages else (after or None)
    return JSONResponse({"messages": messages, "last_id": last_id}, headers={"Cache-Control": "no-store"})


@studio_router.post("/chat")
async def chat_post(request: Request):
    company = _company_or_error()
    if isinstance(company, JSONResponse):
        return company
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("invalid JSON body")
    if not isinstance(body, dict):
        return _bad("body must be an object")
    channel = body.get("channel")
    text = body.get("text")
    if not isinstance(channel, str) or not valid_channel(channel, company):
        return _bad(f"invalid channel: {channel}")
    if not isinstance(text, str) or not text.strip():
        return _bad("text must be a non-empty string")
    if len(text) > TEXT_MAX:
        return _bad(f"text longer than {TEXT_MAX} characters")

    message = await asyncio.to_thread(append_boss_message, channel, text)

    plan = delivery_plan(channel, text, company)
    delivered: list[dict[str, Any]] = []
    if plan:
        try:
            daemon_items = await _fetch_daemon_sessions()
            sessions = await asyncio.to_thread(collect_sessions, daemon_items, company)
        except Exception as exc:  # noqa: BLE001 - 消息已入库, 投递是附带的
            logger.warning("studio: session lookup for delivery failed: %s", exc)
            sessions = []
        jobs = []
        for staff_id, prompt in plan:
            target = pick_delivery_session(staff_id, sessions)
            if target:
                jobs.append(_deliver_one(staff_id, target, prompt))
        if jobs:
            delivered = list(await asyncio.gather(*jobs))
    return JSONResponse({"message": message, "delivered": delivered})


@studio_router.get("/sessions")
async def sessions_list():
    company = _company_or_error()
    if isinstance(company, JSONResponse):
        return company
    daemon_items = await _fetch_daemon_sessions()
    sessions = await asyncio.to_thread(collect_sessions, daemon_items, company)
    return JSONResponse({"sessions": sessions}, headers={"Cache-Control": "no-store"})


def _bind_project(session_id: str, item: dict[str, Any] | None, project: str) -> None:
    """只写 ``project``; 写到台账里解析出的那条记录上, 其余字段一律不动。"""
    from omnicompany.packages.services._core.identity import (
        all_session_bindings,
        binding_for_session,
        update_session_binding,
    )

    binding = _binding_for(item) if item else binding_for_session(trace_id=session_id, pty_id=session_id)
    trace_id = str((binding or {}).get("trace_id") or "")
    if trace_id and trace_id in all_session_bindings():
        update_session_binding(trace_id, project=project)
    else:
        # 台账里还没有这条会话: 新建一条, 带上 pty_id 让按 PTY 解析的消费方也找得到。
        update_session_binding(session_id, project=project, pty_id=session_id)


@studio_router.post("/sessions/bind")
async def sessions_bind(request: Request):
    company = _company_or_error()
    if isinstance(company, JSONResponse):
        return company
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("invalid JSON body")
    session_id = body.get("session") if isinstance(body, dict) else None
    if not isinstance(session_id, str) or not session_id.strip():
        return _bad("session must be a non-empty string")
    session_id = session_id.strip()

    daemon_items = await _fetch_daemon_sessions()
    item = next((i for i in daemon_items or [] if str(i.get("id") or "") == session_id), None)
    if item is None:
        from omnicompany.packages.services._core.identity import all_session_bindings

        ledger = await asyncio.to_thread(all_session_bindings)
        in_ledger = session_id in ledger or any(
            isinstance(r, dict) and str(r.get("pty_id") or "") == session_id for r in ledger.values()
        )
        if not in_ledger:
            return _bad(f"session not found: {session_id}", 404)
    await asyncio.to_thread(_bind_project, session_id, item, project_id(company))
    return {"ok": True}
