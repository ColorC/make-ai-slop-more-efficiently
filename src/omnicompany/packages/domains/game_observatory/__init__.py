"""Public game-body and player-voice observatory.

The package is deliberately independent from the internal demogame design-reference
library.  It stores external/player-facing observations, source-backed reports,
device traces, and public projections behind a small set of contracts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime import GameObservatory

__all__ = ["GameObservatory"]


def __getattr__(name: str) -> Any:
    """Keep lightweight subpackages free of the full observatory runtime."""

    if name != "GameObservatory":
        raise AttributeError(name)
    from .runtime import GameObservatory

    return GameObservatory
