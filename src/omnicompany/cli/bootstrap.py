"""Lightweight console bootstrap for the ``omni`` command.

The normal CLI imports every command group.  A persistent game-player worker
publishes a loopback descriptor specifically so repeated, already-learned
``navigate`` calls do not need that cold-start cost.  This module recognizes
only that exact agent-facing command shape; every other invocation lazily
loads the existing Click CLI unchanged.
"""

from __future__ import annotations

import json
import importlib
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

# Top-level command discovery must not import every command implementation.
# Values are (module suffix under omnicompany.cli, exported Click command).
_LAZY_COMMANDS: dict[str, tuple[str, str]] = {
    "trace": ("commands.trace", "cmd_trace"),
    "round": ("commands.round_", "cmd_round"),
    "node": ("commands.node", "cmd_node"),
    "open-loops": ("commands.loops", "cmd_loops"),
    "pain": ("commands.pain", "cmd_pain"),
    "evo": ("commands.evo", "cmd_evo"),
    "domain": ("commands.domain", "cmd_domain"),
    "run": ("unified", "cmd_run"), "exec": ("unified", "cmd_exec"),
    "replay": ("unified", "cmd_replay"), "tail": ("unified", "cmd_tail"),
    "trace-view": ("unified", "cmd_trace_view"), "traces": ("unified", "cmd_traces"),
    "pipelines": ("unified", "cmd_pipelines"), "health": ("unified", "cmd_health"),
    "describe": ("unified", "cmd_describe"), "routers": ("unified", "cmd_routers"),
    "formats": ("unified", "cmd_formats"), "nodes": ("unified", "cmd_nodes"),
    "errors": ("unified", "cmd_errors"), "diagnose": ("unified", "cmd_diagnose"),
    "inquiry": ("commands.inquiry", "cmd_inquiry"),
    "guardian": ("commands.guardian", "cmd_guardian"),
    "home": ("commands.home", "cmd_home"),
    "desktop-node": ("commands.desktop_node", "cmd_desktop_node"),
    "debt": ("commands.debt", "cmd_debt"),
    "registry": ("commands.registry", "cmd_registry"),
    "material": ("commands.material", "cmd_material"),
    "learning-observatory": ("commands.learning_observatory", "cmd_learning_observatory"),
    "resolve": ("commands.resolve", "cmd_resolve"),
    "game": ("commands.game", "cmd_game"),
    "semantic": ("commands.semantic", "cmd_semantic"),
    "who": ("commands.identity", "cmd_who"), "whoami": ("commands.identity", "cmd_whoami"),
    "session": ("commands.identity", "cmd_session"),
    "plan": ("commands.plan", "cmd_plan"), "todo": ("commands.todo", "cmd_todo"),
    "taskline": ("commands.taskline", "cmd_taskline"),
    "new": ("commands.creation", "cmd_new"), "sandbox": ("commands.creation", "cmd_sandbox"),
    "guide": ("commands.guide", "cmd_guide"), "reflect": ("commands.guide", "cmd_reflect"),
    "self-portrait": ("commands.self_portrait", "cmd_self_portrait"),
    "register": ("commands.registration", "cmd_register"),
    "lookup": ("commands.registration", "cmd_lookup"),
    "register-types": ("commands.registration", "cmd_register_types"),
    "lock": ("commands.protection", "cmd_lock"), "meta-io": ("commands.meta_io", "cmd_meta_io"),
    "team": ("commands.team", "cmd_team"), "assistant": ("commands.assistant", "cmd_assistant"),
    "human": ("commands.human", "cmd_human"),
    "llm": ("commands.llm_audit", "cmd_llm"), "pipeline": ("commands.llm_audit", "cmd_pipeline"),
    "docauthor": ("commands.docauthor", "cmd_docauthor"), "cc": ("commands.cc", "cmd_cc"),
    "worker": ("commands.worker", "cmd_worker"), "context": ("commands.context", "cmd_context"),
    "chat": ("commands.chat", "cmd_chat"), "research": ("commands.research", "cmd_research"),
    "atlas": ("commands.atlas", "cmd_atlas"), "decisions": ("commands.decisions", "cmd_decisions"),
    "ledger": ("commands.ledger", "cmd_ledger"),
    "token-ledger": ("commands.token_ledger", "cmd_token_ledger"),
    "hr": ("commands.hr", "cmd_hr"), "notify": ("commands.notify", "cmd_notify"),
    "refs": ("commands.refs", "cmd_refs"), "agents": ("commands.agents", "cmd_agents"),
    "dispatch": ("commands.dispatch", "cmd_dispatch"), "notes": ("commands.notes", "cmd_notes"),
    "canvas": ("commands.canvas", "cmd_canvas"), "task": ("commands.task", "cmd_task"),
    "progress": ("commands.progress", "cmd_progress"), "convos": ("commands.convos", "cmd_convos"),
    "handoff": ("commands.handoff", "cmd_handoff"), "convflow": ("commands.convflow", "cmd_convflow"),
    "workflow": ("commands.workflow", "cmd_workflow"),
    "dashboard": ("commands.dashboard", "cmd_dashboard"),
    "project": ("commands.project", "cmd_project"),
    "workstream": ("commands.workstream", "cmd_workstream"),
    "governance": ("commands.governance", "cmd_governance"),
    "cron": ("commands.cron", "cmd_cron"), "testmap": ("commands.testmap", "cmd_testmap"),
    "plan-complete-gate": ("commands.plan_gate", "cmd_plan_complete_gate"),
    "vilo": ("commands.vilo", "cmd_vilo"),
    "review": ("commands.boss_sight", "cmd_review"),
    "prompt": ("commands.boss_sight", "cmd_prompt"),
    "propose": ("commands.boss_sight", "cmd_propose"),
    "agent": ("commands.boss_sight", "cmd_agent"),
}

_HIDDEN_COMMANDS = frozenset({"chat", "convos"})


def _normalize_windows_process_priority() -> bool:
    """Break inherited background priority before launching CLI children.

    Windows propagates a parent's priority class.  A previously deprioritized
    terminal can therefore make a fresh ``omni`` invocation and every process
    it launches (npm builds, daemon restarts, probes) remain BelowNormal long
    after the original pressure has disappeared.  The CLI is an interactive
    control-plane entrypoint, so restore its own process to Normal best-effort.
    """

    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        normal_priority_class = 0x00000020
        return bool(
            kernel32.SetPriorityClass(
                kernel32.GetCurrentProcess(),
                normal_priority_class,
            )
        )
    except Exception:  # noqa: BLE001 - CLI access must survive OS policy limits
        return False


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


def _guardian_precheck() -> None:
    """Keep the global write sentinel without importing the full CLI graph."""

    if os.environ.get("OMNICOMPANY_SKIP_GUARDIAN_PRECHECK") == "1":
        return
    try:
        from omnicompany.core.guarded_write import sentinel_check_guardian_integrity

        report = sentinel_check_guardian_integrity()
    except Exception as exc:  # noqa: BLE001 - precheck warns but never blocks CLI access
        print(f"OmniGuardian sentinel startup failed: {exc}", file=sys.stderr)
        return
    if report.get("ok"):
        return
    print("OmniGuardian PRE-CHECK found issues:", file=sys.stderr)
    for issue in report.get("issues", []):
        print(f"  - {issue}", file=sys.stderr)


def _load_lazy_command(name: str):
    module_name, attribute = _LAZY_COMMANDS[name]
    module = importlib.import_module(f"omnicompany.cli.{module_name}")
    command = getattr(module, attribute)

    # BOSS SIGHT extends these groups at registration time. Pay that import
    # cost only when one of the affected commands is actually invoked.
    if name in {"worker", "plan"}:
        import click

        worker = command if name == "worker" else getattr(
            importlib.import_module("omnicompany.cli.commands.worker"), "cmd_worker"
        )
        plan = command if name == "plan" else getattr(
            importlib.import_module("omnicompany.cli.commands.plan"), "cmd_plan"
        )
        boss = importlib.import_module("omnicompany.cli.commands.boss_sight")
        boss.register_boss_sight_commands(click.Group("omni"), cmd_worker=worker, cmd_plan=plan)
    elif name == "agent":
        control = importlib.import_module("omnicompany.cli.commands.agent_control")
        control.register_agent_control_commands(command)
    return command


def _print_lazy_help() -> None:
    visible = [name for name in _LAZY_COMMANDS if name not in _HIDDEN_COMMANDS]
    print("Usage: omni [OPTIONS] COMMAND [ARGS]...")
    print()
    print("OmniCompany CLI — unified execution, observability, and management.")
    print()
    print("Options:")
    print("  --help  Show this message and exit.")
    print()
    print("Commands:")
    for name in visible:
        print(f"  {name}")


def _run_lazy_cli(argv: list[str]):
    if not argv or argv == ["--help"] or argv == ["-h"]:
        _print_lazy_help()
        return 0
    if os.environ.get("_OMNI_COMPLETE"):
        return _run_full_cli(argv)
    name = argv[0]
    if name not in _LAZY_COMMANDS:
        return _run_full_cli(argv)

    _guardian_precheck()
    try:
        command = _load_lazy_command(name)
    except ModuleNotFoundError as exc:
        if name == "game" and exc.name in {"PIL", "cv2", "numpy", "onnxruntime", "rapidocr_onnxruntime"}:
            print('Error: Game Observatory dependencies are not installed. Run: pip install "omnicompany[game-observatory]"', file=sys.stderr)
            return 1
        raise

    import click

    try:
        result = command.main(
            args=argv[1:],
            prog_name=f"omni {name}",
            standalone_mode=False,
        )
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.Abort:
        print("Aborted!", file=sys.stderr)
        return 1
    if isinstance(result, int) and not isinstance(result, bool):
        return result
    return 0


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

    _normalize_windows_process_priority()
    _configure_utf8_streams()
    arguments = list(sys.argv[1:] if argv is None else argv)
    descriptor_value = os.environ.get(SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV)
    if descriptor_value:
        arguments = _bind_active_published_session(arguments)
    fast_arguments = _parse_fast_navigate(arguments) if descriptor_value else None
    if fast_arguments is None:
        # The published game runtime owns its full fallback semantics (including
        # session-id canonicalization). Ordinary CLI calls use command-level
        # lazy loading and never initialize unrelated subsystems.
        if descriptor_value or tuple(arguments[: len(_FAST_PREFIX)]) == _FAST_PREFIX:
            return _run_full_cli(arguments)
        return _run_lazy_cli(arguments)

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
