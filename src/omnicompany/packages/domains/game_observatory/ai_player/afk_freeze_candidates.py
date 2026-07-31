"""Build the evidence-backed, deliberately non-frozen AFK stage-0 candidate set."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


BUNDLE_PATH = Path(
    "data/domains/game_observatory/drafts/afk-journey-rowan-hero-detail.partial.v1.json"
)
INVENTORY_PATH = Path(
    "docs/plans/game-observatory/"
    "[2026-07-15]AI-PLAYER-LIFELONG-EXPLORATION-v1/afk-benchmark-inventory.md"
)
DATABASE_PATH = Path("data/domains/game_observatory/observatory.sqlite3")
ADJUDICATION_PATH = Path(
    "data/domains/game_observatory/benchmarks/ai_player/fixtures/"
    "afk_hero_growth_v1_candidate_v4_adjudication.v1.json"
)
DEFAULT_OUTPUT_PATH = Path(
    "data/domains/game_observatory/benchmarks/ai_player/fixtures/afk_hero_growth_v1_candidate_v4"
)

INVALID_REPLAY_ROUTE_IDS = {
    "route.afk.candidate.r6.collapsed-wall-to-boots-source",
    "route.afk.candidate.r7.patched-source-to-green-source",
    "route.afk.candidate.r8.level-info-to-headband-source",
}
ADJUDICATED_STATE_IDS = {
    "screen.afk.world.source-tracking.savanna",
    "screen.afk.common.exit-confirmation-overlay",
}
ADJUDICATED_EDGE_IDS = {
    "transition.afk.world.source-tracking-to-exit-confirmation",
    "transition.afk.common.exit-confirmation-dismiss-outside",
}

EXCLUDED_TRANSITIONS = {
    "transition.afk.rowan.hero-to-skill": "no registered return path from the skill page",
    "transition.afk.rowan.skill-brief-to-detail": (
        "no registered return path from detail mode to brief mode"
    ),
    "transition.afk.rowan.hero-to-normal-equipment": (
        "no registered return path from normal equipment to hero main"
    ),
    "transition.afk.rowan.season-wall-to-gloves": (
        "gloves detail has no registered return path"
    ),
    "transition.afk.rowan.season-level-info-to-wall": (
        "system-back locator acceptance has not been adjudicated"
    ),
    "transition.afk.rowan.hero-next-to-cecia": (
        "inventory recorded an erroneous Rowan self-loop; corrected endpoint drift is not "
        "accepted without human re-adjudication"
    ),
    "transition.afk.cecia.hero-previous-to-rowan": (
        "inventory recorded an erroneous Rowan self-loop; corrected endpoint drift is not "
        "accepted without human re-adjudication"
    ),
}

PROJECTED_SAFE_INTERACTIONS = (
    "interaction.afk.rowan.attributes-back",
    "interaction.afk.rowan.normal-equipment-collapse",
    "interaction.afk.rowan.season-lamp-source-close",
    "interaction.afk.rowan.season-lamp-back",
    "interaction.afk.rowan.season-gloves-source-close",
)

UNRESOLVED_INTERACTION_EDGES = (
    "interaction.afk.rowan.skill-select-nodes",
    "interaction.afk.rowan.normal-equipment-scroll",
)

ADJACENT_ENTRY_OBJECTS = {
    "ui.afk.rowan.character-art",
    "ui.afk.rowan.skill-demo-button",
    "ui.afk.rowan.trial-entry",
}
PROHIBITED_OBJECTS = {"ui.afk.rowan.trial-chat-button"}

PURE_NAVIGATION_UI_OBJECT_IDS = {"ui.afk.rowan.normal-equipment-expand"}
PURE_NAVIGATION_INTERACTION_IDS = (
    "interaction.afk.rowan.normal-equipment-expand",
    "interaction.afk.rowan.normal-equipment-collapse",
    "interaction.afk.rowan.attributes-back",
    "interaction.afk.rowan.season-lamp-source-close",
    "interaction.afk.rowan.season-gloves-source-close",
    "interaction.afk.rowan.season-patched-bow-tie-source-close",
    "interaction.afk.rowan.season-winged-headband-source-close",
    "interaction.afk.rowan.season-green-robe-source-close",
    "interaction.afk.rowan.season-leather-boots-source-close",
    "interaction.afk.rowan.season-lamp-back",
    "interaction.afk.rowan.season-patched-bow-tie-back",
    "interaction.afk.rowan.season-winged-headband-back",
    "interaction.afk.rowan.season-green-robe-back",
    "interaction.afk.rowan.season-leather-boots-back",
    "interaction.afk.rowan.season-wall-collapse-cards",
    "interaction.afk.rowan.season-wall-expand-cards",
)


def _projected_object_id(interaction_id: str) -> str:
    return f"candidate.object.{interaction_id.removeprefix('interaction.')}"


PURE_NAVIGATION_OBJECT_FAMILIES = (
    {
        "id": "family.current.normal-equipment-disclosure",
        "member_object_ids": [
            "ui.afk.rowan.normal-equipment-expand",
            _projected_object_id("interaction.afk.rowan.normal-equipment-collapse"),
        ],
    },
    {
        "id": "family.current.bottom-left-back",
        "member_object_ids": [
            _projected_object_id("interaction.afk.rowan.attributes-back"),
            _projected_object_id("interaction.afk.rowan.season-lamp-back"),
            _projected_object_id("interaction.afk.rowan.season-patched-bow-tie-back"),
            _projected_object_id("interaction.afk.rowan.season-winged-headband-back"),
            _projected_object_id("interaction.afk.rowan.season-green-robe-back"),
            _projected_object_id("interaction.afk.rowan.season-leather-boots-back"),
        ],
    },
    {
        "id": "family.current.season-source-dismiss",
        "member_object_ids": [
            _projected_object_id("interaction.afk.rowan.season-lamp-source-close"),
            _projected_object_id("interaction.afk.rowan.season-gloves-source-close"),
            _projected_object_id("interaction.afk.rowan.season-patched-bow-tie-source-close"),
            _projected_object_id("interaction.afk.rowan.season-winged-headband-source-close"),
            _projected_object_id("interaction.afk.rowan.season-green-robe-source-close"),
            _projected_object_id("interaction.afk.rowan.season-leather-boots-source-close"),
        ],
    },
    {
        "id": "family.current.season-wall-card-row-disclosure",
        "member_object_ids": [
            _projected_object_id("interaction.afk.rowan.season-wall-collapse-cards"),
            _projected_object_id("interaction.afk.rowan.season-wall-expand-cards"),
        ],
    },
)

ROUTE_SPECS = (
    {
        "id": "route.afk.candidate.r1.hero-to-identity",
        "start_state_id": "screen.afk.rowan.hero-main",
        "goal_state_id": "screen.afk.rowan.identity-overlay",
        "interaction_ids": ["interaction.afk.rowan.identity-open"],
    },
    {
        "id": "route.afk.candidate.r2.identity-to-attributes-lower",
        "start_state_id": "screen.afk.rowan.identity-overlay",
        "goal_state_id": "screen.afk.rowan.attributes-overlay-lower",
        "interaction_ids": [
            "interaction.afk.rowan.identity-close",
            "interaction.afk.rowan.attributes-open",
            "interaction.afk.rowan.attributes-scroll-to-lower",
        ],
        "action_budget": 6,
    },
    {
        "id": "route.afk.candidate.r3.attributes-lower-to-hero",
        "start_state_id": "screen.afk.rowan.attributes-overlay-lower",
        "goal_state_id": "screen.afk.rowan.hero-main",
        "interaction_ids": [
            "interaction.afk.rowan.attributes-scroll-to-upper",
            "interaction.afk.rowan.attributes-back",
        ],
    },
    {
        "id": "route.afk.candidate.r4.normal-equipment-cycle",
        "start_state_id": "screen.afk.rowan.normal-equipment-collapsed",
        "goal_state_id": "screen.afk.rowan.normal-equipment-collapsed",
        "interaction_ids": [
            "interaction.afk.rowan.normal-equipment-expand",
            "interaction.afk.rowan.normal-equipment-scroll",
            "interaction.afk.rowan.normal-equipment-collapse",
        ],
        "unresolved_reasons": ["scroll remains an unadjudicated semantic self-loop"],
    },
    {
        "id": "route.afk.candidate.r5.normal-equipment-collapse-expand",
        "start_state_id": "screen.afk.rowan.normal-equipment-expanded",
        "goal_state_id": "screen.afk.rowan.normal-equipment-expanded",
        "interaction_ids": [
            "interaction.afk.rowan.normal-equipment-collapse",
            "interaction.afk.rowan.normal-equipment-expand",
        ],
    },
    {
        "id": "route.afk.candidate.r6.collapsed-wall-to-boots-source",
        "start_state_id": "screen.afk.rowan.season-equipment-wall-collapsed",
        "goal_state_id": "screen.afk.rowan.season-source-leather-boots",
        "interaction_ids": [
            "interaction.afk.rowan.season-wall-expand-cards",
            "interaction.afk.rowan.season-leather-boots-open",
            "interaction.afk.rowan.season-leather-boots-source-open",
        ],
    },
    {
        "id": "route.afk.candidate.r7.patched-source-to-green-source",
        "start_state_id": "screen.afk.rowan.season-source-patched-bow-tie",
        "goal_state_id": "screen.afk.rowan.season-source-green-robe",
        "interaction_ids": [
            "interaction.afk.rowan.season-patched-bow-tie-source-close",
            "interaction.afk.rowan.season-patched-bow-tie-back",
            "interaction.afk.rowan.season-green-robe-open",
            "interaction.afk.rowan.season-green-robe-source-open",
        ],
    },
    {
        "id": "route.afk.candidate.r8.level-info-to-headband-source",
        "start_state_id": "screen.afk.rowan.season-level-info-overlay",
        "goal_state_id": "screen.afk.rowan.season-source-winged-wooden-headband",
        "interaction_ids": [
            "interaction.afk.rowan.season-level-info-close",
            "interaction.afk.rowan.season-winged-headband-open",
            "interaction.afk.rowan.season-winged-headband-source-open",
        ],
        "unresolved_reasons": ["system-back locator acceptance is not frozen"],
    },
)

INTERRUPTION_SPECS = (
    ("after_observation_before_action", "reobserve_before_any_action"),
    ("after_action_before_effect_check", "reobserve_and_resolve_pending_action"),
    ("after_effect_check_before_memory_append", "recheck_effect_then_append_once"),
    ("during_frontier_task_cas", "reload_task_version_then_retry_cas"),
    ("before_skill_result_adjudication", "reload_evidence_then_adjudicate"),
    ("before_session_capsule_append", "reobserve_then_append_next_sequence"),
)


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[6]


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _candidate(payload: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(payload)
    item["candidate_hash"] = _sha256_bytes(_canonical_bytes(item))
    return item


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _immutable_write_json(path: Path, payload: Any) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != raw:
            raise FileExistsError(f"immutable candidate file already differs: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return _sha256_bytes(raw)


class _EvidenceCatalog:
    def __init__(self, workspace_root: Path, bundle_sha256: str) -> None:
        self.root = workspace_root
        self.bundle_sha256 = bundle_sha256
        database = (workspace_root / DATABASE_PATH).resolve()
        self.connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.references: dict[tuple[str, str], dict[str, Any]] = {}

    def close(self) -> None:
        self.connection.close()

    def canonical_ref(self, source: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "kind": "canonical_fragment",
            "id": str(source["id"]),
            "path": BUNDLE_PATH.as_posix(),
            "sha256": self.bundle_sha256,
            "fragment_sha256": _sha256_bytes(_canonical_bytes(source)),
        }

    def inventory_ref(self, inventory_sha256: str) -> dict[str, Any]:
        return {
            "kind": "source_document",
            "id": "afk-benchmark-inventory.2026-07-15",
            "path": INVENTORY_PATH.as_posix(),
            "sha256": inventory_sha256,
        }

    def step_ref(self, step_id: str) -> dict[str, Any]:
        key = ("evidence_step", step_id)
        if key not in self.references:
            row = self.connection.execute(
                "SELECT body_json FROM evidence_steps WHERE id=?",
                (step_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"missing evidence step: {step_id}")
            body_json = str(row["body_json"])
            body = json.loads(body_json)
            if body.get("status") != "passed":
                raise ValueError(f"candidate evidence step is not passed: {step_id}")
            self.references[key] = {
                "kind": "evidence_step",
                "id": step_id,
                "evidence_run_id": body["evidence_run_id"],
                "status": body["status"],
                "sha256": _sha256_bytes(body_json.encode("utf-8")),
            }
        return dict(self.references[key])

    def artifact_ref(self, artifact_id: str) -> dict[str, Any]:
        key = ("artifact", artifact_id)
        if key not in self.references:
            row = self.connection.execute(
                "SELECT body_json FROM artifacts WHERE id=?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"missing artifact registration: {artifact_id}")
            body = json.loads(row["body_json"])
            path = Path(body["path"])
            if not path.is_absolute():
                path = self.root / path
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"artifact file is unreadable: {artifact_id}: {path}")
            file_hash = _sha256_file(path)
            if body.get("sha256") != file_hash:
                raise ValueError(f"artifact hash mismatch: {artifact_id}")
            self.references[key] = {
                "kind": "artifact",
                "id": artifact_id,
                "path": _relative_path(path, self.root),
                "sha256": file_hash,
                "media_kind": body.get("kind"),
            }
        return dict(self.references[key])

    def refs_for(self, source: Mapping[str, Any]) -> list[dict[str, Any]]:
        refs = [self.canonical_ref(source)]
        refs.extend(self.step_ref(item) for item in source.get("evidence_step_ids", []))
        refs.extend(self.artifact_ref(item) for item in source.get("artifact_ids", []))
        return _deduplicate_refs(refs)


def _deduplicate_refs(refs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for reference in refs:
        item = dict(reference)
        key = (str(item["kind"]), str(item["id"]))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _index_by_id(items: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): dict(item) for item in items}


def _strict_sources(bundle: Mapping[str, Any]) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    state_ids = set(bundle["content_partition"]["strict_surface_ids"])
    states = [item for item in bundle["screen_states"] if item["id"] in state_ids]
    interactions = [
        item
        for item in bundle["interactions"]
        if item.get("from_state_id") in state_ids and item.get("to_state_id") in state_ids
    ]
    transitions = [
        item
        for item in bundle["state_transitions"]
        if item.get("from_state_id") in state_ids and item.get("to_state_id") in state_ids
    ]
    if (len(states), len(interactions), len(transitions)) != (24, 43, 36):
        raise ValueError(
            "AFK strict source inventory drifted: expected states/interactions/transitions "
            f"24/43/36, got {len(states)}/{len(interactions)}/{len(transitions)}"
        )
    return states, interactions, transitions


def _load_v4_adjudication(
    workspace_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = (workspace_root / ADJUDICATION_PATH).resolve()
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema") != "game-observatory.ai-player.afk-v4-adjudication.v1":
        raise ValueError("AFK v4 adjudication schema is invalid")
    if payload.get("benchmark_id") != "afk_hero_growth_v1":
        raise ValueError("AFK v4 adjudication benchmark_id is invalid")
    if payload.get("human_truth_status") != "not_signed":
        raise ValueError("AFK v4 adjudication must not claim human truth")

    state_ids = {item.get("id") for item in payload.get("observed_states", [])}
    edge_ids = {item.get("id") for item in payload.get("observed_edges", [])}
    invalid_route_ids = {item.get("route_id") for item in payload.get("invalid_replays", [])}
    if state_ids != ADJUDICATED_STATE_IDS:
        raise ValueError("AFK v4 adjudication observed state set drifted")
    if edge_ids != ADJUDICATED_EDGE_IDS:
        raise ValueError("AFK v4 adjudication observed edge set drifted")
    if invalid_route_ids != INVALID_REPLAY_ROUTE_IDS:
        raise ValueError("AFK v4 adjudication invalid replay route set drifted")
    for replay in payload["invalid_replays"]:
        if replay.get("replay_status") != "invalid_start_state":
            raise ValueError(f"invalid replay status drifted: {replay.get('route_id')}")
        if replay.get("semantic_goal_status") != "failed_not_reached":
            raise ValueError(f"semantic goal status drifted: {replay.get('route_id')}")
        if replay.get("observed_start_state_id") not in ADJUDICATED_STATE_IDS:
            raise ValueError(f"invalid replay observed start is unresolved: {replay.get('route_id')}")
        recovery = replay.get("recovery_chain", {})
        if recovery.get("replay_status") != "not_verified_current_build":
            raise ValueError(f"recovery status drifted: {replay.get('route_id')}")
        if not recovery.get("interaction_ids"):
            raise ValueError(f"recovery chain is empty: {replay.get('route_id')}")
    reference = {
        "kind": "source_document",
        "id": str(payload["id"]),
        "path": ADJUDICATION_PATH.as_posix(),
        "sha256": _sha256_bytes(raw),
    }
    return payload, reference


def _adjudication_evidence_refs(
    source: Mapping[str, Any],
    evidence: _EvidenceCatalog,
    adjudication_ref: Mapping[str, Any],
) -> list[dict[str, Any]]:
    refs = [dict(adjudication_ref)]
    refs.extend(evidence.step_ref(item) for item in source.get("evidence_step_ids", []))
    refs.extend(evidence.artifact_ref(item) for item in source.get("artifact_ids", []))
    return _deduplicate_refs(refs)


def _state_candidates(
    states: Iterable[Mapping[str, Any]],
    evidence: _EvidenceCatalog,
    adjudication: Mapping[str, Any],
    adjudication_ref: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates = [
        _candidate(
            {
                "id": source["id"],
                "semantic_status": "candidate",
                "frozen": False,
                "name": source.get("name"),
                "visible_facts": source.get("visible_facts", []),
                "evidence_status": "raw_evidence_resolved",
                "evidence_refs": evidence.refs_for(source),
            }
        )
        for source in states
    ]
    candidates.extend(
        _candidate(
            {
                "id": source["id"],
                "source_kind": "v4_adjudication_fixture",
                "semantic_status": "evidence_backed_candidate",
                "frozen": False,
                "name": source.get("name"),
                "visible_facts": source.get("visible_facts", []),
                "evidence_status": "raw_evidence_resolved",
                "human_truth_status": "not_signed",
                "evidence_refs": _adjudication_evidence_refs(
                    source,
                    evidence,
                    adjudication_ref,
                ),
            }
        )
        for source in adjudication["observed_states"]
    )
    return candidates


def _edge_candidates(
    transitions: Iterable[Mapping[str, Any]],
    interactions: Iterable[Mapping[str, Any]],
    evidence: _EvidenceCatalog,
    adjudication: Mapping[str, Any],
    adjudication_ref: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    interaction_index = _index_by_id(interactions)
    safe_edges: list[dict[str, Any]] = []
    excluded_edges: list[dict[str, Any]] = []
    for source in transitions:
        payload = {
            "id": source["id"],
            "source_kind": "canonical_transition",
            "source_id": source["id"],
            "from_state_id": source["from_state_id"],
            "to_state_id": source["to_state_id"],
            "interaction_ids": source.get("via_interaction_ids", []),
            "frozen": False,
            "evidence_refs": evidence.refs_for(source),
        }
        reason = EXCLUDED_TRANSITIONS.get(str(source["id"]))
        if reason:
            payload.update(
                {
                    "candidate_status": "excluded",
                    "safe_candidate": False,
                    "exclusion_reason": reason,
                }
            )
            excluded_edges.append(_candidate(payload))
        else:
            payload.update({"candidate_status": "candidate", "safe_candidate": True})
            safe_edges.append(_candidate(payload))

    for interaction_id in PROJECTED_SAFE_INTERACTIONS:
        source = interaction_index[interaction_id]
        safe_edges.append(
            _candidate(
                {
                    "id": f"candidate.transition.{interaction_id.removeprefix('interaction.')}",
                    "source_kind": "interaction_projection",
                    "source_id": interaction_id,
                    "from_state_id": source["from_state_id"],
                    "to_state_id": source["to_state_id"],
                    "interaction_ids": [interaction_id],
                    "candidate_status": "candidate",
                    "safe_candidate": True,
                    "frozen": False,
                    "evidence_refs": evidence.refs_for(source),
                }
            )
        )

    for source in adjudication["observed_edges"]:
        safe_edges.append(
            _candidate(
                {
                    "id": source["id"],
                    "source_kind": "v4_adjudication_observed_transition",
                    "source_id": source["interaction_id"],
                    "from_state_id": source["from_state_id"],
                    "to_state_id": source["to_state_id"],
                    "interaction_ids": [source["interaction_id"]],
                    "semantic_effect": source["semantic_effect"],
                    "candidate_status": "evidence_backed_candidate",
                    "safe_candidate": True,
                    "frozen": False,
                    "human_truth_status": "not_signed",
                    "evidence_refs": _adjudication_evidence_refs(
                        source,
                        evidence,
                        adjudication_ref,
                    ),
                }
            )
        )

    unresolved_edges = []
    for interaction_id in UNRESOLVED_INTERACTION_EDGES:
        source = interaction_index[interaction_id]
        unresolved_edges.append(
            _candidate(
                {
                    "id": f"candidate.transition.{interaction_id.removeprefix('interaction.')}",
                    "source_kind": "interaction_projection",
                    "source_id": interaction_id,
                    "from_state_id": source["from_state_id"],
                    "to_state_id": source["to_state_id"],
                    "candidate_status": "unresolved",
                    "safe_candidate": False,
                    "frozen": False,
                    "unresolved_reason": "semantic self-loop requires final truth adjudication",
                    "evidence_refs": evidence.refs_for(source),
                }
            )
        )
    if len(safe_edges) != 36:
        raise ValueError(f"expected 36 unambiguous safe edge candidates, got {len(safe_edges)}")
    return {
        "safe_edges": safe_edges,
        "excluded_edges": excluded_edges,
        "unresolved_edges": unresolved_edges,
    }


def _input_target_ids(interaction: Mapping[str, Any]) -> list[str]:
    input_spec = interaction.get("input", {})
    result = list(input_spec.get("targets", []))
    if input_spec.get("target"):
        result.append(str(input_spec["target"]))
    return result


def _object_candidates(
    bundle: Mapping[str, Any],
    interactions: Iterable[Mapping[str, Any]],
    evidence: _EvidenceCatalog,
) -> list[dict[str, Any]]:
    interaction_list = list(interactions)
    strict_target_ids = {
        target_id
        for interaction in interaction_list
        for target_id in _input_target_ids(interaction)
    }
    objects: list[dict[str, Any]] = []
    known_ids = {str(item["id"]) for item in bundle["ui_elements"]}
    for source in bundle["ui_elements"]:
        if not source.get("bounds"):
            continue
        object_id = str(source["id"])
        if object_id in PURE_NAVIGATION_UI_OBJECT_IDS:
            classification = "pure_navigation"
            classification_status = "evidence_backed_candidate"
        elif object_id in ADJACENT_ENTRY_OBJECTS:
            classification = "adjacent_play_entry"
            classification_status = "candidate"
        elif object_id in PROHIBITED_OBJECTS:
            classification = "prohibited"
            classification_status = "candidate"
        elif object_id in strict_target_ids:
            classification = "play"
            classification_status = "candidate"
        else:
            classification = "unresolved"
            classification_status = "unresolved"
        objects.append(
            _candidate(
                {
                    "id": object_id,
                    "source_kind": "canonical_ui_element",
                    "source_id": object_id,
                    "screen_state_ids": source.get("screen_state_ids", []),
                    "name": source.get("name"),
                    "role": source.get("role"),
                    "bounds": source["bounds"],
                    "classification": classification,
                    "classification_status": classification_status,
                    "frozen": False,
                    "evidence_refs": evidence.refs_for(source),
                }
            )
        )

    for source in interaction_list:
        input_spec = source.get("input", {})
        target_ids = _input_target_ids(source)
        has_known_target = any(target in known_ids for target in target_ids)
        point = input_spec.get("point")
        if not point or has_known_target:
            continue
        objects.append(
            _candidate(
                {
                    "id": f"candidate.object.{source['id'].removeprefix('interaction.')}",
                    "source_kind": "state_bound_interaction_point",
                    "source_id": source["id"],
                    "screen_state_ids": [source["from_state_id"]],
                    "name": source["id"].rsplit(".", 1)[-1],
                    "role": source.get("immediate_feedback"),
                    "point": point,
                    "classification": (
                        "pure_navigation"
                        if source["id"] in PURE_NAVIGATION_INTERACTION_IDS
                        else "play"
                    ),
                    "classification_status": (
                        "evidence_backed_candidate"
                        if source["id"] in PURE_NAVIGATION_INTERACTION_IDS
                        else "candidate"
                    ),
                    "frozen": False,
                    "evidence_refs": evidence.refs_for(source),
                }
            )
        )
    if len(objects) != 51:
        raise ValueError(f"expected 51 bounded/projected object candidates, got {len(objects)}")
    return objects


def _route_candidates(
    interaction_index: Mapping[str, Mapping[str, Any]],
    evidence: _EvidenceCatalog,
    adjudication: Mapping[str, Any],
    adjudication_ref: Mapping[str, Any],
) -> list[dict[str, Any]]:
    result = []
    invalid_replays = {
        str(item["route_id"]): item for item in adjudication["invalid_replays"]
    }
    for spec in ROUTE_SPECS:
        sources = [interaction_index[item] for item in spec["interaction_ids"]]
        refs = _deduplicate_refs(
            reference for source in sources for reference in evidence.refs_for(source)
        )
        payload = {
            **spec,
            "candidate_status": "candidate",
            "frozen": False,
            "goal_predicate": {"state_id_equals": spec["goal_state_id"]},
            "action_budget": spec.get("action_budget", max(4, len(sources) + 2)),
            "recovery_rule": "restore the fixture start state; do not replay pending action",
            "replay_status": "not_run",
            "human_truth_status": "not_signed",
            "evidence_refs": refs,
        }
        invalid = invalid_replays.get(str(spec["id"]))
        if invalid is not None:
            unresolved_reasons = list(spec.get("unresolved_reasons", []))
            unresolved_reasons.extend(invalid.get("unresolved_reasons", []))
            payload.update(
                {
                    "required_start_predicate": {
                        "state_id_equals": spec["start_state_id"],
                    },
                    "replay_status": invalid["replay_status"],
                    "semantic_goal_status": invalid["semantic_goal_status"],
                    "replay_can_be_frozen": False,
                    "observed_start_state_id": invalid["observed_start_state_id"],
                    "observed_end_state_id": invalid["observed_end_state_id"],
                    "invalid_replay": {
                        "evidence_run_id": invalid["evidence_run_id"],
                        "actual_step_semantics": invalid["actual_step_semantics"],
                    },
                    "invalid_replay_refs": _adjudication_evidence_refs(
                        invalid,
                        evidence,
                        adjudication_ref,
                    ),
                    "recovery_chain": invalid["recovery_chain"],
                    "recovery_rule": (
                        "abort before action when required_start_predicate fails; "
                        "the expected recovery chain is not verified on the current build"
                    ),
                }
            )
            if unresolved_reasons:
                payload["unresolved_reasons"] = list(dict.fromkeys(unresolved_reasons))
        result.append(
            _candidate(payload)
        )
    return result


def _interruption_candidates(inventory_ref: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        _candidate(
            {
                "id": f"interruption.afk.candidate.{injection_point}",
                "injection_point": injection_point,
                "candidate_status": "unresolved",
                "frozen": False,
                "controlled_injection_executed": False,
                "historical_failure_counts_as_injection": False,
                "resume_rule": resume_rule,
                "raw_gameplay_evidence_refs": [],
                "evidence_refs": [dict(inventory_ref)],
                "unresolved_reason": "fixture shape only; no controlled interruption run exists",
            }
        )
        for injection_point, resume_rule in INTERRUPTION_SPECS
    ]


def _boundary_candidates(
    bundle: Mapping[str, Any],
    states: list[Mapping[str, Any]],
    interactions: list[Mapping[str, Any]],
    evidence: _EvidenceCatalog,
    inventory_ref: Mapping[str, Any],
) -> list[dict[str, Any]]:
    interaction_index = _index_by_id(bundle["interactions"])
    play_refs = _deduplicate_refs(
        reference
        for source in [states[0], interactions[0]]
        for reference in evidence.refs_for(source)
    )
    adjacent = bundle["play_connections"][0]
    adjacent_source = {
        **adjacent,
        "artifact_ids": [adjacent["representative_artifact_id"]],
        "evidence_step_ids": [],
    }
    exclusion = bundle["excluded_content"][0]
    exclusion_sources = [
        interaction_index[item]
        for item in exclusion.get("interaction_ids", [])
        if item in interaction_index
    ]
    prohibited_refs = _deduplicate_refs(
        reference
        for source in exclusion_sources
        for reference in evidence.refs_for(source)
    )
    pure_navigation_sources = [
        next(item for item in bundle["ui_elements"] if item["id"] == object_id)
        for object_id in sorted(PURE_NAVIGATION_UI_OBJECT_IDS)
    ] + [interaction_index[item] for item in PURE_NAVIGATION_INTERACTION_IDS]
    pure_navigation_refs = _deduplicate_refs(
        reference
        for source in pure_navigation_sources
        for reference in evidence.refs_for(source)
    )
    pure_navigation_object_ids = [
        object_id for family in PURE_NAVIGATION_OBJECT_FAMILIES for object_id in family["member_object_ids"]
    ]
    return [
        _candidate(
            {
                "id": "boundary.afk.candidate.hero-growth-play",
                "classification": "play",
                "candidate_status": "candidate",
                "frozen": False,
                "member_state_ids": [item["id"] for item in states],
                "member_interaction_ids": [
                    item["id"]
                    for item in interactions
                    if item["id"] not in PURE_NAVIGATION_INTERACTION_IDS
                ],
                "evidence_refs": play_refs,
            }
        ),
        _candidate(
            {
                "id": "boundary.afk.candidate.hero-presentation-entry",
                "classification": "adjacent_play_entry",
                "candidate_status": "candidate",
                "frozen": False,
                "connection_id": adjacent["id"],
                "member_object_ids": ["ui.afk.rowan.character-art"],
                "evidence_refs": evidence.refs_for(adjacent_source),
            }
        ),
        _candidate(
            {
                "id": "boundary.afk.candidate.pure-navigation",
                "classification": "pure_navigation",
                "candidate_status": "evidence_backed_candidate",
                "frozen": False,
                "member_object_ids": pure_navigation_object_ids,
                "object_families": [dict(item) for item in PURE_NAVIGATION_OBJECT_FAMILIES],
                "state_bound_instance_count": len(pure_navigation_object_ids),
                "semantic_object_family_count": len(PURE_NAVIGATION_OBJECT_FAMILIES),
                "raw_gameplay_evidence_refs": [
                    reference
                    for reference in pure_navigation_refs
                    if reference["kind"] in {"evidence_step", "artifact"}
                ],
                "evidence_refs": [*pure_navigation_refs, dict(inventory_ref)],
                "candidate_note": (
                    "16 state-bound instances grouped into 4 semantic object families; "
                    "skill-detail and season-level-help remain outside this candidate until "
                    "their return-edge and target-label defects are corrected"
                ),
            }
        ),
        _candidate(
            {
                "id": "boundary.afk.candidate.competitive-chat-exclusion",
                "classification": "prohibited",
                "candidate_status": "candidate",
                "frozen": False,
                "exclusion_id": exclusion["id"],
                "member_object_ids": ["ui.afk.rowan.trial-chat-button"],
                "evidence_refs": prohibited_refs,
            }
        ),
    ]


def build_afk_freeze_candidates(
    workspace_root: Path | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Create a deterministic candidate package without changing canonical inputs."""

    root = (workspace_root or _workspace_root()).resolve()
    destination = (output_dir or (root / DEFAULT_OUTPUT_PATH)).resolve()
    bundle_file = root / BUNDLE_PATH
    inventory_file = root / INVENTORY_PATH
    bundle_raw = bundle_file.read_bytes()
    inventory_raw = inventory_file.read_bytes()
    bundle = json.loads(bundle_raw)
    bundle_hash = _sha256_bytes(bundle_raw)
    inventory_hash = _sha256_bytes(inventory_raw)
    adjudication, adjudication_ref = _load_v4_adjudication(root)
    states, interactions, transitions = _strict_sources(bundle)
    interaction_index = _index_by_id(interactions)
    evidence = _EvidenceCatalog(root, bundle_hash)
    try:
        state_items = _state_candidates(
            states,
            evidence,
            adjudication,
            adjudication_ref,
        )
        edge_items = _edge_candidates(
            transitions,
            interactions,
            evidence,
            adjudication,
            adjudication_ref,
        )
        object_items = _object_candidates(bundle, interactions, evidence)
        route_items = _route_candidates(
            interaction_index,
            evidence,
            adjudication,
            adjudication_ref,
        )
        inventory_ref = evidence.inventory_ref(inventory_hash)
        interruption_items = _interruption_candidates(inventory_ref)
        boundary_items = _boundary_candidates(
            bundle,
            states,
            interactions,
            evidence,
            inventory_ref,
        )
        evidence_index = sorted(
            [*evidence.references.values(), inventory_ref, adjudication_ref],
            key=lambda item: (str(item["kind"]), str(item["id"])),
        )
    finally:
        evidence.close()

    collections: dict[str, Any] = {}
    payloads = {
        "states": {
            "schema": "game-observatory.ai-player.afk-state-candidates.v1",
            "items": state_items,
        },
        "edges": {
            "schema": "game-observatory.ai-player.afk-edge-candidates.v1",
            **edge_items,
        },
        "objects": {
            "schema": "game-observatory.ai-player.afk-object-candidates.v1",
            "items": object_items,
        },
        "boundaries": {
            "schema": "game-observatory.ai-player.afk-boundary-candidates.v1",
            "items": boundary_items,
        },
        "evidence_index": {
            "schema": "game-observatory.ai-player.afk-evidence-index.v1",
            "items": evidence_index,
        },
    }
    for name, payload in payloads.items():
        relative = f"{name}.v1.json"
        digest = _immutable_write_json(destination / relative, payload)
        collections[name] = {"path": relative, "sha256": digest}

    route_files = []
    for index, route in enumerate(route_items, start=1):
        relative = f"routes/r{index}.json"
        digest = _immutable_write_json(destination / relative, route)
        route_files.append({"id": route["id"], "path": relative, "sha256": digest})
    collections["routes"] = route_files

    interruption_files = []
    for index, interruption in enumerate(interruption_items, start=1):
        relative = f"interruptions/i{index}.json"
        digest = _immutable_write_json(destination / relative, interruption)
        interruption_files.append(
            {"id": interruption["id"], "path": relative, "sha256": digest}
        )
    collections["interruptions"] = interruption_files

    manifest = {
        "schema": "game-observatory.ai-player.afk-freeze-candidate-manifest.v1",
        "id": "afk_hero_growth_v1_candidate_v4",
        "benchmark_id": "afk_hero_growth_v1",
        "semantic_status": "candidate",
        "freeze_status": "not_frozen",
        "frozen": False,
        "freeze_pass": False,
        "candidate_revision": "2026-07-16.4",
        "source": {
            "canonical_bundle": {
                "path": BUNDLE_PATH.as_posix(),
                "id": bundle["id"],
                "sha256": bundle_hash,
            },
            "inventory": {"path": INVENTORY_PATH.as_posix(), "sha256": inventory_hash},
            "adjudication_fixture": {
                "path": ADJUDICATION_PATH.as_posix(),
                "id": adjudication["id"],
                "sha256": adjudication_ref["sha256"],
                "human_truth_status": adjudication["human_truth_status"],
            },
            "database": {"path": DATABASE_PATH.as_posix(), "access": "read_only"},
        },
        "counts": {
            "state_candidates": len(state_items),
            "safe_edge_candidates": len(edge_items["safe_edges"]),
            "excluded_edges": len(edge_items["excluded_edges"]),
            "object_candidates": len(object_items),
            "route_fixtures": len(route_items),
            "controlled_interruption_fixtures": len(interruption_items),
            "boundary_classes": len(boundary_items),
            "frozen_items": 0,
            "human_truth_signatures": 0,
        },
        "collections": collections,
        "known_blockers": [
            "pure_navigation boundary is evidence-backed but not human-signed",
            "controlled interruptions have not been executed",
            "routes have not been replayed or human-signed",
            "r6, r7, and r8 live replays started from the wrong semantic state",
            "r6, r7, and r8 expected recovery chains are not verified on the current build",
            "candidate states, edges, and objects are not frozen truth",
            "human truth signature is absent",
        ],
        "human_truth_signature": None,
    }
    manifest["manifest_hash"] = _sha256_bytes(_canonical_bytes(manifest))
    manifest_path = destination / "candidate_manifest.v1.json"
    _immutable_write_json(manifest_path, manifest)
    validation = validate_afk_freeze_candidate(manifest_path, workspace_root=root)
    _immutable_write_json(destination / "validation.v1.json", validation)
    return manifest_path


def _load_collection(
    destination: Path,
    reference: Mapping[str, Any],
    errors: list[str],
) -> Any:
    path = destination / str(reference["path"])
    if not path.is_file():
        errors.append(f"missing collection file: {reference['path']}")
        return None
    if _sha256_file(path) != reference.get("sha256"):
        errors.append(f"collection hash mismatch: {reference['path']}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check_candidate_hash(item: Mapping[str, Any]) -> bool:
    payload = dict(item)
    expected = payload.pop("candidate_hash", None)
    return expected == _sha256_bytes(_canonical_bytes(payload))


def _iter_candidate_items(
    states: Mapping[str, Any],
    edges: Mapping[str, Any],
    objects: Mapping[str, Any],
    boundaries: Mapping[str, Any],
    routes: Iterable[Mapping[str, Any]],
    interruptions: Iterable[Mapping[str, Any]],
) -> Iterable[Mapping[str, Any]]:
    yield from states.get("items", [])
    yield from edges.get("safe_edges", [])
    yield from edges.get("excluded_edges", [])
    yield from edges.get("unresolved_edges", [])
    yield from objects.get("items", [])
    yield from boundaries.get("items", [])
    yield from routes
    yield from interruptions


def validate_afk_freeze_candidate(
    manifest_path: Path,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Validate candidate structure and always fail closed on freeze semantics."""

    root = (workspace_root or _workspace_root()).resolve()
    manifest_path = manifest_path.resolve()
    destination = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    blockers: list[str] = []
    if manifest.get("semantic_status") != "candidate":
        errors.append("manifest semantic_status must remain candidate")
    if manifest.get("frozen") is not False or manifest.get("freeze_pass") is not False:
        errors.append("candidate manifest cannot claim frozen or freeze_pass")
    digest_payload = dict(manifest)
    expected_manifest_hash = digest_payload.pop("manifest_hash", None)
    if expected_manifest_hash != _sha256_bytes(_canonical_bytes(digest_payload)):
        errors.append("manifest_hash mismatch")

    manifest_adjudication = manifest.get("source", {}).get("adjudication_fixture", {})
    expected_adjudication = (root / ADJUDICATION_PATH).resolve()
    if manifest_adjudication.get("path") != ADJUDICATION_PATH.as_posix():
        errors.append("manifest does not name the AFK v4 adjudication fixture")
    elif (
        not expected_adjudication.is_file()
        or manifest_adjudication.get("sha256") != _sha256_file(expected_adjudication)
    ):
        errors.append("manifest AFK v4 adjudication fixture hash mismatch")
    if manifest_adjudication.get("human_truth_status") != "not_signed":
        errors.append("manifest adjudication fixture must remain unsigned")

    collections = manifest.get("collections", {})
    states = _load_collection(destination, collections.get("states", {}), errors) or {}
    edges = _load_collection(destination, collections.get("edges", {}), errors) or {}
    objects = _load_collection(destination, collections.get("objects", {}), errors) or {}
    boundaries = _load_collection(destination, collections.get("boundaries", {}), errors) or {}
    _load_collection(destination, collections.get("evidence_index", {}), errors)
    routes = [
        _load_collection(destination, item, errors) or {}
        for item in collections.get("routes", [])
    ]
    interruptions = [
        _load_collection(destination, item, errors) or {}
        for item in collections.get("interruptions", [])
    ]

    state_ids = {item.get("id") for item in states.get("items", [])}
    safe_edges = edges.get("safe_edges", [])
    excluded_ids = {item.get("id") for item in edges.get("excluded_edges", [])}
    bad_switches = {
        "transition.afk.rowan.hero-next-to-cecia",
        "transition.afk.cecia.hero-previous-to-rowan",
    }
    if len(state_ids) != 26:
        errors.append(f"expected 26 unique state candidates, got {len(state_ids)}")
    if not ADJUDICATED_STATE_IDS.issubset(state_ids):
        errors.append("AFK v4 adjudicated states are missing")
    if len(safe_edges) < 30:
        errors.append(f"expected at least 30 safe edge candidates, got {len(safe_edges)}")
    if bad_switches.intersection({item.get("id") for item in safe_edges}):
        errors.append("known erroneous hero-switch edges entered the safe set")
    if not bad_switches.issubset(excluded_ids):
        errors.append("known erroneous hero-switch edges are not explicitly excluded")
    safe_edge_ids = {item.get("id") for item in safe_edges}
    if not ADJUDICATED_EDGE_IDS.issubset(safe_edge_ids):
        errors.append("AFK v4 adjudicated observed edges are missing")
    for edge in safe_edges:
        if edge.get("from_state_id") not in state_ids or edge.get("to_state_id") not in state_ids:
            errors.append(f"safe edge endpoint is unresolved: {edge.get('id')}")
    if len(objects.get("items", [])) < 40:
        errors.append("fewer than 40 object candidates")
    if len(routes) != 8:
        errors.append(f"expected 8 route fixtures, got {len(routes)}")
    for route in routes:
        if route.get("start_state_id") not in state_ids or route.get("goal_state_id") not in state_ids:
            errors.append(f"route endpoint is unresolved: {route.get('id')}")
    invalid_routes = {item.get("id"): item for item in routes if item.get("id") in INVALID_REPLAY_ROUTE_IDS}
    if set(invalid_routes) != INVALID_REPLAY_ROUTE_IDS:
        errors.append("AFK v4 invalid replay routes are incomplete")
    for route_id, route in invalid_routes.items():
        if route.get("replay_status") != "invalid_start_state":
            errors.append(f"invalid replay status mismatch: {route_id}")
        if route.get("semantic_goal_status") != "failed_not_reached":
            errors.append(f"semantic goal status mismatch: {route_id}")
        if route.get("replay_can_be_frozen") is not False:
            errors.append(f"invalid replay can be frozen: {route_id}")
        if route.get("required_start_predicate") != {
            "state_id_equals": route.get("start_state_id")
        }:
            errors.append(f"required start predicate mismatch: {route_id}")
        if route.get("observed_start_state_id") != "screen.afk.world.source-tracking.savanna":
            errors.append(f"observed start state mismatch: {route_id}")
        recovery = route.get("recovery_chain", {})
        if recovery.get("replay_status") != "not_verified_current_build":
            errors.append(f"recovery replay status mismatch: {route_id}")
        if not recovery.get("interaction_ids"):
            errors.append(f"recovery chain is empty: {route_id}")
        invalid_refs = route.get("invalid_replay_refs", [])
        if not invalid_refs:
            errors.append(f"invalid replay evidence is missing: {route_id}")
        positive_step_ids = {
            item.get("id") for item in route.get("evidence_refs", []) if item.get("kind") == "evidence_step"
        }
        invalid_step_ids = {
            item.get("id") for item in invalid_refs if item.get("kind") == "evidence_step"
        }
        if positive_step_ids.intersection(invalid_step_ids):
            errors.append(f"invalid replay evidence counted as route semantic evidence: {route_id}")
    if len(interruptions) != 6:
        errors.append(f"expected 6 controlled interruption fixtures, got {len(interruptions)}")
    if any(item.get("controlled_injection_executed") for item in interruptions):
        errors.append("candidate interruption fixture falsely claims execution")
    boundary_items = boundaries.get("items", [])
    if {item.get("classification") for item in boundary_items} != {
        "play",
        "adjacent_play_entry",
        "pure_navigation",
        "prohibited",
    }:
        errors.append("the four boundary classes are incomplete")
    pure_navigation = next(
        (item for item in boundary_items if item.get("classification") == "pure_navigation"),
        {},
    )
    pure_navigation_status = pure_navigation.get("candidate_status")
    if pure_navigation_status not in {"unresolved", "evidence_backed_candidate"}:
        errors.append("pure_navigation has an unsupported candidate status")
    if pure_navigation_status == "evidence_backed_candidate":
        member_ids = list(pure_navigation.get("member_object_ids", []))
        families = list(pure_navigation.get("object_families", []))
        family_member_ids = [
            object_id for family in families for object_id in family.get("member_object_ids", [])
        ]
        object_index = {item.get("id"): item for item in objects.get("items", [])}
        if len(member_ids) != 16 or len(set(member_ids)) != 16:
            errors.append("pure_navigation requires 16 unique state-bound instances")
        if len(families) != 4 or len({item.get("id") for item in families}) != 4:
            errors.append("pure_navigation requires 4 unique semantic object families")
        if sorted(family_member_ids) != sorted(member_ids):
            errors.append("pure_navigation family members do not match boundary members")
        if pure_navigation.get("state_bound_instance_count") != 16:
            errors.append("pure_navigation state-bound instance count is incorrect")
        if pure_navigation.get("semantic_object_family_count") != 4:
            errors.append("pure_navigation semantic object family count is incorrect")
        if not pure_navigation.get("raw_gameplay_evidence_refs"):
            errors.append("pure_navigation evidence-backed candidate lacks raw evidence")
        for object_id in member_ids:
            item = object_index.get(object_id)
            if item is None:
                errors.append(f"pure_navigation object is absent: {object_id}")
            elif (
                item.get("classification") != "pure_navigation"
                or item.get("classification_status") != "evidence_backed_candidate"
            ):
                errors.append(f"pure_navigation object classification mismatch: {object_id}")

    database = (root / DATABASE_PATH).resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        for item in _iter_candidate_items(
            states,
            edges,
            objects,
            boundaries,
            routes,
            interruptions,
        ):
            if not _check_candidate_hash(item):
                errors.append(f"candidate_hash mismatch: {item.get('id')}")
            refs = item.get("evidence_refs", [])
            if not refs:
                errors.append(f"candidate has no provenance reference: {item.get('id')}")
            all_refs = [*refs, *item.get("invalid_replay_refs", [])]
            for reference in all_refs:
                if not reference.get("sha256"):
                    errors.append(f"evidence reference lacks hash: {item.get('id')}")
                    continue
                kind = reference.get("kind")
                if kind in {"artifact", "canonical_fragment", "source_document"}:
                    path = Path(str(reference.get("path", "")))
                    if not path.is_absolute():
                        path = root / path
                    if not path.is_file() or _sha256_file(path) != reference["sha256"]:
                        errors.append(f"unreadable or hash-invalid evidence: {reference.get('id')}")
                elif kind == "evidence_step":
                    row = connection.execute(
                        "SELECT body_json FROM evidence_steps WHERE id=?",
                        (reference.get("id"),),
                    ).fetchone()
                    if row is None or _sha256_bytes(str(row[0]).encode("utf-8")) != reference["sha256"]:
                        errors.append(f"missing or hash-invalid evidence step: {reference.get('id')}")
    finally:
        connection.close()

    blockers.extend(manifest.get("known_blockers", []))
    if pure_navigation.get("candidate_status") == "unresolved":
        blockers.append("pure_navigation has no frozen evidence set")
    elif pure_navigation.get("candidate_status") == "evidence_backed_candidate":
        blockers.append("pure_navigation evidence-backed candidate is not human-signed")
    if any(item.get("candidate_status") == "unresolved" for item in interruptions):
        blockers.append("controlled interruption fixtures are definitions only")
    if any(item.get("replay_status") != "passed" for item in routes):
        blockers.append("route fixtures have no passed replay")
    if manifest.get("human_truth_signature") is None:
        blockers.append("human truth signature is absent")
    blockers = list(dict.fromkeys(blockers))
    return {
        "schema": "game-observatory.ai-player.afk-candidate-validation.v1",
        "manifest_id": manifest.get("id"),
        "candidate_structure_pass": not errors,
        "freeze_pass": False,
        "errors": errors,
        "freeze_blockers": blockers,
        "validated_counts": {
            "states": len(state_ids),
            "safe_edges": len(safe_edges),
            "objects": len(objects.get("items", [])),
            "routes": len(routes),
            "interruptions": len(interruptions),
            "boundaries": len(boundary_items),
        },
    }
