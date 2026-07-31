"""Lightweight console bootstrap for the ``omni`` command.

The normal CLI imports every command group.  A persistent game-player worker
publishes a loopback descriptor specifically so repeated, already-learned
``navigate`` calls do not need that cold-start cost.  This module recognizes
only that exact agent-facing command shape; every other invocation lazily
loads the existing Click CLI unchanged.
"""

from __future__ import annotations

import json
import os
import sys

from omnicompany.game_player_runtime_client import (
    EXTERNAL_AGENT_SESSION_ID_ENV,
    SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV,
    SessionGamePlayerRuntimeError,
    forward_navigate_to_session_runtime,
)

_FAST_PREFIX = ("game", "player", "--json", "--agent-brief", "navigate")
_REQUIRED_OPTIONS = frozenset({"--environment", "--session", "--source-step"})
_OPTION_NAMES = _REQUIRED_OPTIONS | frozenset({"--max-skills", "--max-safety"})
_SAFETY_LEVELS = frozenset(
    {"read_only", "reversible", "progression", "social", "economic", "restricted"}
)


class _NavigateArguments:
    __slots__ = (
        "goal",
        "environment_id",
        "session_id",
        "source_step_id",
        "max_skills",
        "max_safety",
    )

    def __init__(
        self,
        *,
        goal: str,
        environment_id: str,
        session_id: str,
        source_step_id: str,
        max_skills: int = 12,
        max_safety: str = "economic",
    ) -> None:
        self.goal = goal
        self.environment_id = environment_id
        self.session_id = session_id
        self.source_step_id = source_step_id
        self.max_skills = max_skills
        self.max_safety = max_safety


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - console encoding is best-effort
            pass


def _parse_fast_navigate(argv: list[str]) -> _NavigateArguments | None:
    """Return arguments only for the canonical agent-facing navigate syntax."""

    if len(argv) < len(_FAST_PREFIX) + 1:
        return None
    if tuple(argv[: len(_FAST_PREFIX)]) != _FAST_PREFIX:
        return None
    goal = argv[len(_FAST_PREFIX)]
    if not goal or goal.startswith("--"):
        return None
    tail = list(argv[len(_FAST_PREFIX) + 1 :])
    if len(tail) % 2:
        return None
    options: dict[str, str] = {}
    for index in range(0, len(tail), 2):
        name, value = tail[index : index + 2]
        if name not in _OPTION_NAMES or name in options:
            return None
        if not value or value.startswith("--"):
            return None
        options[name] = value
    if not _REQUIRED_OPTIONS.issubset(options):
        return None
    try:
        max_skills = int(options.get("--max-skills", "12"))
    except ValueError:
        return None
    if not 1 <= max_skills <= 20:
        return None
    max_safety = options.get("--max-safety", "economic")
    if max_safety not in _SAFETY_LEVELS:
        return None
    return _NavigateArguments(
        goal=goal,
        environment_id=options["--environment"],
        session_id=options["--session"],
        source_step_id=options["--source-step"],
        max_skills=max_skills,
        max_safety=max_safety,
    )


def _run_full_cli(argv: list[str]):
    from .main import cli

    return cli.main(args=list(argv), prog_name="omni", standalone_mode=True)


def _bind_active_published_session(argv: list[str]) -> list[str]:
    """Bind agent-issued player commands to the runtime-owned canonical session.

    A resumed provider keeps its native history and can occasionally repeat a
    predecessor session id after rollover.  Inside the published, authenticated
    runtime that predecessor is never a legitimate write target.  Canonicalize
    only explicit ``--session`` options in the isolated ``game player`` process;
    ordinary host CLI calls do not carry this runtime environment.
    """

    session_id = os.environ.get(EXTERNAL_AGENT_SESSION_ID_ENV)
    if not session_id or tuple(argv[:2]) != ("game", "player"):
        return list(argv)
    bound = list(argv)
    for index, value in enumerate(bound):
        if value == "--session" and index + 1 < len(bound):
            if bound[index + 1] and not bound[index + 1].startswith("--"):
                bound[index + 1] = session_id
        elif value.startswith("--session="):
            bound[index] = f"--session={session_id}"
    return bound


def main(argv: list[str] | tuple[str, ...] | None = None):
    """Run the narrow fast-forward path or lazily enter the full Click CLI."""

    _configure_utf8_streams()
    arguments = list(sys.argv[1:] if argv is None else argv)
    descriptor_value = os.environ.get(SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV)
    if descriptor_value:
        arguments = _bind_active_published_session(arguments)
    fast_arguments = _parse_fast_navigate(arguments) if descriptor_value else None
    if fast_arguments is None:
        return _run_full_cli(arguments)

    # Once a published descriptor and the exact fast command are accepted,
    # every error is fail-closed.  Falling back could repeat an already accepted
    # device action after a lost response.
    try:
        forwarded = forward_navigate_to_session_runtime(
            environment_id=fast_arguments.environment_id,
            session_id=fast_arguments.session_id,
            goal=fast_arguments.goal,
            source_step_id=fast_arguments.source_step_id,
            max_skills=fast_arguments.max_skills,
            max_safety=fast_arguments.max_safety,
        )
        if forwarded is None:
            raise SessionGamePlayerRuntimeError(
                "published session runtime descriptor disappeared before forwarding"
            )
        for emission in forwarded.emissions:
            print(
                json.dumps(
                    emission,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            )
        if forwarded.error and not forwarded.emissions:
            raise SessionGamePlayerRuntimeError(forwarded.error)
        return forwarded.exit_code
    except SessionGamePlayerRuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - matched fast paths stay fail-closed
        print(f"Error: session runtime fast-forward failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
