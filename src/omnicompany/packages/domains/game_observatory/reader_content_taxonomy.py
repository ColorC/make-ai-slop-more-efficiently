from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4


_SCHEMA = "game-observatory.reader-content-taxonomy.v1"
_MANIFEST_PATH = Path(__file__).with_name("reader_content_taxonomy.json")
_GROUP_KINDS = {"system", "collection"}
_UNGROUPED_LEVELS = {"journey", "play_loop"}
GROUP_READER_STORY_SCHEMA = "game-observatory.reader-system-stories.v1"
GROUP_READER_STORY_ARCHIVE_SCHEMA = "game-observatory.reader-system-story-archive.v1"
GROUP_READER_STORY_REGISTRY_PATH = (
    Path(__file__).resolve().parents[5]
    / "data"
    / "domains"
    / "game_observatory"
    / "reader-system-stories.v1.json"
)
GROUP_READER_STORY_ARCHIVE_DIR = (
    GROUP_READER_STORY_REGISTRY_PATH.parent / "reader-system-story-archives"
)


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"reader content taxonomy requires non-empty {field}")
    return text


def _reader_story(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    story = deepcopy(value)
    _require_text(story.get("title"), f"{field}.title")
    _require_text(story.get("summary"), f"{field}.summary")
    sections = story.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError(f"{field}.sections must be a non-empty list")
    return story


def _unique_text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    items = [_require_text(item, field) for item in value]
    if len(items) != len(set(items)):
        raise ValueError(f"{field} must not contain duplicates")
    return items


def _empty_group_reader_story_registry() -> dict[str, Any]:
    return {
        "schema": GROUP_READER_STORY_SCHEMA,
        "updated_at": None,
        "stories": [],
        "story_by_group": {},
    }


def load_group_reader_story_registry(
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(registry_path or GROUP_READER_STORY_REGISTRY_PATH)
    if not path.exists():
        registry = _empty_group_reader_story_registry()
        registry["path"] = str(path)
        return registry

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != GROUP_READER_STORY_SCHEMA:
        raise ValueError(
            f"group reader story registry schema must be {GROUP_READER_STORY_SCHEMA}"
        )
    stories = value.get("stories")
    if not isinstance(stories, list):
        raise ValueError("group reader story registry requires a stories list")

    clean_stories: list[dict[str, Any]] = []
    story_by_group: dict[str, dict[str, Any]] = {}
    for index, raw_story in enumerate(stories):
        if not isinstance(raw_story, dict):
            raise ValueError(f"group reader story {index} must be an object")
        group_id = _require_text(raw_story.get("group_id"), f"stories[{index}].group_id")
        if group_id in story_by_group:
            raise ValueError(f"duplicate group reader story {group_id}")
        game_id = _require_text(raw_story.get("game_id"), f"{group_id}.game_id")
        related_sources = _unique_text_list(
            raw_story.get("related_sources"),
            f"{group_id}.related_sources",
        )
        provenance = raw_story.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"{group_id}.provenance must be an object")
        clean_story = {
            "group_id": group_id,
            "game_id": game_id,
            "related_sources": related_sources,
            "reader_story": _reader_story(
                raw_story.get("reader_story"),
                f"{group_id}.reader_story",
            ),
            "provenance": deepcopy(provenance),
        }
        clean_stories.append(clean_story)
        story_by_group[group_id] = clean_story

    return {
        "schema": GROUP_READER_STORY_SCHEMA,
        "updated_at": value.get("updated_at"),
        "stories": clean_stories,
        "story_by_group": story_by_group,
        "path": str(path),
    }


@lru_cache(maxsize=1)
def load_reader_content_taxonomy() -> dict[str, Any]:
    value = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
        raise ValueError(f"reader content taxonomy schema must be {_SCHEMA}")

    labels = value.get("level_labels")
    groups = value.get("groups")
    entries = value.get("entries")
    if not isinstance(labels, dict) or not isinstance(groups, list) or not isinstance(entries, list):
        raise ValueError("reader content taxonomy requires level_labels, groups and entries")

    clean_labels = {
        _require_text(level, "level_labels key"): _require_text(label, f"label {level}")
        for level, label in labels.items()
    }
    group_by_id: dict[str, dict[str, Any]] = {}
    for raw_group in groups:
        if not isinstance(raw_group, dict):
            raise ValueError("reader content taxonomy groups must be objects")
        group = deepcopy(raw_group)
        group_id = _require_text(group.get("id"), "group.id")
        if group_id in group_by_id:
            raise ValueError(f"duplicate reader content group {group_id}")
        kind = _require_text(group.get("kind"), f"{group_id}.kind")
        if kind not in _GROUP_KINDS:
            raise ValueError(f"reader content group {group_id} has unsupported kind {kind}")
        for field in ("game_id", "slug", "title", "summary"):
            group[field] = _require_text(group.get(field), f"{group_id}.{field}")
        group["order"] = int(group.get("order") or 0)
        group_by_id[group_id] = group

    entry_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("reader content taxonomy entries must be objects")
        entry = deepcopy(raw_entry)
        game_id = _require_text(entry.get("game_id"), "entry.game_id")
        play_slug = _require_text(entry.get("play_slug"), "entry.play_slug")
        level = _require_text(entry.get("level"), f"{game_id}/{play_slug}.level")
        if level not in clean_labels or level == "unclassified":
            raise ValueError(
                f"reader content entry {game_id}/{play_slug} has unsupported level {level}"
            )
        key = (game_id, play_slug)
        if key in entry_by_key:
            raise ValueError(f"duplicate reader content entry {game_id}/{play_slug}")
        group_id = str(entry.get("group_id") or "").strip()
        if group_id:
            group = group_by_id.get(group_id)
            if group is None:
                raise ValueError(
                    f"reader content entry {game_id}/{play_slug} references unknown {group_id}"
                )
            if group["game_id"] != game_id:
                raise ValueError(
                    f"reader content entry {game_id}/{play_slug} crosses game group boundary"
                )
        elif level not in _UNGROUPED_LEVELS:
            raise ValueError(
                f"reader content entry {game_id}/{play_slug} requires a parent group"
            )
        entry["order"] = int(entry.get("order") or 0)
        entry_by_key[key] = entry

    for group_id, group in group_by_id.items():
        story_play_slug = str(group.get("story_play_slug") or "").strip()
        if not story_play_slug:
            continue
        story_entry = entry_by_key.get((group["game_id"], story_play_slug))
        if story_entry is None:
            raise ValueError(
                f"reader content group {group_id} references unknown story "
                f"{story_play_slug}"
            )
        if str(story_entry.get("group_id") or "") != group_id:
            raise ValueError(
                f"reader content group {group_id} story {story_play_slug} "
                "must belong to the same group"
            )
        group["story_play_slug"] = story_play_slug

    return {
        "schema": _SCHEMA,
        "level_labels": clean_labels,
        "groups": list(group_by_id.values()),
        "group_by_id": group_by_id,
        "entries": list(entry_by_key.values()),
        "entry_by_key": entry_by_key,
    }


def _group_with_current_reader_story(
    taxonomy: dict[str, Any],
    group_id: str,
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    group = deepcopy(taxonomy["group_by_id"].get(group_id) or {})
    if not group:
        return {}
    story_registry = registry or load_group_reader_story_registry()
    story_record = story_registry["story_by_group"].get(group_id)
    if story_record is None:
        return group
    if group["game_id"] != story_record["game_id"]:
        raise ValueError(f"group reader story {group_id} crosses game boundary")
    for play_slug in story_record["related_sources"]:
        source_entry = taxonomy["entry_by_key"].get(
            (group["game_id"], play_slug)
        )
        if (
            source_entry is None
            or str(source_entry.get("group_id") or "") != group_id
        ):
            raise ValueError(
                f"group reader story {group_id} source {play_slug} "
                "must belong to the same group"
            )
    group["reader_story"] = deepcopy(story_record["reader_story"])
    group["reader_story_meta"] = {
        "related_sources": list(story_record["related_sources"]),
    }
    return group


def reader_content_position(
    game_id: str,
    play_slug: str,
    *,
    content_kind: str = "play",
) -> dict[str, Any]:
    taxonomy = load_reader_content_taxonomy()
    entry = taxonomy["entry_by_key"].get((str(game_id), str(play_slug)))
    if entry is None:
        level = "journey" if content_kind == "journey" else "unclassified"
        return {
            "level": level,
            "label": taxonomy["level_labels"][level],
            "order": 0,
            "reader_visibility": "public",
            "group": {},
        }

    group_id = str(entry.get("group_id") or "")
    return {
        "level": entry["level"],
        "label": taxonomy["level_labels"][entry["level"]],
        "order": entry["order"],
        "reader_visibility": str(
            entry.get("reader_visibility") or "public"
        ),
        "group": _group_with_current_reader_story(taxonomy, group_id),
    }


def reader_content_taxonomy_manifest() -> dict[str, Any]:
    taxonomy = load_reader_content_taxonomy()
    registry = load_group_reader_story_registry()
    return {
        "schema": taxonomy["schema"],
        "level_labels": deepcopy(taxonomy["level_labels"]),
        "groups": [
            _group_with_current_reader_story(
                taxonomy,
                group["id"],
                registry=registry,
            )
            for group in taxonomy["groups"]
        ],
        "entries": deepcopy(taxonomy["entries"]),
    }


def _utc_timestamp() -> tuple[str, str]:
    current = datetime.now(timezone.utc)
    return (
        current.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        current.strftime("%Y%m%dT%H%M%S%fZ"),
    )


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def upsert_group_reader_story(
    group_id: str,
    reader_story: dict[str, Any],
    *,
    related_sources: list[str],
    provenance: dict[str, Any],
    registry_path: str | Path | None = None,
    archive_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Replace one group story without mutating its source partial bundles."""

    clean_group_id = _require_text(group_id, "group_id")
    clean_story = _reader_story(reader_story, f"{clean_group_id}.reader_story")
    clean_related_sources = _unique_text_list(
        related_sources,
        f"{clean_group_id}.related_sources",
    )
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError(f"{clean_group_id}.provenance must be a non-empty object")

    taxonomy = load_reader_content_taxonomy()
    group = taxonomy["group_by_id"].get(clean_group_id)
    if group is None:
        raise ValueError(f"unknown reader content group {clean_group_id}")
    for play_slug in clean_related_sources:
        source_entry = taxonomy["entry_by_key"].get((group["game_id"], play_slug))
        if source_entry is None or str(source_entry.get("group_id") or "") != clean_group_id:
            raise ValueError(
                f"group reader story {clean_group_id} source {play_slug} "
                "must belong to the same group"
            )

    target_path = Path(registry_path or GROUP_READER_STORY_REGISTRY_PATH)
    registry = load_group_reader_story_registry(target_path)
    previous = registry["story_by_group"].get(clean_group_id)
    updated_at, archive_stamp = _utc_timestamp()
    clean_provenance = deepcopy(provenance)
    previous_provenance = previous.get("provenance", {}) if previous else {}
    clean_provenance.setdefault(
        "created_at",
        previous_provenance.get("created_at") or updated_at,
    )
    clean_provenance["updated_at"] = updated_at
    entry = {
        "group_id": clean_group_id,
        "game_id": group["game_id"],
        "related_sources": clean_related_sources,
        "reader_story": clean_story,
        "provenance": clean_provenance,
    }

    archive_path: Path | None = None
    if previous is not None:
        resolved_archive_dir = Path(
            archive_dir
            or (
                GROUP_READER_STORY_ARCHIVE_DIR
                if target_path == GROUP_READER_STORY_REGISTRY_PATH
                else target_path.parent / "reader-system-story-archives"
            )
        )
        archive_slug = clean_group_id.replace(".", "_")
        archive_path = resolved_archive_dir / f"{archive_stamp}-{archive_slug}.json"
        _write_json_atomic(
            archive_path,
            {
                "schema": GROUP_READER_STORY_ARCHIVE_SCHEMA,
                "archived_at": updated_at,
                "registry_path": str(target_path),
                "story": previous,
            },
        )

    stories = [
        existing
        for existing in registry["stories"]
        if existing["group_id"] != clean_group_id
    ]
    stories.append(entry)
    stories.sort(key=lambda item: item["group_id"])
    _write_json_atomic(
        target_path,
        {
            "schema": GROUP_READER_STORY_SCHEMA,
            "updated_at": updated_at,
            "stories": stories,
        },
    )
    load_reader_content_taxonomy.cache_clear()
    return {
        "registry_path": str(target_path),
        "archive_path": str(archive_path) if archive_path else None,
        "entry": deepcopy(entry),
    }
