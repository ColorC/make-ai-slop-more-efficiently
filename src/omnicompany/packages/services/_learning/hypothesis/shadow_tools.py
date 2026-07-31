# [OMNI] origin=codex domain=services/hypothesis ts=2026-07-14T00:00:00Z type=router status=active
"""Shadow-scene tools for audited game exploration suggestions.

The hypothesis Experimenter may inspect a frozen screenshot and append probe
suggestions.  This module intentionally has no device adapter and no action
execution dependency.  A suggestion remains a proposal until an external owner
reviews it and records an EvidenceStep through the game-observatory gateway.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from PIL import Image, ImageDraw

from omnicompany.packages.services._core.agent import (
    SingleToolRouter,
    ToolExecutionError,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext


_APPEND_LOCK = threading.Lock()

_UNSUPPORTED_FUNCTION_TERMS = (
    "排序",
    "筛选",
    "切换",
    "导航",
    "详情",
    "语音",
    "表情",
    "购买",
    "确认",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _required_grounding_paths(scene: dict[str, Any]) -> list[Path]:
    """Return the de-duplicated union of frame, coordinate references, and explicit inputs."""
    observation = scene.get("observation") or {}
    raw_paths = [
        observation.get("frame_path"),
        *(scene.get("grounding_image_paths") or []),
        *(scene.get("required_grounding_image_paths") or []),
    ]
    required: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        if not str(raw or "").strip():
            continue
        path = Path(str(raw)).resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        required.append(path)
    return required


def _candidate_overlap_ratio(
    target_bounds: dict[str, Any],
    candidate_bounds: dict[str, Any],
) -> float:
    """Return intersection over the smaller rectangle area.

    Candidate boxes are untrusted and may be looser or tighter than the final
    control box.  Comparing against the smaller area permits that adjustment
    while still rejecting provenance ids from unrelated screen regions.
    """

    values = [
        _int_or_none(target_bounds.get(key))
        for key in ("x", "y", "width", "height")
    ] + [
        _int_or_none(candidate_bounds.get(key))
        for key in ("x", "y", "width", "height")
    ]
    if any(value is None for value in values):
        return 0.0
    tx, ty, tw, th, cx, cy, cw, ch = (int(value) for value in values)
    if min(tw, th, cw, ch) <= 0:
        return 0.0
    intersection_width = max(0, min(tx + tw, cx + cw) - max(tx, cx))
    intersection_height = max(0, min(ty + th, cy + ch) - max(ty, cy))
    intersection = intersection_width * intersection_height
    return intersection / min(tw * th, cw * ch)


def _bounds_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        _int_or_none(left.get(field)) == _int_or_none(right.get(field))
        for field in ("x", "y", "width", "height")
    )


def _complete_ocr_tokens(image: Image.Image) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return all OCR tokens and the subset wholly inside the exact crop."""

    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment boundary
        raise ToolExecutionError(
            "region inspection requires rapidocr_onnxruntime and numpy"
        ) from exc
    result, _ = RapidOCR()(np.asarray(image.convert("RGB")))
    all_tokens: list[dict[str, Any]] = []
    complete_tokens: list[dict[str, Any]] = []
    edge_margin = max(6, round(min(image.width, image.height) * 0.015))
    for item in result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        try:
            polygon = [[float(point[0]), float(point[1])] for point in item[0]]
            score = float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        text = str(item[1]).strip()
        if not text or score < 0.6:
            continue
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        touches_edge = (
            min(xs) <= edge_margin
            or min(ys) <= edge_margin
            or max(xs) >= image.width - 1 - edge_margin
            or max(ys) >= image.height - 1 - edge_margin
        )
        token = {
            "text": text,
            "score": round(score, 6),
            "polygon": polygon,
            "complete_inside_crop": not touches_edge,
        }
        all_tokens.append(token)
        if not touches_edge:
            complete_tokens.append(token)
    return all_tokens, complete_tokens


class DeclareProbeInventoryRouter(SingleToolRouter):
    """Freeze a full-frame candidate plan before any region crop is requested."""

    TOOL_NAME: ClassVar[str] = "declare_probe_inventory"
    DESCRIPTION: ClassVar[str] = (
        "读取完整冻结画面后，一次性声明本轮候选清单。候选清单只记录画面可见几何、"
        "动作家族、控件组和交互支持类型；工具无法读取 benchmark expected truth，也不操作设备。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "coordinate_space": {
                "type": "string",
                "enum": ["source_pixels", "normalized_1000"],
            },
            "candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                        "action_family": {
                            "type": "string",
                            "enum": ["tap", "swipe", "pinch", "two_finger_swipe"],
                        },
                        "approximate_bounds": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "width": {"type": "integer"},
                                "height": {"type": "integer"},
                            },
                            "required": ["x", "y", "width", "height"],
                        },
                        "group_id": {"type": "string"},
                        "interaction_support_kind": {
                            "type": "string",
                            "enum": [
                                "prior_verified_target",
                                "isolated_overlay_container",
                                "ui_control_group_membership",
                                "reviewed_visual_manifest_candidate",
                                "explicit_gesture_instruction",
                                "pending_visual_review",
                                "unverified_visual",
                            ],
                        },
                        "support_note": {"type": "string"},
                    },
                    "required": [
                        "id",
                        "label",
                        "action_family",
                        "approximate_bounds",
                        "interaction_support_kind",
                    ],
                },
            },
        },
        "required": ["coordinate_space", "candidates"],
    }
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file_bytes",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        scene = getattr(ctx, "hypothesis_scene", None)
        if not isinstance(scene, dict) or scene.get("mode") != "shadow":
            raise ToolExecutionError("declare_probe_inventory 只允许在 hypothesis shadow scene 中使用")
        if scene.get("kind") != "game-ui-exploration":
            raise ToolExecutionError("scene.kind 必须为 game-ui-exploration")
        if scene.get("_region_inspections"):
            raise ToolExecutionError("候选清单必须在第一次区域检查前冻结")
        if scene.get("_probe_candidate_inventory"):
            raise ToolExecutionError("本 session 的候选清单已经冻结，不能覆盖")
        expected_space = str(scene.get("coordinate_space") or "normalized_1000")
        if args.get("coordinate_space") != expected_space:
            raise ToolExecutionError(
                "coordinate_space 必须与 scene 一致: "
                f"expected={expected_space}, got={args.get('coordinate_space')}"
            )
        required_paths = _required_grounding_paths(scene)
        seen_paths = {
            str(Path(str(item)).resolve())
            for item in (getattr(ctx, "hypothesis_seen_image_paths", None) or set())
        }
        missing_paths = [str(item) for item in required_paths if str(item) not in seen_paths]
        if missing_paths:
            raise ToolExecutionError(
                "候选清单只能在完整冻结画面送达后声明；先 read_image。"
                f"missing={missing_paths}"
            )
        observation = scene.get("observation") or {}
        viewport = observation.get("viewport") or {}
        width = _int_or_none(viewport.get("width")) or 0
        height = _int_or_none(viewport.get("height")) or 0
        if width <= 0 or height <= 0:
            raise ToolExecutionError("scene observation 缺少有效源画面尺寸")
        allowed_actions = {str(item) for item in scene.get("allowed_action_types") or []}
        candidates = args.get("candidates") or []
        gesture_instruction_tokens: list[dict[str, Any]] = []
        if scene.get("require_gesture_surface_separation") and any(
            str(item.get("action_family") or "") in {"pinch", "two_finger_swipe"}
            for item in candidates
            if isinstance(item, dict)
        ):
            frame = Path(str(observation.get("frame_path") or "")).resolve()
            if not frame.is_file():
                raise ToolExecutionError(f"冻结原图不存在: {frame}")
            with Image.open(frame) as opened:
                _, gesture_instruction_tokens = _complete_ocr_tokens(
                    opened.convert("RGB")
                )
        ids: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for raw in candidates:
            candidate_id = str(raw.get("id") or "").strip()
            label = str(raw.get("label") or "").strip()
            action_family = str(raw.get("action_family") or "").strip()
            support_kind = str(raw.get("interaction_support_kind") or "").strip()
            if not candidate_id or not label:
                raise ToolExecutionError("候选 id 与 label 必填")
            if candidate_id in ids:
                raise ToolExecutionError(f"候选 id 重复: {candidate_id}")
            if action_family not in allowed_actions:
                raise ToolExecutionError(f"候选动作不在 scene 允许范围: {action_family}")
            source = ProposeProbeRouter._source_geometry(
                {
                    "coordinate_space": expected_space,
                    "action_type": "wait",
                    "target_bounds": raw.get("approximate_bounds"),
                },
                width=width,
                height=height,
            )
            bounds = source.get("target_bounds")
            if not isinstance(bounds, dict):
                raise ToolExecutionError(f"候选 approximate_bounds 无效: {candidate_id}")
            if support_kind == "ui_control_group_membership" and not str(
                raw.get("group_id") or ""
            ).strip():
                raise ToolExecutionError(
                    f"控件组支持需要 group_id: {candidate_id}"
                )
            if (
                support_kind == "reviewed_visual_manifest_candidate"
                and not (scene.get("visual_candidate_manifest") or {}).get("candidates")
            ):
                raise ToolExecutionError(
                    "当前 scene 没有已审阅 visual candidate manifest；"
                    "不能声明 reviewed_visual_manifest_candidate。"
                    "若目标属于画面内明确的控件组，使用 ui_control_group_membership "
                    "并填写 group_id；否则只保留为 unverified_visual。"
                )
            if support_kind == "explicit_gesture_instruction" and action_family not in {
                "pinch",
                "two_finger_swipe",
            }:
                raise ToolExecutionError(
                    f"显式手势指令只支持多点手势候选: {candidate_id}"
                )
            if (
                scene.get("require_gesture_surface_separation")
                and action_family in {"pinch", "two_finger_swipe"}
            ):
                gesture_terms = ("双指", "滑动", "放大", "缩小", "拖动", "平移")
                instruction_tokens_inside = []
                for token in gesture_instruction_tokens:
                    text = str(token.get("text") or "")
                    polygon = token.get("polygon") or []
                    if not polygon or not any(term in text for term in gesture_terms):
                        continue
                    xs = [float(point[0]) for point in polygon]
                    ys = [float(point[1]) for point in polygon]
                    if (
                        bounds["x"] <= min(xs)
                        and max(xs) <= bounds["x"] + bounds["width"]
                        and bounds["y"] <= min(ys)
                        and max(ys) <= bounds["y"] + bounds["height"]
                    ):
                        instruction_tokens_inside.append(text)
                if instruction_tokens_inside or "指令" in label:
                    raise ToolExecutionError(
                        "多点手势候选的 approximate_bounds 必须圈定实际手势表面，"
                        "不能圈定说明文字；说明文字只写入 support_note。"
                        f" candidate={candidate_id}, tokens={instruction_tokens_inside}"
                    )
            duplicate = next(
                (
                    item
                    for item in normalized
                    if item["action_family"] == action_family
                    and _candidate_overlap_ratio(
                        bounds,
                        item["approximate_bounds"],
                    )
                    >= 0.8
                ),
                None,
            )
            if duplicate is not None:
                raise ToolExecutionError(
                    "同一动作家族的候选几何重复: "
                    f"{candidate_id} overlaps {duplicate['id']}"
                )
            ids.add(candidate_id)
            normalized.append(
                {
                    "id": candidate_id,
                    "label": label,
                    "action_family": action_family,
                    "approximate_bounds": bounds,
                    "group_id": str(raw.get("group_id") or "").strip() or None,
                    "interaction_support_kind": support_kind,
                    "support_note": str(raw.get("support_note") or "").strip(),
                    "inspection_count": 0,
                    "status": (
                        "unverified_visual"
                        if support_kind == "unverified_visual"
                        else (
                            "pending_visual_review"
                            if support_kind == "pending_visual_review"
                            else "pending_inspection"
                        )
                    ),
                }
            )
        required_inventory_ids = {
            str(item).strip()
            for item in scene.get("required_inventory_candidate_ids") or []
            if str(item).strip()
        }
        missing_required_ids = sorted(required_inventory_ids - ids)
        if missing_required_ids:
            raise ToolExecutionError(
                "冻结候选清单缺少 scene 明确要求复核的候选: "
                + ", ".join(missing_required_ids)
            )
        max_inspections = _int_or_none(scene.get("max_region_inspections"))
        actionable = [
            item for item in normalized
            if item["interaction_support_kind"] != "unverified_visual"
        ]
        if max_inspections is not None and len(actionable) > max_inspections:
            raise ToolExecutionError(
                "候选清单超过区域检查预算；不能在冻结后静默丢弃候选。"
                f" actionable={len(actionable)}, limit={max_inspections}"
            )
        scene["_probe_candidate_inventory"] = {
            "frozen_at": _utc_now(),
            "observation_sha256": observation.get("sha256"),
            "coordinate_space": expected_space,
            "candidates": normalized,
        }
        return json.dumps(
            {
                "frozen": True,
                "candidate_count": len(normalized),
                "actionable_candidate_count": len(actionable),
                "unverified_candidate_count": len(normalized) - len(actionable),
            },
            ensure_ascii=False,
        )


class InspectProbeRegionRouter(SingleToolRouter):
    """Render and attach the exact pixels covered by a proposed target box."""

    TOOL_NAME: ClassVar[str] = "inspect_probe_region"
    DESCRIPTION: ClassVar[str] = (
        "在 game-ui shadow scene 中检查候选目标框。工具从冻结原图生成“上下文红框 + "
        "目标框精确裁片”，并把图片挂到下一轮；只写内部检查产物，不操作设备。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "coordinate_space": {
                "type": "string",
                "enum": ["source_pixels", "normalized_1000"],
            },
            "target_bounds": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["x", "y", "width", "height"],
            },
            "note": {"type": "string"},
            "candidate_id": {
                "type": "string",
                "description": "启用候选清单门时必须引用的冻结 candidate id",
            },
        },
        "required": ["coordinate_space", "target_bounds"],
    }
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file_bytes",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.write_file",)
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        scene = getattr(ctx, "hypothesis_scene", None)
        if not isinstance(scene, dict) or scene.get("mode") != "shadow":
            raise ToolExecutionError(
                "inspect_probe_region 只允许在 hypothesis shadow scene 中使用"
            )
        if scene.get("kind") != "game-ui-exploration":
            raise ToolExecutionError("scene.kind 必须为 game-ui-exploration")
        observation = scene.get("observation") or {}
        frame = Path(str(observation.get("frame_path") or "")).resolve()
        required_paths = _required_grounding_paths(scene)
        seen_paths = {
            str(Path(str(item)).resolve())
            for item in (getattr(ctx, "hypothesis_seen_image_paths", None) or set())
        }
        missing_paths = [str(item) for item in required_paths if str(item) not in seen_paths]
        if missing_paths:
            raise ToolExecutionError(
                "必需的冻结画面尚未全部送达；先 read_image。"
                f"missing={missing_paths}"
            )
        viewport = observation.get("viewport") or {}
        width = _int_or_none(viewport.get("width")) or 0
        height = _int_or_none(viewport.get("height")) or 0
        expected_space = str(scene.get("coordinate_space") or "normalized_1000")
        if args.get("coordinate_space") != expected_space:
            raise ToolExecutionError(
                "coordinate_space 必须与 scene 一致: "
                f"expected={expected_space}, got={args.get('coordinate_space')}"
            )
        source_args = ProposeProbeRouter._source_geometry(
            {
                "coordinate_space": args.get("coordinate_space"),
                "action_type": "wait",
                "target_bounds": args.get("target_bounds"),
            },
            width=width,
            height=height,
        )
        bounds = source_args.get("target_bounds")
        if not isinstance(bounds, dict):
            raise ToolExecutionError("target_bounds 无效")
        bx, by, bw, bh = (
            _int_or_none(bounds.get(field))
            for field in ("x", "y", "width", "height")
        )
        if None in {bx, by, bw, bh} or min(bw or 0, bh or 0) <= 0:
            raise ToolExecutionError("target_bounds 无效")
        assert bx is not None and by is not None and bw is not None and bh is not None
        if bx + bw > width or by + bh > height:
            raise ToolExecutionError("target_bounds 超出源画面")

        inventory_candidate_id = str(args.get("candidate_id") or "").strip()
        inventory_candidate: dict[str, Any] | None = None
        if scene.get("require_candidate_inventory"):
            inventory = scene.get("_probe_candidate_inventory") or {}
            inventory_items = {
                str(item.get("id") or ""): item
                for item in inventory.get("candidates") or []
                if isinstance(item, dict) and item.get("id")
            }
            if not inventory_items:
                raise ToolExecutionError(
                    "第一次区域检查前必须调用 declare_probe_inventory 冻结全图候选清单"
                )
            if not inventory_candidate_id:
                raise ToolExecutionError(
                    "启用候选清单门时 inspect_probe_region 必须传 candidate_id"
                )
            inventory_candidate = inventory_items.get(inventory_candidate_id)
            if inventory_candidate is None:
                raise ToolExecutionError(
                    f"candidate_id 不属于冻结候选清单: {inventory_candidate_id}"
                )
            if inventory_candidate.get("interaction_support_kind") == "unverified_visual":
                raise ToolExecutionError(
                    "缺少交互支持的可见图形只保留在候选清单，不消耗区域检查预算"
                )
            previous_candidate_inspections = [
                item
                for item in (scene.get("_region_inspections") or {}).values()
                if isinstance(item, dict)
                and item.get("candidate_id") == inventory_candidate_id
            ]
            approximate_bounds = inventory_candidate.get("approximate_bounds") or {}
            overlaps_frozen_candidate = _candidate_overlap_ratio(
                bounds,
                approximate_bounds,
            ) >= 0.25
            correction_within_radius = False
            if previous_candidate_inspections:
                original_center_x = (
                    float(approximate_bounds.get("x") or 0)
                    + float(approximate_bounds.get("width") or 0) / 2
                )
                original_center_y = (
                    float(approximate_bounds.get("y") or 0)
                    + float(approximate_bounds.get("height") or 0) / 2
                )
                new_center_x = bx + bw / 2
                new_center_y = by + bh / 2
                correction_radius = max(
                    160.0,
                    4.0
                    * max(
                        float(approximate_bounds.get("width") or 0),
                        float(approximate_bounds.get("height") or 0),
                    ),
                )
                correction_within_radius = (
                    (new_center_x - original_center_x) ** 2
                    + (new_center_y - original_center_y) ** 2
                ) ** 0.5 <= correction_radius
            if not overlaps_frozen_candidate and not correction_within_radius:
                raise ToolExecutionError(
                    "target_bounds 与冻结候选距离过远；首次裁片若证明原框偏移，"
                    "第二次检查只允许在有限邻域修正: "
                    f"{inventory_candidate_id}"
                )

        if not frame.is_file():
            raise ToolExecutionError(f"冻结原图不存在: {frame}")
        expected_sha = str(observation.get("sha256") or "")
        if expected_sha and hashlib.sha256(frame.read_bytes()).hexdigest() != expected_sha:
            raise ToolExecutionError("冻结原图 hash 与 scene 不一致")
        output_root_raw = scene.get("allowed_output_root")
        if not output_root_raw:
            raise ToolExecutionError("scene 缺少 allowed_output_root")
        output_root = Path(str(output_root_raw)).resolve()
        inspections = scene.setdefault("_region_inspections", {})
        max_region_inspections = _int_or_none(scene.get("max_region_inspections"))
        if max_region_inspections is None:
            max_region_inspections = max(
                8,
                (_int_or_none(scene.get("max_suggestions")) or 1) * 3,
            )
        if len(inspections) >= max_region_inspections:
            raise ToolExecutionError(
                "区域检查预算已用尽；停止扩散搜索，使用已查看裁片提交合法候选或结束。"
                f" used={len(inspections)}, limit={max_region_inspections}"
            )
        if inventory_candidate is not None:
            existing_for_candidate = [
                item
                for item in inspections.values()
                if isinstance(item, dict)
                and item.get("candidate_id") == inventory_candidate_id
            ]
            if len(existing_for_candidate) >= 2:
                raise ToolExecutionError(
                    f"同一候选最多检查两次: {inventory_candidate_id}"
                )
            inventory_items = (
                (scene.get("_probe_candidate_inventory") or {}).get("candidates") or []
            )
            uninspected_other = [
                item
                for item in inventory_items
                if isinstance(item, dict)
                and item.get("id") != inventory_candidate_id
                and item.get("interaction_support_kind") != "unverified_visual"
                and not any(
                    isinstance(inspection, dict)
                    and inspection.get("candidate_id") == item.get("id")
                    for inspection in inspections.values()
                )
            ]
            remaining_before = max_region_inspections - len(inspections)
            if (
                existing_for_candidate
                and uninspected_other
                and remaining_before <= len(uninspected_other)
            ):
                raise ToolExecutionError(
                    "候选修框不能挤占尚未检查候选的保留槽位: "
                    + ", ".join(str(item.get("id")) for item in uninspected_other)
                )
            remaining_after = max_region_inspections - (len(inspections) + 1)
            if remaining_after < len(uninspected_other):
                raise ToolExecutionError(
                    "本次检查会挤占其他冻结候选的保留槽位；先检查未覆盖候选"
                )
        pending_inspections = [
            item
            for item in inspections.values()
            if isinstance(item, dict)
            and str(Path(str(item.get("path") or "")).resolve()) not in seen_paths
        ]
        if pending_inspections:
            raise ToolExecutionError(
                "上一张目标框裁片尚未真实送达模型上下文；每轮只允许排队一张，"
                "先查看下一轮图片再继续检查或提交候选"
            )
        inspection_id = f"region.{uuid.uuid4().hex[:16]}"
        destination = output_root / "region-inspections" / f"{inspection_id}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(frame) as opened:
            source = opened.convert("RGB")
        full_frame_ocr = scene.get("_full_frame_ocr")
        if not isinstance(full_frame_ocr, dict):
            full_all_tokens, full_complete_tokens = _complete_ocr_tokens(source)
            full_frame_ocr = {
                "all_tokens": full_all_tokens,
                "complete_text_tokens": full_complete_tokens,
            }
            scene["_full_frame_ocr"] = full_frame_ocr
        padding = max(192, round(max(bw, bh) * 0.75))
        padding = min(320, padding)
        cx1, cy1 = max(0, bx - padding), max(0, by - padding)
        cx2, cy2 = min(width, bx + bw + padding), min(height, by + bh + padding)
        context = source.crop((cx1, cy1, cx2, cy2))
        inside_text_tokens: list[dict[str, Any]] = []
        adjacent_text_tokens: list[dict[str, Any]] = []
        intersecting_incomplete_text_tokens: list[dict[str, Any]] = []
        intersecting_rects: list[tuple[float, float, float, float]] = []
        for token in full_frame_ocr.get("complete_text_tokens") or []:
            polygon = token.get("polygon") or []
            if not polygon:
                continue
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            fully_in_context = (
                cx1 <= left and right <= cx2 and cy1 <= top and bottom <= cy2
            )
            fully_in_target = (
                bx <= left
                and right <= bx + bw
                and by <= top
                and bottom <= by + bh
            )
            overlaps_target = not (
                right <= bx or left >= bx + bw or bottom <= by or top >= by + bh
            )
            if fully_in_target:
                inside_text_tokens.append(token)
            elif overlaps_target:
                intersecting_incomplete_text_tokens.append(token)
                intersecting_rects.append((left, top, right, bottom))
            elif fully_in_context and not overlaps_target:
                adjacent_text_tokens.append(token)
        suggested_target_bounds = None
        if intersecting_rects:
            suggested_left = max(
                0,
                int(min([float(bx), *[rect[0] for rect in intersecting_rects]])),
            )
            suggested_top = max(
                0,
                int(min([float(by), *[rect[1] for rect in intersecting_rects]])),
            )
            suggested_right = min(
                width,
                int(max([float(bx + bw), *[rect[2] for rect in intersecting_rects]]) + 0.9999),
            )
            suggested_bottom = min(
                height,
                int(max([float(by + bh), *[rect[3] for rect in intersecting_rects]]) + 0.9999),
            )
            suggested_target_bounds = {
                "x": suggested_left,
                "y": suggested_top,
                "width": suggested_right - suggested_left,
                "height": suggested_bottom - suggested_top,
            }
        context_draw = ImageDraw.Draw(context)
        grid_step = 50
        first_grid_x = ((cx1 + grid_step - 1) // grid_step) * grid_step
        first_grid_y = ((cy1 + grid_step - 1) // grid_step) * grid_step
        for source_x in range(first_grid_x, cx2, grid_step):
            local_x = source_x - cx1
            context_draw.line(
                (local_x, 0, local_x, context.height - 1),
                fill=(64, 176, 210),
                width=1,
            )
            context_draw.text(
                (local_x + 2, 2),
                f"X{source_x}",
                fill=(255, 255, 0),
                stroke_width=1,
                stroke_fill=(0, 0, 0),
            )
        for source_y in range(first_grid_y, cy2, grid_step):
            local_y = source_y - cy1
            context_draw.line(
                (0, local_y, context.width - 1, local_y),
                fill=(210, 126, 64),
                width=1,
            )
            context_draw.text(
                (2, local_y + 2),
                f"Y{source_y}",
                fill=(255, 255, 0),
                stroke_width=1,
                stroke_fill=(0, 0, 0),
            )
        context_draw.rectangle(
            (bx - cx1, by - cy1, bx + bw - cx1 - 1, by + bh - cy1 - 1),
            outline=(255, 64, 64),
            width=5,
        )
        exact = source.crop((bx, by, bx + bw, by + bh))
        crop_ocr_tokens, crop_edge_complete_tokens = _complete_ocr_tokens(exact)
        complete_texts = [item["text"] for item in inside_text_tokens]
        scale = min(3.0, 1100 / max(1, exact.height), 520 / max(1, exact.width))
        if scale > 1.0:
            exact = exact.resize(
                (max(1, round(exact.width * scale)), max(1, round(exact.height * scale))),
                Image.Resampling.LANCZOS,
            )
        exact_draw = ImageDraw.Draw(exact)
        exact_draw.rectangle(
            (0, 0, exact.width - 1, exact.height - 1),
            outline=(255, 48, 48),
            width=7,
        )
        margin, header, gap = 24, 76, 24
        canvas = Image.new(
            "RGB",
            (
                margin * 2 + context.width + gap + exact.width,
                header + margin + max(context.height, exact.height),
            ),
            (28, 30, 35),
        )
        canvas.paste(context, (margin, header))
        canvas.paste(exact, (margin + context.width + gap, header))
        canvas_draw = ImageDraw.Draw(canvas)
        canvas_draw.text((margin, 16), f"CONTEXT {bx},{by},{bw},{bh}", fill="white")
        canvas_draw.text(
            (margin + context.width + gap, 16),
            "EXACT TARGET PIXELS - RED BORDER IS THE HARD LIMIT",
            fill="white",
        )
        canvas_draw.text(
            (margin + context.width + gap, 38),
            f"COMPLETE OCR TOKEN COUNT: {len(complete_texts)}",
            fill="white",
        )
        canvas.save(destination, format="PNG", optimize=True)
        raw = destination.read_bytes()
        if len(raw) > 4_000_000:
            destination.unlink(missing_ok=True)
            raise ToolExecutionError("目标框检查图超过 4 MB；缩小目标框后重试")
        metadata = {
            "schema": "hypothesis.region-inspection.v1",
            "id": inspection_id,
            "candidate_id": inventory_candidate_id or None,
            "path": str(destination),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "source_artifact_id": observation.get("artifact_id"),
            "source_sha256": observation.get("sha256"),
            "target_bounds": bounds,
            "ocr": {
                "engine": "rapidocr_onnxruntime",
                "crop_ocr_tokens": crop_ocr_tokens,
                "crop_edge_complete_text_tokens": crop_edge_complete_tokens,
                "complete_text_tokens": inside_text_tokens,
                "adjacent_complete_text_tokens": adjacent_text_tokens,
                "intersecting_incomplete_text_tokens": intersecting_incomplete_text_tokens,
                "suggested_target_bounds": suggested_target_bounds,
                "full_frame_complete_text_tokens": (
                    full_frame_ocr.get("complete_text_tokens") or []
                ),
            },
        }
        destination.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        inspections[inspection_id] = metadata
        if inventory_candidate is not None:
            inventory_candidate["inspection_count"] = len(
                [
                    item
                    for item in inspections.values()
                    if isinstance(item, dict)
                    and item.get("candidate_id") == inventory_candidate_id
                ]
            )
            inventory_candidate["status"] = "inspected"

        pending = getattr(ctx, "pending_image_attachments", None)
        if pending is None:
            pending = []
            setattr(ctx, "pending_image_attachments", pending)
        pending.append(
            {
                "path": str(destination),
                "name": destination.name,
                "mime": "image/png",
                "base64": base64.b64encode(raw).decode("ascii"),
                "size": len(raw),
                "note": (
                    f"region_inspection_id={inspection_id}; 左侧上下文红框；"
                    "右侧精确裁片的闭合红框是硬边界，只有其中像素可写成框内线索"
                ),
            }
        )
        pending_paths = getattr(ctx, "hypothesis_pending_image_paths", None)
        if isinstance(pending_paths, list):
            pending_paths.append(str(destination))
        return (
            f"[IMAGE_QUEUED] {destination.name} ({len(raw)} bytes, image/png) — "
            f"region_inspection_id={inspection_id}; "
            f"complete_text_tokens={json.dumps(complete_texts, ensure_ascii=False)}; "
            "intersecting_incomplete_text_tokens="
            f"{json.dumps([item['text'] for item in intersecting_incomplete_text_tokens], ensure_ascii=False)}; "
            "suggested_target_bounds="
            f"{json.dumps(suggested_target_bounds, ensure_ascii=False)}; "
            "adjacent_text_tokens="
            f"{json.dumps([item['text'] for item in adjacent_text_tokens], ensure_ascii=False)}; "
            "下一轮看到裁片后再提交 propose_probe"
        )


class ProposeProbeRouter(SingleToolRouter):
    """Append one game-UI probe suggestion without executing it."""

    TOOL_NAME: ClassVar[str] = "propose_probe"
    DESCRIPTION: ClassVar[str] = (
        "在 hypothesis 的 game-ui shadow scene 中提交一条待主 Agent 审核的交互建议。"
        "本工具只追加建议账本，不连接设备、不执行动作。提交前必须 read_image 观察当前完整截图。"
        "几何输入遵循 scene.coordinate_space，可选 source_pixels 或 normalized_1000。"
        "tap 必须提供点击点和目标框；swipe 与 two_finger_swipe 必须提供完整起止点；"
        "pinch 必须提供中心、方向、幅度和手势表面。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "target_name": {"type": "string", "description": "人类可读的游戏内目标名称"},
            "visible_cue": {
                "type": "string",
                "description": "目标框内直接可见的文字、图标形状、颜色与相邻锚点",
            },
            "visible_text_tokens": {
                "type": "array",
                "items": {"type": "string"},
                "description": "visible_cue 实际转写的框内 UI 文字；必须来自 region inspection 完整 OCR token",
            },
            "adjacent_text_tokens": {
                "type": "array",
                "items": {"type": "string"},
                "description": "visible_cue 实际引用的框外邻接 UI 文字；必须来自检查上下文邻接 OCR token",
            },
            "target_fully_enclosed": {
                "type": "boolean",
                "description": "右侧精确裁片是否完整包住目标；只看到局部或边缘时必须为 false 并重新检查",
            },
            "gesture_surface_excludes_instruction": {
                "type": "boolean",
                "description": "多点手势框是否只圈实际操作表面并排除说明文字区域",
            },
            "action_type": {
                "type": "string",
                "enum": [
                    "tap",
                    "swipe",
                    "pinch",
                    "two_finger_swipe",
                    "wait",
                    "back",
                    "stop",
                ],
            },
            "coordinate_space": {
                "type": "string",
                "enum": ["source_pixels", "normalized_1000"],
                "description": "必须与 scene.coordinate_space 一致",
            },
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "x2": {"type": "integer"},
            "y2": {"type": "integer"},
            "duration_ms": {"type": "integer"},
            "pinch_direction": {"type": "string", "enum": ["in", "out"]},
            "pinch_percent": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            "pinch_steps": {"type": "integer", "minimum": 2, "maximum": 60},
            "two_finger_offset_x": {"type": "integer"},
            "two_finger_offset_y": {"type": "integer"},
            "two_finger_steps": {"type": "integer", "minimum": 2, "maximum": 60},
            "target_bounds": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["x", "y", "width", "height"],
            },
            "expected_change": {"type": "string"},
            "rationale": {"type": "string"},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "candidate_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本建议实际复核并采用的 visual_candidate_manifest 候选 id",
            },
            "prior_fact_id": {
                "type": "string",
                "description": "scene 启用既有事实精确模式时必须引用的 prior_verified_targets.id",
            },
            "region_inspection_id": {
                "type": "string",
                "description": "scene 要求框选复核时，由 inspect_probe_region 返回的检查 id",
            },
            "inventory_candidate_id": {
                "type": "string",
                "description": "启用候选清单门时必须引用的冻结 candidate id",
            },
            "interaction_support_kind": {
                "type": "string",
                "enum": [
                    "prior_verified_target",
                    "isolated_overlay_container",
                    "ui_control_group_membership",
                    "reviewed_visual_manifest_candidate",
                    "explicit_gesture_instruction",
                    "pending_visual_review",
                    "unverified_visual",
                ],
            },
        },
        "required": [
            "target_name",
            "visible_cue",
            "action_type",
            "coordinate_space",
            "x",
            "y",
            "target_bounds",
            "expected_change",
            "rationale",
            "risk_flags",
        ],
    }
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file_bytes",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.append_jsonl",)
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    @staticmethod
    def _validate_geometry(
        args: dict[str, Any], *, width: int, height: int,
    ) -> list[str]:
        issues: list[str] = []
        action_type = str(args.get("action_type") or "")
        x, y = _int_or_none(args.get("x")), _int_or_none(args.get("y"))
        x2, y2 = _int_or_none(args.get("x2")), _int_or_none(args.get("y2"))

        points: list[tuple[str, int | None, int | None]] = []
        if action_type in {"tap", "swipe", "pinch", "two_finger_swipe"}:
            points.append(("start", x, y))
        if action_type in {"swipe", "two_finger_swipe"}:
            points.append(("end", x2, y2))
        if action_type == "two_finger_swipe":
            offset_x = _int_or_none(args.get("two_finger_offset_x")) or 0
            offset_y = _int_or_none(args.get("two_finger_offset_y")) or 0
            if offset_x == 0 and offset_y == 0:
                issues.append("two-finger offset is missing")
            points.extend(
                [
                    ("second-finger start", None if x is None else x + offset_x, None if y is None else y + offset_y),
                    ("second-finger end", None if x2 is None else x2 + offset_x, None if y2 is None else y2 + offset_y),
                ]
            )
        if action_type == "pinch":
            if args.get("pinch_direction") not in {"in", "out"}:
                issues.append("pinch direction is missing")
            try:
                percent = float(args.get("pinch_percent"))
            except (TypeError, ValueError):
                percent = 0
            if not 0 < percent <= 1:
                issues.append("pinch percent is invalid")
        for label, px, py in points:
            if px is None or py is None:
                issues.append(f"{label} point is missing")
            elif not (0 <= px < width and 0 <= py < height):
                issues.append(f"{label} point is outside viewport")

        bounds = args.get("target_bounds")
        if action_type in {"tap", "swipe", "pinch", "two_finger_swipe"}:
            if not isinstance(bounds, dict):
                issues.append(f"{action_type} target bounds are missing")
            else:
                bx = _int_or_none(bounds.get("x"))
                by = _int_or_none(bounds.get("y"))
                bw = _int_or_none(bounds.get("width"))
                bh = _int_or_none(bounds.get("height"))
                if None in {bx, by, bw, bh} or (bw or 0) <= 0 or (bh or 0) <= 0:
                    issues.append(f"{action_type} target bounds are invalid")
                elif (bx or 0) + (bw or 0) > width or (by or 0) + (bh or 0) > height:
                    issues.append(f"{action_type} target bounds extend outside viewport")
                else:
                    if action_type in {"tap", "pinch"}:
                        action_points = [(action_type, x, y)]
                    else:
                        action_points = [("swipe start", x, y), ("swipe end", x2, y2)]
                        if action_type == "two_finger_swipe":
                            offset_x = _int_or_none(args.get("two_finger_offset_x")) or 0
                            offset_y = _int_or_none(args.get("two_finger_offset_y")) or 0
                            action_points.extend(
                                [
                                    ("second-finger start", None if x is None else x + offset_x, None if y is None else y + offset_y),
                                    ("second-finger end", None if x2 is None else x2 + offset_x, None if y2 is None else y2 + offset_y),
                                ]
                            )
                    for label, px, py in action_points:
                        if px is not None and py is not None and not (
                            bx <= px < bx + bw and by <= py < by + bh
                        ):
                            issues.append(f"{label} point is outside target bounds")
        return issues

    @staticmethod
    def _source_geometry(
        args: dict[str, Any], *, width: int, height: int,
    ) -> dict[str, Any]:
        coordinate_space = str(args.get("coordinate_space") or "")
        if coordinate_space not in {"source_pixels", "normalized_1000"}:
            raise ToolExecutionError(
                "coordinate_space 必须为 source_pixels 或 normalized_1000"
            )

        def scale(value: Any, extent: int, field_name: str) -> int | None:
            normalized = _int_or_none(value)
            if normalized is None:
                return None
            if coordinate_space == "source_pixels":
                if not 0 <= normalized < extent:
                    raise ToolExecutionError(
                        f"{field_name}={normalized} 超出源像素范围 0..{extent - 1}"
                    )
                return normalized
            if not 0 <= normalized < 1000:
                suggested = round(normalized * 1000 / extent) if 0 <= normalized < extent else None
                hint = f"；若 {normalized} 是源像素，normalized 值约为 {suggested}" if suggested is not None else ""
                raise ToolExecutionError(
                    f"{field_name}={normalized} 超出 normalized_1000 的 0..999{hint}"
                )
            return min(extent - 1, round(normalized * extent / 1000))

        def scale_offset(value: Any, extent: int, field_name: str) -> int:
            normalized = _int_or_none(value)
            if normalized is None:
                return 0
            if coordinate_space == "source_pixels":
                if not -extent < normalized < extent:
                    raise ToolExecutionError(
                        f"{field_name}={normalized} 超出源像素偏移范围"
                    )
                return normalized
            if not -1000 < normalized < 1000:
                raise ToolExecutionError(
                    f"{field_name}={normalized} 超出 normalized_1000 偏移范围"
                )
            return round(normalized * extent / 1000)

        source = dict(args)
        action_type = str(args.get("action_type") or "")
        source["x"] = scale(args.get("x"), width, "x")
        source["y"] = scale(args.get("y"), height, "y")
        source["x2"] = (
            scale(args.get("x2"), width, "x2")
            if action_type in {"swipe", "two_finger_swipe"}
            else None
        )
        source["y2"] = (
            scale(args.get("y2"), height, "y2")
            if action_type in {"swipe", "two_finger_swipe"}
            else None
        )
        source["two_finger_offset_x"] = (
            scale_offset(args.get("two_finger_offset_x"), width, "two_finger_offset_x")
            if action_type == "two_finger_swipe"
            else 0
        )
        source["two_finger_offset_y"] = (
            scale_offset(args.get("two_finger_offset_y"), height, "two_finger_offset_y")
            if action_type == "two_finger_swipe"
            else 0
        )
        bounds = args.get("target_bounds")
        if isinstance(bounds, dict):
            bx = scale(bounds.get("x"), width, "target_bounds.x")
            by = scale(bounds.get("y"), height, "target_bounds.y")
            bw_normalized = _int_or_none(bounds.get("width"))
            bh_normalized = _int_or_none(bounds.get("height"))
            if bw_normalized is None or bh_normalized is None:
                source["target_bounds"] = bounds
            elif coordinate_space == "source_pixels" and not (
                0 < bw_normalized <= width and 0 < bh_normalized <= height
            ):
                raise ToolExecutionError("source_pixels 目标框尺寸超出源画面")
            elif coordinate_space == "normalized_1000" and not (
                0 < bw_normalized <= 1000 and 0 < bh_normalized <= 1000
            ):
                raise ToolExecutionError("normalized_1000 目标框尺寸必须位于 1..1000")
            else:
                source["target_bounds"] = {
                    "x": bx,
                    "y": by,
                    "width": (
                        bw_normalized
                        if coordinate_space == "source_pixels"
                        else max(1, round(bw_normalized * width / 1000))
                    ),
                    "height": (
                        bh_normalized
                        if coordinate_space == "source_pixels"
                        else max(1, round(bh_normalized * height / 1000))
                    ),
                }
                if action_type == "tap" and bx is not None and by is not None:
                    source["x"] = bx + source["target_bounds"]["width"] // 2
                    source["y"] = by + source["target_bounds"]["height"] // 2
        return source

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        scene = getattr(ctx, "hypothesis_scene", None)
        if not isinstance(scene, dict) or scene.get("mode") != "shadow":
            raise ToolExecutionError("propose_probe 只允许在 hypothesis shadow scene 中使用")
        if scene.get("kind") != "game-ui-exploration":
            raise ToolExecutionError("scene.kind 必须为 game-ui-exploration")

        action_type = str(args.get("action_type") or "").strip()
        if not action_type:
            raise ToolExecutionError(
                "action_type 必填；结构不完整的候选不会写入建议账本，也不会消耗预算"
            )

        observation = scene.get("observation") or {}
        frame_path = Path(str(observation.get("frame_path") or "")).resolve()
        required_paths = _required_grounding_paths(scene)
        seen_paths = {
            str(Path(str(item)).resolve())
            for item in (getattr(ctx, "hypothesis_seen_image_paths", None) or set())
        }
        missing_paths = [str(item) for item in required_paths if str(item) not in seen_paths]
        if missing_paths:
            raise ToolExecutionError(
                "必需的截图尚未全部成功附着到模型上下文；先单独调用 read_image，"
                f"在下一轮看到图片后再提交建议。missing={missing_paths}"
            )
        viewport = observation.get("viewport") or {}
        width = _int_or_none(viewport.get("width")) or 0
        height = _int_or_none(viewport.get("height")) or 0
        if width <= 0 or height <= 0:
            raise ToolExecutionError("scene observation 缺少有效源画面尺寸")
        expected_coordinate_space = str(
            scene.get("coordinate_space") or "normalized_1000"
        )
        if args.get("coordinate_space") != expected_coordinate_space:
            raise ToolExecutionError(
                "coordinate_space 必须与 scene 一致: "
                f"expected={expected_coordinate_space}, got={args.get('coordinate_space')}"
            )
        source_args = self._source_geometry(args, width=width, height=height)
        geometry_issues = self._validate_geometry(
            source_args,
            width=width,
            height=height,
        )
        if geometry_issues:
            raise ToolExecutionError(
                "结构不完整或坐标无效，候选未写入账本: " + "; ".join(geometry_issues)
            )

        region_inspection_id = str(args.get("region_inspection_id") or "").strip()
        region_inspection: dict[str, Any] | None = None
        if scene.get("require_region_inspection"):
            inspections = scene.get("_region_inspections") or {}
            if not region_inspection_id:
                source_bounds = source_args.get("target_bounds") or {}
                exact_matches = [
                    (str(inspection_id), inspection)
                    for inspection_id, inspection in inspections.items()
                    if isinstance(inspection, dict)
                    and _bounds_equal(
                        inspection.get("target_bounds") or {},
                        source_bounds,
                    )
                ]
                if not exact_matches:
                    raise ToolExecutionError(
                        "本 scene 要求先用 inspect_probe_region 查看同一目标框；"
                        "未找到 target_bounds 完全一致的当前 session 裁片，"
                        "region_inspection_id 必填"
                    )
                inspection_hashes = {
                    str(inspection.get("sha256") or "").strip()
                    for _, inspection in exact_matches
                }
                if "" in inspection_hashes or len(inspection_hashes) != 1:
                    raise ToolExecutionError(
                        "当前 session 存在 target_bounds 相同但裁片哈希不一致或缺失的检查；"
                        "无法安全补全 region_inspection_id，请显式引用正确检查"
                    )
                delivered_matches = [
                    (inspection_id, inspection)
                    for inspection_id, inspection in exact_matches
                    if str(
                        Path(str(inspection.get("path") or "")).resolve()
                    )
                    in seen_paths
                ]
                if not delivered_matches:
                    raise ToolExecutionError(
                        "目标框的同坐标裁片尚未真实送达模型上下文；"
                        "下一轮看到图片后再提交"
                    )
                # Dict insertion order preserves inspection chronology.  When the
                # same exact crop was inspected repeatedly and every rendered crop
                # has the same hash, use the latest one that was actually delivered.
                region_inspection_id, region_inspection = delivered_matches[-1]
            region_inspection = inspections.get(region_inspection_id)
            if not isinstance(region_inspection, dict):
                raise ToolExecutionError(
                    f"region_inspection_id 不属于当前 session: {region_inspection_id}"
                )
            inspected_bounds = region_inspection.get("target_bounds") or {}
            source_bounds = source_args.get("target_bounds") or {}
            if not _bounds_equal(inspected_bounds, source_bounds):
                raise ToolExecutionError(
                    "propose_probe 的 target_bounds 与已查看裁片不一致；"
                    "必须重新 inspect_probe_region"
                )
            inspected_path = str(Path(str(region_inspection.get("path") or "")).resolve())
            if inspected_path not in seen_paths:
                raise ToolExecutionError(
                    "目标框裁片尚未真实送达模型上下文；下一轮看到图片后再提交"
                )

        inventory_candidate_id = str(args.get("inventory_candidate_id") or "").strip()
        inventory_candidate: dict[str, Any] | None = None
        interaction_support_kind = str(args.get("interaction_support_kind") or "").strip()
        if scene.get("require_candidate_inventory"):
            inventory = scene.get("_probe_candidate_inventory") or {}
            inventory_items = {
                str(item.get("id") or ""): item
                for item in inventory.get("candidates") or []
                if isinstance(item, dict) and item.get("id")
            }
            if not inventory_candidate_id:
                raise ToolExecutionError(
                    "启用候选清单门时 propose_probe 必须传 inventory_candidate_id"
                )
            inventory_candidate = inventory_items.get(inventory_candidate_id)
            if inventory_candidate is None:
                raise ToolExecutionError(
                    f"inventory_candidate_id 不属于冻结候选清单: {inventory_candidate_id}"
                )
            if inventory_candidate.get("action_family") != action_type:
                raise ToolExecutionError(
                    "提案动作与冻结候选动作家族不一致: "
                    f"expected={inventory_candidate.get('action_family')}, got={action_type}"
                )
            if inventory_candidate.get("interaction_support_kind") == "unverified_visual":
                raise ToolExecutionError(
                    "缺少交互支持的可见图形只保留在冻结清单，不写入 actionable ledger"
                )
            if not isinstance(region_inspection, dict) or (
                region_inspection.get("candidate_id") != inventory_candidate_id
            ):
                raise ToolExecutionError(
                    "提案必须引用同一冻结候选的 region inspection"
                )
            candidate_inspections = [
                (str(inspection_id), item)
                for inspection_id, item in (
                    scene.get("_region_inspections") or {}
                ).items()
                if isinstance(item, dict)
                and item.get("candidate_id") == inventory_candidate_id
            ]
            candidate_inspection_count = len(candidate_inspections)
            required_inspection_count = _int_or_none(
                scene.get("min_region_inspections_per_candidate")
            ) or 1
            if candidate_inspection_count < required_inspection_count:
                raise ToolExecutionError(
                    "该候选尚未达到要求的独立裁片检查次数；继续用同一 candidate_id "
                    "inspect 后再提交: "
                    f"candidate={inventory_candidate_id}, "
                    f"used={candidate_inspection_count}, "
                    f"required={required_inspection_count}"
                )
            if (
                scene.get("require_expanding_target_confirmation")
                and action_type == "tap"
                and candidate_inspection_count >= 2
            ):
                latest_inspection_id, latest_inspection = candidate_inspections[-1]
                if region_inspection_id != latest_inspection_id:
                    raise ToolExecutionError(
                        "完整目标确认必须引用同一候选最后一次修框裁片；"
                        "不能在看到第二张裁片后退回更小或更早的框"
                    )
                first_bounds = candidate_inspections[0][1].get("target_bounds") or {}
                latest_bounds = latest_inspection.get("target_bounds") or {}
                expansion = _int_or_none(scene.get("min_target_expansion_px")) or 20
                first_left = int(first_bounds.get("x") or 0)
                first_top = int(first_bounds.get("y") or 0)
                first_right = first_left + int(first_bounds.get("width") or 0)
                first_bottom = first_top + int(first_bounds.get("height") or 0)
                latest_left = int(latest_bounds.get("x") or 0)
                latest_top = int(latest_bounds.get("y") or 0)
                latest_right = latest_left + int(latest_bounds.get("width") or 0)
                latest_bottom = latest_top + int(latest_bounds.get("height") or 0)
                required_left = max(0, first_left - expansion)
                required_top = max(0, first_top - expansion)
                required_right = min(width, first_right + expansion)
                required_bottom = min(height, first_bottom + expansion)
                if not (
                    latest_left <= required_left
                    and latest_top <= required_top
                    and latest_right >= required_right
                    and latest_bottom >= required_bottom
                ):
                    raise ToolExecutionError(
                        "完整目标确认的第二张裁片必须在首张裁片四周保留足够上下文；"
                        "仅平移、缩小或轻微扩框不能证明图标/按钮未被红框截断。"
                        f" candidate={inventory_candidate_id}, "
                        f"required_at_least={{'x': {required_left}, 'y': {required_top}, "
                        f"'right': {required_right}, 'bottom': {required_bottom}}}, "
                        f"latest={latest_bounds}"
                    )
            frozen_support_kind = str(
                inventory_candidate.get("interaction_support_kind") or ""
            )
            supplied_support = str(args.get("interaction_support_kind") or "").strip()
            if frozen_support_kind == "pending_visual_review":
                promotable_support = {
                    "isolated_overlay_container",
                    "ui_control_group_membership",
                    "reviewed_visual_manifest_candidate",
                    "explicit_gesture_instruction",
                }
                if supplied_support not in promotable_support:
                    raise ToolExecutionError(
                        "pending_visual_review 候选在裁片复核后必须提交明确的交互支持类型"
                    )
                interaction_support_kind = supplied_support
                inventory_candidate["resolved_interaction_support_kind"] = supplied_support
                inventory_candidate["status"] = "supported_after_visual_review"
            else:
                interaction_support_kind = frozen_support_kind
            if (
                frozen_support_kind != "pending_visual_review"
                and supplied_support
                and supplied_support != interaction_support_kind
            ):
                raise ToolExecutionError(
                    "interaction_support_kind 必须与冻结候选清单一致"
                )

        target_name = str(args.get("target_name") or "").strip()
        visible_cue = str(args.get("visible_cue") or "").strip()
        expected_change = str(args.get("expected_change") or "").strip()
        rationale = str(args.get("rationale") or "").strip()
        if not target_name or not visible_cue or not expected_change or not rationale:
            raise ToolExecutionError(
                "target_name、visible_cue、expected_change 与 rationale 必填"
            )
        if region_inspection is not None and action_type == "tap":
            clipped_markers = (
                "一部分",
                "部分",
                "边缘",
                "被截",
                "裁掉",
                "未完整",
                "只露出",
            )
            clipped_claims = [marker for marker in clipped_markers if marker in visible_cue]
            if clipped_claims:
                raise ToolExecutionError(
                    "tap 目标框只包含控件局部，必须修框并重新 inspect 后再提交: "
                    + ", ".join(clipped_claims)
                )
        if (
            scene.get("require_full_target_confirmation")
            and region_inspection is not None
            and args.get("target_fully_enclosed") is not True
        ):
            raise ToolExecutionError(
                "提交前必须明确确认右侧精确裁片完整包住目标；"
                "target_fully_enclosed 只有在完整可见时才能为 true"
            )
        if (
            scene.get("expected_change_mode") == "unverified"
            and not expected_change.startswith("待验证：")
        ):
            raise ToolExecutionError(
                "未知真实发现模式的 expected_change 必须以“待验证：”开头；"
                "不得把未执行结果写成事实"
            )
        visible_text_tokens = list(
            dict.fromkeys(
                str(item).strip()
                for item in args.get("visible_text_tokens") or []
                if str(item).strip()
            )
        )
        adjacent_text_tokens = list(
            dict.fromkeys(
                str(item).strip()
                for item in args.get("adjacent_text_tokens") or []
                if str(item).strip()
            )
        )
        if (
            scene.get("require_gesture_surface_separation")
            and action_type in {"pinch", "two_finger_swipe"}
            and region_inspection is not None
        ):
            gesture_terms = ("双指", "滑动", "放大", "缩小", "拖动", "平移")
            gesture_instruction_tokens = [
                str(item.get("text") or "")
                for item in (
                    (region_inspection.get("ocr") or {}).get("complete_text_tokens")
                    or []
                )
                if any(term in str(item.get("text") or "") for term in gesture_terms)
            ]
            if gesture_instruction_tokens:
                raise ToolExecutionError(
                    "多点手势 target_bounds 圈到了说明文字；动作支持写入 rationale，"
                    "target_bounds 必须改为实际手势表面并重新 inspect: "
                    + ", ".join(gesture_instruction_tokens)
                )
            if scene.get("require_text_free_gesture_surface"):
                complete_tokens = [
                    str(item.get("text") or "")
                    for item in (
                        (region_inspection.get("ocr") or {}).get(
                            "complete_text_tokens"
                        )
                        or []
                    )
                    if str(item.get("text") or "").strip()
                ]
                incomplete_tokens = [
                    str(item.get("text") or "")
                    for item in (
                        (region_inspection.get("ocr") or {}).get(
                            "intersecting_incomplete_text_tokens"
                        )
                        or []
                    )
                    if str(item.get("text") or "").strip()
                ]
                if complete_tokens or incomplete_tokens:
                    raise ToolExecutionError(
                        "本 fixture 要求手势表面不包含任何界面文字；"
                        "把 target_bounds 移到角色/场景画布的无字区域并重新 inspect。"
                        f" complete={complete_tokens}, intersecting={incomplete_tokens}"
                    )
            if args.get("gesture_surface_excludes_instruction") is not True:
                raise ToolExecutionError(
                    "多点手势提案必须确认手势表面排除了说明文字区域"
                )
        if (
            scene.get("target_naming_mode") == "visual-neutral"
            and not str(args.get("prior_fact_id") or "").strip()
        ):
            supported_text = [*visible_text_tokens, *adjacent_text_tokens]
            unsupported_name_terms = [
                term
                for term in _UNSUPPORTED_FUNCTION_TERMS
                if term in target_name
                and not any(term in token for token in supported_text)
            ]
            if unsupported_name_terms:
                raise ToolExecutionError(
                    "无文字或既有事实支撑的目标名称只能描述可见形状、位置或原文；"
                    "把功能假设移到以‘待验证：’开头的 expected_change。"
                    "当前无支撑功能词: "
                    + ", ".join(unsupported_name_terms)
                )
        if region_inspection is not None:
            allowed_text_tokens = [
                str(item.get("text") or "").strip()
                for item in (
                    (region_inspection.get("ocr") or {}).get(
                        "complete_text_tokens"
                    )
                    or []
                )
                if str(item.get("text") or "").strip()
            ]
            allowed_adjacent_tokens = [
                str(item.get("text") or "").strip()
                for item in (
                    (region_inspection.get("ocr") or {}).get(
                        "adjacent_complete_text_tokens"
                    )
                    or []
                )
                if str(item.get("text") or "").strip()
            ]
            unknown_text_tokens = [
                item for item in visible_text_tokens if item not in allowed_text_tokens
            ]
            if unknown_text_tokens:
                raise ToolExecutionError(
                    "visible_text_tokens 含有不在精确裁片完整 OCR 清单中的文字: "
                    + ", ".join(unknown_text_tokens)
                )
            unknown_adjacent_tokens = [
                item
                for item in adjacent_text_tokens
                if item not in allowed_adjacent_tokens
            ]
            if unknown_adjacent_tokens:
                raise ToolExecutionError(
                    "adjacent_text_tokens 含有不在检查上下文邻接 OCR 清单中的文字: "
                    + ", ".join(unknown_adjacent_tokens)
                )
            allowed_claim_tokens = [*allowed_text_tokens, *allowed_adjacent_tokens]
            quoted_claims: list[str] = []
            for pattern in (
                r'"([^"\n]+)"',
                r"'([^'\n]+)'",
                r"“([^”\n]+)”",
                r"‘([^’\n]+)’",
            ):
                quoted_claims.extend(re.findall(pattern, visible_cue))
            numeric_claims = re.findall(
                r"(?<![A-Za-z])\d+(?:\.\d+)?%?(?![A-Za-z])",
                visible_cue,
            )
            unsupported_quoted = [
                item for item in quoted_claims if item not in allowed_claim_tokens
            ]
            unsupported_numeric = [
                item
                for item in numeric_claims
                if not any(item in token for token in allowed_claim_tokens)
            ]
            if unsupported_quoted or unsupported_numeric:
                claims = list(dict.fromkeys([*unsupported_quoted, *unsupported_numeric]))
                raise ToolExecutionError(
                    "visible_cue 转写了精确裁片完整 OCR 清单之外的文字/数值: "
                    + ", ".join(claims)
                )
            declared_tokens = set([*visible_text_tokens, *adjacent_text_tokens])
            full_frame_tokens = [
                str(item.get("text") or "").strip()
                for item in (
                    (region_inspection.get("ocr") or {}).get(
                        "full_frame_complete_text_tokens"
                    )
                    or []
                )
                if len(str(item.get("text") or "").strip()) >= 2
            ]
            undeclared_ui_literals = [
                item
                for item in full_frame_tokens
                if item in visible_cue and item not in declared_tokens
            ]
            if undeclared_ui_literals:
                raise ToolExecutionError(
                    "visible_cue 含有未声明或跨区域挪用的 UI 原文；"
                    "请使用 visible_text_tokens / adjacent_text_tokens，或移除逐字文本: "
                    + ", ".join(dict.fromkeys(undeclared_ui_literals))
                )
            if scene.get("region_visible_cue_scope") == "exact_target_only":
                if adjacent_text_tokens:
                    raise ToolExecutionError(
                        "exact_target_only 模式要求 adjacent_text_tokens=[]"
                    )
                outside_markers = [
                    marker for marker in ("框外", "相邻") if marker in visible_cue
                ]
                if outside_markers:
                    raise ToolExecutionError(
                        "exact_target_only 模式的 visible_cue 只能描述红框内像素；"
                        "移除框外/相邻叙述: " + ", ".join(outside_markers)
                    )

        prior_fact_id = str(args.get("prior_fact_id") or "").strip()
        prior_fact: dict[str, Any] | None = None
        if scene.get("prior_fact_contract_mode") == "exact":
            prior_facts = {
                str(item.get("id") or ""): item
                for item in scene.get("prior_verified_targets") or []
                if isinstance(item, dict) and item.get("id")
            }
            if not prior_fact_id:
                raise ToolExecutionError(
                    "既有事实精确模式要求 prior_fact_id；候选未写入账本"
                )
            prior_fact = prior_facts.get(prior_fact_id)
            if prior_fact is None:
                raise ToolExecutionError(
                    f"prior_fact_id 不属于当前 scene: {prior_fact_id}"
                )
            fact_mismatches: list[str] = []
            if target_name != str(prior_fact.get("target_name") or ""):
                fact_mismatches.append("target_name")
            if action_type != str(prior_fact.get("action_type") or ""):
                fact_mismatches.append("action_type")
            if expected_change != str(prior_fact.get("observed_change") or ""):
                fact_mismatches.append("expected_change")
            prior_action = prior_fact.get("action") or {}
            for field in (
                "x",
                "y",
                "x2",
                "y2",
                "duration_ms",
                "pinch_steps",
                "two_finger_offset_x",
                "two_finger_offset_y",
                "two_finger_steps",
            ):
                if field in prior_action:
                    actual = (
                        _int_or_none(args.get(field))
                        if field in {
                            "duration_ms",
                            "pinch_steps",
                            "two_finger_steps",
                        }
                        else _int_or_none(source_args.get(field))
                    )
                    if actual != _int_or_none(prior_action.get(field)):
                        fact_mismatches.append(f"action.{field}")
            for field in ("pinch_direction", "pinch_percent"):
                if field in prior_action and args.get(field) != prior_action.get(field):
                    fact_mismatches.append(f"action.{field}")
            prior_bounds = prior_fact.get("target_bounds")
            if isinstance(prior_bounds, dict):
                actual_bounds = source_args.get("target_bounds")
                if not isinstance(actual_bounds, dict) or any(
                    _int_or_none(actual_bounds.get(field))
                    != _int_or_none(prior_bounds.get(field))
                    for field in ("x", "y", "width", "height")
                ):
                    fact_mismatches.append("target_bounds")
            if fact_mismatches:
                raise ToolExecutionError(
                    "提案扩写或改动了引用的既有事实；以下字段必须逐字/逐值一致: "
                    + ", ".join(fact_mismatches)
                )

        allowed_actions = {str(item) for item in scene.get("allowed_action_types") or []}
        policy_issues: list[str] = []
        if action_type != "stop" and action_type not in allowed_actions:
            policy_issues.append(f"action type is not allowed: {action_type}")

        risk_flags = [str(item).strip() for item in args.get("risk_flags") or [] if str(item).strip()]
        no_risk_values = {"无风险", "none", "no risk", "n/a", "na"}
        if any(item.casefold() in no_risk_values for item in risk_flags):
            raise ToolExecutionError("无风险时 risk_flags 必须传空数组 []")
        if risk_flags:
            policy_issues.append("proposal declares risk flags")
        target_folded = target_name.casefold()
        forbidden_terms = [
            str(item).strip() for item in scene.get("forbidden_target_terms") or []
            if str(item).strip()
        ]
        matched_forbidden = [item for item in forbidden_terms if item.casefold() in target_folded]
        if matched_forbidden:
            if scene.get("forbidden_target_policy") == "reject":
                raise ToolExecutionError(
                    "目标命中本 scene 的禁止词，候选不会写入账本: "
                    + ", ".join(matched_forbidden)
                )
            policy_issues.append("target matches forbidden terms: " + ", ".join(matched_forbidden))

        candidate_ids = list(dict.fromkeys(
            str(item).strip() for item in args.get("candidate_ids") or []
            if str(item).strip()
        ))
        visual_manifest = scene.get("visual_candidate_manifest") or {}
        candidates_by_id = {
            str(item.get("id") or ""): item
            for item in visual_manifest.get("candidates") or []
        }
        known_candidate_ids = set(candidates_by_id)
        unknown_candidate_ids = [
            item for item in candidate_ids if item not in known_candidate_ids
        ]
        if unknown_candidate_ids:
            raise ToolExecutionError(
                "candidate_ids 含有不属于当前冻结候选清单的 id: "
                + ", ".join(unknown_candidate_ids)
            )
        if (
            visual_manifest
            and scene.get("visual_candidate_policy") == "require-reference"
            and action_type == "tap"
            and not candidate_ids
            and not prior_fact_id
        ):
            raise ToolExecutionError(
                "本 scene 要求每个未知 tap 候选引用同一区域的 visual candidate id；"
                "未被冻结候选清单定位到的自由补点不会写入账本"
            )
        source_target_bounds = source_args.get("target_bounds")
        if candidate_ids and not isinstance(source_target_bounds, dict):
            raise ToolExecutionError("引用 candidate_ids 时必须提供有效目标框")
        mismatched_candidate_ids = [
            candidate_id
            for candidate_id in candidate_ids
            if _candidate_overlap_ratio(
                source_target_bounds,
                candidates_by_id[candidate_id].get("source_bounds") or {},
            ) < 0.5
        ]
        if mismatched_candidate_ids:
            raise ToolExecutionError(
                "candidate_ids 与提交的源像素目标框不在同一画面区域: "
                + ", ".join(mismatched_candidate_ids)
            )
        if prior_fact_id:
            interaction_support_kind = "prior_verified_target"
        if scene.get("require_interaction_support"):
            allowed_support = {
                "prior_verified_target",
                "isolated_overlay_container",
                "ui_control_group_membership",
                "reviewed_visual_manifest_candidate",
                "explicit_gesture_instruction",
            }
            if interaction_support_kind not in allowed_support:
                policy_issues.append("interaction support is unverified")
            if (
                interaction_support_kind == "explicit_gesture_instruction"
                and action_type not in {"pinch", "two_finger_swipe"}
            ):
                policy_issues.append("gesture instruction does not support this action type")
            if (
                interaction_support_kind == "reviewed_visual_manifest_candidate"
                and not candidate_ids
            ):
                policy_issues.append("reviewed visual support is missing candidate provenance")
            if (
                interaction_support_kind == "ui_control_group_membership"
                and not (inventory_candidate or {}).get("group_id")
            ):
                policy_issues.append("control-group support is missing group_id")

        output_root_raw = scene.get("allowed_output_root")
        ledger_raw = scene.get("suggestion_ledger")
        if not output_root_raw or not ledger_raw:
            raise ToolExecutionError("scene 缺少 allowed_output_root 或 suggestion_ledger")
        output_root = Path(str(output_root_raw)).resolve()
        ledger = Path(str(ledger_raw)).resolve()
        if not _path_under(ledger, output_root):
            raise ToolExecutionError("suggestion_ledger 位于允许输出根之外")

        session_id = str(getattr(ctx, "hyp_session_id", "") or ctx.trace_id)
        iteration = int(getattr(ctx, "hyp_iteration", 0) or 0)
        max_suggestions = int(scene.get("max_suggestions") or 20)
        with _APPEND_LOCK:
            existing_for_session = 0
            existing_records: list[dict[str, Any]] = []
            if ledger.is_file():
                for line in ledger.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("session_id") == session_id:
                        existing_for_session += 1
                        existing_records.append(item)
            if isinstance(source_target_bounds, dict):
                geometric_duplicates = [
                    str(item.get("id") or "")
                    for item in existing_records
                    if (item.get("action") or {}).get("type") == action_type
                    and isinstance(item.get("target_bounds"), dict)
                    and _candidate_overlap_ratio(
                        source_target_bounds,
                        item["target_bounds"],
                    )
                    >= 0.8
                ]
                if geometric_duplicates:
                    raise ToolExecutionError(
                        "候选与本 session 已记录目标几何重复；不要用改名或微调框重复提交: "
                        + ", ".join(geometric_duplicates)
                    )
            if existing_for_session >= max_suggestions:
                raise ToolExecutionError(
                    f"本 session 建议预算已用完: {existing_for_session}/{max_suggestions}"
                )

            evidence_ids = list(dict.fromkeys(
                [
                    str(observation.get("artifact_id") or ""),
                    *[str(item) for item in args.get("evidence_ids") or []],
                    *[
                        str(item)
                        for item in (prior_fact or {}).get("evidence_ids") or []
                    ],
                ]
            ))
            evidence_ids = [item for item in evidence_ids if item]
            action_payload = {
                "type": action_type,
                "x": _int_or_none(source_args.get("x")),
                "y": _int_or_none(source_args.get("y")),
                "x2": _int_or_none(source_args.get("x2")),
                "y2": _int_or_none(source_args.get("y2")),
                "duration_ms": _int_or_none(args.get("duration_ms")) or 250,
            }
            if action_type == "pinch":
                action_payload.update(
                    {
                        "pinch_direction": args.get("pinch_direction"),
                        "pinch_percent": args.get("pinch_percent"),
                        "pinch_steps": _int_or_none(args.get("pinch_steps")),
                    }
                )
            if action_type == "two_finger_swipe":
                action_payload.update(
                    {
                        "two_finger_offset_x": _int_or_none(
                            source_args.get("two_finger_offset_x")
                        ),
                        "two_finger_offset_y": _int_or_none(
                            source_args.get("two_finger_offset_y")
                        ),
                        "two_finger_steps": _int_or_none(args.get("two_finger_steps")),
                    }
                )
            record = {
                "schema": "game-observatory.exploration-probe.v2",
                "id": f"shadow.probe.{uuid.uuid4().hex}",
                "session_id": session_id,
                "iteration": iteration,
                "benchmark_run_id": scene.get("benchmark_run_id"),
                "proposed_at": _utc_now(),
                "status": "proposed",
                "executed": False,
                "eligible_for_execution": not policy_issues,
                "observation": {
                    "artifact_id": observation.get("artifact_id"),
                    "frame_path": observation.get("frame_path"),
                    "sha256": observation.get("sha256"),
                    "viewport": {"width": width, "height": height},
                },
                "target_name": target_name,
                "visible_cue": visible_cue,
                "visible_text_tokens": visible_text_tokens,
                "adjacent_text_tokens": adjacent_text_tokens,
                "action": action_payload,
                "target_bounds": source_args.get("target_bounds"),
                "input_geometry": {
                    "coordinate_space": str(args.get("coordinate_space") or ""),
                    "tap_point_strategy": (
                        "target_bounds_center" if action_type == "tap" else None
                    ),
                    "x": _int_or_none(args.get("x")),
                    "y": _int_or_none(args.get("y")),
                    "x2": _int_or_none(args.get("x2")),
                    "y2": _int_or_none(args.get("y2")),
                    "pinch_direction": args.get("pinch_direction"),
                    "pinch_percent": args.get("pinch_percent"),
                    "pinch_steps": _int_or_none(args.get("pinch_steps")),
                    "two_finger_offset_x": _int_or_none(args.get("two_finger_offset_x")),
                    "two_finger_offset_y": _int_or_none(args.get("two_finger_offset_y")),
                    "two_finger_steps": _int_or_none(args.get("two_finger_steps")),
                    "target_bounds": args.get("target_bounds"),
                },
                "expected_change": expected_change,
                "rationale": rationale,
                "risk_flags": risk_flags,
                "policy_issues": policy_issues,
                "evidence_ids": evidence_ids,
                "generator": {
                    "component": "hypothesis.Experimenter",
                    "trace_id": ctx.trace_id,
                    "prior_fact_id": prior_fact_id or None,
                    "inventory_candidate_id": inventory_candidate_id or None,
                    "interaction_support_kind": interaction_support_kind or None,
                    "region_inspection_id": region_inspection_id or None,
                    "target_fully_enclosed": args.get("target_fully_enclosed"),
                    "gesture_surface_excludes_instruction": args.get(
                        "gesture_surface_excludes_instruction"
                    ),
                    "region_inspection_sha256": (
                        (region_inspection or {}).get("sha256")
                    ),
                    "region_complete_text_tokens": [
                        item.get("text")
                        for item in (
                            ((region_inspection or {}).get("ocr") or {}).get(
                                "complete_text_tokens"
                            )
                            or []
                        )
                    ],
                    "region_adjacent_text_tokens": [
                        item.get("text")
                        for item in (
                            ((region_inspection or {}).get("ocr") or {}).get(
                                "adjacent_complete_text_tokens"
                            )
                            or []
                        )
                    ],
                    "visual_candidate_ids": candidate_ids,
                    "visual_candidate_manifest_sha256": visual_manifest.get(
                        "source_result_sha256"
                    ),
                },
            }
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            if inventory_candidate is not None:
                inventory_candidate["status"] = (
                    "proposed" if record["eligible_for_execution"] else "recorded_ineligible"
                )
                inventory_candidate["probe_id"] = record["id"]

        return json.dumps(
            {
                "id": record["id"],
                "recorded": True,
                "executed": False,
                "eligible_for_execution": record["eligible_for_execution"],
                "policy_issues": policy_issues,
                "ledger": str(ledger),
            },
            ensure_ascii=False,
        )


__all__ = [
    "DeclareProbeInventoryRouter",
    "InspectProbeRegionRouter",
    "ProposeProbeRouter",
]
