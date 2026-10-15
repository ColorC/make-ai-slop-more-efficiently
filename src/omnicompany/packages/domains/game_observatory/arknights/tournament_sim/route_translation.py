# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Turning shipped route rows into runtime routes.

Moved verbatim out of ``stage_compiler.py`` so that file fits the 2000-line
budget; the compiler re-exports every name, so importers are unchanged.

The row-axis flip lives here too: ``mapData.map`` lists rows from the top down
while route and checkpoint positions count from the bottom, and every position
crossing that boundary goes through ``flip_row``.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .model_primitives import GridPoint, MovementLayer
from .models import RouteDefinition, SourceRouteCheckpoint
from .stage_clock import to_ticks

DEFAULT_TPS = 30


def flip_row(row: int, height: int) -> int:
    """Convert a bottom-up source row to the top-down map array row.

    ``mapData.map`` lists rows from the top of the screen down, while route and
    checkpoint positions count rows from the bottom.  Every position that
    crosses that boundary must go through here.
    """

    return height - 1 - int(row)


def _point(raw: Any, height: int) -> tuple[int, int] | None:
    if not isinstance(raw, dict):
        return None
    col, row = raw.get("col"), raw.get("row")
    if not isinstance(col, int) or not isinstance(row, int):
        return None
    return int(col), flip_row(row, height)


def _shortest(
    start: tuple[int, int],
    end: tuple[int, int],
    walkable: set[tuple[int, int]],
) -> tuple[list[tuple[int, int]], bool]:
    """Breadth-first walk from ``start`` to ``end`` over ``walkable``.

    Returns the path and whether it stayed inside ``walkable``.  When no
    walkable path exists the fallback is an L-shaped Manhattan walk and the
    flag is False, so the caller can mark the route non-runtime instead of
    pretending the geometry is source-exact.
    """

    if not walkable:
        col, row = start
        path = [start]
        while col != end[0]:
            col += 1 if end[0] > col else -1
            path.append((col, row))
        while row != end[1]:
            row += 1 if end[1] > row else -1
            path.append((col, row))
        return path, False

    allowed = walkable | {start, end}
    previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == end:
            break
        col, row = current
        neighbours = [(col - 1, row), (col + 1, row), (col, row - 1), (col, row + 1)]
        neighbours.sort(key=lambda spot: (abs(spot[0] - end[0]) + abs(spot[1] - end[1]), spot[1], spot[0]))
        for candidate in neighbours:
            if candidate in allowed and candidate not in previous:
                previous[candidate] = current
                queue.append(candidate)
    if end not in previous:
        return _shortest(start, end, set())
    path: list[tuple[int, int]] = []
    cursor: tuple[int, int] | None = end
    while cursor is not None:
        path.append(cursor)
        cursor = previous[cursor]
    path.reverse()
    return path, True


def _compile_route(
    raw: dict[str, Any],
    route_index: int,
    width: int,
    height: int,
    walkable: set[tuple[int, int]],
) -> tuple[RouteDefinition, bool, int]:
    start = _point(raw.get("startPosition"), height)
    end = _point(raw.get("endPosition"), height)
    if start is None or end is None:
        raise ValueError(f"route {route_index} lacks endpoints")
    # Some routes enter from off-screen.  The grid cannot hold a negative
    # coordinate, so the entry point is clamped to the nearest edge cell and
    # the route is reported as an approximation rather than dropped.
    clamped = False
    def _clamp(spot: tuple[int, int]) -> tuple[int, int]:
        nonlocal clamped
        col = min(max(spot[0], 0), width - 1)
        row = min(max(spot[1], 0), height - 1)
        if (col, row) != spot:
            clamped = True
        return col, row

    start, end = _clamp(start), _clamp(end)

    motion = int(raw.get("motionMode") or 0)
    flying = motion == 1
    off_grid_sentinels = 0

    anchors: list[tuple[int, int]] = [start]
    waits: list[int] = [0]
    checkpoints: list[SourceRouteCheckpoint] = []
    teleports = False
    for order, checkpoint in enumerate(raw.get("checkpoints") or []):
        kind = int(checkpoint.get("type", -1))
        position = _point(checkpoint.get("position"), height)
        # Wait (type 1) and disappear (type 5) checkpoints act at the route's
        # current point, and shipped stages put a sentinel in their position
        # field -- negative columns like (-1, 0) or (-6, 0).  Those are not
        # coordinates, so they are dropped rather than clamped onto the grid.
        if position is not None and not (0 <= position[0] < width and 0 <= position[1] < height):
            position = None
            off_grid_sentinels += 1
        offset = checkpoint.get("reachOffset") or {}
        checkpoints.append(
            SourceRouteCheckpoint(
                source_index=order,
                checkpoint_type=kind,
                time_ticks=to_ticks(checkpoint.get("time"), DEFAULT_TPS),
                position=None if position is None else GridPoint(col=position[0], row=position[1]),
                reach_offset_milli=(
                    round(float(offset.get("x") or 0) * 1000),
                    round(float(offset.get("y") or 0) * 1000),
                ),
                randomize_reach_offset=bool(checkpoint.get("randomizeReachOffset")),
                reach_distance_milli=max(0, round(float(checkpoint.get("reachDistance") or 0) * 1000)),
            )
        )
        teleports = teleports or kind in {5, 6}
        if kind in {0, 6} and position is not None and position != anchors[-1]:
            anchors.append(position)
            waits.append(0)
        elif kind == 1:
            waits[-1] += to_ticks(checkpoint.get("time"), DEFAULT_TPS)
    if end != anchors[-1]:
        anchors.append(end)
        waits.append(0)

    # Ground routes are walked over passable tiles; flying and the third
    # motion mode are not constrained by ground passability.
    lattice = walkable if motion == 0 else set()
    cells: list[tuple[int, int]] = [anchors[0]]
    source_exact = True
    for before, after in zip(anchors, anchors[1:]):
        leg, exact = _shortest(before, after, lattice)
        source_exact = source_exact and exact
        for cell in leg[1:]:
            if cell != cells[-1]:
                cells.append(cell)

    wait_ticks = [0] * len(cells)
    cursor = 0
    for anchor, wait in zip(anchors, waits):
        while cursor < len(cells) and cells[cursor] != anchor:
            cursor += 1
        if cursor >= len(cells):
            raise ValueError(f"route {route_index} anchor missing from its walk")
        wait_ticks[cursor] += wait

    # A ground route whose walk left the walkable set is not runtime-valid
    # geometry; flag it rather than let the bundle validator reject the stage.
    non_runtime = motion == 0 and not source_exact
    definition = RouteDefinition(
        route_id=f"route-{route_index:03d}",
        waypoints=tuple(GridPoint(col=col, row=row) for col, row in cells),
        waypoint_wait_ticks=tuple(wait_ticks),
        movement_layer=MovementLayer.FLYING if flying else MovementLayer.GROUND,
        source_route_index=route_index,
        source_checkpoints=tuple(checkpoints),
        compatibility_path_approximates_teleports=teleports,
        source_non_runtime_route=non_runtime,
    )
    return definition, non_runtime, off_grid_sentinels
