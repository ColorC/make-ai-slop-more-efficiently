# [OMNI] origin=codex domain=project_atlas ts=2026-10-04 type=retirement status=active
"""Direct-runtime retirement list for project-atlas skills."""

from __future__ import annotations

import json

from omnicompany.core.config import omni_workspace_root

RETIREMENT_FILE = omni_workspace_root() / "config" / "project_atlas" / "retired_direct_skills.json"


def retired_direct_skill_names() -> frozenset[str]:
    """Names kept in canonical/project lookup but excluded from direct export."""
    try:
        payload = json.loads(RETIREMENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    names = payload.get("names") if isinstance(payload, dict) else None
    if not isinstance(names, list):
        return frozenset()
    return frozenset(str(name).strip() for name in names if str(name).strip())


__all__ = ["RETIREMENT_FILE", "retired_direct_skill_names"]
