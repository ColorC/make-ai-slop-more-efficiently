"""Extend ``omni agent`` into the CLI-first Agent Worker control plane."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

import click

from .._access import any_caller


from omnicompany.core.provider_identity import (
    VISIBLE_AGENT_WORKER_PROVIDER_IDS,
)


RUNTIME_PROVIDERS = VISIBLE_AGENT_WORKER_PROVIDER_IDS

_E_SCOPE_TASK_TARGET = re.compile(r"(?i)(?<![A-Za-z0-9_])e:[\\/]")
_E_SCOPE_RULE_POINTER = (
    "[E-scope rule pointer]\n"
    "This task targets E:\\. Before acting, read E:\\AGENTS.md and its referenced "
    "C:/workspace/AGENTS.md; follow their file-placement and PROJECT_INDEX "
    "directory-contract rules. This pointer applies only to this E: task."
)


def _task_targets_e_scope(task: str, cwd: Path | None) -> bool:
    """Return whether a fresh task is explicitly scoped to the E: workspace drive."""
    if _E_SCOPE_TASK_TARGET.search(task):
        return True
    if cwd is None:
        return False
    try:
        return cwd.resolve().drive.casefold() == "e:"
    except OSError:
        return False


def _with_e_scope_rule_pointer(task: str, cwd: Path | None) -> str:
    """Prefix fresh E: tasks without rewriting or persisting their user task body."""
    if not task or not _task_targets_e_scope(task, cwd):
        return task
    return f"{_E_SCOPE_RULE_POINTER}\n\n{task}"

def _echo(value: Any, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, str):
        click.echo(value)
        return
    click.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _control():
    from omnicompany.packages.services._core.agent.resource_control import ResourceControl

    return ResourceControl()


def _validate_binding(project: str | None, workstream: str | None) -> None:
    if not project and workstream:
        raise click.ClickException("--workstream requires --project")
    if not project:
        return
    from omnicompany.core.projects_registry import list_projects

    if project not in {str(row.get("id")) for row in list_projects()}:
        raise click.ClickException(f"unknown project: {project}")
    if workstream:
        from omnicompany.core.workstreams import get_workstream

        row = get_workstream(workstream)
        if row is None or row.get("project") != project:
            raise click.ClickException("workstream does not exist or belongs to another project")


def _auto_launch_target(task: str, task_type: str) -> tuple[str, str | None, dict[str, Any]]:
    decision = _control().recommend(task or "general interactive agent work", task_type=task_type, limit=9)
    from omnicompany.packages.services._core.agent.control_plane import PROVIDER_EXECUTABLES

    for row in decision["recommendations"]:
        runtime = row.get("runtime_provider")
        if not runtime or runtime not in RUNTIME_PROVIDERS:
            continue
        candidates = PROVIDER_EXECUTABLES.get(str(runtime)) or ()
        if any(shutil.which(executable) for executable in candidates):
            return str(runtime), str(row["model"]), decision
    for runtime in RUNTIME_PROVIDERS:
        candidates = PROVIDER_EXECUTABLES.get(runtime) or ()
        if any(shutil.which(executable) for executable in candidates):
            return runtime, None, decision
    raise click.ClickException("no supported Agent Worker CLI is installed or visible on PATH")


def register_agent_control_commands(cmd_agent: click.Group) -> None:
    """Attach the unified controls to the pre-existing ``omni agent`` group."""

    @cmd_agent.command("start")
    @click.argument("task", required=False, default="")
    @click.option("--provider", type=click.Choice(RUNTIME_PROVIDERS), default=None)
    @click.option("--model", default=None, help="Explicit provider model id; bypasses automatic model choice.")
    @click.option("--auto/--no-auto", "auto_route", default=True, show_default=True)
    @click.option("--task-type", default="auto", help="frontend/backend/chinese_writing/multimodal/simple/batch/long_running/testing/general")
    @click.option("--cwd", default=None, type=click.Path(file_okay=False, path_type=Path))
    @click.option("--project", default=None, help="Optional business binding. Omit for an unbound worker.")
    @click.option("--workstream", default=None)
    @click.option("--plan", "active_plan", default=None)
    @click.option("--topic", default=None)
    @click.option("--parent-agent-id", default=None)
    @click.option(
        "--resume-provider-session-id",
        default=None,
        help="Resume an existing provider-native conversation in this real PTY.",
    )
    @click.option(
        "--origin",
        type=click.Choice(["agent", "human"]),
        default="agent",
        help="Lifecycle owner. Use human only when restoring a person's PTY.",
    )
    @click.option("--visibility", type=click.Choice(["visible", "hidden"]), default="visible")
    @click.option("--safe-mode", is_flag=True, help="Do not add provider permission-bypass flags.")
    @click.option(
        "--pure-mode",
        "pure_mode",
        is_flag=True,
        help="Launch from a fresh task-only directory with provider memory/hooks/skills disabled.",
    )
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def start_agent(
        task: str,
        provider: str | None,
        model: str | None,
        auto_route: bool,
        task_type: str,
        cwd: Path | None,
        project: str | None,
        workstream: str | None,
        active_plan: str | None,
        topic: str | None,
        parent_agent_id: str | None,
        resume_provider_session_id: str | None,
        origin: str,
        visibility: str,
        safe_mode: bool,
        pure_mode: bool,
        as_json: bool,
    ) -> None:
        """Start a real CLI/PTY Agent Worker; project binding is optional."""
        _validate_binding(project, workstream)
        decision: dict[str, Any] | None = None
        if provider is None:
            if not auto_route:
                raise click.ClickException("--no-auto requires --provider")
            provider, recommended_model, decision = _auto_launch_target(task, task_type)
            if model is None:
                model = recommended_model
        else:
            from omnicompany.packages.services._core.agent.control_plane import safe_default_model

            model = safe_default_model(provider, model)
        from omnicompany.packages.services._core.agent.control_plane import (
            AgentLaunchRequest,
            launch_agent_worker,
        )
        launch_cwd = cwd or Path(os.getcwd())
        launch_task = task if pure_mode else _with_e_scope_rule_pointer(task, launch_cwd)

        try:
            handle = launch_agent_worker(
                AgentLaunchRequest(
                    task=launch_task or None,
                    provider=provider,
                    model=model,
                    cwd=str(launch_cwd),
                    project=project,
                    workstream=workstream,
                    active_plan=active_plan,
                    topic=topic,
                    parent_agent_id=parent_agent_id,
                    resume_provider_session_id=resume_provider_session_id,
                    origin=origin,
                    visibility=visibility,
                    safe_mode=safe_mode,
                    pure_mode=pure_mode,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise click.ClickException(str(exc)) from None
        result = handle.to_dict()
        if decision:
            result["routing"] = decision
        _echo(result, as_json)

    @cmd_agent.command("list")
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def list_agents(as_json: bool) -> None:
        """List active CLI Agent Workers; legacy structured-chat rows are excluded."""
        from omnicompany.packages.services._core.agent.control_plane import list_agent_workers

        try:
            rows = list_agent_workers()
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from None
        if as_json:
            _echo({"items": rows, "product_kind": "agent_worker", "runtime_kind": "cli"}, True)
            return
        if not rows:
            click.echo("(no active CLI Agent Workers)")
        for row in rows:
            click.echo(f"{row.get('id')}  {row.get('provider') or '-'}  {row.get('provider_runtime_state') or '-'}  {row.get('cwd') or ''}")

    @cmd_agent.command("send")
    @click.argument("worker_id")
    @click.option("-m", "--message", required=True)
    @click.option("--from-topic", default="", help="Provide the sender's work identity when this session is not durably bound.")
    @click.option(
        "--intent",
        type=click.Choice(["auto", "action", "decision", "blocker", "boundary"]),
        default="auto",
        show_default=True,
        help="Why this message can change the receiver's further work.",
    )
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def send(worker_id: str, message: str, from_topic: str, intent: str, as_json: bool) -> None:
        """Send an identity-enveloped coordination message to an exact CLI Agent Worker."""
        from omnicompany.core.session_messages import send_message

        try:
            result = send_message(worker_id, message, from_topic=from_topic, intent=intent)
        except (RuntimeError, ValueError) as exc:
            raise click.ClickException(str(exc)) from None
        _echo(result, as_json)

    @cmd_agent.command("resume")
    @click.argument("provider_session_id")
    @click.option("--provider", type=click.Choice(RUNTIME_PROVIDERS), required=True)
    @click.option("--cwd", default=None, type=click.Path(file_okay=False, path_type=Path))
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def resume(provider_session_id: str, provider: str, cwd: Path | None, as_json: bool) -> None:
        """Resume a provider-native session in a new real PTY."""
        from omnicompany.packages.services._core.agent.control_plane import resume_agent_worker

        try:
            result = resume_agent_worker(provider, provider_session_id, cwd=str(cwd) if cwd else None)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from None
        _echo(result, as_json)

    @click.group("budget")
    def budget_group() -> None:
        """Provider balance, reset-window, availability and official-news observations."""

    @budget_group.command("status")
    @click.option("--provider", "providers", multiple=True)
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def budget_status(providers: tuple[str, ...], as_json: bool) -> None:
        result = _control().status(providers or None)
        if as_json:
            _echo(result, True)
            return
        for row in result["providers"]:
            calibration = row.get("calibration") or {}
            installed = calibration.get("installed")
            windows = row.get("windows") or []
            click.echo(f"{row['id']}: cli={installed if installed is not None else 'n/a'} calibration={row['calibration_freshness']} windows={len(windows)} pressure={row['consumption_pressure']}")
            for window in windows:
                click.echo(f"  {window['account']}/{window['window_id']}: {window['remaining']}/{window.get('capacity') or '?'} {window['unit']} reset={window.get('reset_at') or '?'} freshness={window['freshness']}")

    @budget_group.command("observe")
    @click.argument("provider")
    @click.option("--remaining", type=float, required=True)
    @click.option("--capacity", type=float, default=None)
    @click.option("--unit", default="tokens", show_default=True)
    @click.option("--reset-at", default=None, help="ISO-8601 account-observed reset time.")
    @click.option("--window", "window_id", default="primary", show_default=True)
    @click.option("--account", default="default", show_default=True)
    @click.option("--source", required=True, help="usage_page / provider_cli / billing_api / manual_report")
    @click.option("--evidence", default="", help="URL, receipt id, screenshot ref or command evidence.")
    @click.option("--confidence", default="reported", type=click.Choice(["exact", "reported", "estimated"]))
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def budget_observe(provider: str, remaining: float, capacity: float | None, unit: str,
                       reset_at: str | None, window_id: str, account: str, source: str,
                       evidence: str, confidence: str, as_json: bool) -> None:
        try:
            result = _control().observe(
                provider,
                remaining=remaining,
                capacity=capacity,
                unit=unit,
                reset_at=reset_at,
                window_id=window_id,
                account=account,
                source=source,
                evidence=evidence,
                confidence=confidence,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        _echo(result, as_json)

    @budget_group.command("refresh")
    @click.option("--provider", "providers", multiple=True,
                  help="Default: every provider that has an official usage API (glm-coding-plan, opencode-go).")
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def budget_refresh(providers: tuple[str, ...], as_json: bool) -> None:
        """Read official usage APIs and record the windows as exact observations.

        `calibrate` 刻意只做不带认证的本地探测, 余额只走 observe。本命令补上中间那
        一段: 对有官方额度接口的 provider(GLM Coding Plan / OpenCode Go)做一次便宜
        的带认证 GET, 把窗口按 source=billing_api / confidence=exact 落进账本, 这样
        `budget status` 不依赖人工报数也能保鲜。挂 cron 即可自动。
        """
        from omnicompany.packages.services.boss_sight.subscriptions import (
            refresh_official_api_observations,
        )

        try:
            result = refresh_official_api_observations(providers or None)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        if as_json:
            _echo({"results": result}, True)
            return
        for row in result:
            if row.get("reason"):
                click.echo(f"{row['provider']}: skipped ({row['reason']})")
                continue
            level = f" plan={row['plan_level']}" if row.get("plan_level") else ""
            click.echo(f"{row['provider']}: recorded {row['recorded']} window(s){level}")
            for window in row.get("windows") or []:
                click.echo(
                    f"  {window['id']}: {window['remaining']}/{window.get('capacity') or '?'} "
                    f"{window.get('unit')} used={window.get('used_pct')}% reset={window.get('resets_at') or '?'}"
                )

    @budget_group.command("calibrate")
    @click.option("--provider", "providers", multiple=True)
    @click.option("--include-local-usage", is_flag=True, help="Run allowlisted local usage commands such as opencode stats.")
    @click.option("--force", is_flag=True)
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def budget_calibrate(providers: tuple[str, ...], include_local_usage: bool, force: bool, as_json: bool) -> None:
        try:
            result = _control().calibrate(providers or None, include_local_usage=include_local_usage, force=force)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        _echo({"results": result, "network_probed": False}, as_json)

    @budget_group.command("news")
    @click.option("--provider", "providers", multiple=True)
    @click.option("--force", is_flag=True)
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def budget_news(providers: tuple[str, ...], force: bool, as_json: bool) -> None:
        try:
            result = _control().check_official_news(providers or None, force=force)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        _echo(result, as_json)

    @click.group("models")
    def models_group() -> None:
        """User-pinned T1-T4 catalog and explainable model routing."""

    @models_group.command("list")
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def models_list(as_json: bool) -> None:
        rows = _control().models()
        if as_json:
            _echo({"models": rows}, True)
            return
        for tier in ("T1", "T2", "T3", "T4"):
            click.echo(f"{tier}: " + ", ".join(row["display_name"] for row in rows if row["tier"] == tier))

    @models_group.command("recommend")
    @click.argument("task")
    @click.option("--task-type", default="auto")
    @click.option("--multimodal", is_flag=True)
    @click.option("--provider", default=None, help="Constrain to one budget-provider id.")
    @click.option("--limit", type=click.IntRange(1, 20), default=5, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def recommend(task: str, task_type: str, multimodal: bool, provider: str | None, limit: int, as_json: bool) -> None:
        try:
            result = _control().recommend(task, task_type=task_type, require_multimodal=multimodal, provider=provider, limit=limit)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        _echo(result, as_json)

    @click.group("review")
    def review_group() -> None:
        """Cross-model and same-model/cross-session delivery reviews."""

    @review_group.command("add")
    @click.option("--reviewer-session", required=True)
    @click.option("--reviewer-model", required=True)
    @click.option("--subject-session", required=True)
    @click.option("--subject-model", required=True)
    @click.option("--severity", type=click.Choice(["pass", "imperfect", "severe"]), required=True)
    @click.option("--task-type", default="general", show_default=True)
    @click.option("--issue", "issues", multiple=True)
    @click.option("--upstream-factor", "upstream_factors", multiple=True)
    @click.option("--evidence", multiple=True)
    @click.option("--reviewer-provider", default="")
    @click.option("--subject-provider", default="")
    @click.option("--note", default="")
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def review_add(reviewer_session: str, reviewer_model: str, subject_session: str,
                   subject_model: str, severity: str, task_type: str,
                   issues: tuple[str, ...], upstream_factors: tuple[str, ...],
                   evidence: tuple[str, ...], reviewer_provider: str,
                   subject_provider: str, note: str, as_json: bool) -> None:
        try:
            result = _control().record_review(
                reviewer_session=reviewer_session,
                reviewer_model=reviewer_model,
                reviewer_provider=reviewer_provider,
                subject_session=subject_session,
                subject_model=subject_model,
                subject_provider=subject_provider,
                severity=severity,
                task_type=task_type,
                issues=list(issues),
                upstream_factors=list(upstream_factors),
                evidence=list(evidence),
                note=note,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        _echo(result, as_json)

    @review_group.command("amend")
    @click.option("--actor", "actor_id", required=True,
                  help="Maintenance actor recording the correction; never impersonates the reviewer.")
    @click.option("--supersede", "superseded_review_ids", multiple=True, required=True)
    @click.option("--replacement", "replacement_review_id", required=True)
    @click.option("--reason", required=True)
    @click.option("--evidence", multiple=True, required=True)
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def review_amend(
        actor_id: str,
        superseded_review_ids: tuple[str, ...],
        replacement_review_id: str,
        reason: str,
        evidence: tuple[str, ...],
        as_json: bool,
    ) -> None:
        """Append a correction link while retaining every historical review row."""
        try:
            result = _control().record_review_amendment(
                actor_id=actor_id,
                superseded_review_ids=list(superseded_review_ids),
                replacement_review_id=replacement_review_id,
                reason=reason,
                evidence=list(evidence),
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        _echo(result, as_json)

    @review_group.command("matrix")
    @click.option("--task-type", default=None)
    @click.option("--json", "as_json", is_flag=True)
    @any_caller
    def review_matrix(task_type: str | None, as_json: bool) -> None:
        _echo(_control().review_matrix(task_type=task_type), as_json)

    @cmd_agent.command("migration")
    @any_caller
    def migration() -> None:
        """Show the sealed legacy-chat compatibility boundary."""
        click.echo("New Agent work: omni agent start|send|resume. Legacy chat data is read-only compatibility history; do not create new chat sessions.")

    cmd_agent.add_command(budget_group)
    cmd_agent.add_command(models_group)
    cmd_agent.add_command(review_group)


__all__ = ["register_agent_control_commands"]
