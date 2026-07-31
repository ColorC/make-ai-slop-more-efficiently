from __future__ import annotations

import hashlib

import pytest

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    SemanticStateV1,
    StateAssignmentV1,
    StateObservationFeaturesV1,
    TransitionEdgeV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.state_graph import (
    SemanticStateGraph,
)
from omnicompany.packages.domains.game_observatory.ai_player.state_recognition import (
    SemanticStateRecognizer,
    StateSplitPartitionV1,
    build_state_observation,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import NormalizedAction
from tests.domains.game_observatory.test_ai_player_store import (
    AFK_ENVIRONMENT,
    SANGUO_ENVIRONMENT,
    evidence,
    initialized_store,
)


def features(
    *,
    selected: str = "rowan",
    overlay: str | None = None,
    text: tuple[str, ...] = ("英雄", "等级 20"),
    screenshot: str = "0f0f0f0f0f0f0f0f",
    volatile: tuple[str, ...] = (),
) -> StateObservationFeaturesV1:
    critical = {"surface": "hero-detail", "tab": "growth"}
    if overlay is not None:
        critical["dialog"] = overlay
    return StateObservationFeaturesV1(
        screenshot_fingerprint=screenshot,
        ui_structure_tokens=["root:hero-detail", "panel:growth", "button:skill"],
        ui_text_tokens=list(text),
        runtime_tokens=["scene:hero", "panel-ready:true", *volatile],
        selected_object_tokens=[selected],
        overlay_tokens=[overlay] if overlay else [],
        region_fingerprints={"navigation": "nav-v1", "content": "hero-layout-v2"},
        critical_features=critical,
        volatile_tokens=list(volatile),
    )


def observation(
    environment_id: str,
    artifact_id: str,
    observation_id: str,
    value: StateObservationFeaturesV1,
):
    return build_state_observation(
        environment_id=environment_id,
        viewport_width=1920,
        viewport_height=1080,
        features=value,
        evidence_refs=[evidence(environment_id, artifact_id)],
        observation_id=observation_id,
    )


def test_recognition_ignores_declared_animation_and_survives_store_reopen(tmp_path):
    observatory, player = initialized_store(tmp_path)
    first = observation(
        AFK_ENVIRONMENT,
        "artifact.afk",
        "observation.afk.hero.1",
        features(volatile=("countdown:10",)),
    )
    first_result = SemanticStateRecognizer(player).recognize(first)
    assert first_result.disposition == "created_candidate"

    second = observation(
        AFK_ENVIRONMENT,
        "artifact.afk",
        "observation.afk.hero.2",
        features(
            screenshot="ffffffffffffffff",
            volatile=("countdown:09", "animation-frame:37"),
        ),
    )
    reopened = AIPlayerStore(observatory)
    second_result = SemanticStateRecognizer(reopened).recognize(second)

    assert reopened.schema_version == 3
    assert second.feature_hash == first.feature_hash
    assert second_result.state_id == first_result.state_id
    assert second_result.disposition == "recognized_existing"
    assert second_result.confidence == 1
    assert len(reopened.list_state_observations(AFK_ENVIRONMENT)) == 2
    assert len(reopened.list_state_assignments(AFK_ENVIRONMENT)) == 2


def test_popup_and_selected_object_are_critical_state_boundaries(tmp_path):
    _, player = initialized_store(tmp_path)
    recognizer = SemanticStateRecognizer(player, match_threshold=0.75)
    base = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.base",
            features(),
        )
    )
    popup = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.popup",
            features(overlay="hero-attributes"),
        )
    )
    selected = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.cecia",
            features(selected="cecia"),
        )
    )

    assert len({base.state_id, popup.state_id, selected.state_id}) == 3
    assert popup.ranked_matches[0].score == 0
    assert "__overlays__" in popup.ranked_matches[0].critical_conflicts
    assert selected.ranked_matches[0].score == 0
    assert "__selected_objects__" in selected.ranked_matches[0].critical_conflicts


def test_nearest_prototype_matches_small_noncritical_text_change(tmp_path):
    _, player = initialized_store(tmp_path)
    recognizer = SemanticStateRecognizer(player, match_threshold=0.80)
    first = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.prototype",
            features(),
        )
    )
    changed = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.variant",
            features(text=("英雄", "等级 21"), screenshot="0f0f0f0f0f0f0f1f"),
        )
    )

    assert changed.state_id == first.state_id
    assert changed.disposition == "recognized_existing"
    state = player.get_semantic_state(AFK_ENVIRONMENT, first.state_id)
    assert state is not None
    assert state.version == 2
    assert len(state.observation_feature_hashes) == 2


def test_same_features_never_cross_environment(tmp_path):
    _, player = initialized_store(tmp_path)
    recognizer = SemanticStateRecognizer(player)
    afk = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.afk",
            features(),
        )
    )
    sanguo = recognizer.recognize(
        observation(
            SANGUO_ENVIRONMENT,
            "artifact.sanguo",
            "observation.sanguo",
            features(),
        )
    )

    assert afk.environment_id != sanguo.environment_id
    assert player.get_semantic_state(AFK_ENVIRONMENT, sanguo.state_id) is None
    assert player.get_semantic_state(SANGUO_ENVIRONMENT, afk.state_id) is None


def test_feature_hash_tampering_is_rejected_before_assignment(tmp_path):
    _, player = initialized_store(tmp_path)
    item = observation(
        AFK_ENVIRONMENT,
        "artifact.afk",
        "observation.tampered",
        features(),
    ).model_copy(update={"feature_hash": "0" * 64})

    with pytest.raises(ValueError, match="feature hash"):
        SemanticStateRecognizer(player).recognize(item)
    assert not player.list_state_observations(AFK_ENVIRONMENT)


def test_adjudicated_merge_reassigns_observations_and_rejects_critical_conflict(tmp_path):
    _, player = initialized_store(tmp_path)
    recognizer = SemanticStateRecognizer(player, match_threshold=1.0)
    first = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.merge.1",
            features(text=("英雄", "攻击")),
        )
    )
    second = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.merge.2",
            features(text=("英雄", "攻击力")),
        )
    )
    assert first.state_id != second.state_id

    merged = recognizer.merge_states(
        AFK_ENVIRONMENT,
        first.state_id,
        [second.state_id],
        evidence_refs=[evidence(AFK_ENVIRONMENT, "artifact.afk")],
    )
    assert merged.status == "accepted"
    assignments = player.list_state_assignments(AFK_ENVIRONMENT, latest_only=True)
    assert {item.state_id for item in assignments} == {first.state_id}
    assert player.get_semantic_state(AFK_ENVIRONMENT, second.state_id).status == "superseded"

    popup = recognizer.recognize(
        observation(
            AFK_ENVIRONMENT,
            "artifact.afk",
            "observation.merge.popup",
            features(overlay="attributes"),
        )
    )
    with pytest.raises(ValueError, match="conflicting critical features"):
        recognizer.merge_states(
            AFK_ENVIRONMENT,
            first.state_id,
            [popup.state_id],
            evidence_refs=[evidence(AFK_ENVIRONMENT, "artifact.afk")],
        )


def test_adjudicated_split_requires_complete_disjoint_observation_partition(tmp_path):
    _, player = initialized_store(tmp_path)
    refs = [evidence(AFK_ENVIRONMENT, "artifact.afk")]
    base_observation = observation(
        AFK_ENVIRONMENT,
        "artifact.afk",
        "observation.split.base",
        features(),
    )
    popup_observation = observation(
        AFK_ENVIRONMENT,
        "artifact.afk",
        "observation.split.popup",
        features(overlay="attributes"),
    )
    player.append_state_observation(base_observation)
    player.append_state_observation(popup_observation)
    source = SemanticStateV1(
        id="state.mixed",
        environment_id=AFK_ENVIRONMENT,
        title="混合状态",
        description="待拆分的错误聚合状态",
        semantic_fingerprint=hashlib.sha256(b"mixed").hexdigest(),
        observation_feature_hashes=[base_observation.feature_hash, popup_observation.feature_hash],
        evidence_refs=refs,
    )
    player.put_semantic_state(source)
    for index, item in enumerate((base_observation, popup_observation), start=1):
        player.append_state_assignment(
            StateAssignmentV1(
                id=f"assignment.split.{index}.v1",
                environment_id=AFK_ENVIRONMENT,
                observation_id=item.id,
                state_id=source.id,
                method="new_candidate",
                confidence=0.5,
                reasons=["构造错误合并反例"],
                evidence_refs=refs,
            )
        )
    recognizer = SemanticStateRecognizer(player)
    children = recognizer.split_state(
        AFK_ENVIRONMENT,
        source.id,
        [
            StateSplitPartitionV1(
                state_id="state.hero.base",
                title="英雄详情",
                description="无浮层的英雄详情",
                observation_ids=(base_observation.id,),
            ),
            StateSplitPartitionV1(
                state_id="state.hero.attributes",
                title="英雄属性浮层",
                description="属性浮层打开的英雄详情",
                observation_ids=(popup_observation.id,),
            ),
        ],
        evidence_refs=refs,
    )

    assert {item.id for item in children} == {"state.hero.base", "state.hero.attributes"}
    assert player.get_semantic_state(AFK_ENVIRONMENT, source.id).status == "superseded"
    assignments = player.list_state_assignments(AFK_ENVIRONMENT, latest_only=True)
    assert {item.version for item in assignments} == {2}
    assert {item.state_id for item in assignments} == {item.id for item in children}


def test_state_graph_routes_only_through_verified_active_edges(tmp_path):
    _, player = initialized_store(tmp_path)
    refs = [evidence(AFK_ENVIRONMENT, "artifact.afk")]
    for state_id in ("state.a", "state.b", "state.c"):
        player.put_semantic_state(
            SemanticStateV1(
                id=state_id,
                environment_id=AFK_ENVIRONMENT,
                title=state_id,
                description=f"Fixture {state_id}",
                semantic_fingerprint=hashlib.sha256(state_id.encode()).hexdigest(),
                observation_feature_hashes=[hashlib.sha256(f"obs:{state_id}".encode()).hexdigest()],
                status="accepted",
                evidence_refs=refs,
            )
        )
    graph = SemanticStateGraph(player)

    def edge(edge_id: str, source: str, target: str, outcome: str):
        return TransitionEdgeV1(
            id=edge_id,
            environment_id=AFK_ENVIRONMENT,
            from_state_id=source,
            to_state_id=target,
            action=NormalizedAction(type="tap", x=100, y=100),
            expected_change=f"{source} to {target}",
            observed_change=f"{source} to {target}",
            outcome=outcome,
            evidence_refs=refs,
        )

    graph.put_edge(edge("edge.a-c.failed", "state.a", "state.c", "failed"))
    graph.put_edge(edge("edge.a-b", "state.a", "state.b", "verified_transition"))
    graph.put_edge(edge("edge.b-c", "state.b", "state.c", "verified_transition"))
    route = graph.shortest_verified_route(AFK_ENVIRONMENT, "state.a", "state.c")

    assert route.state_ids == ("state.a", "state.b", "state.c")
    assert route.edge_ids == ("edge.a-b", "edge.b-c")
    assert route.action_count == 2
    assert graph.reachable_state_ids(AFK_ENVIRONMENT, "state.a", max_actions=1) == (
        "state.a",
        "state.b",
    )
    with pytest.raises(LookupError):
        graph.shortest_verified_route(
            AFK_ENVIRONMENT,
            "state.a",
            "state.c",
            max_actions=1,
        )