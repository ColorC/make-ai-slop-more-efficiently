from __future__ import annotations

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    EvidenceReferenceV1,
    GuideKnowledgeV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.guide_research import (
    GuideDecisionContextV1,
    actionable_guides,
    assess_guide,
    should_refresh_guides,
)


ENVIRONMENT_ID = "environment.sanguo.prelogin"


def guide(*, status: str = "current", **updates) -> GuideKnowledgeV1:
    values = {
        "id": "guide.sanguo.s1-opening",
        "environment_id": ENVIRONMENT_ID,
        "evidence_refs": [
            EvidenceReferenceV1(
                environment_id=ENVIRONMENT_ID,
                source_ids=["res:sanguo-current-guide-research"],
            )
        ],
        "url": "https://example.com/sanguo-s1",
        "platform": "Example",
        "author": "Guide Author",
        "updated_at": "2026-07-15T08:00:00Z",
        "retrieved_at": "2026-07-15T09:00:00Z",
        "applicable_game_version": "1.31.0",
        "season": "S1",
        "server_stage": "opening-week",
        "summary": "Current S1 opening guidance.",
        "locators": ["section:opening"],
        "status": status,
    }
    values.update(updates)
    return GuideKnowledgeV1(**values)


def test_prelogin_guide_can_only_support_discovery_until_live_identity_is_known():
    assessment = assess_guide(guide(), GuideDecisionContextV1())

    assert assessment.mode == "discovery_only"
    assert assessment.reasons == ["live_environment_identity_is_incomplete"]


def test_only_exact_current_applicability_is_actionable():
    exact = GuideDecisionContextV1(
        game_version="1.31.0",
        season="S1",
        server_stage="opening-week",
    )
    wrong_season = exact.model_copy(update={"season": "赤壁惊涛"})

    assert assess_guide(guide(), exact).mode == "actionable"
    assert assess_guide(guide(), wrong_season).mode == "rejected"
    assert actionable_guides([guide()], exact) == [guide()]


def test_stale_unverified_and_contradicted_guides_cannot_drive_actions():
    context = GuideDecisionContextV1(
        game_version="1.31.0",
        season="S1",
        server_stage="opening-week",
    )
    stale = guide(status="stale", stale_reason="Older server rules")
    unverified = guide(
        status="unverified",
        applicable_game_version=None,
        season=None,
        server_stage=None,
        missing_applicability_reason="Source does not identify its environment.",
    )
    contradicted = guide(
        status="contradicted",
        contradiction_summary="The live interface shows a different rule.",
        live_contradiction_evidence_refs=[
            EvidenceReferenceV1(
                environment_id=ENVIRONMENT_ID,
                artifact_ids=["artifact.live-rule"],
            )
        ],
    )

    assert assess_guide(stale, context).mode == "discovery_only"
    assert assess_guide(unverified, context).mode == "discovery_only"
    assert assess_guide(contradicted, context).mode == "rejected"


def test_guide_refresh_triggers_cover_new_system_choices_drift_and_failure():
    assert should_refresh_guides("first_entry_to_new_system")
    assert should_refresh_guides("high_value_hard_to_reverse_choice")
    assert should_refresh_guides("version_or_season_change")
    assert should_refresh_guides("knowledge_expired")
    assert not should_refresh_guides("two_consecutive_failures", consecutive_failures=1)
    assert should_refresh_guides("two_consecutive_failures", consecutive_failures=2)