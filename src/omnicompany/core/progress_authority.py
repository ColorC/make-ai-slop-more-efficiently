# [OMNI] origin=codex domain=core/progress-authority ts=2026-07-10T00:00:00Z type=infra
# [OMNI] summary="WhatNow 计划进度权威只读投影: 统一加载 board 并按 plan_id/goal_id 建索引"
# [OMNI] why="计划文档移除本地 status 后, dashboard 与治理巡检必须共用同一权威读取入口"
# [OMNI] tags=progress,ssot,whatnow,authority
# [OMNI] material_id="material:core.progress_authority.py"
"""Read-only access to the WhatNow plan-progress authority."""

from __future__ import annotations

import json
import os
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_PROGRESS_SERVICE_URL = "http://127.0.0.1:8230"

# A small number of north-star charters are represented directly as goals rather
# than plan-level tasks.  Keeping this mapping here prevents sync, dashboard and
# governance from inventing three different interpretations.
PLAN_GOAL_AUTHORITIES = {
    "[2026-06-25]SEMANTIC-OS": "semantic-os",
}


def progress_service_url() -> str:
    return (
        os.environ.get("PROGRESS_SERVICE_URL")
        or os.environ.get("WHATNOW_URL")
        or DEFAULT_PROGRESS_SERVICE_URL
    ).rstrip("/")


def iter_progress_tasks(board: dict[str, Any]) -> Iterable[dict[str, Any]]:
    direct = board.get("tasks")
    if isinstance(direct, list):
        yield from (task for task in direct if isinstance(task, dict))
        return
    for cluster in board.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        for goal in cluster.get("goals") or []:
            if not isinstance(goal, dict):
                continue
            yield from (task for task in (goal.get("tasks") or []) if isinstance(task, dict))


def iter_progress_goals(board: dict[str, Any]) -> Iterable[dict[str, Any]]:
    direct = board.get("goals")
    if isinstance(direct, list):
        yield from (goal for goal in direct if isinstance(goal, dict))
        return
    for cluster in board.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        yield from (goal for goal in (cluster.get("goals") or []) if isinstance(goal, dict))


def task_groups_by_plan(board: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in iter_progress_tasks(board):
        plan_id = str(task.get("plan_id") or "").strip()
        if plan_id:
            grouped[plan_id].append(task)
    return dict(grouped)


def goals_by_id(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(goal.get("id")): goal
        for goal in iter_progress_goals(board)
        if goal.get("id")
    }


def load_progress_board(
    root: Path,
    *,
    timeout: float = 3.0,
) -> tuple[dict[str, Any] | None, str]:
    """Load the authority, preferring the live service and then its persisted store."""
    # Tests that point OMNI_WORKSPACE_ROOT at a fixture must never leak into the
    # developer's live WhatNow.  An explicit opt-in remains available for true
    # integration tests.
    allow_live = not os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("OMNI_TEST_ALLOW_LIVE_PROGRESS") == "1"
    if allow_live:
        url = progress_service_url() + "/api/board?archived=1"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace")), "http"
        except Exception:  # noqa: BLE001 - the persisted service store is the intended fallback
            pass
    path = Path(root) / "data" / "services" / "whatnow" / "whatnow.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")), "local-fallback"
    except (OSError, json.JSONDecodeError):
        return None, "unavailable"


def load_plan_authority_index(
    root: Path,
    *,
    board: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], str]:
    if board is None:
        board, source = load_progress_board(Path(root))
    else:
        source = "provided"
    if board is None:
        return {}, source
    grouped = task_groups_by_plan(board)
    # Duplicates are intentionally not guessed; the SSOT audit reports them.
    return {plan_id: tasks[0] for plan_id, tasks in grouped.items() if len(tasks) == 1}, source


__all__ = [
    "PLAN_GOAL_AUTHORITIES",
    "goals_by_id",
    "iter_progress_goals",
    "iter_progress_tasks",
    "load_plan_authority_index",
    "load_progress_board",
    "progress_service_url",
    "task_groups_by_plan",
]
