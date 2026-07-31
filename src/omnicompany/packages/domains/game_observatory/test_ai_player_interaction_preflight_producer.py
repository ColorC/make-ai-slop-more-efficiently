from __future__ import annotations

import hashlib

import cv2
import numpy as np

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.interaction_preflight import (
    InteractionExpectedChangeV1,
    validate_interaction_preflight,
)
from omnicompany.packages.domains.game_observatory.ai_player.interaction_preflight_producer import (
    InteractionPreflightProducer,
    InteractionPreflightProductionRequestV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    NormalizedAction,
    SourcePixelRect,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


ENVIRONMENT_ID = "environment.interaction-producer.fixture"
BOUNDS = SourcePixelRect(x=10, y=20, width=30, height=40)


def _seed(tmp_path):
    observatory = ObservatoryStore(tmp_path / "observatory")
    image = np.zeros((200, 100, 3), dtype=np.uint8)
    image[20:60, 10:40] = 220
    image_path = observatory.artifact_root / "source.png"
    assert cv2.imwrite(str(image_path), image)
    source = ArtifactRef(
        id="artifact.interaction-producer.source",
        kind="screenshot",
        path=str(image_path),
        sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
        metadata={"environment_id": ENVIRONMENT_ID},
    )
    observatory.save_artifact(source)
    player = AIPlayerStore(observatory)
    reference = EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=[source.id],
    )
    player.put_environment(
        EnvironmentScopeV1(
            id=ENVIRONMENT_ID,
            game_id="fixture-game",
            build_scope_id="build.fixture",
            account_scope_id="account.fixture",
            channel="fixture",
            device_scope_id="device.fixture",
            locale="zh-CN",
            viewport_width=100,
            viewport_height=200,
            identity_hash="identity-interaction-producer",
            evidence_refs=[reference],
        )
    )
    return observatory, player, source


def _locator(source: ArtifactRef, *, include_ocr: bool = True, interactive: bool = True):
    elements = [
        {
            "id": "candidate.cta",
            "type": "icon",
            "source_bounds": BOUNDS.model_dump(mode="json"),
            "interactivity": interactive,
            "interaction_candidate": True,
            "interactivity_source": "omniparser",
            "content": "button",
            "source": "box_yolo_content_yolo",
        }
    ]
    if include_ocr:
        elements.append(
            {
                "id": "ocr.cta",
                "type": "text",
                "source_bounds": {"x": 15, "y": 30, "width": 20, "height": 15},
                "interactivity": False,
                "interaction_candidate": False,
                "content": "前往聊天",
                "source": "easyocr",
            }
        )
    return {
        "schema": "game-observatory.visual-locator-run.v1",
        "image": {
            "sha256": source.sha256,
            "width": 100,
            "height": 200,
        },
        "elements": elements,
    }


def _request(source: ArtifactRef, locator, **updates):
    values = {
        "environment_id": ENVIRONMENT_ID,
        "source_artifact_id": source.id,
        "locator_result": locator,
        "candidate_id": "candidate.cta",
        "candidate_kind": "actionable_control",
        "recognition_observation_id": "observation.interaction.current",
        "captured_state_id": "state.interaction.current",
        "expected_change": InteractionExpectedChangeV1(kind="semantic_state_change"),
        "overlay_state": "none",
    }
    values.update(updates)
    return InteractionPreflightProductionRequestV1(**values)


def _ui_tree_artifact(observatory, *, selected: bool) -> ArtifactRef:
    path = observatory.artifact_root / f"ui-{selected}.xml"
    path.write_text(
        f'<hierarchy><node bounds="[10,20][40,60]" clickable="true" enabled="true" '
        f'selected="{str(selected).lower()}" /></hierarchy>',
        encoding="utf-8",
    )
    artifact = ArtifactRef(
        id=f"artifact.interaction-producer.ui.{selected}",
        kind="ui_tree",
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        metadata={"environment_id": ENVIRONMENT_ID},
    )
    observatory.save_artifact(artifact)
    return artifact


def test_omniparser_and_overlapping_ocr_produce_a_hashed_preflight(tmp_path):
    observatory, player, source = _seed(tmp_path)
    result = InteractionPreflightProducer(player).produce(
        _request(source, _locator(source))
    )

    assert result.disposition == "passed"
    assert result.preflight is not None
    assert result.interactivity_evidence == [
        "omniparser_interactivity",
        "ocr_label_match",
    ]
    local = observatory.get_artifact(result.local_evidence_artifact_id)
    assert local is not None
    assert local.metadata["source_artifact_id"] == source.id
    assert local.metadata["source_bounds"] == BOUNDS.model_dump(mode="json")
    validate_interaction_preflight(
        result.preflight,
        action=NormalizedAction(type="tap", x=20, y=30),
        target_bounds=BOUNDS,
        viewport_width=100,
        viewport_height=200,
        environment_id=ENVIRONMENT_ID,
        source_artifact=source,
        local_evidence_artifact=local,
    )


def test_single_visual_signal_and_unknown_overlay_are_fail_closed(tmp_path):
    _observatory, player, source = _seed(tmp_path)
    producer = InteractionPreflightProducer(player)

    single = producer.produce(_request(source, _locator(source, include_ocr=False)))
    unknown_overlay = producer.produce(
        _request(source, _locator(source), overlay_state="unknown")
    )

    assert single.disposition == "rejected"
    assert "两种独立视觉证据" in " ".join(single.reasons)
    assert unknown_overlay.disposition == "rejected"
    assert "遮罩层状态未知" in " ".join(unknown_overlay.reasons)


def test_active_overlay_blocks_background_candidate(tmp_path):
    _observatory, player, source = _seed(tmp_path)
    result = InteractionPreflightProducer(player).produce(
        _request(
            source,
            _locator(source),
            overlay_state="active",
            active_layer_interaction_bounds=SourcePixelRect(x=50, y=100, width=40, height=50),
        )
    )

    assert result.disposition == "rejected"
    assert "允许交互区域之外" in " ".join(result.reasons)


def test_ui_tree_action_can_confirm_navigation_and_selected_tab_is_rejected(tmp_path):
    observatory, player, source = _seed(tmp_path)
    producer = InteractionPreflightProducer(player)
    unselected = _ui_tree_artifact(observatory, selected=False)
    selected = _ui_tree_artifact(observatory, selected=True)
    locator = _locator(source, include_ocr=False, interactive=False)

    passed = producer.produce(
        _request(
            source,
            locator,
            candidate_kind="navigation",
            ui_tree_artifact_id=unselected.id,
        )
    )
    rejected = producer.produce(
        _request(
            source,
            locator,
            candidate_kind="navigation",
            ui_tree_artifact_id=selected.id,
        )
    )

    assert passed.disposition == "passed"
    assert passed.preflight is not None
    assert passed.preflight.selection_state == "unselected"
    assert passed.interactivity_evidence == ["ui_tree_action"]
    assert rejected.disposition == "rejected"
    assert "已经处于选中状态" in " ".join(rejected.reasons)


def test_locator_result_must_bind_the_current_source_hash(tmp_path):
    _observatory, player, source = _seed(tmp_path)
    locator = _locator(source)
    locator["image"]["sha256"] = "0" * 64

    result = InteractionPreflightProducer(player).produce(_request(source, locator))

    assert result.disposition == "rejected"
    assert result.local_evidence_artifact_id is None
    assert "没有绑定当前原图" in " ".join(result.reasons)