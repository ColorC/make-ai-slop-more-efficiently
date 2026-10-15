# [OMNI] origin=codex created_by=codex intent="enforce first-sight resource, notebook, and rehearsal boundaries"
"""First-sight content and rehearsal firewall for Arknights."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

AUTOPILOT_PART = "autopilot"
INVENTORY_NAME = "maa-semantic-inventory-v1.json"
ALLOWED_REHEARSAL_THEMES = frozenset({"Phantom", "Mizuki"})


class FirstSightFirewallError(ValueError):
    """A first-sight policy boundary was violated."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_omitted(relative: Path) -> bool:
    parts = relative.parts
    if INVENTORY_NAME in parts:
        return True
    try:
        index = parts.index("resource")
    except ValueError:
        return False
    tail = parts[index + 1 :]
    return len(tail) >= 3 and tail[0] == "roguelike" and tail[2] == AUTOPILOT_PART


def validate_first_sight_view(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    forbidden = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and _is_omitted(p.relative_to(root))]
    descendants = [p.relative_to(root).as_posix() for p in root.rglob("resource/roguelike/*/autopilot/**")]
    if forbidden or descendants:
        raise FirstSightFirewallError("first-sight view contains forbidden autopilot or inventory content")
    manifest_path = root / "first-sight-resource-manifest.json"
    if not manifest_path.is_file():
        raise FirstSightFirewallError("first-sight resource manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FirstSightFirewallError("first-sight resource manifest is invalid") from error
    hashes = manifest.get("source_sha256") if isinstance(manifest, dict) else None
    if (
        manifest.get("schema_version") != 1
        or manifest.get("policy") != "first-sight"
        or manifest.get("autopilot_descendants_present") is not False
        or manifest.get("inventory_excluded") is not True
        or not isinstance(hashes, dict)
        or manifest.get("copied_files") != len(hashes)
    ):
        raise FirstSightFirewallError("first-sight resource manifest contract is invalid")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != set(hashes):
        raise FirstSightFirewallError("first-sight resource view file set differs from manifest")
    for relative, expected in hashes.items():
        if not isinstance(relative, str) or not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
            raise FirstSightFirewallError("first-sight resource manifest hash entry is invalid")
        if _sha256(root / relative) != expected:
            raise FirstSightFirewallError("first-sight resource view hash differs from manifest")
    return manifest


def build_first_sight_resource_view(
    source_root: str | Path,
    destination: str | Path,
    *,
    theme: str | None = None,
    mode: str = "first-sight",
) -> dict[str, Any]:
    """Copy a vendor tree to a new empty destination, excluding answer-bearing assets."""
    source, target = Path(source_root), Path(destination)
    if not source.is_dir():
        raise FirstSightFirewallError(f"source root is not a directory: {source}")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FirstSightFirewallError("resource-view destination must be a new or empty directory")
    target.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    omitted = {"resource/roguelike/*/autopilot/", INVENTORY_NAME}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if _is_omitted(relative):
            continue
        destination_path = target / relative
        if path.is_dir():
            destination_path.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination_path)
            copied.append({"path": relative.as_posix(), "sha256": _sha256(path)})
    manifest = {
        "schema_version": 1,
        "policy": "first-sight",
        "source_root": str(source.resolve()),
        "source_sha256": {row["path"]: row["sha256"] for row in copied},
        "copied_files": len(copied),
        "omitted": sorted(omitted),
        "mode": mode,
        "theme": theme,
        "autopilot_descendants_present": False,
        "inventory_excluded": True,
    }
    (target / "first-sight-resource-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    validate_first_sight_view(target)
    return manifest


def review_familiarization_note(text: str, *, first_sight: bool, source_observation_visible: bool = False) -> dict[str, Any]:
    """Review without retaining a rejected hidden answer payload."""
    raw = str(text).strip()
    if not first_sight:
        return {"accepted": True, "text": raw, "policy": "normal"}
    mapping = re.compile(r"(?:event|encounter|事件|遭遇)[^\n]{0,100}(?:->|→|=>|选择|option|选项)[^\n]{0,100}(?:->|→|=>|结果|result)", re.I)
    explicit = re.search(r"\b(?:visible|shown|observed|source observation)\b|可见|已观察", raw, re.I)
    if mapping.search(raw) and not (source_observation_visible and explicit):
        evidence = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return {"accepted": False, "reason": "hidden_event_mapping", "evidence_sha256": evidence, "payload_retained": False}
    if explicit and source_observation_visible:
        return {"accepted": True, "text": raw, "label": "visible_in_source_observation", "policy": "first-sight"}
    return {"accepted": True, "text": raw, "policy": "first-sight"}


def enforce_rehearsal_theme(theme: str, *, first_sight: bool = True) -> str:
    if first_sight and theme not in ALLOWED_REHEARSAL_THEMES:
        raise FirstSightFirewallError("first-sight rehearsal theme must be Phantom or Mizuki")
    return theme


# Short aliases useful to callers integrating the firewall.
create_first_sight_resource_view = build_first_sight_resource_view
review_notebook_entry = review_familiarization_note
check_rehearsal_theme = enforce_rehearsal_theme
