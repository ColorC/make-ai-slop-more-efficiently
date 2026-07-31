# [OMNI] origin=codex domain=services/_core/team_builder ts=2026-07-25T00:00:00Z type=contract
# [OMNI] summary="Single authority for generated Team package, data, and import locations"
# [OMNI] why="Team Builder previously guessed flat services paths independently in several workers"
"""Canonical package-location contract for generated Teams.

This module owns only deployment location metadata.  It deliberately does not
define another Team, position, Agent, or routing model.
"""

from __future__ import annotations

import re


_ROOT_PARTS = ("src", "omnicompany", "packages")
_ALLOWED_SERVICE_BUCKETS = frozenset(
    {"_core", "_diagnosis", "_authoring", "_learning", "_utility"}
)
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_snake_case(value: str, *, field: str) -> None:
    if not _SNAKE_CASE.fullmatch(value):
        raise ValueError(f"{field} must be lower snake_case: {value!r}")


def canonical_team_package_path(value: object, *, team_name: str) -> str:
    """Validate and normalize one generated Team package directory.

    New shared facilities must live under a declared ``services`` bucket.
    Business-private Teams must live under one concrete ``domains`` namespace.
    Flat ``services/<team>`` paths, traversal, absolute paths, and extra nesting
    are rejected instead of being guessed or silently rewritten.
    """

    _validate_snake_case(team_name, field="team_name")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "target_package_path is required; choose an exact services bucket "
            "or business domain before generation"
        )
    raw = value.strip()
    if "\\" in raw:
        raise ValueError("target_package_path must use forward slashes")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("target_package_path must be repository-relative")

    parts = tuple(part for part in raw.rstrip("/").split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise ValueError("target_package_path cannot contain traversal segments")
    if parts[:3] != _ROOT_PARTS:
        raise ValueError(
            "target_package_path must start with "
            "'src/omnicompany/packages/'"
        )
    if len(parts) != 6:
        raise ValueError(
            "target_package_path must identify exactly one services bucket or "
            "one business domain plus the Team package"
        )

    area, namespace, package_name = parts[3:]
    if area == "services":
        if namespace not in _ALLOWED_SERVICE_BUCKETS:
            raise ValueError(
                "new service Team requires one canonical bucket: "
                f"{sorted(_ALLOWED_SERVICE_BUCKETS)}"
            )
    elif area == "domains":
        _validate_snake_case(namespace, field="domain namespace")
    else:
        raise ValueError(
            "target_package_path must be under packages/services or "
            "packages/domains"
        )

    if package_name != team_name:
        raise ValueError(
            "target_package_path final segment must equal team_name "
            f"({team_name!r})"
        )
    return "/".join(parts) + "/"


def team_data_path(target_package_path: str, *, team_name: str) -> str:
    """Derive the only writable data directory from a validated package path."""

    target = canonical_team_package_path(
        target_package_path,
        team_name=team_name,
    )
    parts = target.rstrip("/").split("/")
    return f"data/{parts[3]}/{parts[4]}/{parts[5]}/"


def team_python_module(target_package_path: str, *, team_name: str) -> str:
    """Derive the import package from a validated repository path."""

    target = canonical_team_package_path(
        target_package_path,
        team_name=team_name,
    )
    return target[len("src/") :].rstrip("/").replace("/", ".")


def team_omni_domain(target_package_path: str, *, team_name: str) -> str:
    """Derive the OmniMark domain prefix for generated package files."""

    target = canonical_team_package_path(
        target_package_path,
        team_name=team_name,
    )
    return target[len("src/omnicompany/packages/") :].rstrip("/")


def canonical_package_file_path(value: object) -> str:
    """Validate one file path relative to a generated Team package."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("generated file path must be a non-empty string")
    raw = value.strip()
    if "\\" in raw:
        raise ValueError("generated file path must use forward slashes")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("generated file path must be package-relative")
    raw_parts = raw.split("/")
    if any(not part for part in raw_parts):
        raise ValueError("generated file path cannot contain empty segments")
    parts = tuple(raw_parts)
    if any(part in {".", ".."} for part in parts):
        raise ValueError("generated file path cannot contain traversal segments")
    return "/".join(parts)


__all__ = [
    "canonical_package_file_path",
    "canonical_team_package_path",
    "team_data_path",
    "team_omni_domain",
    "team_python_module",
]
