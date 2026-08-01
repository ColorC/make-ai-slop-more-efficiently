"""Runtime decisions for an independently operated AI game account."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import AccountActionPolicyV1


class AccountActionIntentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    game_internal: bool
    involves_real_money: bool = False
    submits_external_personal_identity: bool = False
    impersonates_real_person: bool = False

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("account action category must contain non-whitespace text")
        return normalized


class AccountActionDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    disposition: Literal["autonomous", "awaiting_authorization", "rejected"]
    reason: str
    authorization_action: Literal[
        "real_money_payment",
        "external_personal_identity_submission",
    ] | None = None


def evaluate_account_action(
    intent: AccountActionIntentV1,
    policy: AccountActionPolicyV1,
) -> AccountActionDecisionV1:
    if intent.impersonates_real_person:
        return AccountActionDecisionV1(
            intent_id=intent.id,
            disposition="rejected",
            reason="AI player identity cannot impersonate a real person.",
        )
    if intent.category == "real_money_payment" or intent.involves_real_money:
        return AccountActionDecisionV1(
            intent_id=intent.id,
            disposition="awaiting_authorization",
            reason="Real-money payment requires authorization for this action.",
            authorization_action="real_money_payment",
        )
    if (
        intent.category == "external_personal_identity_submission"
        or intent.submits_external_personal_identity
    ):
        return AccountActionDecisionV1(
            intent_id=intent.id,
            disposition="awaiting_authorization",
            reason="External personal identity submission requires authorization.",
            authorization_action="external_personal_identity_submission",
        )
    if intent.game_internal and policy.game_internal_action_default == "autonomous":
        return AccountActionDecisionV1(
            intent_id=intent.id,
            disposition="autonomous",
            reason="The independent AI player may execute game-internal actions autonomously.",
        )
    return AccountActionDecisionV1(
        intent_id=intent.id,
        disposition="rejected",
        reason="The action is outside the game-account operating scope.",
    )
