# [OMNI] origin=claude-code domain=services/_focus ts=2026-06-23T00:00:00Z type=runner
# [OMNI] material_id="material:services._focus.plan_progress_recorder.run.py"
"""plan-progress-recorder 运行入口（实验态裸跑 + 按名可跑的 build_team）。

实验态:  python -m omnicompany.packages.services._focus.plan_progress_recorder.run "<plan_id>" [task_id]
按名:     core.dispatch.dispatch("plan-progress-recorder", {"plan_id": "...", "task_id": "..."})
"""
from __future__ import annotations

import asyncio
from typing import Any

from .workers import ALL_WORKERS


def build_team() -> list:
    """engine=event 的 build_team: 返回 worker 实例清单。"""
    return [W() for W in ALL_WORKERS]


async def run_experiment(plan_id: str, task_id: str | None = None) -> dict[str, Any]:
    """实验态裸跑一次（MaterialDispatcher），返回 source/internal/sink 摘要。"""
    from omnicompany.packages.services._core.omnicompany.material_dispatcher import (
        MaterialDispatcher,
    )

    dispatcher = MaterialDispatcher(workers=build_team())
    payload: dict[str, Any] = {"plan_id": plan_id}
    if task_id:
        payload["task_id"] = task_id
    events = await dispatcher.run_job("planprog.request", payload)
    by_type: dict[str, list[dict]] = {}
    for e in events:
        by_type.setdefault(e.event_type, []).append(e.payload)
    return {
        "event_types": [e.event_type for e in events],
        "assessment": (by_type.get("planprog.assessment") or [{}])[0],
        "recorded": (by_type.get("planprog.recorded") or [{}])[0],
    }


def _main() -> None:
    import json
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK
    except Exception:  # noqa: BLE001
        pass
    if len(sys.argv) < 2:
        print("用法: run.py <plan_id> [task_id]")
        raise SystemExit(2)
    plan_id = sys.argv[1]
    task_id = sys.argv[2] if len(sys.argv) > 2 else None
    out = asyncio.run(run_experiment(plan_id, task_id))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
