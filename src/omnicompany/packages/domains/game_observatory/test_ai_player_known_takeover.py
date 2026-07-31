from __future__ import annotations

from types import SimpleNamespace

from omnicompany.cli.commands import game as game_command


def test_known_skill_title_uses_explicit_target_to_disambiguate_same_control() -> None:
    leave_city = SimpleNamespace(title="出城地图入口")
    return_city = SimpleNamespace(title="回城")

    assert game_command._known_skill_title_matches_target(leave_city, "出城地图入口")
    assert game_command._known_skill_title_matches_target(leave_city, "出城")
    assert not game_command._known_skill_title_matches_target(return_city, "出城地图入口")


def test_known_skill_title_keeps_conservative_fallback_for_unrelated_goal() -> None:
    skill = SimpleNamespace(title="武将")

    assert game_command._known_skill_title_matches_target(skill, "武将入口")
    assert not game_command._known_skill_title_matches_target(skill, "编队换将")