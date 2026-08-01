"""Environment-isolated semantic state graph and verified route search."""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .contracts import SemanticStateV1, TransitionEdgeV1
from .store import AIPlayerStore


VerifiedOutcome = Literal[
    "verified_transition",
    "verified_state_change",
    "verified_progress",
]


class StateRouteV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.state-route.v1"] = Field(
        default="game-observatory.ai-player.state-route.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    start_state_id: str = Field(min_length=1)
    goal_state_id: str = Field(min_length=1)
    state_ids: tuple[str, ...] = Field(min_length=1)
    edge_ids: tuple[str, ...]
    total_cost: float = Field(ge=0)
    verified_only: Literal[True] = True

    @computed_field(return_type=int)
    @property
    def action_count(self) -> int:
        return len(self.edge_ids)


class SemanticStateGraph:
    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    def states(
        self,
        environment_id: str,
        *,
        include_candidates: bool = True,
    ) -> list[SemanticStateV1]:
        statuses = ("accepted", "candidate") if include_candidates else ("accepted",)
        return self.store.list_semantic_states(environment_id, statuses=statuses)

    def edges(
        self,
        environment_id: str,
        *,
        verified_only: bool = False,
    ) -> list[TransitionEdgeV1]:
        outcomes: Sequence[str] | None = (
            ("verified_transition", "verified_state_change", "verified_progress")
            if verified_only
            else None
        )
        return self.store.list_transition_edges(environment_id, outcomes=outcomes)

    def put_edge(self, edge: TransitionEdgeV1) -> TransitionEdgeV1:
        verified = edge.outcome.startswith("verified_")
        if verified:
            if edge.to_state_id is None:
                raise ValueError("verified graph edge requires a destination")
            destination = self.store.get_semantic_state(edge.environment_id, edge.to_state_id)
            if destination is None or destination.status != "accepted":
                raise ValueError("verified graph edge destination must be an accepted state")
        source = self.store.get_semantic_state(edge.environment_id, edge.from_state_id)
        if source is None or source.status in {"superseded", "invalidated"}:
            raise ValueError("graph edge source is not an active semantic state")
        if (
            verified and source.status != "accepted"
        ):
            raise ValueError("verified graph edge source must be an accepted state")
        return self.store.put_transition_edge(edge)

    def shortest_verified_route(
        self,
        environment_id: str,
        start_state_id: str,
        goal_state_id: str,
        *,
        max_actions: int | None = None,
        forbidden_edge_ids: Sequence[str] = (),
    ) -> StateRouteV1:
        if max_actions is not None and max_actions < 0:
            raise ValueError("max_actions must be non-negative")
        active_states = {
            state.id for state in self.states(environment_id, include_candidates=False)
        }
        if start_state_id not in active_states:
            raise KeyError(f"unknown active start state: {start_state_id}")
        if goal_state_id not in active_states:
            raise KeyError(f"unknown active goal state: {goal_state_id}")
        if start_state_id == goal_state_id:
            return StateRouteV1(
                environment_id=environment_id,
                start_state_id=start_state_id,
                goal_state_id=goal_state_id,
                state_ids=(start_state_id,),
                edge_ids=(),
                total_cost=0,
            )
        forbidden = set(forbidden_edge_ids)
        adjacency: dict[str, list[TransitionEdgeV1]] = defaultdict(list)
        for edge in self.edges(environment_id, verified_only=True):
            if (
                edge.id not in forbidden
                and edge.to_state_id is not None
                and edge.from_state_id in active_states
                and edge.to_state_id in active_states
            ):
                adjacency[edge.from_state_id].append(edge)
        for outgoing in adjacency.values():
            outgoing.sort(key=lambda edge: edge.id)

        queue: list[tuple[float, int, str, tuple[str, ...], tuple[str, ...]]] = [
            (0, 0, start_state_id, (start_state_id,), ())
        ]
        best: dict[str, tuple[float, int]] = {start_state_id: (0, 0)}
        while queue:
            cost, action_count, state_id, state_path, edge_path = heapq.heappop(queue)
            if state_id == goal_state_id:
                return StateRouteV1(
                    environment_id=environment_id,
                    start_state_id=start_state_id,
                    goal_state_id=goal_state_id,
                    state_ids=state_path,
                    edge_ids=edge_path,
                    total_cost=cost,
                )
            if max_actions is not None and action_count >= max_actions:
                continue
            for edge in adjacency.get(state_id, []):
                destination = edge.to_state_id
                if destination is None:
                    continue
                next_count = action_count + 1
                next_cost = cost + 1
                previous = best.get(destination)
                if previous is not None and previous <= (next_cost, next_count):
                    continue
                best[destination] = (next_cost, next_count)
                heapq.heappush(
                    queue,
                    (
                        next_cost,
                        next_count,
                        destination,
                        (*state_path, destination),
                        (*edge_path, edge.id),
                    ),
                )
        raise LookupError(
            f"no verified route in {environment_id}: {start_state_id} -> {goal_state_id}"
        )

    def reachable_state_ids(
        self,
        environment_id: str,
        start_state_id: str,
        *,
        max_actions: int,
    ) -> tuple[str, ...]:
        if max_actions < 0:
            raise ValueError("max_actions must be non-negative")
        active = {state.id for state in self.states(environment_id, include_candidates=False)}
        if start_state_id not in active:
            raise KeyError(f"unknown active start state: {start_state_id}")
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self.edges(environment_id, verified_only=True):
            if edge.to_state_id in active:
                adjacency[edge.from_state_id].add(str(edge.to_state_id))
        seen = {start_state_id}
        frontier = {start_state_id}
        for _depth in range(max_actions):
            next_frontier = {
                destination
                for state_id in frontier
                for destination in adjacency.get(state_id, set())
                if destination not in seen
            }
            if not next_frontier:
                break
            seen.update(next_frontier)
            frontier = next_frontier
        return tuple(sorted(seen))
