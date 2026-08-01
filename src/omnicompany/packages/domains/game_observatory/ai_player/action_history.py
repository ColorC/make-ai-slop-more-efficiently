"""Pre-action guard against repeating known failed state/action pairs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..models import NormalizedAction, SourcePixelRect
from .contracts import TransitionEdgeV1
from .store import AIPlayerStore


class ActionHistoryDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    current_state_id: str | None = Field(default=None, min_length=1)
    matched_transition_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


def _point_distance_matches(left: int | None, right: int | None, tolerance: int) -> bool:
    return left is not None and right is not None and abs(left - right) <= tolerance


def _intersection_over_union(left: SourcePixelRect, right: SourcePixelRect) -> float:
    x1 = max(left.x, right.x)
    y1 = max(left.y, right.y)
    x2 = min(left.x + left.width, right.x + right.width)
    y2 = min(left.y + left.height, right.y + right.height)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if intersection == 0:
        return 0.0
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union


def _same_action_target(
    edge: TransitionEdgeV1,
    action: NormalizedAction,
    target_bounds: SourcePixelRect | None,
) -> bool:
    previous = edge.action
    if previous.type != action.type:
        return False
    if target_bounds is not None and edge.target_bounds is not None:
        if _intersection_over_union(edge.target_bounds, target_bounds) >= 0.50:
            return True
    if action.type == "tap":
        return _point_distance_matches(previous.x, action.x, 8) and _point_distance_matches(
            previous.y,
            action.y,
            8,
        )
    if action.type == "swipe":
        return all(
            (
                _point_distance_matches(previous.x, action.x, 12),
                _point_distance_matches(previous.y, action.y, 12),
                _point_distance_matches(previous.x2, action.x2, 12),
                _point_distance_matches(previous.y2, action.y2, 12),
            )
        )
    return previous == action


class ActionHistoryGuard:
    """Reject a failed/no-change target in the same semantic state before device access."""

    BLOCKING_OUTCOMES = frozenset({"failed", "forbidden", "verified_no_change"})

    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    def evaluate(
        self,
        *,
        environment_id: str,
        session_id: str | None = None,
        current_state_id: str | None = None,
        action: NormalizedAction,
        target_bounds: SourcePixelRect | None = None,
    ) -> ActionHistoryDecisionV1:
        if current_state_id is None and session_id is not None:
            capsule = self.store.get_latest_session_capsule(
                environment_id,
                session_id=session_id,
            )
            current_state_id = capsule.last_confirmed_state_id if capsule is not None else None
        if current_state_id is None:
            return ActionHistoryDecisionV1(
                allowed=True,
                reason="当前胶囊没有已确认状态，动作历史门只记录该缺口，不臆测状态。",
            )
        matches = [
            edge
            for edge in self.store.list_transition_edges(environment_id)
            if edge.from_state_id == current_state_id
            and edge.outcome in self.BLOCKING_OUTCOMES
            and _same_action_target(edge, action, target_bounds)
        ]
        if matches:
            return ActionHistoryDecisionV1(
                allowed=False,
                current_state_id=current_state_id,
                matched_transition_ids=[edge.id for edge in matches],
                reason=(
                    "同一语义状态下的同一目标已有失败、禁止或无变化记录；"
                    "缺少新证据时不得再次访问设备。"
                ),
            )
        return ActionHistoryDecisionV1(
            allowed=True,
            current_state_id=current_state_id,
            reason="当前状态与目标组合没有阻断性历史记录。",
        )
