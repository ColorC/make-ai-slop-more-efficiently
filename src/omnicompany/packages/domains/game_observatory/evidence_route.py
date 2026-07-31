from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .gateway import DeviceGateway, GatewayError
from .models import (
    EvidenceDynamicSceneProfile,
    EvidenceTerminalCondition,
    NormalizedAction,
    SourcePixelRect,
    utc_now,
)
from .store import ObservatoryStore


class EvidenceRouteStep(BaseModel):
    id: str
    action: NormalizedAction
    target_name: str | None = None
    target_bounds: SourcePixelRect | None = None
    settle_threshold: float = Field(default=0.03, ge=0, le=1)
    required_consecutive: int = Field(default=3, ge=1, le=20)
    settle_timeout_seconds: float = Field(default=8.0, ge=0.05, le=60)
    sample_interval_seconds: float = Field(default=0.25, ge=0.05, le=10)
    terminal_condition: EvidenceTerminalCondition | None = None
    dynamic_scene_profile: EvidenceDynamicSceneProfile | None = None

    @model_validator(mode="after")
    def dynamic_scene_requires_terminal(self) -> "EvidenceRouteStep":
        if self.dynamic_scene_profile is not None and self.terminal_condition is None:
            raise ValueError("dynamic scene profile requires an explicit terminal condition")
        return self


class EvidenceRoute(BaseModel):
    schema_id: str = Field(default="game-observatory.evidence-route.v1", alias="schema")
    id: str
    title: str
    target_id: str
    viewport_width: int = Field(gt=0, le=16_384)
    viewport_height: int = Field(gt=0, le=16_384)
    game_id: str
    build_scope_id: str
    scope_id: str
    start_state: str
    end_state: str
    excluded_actions: list[str] = Field(default_factory=list)
    steps: list[EvidenceRouteStep] = Field(min_length=1)


def evidence_route_sha256(route: EvidenceRoute) -> str:
    """Return the canonical content hash persisted before route execution."""

    payload = json.dumps(
        route.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class EvidenceRouteRunner:
    def __init__(
        self,
        store: ObservatoryStore,
        gateway: DeviceGateway | None = None,
    ) -> None:
        self.store = store
        self.gateway = gateway or DeviceGateway(store)

    def run(
        self,
        route: EvidenceRoute,
        lease_token: str,
        *,
        repetitions: int = 1,
    ) -> dict[str, Any]:
        requested = max(1, min(repetitions, 20))
        route_definition_sha256 = evidence_route_sha256(route)
        if not self.store.get_target(route.target_id):
            self.gateway.refresh()
        summaries: list[dict[str, Any]] = []
        overall_ok = True

        for repetition in range(1, requested + 1):
            evidence_run_id: str | None = None
            step_summaries: list[dict[str, Any]] = []
            run_error: str | None = None
            manifest = None
            try:
                run = self.gateway.start_evidence_run(
                    route.target_id,
                    lease_token,
                    viewport_width=route.viewport_width,
                    viewport_height=route.viewport_height,
                    game_id=route.game_id,
                    build_scope_id=route.build_scope_id,
                    scope_id=route.scope_id,
                    environment={
                        "route_id": route.id,
                        "route_title": route.title,
                        "route_repetition": repetition,
                        "route_start_state": route.start_state,
                        "route_end_state": route.end_state,
                        "route_definition_sha256": route_definition_sha256,
                    },
                )
                evidence_run_id = run.id
                for expected_index, spec in enumerate(route.steps, start=1):
                    step = self.gateway.record_evidence_step(
                        run.id,
                        lease_token,
                        spec.action,
                        target_name=spec.target_name,
                        target_bounds=spec.target_bounds,
                        settle_threshold=spec.settle_threshold,
                        required_consecutive=spec.required_consecutive,
                        settle_timeout_seconds=spec.settle_timeout_seconds,
                        sample_interval_seconds=spec.sample_interval_seconds,
                        terminal_condition=spec.terminal_condition,
                        dynamic_scene_profile=spec.dynamic_scene_profile,
                    )
                    step_summaries.append(
                        {
                            "route_step_id": spec.id,
                            "evidence_step_id": step.id,
                            "expected_index": expected_index,
                            "actual_index": step.step_index,
                            "status": step.status,
                            "before_frame_id": step.before_frame_id,
                            "after_frame_id": step.after_frame_id,
                            "video_artifact_id": step.video_artifact_id,
                            "action_run_id": step.action_run_id,
                            "intermediate_frame_count": len(step.intermediate_frame_ids),
                            "stability": step.stability.model_dump(mode="json"),
                            "quality_issues": step.quality_issues,
                            "error": step.error,
                        }
                    )
                    if step.status != "passed":
                        run_error = f"route step failed: {spec.id}"
                        break
                manifest = self.gateway.complete_evidence_run(run.id, lease_token)
            except (GatewayError, OSError, ValueError) as exc:
                run_error = str(exc)
                if evidence_run_id:
                    try:
                        manifest = self.gateway.complete_evidence_run(
                            evidence_run_id, lease_token
                        )
                    except Exception:
                        pass

            repetition_ok = bool(
                manifest
                and manifest.publishable
                and len(step_summaries) == len(route.steps)
                and all(item["status"] == "passed" for item in step_summaries)
                and [item["actual_index"] for item in step_summaries]
                == list(range(1, len(route.steps) + 1))
            )
            overall_ok = overall_ok and repetition_ok
            summaries.append(
                {
                    "repetition": repetition,
                    "ok": repetition_ok,
                    "evidence_run_id": evidence_run_id,
                    "manifest_id": manifest.id if manifest else None,
                    "publishable": manifest.publishable if manifest else False,
                    "publication_issues": manifest.publication_issues if manifest else [],
                    "error": run_error,
                    "steps": step_summaries,
                }
            )
            if not repetition_ok:
                break

        completed = len(summaries)
        overall_ok = overall_ok and completed == requested
        generated_at = manifest.generated_at if manifest is not None else utc_now()
        payload = {
            "schema": "game-observatory.evidence-route-verification.v1",
            "generated_at": generated_at,
            "ok": overall_ok,
            "route": route.model_dump(mode="json", by_alias=True),
            "requested_repetitions": requested,
            "completed_repetitions": completed,
            "runs": summaries,
        }
        export_root = self.store.export_root / "evidence-routes"
        export_root.mkdir(parents=True, exist_ok=True)
        stamp = re.sub(r"[^0-9]", "", payload["generated_at"])[:14]
        path = export_root / f"{route.id}-{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["verification_path"] = str(path.resolve())
        return payload


def load_evidence_route(path: Path) -> EvidenceRoute:
    return EvidenceRoute.model_validate_json(path.read_text(encoding="utf-8"))
