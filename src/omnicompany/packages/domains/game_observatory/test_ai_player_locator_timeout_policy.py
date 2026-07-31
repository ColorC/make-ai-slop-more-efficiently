from __future__ import annotations

from click.testing import CliRunner

from omnicompany.cli.commands import game as game_command


def test_external_locator_retains_cold_start_budget(monkeypatch) -> None:
    captured: dict[str, float] = {}

    class FakeService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def locate(self, **kwargs):
            captured["timeout_seconds"] = float(kwargs["timeout_seconds"])
            raise RuntimeError("fixture stop after timeout capture")

    monkeypatch.setenv(game_command.EXTERNAL_AGENT_INVOCATION_ID_ENV, "invocation.test")
    monkeypatch.setattr(
        game_command,
        "_canonical_locator_source",
        lambda *_args, **_kwargs: (object(), object(), object(), object()),
    )
    monkeypatch.setattr(game_command, "CanonicalVisualLocatorService", FakeService)

    result = CliRunner().invoke(
        game_command.cmd_game,
        [
            "player",
            "observe",
            "locate",
            "--environment",
            "environment.test",
            "--source-step",
            "evidence.step.test",
            "--timeout",
            "30",
        ],
    )

    assert result.exit_code != 0
    assert captured["timeout_seconds"] == 90.0