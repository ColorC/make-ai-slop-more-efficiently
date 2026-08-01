"""Build fail-closed interaction preflights from canonical local perception evidence."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal

import cv2
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import ArtifactRef, SourcePixelRect, utc_now
from .interaction_preflight import (
    InteractionExpectedChangeV1,
    InteractionPreflightV1,
)
from .store import AIPlayerStore


class InteractionPreflightProductionRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str = Field(min_length=1)
    source_artifact_id: str = Field(min_length=1)
    locator_result: dict[str, Any]
    candidate_id: str = Field(min_length=1)
    candidate_kind: Literal[
        "actionable_control",
        "navigation",
        "selection_control",
        "text_input",
        "gesture_region",
    ]
    recognition_observation_id: str = Field(min_length=1)
    captured_state_id: str = Field(min_length=1)
    expected_change: InteractionExpectedChangeV1
    overlay_state: Literal["none", "active", "unknown"]
    active_layer_interaction_bounds: SourcePixelRect | None = None
    ui_tree_artifact_id: str | None = Field(default=None, min_length=1)
    template_evidence_artifact_id: str | None = Field(default=None, min_length=1)
    consecutive_no_change_count: int = Field(default=0, ge=0)
    max_consecutive_no_change_before_rerecognition: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def bind_overlay_region(self) -> "InteractionPreflightProductionRequestV1":
        if self.overlay_state == "active" and self.active_layer_interaction_bounds is None:
            raise ValueError("an active overlay requires its allowed interaction bounds")
        if self.overlay_state != "active" and self.active_layer_interaction_bounds is not None:
            raise ValueError("active-layer bounds are only valid for an active overlay")
        return self


class InteractionPreflightProductionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: Literal["passed", "rejected"]
    candidate_id: str = Field(min_length=1)
    local_evidence_artifact_id: str | None = Field(default=None, min_length=1)
    interactivity_evidence: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    preflight: InteractionPreflightV1 | None = None

    @model_validator(mode="after")
    def preserve_fail_closed_result(self) -> "InteractionPreflightProductionResultV1":
        if self.disposition == "passed":
            if self.preflight is None or self.reasons:
                raise ValueError("a passed production result requires one clean preflight")
        elif self.preflight is not None or not self.reasons:
            raise ValueError("a rejected production result requires reasons and no preflight")
        return self


_BOUNDS_PATTERN = re.compile(r"^\[(\d+),(\d+)]\[(\d+),(\d+)]$")


def _iou(left: SourcePixelRect, right: SourcePixelRect) -> float:
    x1 = max(left.x, right.x)
    y1 = max(left.y, right.y)
    x2 = min(left.x + left.width, right.x + right.width)
    y2 = min(left.y + left.height, right.y + right.height)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if intersection == 0:
        return 0.0
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union


def _contains(outer: SourcePixelRect, inner: SourcePixelRect) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def _rect(value: Any) -> SourcePixelRect | None:
    try:
        return SourcePixelRect.model_validate(value)
    except Exception:
        return None


def _artifact_is_current(artifact: ArtifactRef, environment_id: str) -> bool:
    path = Path(artifact.path)
    return (
        artifact.metadata.get("environment_id") == environment_id
        and path.is_file()
        and hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
    )


def _matching_ui_node(
    artifact: ArtifactRef | None,
    *,
    environment_id: str,
    candidate_bounds: SourcePixelRect,
) -> dict[str, bool] | None:
    if artifact is None or not _artifact_is_current(artifact, environment_id):
        return None
    try:
        root = ET.parse(artifact.path).getroot()
    except (ET.ParseError, OSError):
        return None
    best: tuple[float, dict[str, bool]] | None = None
    for node in root.iter():
        match = _BOUNDS_PATTERN.match(str(node.attrib.get("bounds") or ""))
        if match is None:
            continue
        x1, y1, x2, y2 = (int(value) for value in match.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        node_bounds = SourcePixelRect(x=x1, y=y1, width=x2 - x1, height=y2 - y1)
        overlap = _iou(candidate_bounds, node_bounds)
        if overlap < 0.5:
            continue
        state = {
            "clickable": node.attrib.get("clickable") == "true",
            "enabled": node.attrib.get("enabled", "true") == "true",
            "selected": node.attrib.get("selected") == "true"
            or node.attrib.get("checked") == "true",
        }
        if best is None or overlap > best[0]:
            best = (overlap, state)
    return best[1] if best is not None else None


def _has_overlapping_ocr_label(
    elements: list[dict[str, Any]],
    *,
    candidate_id: str,
    candidate_bounds: SourcePixelRect,
) -> bool:
    for element in elements:
        if str(element.get("id")) == candidate_id:
            continue
        content = str(element.get("content") or "").strip()
        element_type = str(element.get("type") or "").lower()
        source = str(element.get("source") or "").lower()
        bounds = _rect(element.get("source_bounds"))
        if (
            content
            and bounds is not None
            and ("text" in element_type or "ocr" in source)
            and (_iou(candidate_bounds, bounds) > 0 or _contains(candidate_bounds, bounds))
        ):
            return True
    return False


class InteractionPreflightProducer:
    """Deterministically fuse UI tree, OmniParser, OCR, templates and history."""

    def __init__(self, player_store: AIPlayerStore) -> None:
        self.player_store = player_store
        self.store = player_store.observatory_store

    def produce(
        self,
        request: InteractionPreflightProductionRequestV1,
    ) -> InteractionPreflightProductionResultV1:
        reasons: list[str] = []
        source = self.store.get_artifact(request.source_artifact_id)
        if source is None or not _artifact_is_current(source, request.environment_id):
            return self._rejected(request, ["当前原图不存在、环境不符或文件哈希失效。"])
        observation = self.player_store.get_state_observation(
            request.environment_id,
            request.recognition_observation_id,
        )
        observations = self.player_store.list_state_observations(request.environment_id)
        if observation is None:
            return self._rejected(request, ["交互候选没有绑定 canonical 状态观测。"])
        if not observations or observations[-1].id != observation.id:
            return self._rejected(request, ["交互候选绑定的不是当前最新状态观测。"])
        observation_artifact_ids = {
            artifact_id
            for reference in observation.evidence_refs
            for artifact_id in reference.artifact_ids
        }
        if source.id not in observation_artifact_ids:
            return self._rejected(request, ["当前原图不属于交互候选绑定的状态观测证据。"])
        current_assignment = self.player_store.get_current_state_assignment(
            request.environment_id,
            request.recognition_observation_id,
        )
        if (
            current_assignment is None
            or current_assignment.status != "active"
            or current_assignment.state_id != request.captured_state_id
        ):
            return self._rejected(request, ["状态观测没有有效绑定当前语义状态。"])
        image_info = request.locator_result.get("image")
        elements = request.locator_result.get("elements")
        if not isinstance(image_info, dict) or not isinstance(elements, list):
            return self._rejected(request, ["视觉定位结果缺少图像身份或候选列表。"])
        if (
            image_info.get("sha256") != source.sha256
            or image_info.get("width") is None
            or image_info.get("height") is None
        ):
            return self._rejected(request, ["视觉定位结果没有绑定当前原图。"])
        try:
            width = int(image_info["width"])
            height = int(image_info["height"])
        except (TypeError, ValueError):
            return self._rejected(request, ["视觉定位结果的原图尺寸无效。"])
        if (observation.viewport_width, observation.viewport_height) != (width, height):
            return self._rejected(request, ["状态观测视口与视觉定位结果不一致。"])
        candidate = next(
            (
                item
                for item in elements
                if isinstance(item, dict) and str(item.get("id")) == request.candidate_id
            ),
            None,
        )
        if candidate is None:
            return self._rejected(request, ["指定候选不在当前视觉定位结果中。"])
        bounds = _rect(candidate.get("source_bounds"))
        if bounds is None:
            return self._rejected(request, ["候选没有有效的原图位置框。"])
        if bounds.x + bounds.width > width or bounds.y + bounds.height > height:
            return self._rejected(request, ["候选位置框超出当前原图。"])
        if not bool(candidate.get("interaction_candidate")):
            reasons.append("视觉定位器没有把该元素列为交互候选。")

        signals: list[str] = []
        if bool(candidate.get("interactivity")) and str(
            candidate.get("interactivity_source")
        ) == "omniparser":
            signals.append("omniparser_interactivity")
        if _has_overlapping_ocr_label(
            elements,
            candidate_id=request.candidate_id,
            candidate_bounds=bounds,
        ):
            signals.append("ocr_label_match")

        ui_artifact = (
            self.store.get_artifact(request.ui_tree_artifact_id)
            if request.ui_tree_artifact_id
            else None
        )
        ui_node = _matching_ui_node(
            ui_artifact,
            environment_id=request.environment_id,
            candidate_bounds=bounds,
        )
        if ui_node is not None and ui_node["clickable"] and ui_node["enabled"]:
            signals.append("ui_tree_action")

        template_artifact = (
            self.store.get_artifact(request.template_evidence_artifact_id)
            if request.template_evidence_artifact_id
            else None
        )
        if (
            template_artifact is not None
            and _artifact_is_current(template_artifact, request.environment_id)
            and template_artifact.metadata.get("source_artifact_id") == source.id
            and template_artifact.metadata.get("source_bounds") == bounds.model_dump(mode="json")
            and template_artifact.metadata.get("template_match") is True
        ):
            signals.append("template_match")

        transition_edges = self.player_store.list_transition_edges(request.environment_id)
        for edge in transition_edges:
            if (
                edge.from_state_id == request.captured_state_id
                and edge.outcome == "verified_transition"
                and edge.target_bounds is not None
                and _iou(edge.target_bounds, bounds) >= 0.5
            ):
                signals.append("verified_transition")
                break
        derived_no_change_count = sum(
            edge.from_state_id == request.captured_state_id
            and edge.outcome in {"failed", "verified_no_change", "forbidden"}
            and edge.target_bounds is not None
            and _iou(edge.target_bounds, bounds) >= 0.5
            for edge in transition_edges
        )
        if request.consecutive_no_change_count != derived_no_change_count:
            reasons.append("连续无变化次数与 canonical 动作历史不一致。")

        signals = list(dict.fromkeys(signals))
        structural = any(
            signal in {"ui_tree_action", "verified_transition", "temporal_probe"}
            for signal in signals
        )
        two_source_visual = "omniparser_interactivity" in signals and any(
            signal in {"ocr_label_match", "template_match"} for signal in signals
        )
        if not (structural or two_source_visual):
            reasons.append("候选缺少结构证据，且没有两种独立视觉证据互相确认。")

        canonical_overlay_state = (
            "active" if observation.features.overlay_tokens else "none"
        )
        if request.overlay_state != "unknown" and request.overlay_state != canonical_overlay_state:
            reasons.append("请求中的遮罩状态与 canonical 状态观测不一致。")
        if request.overlay_state == "unknown":
            layer_state = "unknown"
            reasons.append("当前遮罩层状态未知。")
        elif request.overlay_state == "none":
            layer_state = "topmost"
        elif request.active_layer_interaction_bounds is not None and _contains(
            request.active_layer_interaction_bounds,
            bounds,
        ):
            layer_state = "topmost"
        else:
            layer_state = "outside_active_overlay"
            reasons.append("候选位于当前教程或弹窗允许交互区域之外。")

        if request.candidate_kind in {"navigation", "selection_control"}:
            if ui_node is None:
                selection_state = "unknown"
                reasons.append("页签或导航候选缺少可核验的选中态。")
            else:
                selection_state = "selected" if ui_node["selected"] else "unselected"
                if ui_node["selected"]:
                    reasons.append("候选已经处于选中状态。")
        else:
            selection_state = "not_applicable"

        decision = {
            "candidate_bounds": bounds.model_dump(mode="json"),
            "candidate_kind": request.candidate_kind,
            "interactivity": "confirmed" if not reasons else "heuristic",
            "interactivity_evidence": signals or ["visual_heuristic"],
            "selection_state": selection_state,
            "layer_state": layer_state,
            "recognition_observation_id": request.recognition_observation_id,
            "captured_state_id": request.captured_state_id,
            "expected_change": request.expected_change.model_dump(mode="json"),
            "consecutive_no_change_count": derived_no_change_count,
            "max_consecutive_no_change_before_rerecognition": (
                request.max_consecutive_no_change_before_rerecognition
            ),
        }
        local = self._save_local_evidence(
            request=request,
            source=source,
            bounds=bounds,
            decision=decision,
            locator_image=image_info,
        )
        if reasons:
            return self._rejected(
                request,
                reasons,
                local_evidence_artifact_id=local.id,
                signals=signals,
            )
        preflight = InteractionPreflightV1(
            source_artifact_id=source.id,
            local_evidence_artifact_id=local.id,
            source_viewport_width=width,
            source_viewport_height=height,
            candidate_bounds=bounds,
            candidate_kind=request.candidate_kind,
            interactivity="confirmed",
            interactivity_evidence=signals,
            selection_state=selection_state,
            layer_state=layer_state,
            recognition_observation_id=request.recognition_observation_id,
            captured_state_id=request.captured_state_id,
            expected_change=request.expected_change,
            consecutive_no_change_count=derived_no_change_count,
            max_consecutive_no_change_before_rerecognition=(
                request.max_consecutive_no_change_before_rerecognition
            ),
        )
        return InteractionPreflightProductionResultV1(
            disposition="passed",
            candidate_id=request.candidate_id,
            local_evidence_artifact_id=local.id,
            interactivity_evidence=signals,
            preflight=preflight,
        )

    def _save_local_evidence(
        self,
        *,
        request: InteractionPreflightProductionRequestV1,
        source: ArtifactRef,
        bounds: SourcePixelRect,
        decision: dict[str, Any],
        locator_image: dict[str, Any],
    ) -> ArtifactRef:
        image = cv2.imread(source.path, cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError("current interaction source image cannot be decoded")
        crop = image[bounds.y : bounds.y + bounds.height, bounds.x : bounds.x + bounds.width]
        ok, encoded = cv2.imencode(".png", crop)
        if not ok:
            raise ValueError("interaction local evidence cannot be encoded")
        payload = {
            "environment_id": request.environment_id,
            "source_artifact_id": source.id,
            "source_sha256": source.sha256,
            "candidate_id": request.candidate_id,
            "source_bounds": bounds.model_dump(mode="json"),
            "candidate_decision": decision,
            "locator_image": locator_image,
        }
        identity = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            + bytes(encoded)
        ).hexdigest()
        artifact_id = f"artifact.interaction-preflight.{identity[:24]}"
        path = self.store.artifact_root / "interaction_preflight" / f"{identity}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(encoded))
        artifact = ArtifactRef(
            id=artifact_id,
            kind="annotated_plate",
            path=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            captured_at=utc_now(),
            metadata={
                **payload,
                "producer": "interaction-preflight-producer.v1",
            },
        )
        existing = self.store.get_artifact(artifact_id)
        if existing is not None and (
            existing.sha256 != artifact.sha256 or existing.metadata != artifact.metadata
        ):
            raise ValueError("stable interaction evidence id already contains different content")
        if existing is None:
            self.store.save_artifact(artifact)
        return existing or artifact

    @staticmethod
    def _rejected(
        request: InteractionPreflightProductionRequestV1,
        reasons: list[str],
        *,
        local_evidence_artifact_id: str | None = None,
        signals: list[str] | None = None,
    ) -> InteractionPreflightProductionResultV1:
        return InteractionPreflightProductionResultV1(
            disposition="rejected",
            candidate_id=request.candidate_id,
            local_evidence_artifact_id=local_evidence_artifact_id,
            interactivity_evidence=signals or [],
            reasons=reasons,
        )


__all__ = [
    "InteractionPreflightProducer",
    "InteractionPreflightProductionRequestV1",
    "InteractionPreflightProductionResultV1",
]
