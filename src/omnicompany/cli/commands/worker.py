# [OMNI] origin=codex domain=cli ts=2026-05-17 type=infrastructure
# [OMNI] material_id="material:cli.worker.external_agent_command_group.py"
"""CLI entry points for audited external agent workers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path
import re
from typing import Any

import click

from omnicompany.packages.services._core.agent.external_workers import (
    AgentCapabilityGrant,
    AgentContextEnvelope,
    AgentRuntimeProfile,
    AgentRuntimeRequest,
    CodexExecWorker,
    ExternalAgentPermissionMode,
    ExternalAgentRunRequest,
    ExternalAgentStatus,
    build_default_external_agent_worker_registry,
    resolve_external_agent_model,
    run_agent_runtime_request,
    run_external_agent_request,
    select_agent_runtime,
)
from omnicompany.packages.services._core.agent.external_workers import lifecycle as worker_lifecycle
from omnicompany.packages.services._core.agent.spawn_surface import (
    ENTRY_EXTERNAL_WORKER_RUN,
    ensure_agent_spawn_metadata,
)


_E_SCOPE_TASK_TARGET = re.compile(r"(?i)(?<![A-Za-z0-9_])e:[\\/]")
_E_SCOPE_RULE_POINTER = (
    "[E-scope rule pointer]\n"
    "This task targets E:\\. Before acting, read E:\\AGENTS.md and its referenced "
    "C:/workspace/AGENTS.md; follow their file-placement and PROJECT_INDEX "
    "directory-contract rules. This pointer applies only to this E: task."
)


_CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def _provider_uses_codex_worker(provider: str) -> bool:
    registry = build_default_external_agent_worker_registry()
    try:
        worker = registry.create(provider)
    except KeyError:
        return False
    return isinstance(worker, CodexExecWorker)


@click.group("worker")
def cmd_worker() -> None:
    """Run audited external workers such as Claude Code and Codex."""


@cmd_worker.command("providers")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def worker_providers(as_json: bool) -> None:
    """List external worker providers available to this checkout."""

    registry = build_default_external_agent_worker_registry()
    items = []
    for provider in registry.list_providers():
        items.append(
            {
                "provider": provider,
                "default_permission_mode": ExternalAgentPermissionMode.READONLY.value,
                "cheap_readonly_model": resolve_external_agent_model(
                    provider=provider,
                    permission_mode=ExternalAgentPermissionMode.READONLY,
                    model_policy="cheap",
                ),
                "cheap_write_model": resolve_external_agent_model(
                    provider=provider,
                    permission_mode=ExternalAgentPermissionMode.WORKSPACE_WRITE,
                    model_policy="cheap",
                ),
            }
        )

    payload = {
        "items": items,
        "total": len(items),
        "permission_modes": [mode.value for mode in ExternalAgentPermissionMode],
    }
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    click.echo("External worker providers:")
    for item in items:
        click.echo(
            f"  {item['provider']:<12} default={item['default_permission_mode']}"
            f" cheap_readonly={item['cheap_readonly_model'] or '-'}"
            f" cheap_write={item['cheap_write_model'] or '-'}"
        )


@cmd_worker.command("run")
@click.argument("provider", required=False, default=None)
@click.option("--prompt", "-p", default=None, help="Inline task/spec text.")
@click.option(
    "--spec",
    "spec_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read the worker task/spec from a text file.",
)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read task/spec text from stdin.")
@click.option(
    "--cwd",
    "cwd_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    help="Working directory for the external worker.",
)
@click.option(
    "--permission",
    "permission_mode",
    type=click.Choice([mode.value for mode in ExternalAgentPermissionMode]),
    default=ExternalAgentPermissionMode.READONLY.value,
    show_default=True,
)
@click.option(
    "--allow-trusted-bypass",
    is_flag=True,
    help="Required when --permission trusted-bypass is used.",
)
@click.option("--model", default=None, help="Provider model override.")
@click.option(
    "--model-provider",
    default=None,
    help="LLM provider identity inside the selected harness (for example openai).",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(_CODEX_REASONING_EFFORTS),
    default=None,
    help="Codex reasoning effort for this task: minimal, low, medium, high, or xhigh.",
)
@click.option(
    "--network-access",
    type=click.Choice(["true", "false"]),
    default=None,
    help=(
        "Codex workspace-write network access for this task. true permits general "
        "outbound network access (not only localhost) when enabled; false keeps it "
        "disabled. Omit to preserve the Codex default."
    ),
)
@click.option("--profile", default=None, help="Provider profile override.")
@click.option(
    "--runtime-profile",
    type=click.Choice([profile.value for profile in AgentRuntimeProfile]),
    default=None,
    help=(
        "Canonical runtime lane: stable_pi, native_bus, or compat. "
        "Without this option the legacy explicit provider path is preserved."
    ),
)
@click.option(
    "--model-policy",
    type=click.Choice(["none", "cheap"]),
    default="none",
    show_default=True,
    help="Default-model resolver policy. Use cheap mainly for Codex.",
)
@click.option(
    "--minimal-context",
    is_flag=True,
    help=(
        "Use a narrow provider context for bounded review/schema tasks; Codex "
        "skips ambient user config, memories, plugins, apps, and multi-agent tools; "
        "OpenCode uses --pure and disables external Claude/Agents skills and prompts."
    ),
)
@click.option(
    "--timeout",
    "timeout_s",
    type=float,
    default=None,
    help=(
        "Total deadline in seconds (default off); 0 disables the total deadline."
    ),
)
@click.option(
    "--idle-timeout",
    "idle_timeout_s",
    type=float,
    default=None,
    help=(
        "End the run after subtree CPU/IO inactivity and no fresh heartbeat for this many seconds "
        "(default off, or metadata idle_timeout_s); 0 disables it."
    ),
)
@click.option(
    "--context",
    "context_paths",
    type=click.Path(dir_okay=False, path_type=Path),
    multiple=True,
    help="Attach a context file without making it the main task.",
)
@click.option(
    "--context-text",
    "context_texts",
    multiple=True,
    help="Attach a short context string without making it the main task.",
)
@click.option(
    "--context-alias",
    "context_alias_items",
    multiple=True,
    help=(
        "Attach a context file as alias=path. Use an ASCII alias for paths "
        "that Claude may misread, especially Chinese filenames."
    ),
)
@click.option(
    "--image",
    "image_paths",
    type=click.Path(dir_okay=False, path_type=Path),
    multiple=True,
    help=(
        "Attach an image to the worker initial turn. May be repeated; "
        "the canonical omni-native Agent and supported compatibility harnesses "
        "accept images. Each image must stay under --cwd."
    ),
)
@click.option(
    "--output-schema",
    "output_schema_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional structured-output schema path.",
)
@click.option(
    "--watch-path",
    "watch_paths",
    type=click.Path(path_type=Path),
    multiple=True,
    help=(
        "Snapshot a file or directory before/after the worker run. "
        "Use for ignored data roots or explicit allowed write sets."
    ),
)
@click.option(
    "--run-root",
    "run_root_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Directory for an explicit UTF-8 run record. Writes prompt.md, "
        "request.json, result.json, and a compact structured_output.json when "
        "available under <run-root>/<run-id>/."
    ),
)
@click.option("--trace-id", default=None, help="Trace id to attach to audit events.")
@click.option(
    "--metadata",
    "metadata_items",
    multiple=True,
    help="Metadata key=value to include in the run request. May be repeated.",
)
@click.option(
    "--env",
    "env_items",
    multiple=True,
    help="Environment key=value to pass to providers that support it. May be repeated.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit full JSON result.")
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the full JSON result to this file.",
)
def worker_run(
    provider: str | None,
    prompt: str | None,
    spec_path: Path | None,
    from_stdin: bool,
    cwd_path: Path,
    permission_mode: str,
    allow_trusted_bypass: bool,
    model: str | None,
    model_provider: str | None,
    reasoning_effort: str | None,
    network_access: str | None,
    profile: str | None,
    runtime_profile: str | None,
    model_policy: str,
    minimal_context: bool,
    timeout_s: float | None,
    idle_timeout_s: float | None,
    context_paths: tuple[Path, ...],
    context_texts: tuple[str, ...],
    context_alias_items: tuple[str, ...],
    image_paths: tuple[Path, ...],
    output_schema_path: Path | None,
    watch_paths: tuple[Path, ...],
    run_root_path: Path | None,
    trace_id: str | None,
    metadata_items: tuple[str, ...],
    env_items: tuple[str, ...],
    as_json: bool,
    output_path: Path | None,
) -> None:
    """Run an external worker once and wait for its result.

    Examples:
      omni worker run claude-code --spec docs/spec.md --permission workspace-write
      echo "inspect this repo" | omni worker run claude-code --stdin --json
    """

    try:
        permission = ExternalAgentPermissionMode(permission_mode)
    except ValueError as exc:
        raise click.UsageError(
            "permission must be one of: readonly, workspace-write, trusted-bypass"
        ) from exc
    if permission == ExternalAgentPermissionMode.TRUSTED_BYPASS and not allow_trusted_bypass:
        raise click.UsageError("trusted-bypass requires --allow-trusted-bypass")

    selection = None
    if runtime_profile is not None:
        try:
            selection = select_agent_runtime(
                runtime_profile,
                requested_harness=provider or "",
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        provider = selection.harness_id
        if selection.profile == AgentRuntimeProfile.STABLE_PI and (
            not (model_provider or "").strip() or not (model or "").strip()
        ):
            raise click.UsageError(
                "stable_pi requires --model-provider and --model; "
                "ambient model selection is disabled for the stable profile"
            )
    else:
        provider = provider or "claude-code"

    if (reasoning_effort is not None or network_access is not None) and not _provider_uses_codex_worker(
        provider
    ):
        raise click.UsageError(
            "--reasoning-effort and --network-access are only supported for Codex worker adapters"
        )

    cwd = cwd_path.expanduser().resolve()
    if not cwd.is_dir():
        raise click.UsageError(f"cwd must be an existing directory: {cwd}")
    if image_paths and provider != "codex":
        raise click.UsageError(
            "--image is currently supported only by the codex compatibility harness"
        )
    resolved_image_paths = [
        _resolve_image_file(path, cwd=cwd) for path in image_paths
    ]

    task = _read_task_text(prompt=prompt, spec_path=spec_path, from_stdin=from_stdin)
    attached_context = list(_e_scope_rule_context(task, cwd)) + _read_attached_context(
        context_paths,
        context_texts,
        context_alias_items,
        cwd=cwd,
    )
    context_envelope = AgentContextEnvelope.from_legacy(
        attached_context,
        provenance=("cli:omni-worker-run",),
    )
    run_id = f"external-cli-{uuid.uuid4().hex}"
    run_dir = _prepare_run_record_dir(run_root_path, run_id) if run_root_path is not None else None
    metadata = ensure_agent_spawn_metadata(
        ENTRY_EXTERNAL_WORKER_RUN,
        _parse_key_values(metadata_items, option_name="--metadata"),
        cli_entrypoint="omni_worker_run",
    )
    if spec_path is not None:
        # The full spec body is already the task. Keep its source identity in
        # the audit record instead of embedding an absolute path in the prompt:
        # models otherwise spend an unnecessary tool call rereading identical
        # content before doing the real task.
        metadata["spec_source_path"] = str(spec_path.expanduser().resolve())
    if minimal_context:
        metadata["minimal_context"] = True
    timeout_s = _resolve_total_timeout(timeout_s)
    idle_timeout = _resolve_idle_timeout(idle_timeout_s, metadata)
    if idle_timeout is None:
        metadata.pop("idle_timeout_s", None)
    else:
        metadata["idle_timeout_s"] = idle_timeout
    lifecycle_dir = (
        run_dir
        if run_dir is not None
        else worker_lifecycle.default_worker_run_root() / run_id
    )
    metadata["worker_lifecycle_dir"] = str(lifecycle_dir)
    if reasoning_effort is not None or network_access is not None:
        codex_config = metadata.get("codex_config")
        if codex_config is None:
            codex_config = {}
        elif not isinstance(codex_config, dict):
            raise click.UsageError("metadata codex_config must be a dict")
        else:
            codex_config = dict(codex_config)
        if reasoning_effort is not None:
            codex_config["model_reasoning_effort"] = json.dumps(reasoning_effort)
        if network_access is not None:
            codex_config["sandbox_workspace_write.network_access"] = network_access.lower()
        metadata["codex_config"] = codex_config
    if run_dir is not None:
        metadata["run_record_dir"] = str(run_dir)
    env = _parse_key_values(env_items, option_name="--env")
    env.setdefault("OMNI_EXTERNAL_WORKER_RUN_ID", run_id)
    env.setdefault("OMNI_EXTERNAL_WORKER_PROVIDER", provider)
    capability_grant = None
    if provider == "omni-native":
        if permission == ExternalAgentPermissionMode.TRUSTED_BYPASS:
            raise click.UsageError(
                f"{provider} canonical runtime does not support trusted-bypass"
            )
        raw_prefixes = metadata.get("allowed_bash_command_prefixes") or ()
        prefixes = (
            (str(raw_prefixes),)
            if isinstance(raw_prefixes, str)
            else tuple(str(item) for item in raw_prefixes)
        )
        capability_grant = AgentCapabilityGrant.for_workspace(
            cwd,
            permission_mode=permission.value,
            grant_id=f"grant:{run_id}",
            policy_ref=str(
                metadata.get("capability_policy_ref")
                or "policy:omni-worker-cli"
            ),
            allowed_shell_command_prefixes=prefixes,
        )

    if run_dir is not None:
        _write_run_input_record(
            run_dir=run_dir,
            provider=provider,
            cwd=cwd,
            permission_mode=permission.value,
            timeout_s=timeout_s,
            prompt=task,
            attached_context=attached_context,
            image_paths=resolved_image_paths,
            watch_paths=watch_paths,
            env_keys=sorted(env),
            metadata=metadata,
        )

    # Start before any provider work: on Windows this puts the controller in a
    # KILL_ON_JOB_CLOSE job, so every process the provider spawns is born inside.
    lifecycle = worker_lifecycle.WorkerLifecycle(
        lifecycle_dir,
        run_id=run_id,
        provider=provider,
        cwd=cwd,
        timeout_s=timeout_s,
        idle_timeout_s=idle_timeout,
        extra={"metadata": metadata},
    ).start()
    if run_dir is not None:
        try:
            worker_lifecycle.register_run_link(run_dir, run_id)
        except OSError:
            pass
    restore_signals = _install_cancel_signal_handlers(lifecycle)
    try:
        _run_worker_with_lifecycle(
            lifecycle=lifecycle,
            selection=selection,
            provider=provider,
            task=task,
            cwd=cwd,
            run_id=run_id,
            permission=permission,
            model=model,
            model_provider=model_provider,
            model_policy=model_policy,
            profile=profile,
            timeout_s=timeout_s,
            attached_context=attached_context,
            context_envelope=context_envelope,
            capability_grant=capability_grant,
            resolved_image_paths=resolved_image_paths,
            output_schema_path=output_schema_path,
            watch_paths=watch_paths,
            env=env,
            trace_id=trace_id,
            metadata=metadata,
            run_dir=run_dir,
            output_path=output_path,
            as_json=as_json,
        )
    finally:
        restore_signals()
        # No-op when an exit marker was already written above.
        lifecycle.finish(
            status="error",
            exit_code=None,
            reason="worker controller left the run without an exit marker",
        )


def _run_worker_with_lifecycle(
    *,
    lifecycle: worker_lifecycle.WorkerLifecycle,
    selection: Any,
    provider: str,
    task: str,
    cwd: Path,
    run_id: str,
    permission: ExternalAgentPermissionMode,
    model: str | None,
    model_provider: str | None,
    model_policy: str,
    profile: str | None,
    timeout_s: float | None,
    attached_context: list[str],
    context_envelope: AgentContextEnvelope,
    capability_grant: AgentCapabilityGrant | None,
    resolved_image_paths: list[Path],
    output_schema_path: Path | None,
    watch_paths: tuple[Path, ...],
    env: dict[str, str],
    trace_id: str | None,
    metadata: dict[str, Any],
    run_dir: Path | None,
    output_path: Path | None,
    as_json: bool,
) -> None:
    try:
        if selection is not None:
            runtime_request = AgentRuntimeRequest(
                prompt=task,
                cwd=cwd,
                runtime_profile=selection.profile,
                harness_id=selection.harness_id,
                run_id=run_id,
                permission_mode=permission,
                model_provider=model_provider or "",
                model=model,
                model_policy=model_policy,  # type: ignore[arg-type]
                provider_profile=profile,
                timeout_s=timeout_s,
                context_envelope=context_envelope,
                capability_grant=capability_grant,
                image_paths=resolved_image_paths,
                output_schema_path=output_schema_path,
                watch_paths=list(watch_paths),
                env=env,
                trace_id=trace_id or "",
                metadata=metadata,
            )
            result = asyncio.run(run_agent_runtime_request(runtime_request))
        else:
            request = ExternalAgentRunRequest(
                provider=provider,
                harness_id=provider,
                model_provider=model_provider or "",
                prompt=task,
                cwd=cwd,
                run_id=run_id,
                permission_mode=permission,
                model=model,
                model_policy=model_policy,  # type: ignore[arg-type]
                profile=profile,
                timeout_s=timeout_s,
                attached_context=(
                    []
                    if provider == "omni-native"
                    else attached_context
                ),
                context_envelope=(
                    context_envelope
                    if provider == "omni-native"
                    else None
                ),
                capability_grant=capability_grant,
                image_paths=resolved_image_paths,
                output_schema_path=output_schema_path,
                watch_paths=list(watch_paths),
                env=env,
                trace_id=trace_id or "",
                metadata=metadata,
            )
            result = asyncio.run(run_external_agent_request(request))
    except KeyboardInterrupt:
        lifecycle.finish(
            status="cancelled",
            exit_code=130,
            reason=lifecycle.cancel_reason or "KeyboardInterrupt",
        )
        click.echo("[interrupted]", err=True)
        raise SystemExit(130)
    except Exception as exc:
        lifecycle.finish(status="error", exit_code=1, reason=f"{type(exc).__name__}: {exc}")
        if as_json:
            click.echo(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            click.echo(f"ERROR: {exc}", err=True)
        raise SystemExit(1)

    payload = result.audit_payload()
    if run_dir is not None:
        payload["run_record_dir"] = str(run_dir)
        _write_json_no_bom(run_dir / "result.json", payload)
        structured_output = payload.get("structured_output")
        if (
            payload.get("status") == ExternalAgentStatus.SUCCEEDED.value
            and isinstance(structured_output, dict)
        ):
            _write_json_no_bom(run_dir / "structured_output.json", structured_output)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_no_bom(output_path, payload)

    exit_code = _exit_code_for_status(result.normalized_status())
    if (
        exit_code == 0
        and isinstance(result.structured_output, dict)
        and str(result.structured_output.get("outcome") or "").strip().lower()
        == "blocked"
    ):
        # A provider turn can complete normally while the domain task reports
        # that it could not run. Scheduled callers need this to be a real
        # failure signal instead of a green cron receipt.
        exit_code = 2
    lifecycle.finish(
        status=result.normalized_status().value,
        exit_code=exit_code,
        reason=str(result.error or ""),
    )

    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human_result(payload)
    raise SystemExit(exit_code)


@cmd_worker.command("trace")
@click.argument("trace_id")
@click.option(
    "--db",
    "db_selector",
    type=click.Choice(["events", "ide", "both"]),
    default="both",
    show_default=True,
    help="Read data/events.db, data/ide_events.db, or both.",
)
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def worker_trace(trace_id: str, db_selector: str, limit: int, as_json: bool) -> None:
    """Read DB trace events for a worker run or Claude session."""

    items = _read_trace_events(trace_id=trace_id, db_selector=db_selector, limit=limit)
    payload = {"trace_id": trace_id, "total": len(items), "items": items}
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    click.echo(f"trace_id: {trace_id}")
    click.echo(f"events  : {len(items)}")
    for item in items:
        detail = ""
        event_payload = item.get("payload") or {}
        if isinstance(event_payload, dict):
            tool = event_payload.get("tool")
            if tool:
                detail = f" tool={tool}"
        click.echo(f"- [{item['db']}] {item['timestamp']} {item['event_type']}{detail}")


_RUN_ROOT_OPTION_HELP = (
    "Run root to scan (the --run-root used by `worker run`). May be repeated; "
    "the default lifecycle root is scanned too unless --no-default-root."
)


@cmd_worker.command("ps")
@click.option(
    "--run-root",
    "run_roots",
    type=click.Path(file_okay=False, path_type=Path),
    multiple=True,
    help=_RUN_ROOT_OPTION_HELP,
)
@click.option("--no-default-root", is_flag=True, help="Scan only the given --run-root values.")
@click.option("--all", "show_all", is_flag=True, help="Include exited, killed and legacy runs.")
@click.option(
    "--reconcile",
    is_flag=True,
    help="Write a killed exit marker for runs whose controller identity is gone.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def worker_ps(
    run_roots: tuple[Path, ...],
    no_default_root: bool,
    show_all: bool,
    reconcile: bool,
    as_json: bool,
) -> None:
    """List worker runs from their pid/heartbeat/exit records."""

    items = []
    for run_dir in worker_lifecycle.discover_worker_runs(
        run_roots, include_default=not no_default_root
    ):
        record = (
            worker_lifecycle.reconcile_worker_run(run_dir)
            if reconcile
            else worker_lifecycle.read_worker_run(run_dir)
        )
        if show_all or record["state"] in worker_lifecycle.ACTIVE_STATES:
            items.append(record)
    if as_json:
        click.echo(json.dumps({"items": items, "total": len(items)}, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("No worker runs.")
        return
    for item in items:
        exit_info = item.get("exit") or {}
        click.echo(
            f"{item['run_id']}  {item['state']:<15} provider={item.get('provider') or '-'}"
            f" pid={item.get('pid') or '-'}"
            f" heartbeat_age={_fmt_age(item.get('heartbeat_age_s'))}"
            f" activity_age={_fmt_age(item.get('last_activity_age_s'))}"
            + (f" exit={exit_info.get('status')}" if exit_info else "")
        )


@cmd_worker.command("status")
@click.argument("run_ref")
@click.option(
    "--run-root",
    "run_roots",
    type=click.Path(file_okay=False, path_type=Path),
    multiple=True,
    help=_RUN_ROOT_OPTION_HELP,
)
@click.option(
    "--reconcile",
    is_flag=True,
    help="Write a killed exit marker if the controller identity is gone.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def worker_status(run_ref: str, run_roots: tuple[Path, ...], reconcile: bool, as_json: bool) -> None:
    """Show one worker run by run id or run directory."""

    run_dir = worker_lifecycle.find_worker_run(run_ref, run_roots)
    if run_dir is None:
        raise click.ClickException(f"worker run not found: {run_ref}")
    record = (
        worker_lifecycle.reconcile_worker_run(run_dir)
        if reconcile
        else worker_lifecycle.read_worker_run(run_dir)
    )
    if as_json:
        click.echo(json.dumps(record, ensure_ascii=False, indent=2))
        return
    for key in (
        "run_id",
        "state",
        "provider",
        "pid",
        "run_dir",
        "started_at",
        "heartbeat_age_s",
        "last_activity_age_s",
        "timeout_s",
        "idle_timeout_s",
    ):
        click.echo(f"{key:<20}: {record.get(key)}")
    containment = record.get("containment") or {}
    click.echo(f"{'containment':<20}: {containment.get('status', '-')}")
    exit_info = record.get("exit") or {}
    if exit_info:
        click.echo(
            f"{'exit':<20}: {exit_info.get('status')} code={exit_info.get('exit_code')}"
            f" writer={exit_info.get('writer')} {exit_info.get('reason') or ''}".rstrip()
        )


def _fmt_age(value: Any) -> str:
    return "-" if value is None else f"{float(value):.0f}s"


def _resolve_total_timeout(value: float | None) -> float | None:
    if value is None:
        # A fixed wall-clock cap would kill legitimate long runs; the deadline is opt-in.
        return None
    if value < 0:
        raise click.UsageError("--timeout must be >= 0 (0 disables the total deadline)")
    return None if value == 0 else float(value)


def _resolve_idle_timeout(value: float | None, metadata: dict[str, Any]) -> float | None:
    if value is None:
        raw = metadata.get("idle_timeout_s")
        if raw in (None, ""):
            # Output silence is not idleness: an orchestrator can wait on a long child
            # command without emitting events, so the idle deadline is opt-in.
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise click.UsageError(f"metadata idle_timeout_s must be a number, got {raw!r}") from exc
    if value < 0:
        raise click.UsageError("--idle-timeout must be >= 0 (0 disables the idle timeout)")
    return None if value == 0 else float(value)


def _install_cancel_signal_handlers(lifecycle: worker_lifecycle.WorkerLifecycle):
    """Turn SIGTERM / Ctrl+Break into a cancel that still writes exit.json."""

    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous: dict[Any, Any] = {}

    def handler(signum, frame):  # noqa: ARG001 - signal handler signature
        lifecycle.cancel_reason = f"signal {signal.Signals(signum).name}"
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, handler)
        except (OSError, ValueError):
            continue

    def restore() -> None:
        for sig, prev in previous.items():
            try:
                signal.signal(sig, prev)
            except (OSError, ValueError, TypeError):
                pass

    return restore


def _read_task_text(*, prompt: str | None, spec_path: Path | None, from_stdin: bool) -> str:
    sections: list[str] = []
    if spec_path is not None:
        sections.append(spec_path.read_text(encoding="utf-8"))
    if from_stdin:
        sections.append(sys.stdin.read())
    if prompt:
        sections.append(prompt)
    task = _clean_cli_text("\n\n".join(section.strip() for section in sections if section.strip()).strip())
    if not task:
        raise click.UsageError("provide a task via --spec, --stdin, or --prompt")
    return task


def _e_scope_rule_context(task: str, cwd: Path) -> tuple[str, ...]:
    """Attach the E: entry rule as context while retaining the exact task body."""
    if _E_SCOPE_TASK_TARGET.search(task) or cwd.drive.casefold() == "e:":
        return (_E_SCOPE_RULE_POINTER,)
    return ()


def _prepare_run_record_dir(run_root_path: Path, run_id: str) -> Path:
    root = run_root_path.expanduser().resolve()
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _write_run_input_record(
    *,
    run_dir: Path,
    provider: str,
    cwd: Path,
    permission_mode: str,
    timeout_s: float,
    prompt: str,
    attached_context: list[str],
    image_paths: list[Path],
    watch_paths: tuple[Path, ...],
    env_keys: list[str],
    metadata: dict[str, Any],
) -> None:
    prompt_path = run_dir / "prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    for index, context in enumerate(attached_context, start=1):
        (run_dir / f"context_{index:02d}.md").write_text(context, encoding="utf-8")
    request_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "cwd": str(cwd),
        "permission_mode": permission_mode,
        "timeout_s": timeout_s,
        "prompt_path": str(prompt_path),
        "attached_context_count": len(attached_context),
        "image_inputs": [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256_path(path),
            }
            for path in image_paths
        ],
        "watch_paths": [str(path) for path in watch_paths],
        "env_keys": env_keys,
        "metadata": metadata,
    }
    _write_json_no_bom(run_dir / "request.json", request_payload)


def _write_json_no_bom(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_attached_context(
    context_paths: tuple[Path, ...],
    context_texts: tuple[str, ...],
    context_alias_items: tuple[str, ...],
    *,
    cwd: Path,
) -> list[str]:
    context: list[str] = []
    for path in context_paths:
        path = _resolve_context_file(path, cwd=cwd)
        context.append(
            f"# Attached context file: {path.resolve()}\n\n"
            + path.read_text(encoding="utf-8")
        )
    for item in context_alias_items:
        alias, path = _parse_context_alias(item, cwd=cwd)
        context.append(
            f"# Attached context alias: {alias}\n"
            f"# Source path: {path.resolve()}\n\n"
            + path.read_text(encoding="utf-8")
        )
    context.extend(_clean_cli_text(text) for text in context_texts if text.strip())
    return context


def _clean_cli_text(text: str) -> str:
    """Remove invalid surrogate code points introduced by Windows stdio.

    Project files are UTF-8 no BOM. If PowerShell or a terminal transport
    decodes stdin/argv with surrogateescape, writing that text as UTF-8 raises
    UnicodeEncodeError. Keep the CLI durable by replacing invalid fragments at
    the boundary instead of letting run-record creation crash.
    """

    return text.encode("utf-8", errors="replace").decode("utf-8")


def _parse_context_alias(item: str, *, cwd: Path) -> tuple[str, Path]:
    if "=" not in item:
        raise click.UsageError(f"--context-alias values must be alias=path, got {item!r}")
    alias, raw_path = item.split("=", 1)
    alias = alias.strip()
    raw_path = raw_path.strip()
    if not alias:
        raise click.UsageError("--context-alias alias cannot be empty")
    if not alias.replace("_", "").replace("-", "").isalnum() or not alias.isascii():
        raise click.UsageError("--context-alias alias must be ASCII letters, numbers, '_' or '-'")
    path = _resolve_context_file(Path(raw_path), cwd=cwd)
    return alias, path


def _resolve_context_file(path: Path, *, cwd: Path) -> Path:
    resolved = path.expanduser()
    if not resolved.is_absolute():
        resolved = cwd / resolved
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise click.UsageError(f"context path must be an existing file: {resolved}")
    return resolved


def _resolve_image_file(path: Path, *, cwd: Path) -> Path:
    resolved = path.expanduser()
    resolved = (resolved if resolved.is_absolute() else cwd / resolved).resolve()
    try:
        resolved.relative_to(cwd)
    except ValueError as exc:
        raise click.UsageError(f"--image must stay under --cwd: {resolved}") from exc
    if not resolved.is_file():
        raise click.UsageError(f"--image must be an existing file: {resolved}")
    return resolved


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_key_values(items: tuple[str, ...], *, option_name: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise click.UsageError(f"{option_name} values must be key=value, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise click.UsageError(f"{option_name} key cannot be empty")
        parsed[key] = value
    return parsed


def _print_human_result(payload: dict[str, Any]) -> None:
    click.echo(f"run_id  : {payload.get('run_id')}")
    click.echo(f"provider: {payload.get('provider')}")
    click.echo(f"status  : {payload.get('status')}")
    if payload.get("changed_files"):
        click.echo("changed_files:")
        for path in payload["changed_files"]:
            click.echo(f"  - {path}")
    if payload.get("diff_summary"):
        click.echo("diff_summary:")
        click.echo(str(payload["diff_summary"]))
    if payload.get("error"):
        click.echo(f"error   : {payload.get('error')}")
    final_text = str(payload.get("final_text") or "").strip()
    if final_text:
        click.echo("")
        click.echo(final_text)


def _exit_code_for_status(status: ExternalAgentStatus) -> int:
    if status == ExternalAgentStatus.SUCCEEDED:
        return 0
    if status == ExternalAgentStatus.PERMISSION_VIOLATION:
        return 3
    if status == ExternalAgentStatus.TIMED_OUT:
        return 4
    return 2


def _read_trace_events(*, trace_id: str, db_selector: str, limit: int) -> list[dict[str, Any]]:
    from omnicompany.core.config import resolve_unified_db_path

    basenames: list[tuple[str, str]]
    if db_selector == "events":
        basenames = [("events", "events.db")]
    elif db_selector == "ide":
        basenames = [("ide", "ide_events.db")]
    else:
        basenames = [("events", "events.db"), ("ide", "ide_events.db")]

    rows_out: list[dict[str, Any]] = []
    per_db_limit = max(1, limit)
    for label, basename in basenames:
        db_path = resolve_unified_db_path(basename)
        if not db_path.is_file():
            continue
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT trace_id, parent_id, event_type, source, tags, timestamp, data "
                    "FROM events WHERE trace_id=? ORDER BY timestamp LIMIT ?",
                    (trace_id, per_db_limit),
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        for row in rows:
            data = _parse_event_data(row["data"])
            payload = data.get("payload") if isinstance(data, dict) else None
            rows_out.append(
                {
                    "db": label,
                    "trace_id": row["trace_id"],
                    "parent_id": row["parent_id"],
                    "event_type": row["event_type"],
                    "source": row["source"],
                    "tags": _parse_json_maybe(row["tags"], default=[]),
                    "timestamp": row["timestamp"],
                    "payload": payload,
                }
            )
    rows_out.sort(key=lambda item: str(item.get("timestamp") or ""))
    return rows_out[:limit]


def _parse_event_data(raw: str) -> dict[str, Any]:
    parsed = _parse_json_maybe(raw, default={})
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_maybe(raw: str, *, default: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


__all__ = ["cmd_worker"]
