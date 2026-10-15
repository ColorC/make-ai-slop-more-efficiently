# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""The delayed blast a ``boom`` carrier leaves behind when it dies.

PRTS gives death explosions a **1 second fuse** (「延迟 1 秒」 on both IS3
carriers), so the blast outlives the enemy that produced it and cannot be
resolved inline at the moment of death -- the carrier has already been removed
from the board.  It therefore needs its own state, on the same footing as
``PollutedZoneState``.

Everything the blast needs is frozen at death time: where it goes off, how far
it reaches, how much it deals, and how long the cold it applies lasts.  Nothing
is looked up afterwards, so a blast resolves identically whether the battle ran
straight through or was snapshotted and restored mid-fuse.
"""

from __future__ import annotations

from pydantic import Field

from .model_primitives import _Contract


class PendingExplosionState(_Contract):
    instance_id: str = Field(min_length=1)
    source_enemy_id: str = Field(min_length=1)
    center_col_milli: int
    center_row_milli: int
    radius_milli_tiles: int = Field(gt=0, le=20_000)
    fuse_remaining_ticks: int = Field(gt=0, le=300)
    damage: int = Field(gt=0)
    #: Ticks of 敌方寒冷 the blast applies, or 0 when it applies none.
    cold_ticks: int = Field(default=0, ge=0)
