from __future__ import annotations

import json
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).parents[3] / ".agents" / "skills"
SKILL_NAMES = {
    "ai-player-orchestrator",
    "game-state-recognition",
    "game-frontier-explorer",
    "gameplay-discovery",
    "game-skill-crystallizer",
    "game-memory-consolidator",
    "game-task-curator",
    "game-guide-research",
    "game-recovery",
    "game-evidence-recorder",
    "game-ai-account-voice",
    "ai-player-benchmark",
}
EXPLICIT_ONLY = {
    "game-frontier-explorer",
    "game-skill-crystallizer",
    "game-guide-research",
    "game-ai-account-voice",
    "ai-player-benchmark",
}


def test_all_ai_player_skill_packages_are_versioned_and_have_forward_fixtures():
    for name in sorted(SKILL_NAMES):
        root = SKILL_ROOT / name
        skill_text = (root / "SKILL.md").read_text(encoding="utf-8")
        metadata = yaml.safe_load((root / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        fixture = json.loads(
            (root / "fixtures" / "forward_test.json").read_text(encoding="utf-8")
        )
        assert f"name: {name}" in skill_text
        assert "Contract version: `1.0.0`." in skill_text
        assert f"${name}" in metadata["interface"]["default_prompt"]
        assert fixture["skill"] == name
        assert fixture["fixture_version"] == "1.0.0"
        assert fixture["negative_case"]["condition"]
        assert fixture["negative_case"]["expected"]
        if name in EXPLICIT_ONLY:
            assert metadata["policy"]["allow_implicit_invocation"] is False


def test_generation_validation_send_and_review_authorities_remain_separate():
    crystallizer = json.loads(
        (SKILL_ROOT / "game-skill-crystallizer" / "fixtures" / "forward_test.json").read_text(
            encoding="utf-8"
        )
    )
    assert {"SkillRunV1", "SkillValidationV1", "preferred successor"} <= set(
        crystallizer["forbidden_writes"]
    )
    voice = json.loads(
        (SKILL_ROOT / "game-ai-account-voice" / "fixtures" / "forward_test.json").read_text(
            encoding="utf-8"
        )
    )
    assert {"SpeechEventV1", "device action during implicit invocation"} <= set(
        voice["forbidden_writes"]
    )
    benchmark = json.loads(
        (SKILL_ROOT / "ai-player-benchmark" / "fixtures" / "forward_test.json").read_text(
            encoding="utf-8"
        )
    )
    assert "independent_review.md" in benchmark["forbidden_writes"]


def test_untrusted_game_and_web_text_never_becomes_agent_instruction():
    for name in {
        "ai-player-orchestrator",
        "game-state-recognition",
        "game-frontier-explorer",
        "game-memory-consolidator",
        "game-guide-research",
        "game-ai-account-voice",
    }:
        text = (SKILL_ROOT / name / "SKILL.md").read_text(encoding="utf-8").casefold()
        assert "untrusted" in text