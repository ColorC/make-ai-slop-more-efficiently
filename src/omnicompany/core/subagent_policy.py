# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-10-17T13:00:00+08:00 type=infra status=active
# [OMNI] summary="Codex 子线程策略 hook：往运行超 3 小时或压缩超 3 次的旧子线程 followup_task / send_input 续派活时拒绝，要求交接后 spawn 新线程。纯标准库，失败放行。"
# [OMNI] why="2026-10-17 长跑诊断：卫戍协议 20 个子线程跑 10 小时以上、合计 322 小时，常驻线程反复压缩丢约束。NEXT-CHANGES-v1 §3.2：一任务一线程，超 3 小时或 3 次压缩就交接重开。"
"""Subagent reuse policy for Codex (``PreToolUse`` on ``followup_task`` / ``send_input``).

The target of ``followup_task`` is a task name (child ``agent_path`` = ``/root/<name>``);
the target of ``send_input`` is a thread id.  Children are found from the parent's
``transcript_path``: same sessions root, ``session_meta.parent_thread_id`` = this session.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

MAX_AGE_HOURS = 3.0
MAX_COMPACTIONS = 3
STRIKES_BEFORE_ALLOW = 2
SCAN_DAYS = 7
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_DAY_DIR = re.compile(r"^\d{2}$")


def _state_root() -> Path:
    return Path(os.environ.get("OMNI_SUBAGENT_POLICY_HOME") or (Path.home() / ".omni" / "subagent-policy"))


def _head(path: Path, size: int = 8192) -> str:
    with path.open("rb") as stream:
        return stream.read(size).decode("utf-8", errors="replace")


def _meta_field(head: str, name: str) -> str | None:
    match = re.search(r'"%s":"([^"]*)"' % re.escape(name), head)
    return match.group(1) if match else None


def _sessions_root(transcript: Path) -> Path | None:
    # <root>/sessions/YYYY/MM/DD/rollout-*.jsonl
    try:
        root = transcript.parents[3]
    except IndexError:
        return None
    return root if root.name == "sessions" else None


def _day_dirs(root: Path, since: _dt.date, until: _dt.date) -> list[Path]:
    days = []
    day = since
    while day <= until:
        candidate = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        if candidate.is_dir():
            days.append(candidate)
        day += _dt.timedelta(days=1)
    return days


def find_child(transcript: Path, session_id: str, target: str, today: _dt.date | None = None) -> Path | None:
    root = _sessions_root(transcript)
    if root is None:
        return None
    today = today or _dt.date.today()
    if _UUID.match(target):
        for day in _day_dirs(root, today - _dt.timedelta(days=SCAN_DAYS), today)[::-1]:
            hits = list(day.glob(f"rollout-*{target}.jsonl"))
            if hits:
                return hits[0]
        return None
    name = target.rstrip("/").rsplit("/", 1)[-1]
    best: Path | None = None
    for day in _day_dirs(root, today - _dt.timedelta(days=SCAN_DAYS), today):
        for rollout in day.glob("rollout-*.jsonl"):
            if rollout == transcript:
                continue
            try:
                head = _head(rollout)
            except OSError:
                continue
            if f'"parent_thread_id":"{session_id}"' not in head:
                continue
            path = _meta_field(head, "agent_path") or ""
            nickname = _meta_field(head, "agent_nickname") or ""
            if path.rstrip("/").rsplit("/", 1)[-1] == name or nickname == target:
                if best is None or rollout.name > best.name:
                    best = rollout
    return best


def child_stats(rollout: Path, now: float | None = None) -> dict[str, Any]:
    head = _head(rollout)
    started = _meta_field(head, "timestamp")
    try:
        start = _dt.datetime.fromisoformat((started or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        start = rollout.stat().st_ctime
    compactions = 0
    needle = b'"type":"compacted"'
    tail = b""
    with rollout.open("rb") as stream:
        while chunk := stream.read(1 << 22):
            block = tail + chunk  # tail is shorter than the needle, so nothing is counted twice
            compactions += block.count(needle)
            tail = block[-(len(needle) - 1):]
    now = now if now is not None else _dt.datetime.now().timestamp()
    return {"rollout": str(rollout), "age_hours": max(0.0, (now - start) / 3600), "compactions": compactions}


def record_event(kind: str, **fields: Any) -> None:
    """Append one decision to <state root>/events.jsonl (read back by ``omni accept stats``)."""
    try:
        root = _state_root()
        root.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": _dt.datetime.now().astimezone().isoformat(), "kind": kind, **fields},
                          ensure_ascii=False, default=str)
        with (root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except Exception:  # noqa: BLE001 - the ledger must never change the hook's outcome
        pass


def _strike(session_id: str, target: str) -> int:
    path = _state_root() / (re.sub(r"[^A-Za-z0-9._-]+", "_", f"{session_id}--{target}") + ".json")
    try:
        count = int(json.loads(path.read_text(encoding="utf-8")).get("strikes", 0)) + 1
    except (OSError, ValueError):
        count = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"strikes": count, "at": _dt.datetime.now().isoformat()}), encoding="utf-8")
    return count


def decide(payload: dict[str, Any]) -> str | None:
    """Return a denial reason, or ``None`` to allow."""
    tool = str(payload.get("tool_name") or "")
    if "followup_task" not in tool and "send_input" not in tool:
        return None
    tool_input = payload.get("tool_input") or {}
    target = str(tool_input.get("target") or tool_input.get("id") or "").strip()
    session_id = str(payload.get("session_id") or "")
    transcript = Path(str(payload.get("transcript_path") or ""))
    if not target or not session_id or not transcript.is_file():
        return None
    child = find_child(transcript, session_id, target)
    if child is None:
        return None
    stats = child_stats(child)
    # Env overrides exist for live verification (e.g. 0 to force a denial on a fresh child).
    max_age = float(os.environ.get("OMNI_SUBAGENT_POLICY_MAX_AGE_HOURS") or MAX_AGE_HOURS)
    max_compactions = int(os.environ.get("OMNI_SUBAGENT_POLICY_MAX_COMPACTIONS") or MAX_COMPACTIONS)
    if stats["age_hours"] <= max_age and stats["compactions"] <= max_compactions:
        return None
    facts = {"session": session_id, "target": target, "age_hours": round(stats["age_hours"], 2),
             "compactions": stats["compactions"], "rollout": stats["rollout"]}
    if _strike(session_id, target) > STRIKES_BEFORE_ALLOW:
        record_event("allow_after_strikes", **facts)
        return None
    record_event("deny", **facts)
    return (
        f"子线程 {target} 已运行 {stats['age_hours']:.1f} 小时、压缩 {stats['compactions']} 次，"
        f"超过一线程一任务的上限（{max_age:g} 小时或 {max_compactions} 次压缩）。"
        "不要再往它续派活：让它把结论、证据绝对路径、未完项写成交接文件后关闭它，"
        "再用 spawn_agent 新开线程、只带交接摘要和写集。"
        f"（子线程转录：{stats['rollout']}；依据 omnicompany longrun-health-2026-10-17/NEXT-CHANGES-v1.md §3.2。"
        f"同一目标连续被拒 {STRIKES_BEFORE_ALLOW} 次后放行，仅限确需原线程上下文的一句话追问。）"
    )


def _debug_log(payload: dict[str, Any], outcome: Any) -> None:
    # Same switch as acceptance_gate.debug_log: a fixed file, so it works without the caller's env.
    try:
        root = Path.home() / ".omni"
        if not (root / "hook-debug.on").exists():
            return
        record = {"at": _dt.datetime.now().astimezone().isoformat(), "script": "subagent_policy",
                  "payload_keys": sorted(payload), "session_id": payload.get("session_id"),
                  "tool_name": payload.get("tool_name"), "tool_input": payload.get("tool_input"),
                  "transcript_path": payload.get("transcript_path"),
                  # Named variables only: prefix matching once captured a value that embedded an API key.
                  "env": {k: os.environ[k][:200] for k in ("OMNI_SUBAGENT_POLICY_HOME", "OMNI_PROBE",
                                                           "OMNI_SUBAGENT_POLICY_MAX_AGE_HOURS",
                                                           "OMNI_SUBAGENT_POLICY_MAX_COMPACTIONS", "CODEX_HOME",
                                                           "CODEX_THREAD_ID") if k in os.environ},
                  "outcome": outcome}
        with (root / "hook-debug.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - diagnostics must never change the hook's outcome
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="subagent_policy")
    parser.add_argument("--provider", default="codex")
    parser.parse_args(argv)
    payload: dict[str, Any] = {}
    try:
        # Bytes, then UTF-8: a text-mode stdin under -I uses the console code page and garbles JSON.
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        reason = decide(payload)
    except Exception as exc:  # noqa: BLE001 - policy must never wedge the agent
        sys.stderr.write(f"subagent policy hook error (ignored): {exc}\n")
        _debug_log(payload if isinstance(payload, dict) else {}, {"error": str(exc)})
        return 0
    _debug_log(payload, {"deny": reason})
    if not reason:
        return 0
    sys.stdout.write(json.dumps({
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                               "permissionDecisionReason": reason},
    }, ensure_ascii=False))
    sys.stderr.write(reason)
    return 2


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    raise SystemExit(main())
