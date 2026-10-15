from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from omnicompany.runtime.material_blackboard import (
    BlackboardActivation,
    BlackboardCoordinator,
    BlackboardMaterial,
    BlackboardSubscription,
    MaterialChange,
    MaterialOutput,
)


class _Authority:
    """Test authority with an append-only change cursor; it is not an EventBus."""

    def __init__(self, initial: Sequence[BlackboardMaterial]) -> None:
        self.materials = list(initial)
        self._keys: dict[str, BlackboardMaterial] = {}

    async def changes(self, *, after_cursor: str | None, limit: int) -> list[MaterialChange]:
        offset = int(after_cursor or "0")
        return [
            MaterialChange(str(index + 1), material)
            for index, material in enumerate(self.materials[offset : offset + limit], offset)
        ]

    async def publish(
        self,
        output: MaterialOutput,
        *,
        causation_refs: tuple[str, ...],
        idempotency_key: str,
        hop_depth: int,
    ) -> BlackboardMaterial:
        if idempotency_key in self._keys:
            return self._keys[idempotency_key]
        material = BlackboardMaterial(
            material_ref=f"material:generated:{len(self.materials)}",
            format_id=output.format_id,
            revision="v1",
            content={**output.content, "caused_by": causation_refs},
            hop_depth=hop_depth,
        )
        self._keys[idempotency_key] = material
        self.materials.append(material)
        return material


class _Runtime:
    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers
        self.activations: list[BlackboardActivation] = []

    async def run(self, activation: BlackboardActivation) -> Sequence[MaterialOutput]:
        self.activations.append(activation)
        return self.handlers[activation.subscription_id](activation)


def _material(ref: str, format_id: str, **content: object) -> BlackboardMaterial:
    return BlackboardMaterial(ref, format_id, "v1", content)


@pytest.mark.asyncio
async def test_blackboard_fan_in_and_unconsumed_material_without_eventbus() -> None:
    authority = _Authority(
        [
            _material("material:words", "synth.words", count=3),
            _material("material:orphan", "synth.unconsumed", note="legal"),
            _material("material:chars", "synth.chars", count=20),
        ]
    )
    runtime = _Runtime(
        {
            "merge": lambda activation: [
                MaterialOutput(
                    "synth.report",
                    {
                        "words": activation.inputs["synth.words"].content["count"],
                        "chars": activation.inputs["synth.chars"].content["count"],
                    },
                )
            ]
        }
    )
    coordinator = BlackboardCoordinator(
        authority,
        runtime,
        [
            BlackboardSubscription(
                "merge",
                ("synth.words", "synth.chars"),
                output_formats=("synth.report",),
            )
        ],
    )

    report = await coordinator.run()

    assert report.activations == 1
    assert report.unconsumed_refs == ("material:orphan", "material:generated:3")
    assert report.published_refs == ("material:generated:3",)
    assert authority.materials[-1].content["words"] == 3
    assert authority.materials[-1].content["chars"] == 20


@pytest.mark.asyncio
async def test_blackboard_cycle_is_bounded_by_material_hop_depth() -> None:
    authority = _Authority([_material("material:seed", "loop.a", value=0)])
    runtime = _Runtime(
        {
            "a-to-b": lambda activation: [
                MaterialOutput("loop.b", {"value": activation.inputs["loop.a"].content["value"] + 1})
            ],
            "b-to-a": lambda activation: [
                MaterialOutput("loop.a", {"value": activation.inputs["loop.b"].content["value"] + 1})
            ],
        }
    )
    coordinator = BlackboardCoordinator(
        authority,
        runtime,
        [
            BlackboardSubscription("a-to-b", ("loop.a",), "any", ("loop.b",)),
            BlackboardSubscription("b-to-a", ("loop.b",), "any", ("loop.a",)),
        ],
        max_hops=3,
    )

    report = await coordinator.run(max_activations=20)

    assert report.activations == 4
    assert len(report.published_refs) == 3
    assert report.cycle_limited_refs
    assert len(authority.materials) == 4


@pytest.mark.asyncio
async def test_blackboard_rejects_undeclared_runtime_output_before_material_write() -> None:
    authority = _Authority([_material("material:seed", "input", value=1)])
    runtime = _Runtime({"worker": lambda _activation: [MaterialOutput("wrong", {})]})
    coordinator = BlackboardCoordinator(
        authority,
        runtime,
        [BlackboardSubscription("worker", ("input",), "any", ("expected",))],
    )

    with pytest.raises(ValueError, match="undeclared format"):
        await coordinator.run()

    assert len(authority.materials) == 1
