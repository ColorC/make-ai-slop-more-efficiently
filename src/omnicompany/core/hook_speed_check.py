# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-10-17T13:00:00+08:00 type=infra status=active
# [OMNI] summary="运行时 hook 冷启动测速（安全 hook、验收门、子线程策略各 50 次），对照 10-12 速度线；状态只写临时目录。"
# [OMNI] why="2026-10-12 用户因 hook 超时下线 6 个 hook，新增或改动的 hook 必须先过速度线。见 docs/projects/agent-longrun-remote/WATCHLIST.md W10。"
"""Cold-start timing of the runtime hooks (50 runs each), against the 2026-10-12 bar.

    python -I hook_speed_check.py [<omnicompany src dir>]     (default: the src tree this file is in)

Bar: the safety hook measured median ~63 ms / max 265 ms when the user kept only it (2026-10-12);
a new or changed hook must stay in that range.  All state goes to a temp dir, never ~/.omni.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

RUNS = 50
PARENT = "01a1400a-b5c8-75c3-b964-ddb574f6e041"
CHILD = "01a14015-19d0-7b32-8d44-2679a9053703"


def _fixtures(src: Path, state: Path) -> dict[str, Path]:
    """A bound checklist (worst Stop path), a 3-hour-old session, and a Codex parent + fresh sub-thread."""
    os.environ["OMNI_ACCEPT_HOME"] = str(state / "acc")
    sys.path.insert(0, str(src))
    from omnicompany.core import acceptance_gate as gate

    doc = state / "acceptance.json"
    gate._atomic_write_json(doc, {"schema": gate.SCHEMA, "title": "speed", "frozen_at": gate._now(), "source": {},
                                  "requests": [{"id": "U1", "ts": "", "text": "做完并截图"}],
                                  "items": [{"id": "A1", "covers": ["U1"], "quote": "做完", "check": "c",
                                             "layer": "L4", "user_path": True, "evidence": []}],
                                  "amendments": [], "verdicts": [], "gate_overrides": []})
    gate.bind(doc, "claude_code", "bound-sess")

    today = dt.date.today()
    day = state / "codex" / "sessions" / f"{today:%Y}" / f"{today:%m}" / f"{today:%d}"
    day.mkdir(parents=True)
    now = dt.datetime.now(dt.timezone.utc)
    parent = day / f"rollout-{today}T08-00-00-{PARENT}.jsonl"
    parent.write_text(json.dumps({"timestamp": now.isoformat(), "type": "session_meta",
                                  "payload": {"id": PARENT}}) + "\n", encoding="utf-8")
    child = {"timestamp": now.isoformat(), "type": "session_meta",
             "payload": {"id": CHILD, "parent_thread_id": PARENT, "source": {"subagent": {"thread_spawn": {
                 "parent_thread_id": PARENT, "agent_path": "/root/architecture", "agent_nickname": "Kuhn"}}}}}
    (day / f"rollout-{today}T09-00-00-{CHILD}.jsonl").write_text(json.dumps(child, separators=(",", ":")) + "\n",
                                                                 encoding="utf-8")
    long_session = state / "long.jsonl"
    long_session.write_text(json.dumps({"type": "user", "message": {"content": "x"},
                                        "timestamp": (now - dt.timedelta(hours=3)).isoformat()}) + "\n",
                            encoding="utf-8")
    return {"parent": parent, "long": long_session}


def _cases(src: Path, py: str, files: dict[str, Path]) -> dict[str, tuple[list[str], dict[str, Any]]]:
    gate = src / "omnicompany" / "core" / "acceptance_gate.py"
    policy = src / "omnicompany" / "core" / "subagent_policy.py"
    safety = src / "omnicompany" / "dashboard" / "ccdaemon" / "hooks" / "safety_pretooluse.py"
    stop = [py, "-I", "-S", str(gate), "hook", "--event", "Stop", "--provider", "claude_code"]
    return {
        "safety_pretooluse (baseline)": ([py, "-I", "-S", str(safety)],
                                         {"tool_name": "Bash", "tool_input": {"command": "git status"}}),
        "safety ssh to work node (deny)": ([py, "-I", "-S", str(safety)],
                                           {"tool_name": "Bash", "tool_input": {"command": "ssh omni-game-win dir"}}),
        "accept Stop unbound, no claim": (stop, {"session_id": "nobody", "last_assistant_message": "我先看日志"}),
        "accept Stop unbound long claim": (stop, {"session_id": "long-sess", "transcript_path": str(files["long"]),
                                                  "last_assistant_message": "完成了"}),
        "accept Stop bound+claim": (stop, {"session_id": "bound-sess", "last_assistant_message": "全部完成了"}),
        "accept SessionStart bound": ([py, "-I", "-S", str(gate), "hook", "--event", "SessionStart"],
                                      {"session_id": "bound-sess", "source": "compact"}),
        "accept PreToolUse update_goal": ([py, "-I", "-S", str(gate), "hook", "--event", "PreToolUse"],
                                          {"session_id": "nobody", "tool_name": "update_goal",
                                           "tool_input": {"status": "complete"}}),
        "subagent_policy followup": ([py, "-I", "-S", str(policy)],
                                     {"session_id": PARENT, "transcript_path": str(files["parent"]),
                                      "tool_name": "followup_task", "tool_input": {"target": "architecture"}}),
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    argv = sys.argv[1:] if argv is None else argv
    src = Path(argv[0]) if argv else Path(__file__).resolve().parents[2]
    state = Path(tempfile.mkdtemp(prefix="hookspeed-"))
    env = dict(os.environ, OMNI_ACCEPT_HOME=str(state / "acc"), OMNI_SUBAGENT_POLICY_HOME=str(state / "pol"))
    files = _fixtures(src, state)
    for name, (cmd, payload) in _cases(src, sys.executable, files).items():
        times = []
        for _ in range(RUNS):
            started = time.perf_counter()
            subprocess.run(cmd, input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True,
                           env=env)
            times.append((time.perf_counter() - started) * 1000)
        print(f"{name:34s} median {statistics.median(times):6.1f} ms  p90 {sorted(times)[int(RUNS * .9)]:6.1f}"
              f"  max {max(times):6.1f}")
    print(f"临时状态目录（可删）：{state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
