from __future__ import annotations

import hashlib
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from omnicompany.cli.commands import game as game_cli
from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    SkillLocatorV1,
    SkillStepV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.guarded_skill_step_adapter import (
    GuardedSkillStepAdapter,
)
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    EvidenceStep,
    NormalizedAction,
    SourcePixelRect,
)


def _save_rgb_artifact(facility, tmp_path, artifact_id: str, rgb: np.ndarray) -> ArtifactRef:
    path = tmp_path / f"{artifact_id}.png"
    Image.fromarray(rgb, mode="RGB").save(path)
    raw = path.read_bytes()
    artifact = ArtifactRef(
        id=artifact_id,
        kind="screenshot",
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        media_type="image/png",
        metadata={"semantic_state_eligible": True},
    )
    facility.store.save_artifact(artifact)
    return artifact


def _dynamic_fixture(tmp_path):
    context = game_cli._PlayerCLIContext(root=tmp_path / "root", as_json=True)
    facility = context.facility()
    rng = np.random.default_rng(817)
    patch = rng.integers(0, 256, size=(24, 32, 3), dtype=np.uint8)
    reference = np.full((180, 320, 3), 18, dtype=np.uint8)
    current = np.full((180, 320, 3), 18, dtype=np.uint8)
    reference[50:74, 40:72] = patch
    current[100:124, 210:242] = patch
    reference_artifact = _save_rgb_artifact(
        facility, tmp_path, "artifact.template.reference", reference
    )
    current_artifact = _save_rgb_artifact(
        facility, tmp_path, "artifact.template.current", current
    )
    source_step = EvidenceStep(
        id="evidence.step.template.current",
        evidence_run_id="evidence.run.template.current",
        step_index=1,
        status="passed",
        ended_at="2026-07-19T14:30:00+08:00",
        before_frame_id=current_artifact.id,
        after_frame_id=current_artifact.id,
        artifact_ids=[current_artifact.id],
        action=NormalizedAction(type="wait", seconds=0),
        viewport_width=320,
        viewport_height=180,
        metadata={"observation_only": True},
    )
    facility.store.save_evidence_step(source_step)
    locator = SkillLocatorV1(
        id="locator.dynamic.land",
        step_index=0,
        strategy="template",
        selector="城外3级粮食地",
        reference_bounds=SourcePixelRect(x=40, y=50, width=32, height=24),
        mobility="dynamic_world_object",
        reference_artifact_id=reference_artifact.id,
        search_region=SourcePixelRect(x=0, y=0, width=320, height=180),
        match_threshold=0.82,
    )
    action_step = SkillStepV1(
        id="step.dynamic.land.tap",
        kind="action",
        action=NormalizedAction(type="tap", x=56, y=62),
        locator_id=locator.id,
        idempotency="verify_before_retry",
        side_effect="progression",
    )
    assert_step = SkillStepV1(
        id="step.dynamic.land.assert",
        kind="assert",
        depends_on_step_ids=[action_step.id],
        assertion="已打开3级粮食地",
        expected_state_id="state.dynamic.land.open",
        idempotency="read_only",
        side_effect="none",
    )
    skill = SimpleNamespace(
        id="skill.dynamic.land.version.1",
        title="城外3级粮食地",
        applicability="城外地图",
        procedure_steps=["点击3级粮食地"],
        executor_kind="normalized_actions",
        locators=[locator],
        steps=[action_step, assert_step],
    )
    return context, source_step, skill, action_step


def test_production_template_replay_relocates_tap_and_records_provenance(tmp_path):
    context, source_step, skill, action_step = _dynamic_fixture(tmp_path)

    action, bounds, provenance = game_cli._resolve_skill_action_locator(
        context,
        skill=skill,
        step=action_step,
        source_step_id=source_step.id,
    )

    assert bounds == SourcePixelRect(x=210, y=100, width=32, height=24)
    assert (action.x, action.y) == (226, 112)
    assert provenance is not None
    assert provenance["reference_artifact_id"] == "artifact.template.reference"
    assert provenance["current_artifact_id"] == "artifact.template.current"
    assert provenance["score"] >= 0.82
    assert provenance["resolved_bounds"] == bounds.model_dump(mode="json")


def test_guarded_adapter_accepts_complete_dynamic_template_without_device_access(tmp_path):
    _context, _source_step, skill, _action_step = _dynamic_fixture(tmp_path)
    calls: list[str] = []
    adapter = GuardedSkillStepAdapter(
        execute_guarded_action=lambda *_args: calls.append("action"),
        observe_state=lambda *_args: calls.append("observe"),
    )

    adapter.validate_skill(skill)

    assert calls == []


def test_production_template_replay_fails_before_action_when_current_target_is_missing(tmp_path):
    context, source_step, skill, action_step = _dynamic_fixture(tmp_path)
    facility = context.facility()
    blank = np.full((180, 320, 3), 18, dtype=np.uint8)
    blank_artifact = _save_rgb_artifact(facility, tmp_path, "artifact.template.blank", blank)
    facility.store.save_evidence_step(
        source_step.model_copy(
            update={
                "id": "evidence.step.template.blank",
                "before_frame_id": blank_artifact.id,
                "after_frame_id": blank_artifact.id,
                "artifact_ids": [blank_artifact.id],
            }
        )
    )

    with pytest.raises(game_cli.click.ClickException, match="dynamic_locator_unresolved"):
        game_cli._resolve_skill_action_locator(
            context,
            skill=skill,
            step=action_step,
            source_step_id="evidence.step.template.blank",
        )