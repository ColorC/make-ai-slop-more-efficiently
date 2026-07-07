# [OMNI] origin=ai-ide ts=2026-06-04 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.import_sessions_routes.py"
"""载入已有会话 (#2 / A1) — 列出本机 Claude Code / Codex 的历史会话, 供 BOSS SIGHT 载入续接。

为什么单独一个 router:
- 实际"创建并续接"复用 chat.py 已有的 create(provider, cwd, fork_from_provider_session_id):
  claude_code → resume + fork_session(源会话不受影响); codex → resume_thread 续同一线程。
- 这里只负责"扫盘 + 给前端列出可选会话"这一只读步骤, 跟 token 统计扫描(boss_sight.routes)
  解耦, 也不污染体量已经很大的 chat.py。

会话来源:
- Claude Code: ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl —— 文件名 stem 就是
  resume 锚点(claude session id)。
- Codex: ~/.codex/sessions/**/rollout-*.jsonl —— thread id 取自 session_meta.payload.id,
  退化到从文件名里解析 UUID。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_log = logging.getLogger(__name__)

# 前缀 /cc/chat: dashboard 反向代理把 /api/cc/{path} → ccdaemon /cc/{path}, 故前端
# 请求 /api/cc/chat/importable 会落到这里。(与 chat.py 的 cc_chat_router 同前缀约定。)
import_sessions_router = APIRouter(prefix="/cc/chat", tags=["cc-chat-import"])

_MAX_FILES = 60
_MAX_AGE_DAYS = 90
_HEAD_LINES = 200
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _recent_files(root: Path, cap: int) -> list[tuple[float, Path]]:
    if not root.is_dir():
        return []
    cutoff = time.time() - _MAX_AGE_DAYS * 86400
    files: list[tuple[float, Path]] = []
    try:
        for p in root.rglob("*.jsonl"):
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt < cutoff:
                continue
            files.append((mt, p))
    except OSError:
        pass
    files.sort(key=lambda x: x[0], reverse=True)
    return files[:cap]


def _clip(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _first_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict):
                if isinstance(blk.get("text"), str) and blk.get("type") in (None, "text"):
                    return blk["text"]
                if isinstance(blk.get("content"), str):
                    return blk["content"]
            elif isinstance(blk, str):
                return blk
    return ""


def _looks_internal(text: str) -> bool:
    t = text.lstrip()
    return t.startswith("<") or t.startswith("[OMNI]") or t.startswith("Caveat:")


# ────────────────────────────────────────────────────────────────────────
# 载入已有对话为"总控前文" (#3 / A1): 从 transcript 抽出真实 (role, text) 序列
# ────────────────────────────────────────────────────────────────────────


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _claude_msg_text(content: Any) -> str:
    """claude message.content → 显示文本(string 或 blocks)。跳过 thinking/tool_use/tool_result。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") in ("thinking", "tool_use", "tool_result"):
                    continue
                if blk.get("type") in (None, "text") and isinstance(blk.get("text"), str):
                    parts.append(blk["text"])
                elif isinstance(blk.get("content"), str):
                    parts.append(blk["content"])
            elif isinstance(blk, str):
                parts.append(blk)
        return "\n".join(p for p in parts if p.strip())
    return ""


def _codex_msg_text(content: Any) -> str:
    """codex payload.content(message) → 文本。取 input_text / output_text / text 块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") in ("tool_use", "tool_reference", "tool_result"):
                    continue
                if isinstance(blk.get("text"), str):
                    parts.append(blk["text"])
                elif isinstance(blk.get("content"), str):
                    parts.append(blk["content"])
            elif isinstance(blk, str):
                parts.append(blk)
        return "\n".join(p for p in parts if p.strip())
    return ""


def _extract_transcript(provider: str, path: Path, cap_chars: int = 60000, per_msg: int = 4000) -> tuple[list[dict], bool]:
    """从 transcript(.jsonl)抽出按时间排序的 [{role, text}], 跳过工具/思考/系统提醒。

    返回 (messages, truncated)。truncated=True 表示因 cap 截断了尾部。
    """
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                role = ""
                text = ""
                if provider == "codex":
                    if obj.get("type") != "response_item":
                        continue
                    payload = obj.get("payload") or {}
                    if payload.get("type") != "message":
                        continue
                    role = str(payload.get("role") or "")
                    if role == "developer":  # codex 系统指令, 跳过
                        continue
                    text = _codex_msg_text(payload.get("content"))
                else:  # claude
                    if obj.get("type") not in ("user", "assistant"):
                        continue
                    msg = obj.get("message") or {}
                    role = str(msg.get("role") or obj.get("type") or "")
                    text = _claude_msg_text(msg.get("content"))
                text = (text or "").strip()
                if not text or _looks_internal(text):
                    continue
                if role not in ("user", "assistant"):
                    role = "user" if role != "assistant" else "assistant"
                ts = _parse_iso(obj.get("timestamp"))
                rows.append({"role": role, "text": text, "_ts": ts})
    except OSError as e:
        raise HTTPException(400, f"读取 transcript 失败: {e}") from e

    # 有时间戳的按时间排; 没有的保持原始读入顺序(stable sort 保留)
    rows.sort(key=lambda r: (r["_ts"] is None, r["_ts"] or 0.0))

    out: list[dict] = []
    total = 0
    truncated = False
    for r in rows:
        t = r["text"]
        if len(t) > per_msg:
            t = t[:per_msg] + " …[截断]"
        if total + len(t) > cap_chars:
            truncated = True
            break
        total += len(t)
        out.append({"role": r["role"], "text": t})
    return out, truncated


def _format_preamble(provider: str, session_id: str, title: str, cwd: str, messages: list[dict], truncated: bool) -> str:
    label = "Codex" if provider == "codex" else "Claude Code"
    lines = [
        "[from: BOSS-SIGHT bus event, not_user: true]",
        "event_type: load_prior_conversation",
        "下面是用户选择载入、作为本次对话【前文/背景上下文】的一段已有对话(来自"
        f" {label})。这不是新指令, 是供你参考的历史。",
        f"source_provider: {provider}",
        f"source_session: {session_id}",
    ]
    if cwd:
        lines.append(f"source_cwd: {cwd}")
    if title:
        lines.append(f"source_title: {title}")
    lines.append("")
    lines.append("===== 载入的历史对话(开始) =====")
    for m in messages:
        who = "用户" if m["role"] == "user" else "助理"
        lines.append(f"\n[{who}]\n{m['text']}")
    lines.append("\n===== 载入的历史对话(结束) =====")
    if truncated:
        lines.append("(注: 内容过长, 已截断尾部。)")
    lines.append(
        "\n以上是供你参考的前文。请勿展开复述, 只用一句话确认已把它纳入上下文即可; "
        "之后用户会基于这段背景继续跟你对话。"
    )
    return "\n".join(lines)


def _scan_claude() -> list[dict]:
    root = Path.home() / ".claude" / "projects"
    out: list[dict] = []
    for mtime, f in _recent_files(root, _MAX_FILES):
        cwd = ""
        preview = ""
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > _HEAD_LINES:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if not cwd and isinstance(obj.get("cwd"), str):
                        cwd = obj["cwd"]
                    if not preview:
                        msg = obj.get("message") or {}
                        is_user = msg.get("role") == "user" or obj.get("type") == "user"
                        if is_user:
                            txt = _first_text(msg.get("content"))
                            if txt and not _looks_internal(txt):
                                preview = txt
                    if cwd and preview:
                        break
        except OSError:
            continue
        out.append({
            "provider": "claude_code",
            "session_id": f.stem,
            "cwd": cwd,
            "mtime": mtime,
            "preview": _clip(preview),
            "file": str(f),
        })
    return out


def _codex_user_text(obj: dict) -> str:
    """尽力从一条 codex rollout 事件里抠出用户输入文本(事件格式多变, 容错)。"""
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return ""
    ptype = str(payload.get("type") or "")
    role = payload.get("role")
    if role == "user" or "user" in ptype.lower():
        for key in ("content", "text", "message", "input"):
            txt = _first_text(payload.get(key))
            if txt:
                return txt
    return ""


def _scan_codex() -> list[dict]:
    root = Path.home() / ".codex" / "sessions"
    out: list[dict] = []
    for mtime, f in _recent_files(root, _MAX_FILES):
        sid = ""
        cwd = ""
        preview = ""
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > _HEAD_LINES:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if obj.get("type") == "session_meta":
                        p = obj.get("payload") or {}
                        sid = sid or str(p.get("id") or p.get("conversation_id") or p.get("session_id") or "")
                        cwd = cwd or str(p.get("cwd") or p.get("working_directory") or "")
                    if not preview:
                        txt = _codex_user_text(obj)
                        if txt and not _looks_internal(txt):
                            preview = txt
                    if sid and cwd and preview:
                        break
        except OSError:
            continue
        if not sid:
            m = _UUID_RE.search(f.name)
            sid = m.group(0) if m else f.stem
        out.append({
            "provider": "codex",
            "session_id": sid,
            "cwd": cwd,
            "mtime": mtime,
            "preview": _clip(preview),
            "file": str(f),
        })
    return out


# ── 完成感知: 读 transcript 尾部判"运行中/已完成/等待" + 抽"完成了什么" ──────────
# 多 agent 视图要的核心信号(用户 2026-06-13: "我可以看到其是否完成了, 完成了什么")。
# head 扫描只取首条用户消息当 preview; 这里读尾部最后若干条 → 最后一条消息角色 + 最后一段
# 助手回复, 配合 mtime 推出运行态。判据沿用 claude-code 的实践: 新鲜写入=在跑, 末条是助手
# 回复且已静默=这轮干完了。
_TAIL_BYTES = 131072   # 读尾部 128KB, 足够覆盖最后若干条消息(不整文件读, 防长会话卡顿)
_WORKING_SEC = 45      # 尾部 mtime 这么近 → 视为仍在跑(刚写过)


def _tail_lines(path: Path, max_bytes: int = _TAIL_BYTES, max_lines: int = 160) -> list[str]:
    """读文件尾部最多 max_bytes, 返回最后 max_lines 个非空行(丢弃首个可能被截断的半行)。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # 丢弃可能被截断的半行
            data = fh.read()
    except OSError:
        return []
    lines = [ln for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    return lines[-max_lines:]


def _claude_user_prompt(content: Any) -> str:
    """只从真实用户输入(字符串 / text 块)提取 prompt; tool_result/tool_use 回灌(claude 也存成
    type:user 消息)返回 '' —— 否则尾部抓到的"最后一条 user"会是工具输出而非用户原话。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") in ("tool_result", "tool_use") for b in content):
            return ""
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                return b["text"].strip()
            if isinstance(b, str) and b.strip():
                return b.strip()
    return ""


def _claude_tail_status(lines: list[str]) -> dict[str, str]:
    last_role = ""
    last_assistant = ""
    last_user = ""  # 最后一条真实用户 prompt(跳过 tool_result 回灌/注入), 给"无摘要"行兜底显示
    for ln in reversed(lines):
        try:
            obj = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        msg = obj.get("message") or {}
        role = msg.get("role") or obj.get("type")
        if role not in ("user", "assistant"):
            continue
        if not last_role:
            last_role = role
        if role == "assistant" and not last_assistant:
            txt = _claude_msg_text(msg.get("content"))
            if txt.strip():
                last_assistant = txt
        if role == "user" and not last_user:
            txt = _claude_user_prompt(msg.get("content"))
            if txt and not _looks_internal(txt):
                last_user = txt
        if last_role and last_assistant and last_user:
            break
    return {"last_role": last_role, "last_assistant": _clip(last_assistant, 200), "last_user": _clip(last_user, 200)}


def _codex_tail_status(lines: list[str]) -> dict[str, str]:
    last_role = ""
    last_assistant = ""
    last_user = ""
    for ln in reversed(lines):
        try:
            obj = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = str(payload.get("type") or "").lower()
        role = payload.get("role")
        if role == "assistant" or "agent" in ptype or "output" in ptype:
            txt = _codex_msg_text(payload.get("content")) or _first_text(payload.get("text"))
            if txt.strip():
                if not last_role:
                    last_role = "assistant"
                if not last_assistant:
                    last_assistant = txt
        elif role == "user":
            if not last_role:
                last_role = "user"
            if not last_user:
                txt = _codex_msg_text(payload.get("content")) or _first_text(payload.get("text"))
                if txt.strip() and not _looks_internal(txt):
                    last_user = txt
        if last_role and last_assistant and last_user:
            break
    return {"last_role": last_role, "last_assistant": _clip(last_assistant, 200), "last_user": _clip(last_user, 200)}


def _tail_status(provider: str, path: Path) -> dict[str, str]:
    lines = _tail_lines(path)
    if not lines:
        return {"last_role": "", "last_assistant": ""}
    return _codex_tail_status(lines) if provider == "codex" else _claude_tail_status(lines)


def _derive_run_status(age_sec: float, last_role: str) -> str:
    """运行态: working(刚写过/在跑) / done(已回复且静默, 这轮干完) / waiting(末条是用户输入) / idle。"""
    if age_sec < _WORKING_SEC:
        return "working"
    if last_role == "assistant":
        return "done"
    if last_role == "user":
        return "waiting"
    return "idle"


@import_sessions_router.get("/active")
async def list_active_sessions(window_sec: int = 600, limit: int = 30) -> dict[str, Any]:
    """真正在跑的会话 = transcript(.jsonl)近 window_sec 秒有写入。含 omni 之外别的目录里的
    claude/codex 对话(用户反馈: 别处真活跃的对话没被捕捉到)。按最近活动排序。

    每条附 status(运行态) + last_did(最后一段助手回复 = "完成了什么"), 供多 agent 视图直接显示。
    尾部读只对返回的 ≤limit 条做, I/O 有界。"""
    window_sec = max(60, min(int(window_sec), 7 * 86400))
    limit = max(1, min(int(limit), 80))
    now = time.time()
    active: list[dict] = []
    try:
        scanned = _scan_claude() + _scan_codex()
    except Exception:  # noqa: BLE001
        scanned = []
    for it in scanned:
        if not it.get("session_id"):
            continue
        if (now - float(it.get("mtime") or 0)) <= window_sec:
            active.append(it)
    active.sort(key=lambda it: it.get("mtime", 0), reverse=True)
    total = len(active)
    items = active[:limit]
    for it in items:
        try:
            st = _tail_status(str(it.get("provider")), Path(it["file"]))
        except Exception:  # noqa: BLE001
            st = {"last_role": "", "last_assistant": ""}
        age = now - float(it.get("mtime") or 0)
        it["status"] = _derive_run_status(age, st.get("last_role", ""))
        it["last_did"] = st.get("last_assistant", "")
        it["last_user"] = st.get("last_user", "")  # 最后一句用户 prompt, 无摘要时兜底显示
    # 附性价比模型维护的对话摘要(项目/计划/在做什么/最近一步)+ 懒触发一次刷新。
    # 摘要只在后台线程里由便宜模型算(单飞+节流), 不堵本响应; 没摘要的行前端回退到原始 preview。
    try:
        from omnicompany.dashboard.boss_sight.services import agent_digest
        store = agent_digest.load_digests()
        for it in items:
            d = store.get(f"{it.get('provider')}:{it.get('session_id')}")
            if d:
                it["digest"] = {k: d.get(k, "") for k in ("project", "plan", "title", "last_step")}
        agent_digest.schedule_tick(items)
    except Exception:  # noqa: BLE001
        _log.debug("agent_digest attach/schedule skipped", exc_info=True)
    # 被动收集机器级 agent 注册表(身份/位置/在做啥): 检测到对话在跑就懒触发一次后台 rebuild
    # (确定性, 不调模型, 节流), 让总控派发路由器的候选清单始终新鲜。
    try:
        from omnicompany.dashboard.boss_sight.services import agent_registry
        agent_registry.schedule_rebuild()
    except Exception:  # noqa: BLE001
        _log.debug("agent_registry schedule skipped", exc_info=True)
    return {"count": total, "window_sec": window_sec, "items": items}


@import_sessions_router.get("/importable")
async def list_importable_sessions(limit: int = 40) -> dict[str, Any]:
    """列出可载入续接的本机历史会话(Claude Code + Codex), 按最近修改排序。"""
    limit = max(1, min(int(limit), 120))
    items: list[dict] = []
    try:
        items.extend(_scan_claude())
    except Exception:  # noqa: BLE001
        pass
    try:
        items.extend(_scan_codex())
    except Exception:  # noqa: BLE001
        pass
    # 过滤掉抠不到 id 的(没法 resume), 再按 mtime 排序裁剪。
    items = [it for it in items if it.get("session_id")]
    items.sort(key=lambda it: it.get("mtime", 0), reverse=True)
    return {"count": len(items), "items": items[:limit]}


class LoadContextBody(BaseModel):
    provider: str = Field(..., max_length=40)
    session_id: str = Field(default="", max_length=200)
    file: str = Field(..., min_length=1, max_length=4000)
    cwd: str | None = Field(default=None, max_length=4000)
    title: str | None = Field(default=None, max_length=200)
    cap_chars: int = Field(default=60000, ge=2000, le=200000)


def _is_under(p: Path, base: Path) -> bool:
    try:
        p.relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _validate_transcript_path(raw: str) -> Path:
    try:
        rp = Path(raw).resolve()
    except OSError as e:
        raise HTTPException(400, f"无效路径: {e}") from e
    home = Path.home()
    if not (_is_under(rp, home / ".claude") or _is_under(rp, home / ".codex")):
        raise HTTPException(400, "transcript 路径必须在 ~/.claude 或 ~/.codex 下")
    if not rp.is_file():
        raise HTTPException(404, f"transcript 文件不存在: {raw}")
    return rp


def _find_controller_session(mgr: Any) -> Any | None:
    """唯一活跃总控 session(与 ControllerWaker._find_active_controllers 同规则)。"""
    sessions = getattr(mgr, "_sessions", {})
    live = [
        s for s in sessions.values()
        if getattr(s, "provider", "") == "controller"
        and getattr(s, "ended_at", None) is None
        and not getattr(s, "archived", False)
    ]
    if not live:
        return None
    live.sort(key=lambda s: getattr(s, "started_at", 0) or 0, reverse=True)
    return live[0]


_load_ctx_tasks: set = set()


@import_sessions_router.post("/load_context")
async def load_context(body: LoadContextBody) -> dict[str, Any]:
    """#3 / A1: 把选中的已有对话(claude/codex)真实内容载入为【总控对话的前文】。

    读 transcript → 抽真实 (role,text) → 作为一条带标注的消息注入唯一活跃总控, 让总控模型
    真正"看见"这段历史(并显示在总控对话里)。注入会触发一次总控 turn, 丢后台、立即返回;
    总控对话经 WS 流式显示。
    """
    path = _validate_transcript_path(body.file)
    provider = "codex" if body.provider == "codex" else "claude_code"
    messages, truncated = _extract_transcript(provider, path, cap_chars=body.cap_chars)
    if not messages:
        raise HTTPException(422, "没从这段对话里抽到可读消息(可能全是工具/系统记录)")

    try:
        from .chat import get_chat_manager
        mgr = get_chat_manager()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"chat manager 不可用: {e}") from e

    controller = _find_controller_session(mgr)
    if controller is None:
        return {"ok": False, "reason": "no_active_controller", "message_count": len(messages)}

    preamble = _format_preamble(
        provider, body.session_id or path.stem, body.title or "", body.cwd or "", messages, truncated,
    )

    async def _inject() -> None:
        try:
            await mgr.submit_user_prompt(controller, preamble, record_history=True)
        except Exception:  # noqa: BLE001
            _log.exception("load_context inject failed")

    try:
        task = asyncio.get_running_loop().create_task(_inject())
        _load_ctx_tasks.add(task)
        task.add_done_callback(_load_ctx_tasks.discard)
    except RuntimeError as e:
        raise HTTPException(500, f"无法调度注入总控: {e}") from e

    return {
        "ok": True,
        "controller_id": getattr(controller, "id", None),
        "message_count": len(messages),
        "truncated": truncated,
        "chars": sum(len(m["text"]) for m in messages),
    }


__all__ = ["import_sessions_router"]
