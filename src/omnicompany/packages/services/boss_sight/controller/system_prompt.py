# [OMNI] origin=codex domain=dashboard/boss-sight ts=2026-08-15T00:00:00Z type=config status=active
# [OMNI] material_id="material:dashboard.boss_sight.controller.system_prompt.py"
"""Load the native controller prompt without constructing an Agent runtime."""

from __future__ import annotations

import logging
from pathlib import Path


_log = logging.getLogger(__name__)
_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"


def load_controller_system_prompt() -> str:
    """Read the externally maintained prompt used by the Claude Code session."""
    if not _PROMPT_PATH.is_file():
        _log.error("controller system.md not found at %s", _PROMPT_PATH)
        return ""
    return _PROMPT_PATH.read_text(encoding="utf-8")


CONTROLLER_SYSTEM_PROMPT = load_controller_system_prompt()

__all__ = ["CONTROLLER_SYSTEM_PROMPT", "load_controller_system_prompt"]
