# [OMNI] origin=codex domain=services/team_builder ts=2026-07-24T00:00:00Z type=contract
# [OMNI] summary="Adapt Team Architect output into canonical TeamSpec position and generation fields"
# [OMNI] why="Team Builder must generate the existing TeamSpec instead of introducing a parallel team or position schema"
"""Canonical Team Builder design adapter.

This module deliberately defines no Team/position data model.  It normalizes the
LLM-authored design and validates its nested values with the canonical protocol
models in :mod:`omnicompany.protocol.team`.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping

from omnicompany.packages.services._core.team_builder.package_location import (
    canonical_team_package_path,
    team_data_path,
)
from omnicompany.protocol.team import (
    TeamGenerationMethod,
    TeamGenerationSpec,
    TeamPositionActivation,
    TeamPositionSpec,
)


TEAM_BUILDER_ID = "team-builder"
_NON_RESPONSIBILITY_DEFAULT = "不承担已分配给其他 Team 岗位的职责"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _stable_intent_ref(intent: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(intent), ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"intent:sha256:{digest}"


def _request_ref(intent: Mapping[str, Any]) -> str:
    meta = intent.get("_meta")
    if isinstance(meta, Mapping):
        for key in ("origin_request_ref", "request_ref"):
            value = str(meta.get(key) or "").strip()
            if value:
                return value
    for key in ("origin_request_ref", "request_ref", "body_path"):
        value = str(intent.get(key) or "").strip()
        if value:
            return value
    return _stable_intent_ref(intent)


def _source_refs(references: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for item in references.get("references") or []:
        if not isinstance(item, Mapping):
            continue
        value = str(item.get("source_path") or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _slug(value: str) -> str:
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    snake = re.sub(r"[^a-zA-Z0-9]+", "_", snake).strip("_").lower()
    return snake or "position"


def _canonical_position(
    raw: Mapping[str, Any],
    *,
    activation_ref: str,
    force_active: bool = False,
) -> TeamPositionSpec:
    data = dict(raw)
    if force_active:
        data["activation"] = TeamPositionActivation.ACTIVE.value
    activation = str(data.get("activation") or TeamPositionActivation.ON_DEMAND.value)
    if activation == TeamPositionActivation.ACTIVE.value:
        evidence = _string_list(data.get("activation_evidence_refs"))
        if activation_ref not in evidence:
            evidence.append(activation_ref)
        data["activation_evidence_refs"] = evidence
    return TeamPositionSpec.model_validate(data)


def finalize_team_design(
    design: Mapping[str, Any],
    *,
    intent: Mapping[str, Any],
    references: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a Team Builder design aligned to canonical TeamSpec fields.

    Every generated Worker skeleton maps to a position.  Missing position
    records are deterministically derived from the Worker brief; explicit
    position records remain authoritative for their organizational wording.
    The generation provenance is always stamped by code, never trusted to the
    LLM.
    """

    result = deepcopy(dict(design))
    request_ref = _request_ref(intent)
    team_name = str(result.get("team_name") or "").strip()
    if not team_name:
        raise ValueError("team_design.team_name is required")
    team_id = str(result.get("team_id") or "").strip()
    result["team_id"] = team_id or team_name.replace("_", "-")

    # Deployment location is one exact, validated value.  An explicit upstream
    # request may supply it so TeamArchitect does not have to infer directory
    # governance.  If both sources exist, disagreement is an error rather than
    # a silent precedence rule.
    design_target = result.get("target_package_path")
    intent_target = intent.get("target_package_path")
    if not intent_target:
        intent_meta = intent.get("_meta")
        if isinstance(intent_meta, Mapping):
            intent_target = intent_meta.get("target_package_path")

    normalized_design_target = (
        canonical_team_package_path(design_target, team_name=team_name)
        if design_target
        else None
    )
    normalized_intent_target = (
        canonical_team_package_path(intent_target, team_name=team_name)
        if intent_target
        else None
    )
    if (
        normalized_design_target
        and normalized_intent_target
        and normalized_design_target != normalized_intent_target
    ):
        raise ValueError(
            "target_package_path conflicts between the explicit request and "
            "TeamArchitect output"
        )
    target_package_path = canonical_team_package_path(
        normalized_intent_target or normalized_design_target,
        team_name=team_name,
    )
    result["target_package_path"] = target_package_path
    result["design_path"] = f"{target_package_path}DESIGN.md"
    result["workspace_skeleton"] = {
        "write_prefixes": [
            target_package_path,
            team_data_path(target_package_path, team_name=team_name),
        ],
        "read_prefixes": "READ_ANY",
        "bash_cwd_prefixes": [""],
    }

    raw_workers = result.get("workers_skeleton") or []
    if not isinstance(raw_workers, list):
        raise ValueError("team_design.workers_skeleton must be a list")
    workers = [dict(item) for item in raw_workers if isinstance(item, Mapping)]

    raw_positions = result.get("positions") or []
    if not isinstance(raw_positions, list):
        raise ValueError("team_design.positions must be a list")

    position_inputs: dict[str, dict[str, Any]] = {}
    position_order: list[str] = []
    for item in raw_positions:
        if not isinstance(item, Mapping):
            raise ValueError("each team_design.positions item must be an object")
        position_id = str(item.get("id") or "").strip()
        if not position_id:
            raise ValueError("each team_design.positions item requires id")
        if position_id in position_inputs:
            raise ValueError(f"duplicate TeamPositionSpec.id: {position_id}")
        position_inputs[position_id] = dict(item)
        position_order.append(position_id)

    used_ids = set(position_inputs)
    mapped_ids: set[str] = set()
    for worker in workers:
        worker_id = str(worker.get("worker_name") or worker.get("worker_id") or "").strip()
        if not worker_id:
            raise ValueError("each workers_skeleton item requires worker_name or worker_id")
        position_id = str(worker.get("position_id") or "").strip()
        if not position_id:
            candidate = _slug(worker_id)
            position_id = candidate
            suffix = 2
            while position_id in used_ids and position_id not in position_inputs:
                position_id = f"{candidate}_{suffix}"
                suffix += 1
            worker["position_id"] = position_id

        if position_id not in position_inputs:
            brief = str(worker.get("brief") or f"执行 {worker_id} 节点职责").strip()
            position_inputs[position_id] = {
                "id": position_id,
                "name": str(worker.get("position_name") or worker_id).strip(),
                "responsibilities": [brief],
                "non_responsibilities": [_NON_RESPONSIBILITY_DEFAULT],
                "activation": TeamPositionActivation.ACTIVE.value,
                "activation_evidence_refs": [request_ref],
            }
            position_order.append(position_id)
            used_ids.add(position_id)
        mapped_ids.add(position_id)

    canonical_positions: list[dict[str, Any]] = []
    for position_id in position_order:
        raw = position_inputs[position_id]
        position = _canonical_position(
            raw,
            activation_ref=request_ref,
            force_active=position_id in mapped_ids,
        )
        canonical_positions.append(position.model_dump(mode="json"))

    active_ids = {
        item["id"]
        for item in canonical_positions
        if item["activation"] == TeamPositionActivation.ACTIVE.value
    }
    unmapped_active = sorted(active_ids - mapped_ids)
    if unmapped_active:
        raise ValueError(
            "active positions require a workers_skeleton mapping: "
            f"{unmapped_active}"
        )

    raw_generation = result.get("generation")
    parent_team_id: str | None = None
    if isinstance(raw_generation, Mapping):
        parent_team_id = str(raw_generation.get("parent_team_id") or "").strip() or None
    if parent_team_id is None:
        meta = intent.get("_meta")
        if isinstance(meta, Mapping):
            parent_team_id = str(meta.get("parent_team_id") or "").strip() or None

    generation = TeamGenerationSpec(
        method=TeamGenerationMethod.TEAM_BUILDER,
        builder_id=TEAM_BUILDER_ID,
        request_ref=request_ref,
        source_refs=_source_refs(references),
        parent_team_id=parent_team_id,
    )

    result["workers_skeleton"] = workers
    result["positions"] = canonical_positions
    result["generation"] = generation.model_dump(mode="json", exclude_none=True)
    if not isinstance(result.get("_meta"), dict):
        result["_meta"] = {}
    result["_meta"]["canonical_team_contract"] = {
        "team_type": "omnicompany.protocol.team.TeamSpec",
        "position_type": "omnicompany.protocol.team.TeamPositionSpec",
        "builder_id": TEAM_BUILDER_ID,
    }
    return result


__all__ = ["TEAM_BUILDER_ID", "finalize_team_design"]
