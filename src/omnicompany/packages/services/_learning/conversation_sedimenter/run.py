# [OMNI] origin=claude-code domain=services/_learning ts=2026-06-23T00:00:00Z type=runner
# [OMNI] material_id="material:services._learning.conversation_sedimenter.run.py"
"""conversation-operation-sedimenter 运行入口（实验态裸跑 + 按名可跑的 build_team）。

实验态: python -m omnicompany.packages.services._learning.conversation_sedimenter.run "<transcript.jsonl>"
按名:    dispatch("conversation-operation-sedimenter", {"transcript_path": "...", "source": "claude-code"})
"""
from __future__ import annotations

import asyncio
from typing import Any

from .workers import ALL_WORKERS


def build_team() -> list:
    return [W() for W in ALL_WORKERS]


async def run_experiment(transcript_path: str | None = None, session_id: str | None = None,
                         source: str = "claude-code") -> dict[str, Any]:
    from omnicompany.packages.services._core.omnicompany.material_dispatcher import (
        MaterialDispatcher,
    )

    payload: dict[str, Any] = {"source": source}
    if transcript_path:
        payload["transcript_path"] = transcript_path
    if session_id:
        payload["session_id"] = session_id
    dispatcher = MaterialDispatcher(workers=build_team())
    events = await dispatcher.run_job("convop.request", payload)
    by_type: dict[str, list[dict]] = {}
    for e in events:
        by_type.setdefault(e.event_type, []).append(e.payload)
    trace = (by_type.get("convop.trace") or [{}])[0]
    return {
        "event_types": [e.event_type for e in events],
        "trace_summary": {"n_events": trace.get("n_events"), "n_lines_scanned": trace.get("n_lines_scanned"),
                          "tool_histogram": trace.get("tool_histogram")},
        "operations": (by_type.get("convop.operations") or [{}])[0],
        "team_skeleton": (by_type.get("convop.team_skeleton") or [{}])[0],
    }


def _main() -> None:
    import json
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if len(sys.argv) < 2:
        print("用法: run.py <transcript.jsonl 路径>")
        raise SystemExit(2)
    out = asyncio.run(run_experiment(transcript_path=sys.argv[1]))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
