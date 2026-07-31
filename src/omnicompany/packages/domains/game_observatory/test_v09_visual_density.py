from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AFK_PARTIAL = (
    REPO_ROOT
    / "data"
    / "domains"
    / "game_observatory"
    / "drafts"
    / "afk-journey-rowan-hero-detail.partial.v1.json"
)
STUDIO_JS = (
    REPO_ROOT
    / "src"
    / "omnicompany"
    / "packages"
    / "domains"
    / "game_observatory"
    / "web"
    / "studio.js"
)
STUDIO_CSS = STUDIO_JS.with_name("studio.css")
PLAN_ROOT = (
    REPO_ROOT
    / "docs"
    / "plans"
    / "game-observatory"
    / "[2026-07-15]VISUAL-DENSITY-AND-AI-PLAYER-EFFICIENCY-v0.9"
)


def _bundle() -> dict[str, object]:
    return json.loads(AFK_PARTIAL.read_text(encoding="utf-8"))


def test_afk_design_visuals_cover_entry_loop_rules_tags_and_interaction_coordinates() -> None:
    bundle = _bundle()
    design = bundle["design_document"]
    entry_steps = design["entry_flow"]["steps"]

    assert len(entry_steps) == 2
    assert all(step["from_artifact_id"] and step["to_artifact_id"] for step in entry_steps)
    assert all(step["point"] and step["target_bounds"] for step in entry_steps)

    interactions = {item["id"]: item for item in bundle["interactions"]}
    assert len(design["core_loop"]) == 5
    for step in design["core_loop"]:
        assert step["screen_state_ids"]
        assert step["interaction_ids"]
        interaction = interactions[step["interaction_ids"][0]]
        assert len(
            [
                artifact_id
                for artifact_id in interaction["artifact_ids"]
                if not artifact_id.startswith("art.video.")
            ]
        ) >= 2

    assert len(design["state_rule_visuals"]) == len(design["state_rules"])
    assert all(item["artifact_id"] for item in design["state_rule_visuals"])
    assert len(bundle["play_tag_previews"]) == 5
    assert all(item["artifact_id"] for item in bundle["play_tag_previews"])

    strict_surface_ids = set(bundle["content_partition"]["strict_surface_ids"])
    strict_interactions = [
        item
        for item in bundle["interactions"]
        if item["from_state_id"] in strict_surface_ids
        and item["to_state_id"] in strict_surface_ids
    ]
    elements = {item["id"]: item for item in bundle["ui_elements"]}
    assert len(strict_interactions) == 43
    for interaction in strict_interactions:
        images = [
            artifact_id
            for artifact_id in interaction["artifact_ids"]
            if not artifact_id.startswith("art.video.")
        ]
        assert len(images) >= 2
        input_spec = interaction["input"]
        if input_spec["type"] != "tap":
            continue
        targets = input_spec.get("targets") or (
            [input_spec["target"]] if input_spec.get("target") else []
        )
        assert input_spec.get("point") or any(
            elements.get(target_id, {}).get("bounds") for target_id in targets
        )


def test_studio_has_real_image_networks_coordinate_overlays_and_compact_layout() -> None:
    script = STUDIO_JS.read_text(encoding="utf-8")
    styles = STUDIO_CSS.read_text(encoding="utf-8")

    assert "function entryFlowMarkup" in script
    assert "function playConnectionsMarkup" in script
    assert "function coreLoopNetworkMarkup" in script
    assert "function readerActionOverlay" in script
    assert "directActionOverlay(step.point, step.target_bounds, bundle)" in script
    assert "readerActionOverlay(interaction, bundle)" in script
    assert "family-interaction-evidence" in script
    assert "play-tag-preview-grid" in script
    assert "demo-preview-image" in script

    for class_name in (
        ".play-boundary-map",
        ".boundary-step-grid",
        ".play-connection-visual-grid",
        ".core-loop-network",
        ".family-interaction-evidence",
        ".play-tag-preview-grid",
        ".demo-list.has-images",
    ):
        assert class_name in styles

    assert ".play-admin { margin-top: 40px" in styles
    assert ".spec-section { scroll-margin-top: 100px; padding: 16px" in styles
    assert ".reader-interaction { padding: 12px" in styles
    assert "max-height: 360px" in styles
    assert "width:min(100%, ${Math.round(360 * width / height)}px)" in script


def test_v09_keeps_durable_visual_audit_and_ai_player_efficiency_review() -> None:
    visual_audit = (PLAN_ROOT / "visual-coverage-audit.md").read_text(encoding="utf-8")
    efficiency_review = (PLAN_ROOT / "ai-player-efficiency-review.md").read_text(
        encoding="utf-8"
    )

    assert "玩法设计案首屏" in visual_audit
    assert "玩家循环" in visual_audit
    assert "点击点和目标框叠图" in visual_audit
    assert "546 个 evidence step" in efficiency_review
    assert "完整留证数据面" in efficiency_review
    assert "模型感知数据面" in efficiency_review
    assert "视觉 token" in efficiency_review
    assert "状态缓存命中率" in efficiency_review