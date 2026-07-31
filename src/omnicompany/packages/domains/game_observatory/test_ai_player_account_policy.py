from __future__ import annotations

from omnicompany.packages.domains.game_observatory.ai_player.account_policy import (
    AccountActionIntentV1,
    evaluate_account_action,
)
from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    AccountActionPolicyV1,
    EvidenceReferenceV1,
)


ENVIRONMENT_ID = "environment.sanguo.local-mumu"


def policy() -> AccountActionPolicyV1:
    return AccountActionPolicyV1(
        id="account-policy.sanguo.1",
        ai_identity_label="三谋 AI 玩家",
        environment_id=ENVIRONMENT_ID,
        evidence_refs=[
            EvidenceReferenceV1(
                environment_id=ENVIRONMENT_ID,
                source_ids=["ai-player.acceptance.v1"],
            )
        ],
    )


def intent(index: int, **updates) -> AccountActionIntentV1:
    values = {
        "id": f"intent.{index}",
        "category": f"game-business-{index}",
        "summary": f"Execute game business action {index}",
        "game_internal": True,
    }
    values.update(updates)
    return AccountActionIntentV1(**values)


def test_any_current_or_future_game_internal_business_is_autonomous():
    decisions = [evaluate_account_action(intent(index), policy()) for index in range(498)]

    assert len(decisions) == 498
    assert all(item.disposition == "autonomous" for item in decisions)


def test_real_money_and_external_identity_complete_the_500_sample_policy_set():
    payment = evaluate_account_action(
        intent(498, involves_real_money=True),
        policy(),
    )
    identity = evaluate_account_action(
        intent(499, submits_external_personal_identity=True),
        policy(),
    )

    assert payment.disposition == "awaiting_authorization"
    assert payment.authorization_action == "real_money_payment"
    assert identity.disposition == "awaiting_authorization"
    assert identity.authorization_action == "external_personal_identity_submission"


def test_ai_account_cannot_impersonate_user_or_operate_outside_game_scope():
    impersonation = evaluate_account_action(
        intent(1, impersonates_real_person=True),
        policy(),
    )
    outside = evaluate_account_action(
        intent(2, game_internal=False),
        policy(),
    )

    assert impersonation.disposition == "rejected"
    assert outside.disposition == "rejected"