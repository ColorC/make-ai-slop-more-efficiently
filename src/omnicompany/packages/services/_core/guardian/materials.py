# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.material_declarations.compat.py"
"""Compatibility exports for Guardian patrol materials.

Format objects live in ``formats.py`` so Guardian's own OMNI-023 rule can
discover them from the standard protocol location. This module keeps the older
``guardian.materials`` import surface without declaring duplicate formats.
"""
from __future__ import annotations

from .formats import (
    GUARDIAN_FILE_CONTEXT_SET,
    GUARDIAN_PATROL_MATERIALS,
    GUARDIAN_SCAN_REQUEST,
    GUARDIAN_VIOLATION_SET,
    GUARDIAN_VIOLATION_SET_JUDGED,
)


ALL_MATERIALS = GUARDIAN_PATROL_MATERIALS


__all__ = [
    "GUARDIAN_SCAN_REQUEST",
    "GUARDIAN_FILE_CONTEXT_SET",
    "GUARDIAN_VIOLATION_SET",
    "GUARDIAN_VIOLATION_SET_JUDGED",
    "GUARDIAN_PATROL_MATERIALS",
    "ALL_MATERIALS",
]
