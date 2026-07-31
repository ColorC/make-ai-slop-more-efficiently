from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import cv2
import numpy as np

from omnicompany.packages.domains.game_observatory.adapters import AdapterActionError
from omnicompany.packages.domains.game_observatory.evidence import EvidenceRecorder
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    NormalizedAction,
    ObservationBundle,
    RunResult,
    SourcePixelRect,
    utc_now,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


class FakeEvidenceAdapter:
    def __init__(
        self,
        store: ObservatoryStore,
        root: Path,
        *,
        video_fails: bool = False,
        action_fails: bool = False,
    ) -> None:
        self.store = store
        self.root = root
        self.video_fails = video_fails
        self.action_fails = action_fails
        self.state = 0
        self.observations = 0
        self.action_calls = 0

    def _png(self, path: Path) -> None:
        image = np.zeros((200, 100, 3), dtype=np.uint8)
        image[:, :, 0] = np.arange(100, dtype=np.uint8)
        image[:, :, 1] = 80 + self.state * 80
        image[20:70, 10:60, 2] = 180
        assert cv2.imwrite(str(path), image)

    def observe_frame(self, *, include_ui: bool = False) -> ObservationBundle:
        del include_ui
        self.observations += 1
        artifact_id = f"art.fake.frame.{self.observations}.{uuid.uuid4().hex[:6]}"
        run_id = f"run.fake.observe.{self.observations}.{uuid.uuid4().hex[:6]}"
        path = self.root / f"{artifact_id}.png"
        self._png(path)
        body = path.read_bytes()
        artifact = ArtifactRef(
            id=artifact_id,
            kind="screenshot",
            path=str(path),
            sha256=hashlib.sha256(body).hexdigest(),
            run_id=run_id,
            media_type="image/png",
        )
        self.store.save_artifact(artifact)
        self.store.save_run(
            RunResult(
                id=run_id,
                adapter="fake",
                target_id="device://fake/1",
                status="passed",
                ended_at=utc_now(),
                artifact_ids=[artifact.id],
            )
        )
        return ObservationBundle(target_id="device://fake/1", frame=artifact)

    def begin_video_capture(self, *, max_seconds: int = 180) -> dict[str, int]:
        del max_seconds
        if self.video_fails:
            raise RuntimeError("video unavailable")
        return {"sequence": self.observations}

    def finish_video_capture(
        self,
        handle: dict[str, int],
        *,
        evidence_run_id: str,
        evidence_step_id: str,
    ) -> ArtifactRef:
        del handle, evidence_step_id
        artifact_id = f"art.fake.video.{uuid.uuid4().hex[:8]}"
        path = self.root / f"{artifact_id}.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2")
        body = path.read_bytes()
        artifact = ArtifactRef(
            id=artifact_id,
            kind="video",
            path=str(path),
            sha256=hashlib.sha256(body).hexdigest(),
            run_id=evidence_run_id,
            media_type="video/mp4",
        )
        self.store.save_artifact(artifact)
        return artifact

    def act(self, action: NormalizedAction) -> dict[str, object]:
        self.action_calls += 1
        run = RunResult(
            id=f"run.fake.action.{uuid.uuid4().hex[:8]}",
            adapter="fake",
            target_id="device://fake/1",
            status="failed" if self.action_fails else "passed",
            ended_at=utc_now(),
            error="simulated action failure" if self.action_fails else None,
        )
        self.store.save_run(run)
        if self.action_fails:
            result = {"ok": False, "run_id": run.id, "action": action.model_dump()}
            raise AdapterActionError("simulated action failure", result)
        self.state = 1
        return {"ok": True, "run_id": run.id, "action": action.model_dump()}


def _start(recorder: EvidenceRecorder):
    return recorder.start_run(
        target_id="device://fake/1",
        adapter="fake",
        viewport_width=100,
        viewport_height=200,
        game_id="afk-journey",
        build_scope_id="build.test",
        scope_id="hero-hall-safe-entry",
    )


def _tap() -> NormalizedAction:
    return NormalizedAction(type="tap", x=25, y=50)


def _bounds() -> SourcePixelRect:
    return SourcePixelRect(x=10, y=20, width=50, height=60)


def test_evidence_step_unifies_before_action_intermediate_after_video_and_manifest(tmp_path):
    store = ObservatoryStore(tmp_path / "store")
    adapter = FakeEvidenceAdapter(store, tmp_path)
    recorder = EvidenceRecorder(store, adapter, sleep=lambda _seconds: None)
    run = _start(recorder)

    step = recorder.record_step(
        run.id,
        _tap(),
        target_name="英雄厅入口",
        target_bounds=_bounds(),
        settle_timeout_seconds=1,
        sample_interval_seconds=0.05,
    )
    manifest = recorder.complete_run(run.id)

    assert step.status == "passed"
    assert step.before_frame_id
    assert step.after_frame_id
    assert step.video_artifact_id
    assert len(step.intermediate_frame_ids) == 3
    assert step.stability.settled is True
    assert step.source_point.model_dump() == {"x": 25, "y": 50}
    assert manifest.publishable is True
    assert manifest.publication_issues == []
    assert manifest.run.status == "passed"
    assert manifest.run.step_ids == [step.id]
    assert manifest.action_run_ids == [step.action_run_id]
    assert set(manifest.artifact_ids) == set(step.artifact_ids)
    assert set(manifest.observation_run_ids) == set(step.observation_run_ids)
    assert store.get_evidence_manifest(run.id).id == manifest.id
    assert store.counts()["evidence_runs"] == 1
    assert store.counts()["evidence_steps"] == 1
    assert store.counts()["evidence_manifests"] == 1


def test_pre_action_video_failure_prevents_mutation_and_remains_durable(tmp_path):
    store = ObservatoryStore(tmp_path / "store")
    adapter = FakeEvidenceAdapter(store, tmp_path, video_fails=True)
    recorder = EvidenceRecorder(store, adapter, sleep=lambda _seconds: None)
    run = _start(recorder)

    step = recorder.record_step(
        run.id,
        _tap(),
        target_name="英雄厅入口",
        target_bounds=_bounds(),
    )
    manifest = recorder.complete_run(run.id)

    assert adapter.action_calls == 0
    assert step.status == "failed"
    assert "pre-action evidence failed" in " ".join(step.quality_issues)
    assert store.get_evidence_step(step.id).status == "failed"
    assert manifest.publishable is False
    assert any("missing before_frame" in item for item in manifest.publication_issues)
    assert any("missing video" in item for item in manifest.publication_issues)


def test_failed_action_keeps_action_run_and_visual_recovery_evidence(tmp_path):
    store = ObservatoryStore(tmp_path / "store")
    adapter = FakeEvidenceAdapter(store, tmp_path, action_fails=True)
    recorder = EvidenceRecorder(store, adapter, sleep=lambda _seconds: None)
    run = _start(recorder)

    step = recorder.record_step(
        run.id,
        _tap(),
        target_name="英雄厅入口",
        target_bounds=_bounds(),
        settle_timeout_seconds=1,
        sample_interval_seconds=0.05,
    )
    manifest = recorder.complete_run(run.id)

    assert step.status == "failed"
    assert step.action_run_id
    assert store.get_run(step.action_run_id).status == "failed"
    assert step.before_frame_id and step.after_frame_id and step.video_artifact_id
    assert step.action_run_id in manifest.action_run_ids
    assert manifest.publishable is False
    assert any("simulated action failure" in item for item in manifest.publication_issues)


def test_manifest_rechecks_artifact_files_and_fails_closed_on_dead_media(tmp_path):
    store = ObservatoryStore(tmp_path / "store")
    adapter = FakeEvidenceAdapter(store, tmp_path)
    recorder = EvidenceRecorder(store, adapter, sleep=lambda _seconds: None)
    run = _start(recorder)
    step = recorder.record_step(
        run.id,
        _tap(),
        target_name="英雄厅入口",
        target_bounds=_bounds(),
        settle_timeout_seconds=1,
        sample_interval_seconds=0.05,
    )
    Path(store.get_artifact(step.after_frame_id).path).unlink()

    manifest = recorder.complete_run(run.id)

    assert manifest.publishable is False
    assert manifest.run.status == "failed"
    assert any("artifact file is missing" in item for item in manifest.publication_issues)


def test_tap_geometry_outside_target_bounds_is_not_publishable(tmp_path):
    store = ObservatoryStore(tmp_path / "store")
    adapter = FakeEvidenceAdapter(store, tmp_path)
    recorder = EvidenceRecorder(store, adapter, sleep=lambda _seconds: None)
    run = _start(recorder)

    step = recorder.record_step(
        run.id,
        _tap(),
        target_name="错误热区",
        target_bounds=SourcePixelRect(x=60, y=20, width=20, height=40),
        settle_timeout_seconds=1,
        sample_interval_seconds=0.05,
    )
    manifest = recorder.complete_run(run.id)

    assert step.status == "failed"
    assert any("outside target bounds" in item for item in step.publication_issues())
    assert manifest.publishable is False