# [OMNI] origin=arch-ratchet-surgery ts=2026-08-17 type=infra
# [OMNI] material_id="material:dashboard.controlplane.catalogue_common.py"
"""catalogue 与 teambuilder_materialization 共享的微型工具。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SKIP_PARTS = {"__pycache__", "node_modules", "_archive", "_graveyard", ".git"}



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _packages_root() -> Path:
    return Path(__file__).resolve().parents[2] / "packages"


def _is_skipped(p: Path) -> bool:
    return any(part in SKIP_PARTS for part in p.parts)


def _safe_text(value: Any, limit: int = 320) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _to_camel_identifier(value: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^0-9A-Za-z]+", value or "") if part)


def _to_snake_identifier(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", value or "").strip("_")
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"_+", "_", text).lower().strip("_")


def _doctor_level_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"blocking": 0, "degrading": 0, "advisory": 0, "info": 0}
    for finding in findings:
        level = str(finding.get("level") or "info")
        counts[level] = counts.get(level, 0) + 1
    return counts


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
