"""Identity-enveloped messages between registered CLI sessions."""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from omnicompany.core.config import omni_workspace_root

_lock = threading.RLock()

_DIRECT_INTENTS = frozenset({"auto", "action", "decision", "blocker", "boundary"})
_ACK_ONLY_RE = re.compile(
    r"^(?:收到|已收到|好的|好|ok(?:ay)?|ack(?:nowledged)?|知悉|明白|了解|谢谢|感谢)"
    r"(?:[，,。.!！\s]*(?:无需回复|不必回复))?[。.!！\s]*$",
    re.IGNORECASE,
)
_ACTIONABLE_RE = re.compile(
    r"(?:"
    r"请(?:你|先|勿|不要|确认|回复|告知|处理|检查|复跑|释放|停止|等待|继续|修改|同步|构建|执行|完成|开始|查看)|"
    r"需要你|麻烦|请问|能否|是否|怎么办|如何|为什么|"
    r"冲突|阻塞|卡住|协商|claim|negotiate|blocked|conflict|waiting on|"
    r"need you|please|could you|can you|do not|don't|must|"
    r"^(?:检查|复查|验证|复跑|修改|修复|更新|新增|删除|释放|停止|等待|继续|同步|构建|执行|读取|写入)|"
    r"^(?:check|review|verify|test|run|fix|update|add|remove|release|stop|wait|continue|sync|build|inspect|read|write)\b|"
    r"[?？]"
    r")",
    re.IGNORECASE,
)
_TERMINAL_UPDATE_RE = re.compile(
    r"(?:已完成|已通过|已发布|已同步|已合并|已收口|已闭环|完成回执|最终回执|"
    r"无需额外操作|无需操作|不再改动|不再触碰|"
    # Hyphen-aware boundaries: technical compounds like "fail-closed" or
    # "type-checked" are not completion reports.
    r"(?<![\w-])(?:completed|passed|published|synced|merged|closed|no further action)(?![\w-]))",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_path() -> Path:
    override = os.environ.get("OMNI_SESSION_MESSAGE_LOG")
    return Path(override) if override else omni_workspace_root() / "data" / "lifecycle" / "session_messages.jsonl"


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def assess_follow_up_relevance(
    message: str,
    *,
    intent: str = "auto",
    reply_to: str = "",
) -> dict[str, Any]:
    """Decide whether a cross-session message can affect further work.

    Direct messages are coordination requests, not a status broadcast channel.
    A correlated reply may carry a requested result, while empty acknowledgements
    are noise in both directions.
    """
    body = _clean(message, 4000)
    normalized_intent = str(intent or "auto").strip().lower()
    if normalized_intent not in _DIRECT_INTENTS:
        raise ValueError(
            f"未知协调意图 {intent!r}；可用值：auto/action/decision/blocker/boundary"
        )
    if not body:
        return {"allowed": False, "reason": "消息为空", "intent": normalized_intent}
    if _ACK_ONLY_RE.fullmatch(body):
        return {
            "allowed": False,
            "reason": "纯确认不会改变后续工作",
            "intent": normalized_intent,
        }
    if reply_to:
        return {
            "allowed": True,
            "reason": "对既有协调请求的关联结果或阻塞",
            "intent": "reply",
        }
    # An explicit intent is the sender's declaration and always wins: the
    # phrasing heuristics below are subjective and only arbitrate `auto`.
    if normalized_intent != "auto":
        return {
            "allowed": True,
            "reason": f"发送方声明为 {normalized_intent} 协调",
            "intent": normalized_intent,
        }
    if _TERMINAL_UPDATE_RE.search(body) and not _ACTIONABLE_RE.search(body):
        return {
            "allowed": False,
            "reason": "仅报告完成状态且没有要求接收方继续行动",
            "intent": normalized_intent,
        }
    if _ACTIONABLE_RE.search(body):
        return {
            "allowed": True,
            "reason": "包含明确行动、裁决、阻塞或边界信号",
            "intent": "auto",
        }
    return {
        "allowed": False,
        "reason": "未说明这条消息将如何改变接收方的后续工作",
        "intent": "auto",
    }


def resolve_target(target_ref: str, records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resolve an exact identity/session/PTY/key or an unambiguous id prefix."""
    if records is None:
        from omnicompany.packages.services.boss_sight.services.agent_registry import (
            list_records,
            schedule_rebuild,
        )

        records = list_records(running_only=False, rebuild_first=False)
        # The registry is durable but can lag the live ccdaemon PTY projection.
        # Merge both so the IDs shown in Multi-agent are valid message targets.
        try:
            from omnicompany.packages.services._core.agent.control_plane import list_agent_workers

            live_rows: list[dict[str, Any]] = []
            for runtime in list_agent_workers():
                row = dict(runtime)
                pty_id = str(row.get("pty_id") or row.get("id") or "")
                provider_session_id = str(row.get("provider_session_id") or "")
                row.setdefault("pty_id", pty_id)
                row.setdefault("session_id", provider_session_id or pty_id)
                row.setdefault("key", f"{row.get('provider') or 'agent'}:{provider_session_id or pty_id}")
                row.setdefault("identity", row.get("display_title") or row.get("summary_title") or row.get("provider_title") or pty_id)
                row["running"] = bool(row.get("alive", True))
                live_rows.append(row)
            records = [*records, *live_rows]
        except RuntimeError:
            # Durable registry lookup remains a useful fallback while ccdaemon
            # is unavailable; delivery will report the real failure later.
            pass
        schedule_rebuild()
    rows = [dict(row) for row in records]
    ref = str(target_ref or "").strip()
    if not ref:
        raise ValueError("目标会话不能为空")
    exact_fields = ("key", "session_id", "pty_id", "identity")
    exact = [row for row in rows if any(str(row.get(field) or "") == ref for field in exact_fields)]
    # The durable registry and the live ccdaemon projection can both list the
    # same session; rows sharing one pty_id (or session_id) are one target, not
    # an ambiguity. Prefer the row that knows its PTY so delivery can go direct.
    if len(exact) > 1:
        session_ids = {str(row.get("session_id") or "") for row in exact} - {""}
        pty_ids = {str(row.get("pty_id") or "") for row in exact} - {""}
        # A reused PTY may have a new provider-native session id after the
        # durable registry and live projection are merged.  The PTY is still
        # the exact physical delivery target, so one shared pty_id wins.
        if len(pty_ids) == 1:
            exact = [max(exact, key=lambda row: bool(row.get("pty_id")))]
        elif len(session_ids) <= 1 and len(pty_ids) <= 1:
            exact = [max(exact, key=lambda row: bool(row.get("pty_id")))]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"目标 {ref!r} 命中多个会话，请使用 provider:session_id")
    prefix = [
        row for row in rows
        if any(str(row.get(field) or "").startswith(ref) for field in ("key", "session_id", "pty_id"))
    ]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        raise ValueError(f"目标前缀 {ref!r} 不唯一，共 {len(prefix)} 条")
    raise ValueError(f"未找到目标会话: {ref}")


def source_identity(*, from_topic: str = "") -> dict[str, Any]:
    from omnicompany.packages.services._core.identity import current_session_meta

    meta = current_session_meta()
    topic = _clean(meta.get("topic") or from_topic, 240)
    identity = {
        "trace_id": _clean(meta.get("trace_id"), 240),
        "session_id": _clean(meta.get("session_id") or meta.get("claude_session_id"), 240),
        "pty_id": _clean(meta.get("pty_id"), 120),
        "provider": _clean(meta.get("provider"), 40),
        "name": _clean(meta.get("identity") or meta.get("name"), 180),
        "project": _clean(meta.get("project"), 100),
        "workstream": _clean(meta.get("workstream"), 160),
        "plan": _clean(meta.get("active_plan"), 240),
        "task": _clean(meta.get("task_id"), 240),
        "topic": topic,
    }
    if not identity["trace_id"] or identity["trace_id"].startswith("cc_unknown_"):
        raise ValueError("当前会话没有稳定 session 标识；先运行 omni session bind --trace-id <id>")
    if not any(identity[field] for field in ("project", "workstream", "plan", "task", "topic")):
        raise ValueError("当前会话没有内容身份；先绑定 project/workstream/topic，或传 --from-topic")
    return identity


def build_envelope(
    message_id: str,
    source: dict[str, Any],
    message: str,
    *,
    target: dict[str, Any] | None = None,
    reply_to: str = "",
    intent: str = "auto",
) -> str:
    """Build an explicit coordination envelope for terminal-only delivery.

    Agent CLIs currently accept incoming work through their normal prompt input,
    so the content must make its non-user origin unambiguous. Keep the reply
    command in the message itself: the receiving Agent can report without
    guessing who sent it or hunting for a PTY.
    """
    request = _clean(message, 4000)
    if not request:
        raise ValueError("消息不能为空")
    source_scope = [
        f"project={source.get('project') or '-'}",
        f"workstream={source.get('workstream') or '-'}",
        f"plan={source.get('plan') or '-'}",
        f"task={source.get('task') or '-'}",
        f"topic={source.get('topic') or '-'}",
    ]
    target = target or {}
    target_scope = [
        f"project={target.get('project') or '-'}",
        f"workstream={target.get('workstream') or '-'}",
        f"plan={target.get('active_plan') or target.get('plan') or '-'}",
        f"topic={target.get('topic') or target.get('current_task') or '-'}",
    ]
    source_label = source.get("name") or source.get("trace_id") or "unknown"
    target_label = target.get("identity") or target.get("key") or target.get("session_id") or "this session"
    # 收件方可能对多会话协作一无所知：首行必须用人话讲清来源与回信方式，
    # 不能假设对方认识 "[OMNI AGENT MESSAGE]" 这种标记（2026-08-18 用户裁定）。
    sender_pty = str(source.get("pty_id") or "").strip()
    reply_hints = [f'omni agents reply {message_id} -m "<阻塞原因>"']
    if sender_pty:
        reply_hints.append(f'或直接回发 omni agent send {sender_pty} -m "..."')
    lines = [
        f"[OMNI AGENT MESSAGE v1 · {message_id}]",
        "说明: 这条消息来自另一个 AI 会话（不是人类用户输入），经 omni CLI 跨会话投递；请当作协调消息处理。",
        f"发件会话: {source_label} · provider={source.get('provider') or '-'} · omni pty id={sender_pty or '-'} · session={source.get('trace_id') or '-'}",
        f"Sender scope: {'; '.join(source_scope)}",
        f"To: {target_label} · provider={target.get('provider') or '-'} · session={target.get('session_id') or target.get('pty_id') or '-'}",
        f"Receiver scope: {'; '.join(target_scope)}",
    ]
    if reply_to:
        lines.append(f"In reply to: {reply_to}")
    lines.extend([
        f"Delegation intent: {intent}",
        f"Request: {request}",
        # 默认不回信（2026-08-27 用户裁定，适用于所有 agent 收到的所有消息）：
        # 回路是"收到就回"维持的——A 报 → B 回 → B 的回复唤醒 A → A 再报，
        # 每条冗余消息都消耗对方的额度与注意力。因此信封不再邀请回复，
        # 只在真正阻塞时给出回信手段。
        "非必要不回复、非必要不上报: 收到后直接干活，不发确认/回执/进度；自行解决，把结论写进 artifact / 工作报告即生效，"
        "对方会自己读原件。自我更正、假设被证伪、口径修订都不必通知任何人。"
        "只有①完全无法自行解决 或 ②完全不属于你职责且有确实障碍 才形成报告发出。",
        f"确需回信时: {'；'.join(reply_hints)}",
    ])
    return "\n".join(lines)


def _ccdaemon_base() -> str:
    from omnicompany.dashboard.ccdaemon import lifecycle

    status = lifecycle.read_status()
    if not (getattr(status, "alive", False) and getattr(status, "port", None)):
        raise RuntimeError("ccdaemon 未运行")
    return f"http://127.0.0.1:{status.port}"


def _post_input(pty_id: str, envelope: str) -> dict[str, Any]:
    body = json.dumps({"data": envelope, "submit": True}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{_ccdaemon_base()}/cc/sessions/{pty_id}/input",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    # 守护端的投递确认链最长 ~11s（1.2s 突发窗 + 4s 确认 + 补回车 + 4s 再确认），
    # 10s 超时会把已成功的投递误报成 fallback timed out。
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - loopback daemon
        return json.loads(response.read().decode("utf-8"))


def _queue_next_turn(target: dict[str, Any], envelope: str) -> dict[str, Any]:
    from omnicompany.packages.services.boss_sight.services.agent_attention import queue_feedback

    session_id = str(target.get("session_id") or target.get("pty_id") or "")
    if not session_id:
        raise RuntimeError("目标没有可投递 session_id/pty_id")
    queue_feedback(session_id, envelope, author="agent")
    return {"mode": "next_turn", "session_id": session_id}


def _append(record: dict[str, Any]) -> None:
    path = log_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def send_message(
    target_ref: str,
    message: str,
    *,
    from_topic: str = "",
    reply_to: str = "",
    intent: str = "auto",
    records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    relevance = assess_follow_up_relevance(message, intent=intent, reply_to=reply_to)
    if not relevance["allowed"]:
        raise ValueError(
            "跨会话消息已抑制："
            f"{relevance['reason']}。直接消息请明确下一步，或使用 "
            "--intent action|decision|blocker|boundary；受托结果请用 omni agents reply <message-id>。"
        )
    target = resolve_target(target_ref, records)
    if not target.get("running"):
        raise ValueError("目标会话不在运行；请先恢复或新建会话")
    source = source_identity(from_topic=from_topic)
    message_id = f"msg-{uuid4().hex[:12]}"
    envelope = build_envelope(message_id, source, message, target=target, reply_to=reply_to, intent=intent)
    delivery: dict[str, Any]
    pty_id = str(target.get("pty_id") or "")
    try:
        if not pty_id:
            raise RuntimeError("目标没有 PTY")
        immediate = _post_input(pty_id, envelope)
        if immediate.get("ok") is False:
            # The terminal accepted bytes but the daemon could not confirm that
            # the provider consumed Enter. Do not enqueue the full body: it may
            # be sitting in the composer and a second copy would be ambiguous.
            delivery = {"mode": "unconfirmed", "pty_id": pty_id, **immediate}
        else:
            delivery = {"mode": "immediate", **immediate}
    except (RuntimeError, OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        delivery = {**_queue_next_turn(target, envelope), "fallback_reason": str(exc)}
    record = {
        "id": message_id,
        "created_at": _now(),
        "source": source,
        "target": {
            key: target.get(key)
            for key in ("key", "identity", "provider", "session_id", "pty_id", "project", "workstream", "active_plan", "topic", "current_task")
        },
        "message": _clean(message, 4000),
        "envelope": envelope,
        "reply_to": reply_to,
        "relevance": relevance,
        "delivery": delivery,
    }
    _append(record)
    return record


def get_message(message_id: str) -> dict[str, Any] | None:
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id") == message_id:
            return row
    return None


def reply_message(message_id: str, message: str, *, from_topic: str = "") -> dict[str, Any]:
    original = get_message(message_id)
    if original is None:
        raise ValueError(f"未找到消息: {message_id}")
    source = original.get("source") or {}
    target_ref = source.get("session_id") or source.get("pty_id") or source.get("trace_id")
    if not target_ref:
        raise ValueError("原消息没有可回报的来源会话")
    return send_message(
        str(target_ref),
        message,
        from_topic=from_topic or f"回复 {message_id}",
        reply_to=message_id,
        intent="auto",
    )


__all__ = [
    "assess_follow_up_relevance",
    "build_envelope",
    "get_message",
    "reply_message",
    "resolve_target",
    "send_message",
    "source_identity",
]
