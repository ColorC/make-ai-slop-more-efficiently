# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-10-17T12:00:00+08:00 type=infra status=active
# [OMNI] summary="验收清单（可选工具）：把用户原话冻结为清单、记证据、确定性检查 + 独立验收者；Stop hook 只在长回合首次声称完成时提醒一次。CLI 与 hook 共用，纯标准库。"
# [OMNI] why="2026-10-17 长跑诊断：≥32 次完成声明被推翻（见 docs/plans/ai-learning-observatory/[2026-08-02]LONGITUDINAL-MONITORING-OPERATIONS/longrun-health-2026-10-17/NEXT-CHANGES-v1.md §2）。同日晚用户：强制校验过度约束、造成刻板行为，改为可选工具 + 长回合最多提醒一次，不打断、不注入普通工作。"
"""Acceptance checklist (stdlib only; imported by ``omni accept`` and run directly by the Stop hook).

An optional tool; nothing requires a session to build one.  Lifecycle::

    omni accept init  --file A.json          # requests = the user's own messages, verbatim
    omni accept add   --file A.json --covers U1 --quote "<exact words>" --check "..." --layer L4 --user-path
    omni accept freeze --file A.json          # items frozen; binds A.json to the current session
    omni accept evidence --file A.json --item A1 --path <abs proof>
    omni accept done  --file A.json          # refresh + deterministic check + independent verifier

Hook (``python -I -S acceptance_gate.py hook --event Stop --provider <P>``): a turn that has
run an hour or more and ends on a completion claim gets one short reminder per session to
re-read the user's words.  Nothing else blocks or injects.  SessionStart / PreToolUse are
still accepted and ignored, so stale registrations stay harmless (re-injection and the Codex
``update_goal`` denial were retired 2026-10-17: forced verification made sessions rigid).
"""
from __future__ import annotations

# argparse / shutil / subprocess are imported where used, and tempfile not at all: hooks run this file cold on every
# Stop / SessionStart / PreToolUse and never need them (about 25 ms of the cold start, 10-17 importtime).
import datetime as _dt
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "omni.acceptance.v1"
LAYERS = ("L0", "L1", "L2", "L3", "L4")
INJECT_CHAR_BUDGET = 3500

NOISE_PREFIX = (
    "<system-reminder", "<command-", "<local-command", "# AGENTS.md", "<environment_context",
    "<user_instructions", "Caveat:", "<permissions", "<skills_instructions", "<INSTRUCTIONS>",
    "<user_shell_command", "<turn_aborted", "<task-notification", "[OMNI AGENT MESSAGE]",
    "<subagent_notification", "[Request interrupted", "This session is being continued",
)
CONTINUE_ONLY = {"continue", "继续", "继续。", "go on", "keep going", "接着做", "继续做"}


class GateError(Exception):
    pass


# ── time / io ─────────────────────────────────────────────────────────────


def _now() -> str:
    # Microseconds: freshness compares verdicts against evidence that can land in the same second.
    return _dt.datetime.now().astimezone().isoformat(timespec="microseconds")


def _epoch(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def state_root() -> Path:
    return Path(os.environ.get("OMNI_ACCEPT_HOME") or (Path.home() / ".omni" / "acceptance"))


def _atomic_write_json(path: Path, data: Any) -> None:
    # exclusive-create sibling temp + os.replace; not tempfile (it imports shutil/random, ~10 ms of every hook)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for attempt in range(100):
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{attempt}.tmp")
        try:
            fd = os.open(tmp, flags, 0o644)
            break
        except FileExistsError:
            continue
    else:
        raise OSError(f"no free temp name next to {path}")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8-sig"))


def record_event(kind: str, **fields: Any) -> None:
    """Append one decision to <state root>/events.jsonl for later review (``omni accept stats``).

    Called on lifecycle steps and gate decisions only, never on a hook's no-op path.
    """
    try:
        root = state_root()
        root.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": _now(), "kind": kind, **fields}, ensure_ascii=False, default=str)
        with (root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except Exception:  # noqa: BLE001 - the ledger must never change an outcome
        pass


def summarize_events(path: Path, since: str | None = None) -> dict[str, Any]:
    """Counts by kind (verdicts split by pass) for events at or after ``since``."""
    cutoff = _epoch(since) if since else 0.0
    kinds: dict[str, int] = {}
    sessions: set[str] = set()
    first = last = None
    if path.is_file():
        for raw in path.read_bytes().decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            at = str(event.get("at") or "")
            if _epoch(at) < cutoff:
                continue
            kind = str(event.get("kind") or "?")
            if kind == "verdict":
                kind = "verdict_pass" if event.get("passed") else "verdict_fail"
            kinds[kind] = kinds.get(kind, 0) + 1
            if event.get("session"):
                sessions.add(str(event["session"]))
            first = first or at
            last = at
    return {"file": str(path), "kinds": dict(sorted(kinds.items())), "sessions": len(sessions),
            "first": first, "last": last}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clip(text: str, limit: int) -> str:
    text = _norm(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── session identity and transcripts ──────────────────────────────────────


def _codex_roots() -> list[Path]:
    roots = [Path(os.environ["CODEX_HOME"])] if os.environ.get("CODEX_HOME") else []
    roots += sorted(p for p in Path.home().glob(".codex*") if p.is_dir())
    return list(dict.fromkeys(roots))


def _claude_roots() -> list[Path]:
    roots = [Path(os.environ["CLAUDE_CONFIG_DIR"])] if os.environ.get("CLAUDE_CONFIG_DIR") else []
    roots += sorted(p for p in Path.home().glob(".claude*") if p.is_dir())
    return list(dict.fromkeys(roots))


def find_transcript(provider: str, session_id: str) -> Path | None:
    hits: list[Path] = []
    if provider == "codex":
        for root in _codex_roots():
            hits += list((root / "sessions").glob(f"*/*/*/rollout-*{session_id}.jsonl"))
    else:
        for root in _claude_roots():
            hits += list((root / "projects").glob(f"*/{session_id}.jsonl"))
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def current_session(provider: str | None = None, session_id: str | None = None) -> tuple[str, str]:
    """Resolve the calling agent session.

    Both ids are present when one agent launched the other (children inherit the
    parent's id).  Guessing is unsafe: on 2026-10-17 a nested Codex run bound the
    *parent* Claude session because its transcript was the most recently written.
    So with both present the caller must say ``--provider``.
    """
    if session_id:
        if provider:
            return provider, session_id
        return ("codex" if session_id == os.environ.get("CODEX_THREAD_ID") else "claude_code"), session_id
    candidates = []
    codex_id = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    claude_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if codex_id:
        candidates.append(("codex", codex_id))
    if claude_id:
        candidates.append(("claude_code", claude_id))
    if provider:
        candidates = [c for c in candidates if c[0] == provider]
    if not candidates:
        raise GateError("无法确定当前会话：环境里没有 CODEX_THREAD_ID / CLAUDE_CODE_SESSION_ID；请用 --session 指定")
    if len(candidates) == 1:
        return candidates[0]
    raise GateError("环境里同时有 CODEX_THREAD_ID 和 CLAUDE_CODE_SESSION_ID（嵌套会话会继承上层的号）；"
                    "请用 --provider codex 或 --provider claude_code 指明你自己是哪个，或用 --session 给出会话号")


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text"):
            parts.append(item.get("text") or "")
    return "\n".join(parts)


def _strip_reminders(text: str) -> str:
    return re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()


def _queued_human_prompt(row: dict) -> str | None:
    """Claude Code stores words typed while a turn is running as a queued_command attachment, not a user row."""
    att = row.get("attachment")
    if row.get("type") != "attachment" or not isinstance(att, dict) or att.get("type") != "queued_command":
        return None
    if att.get("commandMode", "prompt") != "prompt":
        return None
    origin = att.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return None
    prompt = att.get("prompt")
    return prompt if isinstance(prompt, str) else _text_of(prompt)


GOAL_OBJECTIVE = "[goal objective] "


def _own_words(provider: str, row: dict) -> tuple[str, bool] | None:
    """``(text, typed while a turn ran)`` for a row carrying the user's words or a Codex goal objective."""
    if provider == "codex":
        payload = row.get("payload") or {}
        if row.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
            return None
        text = _text_of(payload.get("content"))
        if text.lstrip().startswith("<codex_internal_context"):
            match = re.search(r"<objective>\s*(.*?)\s*</objective>", text, re.S)
            return (GOAL_OBJECTIVE + match.group(1), False) if match else None
        return text, False
    prompt = _queued_human_prompt(row)
    if prompt is not None:
        return _strip_reminders(prompt), True
    if row.get("type") != "user" or row.get("isMeta") or row.get("isCompactSummary"):
        return None
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
        return None
    return _strip_reminders(_text_of(content)), False


def extract_user_messages(provider: str, transcript: Path) -> list[dict[str, str]]:
    """The user's own words in order (tool results, injected context and nudges removed)."""
    out: list[dict[str, str]] = []
    last = None
    queued: set[str] = set()
    with transcript.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = row.get("timestamp") or ""
            hit = _own_words(provider, row)
            if hit is None:
                continue
            text, from_queue = hit
            if from_queue:
                queued.add(text.strip())
            elif text.strip() in queued:
                continue
            stripped = text.lstrip()
            if not stripped or stripped.startswith(NOISE_PREFIX):
                continue
            if _norm(stripped).lower() in CONTINUE_ONLY:
                continue
            if text == last:
                continue
            last = text
            out.append({"ts": ts, "text": text.strip()})
    return out


def last_assistant_text(provider: str, transcript: Path | None, tail_bytes: int = 400_000) -> str:
    if not transcript or not transcript.exists():
        return ""
    with transcript.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(0, size - tail_bytes))
        chunk = stream.read().decode("utf-8", errors="replace")
    text = ""
    for line in chunk.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if provider == "codex":
            payload = row.get("payload") or {}
            if row.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
                candidate = _text_of(payload.get("content")) or "".join(
                    c.get("text", "") for c in payload.get("content") or [] if isinstance(c, dict)
                )
                if candidate.strip():
                    text = candidate
        else:
            if row.get("type") == "assistant":
                candidate = _text_of((row.get("message") or {}).get("content"))
                if candidate.strip():
                    text = candidate
    return text


# ── document ──────────────────────────────────────────────────────────────


def load(path: Path) -> dict[str, Any]:
    doc = _read_json(path)
    if doc.get("schema") != SCHEMA:
        raise GateError(f"不是验收清单（schema 应为 {SCHEMA}）：{path}")
    return doc


def _slug(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(path.resolve()))[-150:]


def frozen_path(path: Path) -> Path:
    return state_root() / "frozen" / (_slug(path) + ".json")


def binding_path(session_id: str) -> Path:
    return state_root() / "bindings" / (re.sub(r"[^A-Za-z0-9._-]+", "_", session_id) + ".json")


def _frozen_view(doc: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("id", "covers", "quote", "check", "layer", "user_path")
    return [{k: item.get(k) for k in keys} for item in doc.get("items", [])]


def bind(path: Path, provider: str, session_id: str) -> Path:
    target = binding_path(session_id)
    _atomic_write_json(target, {
        "acceptance": str(path.resolve()),
        "provider": provider,
        "session_id": session_id,
        "bound_at": _now(),
    })
    return target


def bound_acceptance(session_id: str) -> Path | None:
    target = binding_path(session_id)
    if not session_id or not target.exists():
        return None
    try:
        data = _read_json(target)
    except (OSError, json.JSONDecodeError):
        return None
    path = Path(data.get("acceptance") or "")
    return path if path.is_file() else None


# omnicompany patrol OMNI-035h: docs/ subdirectories hold no .json data; checklists are data.
_OMNI_DOCS = Path(__file__).resolve().parents[3] / "docs"


def placement_problem(path: Path) -> str | None:
    try:
        path.resolve().relative_to(_OMNI_DOCS)
    except ValueError:
        return None
    return (f"验收清单是数据产物，不放 {_OMNI_DOCS} 下（巡检 OMNI-035h）；"
            "放 omnicompany\\data\\services\\acceptance\\<项目或计划>\\（OMNI-041 白名单内），或项目自己的运行目录")


def init(path: Path, *, title: str, provider: str, session_id: str, extra_requests: list[str] | None = None) -> dict[str, Any]:
    if path.exists():
        raise GateError(f"验收清单已存在，不覆盖：{path}")
    problem = placement_problem(path)
    if problem:
        raise GateError(problem)
    transcript = find_transcript(provider, session_id)
    requests = extract_user_messages(provider, transcript) if transcript else []
    for text in extra_requests or []:
        requests.append({"ts": _now(), "text": text.strip()})
    if not requests:
        raise GateError("没有取到任何用户原话；用 --request 补充，或确认会话号")
    doc = {
        "schema": SCHEMA,
        "title": title,
        "created_at": _now(),
        "frozen_at": None,
        "source": {"provider": provider, "session_id": session_id, "transcript": str(transcript) if transcript else None},
        "requests": [{"id": f"U{i + 1}", "ts": r["ts"], "text": r["text"], "waived": None} for i, r in enumerate(requests)],
        "items": [],
        "amendments": [],
        "verdicts": [],
        "gate_overrides": [],
    }
    _atomic_write_json(path, doc)
    record_event("init", file=str(path.resolve()), provider=provider, session=session_id, requests=len(doc["requests"]))
    return doc


def _request(doc: dict[str, Any], request_id: str) -> dict[str, Any]:
    for request in doc["requests"]:
        if request["id"] == request_id:
            return request
    raise GateError(f"没有这条用户原话：{request_id}")


def _quote_in(doc: dict[str, Any], quote: str, covers: list[str]) -> bool:
    needle = _norm(quote)
    return bool(needle) and any(needle in _norm(_request(doc, rid)["text"]) for rid in covers)


def add_item(path: Path, *, covers: list[str], quote: str, check: str, layer: str, user_path: bool) -> dict[str, Any]:
    doc = load(path)
    if doc.get("frozen_at"):
        raise GateError("清单已冻结；改条目用 omni accept amend，并给出用户原话依据")
    if layer not in LAYERS:
        raise GateError(f"layer 必须是 {', '.join(LAYERS)}")
    if not _quote_in(doc, quote, covers):
        raise GateError("quote 必须是所覆盖原话里逐字出现的片段")
    item = {
        "id": f"A{len(doc['items']) + 1}",
        "covers": covers,
        "quote": quote,
        "check": check,
        "layer": layer,
        "user_path": user_path,
        "evidence": [],
    }
    doc["items"].append(item)
    _atomic_write_json(path, doc)
    return item


def refresh(path: Path) -> list[dict[str, Any]]:
    """Append user messages that arrived after init; they must be covered before done."""
    doc = load(path)
    source = doc.get("source") or {}
    transcript = Path(source["transcript"]) if source.get("transcript") else None
    if not transcript or not transcript.exists():
        return []
    known = {_norm(r["text"]) for r in doc["requests"]}
    added = []
    for message in extract_user_messages(source.get("provider") or "claude_code", transcript):
        if _norm(message["text"]) in known:
            continue
        request = {"id": f"U{len(doc['requests']) + 1}", "ts": message["ts"], "text": message["text"],
                   "waived": None, "added_after_freeze": bool(doc.get("frozen_at"))}
        doc["requests"].append(request)
        known.add(_norm(message["text"]))
        added.append(request)
    if added:
        _atomic_write_json(path, doc)
    return added


def structure_problems(doc: dict[str, Any]) -> list[str]:
    problems = []
    covered = {rid for item in doc.get("items", []) for rid in item.get("covers") or []}
    for request in doc.get("requests", []):
        if request.get("waived"):
            continue
        if request["id"] not in covered:
            problems.append(f"{request['id']} 没有任何验收项覆盖：「{_clip(request['text'], 80)}」")
    for item in doc.get("items", []):
        try:
            if not _quote_in(doc, item.get("quote") or "", item.get("covers") or []):
                problems.append(f"{item['id']} 的 quote 不是所覆盖原话里的逐字片段")
        except GateError as exc:
            problems.append(f"{item['id']}：{exc}")
        if item.get("layer") not in LAYERS:
            problems.append(f"{item['id']} 的 layer 无效")
    if not any(item.get("user_path") for item in doc.get("items", [])):
        problems.append("缺少用户真实路径项（--user-path，按用户会怎么用来验）")
    return problems


def freeze(path: Path, *, provider: str, session_id: str) -> dict[str, Any]:
    doc = load(path)
    problems = structure_problems(doc)
    if problems:
        raise GateError("冻结前需先补齐：\n- " + "\n- ".join(problems))
    doc["frozen_at"] = _now()
    _atomic_write_json(path, doc)
    _atomic_write_json(frozen_path(path), {"path": str(path.resolve()), "frozen_at": doc["frozen_at"],
                                            "items": _frozen_view(doc)})
    bind(path, provider, session_id)
    record_event("freeze", file=str(path.resolve()), provider=provider, session=session_id, items=len(doc["items"]))
    return doc


def amend(path: Path, *, item_id: str | None, field: str, value: str, basis_quote: str,
          covers: list[str] | None = None, layer: str = "L4", user_path: bool = False) -> dict[str, Any]:
    """Change a frozen checklist only on the user's own words (``basis_quote`` must be verbatim)."""
    refresh(path)
    doc = load(path)
    if not any(_norm(basis_quote) and _norm(basis_quote) in _norm(r["text"]) for r in doc["requests"]):
        raise GateError("basis_quote 必须逐字出自用户原话")
    record = {"at": _now(), "item": item_id, "field": field, "value": value, "basis_quote": basis_quote}
    if field == "add":
        if not covers or not _quote_in(doc, value, covers):
            raise GateError("新增条目：value 是原话逐字片段，并用 --covers 指明原话编号")
        item = {"id": f"A{len(doc['items']) + 1}", "covers": covers, "quote": value, "check": basis_quote,
                "layer": layer, "user_path": user_path, "evidence": []}
        doc["items"].append(item)
        record["item"] = item["id"]
    elif field == "waive-request":
        _request(doc, value)["waived"] = {"at": record["at"], "basis_quote": basis_quote}
    elif field in ("check", "layer", "quote", "remove"):
        target = next((i for i in doc["items"] if i["id"] == item_id), None)
        if target is None:
            raise GateError(f"没有这个验收项：{item_id}")
        if field == "remove":
            doc["items"].remove(target)
        else:
            target[field] = value
    else:
        raise GateError("field 只能是 add / check / layer / quote / remove / waive-request")
    doc["amendments"].append(record)
    _atomic_write_json(path, doc)
    if doc.get("frozen_at"):
        _atomic_write_json(frozen_path(path), {"path": str(path.resolve()), "frozen_at": doc["frozen_at"],
                                                "items": _frozen_view(doc)})
    record_event("amend", file=str(path.resolve()), field=field, item=record["item"],
                 session=(doc.get("source") or {}).get("session_id"))
    return record


def add_evidence(path: Path, *, item_id: str, proof: Path | None, note: str, command: str | None,
                 exit_code: int | None) -> dict[str, Any]:
    doc = load(path)
    target = next((i for i in doc["items"] if i["id"] == item_id), None)
    if target is None:
        raise GateError(f"没有这个验收项：{item_id}")
    if proof is None and not command:
        raise GateError("证据要给 --path（截图、日志、回执、产物）或 --cmd（实际运行的检查命令）")
    entry: dict[str, Any] = {"recorded_at": _now(), "note": note}
    if proof is not None:
        resolved = proof.resolve()
        if not resolved.exists():
            raise GateError(f"证据文件不存在：{resolved}")
        entry["path"] = str(resolved)
    if command:
        entry["cmd"] = command
        entry["exit_code"] = exit_code
    target["evidence"].append(entry)
    _atomic_write_json(path, doc)
    return entry


def retract_evidence(path: Path, *, item_id: str, index: int, reason: str) -> dict[str, Any]:
    """Withdraw an evidence entry recorded by mistake; it stays in the file, with the reason, for the verifier."""
    if not reason.strip():
        raise GateError("撤回证据要写理由（--note）")
    doc = load(path)
    target = next((i for i in doc["items"] if i["id"] == item_id), None)
    if target is None:
        raise GateError(f"没有这个验收项：{item_id}")
    evidence = target["evidence"]
    if not 1 <= index <= len(evidence):
        raise GateError(f"{item_id} 只有 {len(evidence)} 条证据，没有第 {index} 条")
    entry = evidence[index - 1]
    if entry.get("retracted"):
        raise GateError(f"{item_id} 第 {index} 条证据已经撤回过")
    entry["retracted"] = {"at": _now(), "reason": reason}
    _atomic_write_json(path, doc)
    record_event("evidence_retract", file=str(path.resolve()), item=item_id, index=index,
                 session=(doc.get("source") or {}).get("session_id"))
    return entry


def move(path: Path, new_path: Path, *, carry: list[Path] | None = None) -> dict[str, Any]:
    """Relocate a checklist without touching what was judged.

    The checklist, the files/dirs in ``carry`` and the verdict transcripts next to it move into the new
    folder; evidence paths, verdict paths, the frozen snapshot and session bindings are re-pointed. Nothing
    is re-recorded, so an existing pass stays fresh; the move itself is logged in ``moves``.
    """
    src, dst = path.resolve(), new_path.resolve()
    doc = load(src)
    if dst.exists():
        raise GateError(f"目标已存在，不覆盖：{dst}")
    problem = placement_problem(dst)
    if problem:
        raise GateError(problem)
    plan: list[tuple[Path, Path]] = []
    for extra in carry or []:
        old = extra.resolve()
        if not old.exists():
            raise GateError(f"要一起搬的路径不存在：{old}")
        plan.append((old, dst.parent / old.name))
    for verdict in doc.get("verdicts") or []:
        raw = Path(verdict.get("raw_path") or "")
        if raw.is_file() and raw.resolve().parent == src.parent:
            plan.append((raw.resolve(), dst.parent / raw.name))
    for _old, new in plan:
        if new.exists():
            raise GateError(f"目标已存在，不覆盖：{new}")

    def relocate(value: str | None) -> str | None:
        if not value:
            return value
        p = Path(value).resolve()
        for old, new in [(src, dst), *plan]:
            if p == old or old in p.parents:
                return str(new / p.relative_to(old))
        return value

    for item in doc.get("items") or []:
        for entry in item.get("evidence") or []:
            if entry.get("path"):
                entry["path"] = relocate(entry["path"])
    for verdict in doc.get("verdicts") or []:
        verdict["raw_path"] = relocate(verdict.get("raw_path"))
    doc.setdefault("moves", []).append({"at": _now(), "from": str(src), "to": str(dst),
                                        "carried": [str(old) for old, _ in plan]})
    import shutil

    dst.parent.mkdir(parents=True, exist_ok=True)
    for old, new in plan:
        shutil.move(str(old), str(new))
    _atomic_write_json(dst, doc)
    src.unlink()
    old_snapshot = frozen_path(src)
    if old_snapshot.exists():
        snapshot = _read_json(old_snapshot)
        snapshot["path"] = str(dst)
        _atomic_write_json(frozen_path(dst), snapshot)
        old_snapshot.unlink()
    rebound = []
    bindings = state_root() / "bindings"
    for binding in sorted(bindings.glob("*.json")) if bindings.is_dir() else []:
        try:
            data = _read_json(binding)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("acceptance") and Path(data["acceptance"]).resolve() == src:
            data["acceptance"], data["moved_at"] = str(dst), _now()
            _atomic_write_json(binding, data)
            rebound.append(data.get("session_id"))
    record_event("move", file=str(dst), source=str(src), carried=len(plan), rebound=len(rebound),
                 session=(doc.get("source") or {}).get("session_id"))
    return {"file": str(dst), "carried": [str(new) for _, new in plan], "rebound": rebound}


def check(path: Path) -> dict[str, Any]:
    doc = load(path)
    problems = structure_problems(doc)
    if not doc.get("frozen_at"):
        problems.append("清单还没冻结（omni accept freeze）")
    else:
        snapshot = frozen_path(path)
        if snapshot.exists():
            if _read_json(snapshot).get("items") != _frozen_view(doc):
                problems.append("验收项在冻结后被直接改动过（只能用 omni accept amend 并给出用户原话依据）")
        else:
            problems.append(f"找不到冻结快照：{snapshot}")
    frozen_at = _epoch(doc.get("frozen_at"))
    status = []
    for item in doc.get("items", []):
        evidence = item.get("evidence") or []
        fresh = [e for e in evidence if _epoch(e.get("recorded_at")) >= frozen_at and not e.get("retracted")]
        missing_files = [e["path"] for e in fresh if e.get("path") and not Path(e["path"]).exists()]
        # fix-and-rerun: the same check command counts by its latest recorded result
        latest_by_cmd = {e["cmd"]: e for e in fresh if e.get("cmd")}
        failed_cmds = [e for e in latest_by_cmd.values() if e.get("exit_code") not in (0, None)]
        ok = bool(fresh) and not missing_files and not failed_cmds
        if not fresh:
            problems.append(f"{item['id']} 没有冻结之后记录的证据")
        for missing in missing_files:
            problems.append(f"{item['id']} 的证据文件已不存在：{missing}")
        if failed_cmds:
            problems.append(f"{item['id']} 有退出码非 0 的检查命令")
        status.append({"id": item["id"], "ok": ok, "evidence": len(fresh)})
    return {"ok": not problems, "problems": problems, "items": status, "file": str(path.resolve())}


def latest_verdict(doc: dict[str, Any]) -> dict[str, Any] | None:
    verdicts = doc.get("verdicts") or []
    return verdicts[-1] if verdicts else None


def _last_change_epoch(doc: dict[str, Any]) -> float:
    stamps = [_epoch(doc.get("frozen_at"))]
    stamps += [_epoch(a.get("at")) for a in doc.get("amendments") or []]
    stamps += [_epoch(e.get("recorded_at")) for i in doc.get("items") or [] for e in i.get("evidence") or []]
    stamps += [_epoch(r.get("ts")) for r in doc.get("requests") or [] if r.get("added_after_freeze")]
    return max(stamps or [0.0])


def fresh_pass(doc: dict[str, Any]) -> bool:
    verdict = latest_verdict(doc)
    return bool(verdict and verdict.get("pass") and _epoch(verdict.get("at")) >= _last_change_epoch(doc))


# ── independent verifier ──────────────────────────────────────────────────

VERIFIER_PROMPT = """你是独立验收者，与完成这项工作的 agent 无关。你只相信可检查的状态（文件内容、命令输出、截图、真实运行结果），不相信任何人的自述。

下面是：①用户的原话（逐字，按时间顺序）；②据此冻结的验收项与各自的证据路径。请逐项核验：
- 打开每个证据文件，判断它是否真的证明了该验收项；能用只读命令复核的就复核；
- 用户真实路径项（user_path=true）要按用户会怎么用来判断，证据只是进程/产物存在不算通过；
- 再通读用户原话，找出原话里出现、但没有任何验收项覆盖的约束或要求。
只报影响正确性或用户明确要求的缺口，不提风格建议。不要修改任何文件。

最后只输出一个 JSON 对象（不要代码块以外的多余文字）：
{"items":[{"id":"A1","pass":true,"reason":"…","checked":["…"]}],"missing_user_constraints":["…"],"overall_pass":true}

---
"""


def _verifier_payload(doc: dict[str, Any]) -> str:
    view = {
        "title": doc.get("title"),
        "requests": [{k: r.get(k) for k in ("id", "ts", "text", "waived")} for r in doc["requests"]],
        "items": [
            {k: i.get(k) for k in ("id", "covers", "quote", "check", "layer", "user_path", "evidence")}
            for i in doc["items"]
        ],
        "amendments": doc.get("amendments") or [],
    }
    return VERIFIER_PROMPT + json.dumps(view, ensure_ascii=False, indent=2)


def _parse_verdict(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    found = None
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "overall_pass" in obj:
            found = obj
    return found


def _verifier_command(engine: str, model: str | None, out_file: Path) -> list[str]:
    import shutil

    override = os.environ.get("OMNI_ACCEPT_VERIFIER_CMD")
    if override:
        return [*json.loads(override), str(out_file)]
    if engine == "codex":
        exe = shutil.which("codex") or "codex"
        return [exe, "exec", "--skip-git-repo-check", "-s", "read-only", "-m", model or "gpt-6-luna",
                "-o", str(out_file), "-"]
    exe = shutil.which("claude") or "claude"
    return [exe, "-p", "--model", model or "sonnet", "--allowedTools", "Read,Glob,Grep"]


def verify(path: Path, *, engine: str | None = None, model: str | None = None, timeout: int = 1800) -> dict[str, Any]:
    import subprocess

    doc = load(path)
    worker = (doc.get("source") or {}).get("provider") or "claude_code"
    engine = engine or ("claude" if worker == "codex" else "codex")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = path.with_name(f"{path.stem}.verdict-{stamp}.txt")
    command = _verifier_command(engine, model, out_file)
    try:
        proc = subprocess.run(command, input=_verifier_payload(doc), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, cwd=str(path.parent))
        raw = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
        raw = raw or proc.stdout
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        raw, exit_code = "", 124
    if not out_file.exists():
        out_file.write_text(raw, encoding="utf-8")
    parsed = _parse_verdict(raw)
    passed = bool(parsed and parsed.get("overall_pass") and all(i.get("pass") for i in parsed.get("items") or [])
                  and not parsed.get("missing_user_constraints"))
    ids = {i["id"] for i in doc["items"]}
    if parsed and ids - {i.get("id") for i in parsed.get("items") or []}:
        passed = False
    verdict = {
        "at": _now(),
        "engine": engine,
        "model": model,
        "exit_code": exit_code,
        "pass": passed,
        "raw_path": str(out_file.resolve()),
        "failed": [i for i in (parsed or {}).get("items") or [] if not i.get("pass")],
        "missing_user_constraints": (parsed or {}).get("missing_user_constraints") or [],
        "parse_error": parsed is None,
    }
    doc = load(path)
    doc.setdefault("verdicts", []).append(verdict)
    _atomic_write_json(path, doc)
    record_event("verdict", file=str(path.resolve()), engine=engine, passed=passed, exit_code=exit_code,
                 failed=len(verdict["failed"]), missing=len(verdict["missing_user_constraints"]),
                 parse_error=verdict["parse_error"], session=(doc.get("source") or {}).get("session_id"))
    return verdict


def done(path: Path, *, engine: str | None = None, model: str | None = None, timeout: int = 1800) -> dict[str, Any]:
    added = refresh(path)
    result = check(path)
    result["new_requests"] = [r["id"] for r in added]
    if not result["ok"]:
        result["verdict"] = None
        record_event("done_check_failed", file=str(path.resolve()), problems=len(result["problems"]),
                     session=(load(path).get("source") or {}).get("session_id"))
        return result
    verdict = verify(path, engine=engine, model=model, timeout=timeout)
    result["verdict"] = verdict
    result["ok"] = verdict["pass"]
    return result


def status_text(path: Path, budget: int = INJECT_CHAR_BUDGET) -> str:
    doc = load(path)
    state = "已冻结" if doc.get("frozen_at") else "未冻结"
    lines = [f"【验收清单·{state}】{doc.get('title')}  文件：{path.resolve()}",
             "完成前必须 `omni accept done --file <该文件>` 通过（确定性检查 + 独立验收者）；条目只能凭用户原话 `omni accept amend` 修改。",
             "用户原话："]
    for request in doc["requests"]:
        mark = "（已豁免）" if request.get("waived") else ""
        lines.append(f"- {request['id']}{mark}：{_clip(request['text'], 240)}")
    verdict = latest_verdict(doc)
    lines.append("验收项（证据数 / 层级）：")
    frozen_at = _epoch(doc.get("frozen_at"))
    for item in doc["items"]:
        count = sum(1 for e in item.get("evidence") or [] if _epoch(e.get("recorded_at")) >= frozen_at)
        lines.append(f"- {item['id']} [{item.get('layer')}{'·用户路径' if item.get('user_path') else ''}] "
                     f"证据 {count}：{_clip(item.get('check') or '', 120)}")
    if verdict:
        lines.append(f"最近验收：{'通过' if verdict.get('pass') else '未通过'}（{verdict.get('at')}）"
                     + ("" if fresh_pass(doc) else "，之后又有变更，需要重新验收"))
    text = "\n".join(lines)
    return text if len(text) <= budget else text[: budget - 40] + f"\n…（截断，完整内容见 {path.resolve()}）"


# ── hooks ─────────────────────────────────────────────────────────────────

_CLAIM = re.compile(
    r"(全部|都|均|已经|已|基本)?(完成|做完|搞定|收尾|交付|收口)(了|啦|。|！|!|，|,|\s|$)"
    r"|(?<![a-z])(all done|done|completed|finished|ready for review)(?![a-z])",
    re.I,
)
# Negations, future plans and meta talk right before the verb ("没有声明完成", "是否完成", "结果出来再收尾").
_NEGATION = re.compile(r"(未|没|没有|尚未|还没|并未|仍未|暂未|不能|无法|不会|不算|待|不|声明|宣布|宣称|是否|假装|如果|若|等"
                       r"|再|将|要|准备|稍后)\s*$")
_QUOTED = re.compile(r"```.*?```|`[^`\n]*`|「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"\n]*\"", re.S)


def _strip_quotes(text: str, requests: list[str] | tuple[str, ...] = ()) -> str:
    """Drop quoted spans, block quotes and verbatim clauses of the user's own words."""
    text = _QUOTED.sub(" ", text or "")
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    text = re.sub(r"omni\s+accept\s+done", " ", text, flags=re.I)
    for request in requests:
        for clause in re.split(r"[，。；！？、,.;!?\n]+", request or ""):
            clause = clause.strip()
            if len(clause) >= 4:
                text = text.replace(clause, " ")
    return text


def claims_completion(text: str, requests: list[str] | tuple[str, ...] = ()) -> bool:
    text = _strip_quotes(text, requests)
    for match in _CLAIM.finditer(text):
        before = text[max(0, match.start() - 6): match.start()]
        if _NEGATION.search(before):
            continue
        return True
    return False


def _gate_state_path(session_id: str) -> Path:
    return state_root() / "gate" / (re.sub(r"[^A-Za-z0-9._-]+", "_", session_id) + ".json")


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


REMIND_MIN_MINUTES = 60
REMIND_SCAN_BYTES = 16 << 20
_TIMESTAMP = re.compile(rb'"timestamp"\s*:\s*"([^"]+)"')
_USER_ROW = re.compile(rb'"type"\s*:\s*"user"|"role"\s*:\s*"user"|queued_command')


def _session_head(transcript: Path | None) -> tuple[float, bool]:
    """(start epoch, is a Codex sub-thread) from the transcript's first 64 KiB; (0.0, False) if unknown."""
    if not transcript:
        return 0.0, False
    try:
        with transcript.open("rb") as stream:
            head = stream.read(65536)
    except OSError:
        return 0.0, False
    match = _TIMESTAMP.search(head)
    started = _epoch(match.group(1).decode("utf-8", errors="replace")) if match else 0.0
    # Codex session_meta.source.subagent marks a spawned sub-thread; its parent owns acceptance.
    return started, b'"subagent"' in head.split(b"\n", 1)[0]


def _turn_started(provider: str, transcript: Path, cap: int = REMIND_SCAN_BYTES) -> float:
    """Epoch of the user's latest own message, reading the transcript backwards.

    Tool results, injected notices and Codex goal re-injections do not start a turn.  With no
    such message in the last ``cap`` bytes, the oldest timestamp read is returned (the turn is
    at least that old); 0.0 when nothing is known.
    """
    oldest = 0.0
    with transcript.open("rb") as stream:
        end = pos = stream.seek(0, os.SEEK_END)
        partial = b""
        while pos > 0 and end - pos < cap:
            step = min(1 << 20, pos)
            pos -= step
            stream.seek(pos)
            lines = (stream.read(step) + partial).split(b"\n")
            partial = lines.pop(0) if pos > 0 else b""
            for line in reversed(lines):
                stamp = _TIMESTAMP.search(line)
                if stamp:
                    oldest = _epoch(stamp.group(1).decode("utf-8", errors="replace")) or oldest
                if not _USER_ROW.search(line):
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                hit = _own_words(provider, row) if isinstance(row, dict) else None
                text = hit[0].lstrip() if hit else ""
                if text and not text.startswith(NOISE_PREFIX) and not text.startswith(GOAL_OBJECTIVE):
                    return _epoch(row.get("timestamp")) or oldest
    return oldest


def hook_stop(payload: dict[str, Any], provider: str) -> int:
    """At most one short reminder per session, for a long turn that ends on a completion claim.

    Short turns, planning and ordinary replies are never touched; no checklist is required, a
    bound one only lends its path.  ``OMNI_ACCEPT_NUDGE=0`` turns it off for automated pipelines.
    """
    if os.environ.get("OMNI_ACCEPT_NUDGE", "").strip().lower() in ("0", "off", "false", "no"):
        return 0
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    if not session_id or payload.get("stop_hook_active") or _gate_state_path(session_id).exists():
        return 0
    path = bound_acceptance(session_id)
    requests: list[str] = []
    if path:
        try:
            doc = load(path)
        except (GateError, OSError, json.JSONDecodeError):
            doc = {}
        if doc and fresh_pass(doc):
            return 0
        requests = [r.get("text") or "" for r in doc.get("requests") or []]
    transcript = Path(payload["transcript_path"]) if payload.get("transcript_path") else None
    message = payload.get("last_assistant_message")
    if message is None:
        message = last_assistant_text(provider, transcript)
    if not claims_completion(message or "", requests):
        return 0
    started, subagent = _session_head(transcript)
    now = _dt.datetime.now().timestamp()
    if subagent or not started or now - started < REMIND_MIN_MINUTES * 60:
        return 0
    try:
        turn = _turn_started(provider, transcript)
    except OSError:
        return 0
    minutes = round((now - turn) / 60) if turn else 0
    if minutes < REMIND_MIN_MINUTES:
        return 0
    _atomic_write_json(_gate_state_path(session_id), {"reminded_at": _now(), "minutes": minutes})
    record_event("stop_remind", session=session_id, provider=provider, minutes=minutes,
                 file=str(path.resolve()) if path else None)
    _emit({"decision": "block", "reason": (
        f"收尾提醒（本会话只出现这一次）：这一轮已连续工作约 {minutes} 分钟。"
        "报完成前对照用户原话快速过一遍：每条要求都做到了吗？用户实际会走的路径验过吗？"
        + (f"可对照本会话的清单 {path.resolve()}。" if path else "")
        + "都做到了就直接收尾，不需要补任何流程或独立验收。"
    )})
    return 0


DEBUG_ENV_KEYS = ("OMNI_ACCEPT_HOME", "OMNI_SUBAGENT_POLICY_HOME", "OMNI_SUBAGENT_POLICY_MAX_AGE_HOURS",
                  "OMNI_SUBAGENT_POLICY_MAX_COMPACTIONS", "OMNI_PROBE", "OMNI_AGENT_PROVIDER", "CODEX_HOME",
                  "CODEX_THREAD_ID", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID")


def debug_log(script: str, event: str, payload: dict[str, Any], outcome: Any) -> None:
    """Append one line to ~/.omni/hook-debug.jsonl while ~/.omni/hook-debug.on exists (one stat otherwise).

    The switch is a fixed file, not an env var, because the question it answers is
    usually whether the hook saw the caller's environment at all.
    """
    try:
        root = Path.home() / ".omni"
        if (root / "hook-debug.on").exists():
            _debug_append(root, script, event, payload, outcome)
    except Exception:  # noqa: BLE001 - diagnostics must never change the hook's outcome
        pass


def _debug_append(root: Path, script: str, event: str, payload: dict[str, Any], outcome: Any) -> None:
    record = {
        "at": _now(), "script": script, "event": event, "pid": os.getpid(),
        "payload_keys": sorted(payload), "session_id": payload.get("session_id"),
        "tool_name": payload.get("tool_name"), "tool_input": payload.get("tool_input"),
        "last_assistant_message": (payload.get("last_assistant_message") or "")[:200] or None,
        "transcript_path": payload.get("transcript_path"), "source": payload.get("source"),
        # Named variables only: prefix matching once captured a value that embedded an API key.
        "env": {k: os.environ[k][:200] for k in DEBUG_ENV_KEYS if k in os.environ},
        "outcome": outcome,
    }
    with (root / "hook-debug.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_payload() -> dict[str, Any]:
    # Bytes, then UTF-8: under ``-I`` a text-mode stdin uses the console code page (cp936 here)
    # and silently garbles the provider's UTF-8 JSON (2026-10-17 live test).
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    payload = json.loads(raw) if raw.strip() else {}
    return payload if isinstance(payload, dict) else {}


def run_hook(event: str, provider: str) -> int:
    try:
        payload = read_payload()
    except (OSError, json.JSONDecodeError):
        return 0
    real_stdout, captured = sys.stdout, io.StringIO()
    sys.stdout = captured
    code = 0
    try:
        if event == "Stop":  # SessionStart / PreToolUse: retired, accepted as no-ops
            code = hook_stop(payload, provider)
    except Exception as exc:  # noqa: BLE001 - a broken gate must never wedge the agent
        sys.stderr.write(f"acceptance gate hook error (ignored): {exc}\n")
        code = 0
    finally:
        sys.stdout = real_stdout
    out = captured.getvalue()
    sys.stdout.write(out)
    sys.stdout.flush()
    debug_log("acceptance_gate", event, payload, {"exit": code, "stdout": out[:300]})
    return code


# ── hook installation ─────────────────────────────────────────────────────

_PY = Path(sys.executable)


def _hook_command(script: Path, args: str, python: Path) -> tuple[str, str]:
    command = f'"{python}" -I -S "{script}" {args}'
    windows = f"& '{python}' -I -S '{script}' {args}; exit $LASTEXITCODE"
    return command, windows


_MARK = "[omnicompany acceptance-gate]"
_POLICY_MARK = "[omnicompany subagent-policy]"
_MARKS = (_MARK, _POLICY_MARK)


def desired_groups(provider: str, script: Path, python: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """``(event, marker, group)`` for every runtime hook this provider gets."""
    plan = [("Stop", None, _MARK, script, f"hook --event Stop --provider {provider}")]
    if provider == "codex":
        # Wrapped in .* so the matcher hits whether Codex anchors it or prefixes a tool namespace
        # (2026-10-17 live test: a bare "followup_task|send_input" never dispatched).
        plan.append(("PreToolUse", ".*followup_task.*|.*send_input.*", _POLICY_MARK,
                     script.with_name("subagent_policy.py"), "--provider codex"))
    out = []
    for event, matcher, mark, path, args in plan:
        command, windows = _hook_command(path, args, python)
        handler: dict[str, Any] = {"type": "command", "command": command, "timeout": 15, "_omnicompany": mark}
        if provider == "codex":
            handler["commandWindows"] = windows
        group: dict[str, Any] = {"hooks": [handler]}
        if matcher:
            group["matcher"] = matcher
        out.append((event, mark, group))
    return out


def install_hooks(provider: str, target: Path, *, script: Path, python: Path = _PY,
                  codex_config: Path | None = None) -> dict[str, Any]:
    """Idempotently append the hook groups and drop retired gate groups (SessionStart, update_goal).

    Appending keeps existing indices; a dropped group shifts the ones after it, and ours are re-trusted below.
    """
    import shutil

    data = _read_json(target) if target.exists() else {}
    hooks = data.setdefault("hooks", {})
    changed = []
    desired = desired_groups(provider, script, python)
    wanted = {event for event, mark, _ in desired if mark == _MARK}
    for event in list(hooks):
        kept = [g for g in hooks[event]
                if event in wanted or not any(h.get("_omnicompany") == _MARK for h in g.get("hooks") or [])]
        if len(kept) != len(hooks[event]):
            changed.append(f"{event}:removed")
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    for event, mark, group in desired:
        groups = hooks.setdefault(event, [])
        existing = [g for g in groups if any(h.get("_omnicompany") == mark for h in g.get("hooks") or [])]
        label = f"{event}{'' if mark == _MARK else ':subagent-policy'}"
        if existing:
            if existing[0] != group:
                groups[groups.index(existing[0])] = group
                changed.append(f"{label}:updated")
            continue
        groups.append(group)
        changed.append(f"{label}:added")
    backup = None
    if changed:
        if target.exists():
            backup = target.with_name(target.name + f".bak-acceptance-{_dt.datetime.now():%Y%m%d%H%M%S}")
            shutil.copy2(target, backup)
        _atomic_write_json(target, data)
    trust = []
    if provider == "codex" and codex_config is not None:
        try:
            from omnicompany.core.codex_hook_trust import EVENT_LABELS, hook_hash, write_trust
        except ImportError:  # run as a bare script (python -I -S acceptance_gate.py ...)
            from codex_hook_trust import EVENT_LABELS, hook_hash, write_trust  # type: ignore[import-not-found,no-redef]

        # Trust only the gate's own handlers; other untrusted hooks stay the user's decision.
        ours = []
        for event, groups in hooks.items():
            for gi, group in enumerate(groups):
                for hi, handler in enumerate(group.get("hooks") or []):
                    if handler.get("_omnicompany") in _MARKS and event in EVENT_LABELS:
                        key = f"{target}:{EVENT_LABELS[event]}:{gi}:{hi}"
                        ours.append((key, hook_hash(event, group.get("matcher"), handler)))
        trust = write_trust(codex_config, ours)
    return {"target": str(target), "changed": changed, "backup": str(backup) if backup else None, "trust": trust}


# ── CLI (argparse; ``omni accept`` wraps the same functions) ─────────────


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    args_in = sys.argv[1:] if argv is None else argv
    # hook fast path (no argparse): hook --event <E> [--provider <P>]
    if len(args_in) in (3, 5) and args_in[0] == "hook" and args_in[1] == "--event" \
            and args_in[2] in ("SessionStart", "Stop", "PreToolUse") and (len(args_in) == 3 or args_in[3] == "--provider"):
        return run_hook(args_in[2], args_in[4] if len(args_in) == 5 else "claude_code")
    import argparse

    parser = argparse.ArgumentParser(prog="acceptance_gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    hook = sub.add_parser("hook")
    hook.add_argument("--event", required=True, choices=["SessionStart", "Stop", "PreToolUse"])
    hook.add_argument("--provider", default="claude_code")
    inst = sub.add_parser("install-hooks")
    inst.add_argument("--provider", required=True, choices=["claude_code", "codex"])
    inst.add_argument("--target", required=True, type=Path)
    inst.add_argument("--codex-config", type=Path)
    inst.add_argument("--script", type=Path, default=Path(__file__).resolve())
    inst.add_argument("--python", type=Path, default=_PY)
    args = parser.parse_args(argv)
    if args.cmd == "hook":
        return run_hook(args.event, args.provider)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    _print(install_hooks(args.provider, args.target.absolute(), script=args.script, python=args.python,
                         codex_config=args.codex_config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
