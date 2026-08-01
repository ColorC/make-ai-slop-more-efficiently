from __future__ import annotations

from .reader_projection import build_reader_game_projection, build_reader_projection


def test_reader_projection_returns_the_completed_profile() -> None:
    bundle = {
        "content_kind": "play",
        "scope": {"subject": "测试系统", "coverage": "完整玩家流程"},
        "game": {"id": "test-game", "title": "测试游戏"},
        "play": {"slug": "unknown-play", "title": "测试玩法", "tags": ["规则"]},
    }

    reader = build_reader_projection(bundle, position={"label": "系统档案"})

    assert reader["slug"] == "unknown-play"
    assert reader["kind"] == "系统档案"
    assert reader["title"] == "测试玩法"
    assert reader["position"] == {"label": "系统档案"}


def test_reader_game_projection_uses_public_fallback() -> None:
    reader_game = build_reader_game_projection(
        {"game": {"id": "test-game", "localized_title": "测试游戏"}}
    )

    assert reader_game["title"] == "测试游戏"
