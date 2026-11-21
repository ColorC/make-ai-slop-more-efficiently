"""Manage the external WorkHost development host from OmniCompany."""

from __future__ import annotations

import json
from pathlib import Path

import click

from omnicompany.packages.services._core import workhost_host as host

from .._access import access, any_caller


def _echo(payload: object) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _refuse_internal_session(action: str) -> None:
    """Refuse a WorkHost restart asked for from inside a WorkHost terminal.

    Only the runtime being restarted matters.  A WorkHost terminal restarting
    WorkHost loses its own caller mid-flight, so nobody is left to observe
    whether recovery finished; a caller hosted anywhere else is an ordinary
    operator and is allowed through.
    """
    session = host.detect_internal_session(runtime="workhost")
    if session is None:
        return
    raise click.ClickException(
        f"{action} refused: this command is running inside a WorkHost terminal "
        f"({session['marker']}={session['value']}), which this restart would "
        "take down along with its own caller. Run it from a terminal outside "
        "WorkHost, or let `omni workhost guard` recover the host on its own."
    )


def _live_terminal_rows() -> list[dict]:
    try:
        return host.live_terminals()
    except Exception as exc:  # noqa: BLE001 - a refusal must not depend on a healthy host
        raise click.ClickException(
            "cannot enumerate WorkHost terminals, so a session-destroying action "
            f"cannot be authorized: {exc}"
        ) from exc


@click.group("workhost")
def cmd_workhost() -> None:
    """External WorkHost watchdog, safe restart and runtime status."""


@cmd_workhost.command("configure")
@click.option("--root", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option("--mode", type=click.Choice(["open", "serve"]), default="serve", show_default=True)
@click.option("--port", type=click.IntRange(1, 65535), default=6770, show_default=True)
@click.option("--pairing-address", default="workhost.example.com", show_default=True)
@click.option("--profile-dir", type=click.Path(path_type=Path, file_okay=False), default=None)
@click.option("--poll-seconds", type=click.FloatRange(min=2.0), default=10.0, show_default=True)
@click.option(
    "--cloud-api-url",
    default="",
    help="Pin the WorkHost cloud environment (e.g. https://internal-service.example.com); "
    "empty inherits the build default (test for unpackaged dev).",
)
@access(allow={"external", "controller"})
def workhost_configure(
    root: Path,
    mode: str,
    port: int,
    pairing_address: str,
    profile_dir: Path | None,
    poll_seconds: float,
    cloud_api_url: str,
) -> None:
    """Persist the local WorkHost dev checkout and launch mode."""
    _echo(host.configure(
        root,
        mode=mode,
        port=port,
        pairing_address=pairing_address,
        profile_dir=profile_dir,
        poll_seconds=poll_seconds,
        cloud_api_url=cloud_api_url,
    ).__dict__)


@cmd_workhost.command("status")
@any_caller
def workhost_status() -> None:
    """Show guard and WorkHost runtime health."""
    config = host.load_config()
    payload = host.guard_status(config)
    try:
        payload["live_terminals"] = len(host.live_terminals(config))
    except Exception as exc:  # noqa: BLE001 - status must survive an unreachable host
        payload["live_terminals"] = None
        payload["live_terminals_error"] = str(exc)
    payload["internal_session"] = host.detect_internal_session()
    _echo(payload)


@cmd_workhost.command("sessions")
@any_caller
def workhost_sessions() -> None:
    """List the terminal sessions a destructive restart would destroy."""
    rows = _live_terminal_rows()
    _echo({
        "count": len(rows),
        "terminals": [
            {
                "handle": row.get("handle"),
                "title": row.get("title"),
                "worktree_path": row.get("worktreePath"),
                "branch": row.get("branch"),
                "connected": row.get("connected") is not False,
            }
            for row in rows
        ],
    })


@cmd_workhost.command("ensure")
@any_caller
def workhost_ensure() -> None:
    """Start a missing runtime without killing any existing process/session."""
    payload = host.ensure_runtime()
    _echo(payload)
    if not payload.get("ok"):
        raise click.exceptions.Exit(1)


@cmd_workhost.command("reload")
@any_caller
def workhost_reload() -> None:
    """Reload only the desktop renderer; terminal sessions are untouched."""
    _echo(host.reload_renderer())


@cmd_workhost.command("restart")
@click.option("--yes", is_flag=True, help="Skip the interactive prompt (clients still drop).")
@access(allow={"external", "controller"})
def workhost_restart(yes: bool) -> None:
    """Restart the host and fail if any terminal handle is lost.

    Terminals survive in the daemon, but every attached web/desktop client is
    disconnected, so this still needs a human behind it.
    """
    _refuse_internal_session("app restart")
    live = len(_live_terminal_rows())
    if not yes:
        click.confirm(
            f"Restart the WorkHost host? {live} terminal session(s) stay alive in the "
            "daemon, but every attached browser/desktop client is disconnected",
            abort=True,
        )
    # The handover runs through the checkout's deployment tool, which rebuilds
    # nothing but does wait for the Host to answer; minutes, not seconds.
    click.echo("restarting the WorkHost host (deployment handover, can take a few minutes)…",
               err=True)
    _echo(host.restart_preserving_sessions())


@cmd_workhost.command("full-restart")
@click.option("--confirm", default="", help=f"First confirmation token: {host.FULL_RESTART_CONFIRMATION}")
@click.option(
    "--destroy-terminals",
    type=int,
    default=None,
    help="Acknowledge the exact number of live terminals this will destroy "
         "(see `omni workhost sessions`). Omit it only when none are running.",
)
@access(allow={"external"})
def workhost_full_restart(confirm: str, destroy_terminals: int | None) -> None:
    """Destroy all WorkHost sessions and daemon state, then start clean."""
    if confirm != host.FULL_RESTART_CONFIRMATION:
        raise click.ClickException(
            f"full restart refused; pass --confirm {host.FULL_RESTART_CONFIRMATION}"
        )
    _refuse_internal_session("full restart")
    live = _live_terminal_rows()
    acknowledged = 0 if destroy_terminals is None else destroy_terminals
    if acknowledged != len(live):
        # Why: "no sessions are running" must be proven against the live runtime
        # at the moment of the call, not remembered from when the operator looked.
        raise click.ClickException(
            f"full restart refused: {len(live)} live terminal session(s) would be "
            f"destroyed, acknowledgement was {acknowledged}. Close them, or re-run "
            f"with --destroy-terminals {len(live)} after checking "
            "`omni workhost sessions`."
        )
    click.confirm(
        f"This will close every WorkHost terminal session ({len(live)} live) and the "
        "session daemon. Confirm the destructive restart",
        abort=True,
    )
    _echo(host.full_restart())


@cmd_workhost.group("guard")
def workhost_guard() -> None:
    """Manage the detached, non-destructive WorkHost guard process."""


@workhost_guard.command("start")
@any_caller
def workhost_guard_start() -> None:
    _echo(host.start_guard())


@workhost_guard.command("status")
@any_caller
def workhost_guard_status() -> None:
    _echo(host.guard_status(host.load_config()))


@workhost_guard.command("stop")
@any_caller
def workhost_guard_stop() -> None:
    """Stop the watchdog. Terminal sessions and the running host are untouched."""
    _echo(host.stop_guard())
