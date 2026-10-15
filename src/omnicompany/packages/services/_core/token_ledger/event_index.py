"""Incremental, scoped usage-event index for precise token-ledger windows.

Only usage and session metadata are persisted. Time is usage-report time, not
request start time. Codex input includes cached input; reasoning is in output.
Copied pre-fork history establishes a baseline but is not billed to the fork.
Repeated copies of one native session are deduplicated by native/event identity.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .codex_source import _TOTAL_FIELDS, _DIRTY_ROUND_TOKEN_THRESHOLD

FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens",
          "cache_creation_tokens", "reasoning_output_tokens")


def timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def source_roots() -> list[tuple[str, Path]]:
    from omnicompany.core.provider_identity import codex_home_for, claude_home_for

    candidates = [("codex", Path.home() / ".codex" / "sessions"),
                  ("claude_code", Path.home() / ".claude" / "projects")]
    candidates += [(p, codex_home_for(p) / "sessions") for p in ("codex", "codex_2")]
    candidates += [(p, claude_home_for(p) / "projects") for p in ("claude_code", "claude_2")]
    seen = set()
    result = []
    for provider, root in candidates:
        key = str(root.resolve()).casefold()
        if key not in seen and root.is_dir():
            result.append((provider, root))
            seen.add(key)
    return result


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS sources (path TEXT PRIMARY KEY, state TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        key TEXT PRIMARY KEY, session_id TEXT NOT NULL, provider TEXT NOT NULL,
        timestamp REAL NOT NULL, model TEXT NOT NULL, usage TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS event_session_time ON events(session_id,timestamp)")
    return db


def _event(db, sid, provider, ts, model, usage, identity):
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    # Claude can repeat one message's usage while streaming; keep the latest
    # complete snapshot instead of counting each fragment as another call.
    prior = db.execute("SELECT timestamp,usage FROM events WHERE key=?", (key,)).fetchone()
    if prior:
        previous_usage = json.loads(prior[1])
        if ts < prior[0] or any(usage[k] < previous_usage[k] for k in FIELDS):
            return  # an older/incomplete copy cannot replace a fuller snapshot
    db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
               (key, sid, provider, ts, model, json.dumps(usage)))


def _index_file(db, path: Path, provider: str, requested_sid: str) -> dict:
    saved = db.execute("SELECT state FROM sources WHERE path=?", (str(path),)).fetchone()
    state = json.loads(saved[0]) if saved else {}
    if path.stat().st_size < state.get("offset", 0):
        # Event keys make replay safe; retain existing events if source rotates.
        state = {}
    state.setdefault("session_id", requested_sid)
    state.setdefault("issues", {})
    state["provider"] = provider
    state["path"] = str(path)
    is_codex = provider.startswith("codex")

    def issue(name):
        state["issues"][name] = state["issues"].get(name, 0) + 1

    with path.open("rb") as handle:
        handle.seek(state.get("offset", 0))
        while True:
            line = handle.readline()
            if not line or not line.endswith(b"\n"):
                break  # retain incomplete final line for next incremental run
            state["offset"] = handle.tell()
            needles = (b'"session_meta"', b'"turn_context"', b'"token_count"') if is_codex else (b'"usage"',)
            if not any(x in line for x in needles):
                continue
            try:
                rec = json.loads(line)
            except (ValueError, UnicodeError):
                issue("invalid_usage_json")
                continue
            ts = timestamp(rec.get("timestamp"))
            payload = rec.get("payload") or {}
            if is_codex:
                if rec.get("type") == "session_meta":
                    state["session_id"] = payload.get("id") or payload.get("session_id") or requested_sid
                    state["created"] = timestamp(payload.get("timestamp")) or ts
                    state["cwd"] = payload.get("cwd", "")
                    state["forked_from"] = payload.get("forked_from_id")
                    continue
                if payload.get("model"):
                    state["model"] = payload["model"]
                if rec.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                total = (payload.get("info") or {}).get("total_token_usage")
                if not isinstance(total, dict):
                    continue
                try:
                    total = {k: int(total.get(k) or 0) for k in _TOTAL_FIELDS}
                except (ValueError, TypeError):
                    issue("invalid_counter")
                    continue
                baseline = state.get("baseline", dict.fromkeys(_TOTAL_FIELDS, 0))
                delta = {k: total[k] - baseline[k] for k in _TOTAL_FIELDS}
                if any(v < 0 for v in delta.values()):
                    state["baseline"] = total
                    issue("counter_reset_baseline_not_charged")
                    continue
                if not any(delta.values()):
                    continue
                # Inherited events can have large cumulative counters; they
                # still establish the fork baseline and never count as work.
                if ts is not None and state.get("created") and ts < state["created"]:
                    state["baseline"] = total
                    issue("inherited_events_excluded")
                    continue
                if state.get("forked_from") and "baseline" not in state:
                    # Without copied usage there is no evidence how much of
                    # this first cumulative report belongs to the parent.
                    state["baseline"] = total
                    issue("fork_missing_history_first_baseline_unattributed")
                    continue
                if any(v > _DIRTY_ROUND_TOKEN_THRESHOLD for v in delta.values()):
                    issue("dirty_advance_excluded")
                    continue
                state["baseline"] = total
                usage = dict(zip(FIELDS, (delta["input_tokens"], delta["output_tokens"],
                                         delta["cached_input_tokens"], 0, delta["reasoning_output_tokens"])))
                sid = state["session_id"]
                identity = ["codex", sid, rec.get("timestamp"), total]
                model = state.get("model", "unknown")
            else:
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                raw = msg.get("usage")
                if not isinstance(raw, dict):
                    continue
                sid = rec.get("sessionId") or requested_sid
                state["cwd"] = rec.get("cwd") or state.get("cwd", "")
                try:
                    usage = dict(zip(FIELDS, (int(raw.get("input_tokens") or 0), int(raw.get("output_tokens") or 0),
                        int(raw.get("cache_read_input_tokens") or 0), int(raw.get("cache_creation_input_tokens") or 0), 0)))
                except (ValueError, TypeError):
                    issue("invalid_counter")
                    continue
                event_id = msg.get("id") or rec.get("uuid")
                if not event_id:
                    issue("missing_event_identity")
                    continue
                identity = ["claude", sid, event_id]
                model = msg.get("model") or "unknown"
            if ts is None:
                issue("missing_event_timestamp")
                continue
            _event(db, sid, provider, ts, model, usage, identity)
    state["indexed_at"] = datetime.now(timezone.utc).isoformat()
    state["source_size"] = path.stat().st_size
    db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (str(path), json.dumps(state)))
    return state


def update_index(path: Path, roots: list[tuple[str, Path]], sessions: tuple[str, ...]) -> dict:
    """Backfill only explicitly selected native files, then consume byte deltas."""
    if not sessions:
        raise ValueError("Event indexing requires at least one --session native ID")
    found = []
    wanted = set(sessions)
    with _connect(path) as db:
        for provider, root in roots:
            pattern = "rollout-*.jsonl" if provider.startswith("codex") else "*.jsonl"
            for candidate in root.rglob(pattern):
                sid = candidate.stem[-36:] if provider.startswith("codex") else candidate.stem
                if sid not in wanted or (not provider.startswith("codex") and "workflows" in candidate.parts):
                    continue
                found.append(_index_file(db, candidate, provider, sid))
    return {"indexed_sources": found, "missing_sessions": sorted(wanted - {r["session_id"] for r in found})}


def query_index(path: Path, sessions: tuple[str, ...] = (), start: str | None = None,
                end: str | None = None) -> dict:
    bounds = [timestamp(start), timestamp(end)]
    if (start and bounds[0] is None) or (end and bounds[1] is None):
        raise ValueError("--start/--end require ISO timestamps with timezone")
    if all(v is not None for v in bounds) and bounds[0] >= bounds[1]:
        raise ValueError("--start must precede --end")
    result = {"available": path.is_file(), "index_path": str(path), "start_inclusive": start,
              "end_exclusive": end, "by_session": [], "sources": [], "missing_sessions": list(sessions),
              "semantics": "usage-report timestamps; Codex total=input+output (cache in input, reasoning in output); Claude total=input+output+cache_read+cache_creation; pre-fork history excluded; exact native event copies deduplicated; elapsed span is not active compute time"}
    if not path.is_file():
        return result
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        sources = [json.loads(r[0]) for r in db.execute("SELECT state FROM sources")]
        result["sources"] = [s for s in sources if not sessions or s["session_id"] in sessions]
        known = {s["session_id"] for s in result["sources"]}
        result["missing_sessions"] = sorted(set(sessions) - known)
        rows = {}
        clauses, args = [], []
        if sessions:
            clauses.append("session_id IN (" + ",".join("?" for _ in sessions) + ")")
            args.extend(sessions)
        for op, value in zip((">=", "<"), bounds):
            if value is not None:
                clauses.append("timestamp " + op + " ?")
                args.append(value)
        sql = "SELECT session_id,provider,timestamp,model,usage FROM events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        for sid, provider, ts, model, raw in db.execute(sql, args):
            row = rows.setdefault(sid, {"session_id": sid, "providers": [], "by_model": {}, "calls": 0,
                                      "first_usage": ts, "last_usage": ts, "total_tokens": 0})
            if provider not in row["providers"]:
                row["providers"].append(provider)
            bucket = row["by_model"].setdefault(model, dict.fromkeys(FIELDS, 0))
            usage = json.loads(raw)
            for key in FIELDS:
                bucket[key] += usage[key]
            row["total_tokens"] += usage["input_tokens"] + usage["output_tokens"]
            if not provider.startswith("codex"):
                row["total_tokens"] += usage["cache_read_tokens"] + usage["cache_creation_tokens"]
            row["calls"] += 1
            row["first_usage"] = min(row["first_usage"], ts)
            row["last_usage"] = max(row["last_usage"], ts)
        for row in rows.values():
            row["elapsed_usage_span_seconds"] = row["last_usage"] - row["first_usage"]
            for field in ("first_usage", "last_usage"):
                row[field] = datetime.fromtimestamp(row[field], timezone.utc).isoformat()
        result["by_session"] = sorted(rows.values(), key=lambda r: r["session_id"])
        return result
    finally:
        db.close()
