# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""The closed set of public actions a caller may submit to the engine.

Moved verbatim out of ``models.py`` so that file fits the 2000-line
budget; ``models.py`` re-exports every name, so importers are unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, StrictInt, model_validator

from .model_primitives import (
    ControlEffect,
    DeploymentClass,
    Direction,
    EffectRangeOrigin,
    EffectRangeRotation,
    EffectStat,
    EffectTargetSelector,
    GridPoint,
    SkillActivationPolicy,
    SkillSpEvent,
    SkillSpRecoveryMode,
    VisibilityClass,
    _Contract,
)


class DeployAction(_Contract):
    kind: Literal["deploy"] = "deploy"
    operator_id: str = Field(min_length=1)
    position: GridPoint
    direction: Direction = Direction.RIGHT


class RetreatAction(_Contract):
    kind: Literal["retreat"] = "retreat"
    instance_id: str = Field(min_length=1)


class ActivateSkillAction(_Contract):
    kind: Literal["activate_skill"] = "activate_skill"
    instance_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)


class SelectTargetAction(_Contract):
    kind: Literal["select_target"] = "select_target"
    instance_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    target_instance_id: str = Field(min_length=1)


class SetPausedAction(_Contract):
    kind: Literal["set_paused"] = "set_paused"
    paused: bool


class SetSpeedAction(_Contract):
    kind: Literal["set_speed"] = "set_speed"
    speed: Literal[1, 2]


class WaitAction(_Contract):
    kind: Literal["wait"] = "wait"
    ticks: int = Field(default=1, ge=1, le=300)
    cost_recovery_multiplier_milli: int = Field(default=1000, ge=0, le=1000)
    battle_progress_multiplier_milli: int = Field(default=1000, ge=0, le=1000)
    spawn_progress_multiplier_milli: int | None = Field(default=None, ge=0, le=1000)


PublicAction = Annotated[
    Union[
        DeployAction,
        RetreatAction,
        ActivateSkillAction,
        SelectTargetAction,
        SetPausedAction,
        SetSpeedAction,
        WaitAction,
    ],
    Field(discriminator="kind"),
]
