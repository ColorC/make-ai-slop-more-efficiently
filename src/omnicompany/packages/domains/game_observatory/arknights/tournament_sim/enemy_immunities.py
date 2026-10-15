# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""The control immunities an enemy ships with.

Ten flags live in ``enemy_database.json#/<id>/attributes``.  They are carried as
one contract rather than ten booleans on ``EnemyDefinition`` so that adding the
next control mechanism is a question of extending one map, not of finding every
place an enemy could be affected.

Counts across the whole shipped table, and within the 249 enemies IS3 actually
uses (the second figure is what matters for this line):

    silenceImmune         733 / 81     stunImmune            295 / 21
    levitateImmune        316 / 15     sleepImmune           293 / 15
    frozenImmune          274 / 15     fearedImmune          135 /  3
    attractImmune         103 /  3     disarmedCombatImmune   97 /  1
    palsyImmune            52 /  1     groundBoundImmune       5 /  0
    teleportImmune          0 /  0  -- shipped, never true anywhere

Only some of these correspond to a mechanism this engine can inflict.  The rest
are carried unread on purpose: the flag is then already in place on the day its
mechanism lands, which is the failure this whole family was registered against.
"""

from __future__ import annotations

from pydantic import Field

from .model_primitives import _Contract


class EnemyImmunities(_Contract):
    """Shipped immunity flags, named as the data names them."""

    stun: bool = Field(default=False)
    silence: bool = Field(default=False)
    sleep: bool = Field(default=False)
    frozen: bool = Field(default=False)
    levitate: bool = Field(default=False)
    feared: bool = Field(default=False)
    attract: bool = Field(default=False)
    palsy: bool = Field(default=False)
    disarmed_combat: bool = Field(default=False)
    ground_bound: bool = Field(default=False)
    teleport: bool = Field(default=False)


#: Shipped attribute name -> field on :class:`EnemyImmunities`.
IMMUNE_ATTRIBUTE_FIELDS = {
    "stunImmune": "stun",
    "silenceImmune": "silence",
    "sleepImmune": "sleep",
    "frozenImmune": "frozen",
    "levitateImmune": "levitate",
    "fearedImmune": "feared",
    "attractImmune": "attract",
    "palsyImmune": "palsy",
    "disarmedCombatImmune": "disarmed_combat",
    "groundBoundImmune": "ground_bound",
    "teleportImmune": "teleport",
}


#: Which immunity blocks which control this engine can inflict.  `BIND` is
#: absent because the shipped data carries no bindImmune -- there is nothing to
#: honour, and inventing a flag for it is exactly what this line forbids.
CONTROL_IMMUNITY_FIELDS = {
    "stun": "stun",
    "silence": "silence",
    "disarm": "disarmed_combat",
}


def enemy_immunities(attributes: dict) -> EnemyImmunities:
    """Read the immunity block off an enemy's ``attributes``.

    Absent is False, matching the data: the table writes both polarities
    explicitly, so a missing key means the enemy predates the flag.
    """

    return EnemyImmunities(
        **{
            field: bool(attributes.get(name))
            for name, field in IMMUNE_ATTRIBUTE_FIELDS.items()
        }
    )
