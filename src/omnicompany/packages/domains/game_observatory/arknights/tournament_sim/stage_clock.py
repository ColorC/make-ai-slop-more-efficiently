# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""The shipped wave clock: seconds to ticks, and the action types that matter.

Moved out of ``stage_compiler.py`` so the token reader can share them without
importing the compiler back (which would be a cycle).  ``stage_compiler``
re-exports both names, so importers are unchanged.
"""

from __future__ import annotations

from typing import Any

#: ``actionType`` of a wave action that actually puts an enemy on the field.
#: Every other action type is bookkeeping (random-group placeholders, previews)
#: and is preserved verbatim in ``source_waves`` without becoming a spawn.
SPAWN_ACTION_TYPE = 0


def to_ticks(seconds: Any, tps: int) -> int:
    """Convert shipped seconds to whole ticks, never below zero."""

    return max(0, round(float(seconds or 0) * tps))
