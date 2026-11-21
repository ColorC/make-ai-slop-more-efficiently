# [OMNI] origin=ai-ide ts=2026-05-31 type=infra
# [OMNI] material_id="material:omnicompany.core.caller_identity.py"
"""Caller identity constants shared by CLI access control and daemon sessions."""

from __future__ import annotations

CALLER_ENV = "OMNI_CLI_CALLER"

CALLER_EXTERNAL = "external"
CALLER_CONTROLLER = "controller"
CALLER_SUBAGENT = "subagent"

KNOWN_CALLERS = frozenset({CALLER_EXTERNAL, CALLER_CONTROLLER, CALLER_SUBAGENT})
DEFAULT_CALLER = CALLER_EXTERNAL


def normalize_caller(value: str | None) -> str:
    """Normalize an env/meta caller value to a known caller."""
    caller = (value or "").strip().lower()
    return caller if caller in KNOWN_CALLERS else DEFAULT_CALLER


__all__ = [
    "CALLER_ENV",
    "CALLER_EXTERNAL",
    "CALLER_CONTROLLER",
    "CALLER_SUBAGENT",
    "KNOWN_CALLERS",
    "DEFAULT_CALLER",
    "normalize_caller",
]
