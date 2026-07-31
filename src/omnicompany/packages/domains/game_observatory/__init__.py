"""Public game-body and player-voice observatory.

The package is deliberately independent from the internal demogame design-reference
library.  It stores external/player-facing observations, source-backed reports,
device traces, and public projections behind a small set of contracts.
"""

from .runtime import GameObservatory

__all__ = ["GameObservatory"]