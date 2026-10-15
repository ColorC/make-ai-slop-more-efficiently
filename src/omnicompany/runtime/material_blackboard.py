"""Material-native blackboard coordination without EventBus-as-stock.

This module owns activation state only.  Material bodies and changes belong to
the injected authority, while Agent execution belongs to the injected runtime
port.  The coordinator therefore remains independent of storage and harness.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class BlackboardMaterial:
    material_ref: str
    format_id: str
    revision: str
    content: Mapping[str, Any]
    hop_depth: int = 0


@dataclass(frozen=True, slots=True)
class MaterialChange:
    cursor: str
    material: BlackboardMaterial


@dataclass(frozen=True, slots=True)
class MaterialOutput:
    format_id: str
    content: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BlackboardSubscription:
    subscription_id: str
    input_formats: tuple[str, ...]
    input_mode: str = "all"
    output_formats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.subscription_id or not self.input_formats:
            raise ValueError("blackboard subscription identity and inputs are required")
        if self.input_mode not in {"all", "any"}:
            raise ValueError("blackboard input_mode must be 'all' or 'any'")
        if len(set(self.input_formats)) != len(self.input_formats):
            raise ValueError("blackboard input formats must be unique")


@dataclass(frozen=True, slots=True)
class BlackboardActivation:
    subscription_id: str
    inputs: Mapping[str, BlackboardMaterial]


@dataclass(frozen=True, slots=True)
class BlackboardRunReport:
    cursor: str | None
    activations: int
    published_refs: tuple[str, ...]
    unconsumed_refs: tuple[str, ...]
    cycle_limited_refs: tuple[str, ...]


class MaterialBlackboardAuthority(Protocol):
    """Direct Material query/subscription/mutation boundary."""

    async def changes(
        self,
        *,
        after_cursor: str | None,
        limit: int,
    ) -> Sequence[MaterialChange]: ...

    async def publish(
        self,
        output: MaterialOutput,
        *,
        causation_refs: tuple[str, ...],
        idempotency_key: str,
        hop_depth: int,
    ) -> BlackboardMaterial: ...


class BlackboardAgentRuntime(Protocol):
    """Vendor-neutral execution port used for one subscription activation."""

    async def run(self, activation: BlackboardActivation) -> Sequence[MaterialOutput]: ...


def _activation_key(
    subscription: BlackboardSubscription,
    inputs: Mapping[str, BlackboardMaterial],
) -> str:
    stable = {
        "subscription_id": subscription.subscription_id,
        "inputs": [
            {
                "format_id": format_id,
                "material_ref": material.material_ref,
                "revision": material.revision,
            }
            for format_id, material in sorted(inputs.items())
        ],
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class BlackboardCoordinator:
    """Activate Material subscriptions without treating an event log as inventory."""

    def __init__(
        self,
        authority: MaterialBlackboardAuthority,
        runtime: BlackboardAgentRuntime,
        subscriptions: Sequence[BlackboardSubscription],
        *,
        max_hops: int = 16,
        batch_size: int = 100,
    ) -> None:
        if max_hops < 0 or batch_size < 1:
            raise ValueError("blackboard limits are invalid")
        ids = [item.subscription_id for item in subscriptions]
        if len(set(ids)) != len(ids):
            raise ValueError("blackboard subscription ids must be unique")
        self._authority = authority
        self._runtime = runtime
        self._subscriptions = tuple(subscriptions)
        self._max_hops = max_hops
        self._batch_size = batch_size

    async def run(
        self,
        *,
        after_cursor: str | None = None,
        max_activations: int = 100,
    ) -> BlackboardRunReport:
        if max_activations < 1:
            raise ValueError("max_activations must be positive")
        cursor = after_cursor
        latest: dict[tuple[str, str], BlackboardMaterial] = {}
        activated: set[str] = set()
        published: list[str] = []
        unconsumed: list[str] = []
        cycle_limited: list[str] = []
        activation_count = 0

        while activation_count < max_activations:
            changes = tuple(
                await self._authority.changes(
                    after_cursor=cursor,
                    limit=self._batch_size,
                )
            )
            if not changes:
                break
            for change in changes:
                cursor = change.cursor
                material = change.material
                matching = [
                    item
                    for item in self._subscriptions
                    if material.format_id in item.input_formats
                ]
                if not matching:
                    unconsumed.append(material.material_ref)
                    continue
                if material.hop_depth > self._max_hops:
                    cycle_limited.append(material.material_ref)
                    continue
                for subscription in matching:
                    latest[(subscription.subscription_id, material.format_id)] = material
                    inputs = self._activation_inputs(subscription, latest, material)
                    if inputs is None:
                        continue
                    key = _activation_key(subscription, inputs)
                    if key in activated:
                        continue
                    if activation_count >= max_activations:
                        break
                    activated.add(key)
                    activation_count += 1
                    outputs = tuple(
                        await self._runtime.run(
                            BlackboardActivation(subscription.subscription_id, inputs)
                        )
                    )
                    causation_refs = tuple(
                        item.material_ref for _, item in sorted(inputs.items())
                    )
                    next_hop = max(item.hop_depth for item in inputs.values()) + 1
                    if next_hop > self._max_hops:
                        cycle_limited.extend(causation_refs)
                        continue
                    for index, output in enumerate(outputs):
                        if (
                            subscription.output_formats
                            and output.format_id not in subscription.output_formats
                        ):
                            raise ValueError(
                                f"subscription {subscription.subscription_id} emitted "
                                f"undeclared format {output.format_id}"
                            )
                        created = await self._authority.publish(
                            output,
                            causation_refs=causation_refs,
                            idempotency_key=f"blackboard:{key}:{index}",
                            hop_depth=next_hop,
                        )
                        published.append(created.material_ref)
                if activation_count >= max_activations:
                    break

        return BlackboardRunReport(
            cursor=cursor,
            activations=activation_count,
            published_refs=tuple(published),
            unconsumed_refs=tuple(unconsumed),
            cycle_limited_refs=tuple(cycle_limited),
        )

    @staticmethod
    def _activation_inputs(
        subscription: BlackboardSubscription,
        latest: Mapping[tuple[str, str], BlackboardMaterial],
        current: BlackboardMaterial,
    ) -> dict[str, BlackboardMaterial] | None:
        if subscription.input_mode == "any":
            return {current.format_id: current}
        inputs = {
            format_id: latest[(subscription.subscription_id, format_id)]
            for format_id in subscription.input_formats
            if (subscription.subscription_id, format_id) in latest
        }
        return inputs if len(inputs) == len(subscription.input_formats) else None


__all__ = [
    "BlackboardActivation",
    "BlackboardAgentRuntime",
    "BlackboardCoordinator",
    "BlackboardMaterial",
    "BlackboardRunReport",
    "BlackboardSubscription",
    "MaterialBlackboardAuthority",
    "MaterialChange",
    "MaterialOutput",
]
