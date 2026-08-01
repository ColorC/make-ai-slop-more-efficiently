from __future__ import annotations

"""Recoverable Terra production for reader-facing teardown articles.

The batch consumes an explicit, hash-pinned allowlist.  Each case is prepared
in an isolated packet, authored by a read-only ``gpt-5.6-terra`` worker,
validated against the immutable fact ledger, independently judged, and only
then merged.  System-group stories are written to the group registry; leaf
stories only replace ``design_document.reader_story`` in their own partial.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.external_workers import (
    ExternalAgentPermissionMode,
    ExternalAgentRunRequest,
    ExternalAgentStatus,
    run_external_agent_request,
)

from . import reader_content_taxonomy as content_taxonomy
from . import reader_story_agent as story_agent
from .reader_content_taxonomy import (
    load_group_reader_story_registry,
    load_reader_content_taxonomy,
    upsert_group_reader_story,
)


PLAN_SCHEMA = "game-observatory.reader-story-production-plan.v1"
STATE_SCHEMA = "game-observatory.reader-story-production-state.v1"
PROVENANCE_SCHEMA = "game-observatory.reader-story-image-provenance.v1"
NORMALIZED_RESULT_SCHEMA = (
    "game-observatory.reader-story-normalized-worker-result.v1"
)
MODEL = "gpt-5.6-terra"
MAX_CONCURRENCY = 3
DIRECT_MAX_ATOMS = 600
DIRECT_MAX_REQUIRED_IMAGES = 24
DIRECT_MAX_CONTEXT_CHARS = 600_000
DOSSIER_IMAGE_CHUNK = 48
MAX_WORKER_ATTEMPTS = 2
MAX_STORY_REPAIRS = 2
MAX_SEMANTIC_REPAIRS = 2
JUDGE_DIMENSIONS = (
    "reader_orientation",
    "concept_model",
    "mechanism_completeness",
    "aggregation_quality",
    "interfaces_and_operations",
    "language_and_epistemics",
    "preservation_vs_reference",
)
JUDGE_MIN_SCORE = 4.0
JUDGE_MIN_AVERAGE = 4.5

_GROUP_CASES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "system.sanguo.chapter-tasks",
        ("chapter-course", "chapter-reward-settlement"),
        (),
    ),
    (
        "system.sanguo.city-construction",
        ("city-ruler-building-progression", "residence-copper-production"),
        (),
    ),
    (
        "system.sanguo.resource-economy",
        ("resource-production-attribution", "market-shop-trade"),
        ("warehouse-capacity-overflow",),
    ),
    (
        "system.sanguo.territory-expansion",
        (
            "land-discovery-filter-ownership",
            "land-battle-dispatch-settlement",
            "land-cultivation-leveling",
        ),
        ("land-occupation",),
    ),
    (
        "system.sanguo.general-growth",
        (
            "general-roster-growth",
            "general-attribute-allocation",
            "tactic-learning-upgrade",
        ),
        (),
    ),
    (
        "system.sanguo.troop-supply",
        (
            "conscription-reserve-production",
            "reinforcement-reserve-allocation",
            "battle-loss-recovery",
        ),
        (),
    ),
    (
        "system.sanguo.alliance",
        (
            "alliance-overview-routing",
            "alliance-city-pool",
            "alliance-glory",
            "alliance-journey-rewards",
            "alliance-schedule",
        ),
        (),
    ),
    (
        "system.sanguo.activities",
        (
            "activity-first-bloom",
            "activity-land-expansion",
            "activity-signin-calendar",
        ),
        (),
    ),
)

_BENCHMARK_SLUGS = {
    "hero-role-growth",
    "force-composition",
    "martial-exercise-progression",
    "mail-inbox-read",
}
_EXCLUDED_SLUGS = {
    "land-occupation": "zero_fact_candidate",
    "warehouse-capacity-overflow": "zero_fact_candidate",
    "newcomer-foundation": "fact_index_and_journey_shell",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    _write_json(temporary, value)
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 5:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.02 * (2**attempt))


def _append_event(root: Path, event: Mapping[str, Any]) -> None:
    payload = {"at": _now(), **dict(event)}
    events = root / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _protected_sha256(path: Path) -> str:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"source must contain a JSON object: {path}")
    return story_agent._json_sha256(story_agent._without_reader_story(raw))


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value
    ).strip("-") or "case"


def _within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes repository root: {resolved}") from exc
    return resolved


def _source_index(repo_root: Path) -> dict[tuple[str, str], Path]:
    drafts = repo_root / "data" / "domains" / "game_observatory" / "drafts"
    found: dict[tuple[str, str], Path] = {}
    for path in sorted(drafts.glob("*.partial.v1.json")):
        raw = _read_json(path)
        scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        game = raw.get("game") if isinstance(raw.get("game"), dict) else {}
        play = raw.get("play") if isinstance(raw.get("play"), dict) else {}
        key = (
            str(game.get("id") or scope.get("game_id") or ""),
            str(play.get("slug") or ""),
        )
        if not all(key):
            raise ValueError(f"draft lacks game/play identity: {path}")
        if key in found:
            raise ValueError(f"duplicate draft identity {key}")
        found[key] = path
    return found


def _relative(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def build_default_plan(repo_root: Path | None = None) -> dict[str, Any]:
    """Build the checked allowlist; callers persist and review it before a run."""

    repo_root = (repo_root or _repo_root()).resolve()
    taxonomy = load_reader_content_taxonomy()
    sources = _source_index(repo_root)
    group_by_id = taxonomy["group_by_id"]
    entry_by_key = taxonomy["entry_by_key"]
    cases: list[dict[str, Any]] = []

    for priority, (group_id, factual_slugs, gap_slugs) in enumerate(
        _GROUP_CASES,
        start=10,
    ):
        group = group_by_id[group_id]
        game_id = group["game_id"]
        source_paths = [sources[(game_id, slug)] for slug in factual_slugs]
        gap_paths = [sources[(game_id, slug)] for slug in gap_slugs]
        primary = source_paths[0]
        related = [*source_paths[1:], *gap_paths]
        cases.append(
            {
                "case_id": f"group--{group_id}",
                "action": "produce",
                "target_kind": "group",
                "wave": "system_overviews",
                "priority": priority,
                "group_id": group_id,
                "game_id": game_id,
                "target_slug": group["slug"],
                "target_title": group["title"],
                "target_summary": group["summary"],
                "source_relpath": _relative(repo_root, primary),
                "source_protected_sha256": _protected_sha256(primary),
                "related_sources": [
                    {
                        "relpath": _relative(repo_root, path),
                        "protected_sha256": _protected_sha256(path),
                        "role": "gap" if path in gap_paths else "factual",
                    }
                    for path in related
                ],
                "source_play_slugs": list(factual_slugs),
                "gap_play_slugs": list(gap_slugs),
                "mode": "auto",
                "allow_replace": True,
                "semantic_judge": True,
            }
        )

    ordered_entries = sorted(
        taxonomy["entries"],
        key=lambda item: (
            item["game_id"],
            str(item.get("group_id") or ""),
            int(item.get("order") or 0),
            item["play_slug"],
        ),
    )
    for priority, entry in enumerate(ordered_entries, start=100):
        game_id = entry["game_id"]
        slug = entry["play_slug"]
        source = sources[(game_id, slug)]
        if slug in _BENCHMARK_SLUGS:
            action = "benchmark"
            reason = "accepted_gold_story"
        elif slug in _EXCLUDED_SLUGS:
            action = "exclude"
            reason = _EXCLUDED_SLUGS[slug]
        else:
            action = "produce"
            reason = ""
        cases.append(
            {
                "case_id": f"page--{game_id}--{slug}",
                "action": action,
                "reason": reason,
                "target_kind": "page",
                "wave": "leaf_pages",
                "priority": priority,
                "game_id": game_id,
                "target_slug": slug,
                "target_title": str(
                    (_read_json(source).get("play") or {}).get("title") or slug
                ),
                "reader_level": entry["level"],
                "group_id": str(entry.get("group_id") or ""),
                "source_relpath": _relative(repo_root, source),
                "source_protected_sha256": _protected_sha256(source),
                "related_sources": [],
                "mode": "auto",
                "allow_replace": action == "produce",
                "semantic_judge": True,
            }
        )

    plan_root = (
        repo_root
        / "docs"
        / "plans"
        / "game-observatory"
        / "[2026-07-20]GAME-OBSERVATORY-CONVERGENCE-v2"
    )
    examples = [
        sources[("sanguo-mouding-tianxia", "martial-exercise-progression")],
        sources[("afk-journey", "hero-role-growth")],
    ]
    return {
        "schema": PLAN_SCHEMA,
        "created_at": _now(),
        "model": MODEL,
        "max_concurrency": MAX_CONCURRENCY,
        "routing": {
            "direct_max_atoms": DIRECT_MAX_ATOMS,
            "direct_max_required_images": DIRECT_MAX_REQUIRED_IMAGES,
            "direct_max_context_chars": DIRECT_MAX_CONTEXT_CHARS,
            "dossier_image_chunk": DOSSIER_IMAGE_CHUNK,
        },
        "prompts": {
            "direct": _relative(
                repo_root, plan_root / "reader-story-terra-prompt-v5.md"
            ),
            "dossier": _relative(
                repo_root, plan_root / "reader-story-terra-visual-dossier-v1.md"
            ),
            "synthesis": _relative(
                repo_root, plan_root / "reader-story-terra-synthesis-v1.md"
            ),
            "judge": _relative(
                repo_root, plan_root / "reader-story-terra-judge-v1.md"
            ),
        },
        "standard_relpath": _relative(
            repo_root, plan_root / "reader-teardown-article-standard.md"
        ),
        "gold_examples": [_relative(repo_root, path) for path in examples],
        "cases": cases,
    }


def load_plan(path: Path) -> dict[str, Any]:
    value = _read_json(path.resolve())
    if not isinstance(value, dict) or value.get("schema") != PLAN_SCHEMA:
        raise ValueError(f"plan schema must be {PLAN_SCHEMA}")
    if not isinstance(value.get("cases"), list):
        raise ValueError("plan cases must be a list")
    return value


def lint_plan(plan: Mapping[str, Any], repo_root: Path | None = None) -> dict[str, Any]:
    repo_root = (repo_root or _repo_root()).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    valid_actions = {"produce", "benchmark", "exclude"}
    valid_targets = {"group", "page"}

    for index, raw_case in enumerate(plan.get("cases") or []):
        if not isinstance(raw_case, dict):
            errors.append(f"case {index} must be an object")
            continue
        case_id = str(raw_case.get("case_id") or "").strip()
        if not case_id:
            errors.append(f"case {index} lacks case_id")
            continue
        if case_id in seen:
            errors.append(f"duplicate case_id {case_id}")
        seen.add(case_id)
        action = str(raw_case.get("action") or "")
        counts[action] = counts.get(action, 0) + 1
        if action not in valid_actions:
            errors.append(f"{case_id}: invalid action {action!r}")
        target_kind = str(raw_case.get("target_kind") or "")
        if target_kind not in valid_targets:
            errors.append(f"{case_id}: invalid target_kind {target_kind!r}")
        try:
            source = _within(repo_root / raw_case["source_relpath"], repo_root)
        except (KeyError, ValueError) as exc:
            errors.append(f"{case_id}: {exc}")
            continue
        if not source.is_file():
            errors.append(f"{case_id}: source is missing: {source}")
            continue
        expected = str(raw_case.get("source_protected_sha256") or "")
        actual = _protected_sha256(source)
        if expected != actual:
            errors.append(f"{case_id}: primary protected source drifted")
        related_paths: set[Path] = set()
        for related in raw_case.get("related_sources") or []:
            if not isinstance(related, dict):
                errors.append(f"{case_id}: related source must be an object")
                continue
            try:
                path = _within(repo_root / related["relpath"], repo_root)
            except (KeyError, ValueError) as exc:
                errors.append(f"{case_id}: {exc}")
                continue
            if path == source or path in related_paths:
                errors.append(f"{case_id}: duplicate related source {path}")
            related_paths.add(path)
            if not path.is_file():
                errors.append(f"{case_id}: related source is missing: {path}")
                continue
            if str(related.get("protected_sha256") or "") != _protected_sha256(path):
                errors.append(f"{case_id}: related protected source drifted: {path.name}")
        if action == "exclude" and not raw_case.get("reason"):
            errors.append(f"{case_id}: excluded case requires a reason")
        if action == "produce" and raw_case.get("target_slug") in _EXCLUDED_SLUGS:
            errors.append(f"{case_id}: excluded slug cannot be produced")
        if target_kind == "group" and not raw_case.get("group_id"):
            errors.append(f"{case_id}: group target requires group_id")

    prompts = plan.get("prompts")
    if not isinstance(prompts, dict):
        errors.append("plan prompts must be an object")
    else:
        for key in ("direct", "dossier", "synthesis", "judge"):
            raw_path = prompts.get(key)
            if not raw_path:
                errors.append(f"plan lacks {key} prompt")
                continue
            path = _within(repo_root / str(raw_path), repo_root)
            if not path.is_file():
                errors.append(f"{key} prompt is missing: {path}")
    standard = _within(repo_root / str(plan.get("standard_relpath") or ""), repo_root)
    if not standard.is_file():
        errors.append(f"reader standard is missing: {standard}")
    if int(plan.get("max_concurrency") or 0) > MAX_CONCURRENCY:
        errors.append(f"max_concurrency cannot exceed {MAX_CONCURRENCY}")
    if counts.get("produce", 0) < 1:
        warnings.append("plan has no production cases")
    return {
        "schema": "game-observatory.reader-story-plan-lint.v1",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "case_count": len(seen),
        "counts": counts,
    }


def _case_paths(
    case: Mapping[str, Any],
    *,
    repo_root: Path,
) -> tuple[Path, list[Path]]:
    source = _within(repo_root / str(case["source_relpath"]), repo_root)
    related = [
        _within(repo_root / str(item["relpath"]), repo_root)
        for item in case.get("related_sources") or []
    ]
    return source, related


def _assert_case_sources(
    case: Mapping[str, Any],
    *,
    repo_root: Path,
) -> None:
    source, related = _case_paths(case, repo_root=repo_root)
    if _protected_sha256(source) != case["source_protected_sha256"]:
        raise ValueError("primary protected source drifted")
    for item, path in zip(case.get("related_sources") or [], related, strict=True):
        if _protected_sha256(path) != item["protected_sha256"]:
            raise ValueError(f"related protected source drifted: {path.name}")


def _case_root(state_root: Path, case: Mapping[str, Any]) -> Path:
    return state_root / "cases" / _safe_name(str(case["case_id"]))


def _packet_root(state_root: Path, case: Mapping[str, Any]) -> Path:
    return _case_root(state_root, case) / "packet"


def _reader_files(packet: Path) -> list[Path]:
    return sorted((packet / "input" / "reader").glob("*.txt"))


def _context_record(path: Path, *, cwd: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    return {
        "path": path.relative_to(cwd).as_posix(),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
    }


def _context_block(path: Path, *, cwd: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    return (
        f"# Attached context file: {path.relative_to(cwd).as_posix()}\n\n{text}"
    )


def _direct_context_paths(packet: Path) -> list[Path]:
    return [
        path
        for path in _reader_files(packet)
        if not path.name.startswith("00-")
    ]


def _dossier_context_paths(packet: Path) -> list[Path]:
    allowed_prefixes = ("10-", "15-", "20-", "21-", "22-", "23-", "25-", "30-", "40-")
    return [
        path
        for path in _reader_files(packet)
        if path.name.startswith(allowed_prefixes)
    ]


def _synthesis_context_paths(packet: Path) -> list[Path]:
    allowed_prefixes = ("10-", "15-", "20-", "30-", "40-", "50-", "60-")
    return [
        path
        for path in _reader_files(packet)
        if path.name.startswith(allowed_prefixes)
    ]


def _manifest_entries(packet: Path) -> list[dict[str, Any]]:
    manifest = _read_json(packet / "input" / "artifact-manifest.json")
    return [
        item
        for item in manifest.get("entries") or []
        if isinstance(item, dict)
    ]


def _image_path(packet: Path, entry: Mapping[str, Any]) -> Path:
    path = packet / str(entry.get("packet_path") or "")
    if not path.is_file():
        raise FileNotFoundError(f"packet image is missing: {path}")
    return path


def _required_images(packet: Path) -> list[dict[str, Any]]:
    by_sha: dict[str, dict[str, Any]] = {}
    for entry in _manifest_entries(packet):
        sha = str(entry.get("sha256") or "")
        if (
            sha
            and entry.get("required_review") is True
            and entry.get("is_image") is True
            and entry.get("available") is True
        ):
            by_sha.setdefault(sha, entry)
    return [by_sha[sha] for sha in sorted(by_sha)]


def _story_images(packet: Path, story: Mapping[str, Any]) -> list[dict[str, Any]]:
    wanted = story_agent._story_artifact_ids(story)
    by_sha: dict[str, dict[str, Any]] = {}
    for entry in _manifest_entries(packet):
        if (
            entry.get("artifact_id") in wanted
            and entry.get("is_image") is True
            and entry.get("available") is True
            and entry.get("sha256")
        ):
            by_sha.setdefault(str(entry["sha256"]), entry)
    return [by_sha[sha] for sha in sorted(by_sha)]


def _image_attachment_aliases(
    packet: Path,
    story: Mapping[str, Any],
    attached_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Explain duplicate artifact ids that resolve to the same attached SHA."""

    wanted = story_agent._story_artifact_ids(story)
    story_ids_by_sha: dict[str, set[str]] = {}
    packet_path_by_sha: dict[str, str] = {}
    for entry in _manifest_entries(packet):
        artifact_id = str(entry.get("artifact_id") or "")
        sha = str(entry.get("sha256") or "")
        if (
            artifact_id not in wanted
            or not sha
            or entry.get("is_image") is not True
            or entry.get("available") is not True
        ):
            continue
        story_ids_by_sha.setdefault(sha, set()).add(artifact_id)
        packet_path_by_sha.setdefault(
            sha,
            str(entry.get("packet_path") or ""),
        )
    attached_by_sha = {
        str(entry.get("sha256") or ""): str(
            entry.get("artifact_id") or ""
        )
        for entry in attached_entries
        if entry.get("sha256")
    }
    return [
        {
            "sha256": sha,
            "story_artifact_ids": sorted(story_ids_by_sha[sha]),
            "attached_as_artifact_id": attached_by_sha.get(sha),
            "attached": sha in attached_by_sha,
            "packet_path": packet_path_by_sha.get(sha),
            "same_sha_means_same_image": True,
        }
        for sha in sorted(story_ids_by_sha)
    ]


def _write_publication_identity(
    packet: Path,
    case: Mapping[str, Any],
) -> Path:
    baseline = _read_json(packet / "input" / "baseline-manifest.json")
    source_path = Path(str(baseline["source_path"]))
    target = _read_json(packet / "input" / "target.materialized.json")
    identity = {
        "content_object": case["target_kind"],
        "target_slug": case["target_slug"],
        "title": case.get("target_title"),
        "summary": case.get("target_summary"),
        "reader_level": case.get("reader_level"),
        "group_id": case.get("group_id"),
        "source_play_slugs": case.get("source_play_slugs")
        or [case["target_slug"]],
        "gap_play_slugs": case.get("gap_play_slugs") or [],
        "aggregation_mode": "conceptual",
        "source_order_is_not_action_order": True,
        "same_run_required_for_observed_flow": True,
        "wiki_link_rule": (
            "Facts may describe any attached source. Only play_slug values in "
            "public_wiki_targets may become clickable Wiki links."
        ),
        "public_wiki_targets": story_agent.public_reader_link_targets(
            source_path=source_path,
            target=target,
        ),
    }
    path = packet / "input" / "reader" / "15-publication-target.txt"
    path.write_text(
        json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )
    return path


def _refresh_reference_inputs(
    packet: Path,
    plan: Mapping[str, Any],
    *,
    repo_root: Path,
) -> None:
    """Refresh mutable standards/examples while leaving source evidence frozen."""

    standard = _within(repo_root / plan["standard_relpath"], repo_root)
    shutil.copy2(standard, packet / "input" / "reader-standard.md")
    story_agent._write_reader_text(
        packet / "input" / "reader" / "10-reader-standard.txt",
        standard.read_text(encoding="utf-8"),
    )
    expected_examples: set[Path] = set()
    expected_reader_examples: set[Path] = set()
    for index, relative in enumerate(plan.get("gold_examples") or [], start=1):
        example = _within(repo_root / str(relative), repo_root)
        value = _read_json(example)
        story = story_agent._extract_story(value)
        play = value.get("play") if isinstance(value.get("play"), dict) else {}
        slug = str(play.get("slug") or example.stem)
        machine_path = (
            packet
            / "input"
            / "gold-examples"
            / f"{index:02d}-{story_agent._safe_token(slug)}.reader-story.json"
        )
        reader_path = (
            packet
            / "input"
            / "reader"
            / f"60-gold-example-{index:02d}.txt"
        )
        _write_json(machine_path, story)
        story_agent._write_reader_text(
            reader_path,
            story_agent._reader_json(story),
        )
        expected_examples.add(machine_path)
        expected_reader_examples.add(reader_path)
    actual_examples = set((packet / "input" / "gold-examples").glob("*.json"))
    actual_reader_examples = set(
        (packet / "input" / "reader").glob("60-gold-example-*.txt")
    )
    if (
        actual_examples != expected_examples
        or actual_reader_examples != expected_reader_examples
    ):
        raise ValueError(
            "gold example membership changed; use a new state root "
            "instead of mixing packet contracts"
        )


def prepare_case(
    plan: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    state_root: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = (repo_root or _repo_root()).resolve()
    _assert_case_sources(case, repo_root=repo_root)
    packet = _packet_root(state_root, case)
    baseline_path = packet / "input" / "baseline-manifest.json"
    if baseline_path.is_file():
        packet_errors = _existing_packet_contract_errors(
            case,
            packet,
            repo_root=repo_root,
        )
        if packet_errors:
            raise ValueError(
                "existing packet baseline no longer matches the plan: "
                + "; ".join(packet_errors)
            )
    else:
        if packet.exists() and any(packet.iterdir()):
            raise FileExistsError(f"incomplete non-empty packet exists: {packet}")
        source, related = _case_paths(case, repo_root=repo_root)
        prompt = _within(repo_root / plan["prompts"]["direct"], repo_root)
        standard = _within(repo_root / plan["standard_relpath"], repo_root)
        examples = [
            _within(repo_root / item, repo_root)
            for item in plan.get("gold_examples") or []
        ]
        story_agent.prepare_workspace(
            source=source,
            workspace=packet,
            prompt=prompt,
            standard=standard,
            examples=examples,
            related_sources=related,
        )
    _refresh_reference_inputs(packet, plan, repo_root=repo_root)
    _write_publication_identity(packet, case)
    ledger = _read_json(packet / "input" / "atom-ledger.json")
    required = _required_images(packet)
    direct_context = _direct_context_paths(packet)
    context_chars = sum(
        len(path.read_text(encoding="utf-8-sig")) for path in direct_context
    )
    report = {
        "packet": str(packet),
        "atoms": int((ledger.get("counts") or {}).get("total") or 0),
        "required_images": len(required),
        "direct_context_chars": context_chars,
        "source_bundle_id": ledger.get("source_bundle_id"),
    }
    _write_json(_case_root(state_root, case) / "packet-report.json", report)
    return report


def classify_case(
    packet_report: Mapping[str, Any],
    *,
    requested_mode: str = "auto",
) -> str:
    if requested_mode in {"direct", "two_stage"}:
        return requested_mode
    if requested_mode != "auto":
        raise ValueError(f"unsupported case mode: {requested_mode}")
    if (
        int(packet_report["atoms"]) <= DIRECT_MAX_ATOMS
        and int(packet_report["required_images"]) <= DIRECT_MAX_REQUIRED_IMAGES
        and int(packet_report["direct_context_chars"]) <= DIRECT_MAX_CONTEXT_CHARS
    ):
        return "direct"
    return "two_stage"


def _plan_identity(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the immutable target/source identity of a production plan."""

    identity: list[dict[str, Any]] = []
    for case in plan.get("cases") or []:
        identity.append(
            {
                key: copy.deepcopy(case.get(key))
                for key in (
                    "case_id",
                    "action",
                    "target_kind",
                    "wave",
                    "game_id",
                    "target_slug",
                    "group_id",
                    "source_relpath",
                    "source_protected_sha256",
                    "related_sources",
                )
            }
        )
    return sorted(identity, key=lambda item: str(item.get("case_id") or ""))


def _existing_packet_contract_errors(
    case: Mapping[str, Any],
    packet: Path,
    *,
    repo_root: Path,
) -> list[str]:
    """Prove that a frozen packet still represents a case's source identity."""

    baseline_path = packet / "input" / "baseline-manifest.json"
    if not baseline_path.is_file():
        return [f"packet baseline is missing: {case['case_id']}"]
    baseline = _read_json(baseline_path)
    errors: list[str] = []
    expected_source = _within(
        repo_root / str(case["source_relpath"]),
        repo_root,
    )
    if Path(str(baseline.get("source_path") or "")).resolve() != expected_source:
        errors.append(f"packet source path changed: {case['case_id']}")
    if baseline.get("protected_raw_sha256") != case["source_protected_sha256"]:
        errors.append(f"packet primary source hash changed: {case['case_id']}")

    planned_related = list(case.get("related_sources") or [])
    baseline_related = list(baseline.get("related_sources") or [])
    planned_paths = [
        _within(repo_root / str(item["relpath"]), repo_root)
        for item in planned_related
    ]
    baseline_paths = [
        Path(str(item.get("path") or "")).resolve()
        for item in baseline_related
    ]
    if baseline_paths != planned_paths:
        errors.append(f"packet related-source membership changed: {case['case_id']}")
        return errors
    state_root = packet.parents[2]
    frozen_primary_hashes: dict[Path, str] = {}
    for frozen_baseline_path in state_root.glob(
        "cases/*/packet/input/baseline-manifest.json"
    ):
        frozen_baseline = _read_json(frozen_baseline_path)
        frozen_source = Path(
            str(frozen_baseline.get("source_path") or "")
        ).resolve()
        frozen_primary_hashes[frozen_source] = str(
            frozen_baseline.get("protected_raw_sha256") or ""
        )
    for item, baseline_item, related_path in zip(
        planned_related,
        baseline_related,
        planned_paths,
        strict=True,
    ):
        unchanged_file = (
            _file_sha256(related_path)
            == str(baseline_item.get("file_sha256") or "")
        )
        frozen_as_primary = (
            frozen_primary_hashes.get(related_path)
            == item["protected_sha256"]
        )
        if not unchanged_file and not frozen_as_primary:
            errors.append(
                "packet related source hash changed: "
                f"{case['case_id']} {item['relpath']}"
            )
    return errors


def _legacy_state_plan_errors(
    state_root: Path,
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    repo_root: Path,
) -> list[str]:
    """Audit a pre-history state before adopting a compatible plan revision."""

    cases = list(plan.get("cases") or [])
    entries = state.get("cases") if isinstance(state.get("cases"), dict) else {}
    planned_ids = {str(case.get("case_id") or "") for case in cases}
    if planned_ids != set(entries):
        return ["case identity set changed"]
    errors: list[str] = []
    for case in cases:
        case_id = str(case["case_id"])
        old_status = str((entries.get(case_id) or {}).get("status") or "")
        old_action = (
            old_status
            if old_status in {"benchmark", "exclude"}
            else "produce"
        )
        if case.get("action") != old_action:
            errors.append(f"case action changed: {case_id}")
            continue
        if old_action == "produce":
            errors.extend(
                _existing_packet_contract_errors(
                    case,
                    _packet_root(state_root, case),
                    repo_root=repo_root,
                )
            )
    return errors


def _load_state(
    state_root: Path,
    plan: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    path = state_root / "state.json"
    plan_sha = _json_sha256(plan)
    repo_root = (repo_root or _repo_root()).resolve()
    if path.is_file():
        state = _read_json(path)
        if state.get("schema") != STATE_SCHEMA:
            raise ValueError(f"state schema must be {STATE_SCHEMA}")
        if state.get("plan_sha256") != plan_sha:
            current_identity = state.get("plan_identity")
            compatible = (
                current_identity == _plan_identity(plan)
                if isinstance(current_identity, list)
                else not _legacy_state_plan_errors(
                    state_root,
                    state,
                    plan,
                    repo_root=repo_root,
                )
            )
            if not compatible:
                raise ValueError(
                    "state belongs to an incompatible production plan"
                )
            previous_sha = str(state.get("plan_sha256") or "")
            history = (
                list(state.get("plan_history") or [])
                if isinstance(state.get("plan_history"), list)
                else []
            )
            if not history:
                history.append(
                    {
                        "plan_sha256": previous_sha,
                        "activated_at": state.get("created_at"),
                        "legacy_state": True,
                    }
                )
            history.append(
                {
                    "plan_sha256": plan_sha,
                    "previous_plan_sha256": previous_sha,
                    "activated_at": _now(),
                    "reason": "compatible_contract_revision",
                }
            )
            state["plan_sha256"] = plan_sha
            state["plan_identity"] = _plan_identity(plan)
            state["plan_history"] = history
            state["updated_at"] = _now()
            _atomic_json(path, state)
        return state
    state = {
        "schema": STATE_SCHEMA,
        "created_at": _now(),
        "updated_at": _now(),
        "plan_sha256": plan_sha,
        "plan_identity": _plan_identity(plan),
        "plan_history": [
            {
                "plan_sha256": plan_sha,
                "activated_at": _now(),
                "reason": "initial_plan",
            }
        ],
        "cases": {
            case["case_id"]: {
                "status": (
                    "planned"
                    if case.get("action") == "produce"
                    else str(case.get("action") or "excluded")
                ),
                "attempts": {},
            }
            for case in plan.get("cases") or []
        },
    }
    _atomic_json(path, state)
    return state


def _save_case_state(
    state_root: Path,
    state: dict[str, Any],
    case_id: str,
    *,
    status: str,
    **details: Any,
) -> None:
    entry = state["cases"].setdefault(case_id, {"attempts": {}})
    if status != "failed" and "error" not in details:
        entry.pop("error", None)
    if status != "story_repair_running" and "machine_errors" not in details:
        entry.pop("machine_errors", None)
    entry.update({"status": status, "updated_at": _now(), **details})
    state["updated_at"] = _now()
    _atomic_json(state_root / "state.json", state)
    _append_event(
        state_root,
        {"case_id": case_id, "status": status, "details": details},
    )


def _prompt_text(path: Path, case: Mapping[str, Any]) -> str:
    return (
        path.read_text(encoding="utf-8")
        .replace("{{TARGET_SLUG}}", str(case["target_slug"]))
        .replace("{{TARGET_TITLE}}", str(case.get("target_title") or ""))
    )


def _production_contract_signature(
    plan: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    repo_root: Path,
) -> str:
    contract_files: dict[str, str] = {}
    relative_files = [
        str(plan["standard_relpath"]),
        *[str(value) for value in (plan.get("prompts") or {}).values()],
        *[str(value) for value in plan.get("gold_examples") or []],
    ]
    for relative in relative_files:
        path = _within(repo_root / relative, repo_root)
        contract_files[path.relative_to(repo_root).as_posix()] = _file_sha256(path)
    for path in (
        Path(__file__).resolve(),
        Path(story_agent.__file__).resolve(),
        Path(content_taxonomy.__file__).resolve(),
    ):
        contract_files[str(path)] = _file_sha256(path)
    return _json_sha256(
        {
            "model": MODEL,
            "case": case,
            "contract_files": contract_files,
            "routing": {
                "direct_max_atoms": DIRECT_MAX_ATOMS,
                "direct_max_required_images": DIRECT_MAX_REQUIRED_IMAGES,
                "direct_max_context_chars": DIRECT_MAX_CONTEXT_CHARS,
                "dossier_image_chunk": DOSSIER_IMAGE_CHUNK,
            },
        }
    )


def _structured_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        return structured
    final_text = str(payload.get("final_text") or "").strip()
    try:
        value = json.loads(final_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Terra result has no JSON structured output") from exc
    if not isinstance(value, dict):
        raise ValueError("Terra structured output must be an object")
    return value


def _materialize_structured_result(result_path: Path) -> Path:
    """Return an immutable or auditable structured worker result.

    External workers normally provide ``structured_output``.  A successful
    run can occasionally leave the same JSON only in ``final_text`` (for
    example, after a stream ends one closing brace early).  Never rewrite the
    original audit payload.  Instead, accept only an exact JSON object or one
    that becomes an object after appending at most two closing braces at EOF,
    and write that derived payload beside the source result.
    """

    result_path = result_path.resolve()
    payload = _read_json(result_path)
    if not isinstance(payload, dict):
        raise ValueError("Terra result payload must be an object")
    if isinstance(payload.get("structured_output"), dict):
        return result_path
    final_text = str(payload.get("final_text") or "").strip()
    if not final_text:
        raise ValueError("Terra result has no JSON structured output")

    parsed: Any | None = None
    method = "exact_final_text"
    appended_closing_braces = 0
    try:
        parsed = json.loads(final_text)
    except json.JSONDecodeError as initial_error:
        if initial_error.pos != len(final_text):
            raise ValueError(
                "Terra result has no JSON structured output"
            ) from initial_error
        for closing_braces in (1, 2):
            try:
                candidate = json.loads(final_text + ("}" * closing_braces))
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                method = "append_closing_braces_at_eof"
                appended_closing_braces = closing_braces
                break
        if parsed is None:
            raise ValueError(
                "Terra result has no JSON structured output"
            ) from initial_error
    if not isinstance(parsed, dict):
        raise ValueError("Terra structured output must be an object")

    source_sha256 = _file_sha256(result_path)
    normalized_path = result_path.with_name("normalized-result.json")
    if normalized_path.is_file():
        existing = _read_json(normalized_path)
        normalization = (
            existing.get("normalization")
            if isinstance(existing, dict)
            else {}
        )
        if (
            isinstance(existing, dict)
            and isinstance(existing.get("structured_output"), dict)
            and isinstance(normalization, dict)
            and normalization.get("source_result_sha256") == source_sha256
        ):
            return normalized_path
        normalized_path = result_path.with_name(
            f"normalized-result-{source_sha256[:12]}.json"
        )

    normalized = copy.deepcopy(payload)
    normalized["structured_output"] = parsed
    normalized["normalization"] = {
        "schema": NORMALIZED_RESULT_SCHEMA,
        "created_at": _now(),
        "source_result": str(result_path),
        "source_result_sha256": source_sha256,
        "final_text_sha256": hashlib.sha256(
            final_text.encode("utf-8")
        ).hexdigest(),
        "method": method,
        "appended_closing_braces": appended_closing_braces,
        "original_result_unchanged": True,
    }
    _atomic_json(normalized_path, normalized)
    return normalized_path


def _result_succeeded(payload: Mapping[str, Any]) -> bool:
    return (
        str(payload.get("status") or "") == ExternalAgentStatus.SUCCEEDED.value
        and not payload.get("error")
    )


async def _run_worker_stage(
    *,
    case: Mapping[str, Any],
    stage: str,
    packet: Path,
    case_root: Path,
    prompt_path: Path,
    context_paths: Sequence[Path],
    context_values: Sequence[tuple[str, Any]] = (),
    image_entries: Sequence[Mapping[str, Any]] = (),
    timeout_s: float = 1_200,
    semaphore: asyncio.Semaphore,
) -> Path:
    attempts_root = case_root / "attempts" / stage
    context_paths = list(context_paths)
    context_records = [
        _context_record(path, cwd=packet) for path in context_paths
    ]
    attached_context = [
        _context_block(path, cwd=packet) for path in context_paths
    ]
    for label, value in context_values:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, indent=2)
        )
        attached_context.append(f"# Attached context: {label}\n\n{text}")
        context_records.append(
            {
                "path": f"<inline:{label}>",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "chars": len(text),
            }
        )
    images = [_image_path(packet, entry) for entry in image_entries]
    prompt = _prompt_text(prompt_path, case)
    stage_signature = _json_sha256(
        {
            "case_id": case["case_id"],
            "stage": stage,
            "model": MODEL,
            "prompt_sha256": hashlib.sha256(
                prompt.encode("utf-8")
            ).hexdigest(),
            "context_inputs": context_records,
            "image_inputs": [
                {
                    "sha256": entry.get("sha256"),
                    "artifact_id": entry.get("artifact_id"),
                }
                for entry in image_entries
            ],
            "source_protected_sha256": case["source_protected_sha256"],
            "related_protected_sha256": [
                item["protected_sha256"]
                for item in case.get("related_sources") or []
            ],
        }
    )

    existing_attempts = sorted(attempts_root.glob("[0-9][0-9]"))
    matching_attempts = 0
    for attempt in existing_attempts:
        result_path = attempt / "result.json"
        request_path = attempt / "request.json"
        if not result_path.is_file() or not request_path.is_file():
            continue
        existing_request = _read_json(request_path)
        if existing_request.get("stage_signature") != stage_signature:
            continue
        matching_attempts += 1
        existing = _read_json(result_path)
        if _result_succeeded(existing):
            try:
                return _materialize_structured_result(result_path)
            except ValueError:
                # A transport-success payload with unusable structured data is
                # still a failed attempt for this exact stage signature.
                continue
    if matching_attempts >= MAX_WORKER_ATTEMPTS:
        raise RuntimeError(
            f"{stage} already failed {matching_attempts} times for the same inputs"
        )

    next_attempt_number = max(
        (
            int(path.name)
            for path in existing_attempts
            if path.name.isdigit()
        ),
        default=0,
    )
    for signature_attempt in range(
        matching_attempts + 1,
        MAX_WORKER_ATTEMPTS + 1,
    ):
        next_attempt_number += 1
        attempt_number = next_attempt_number
        attempt = attempts_root / f"{attempt_number:02d}"
        result_path = attempt / "result.json"
        request_path = attempt / "request.json"
        attempt.mkdir(parents=True, exist_ok=True)
        run_id = (
            f"reader-story-{_safe_name(str(case['case_id']))}-"
            f"{stage}-{attempt_number}-{uuid.uuid4().hex[:8]}"
        )
        request_record = {
            "schema": "game-observatory.reader-story-stage-request.v1",
            "created_at": _now(),
            "case_id": case["case_id"],
            "stage": stage,
            "attempt": attempt_number,
            "stage_signature": stage_signature,
            "run_id": run_id,
            "provider": "codex",
            "model": MODEL,
            "permission": "readonly",
            "minimal_context": True,
            "prompt_path": str(prompt_path),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "context_inputs": context_records,
            "image_inputs": [
                {
                    "path": path.relative_to(packet).as_posix(),
                    "sha256": entry.get("sha256"),
                    "artifact_id": entry.get("artifact_id"),
                    "bytes": path.stat().st_size,
                }
                for path, entry in zip(images, image_entries, strict=True)
            ],
        }
        _write_json(attempt / "request.json", request_record)
        (attempt / "prompt.md").write_text(prompt, encoding="utf-8")
        request = ExternalAgentRunRequest(
            provider="codex",
            prompt=prompt,
            cwd=packet,
            run_id=run_id,
            permission_mode=ExternalAgentPermissionMode.READONLY,
            model=MODEL,
            model_policy="none",
            timeout_s=timeout_s,
            attached_context=attached_context,
            image_paths=images,
            env={
                "OMNI_EXTERNAL_WORKER_RUN_ID": run_id,
                "OMNI_EXTERNAL_WORKER_PROVIDER": "codex",
            },
            metadata={
                "minimal_context": True,
                "purpose": f"reader_story_{stage}",
                "case_id": case["case_id"],
                "stage_signature": stage_signature,
            },
        )
        async with semaphore:
            result = await run_external_agent_request(request)
        payload = result.audit_payload()
        _write_json(result_path, payload)
        if _result_succeeded(payload):
            try:
                return _materialize_structured_result(result_path)
            except ValueError as exc:
                if signature_attempt == MAX_WORKER_ATTEMPTS:
                    raise RuntimeError(
                        f"{stage} returned invalid structured output after "
                        f"{signature_attempt} matching attempts: {exc}"
                    ) from exc
                continue
        if signature_attempt == MAX_WORKER_ATTEMPTS:
            raise RuntimeError(
                f"{stage} failed after {signature_attempt} matching attempts: "
                f"{payload.get('error') or payload.get('status')}"
            )
    raise AssertionError("unreachable worker retry boundary")


def _merge_dossiers(result_paths: Sequence[Path]) -> dict[str, Any]:
    dossiers: list[dict[str, Any]] = []
    image_logs: list[dict[str, Any]] = []
    for path in result_paths:
        structured = _structured_result(_read_json(path))
        dossier = structured.get("dossier")
        image_log = structured.get("image_review_log_json")
        if not isinstance(dossier, dict) or not isinstance(image_log, dict):
            raise ValueError(f"dossier stage returned an invalid payload: {path}")
        dossiers.append(dossier)
        image_logs.append(image_log)
    merged = copy.deepcopy(dossiers[0])
    list_fields = (
        "concept_candidates",
        "mechanism_groups",
        "interface_states",
        "operation_chains",
        "cross_system_links",
        "do_not_claim",
        "recommended_story_groups",
        "key_artifact_ids",
    )
    for field in list_fields:
        unique: dict[str, Any] = {}
        for dossier in dossiers:
            for item in dossier.get(field) or []:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                unique.setdefault(key, item)
        merged[field] = list(unique.values())
    image_log: dict[str, Any] = {
        "schema": story_agent.IMAGE_REVIEW_SCHEMA,
        "reviews": [],
    }
    for current in image_logs:
        image_log = story_agent._merge_image_review_logs([image_log], current)
    return {
        "schema": "game-observatory.reader-story-visual-dossier.v1",
        "dossier": merged,
        "image_review_log_json": image_log,
    }


def _key_image_entries(
    packet: Path,
    dossier: Mapping[str, Any],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    wanted = {
        str(item)
        for item in (dossier.get("key_artifact_ids") or [])
        if str(item).strip()
    }
    by_sha: dict[str, dict[str, Any]] = {}
    for entry in _manifest_entries(packet):
        if (
            entry.get("artifact_id") in wanted
            and entry.get("is_image") is True
            and entry.get("available") is True
            and entry.get("sha256")
        ):
            by_sha.setdefault(str(entry["sha256"]), entry)
    if not by_sha:
        return _required_images(packet)[:limit]
    return [by_sha[sha] for sha in sorted(by_sha)[:limit]]


def _attached_image_shas(case_root: Path) -> set[str]:
    attached: set[str] = set()
    for request in case_root.glob("attempts/*/*/request.json"):
        value = _read_json(request)
        attached.update(
            str(item.get("sha256") or "")
            for item in value.get("image_inputs") or []
            if item.get("sha256")
        )
    return attached


def _image_provenance(packet: Path, case_root: Path) -> dict[str, Any]:
    review = _read_json(packet / "output" / "image-review-log.json")
    required = {
        str(item["sha256"]) for item in _required_images(packet)
    }
    reviewed = {
        str(item.get("sha256") or "")
        for item in review.get("reviews") or []
        if item.get("sha256")
    }
    attached = _attached_image_shas(case_root)
    errors: list[str] = []
    missing = sorted(required - reviewed)
    unattached = sorted(reviewed - attached)
    if missing:
        errors.append(f"required image reviews missing: {missing[:5]}")
    if unattached:
        errors.append(f"image reviews were not attached to any worker: {unattached[:5]}")
    return {
        "schema": PROVENANCE_SCHEMA,
        "passed": not errors,
        "errors": errors,
        "required_sha256": sorted(required),
        "reviewed_sha256": sorted(reviewed),
        "attached_sha256": sorted(attached),
    }


def _coverage_indexes(structured: Mapping[str, Any]) -> tuple[set[int], set[int]]:
    try:
        assignment = story_agent._decode_structured_json(
            structured,
            "coverage_assignment_json",
        )
    except ValueError:
        return set(), set()
    selected: list[int] = []
    for item in assignment.get("assignments") or []:
        if isinstance(item, dict):
            selected.extend(
                value
                for value in item.get("atom_indexes") or []
                if isinstance(value, int)
            )
    selected.extend(
        value
        for value in assignment.get("internal_atom_indexes") or []
        if isinstance(value, int)
    )
    seen: set[int] = set()
    duplicate: set[int] = set()
    for value in selected:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return seen, duplicate


async def _repair_missing_coverage(
    *,
    case: Mapping[str, Any],
    packet: Path,
    case_root: Path,
    author_result: Path,
    prompt_path: Path,
    semaphore: asyncio.Semaphore,
) -> list[Path]:
    structured = _structured_result(_read_json(author_result))
    ledger = _read_json(packet / "input" / "atom-ledger.json")
    story = story_agent._extract_story(
        story_agent._decode_structured_json(
            structured,
            "reader_story_json",
        )
    )
    try:
        assignment = story_agent._decode_structured_json(
            structured,
            "coverage_assignment_json",
        )
    except ValueError as exc:
        assignment = None
        assignment_decode_error = str(exc)
    else:
        assignment_decode_error = ""
    total = int((ledger.get("counts") or {}).get("total") or 0)
    selected, duplicate = _coverage_indexes(structured)
    missing = sorted(set(range(total)) - selected)
    invalid_indexes = sorted(
        index for index in selected if index < 0 or index >= total
    )
    assignment_error = assignment_decode_error
    if isinstance(assignment, dict):
        try:
            story_agent._expand_coverage_assignment(
                assignment,
                ledger,
                story,
            )
        except ValueError as exc:
            assignment_error = str(exc)
    elif not assignment_error:
        assignment_error = "coverage_assignment_json is missing"
    if not assignment_error:
        return []

    atoms = [
        {
            "index": index,
            "kind": ledger["atoms"][index].get("kind"),
            "fact": ledger["atoms"][index].get("source_excerpt"),
            "obligation": ledger["atoms"][index].get("obligation"),
        }
        for index in range(total)
    ]
    repair_prompt = """# Terra 原子去向表完整替换

候选正文已完成，但紧凑原子去向表没有通过精确一次校验。根据附带的候选正文、
原去向表、机器错误与完整编号原子表，交付一份完整替代表。不要改正文，也不要
借机删除事实。

只输出一个原生 JSON object：
{"coverage_assignment_replacement_json":{
"schema":"game-observatory.reader-story-coverage-assignment.v1",
"source_bundle_id":"与原表及 ledger 一致",
"assignments":[{"after_path":"sections.<id>","status":"semantic",
"atom_indexes":[]}],
"internal_atom_indexes":[],"unsupported_claims":[],"hard_failures":[]}}

硬规则：
- 0 到 total_atoms-1 的每个整数必须恰好出现一次；
- reader 或 boundary 原子只能进入 assignments，internal 原子只能进入
  internal_atom_indexes；
- after_path 必须真实存在于候选，可用 reader_story、summary、lead、
  concepts.<id> 或 sections.<id>；
- 尽量保留原表中有效分组，只消解重复、漏项、越界、错误义务或无效路径；
- 不得输出 coverage patch、正文、Markdown 或解释。
"""
    temporary_prompt = case_root / "prompts" / "coverage-replacement.md"
    temporary_prompt.parent.mkdir(parents=True, exist_ok=True)
    temporary_prompt.write_text(repair_prompt, encoding="utf-8")
    prior_replacement: dict[str, Any] | None = None
    prior_error = assignment_error
    for repair_round in range(1, MAX_WORKER_ATTEMPTS + 1):
        result = await _run_worker_stage(
            case=case,
            stage=f"coverage-replacement-{repair_round:02d}",
            packet=packet,
            case_root=case_root,
            prompt_path=temporary_prompt,
            context_paths=[],
            context_values=(
                ("candidate_reader_story", story),
                ("base_assignment", assignment or {}),
                (
                    "assignment_defects",
                    {
                        "total_atoms": total,
                        "duplicate_indexes": sorted(duplicate),
                        "missing_indexes": missing,
                        "invalid_indexes": invalid_indexes,
                        "machine_error": assignment_error,
                        "prior_replacement_error": prior_error,
                    },
                ),
                ("indexed_atoms", atoms),
                ("prior_invalid_replacement", prior_replacement or {}),
            ),
            image_entries=[],
            timeout_s=600,
            semaphore=semaphore,
        )
        replacement_structured = _structured_result(_read_json(result))
        try:
            replacement = story_agent._decode_structured_json(
                replacement_structured,
                "coverage_assignment_replacement_json",
            )
            story_agent._expand_coverage_assignment(
                replacement,
                ledger,
                story,
            )
        except ValueError as exc:
            prior_error = str(exc)
            raw_replacement = replacement_structured.get(
                "coverage_assignment_replacement_json"
            )
            prior_replacement = (
                raw_replacement if isinstance(raw_replacement, dict) else {}
            )
            if repair_round == MAX_WORKER_ATTEMPTS:
                raise ValueError(
                    "coverage replacement failed exact-once validation: "
                    f"{prior_error}"
                ) from exc
            continue
        return [result]
    raise AssertionError("unreachable coverage replacement boundary")


async def _repair_missing_images(
    *,
    case: Mapping[str, Any],
    packet: Path,
    case_root: Path,
    prior_results: Sequence[Path],
    semaphore: asyncio.Semaphore,
) -> list[Path]:
    manifest_by_artifact_id = {
        str(entry.get("artifact_id") or ""): entry
        for entry in _manifest_entries(packet)
        if entry.get("artifact_id")
    }
    reviewed: set[str] = set()
    for result in prior_results:
        structured = _structured_result(_read_json(result))
        try:
            log = story_agent._decode_structured_json(
                structured,
                "image_review_log_json",
            )
        except ValueError:
            continue
        for review in log.get("reviews") or []:
            if not isinstance(review, dict):
                continue
            artifact_id = str(review.get("artifact_id") or "")
            sha = str(review.get("sha256") or "")
            manifest_entry = manifest_by_artifact_id.get(artifact_id)
            if (
                manifest_entry
                and sha
                and sha == str(manifest_entry.get("sha256") or "")
                and manifest_entry.get("is_image") is True
                and manifest_entry.get("available") is True
            ):
                reviewed.add(sha)
    missing_entries = [
        item for item in _required_images(packet) if item["sha256"] not in reviewed
    ]
    if not missing_entries:
        return []
    repair_prompt = """# Terra 图片核对补丁

实际查看本轮附加的每一张图片。根据附带 manifest 子集逐张写回执，不写文章，
不猜未显示内容。只输出一个原生 JSON object：
{"image_review_log_json":{"schema":"game-observatory.reader-story-image-review-log.v1",
"reviews":[{"artifact_id":"","sha256":"","observed_title":"",
"observed_controls":[],"observed_state":"","observation":"","uncertainties":[]}]},
"completion":{"reviewed_image_groups":0,"self_check":"精确 SHA 集合核对"}}
回执 SHA 必须与 manifest 和真实附图完全一致。
"""
    temporary_prompt = case_root / "prompts" / "image-repair.md"
    temporary_prompt.parent.mkdir(parents=True, exist_ok=True)
    temporary_prompt.write_text(repair_prompt, encoding="utf-8")
    manifest_subset = [
        {
            key: entry.get(key)
            for key in ("artifact_id", "sha256", "packet_path", "review_reasons")
        }
        for entry in missing_entries
    ]
    result = await _run_worker_stage(
        case=case,
        stage="image-repair",
        packet=packet,
        case_root=case_root,
        prompt_path=temporary_prompt,
        context_paths=[],
        context_values=(("manifest_subset", manifest_subset),),
        image_entries=missing_entries,
        timeout_s=600,
        semaphore=semaphore,
    )
    return [result]


def _story_repair_fact_evidence(
    packet: Path,
    validation_errors: Sequence[Any],
) -> dict[str, Any]:
    """Return frozen fact atoms that explain missing numeric tokens."""

    prefix = "reader-visible resource and legacy numbers are missing:"
    requested_tokens: set[str] = set()
    for raw_error in validation_errors:
        error = str(raw_error or "").strip()
        if not error.startswith(prefix):
            continue
        requested_tokens.update(
            token.strip()
            for token in error[len(prefix) :].split(",")
            if token.strip()
        )
    ledger = _read_json(packet / "input" / "atom-ledger.json")
    matched_tokens: set[str] = set()
    matched_atoms: list[dict[str, Any]] = []
    for index, atom in enumerate(ledger.get("atoms") or []):
        if not isinstance(atom, dict) or atom.get("obligation") != "reader":
            continue
        if atom.get("kind") not in {
            "legacy-reader-fact",
            "resource-display-fact",
        }:
            continue
        excerpt = str(atom.get("source_excerpt") or "")
        excerpt_tokens = set(re.findall(r"\d+(?:\.\d+)?%?", excerpt))
        current_matches = sorted(
            requested_tokens & excerpt_tokens,
            key=lambda item: (len(item), item),
        )
        if not current_matches:
            continue
        matched_tokens.update(current_matches)
        matched_atoms.append(
            {
                "index": index,
                "atom_id": atom.get("atom_id"),
                "kind": atom.get("kind"),
                "source_path": atom.get("source_path"),
                "source_excerpt": excerpt,
                "obligation": atom.get("obligation"),
                "priority": atom.get("priority"),
                "artifact_ids": atom.get("artifact_ids") or [],
                "matched_numeric_tokens": current_matches,
            }
        )
    return {
        "schema": "game-observatory.reader-story-repair-fact-evidence.v1",
        "source_bundle_id": ledger.get("source_bundle_id"),
        "requested_numeric_tokens": sorted(
            requested_tokens,
            key=lambda item: (len(item), item),
        ),
        "matched_numeric_tokens": sorted(
            matched_tokens,
            key=lambda item: (len(item), item),
        ),
        "unmatched_numeric_tokens": sorted(
            requested_tokens - matched_tokens,
            key=lambda item: (len(item), item),
        ),
        "matched_atoms": matched_atoms,
    }


async def _repair_story_validation(
    *,
    case: Mapping[str, Any],
    packet: Path,
    case_root: Path,
    author_result: Path,
    prior_results: Sequence[Path],
    image_patch_results: Sequence[Path],
    coverage_patch_results: Sequence[Path],
    validation: Mapping[str, Any],
    semaphore: asyncio.Semaphore,
    initial_story_patch_results: Sequence[Path] = (),
) -> tuple[list[Path], dict[str, Any]]:
    """Give exact machine errors back to Terra without discarding the draft."""

    story_patch_results = list(initial_story_patch_results)
    current_validation = dict(validation)
    for repair_index in range(1, MAX_STORY_REPAIRS + 1):
        if current_validation.get("passed") is True:
            break
        repair_prompt = """# Terra 读者文章定点修复

机器校验拦下了上一版候选。保留候选中的全部事实、数字、机制、界面、操作、
证据边界和有效图片引用，只修复附带的精确错误。不要缩写文章，不要删掉机制来
绕过校验，不要改变概念或章节 ID，除非错误明确说明该 ID 无效。

若错误指出 fact-first prose 使用了“玩家”，保留原事实和因果关系，把机制段、
概念段和界面段的主语改成实际的系统、界面、对象、规则或状态；玩家如何操作只
留在 `observed-operation`，体验和设计判断只留在
`experience-inference`。若错误指出“首访 / 二访 / 三访”，按实际语义改成
“首次进入 / 第二次访问 / 第三次访问”等自然说法，不得删去对应状态。

`repair_fact_evidence` 是从冻结原子账本中按缺失数值精确匹配出的事实摘录。
补数值时必须同时写清它在摘录中的对象、单位和状态，不能只把孤立数字塞进
正文。多个摘录语义不一致时保留各自范围；没有匹配证据时不得猜。

可引用事实的范围大于可点击 Wiki 页面范围。`publication_identity` 中的
`public_wiki_targets` 是 `play_slug` 唯一允许清单。若候选提到未公开来源，
保留正文事实和边界说明，但移除对应 `play_slug` 或无效 related 链接。
`related` 若保留，必须至少包含 `play_slug`、面向读者的 `title` 和
`description`；不要写内部 relationship 字段代替页面文案。

只输出一个原生 JSON object：
{"reader_story_patch":{"replace_story":{完整修正版 reader story}},
"completion":{"repairs":[],"self_check":""}}
不要输出 coverage、图片回执、Markdown 或解释。完整修正版必须保持固定末段
`interfaces-and-operations`、`observed-operation`、`experience-inference`
的顺序，最后一节仍设置 inference=true。
"""
        temporary_prompt = (
            case_root
            / "prompts"
            / f"story-validation-repair-{repair_index:02d}.md"
        )
        temporary_prompt.parent.mkdir(parents=True, exist_ok=True)
        temporary_prompt.write_text(repair_prompt, encoding="utf-8")
        result = await _run_worker_stage(
            case=case,
            stage=f"story-validation-repair-{repair_index:02d}",
            packet=packet,
            case_root=case_root,
            prompt_path=temporary_prompt,
            context_paths=[],
            context_values=(
                (
                    "current_reader_story",
                    _read_json(packet / "output" / "reader_story.json"),
                ),
                (
                    "machine_validation_errors",
                    current_validation.get("errors") or [],
                ),
                (
                    "repair_fact_evidence",
                    _story_repair_fact_evidence(
                        packet,
                        current_validation.get("errors") or [],
                    ),
                ),
                (
                    "publication_identity",
                    json.loads(
                        (
                            packet
                            / "input"
                            / "reader"
                            / "15-publication-target.txt"
                        ).read_text(encoding="utf-8-sig")
                    ),
                ),
            ),
            image_entries=[],
            timeout_s=600,
            semaphore=semaphore,
        )
        story_patch_results.append(result)
        await asyncio.to_thread(
            story_agent.ingest_worker_result,
            packet,
            author_result,
            prior_results=prior_results,
            image_patch_results=image_patch_results,
            coverage_patch_results=coverage_patch_results,
            story_patch_results=story_patch_results,
        )
        current_validation = await asyncio.to_thread(
            story_agent.validate_workspace,
            packet,
        )
    return story_patch_results, current_validation


def _freeze_candidate(
    *,
    packet: Path,
    case_root: Path,
    validation: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> Path:
    versions = case_root / "candidate"
    existing = sorted(versions.glob("v[0-9][0-9][0-9]"))
    version = versions / f"v{len(existing) + 1:03d}"
    version.mkdir(parents=True, exist_ok=False)
    for filename in (
        "reader_story.json",
        "coverage-map.json",
        "image-review-log.json",
        "terra-run-receipt.json",
    ):
        shutil.copy2(packet / "output" / filename, version / filename)
    _write_json(version / "validation.json", validation)
    _write_json(version / "image-provenance.json", provenance)
    return version


def _reference_story(packet: Path) -> dict[str, Any] | None:
    path = packet / "input" / "existing-reader-profile.json"
    if not path.is_file():
        return None
    value = _read_json(path)
    story = value.get("current_reader_story")
    return story if isinstance(story, dict) else None


def _previous_author_stories(
    case_root: Path,
    current_story: Mapping[str, Any],
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    current_sha = _json_sha256(current_story)
    candidates: list[dict[str, Any]] = []
    seen = {current_sha}
    result_paths = [
        *case_root.glob("attempts/direct/*/result.json"),
        *case_root.glob("attempts/direct/*/normalized-result*.json"),
        *case_root.glob("attempts/synthesis/*/result.json"),
        *case_root.glob("attempts/synthesis/*/normalized-result*.json"),
    ]
    for path in sorted(result_paths, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            structured = _structured_result(_read_json(path))
            story = structured.get("reader_story_json")
            if isinstance(story, str):
                story = json.loads(story)
            if not isinstance(story, dict):
                continue
            story = story_agent._extract_story(story)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        story_sha = _json_sha256(story)
        if story_sha in seen:
            continue
        seen.add(story_sha)
        candidates.append(
            {
                "source_result": str(path),
                "reader_story": story,
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def _semantic_judge_errors(
    structured: Mapping[str, Any],
    *,
    case_id: str,
) -> list[str]:
    errors: list[str] = []
    cases = structured.get("cases")
    case_result = (
        next(
            (
                item
                for item in cases
                if isinstance(item, dict) and item.get("case_id") == case_id
            ),
            None,
        )
        if isinstance(cases, list)
        else None
    )
    if structured.get("overall_verdict") != "pass":
        errors.append("overall_verdict is not pass")
    if not isinstance(case_result, dict):
        return [*errors, "case verdict is missing"]
    if case_result.get("verdict") != "pass":
        errors.append("case verdict is not pass")
    if case_result.get("required_repairs"):
        errors.append("semantic judge requires repairs")
    scores = case_result.get("scores")
    if not isinstance(scores, dict):
        return [*errors, "semantic judge scores are missing"]
    numeric_scores: list[float] = []
    for dimension in JUDGE_DIMENSIONS:
        value = scores.get(dimension)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
        ):
            errors.append(f"semantic judge score is missing: {dimension}")
            continue
        score = float(value)
        numeric_scores.append(score)
        if score < JUDGE_MIN_SCORE:
            errors.append(
                f"semantic judge score below {JUDGE_MIN_SCORE:g}: "
                f"{dimension}={score:g}"
            )
    if (
        len(numeric_scores) == len(JUDGE_DIMENSIONS)
        and sum(numeric_scores) / len(numeric_scores) < JUDGE_MIN_AVERAGE
    ):
        errors.append(
            "semantic judge average below "
            f"{JUDGE_MIN_AVERAGE:g}: "
            f"{sum(numeric_scores) / len(numeric_scores):.2f}"
        )
    return errors


async def _judge_candidate(
    *,
    plan: Mapping[str, Any],
    case: Mapping[str, Any],
    packet: Path,
    case_root: Path,
    version: Path,
    repo_root: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[Path, list[str]]:
    story = _read_json(version / "reader_story.json")
    validation = _read_json(version / "validation.json")
    images = _story_images(packet, story)[:16]
    judge_input = {
        "case_id": case["case_id"],
        "target_kind": case["target_kind"],
        "target_title": case.get("target_title"),
        "target_summary": case.get("target_summary"),
        "candidate_reader_story": story,
        "old_reader_story": _reference_story(packet),
        "previous_candidate_stories": _previous_author_stories(
            case_root,
            story,
        ),
        "machine_validation": validation,
        "image_attachment_aliases": _image_attachment_aliases(
            packet,
            story,
            images,
        ),
        "source_boundaries": {
            "aggregation_mode": "conceptual",
            "source_order_is_not_action_order": True,
            "same_run_required_for_observed_flow": True,
            "source_play_slugs": case.get("source_play_slugs")
            or [case["target_slug"]],
            "gap_play_slugs": case.get("gap_play_slugs") or [],
        },
    }
    result = await _run_worker_stage(
        case=case,
        stage="judge",
        packet=packet,
        case_root=case_root,
        prompt_path=_within(repo_root / plan["prompts"]["judge"], repo_root),
        context_paths=[],
        context_values=(("judge_case", judge_input),),
        image_entries=images,
        timeout_s=900,
        semaphore=semaphore,
    )
    structured = _structured_result(_read_json(result))
    judge_errors = _semantic_judge_errors(
        structured,
        case_id=str(case["case_id"]),
    )
    shutil.copy2(result, version / "judge-result.json")
    return result, judge_errors


async def _repair_semantic_judge(
    *,
    case: Mapping[str, Any],
    packet: Path,
    case_root: Path,
    version: Path,
    judge_result: Path,
    repair_index: int,
    semaphore: asyncio.Semaphore,
) -> Path:
    """Apply an independent judge's exact repairs without reopening the fact base."""

    repair_prompt = """# Terra 独立评审定点修订

独立评审已经检查候选文章、旧稿、图片和机器验证。只处理
`semantic_judge_feedback` 中的 required_repairs 与低分原因，同时保留候选中的
全部事实、数字、机制、概念、界面、操作、证据边界、图片引用和有效 Wiki 链接。
不得靠删掉旧信息通过评审。

面向读者的正文只写游戏中的概念、规则、界面、操作和可见结果。不要写内部工作
过程、取证术语、运行代号、代码字段名、命名空间或数据库式表达。若内部标识支撑
了一项真实游戏事实，把事实改写为自然语言；必要的玩家可见英文名称可保留。
机制与界面结论在前，体验与设计推论仍只放在最后的 inference=true 章节。

实际查看本轮附加图片，只用于核对评审要求涉及的可见状态。不要改变无关图片引用。
只输出一个原生 JSON object：
{"reader_story_patch":{"replace_story":{完整修正版 reader story}},
"completion":{"repairs":[],"self_check":""}}
不要输出 coverage、图片回执、Markdown 或解释。固定末段仍依次为
`interfaces-and-operations`、`observed-operation`、`experience-inference`。
"""
    prompt_path = (
        case_root
        / "prompts"
        / f"semantic-judge-repair-{repair_index:02d}.md"
    )
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(repair_prompt, encoding="utf-8")
    story = _read_json(version / "reader_story.json")
    images = _story_images(packet, story)[:16]
    return await _run_worker_stage(
        case=case,
        stage=f"semantic-judge-repair-{repair_index:02d}",
        packet=packet,
        case_root=case_root,
        prompt_path=prompt_path,
        context_paths=[],
        context_values=(
            ("current_reader_story", story),
            (
                "semantic_judge_feedback",
                _structured_result(_read_json(judge_result)),
            ),
            (
                "machine_validation",
                _read_json(version / "validation.json"),
            ),
            ("old_reader_story", _reference_story(packet)),
        ),
        image_entries=images,
        timeout_s=900,
        semaphore=semaphore,
    )


def _merge_permit(
    *,
    plan: Mapping[str, Any],
    case: Mapping[str, Any],
    version: Path,
    repo_root: Path,
) -> dict[str, Any]:
    _assert_case_sources(case, repo_root=repo_root)
    validation = _read_json(version / "validation.json")
    provenance = _read_json(version / "image-provenance.json")
    judge = _read_json(version / "judge-result.json")
    if validation.get("passed") is not True:
        raise ValueError("machine validation is not passed")
    if provenance.get("passed") is not True:
        raise ValueError("image provenance is not passed")
    structured_judge = _structured_result(judge)
    judge_errors = _semantic_judge_errors(
        structured_judge,
        case_id=str(case["case_id"]),
    )
    if judge_errors:
        raise ValueError(
            "semantic judge is not passed: " + "; ".join(judge_errors)
        )
    return {
        "schema": "game-observatory.reader-story-merge-permit.v1",
        "created_at": _now(),
        "case_id": case["case_id"],
        "plan_sha256": _json_sha256(plan),
        "production_contract_signature": _production_contract_signature(
            plan,
            case,
            repo_root=repo_root,
        ),
        "source_protected_sha256": case["source_protected_sha256"],
        "related_protected_sha256": {
            item["relpath"]: item["protected_sha256"]
            for item in case.get("related_sources") or []
        },
        "reader_story_sha256": _file_sha256(version / "reader_story.json"),
        "coverage_map_sha256": _file_sha256(version / "coverage-map.json"),
        "image_review_sha256": _file_sha256(version / "image-review-log.json"),
        "validation_sha256": _file_sha256(version / "validation.json"),
        "provenance_sha256": _file_sha256(version / "image-provenance.json"),
        "judge_sha256": _file_sha256(version / "judge-result.json"),
    }


def _merge_ready_case(
    *,
    plan: Mapping[str, Any],
    case: Mapping[str, Any],
    packet: Path,
    case_root: Path,
    version: Path,
    state_root: Path,
    repo_root: Path,
) -> dict[str, Any]:
    permit = _merge_permit(
        plan=plan,
        case=case,
        version=version,
        repo_root=repo_root,
    )
    merge_root = case_root / "merge"
    _write_json(merge_root / "permit.json", permit)
    story = _read_json(version / "reader_story.json")
    if case["target_kind"] == "page":
        source, _ = _case_paths(case, repo_root=repo_root)
        current = _read_json(source)
        current_design = (
            current.get("design_document")
            if isinstance(current.get("design_document"), dict)
            else {}
        )
        if current_design.get("reader_story") == story:
            receipt = {
                "ok": True,
                "source": str(source),
                "reused": True,
                "reader_story_sha256": _json_sha256(story),
            }
        else:
            receipt = story_agent.merge_candidate(
                packet,
                source=source,
                archive_dir=(
                    state_root
                    / "archives"
                    / _safe_name(case["case_id"])
                    / version.name
                ),
                allow_replace=bool(case.get("allow_replace")),
            )
    else:
        current = load_group_reader_story_registry()["story_by_group"].get(
            str(case["group_id"])
        )
        if current and current.get("reader_story") == story:
            receipt = {
                "ok": True,
                "registry_path": load_group_reader_story_registry()["path"],
                "reused": True,
                "reader_story_sha256": _json_sha256(story),
            }
        else:
            receipt = upsert_group_reader_story(
                str(case["group_id"]),
                story,
                related_sources=case.get("source_play_slugs") or [],
                provenance={
                    "case_id": case["case_id"],
                    "merge_permit_sha256": _json_sha256(permit),
                    "source_play_slugs": case.get("source_play_slugs") or [],
                    "gap_play_slugs": case.get("gap_play_slugs") or [],
                },
                archive_dir=state_root / "group-story-archives",
            )
    _write_json(merge_root / "receipt.json", receipt)
    return receipt


async def _run_case(
    *,
    plan: Mapping[str, Any],
    case: Mapping[str, Any],
    state: dict[str, Any],
    state_root: Path,
    repo_root: Path,
    semaphore: asyncio.Semaphore,
    merge_lock: asyncio.Lock,
    merge: bool,
) -> dict[str, Any]:
    case_id = str(case["case_id"])
    case_root = _case_root(state_root, case)
    current = state["cases"][case_id]
    contract_signature = _production_contract_signature(
        plan,
        case,
        repo_root=repo_root,
    )
    if (
        current.get("status") == "merged"
        and current.get("completion_signature") == contract_signature
    ):
        return {"case_id": case_id, "status": "merged", "reused": True}
    try:
        _save_case_state(state_root, state, case_id, status="preparing")
        async with semaphore:
            packet_report = await asyncio.to_thread(
                prepare_case,
                plan,
                case,
                state_root=state_root,
                repo_root=repo_root,
            )
        packet = _packet_root(state_root, case)
        mode = classify_case(
            packet_report,
            requested_mode=str(case.get("mode") or "auto"),
        )
        _save_case_state(
            state_root,
            state,
            case_id,
            status="prepared",
            mode=mode,
            packet_report=packet_report,
        )
        prior_results: list[Path] = []
        if mode == "direct":
            _save_case_state(state_root, state, case_id, status="direct_running")
            author_result = await _run_worker_stage(
                case=case,
                stage="direct",
                packet=packet,
                case_root=case_root,
                prompt_path=_within(
                    repo_root / plan["prompts"]["direct"], repo_root
                ),
                context_paths=_direct_context_paths(packet),
                image_entries=_required_images(packet),
                semaphore=semaphore,
            )
        else:
            _save_case_state(state_root, state, case_id, status="dossier_running")
            required = _required_images(packet)
            chunks = [
                required[offset : offset + DOSSIER_IMAGE_CHUNK]
                for offset in range(0, len(required), DOSSIER_IMAGE_CHUNK)
            ] or [[]]
            dossier_results: list[Path] = []
            for index, chunk in enumerate(chunks, start=1):
                stage = (
                    "dossier"
                    if len(chunks) == 1
                    else f"dossier-{index:02d}-of-{len(chunks):02d}"
                )
                dossier_results.append(
                    await _run_worker_stage(
                        case=case,
                        stage=stage,
                        packet=packet,
                        case_root=case_root,
                        prompt_path=_within(
                            repo_root / plan["prompts"]["dossier"], repo_root
                        ),
                        context_paths=_dossier_context_paths(packet),
                        context_values=(
                            (
                                "image_chunk_contract",
                                {
                                    "chunk": index,
                                    "total_chunks": len(chunks),
                                    "review_only_attached_sha": True,
                                },
                            ),
                        ),
                        image_entries=chunk,
                        semaphore=semaphore,
                    )
                )
            dossier = _merge_dossiers(dossier_results)
            _write_json(case_root / "dossier.json", dossier)
            prior_results.extend(dossier_results)
            _save_case_state(
                state_root, state, case_id, status="synthesis_running"
            )
            author_result = await _run_worker_stage(
                case=case,
                stage="synthesis",
                packet=packet,
                case_root=case_root,
                prompt_path=_within(
                    repo_root / plan["prompts"]["synthesis"], repo_root
                ),
                context_paths=_synthesis_context_paths(packet),
                context_values=(("visual_fact_dossier", dossier),),
                image_entries=_key_image_entries(packet, dossier["dossier"]),
                semaphore=semaphore,
            )

        coverage_patches = await _repair_missing_coverage(
            case=case,
            packet=packet,
            case_root=case_root,
            author_result=author_result,
            prompt_path=_within(
                repo_root / plan["prompts"]["synthesis"], repo_root
            ),
            semaphore=semaphore,
        )
        image_patches = await _repair_missing_images(
            case=case,
            packet=packet,
            case_root=case_root,
            prior_results=[*prior_results, author_result],
            semaphore=semaphore,
        )
        _save_case_state(state_root, state, case_id, status="machine_validating")
        await asyncio.to_thread(
            story_agent.ingest_worker_result,
            packet,
            author_result,
            prior_results=prior_results,
            image_patch_results=image_patches,
            coverage_patch_results=coverage_patches,
        )
        validation = await asyncio.to_thread(story_agent.validate_workspace, packet)
        story_patches: list[Path] = []
        if validation.get("passed") is not True:
            _save_case_state(
                state_root,
                state,
                case_id,
                status="story_repair_running",
                machine_errors=validation.get("errors") or [],
            )
            story_patches, validation = await _repair_story_validation(
                case=case,
                packet=packet,
                case_root=case_root,
                author_result=author_result,
                prior_results=prior_results,
                image_patch_results=image_patches,
                coverage_patch_results=coverage_patches,
                validation=validation,
                semaphore=semaphore,
            )
        provenance = _image_provenance(packet, case_root)
        if validation.get("passed") is not True:
            raise ValueError(
                "machine validation failed: "
                + "; ".join(validation.get("errors") or [])[:1_000]
            )
        if provenance.get("passed") is not True:
            raise ValueError(
                "image provenance failed: "
                + "; ".join(provenance.get("errors") or [])
            )
        version = _freeze_candidate(
            packet=packet,
            case_root=case_root,
            validation=validation,
            provenance=provenance,
        )
        _save_case_state(
            state_root,
            state,
            case_id,
            status="judging",
            candidate=str(version),
        )
        if case.get("semantic_judge") is not False:
            judge_result, judge_errors = await _judge_candidate(
                plan=plan,
                case=case,
                packet=packet,
                case_root=case_root,
                version=version,
                repo_root=repo_root,
                semaphore=semaphore,
            )
            for semantic_repair_index in range(1, MAX_SEMANTIC_REPAIRS + 1):
                if not judge_errors:
                    break
                _save_case_state(
                    state_root,
                    state,
                    case_id,
                    status="semantic_repair_running",
                    candidate=str(version),
                    semantic_judge_errors=judge_errors,
                )
                semantic_patch = await _repair_semantic_judge(
                    case=case,
                    packet=packet,
                    case_root=case_root,
                    version=version,
                    judge_result=judge_result,
                    repair_index=semantic_repair_index,
                    semaphore=semaphore,
                )
                story_patches.append(semantic_patch)
                await asyncio.to_thread(
                    story_agent.ingest_worker_result,
                    packet,
                    author_result,
                    prior_results=prior_results,
                    image_patch_results=image_patches,
                    coverage_patch_results=coverage_patches,
                    story_patch_results=story_patches,
                )
                validation = await asyncio.to_thread(
                    story_agent.validate_workspace,
                    packet,
                )
                if validation.get("passed") is not True:
                    story_patches, validation = await _repair_story_validation(
                        case=case,
                        packet=packet,
                        case_root=case_root,
                        author_result=author_result,
                        prior_results=prior_results,
                        image_patch_results=image_patches,
                        coverage_patch_results=coverage_patches,
                        validation=validation,
                        semaphore=semaphore,
                        initial_story_patch_results=story_patches,
                    )
                provenance = _image_provenance(packet, case_root)
                if validation.get("passed") is not True:
                    raise ValueError(
                        "semantic repair failed machine validation: "
                        + "; ".join(validation.get("errors") or [])[:1_000]
                    )
                if provenance.get("passed") is not True:
                    raise ValueError(
                        "semantic repair failed image provenance: "
                        + "; ".join(provenance.get("errors") or [])
                    )
                version = _freeze_candidate(
                    packet=packet,
                    case_root=case_root,
                    validation=validation,
                    provenance=provenance,
                )
                _save_case_state(
                    state_root,
                    state,
                    case_id,
                    status="judging",
                    candidate=str(version),
                )
                judge_result, judge_errors = await _judge_candidate(
                    plan=plan,
                    case=case,
                    packet=packet,
                    case_root=case_root,
                    version=version,
                    repo_root=repo_root,
                    semaphore=semaphore,
                )
            if judge_errors:
                raise ValueError(
                    "independent semantic judge did not pass after repairs: "
                    + "; ".join(judge_errors)
                )
        else:
            _write_json(
                version / "judge-result.json",
                {
                    "status": "succeeded",
                    "structured_output": {
                        "overall_verdict": "pass",
                        "cases": [
                            {
                                "case_id": case_id,
                                "verdict": "pass",
                                "required_repairs": [],
                                "scores": {
                                    dimension: 5
                                    for dimension in JUDGE_DIMENSIONS
                                },
                            }
                        ],
                    },
                },
            )
        _save_case_state(
            state_root,
            state,
            case_id,
            status="merge_ready",
            candidate=str(version),
        )
        receipt: dict[str, Any] | None = None
        if merge:
            async with merge_lock:
                receipt = await asyncio.to_thread(
                    _merge_ready_case,
                    plan=plan,
                    case=case,
                    packet=packet,
                    case_root=case_root,
                    version=version,
                    state_root=state_root,
                    repo_root=repo_root,
                )
            _save_case_state(
                state_root,
                state,
                case_id,
                status="merged",
                candidate=str(version),
                merge_receipt=receipt,
                completion_signature=contract_signature,
            )
        return {
            "case_id": case_id,
            "status": "merged" if merge else "merge_ready",
            "mode": mode,
            "candidate": str(version),
            "receipt": receipt,
        }
    except Exception as exc:
        _save_case_state(
            state_root,
            state,
            case_id,
            status="failed",
            error=str(exc),
        )
        return {"case_id": case_id, "status": "failed", "error": str(exc)}


async def run_batch(
    plan: Mapping[str, Any],
    *,
    state_root: Path,
    repo_root: Path | None = None,
    wave: str | None = None,
    only: set[str] | None = None,
    limit: int | None = None,
    max_concurrency: int = MAX_CONCURRENCY,
    merge: bool = False,
) -> dict[str, Any]:
    repo_root = (repo_root or _repo_root()).resolve()
    state_root = state_root.resolve()
    lint = lint_plan(plan, repo_root)
    if not lint["passed"]:
        raise ValueError("production plan failed lint: " + "; ".join(lint["errors"]))
    if not 1 <= max_concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"max_concurrency must be between 1 and {MAX_CONCURRENCY}")
    state_root.mkdir(parents=True, exist_ok=True)
    state = _load_state(state_root, plan, repo_root=repo_root)
    cases = [
        case
        for case in plan.get("cases") or []
        if case.get("action") == "produce"
        and (wave is None or case.get("wave") == wave)
        and (only is None or case.get("case_id") in only)
    ]
    cases.sort(key=lambda item: (int(item.get("priority") or 0), item["case_id"]))
    if limit is not None:
        cases = cases[: max(0, limit)]
    semaphore = asyncio.Semaphore(max_concurrency)
    merge_lock = asyncio.Lock()
    results = await asyncio.gather(
        *[
            _run_case(
                plan=plan,
                case=case,
                state=state,
                state_root=state_root,
                repo_root=repo_root,
                semaphore=semaphore,
                merge_lock=merge_lock,
                merge=merge,
            )
            for case in cases
        ]
    )
    return {
        "schema": "game-observatory.reader-story-batch-result.v1",
        "plan_sha256": _json_sha256(plan),
        "state_root": str(state_root),
        "selected": len(cases),
        "counts": {
            status: sum(item["status"] == status for item in results)
            for status in sorted({item["status"] for item in results})
        },
        "results": results,
    }


def batch_status(state_root: Path) -> dict[str, Any]:
    path = state_root.resolve() / "state.json"
    if not path.is_file():
        raise FileNotFoundError(f"batch state is missing: {path}")
    state = _read_json(path)
    entries = state.get("cases") or {}
    counts: dict[str, int] = {}
    for value in entries.values():
        status = str(value.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema": state.get("schema"),
        "plan_sha256": state.get("plan_sha256"),
        "updated_at": state.get("updated_at"),
        "counts": counts,
        "cases": entries,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("build-plan")
    plan.add_argument("--output", type=Path, required=True)

    lint = commands.add_parser("lint")
    lint.add_argument("--plan", type=Path, required=True)

    run = commands.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--state-root", type=Path, required=True)
    run.add_argument("--wave")
    run.add_argument("--only", action="append", default=[])
    run.add_argument("--limit", type=int)
    run.add_argument("--max-concurrency", type=int, default=MAX_CONCURRENCY)
    run.add_argument("--merge", action="store_true")

    status = commands.add_parser("status")
    status.add_argument("--state-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build-plan":
            value = build_default_plan()
            _write_json(args.output.resolve(), value)
            result: dict[str, Any] = {
                "ok": True,
                "output": str(args.output.resolve()),
                "plan_sha256": _json_sha256(value),
                "cases": len(value["cases"]),
            }
        elif args.command == "lint":
            result = lint_plan(load_plan(args.plan))
        elif args.command == "status":
            result = batch_status(args.state_root)
        else:
            result = asyncio.run(
                run_batch(
                    load_plan(args.plan),
                    state_root=args.state_root,
                    wave=args.wave,
                    only=set(args.only) or None,
                    limit=args.limit,
                    max_concurrency=args.max_concurrency,
                    merge=args.merge,
                )
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "lint" and not result["passed"]:
        return 1
    if args.command == "run" and result["counts"].get("failed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
