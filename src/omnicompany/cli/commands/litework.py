"""Manage the external LiTeWork development host from OmniCompany."""

from __future__ import annotations

import json
from pathlib import Path

import click

from omnicompany.packages.services._core import litework_host as host

from .._access import access, any_caller


def _echo(payload: object) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _refuse_internal_session(action: str) -> None:
    """Refuse a LiTeWork restart asked for from inside a LiTeWork terminal.

    Only the runtime being restarted matters.  A LiTeWork terminal restarting
    LiTeWork loses its own caller mid-flight, so nobody is left to observe
    whether recovery finished; a caller hosted anywhere else is an ordinary
    operator and is allowed through.
    """
    session = host.detect_internal_session(runtime="litework")
    if session is None:
        return
    raise click.ClickException(
        f"{action} refused: this command is running inside a LiTeWork terminal "
        f"({session['marker']}={session['value']}), which this restart would "
        "take down along with its own caller. Run it from a terminal outside "
        "LiTeWork, or let `omni litework guard` recover the host on its own."
    )


def _live_terminal_rows() -> list[dict]:
    try:
        return host.live_terminals()
    except Exception as exc:  # noqa: BLE001 - a refusal must not depend on a healthy host
        raise click.ClickException(
            "cannot enumerate LiTeWork terminals, so a session-destroying action "
            f"cannot be authorized: {exc}"
        ) from exc


@click.group("litework")
def cmd_litework() -> None:
    """External LiTeWork watchdog, safe restart and runtime status."""


@cmd_litework.command("configure")
@click.option("--root", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option("--mode", type=click.Choice(["open", "serve"]), default="serve", show_default=True)
@click.option("--port", type=click.IntRange(1, 65535), default=6770, show_default=True)
@click.option("--pairing-address", default="litework.example.com", show_default=True)
@click.option("--profile-dir", type=click.Path(path_type=Path, file_okay=False), default=None)
@click.option("--poll-seconds", type=click.FloatRange(min=2.0), default=10.0, show_default=True)
@click.option(
    "--cloud-api-url",
    default="",
    help="Pin the LiWork cloud environment (e.g. https://liwork-server.the_company.com); "
    "empty inherits the build default (test for unpackaged dev).",
)
@access(allow={"external", "controller"})
def litework_configure(
    root: Path,
    mode: str,
    port: int,
    pairing_address: str,
    profile_dir: Path | None,
    poll_seconds: float,
    cloud_api_url: str,
) -> None:
    """Persist the local LiTeWork dev checkout and launch mode."""
    _echo(host.configure(
        root,
        mode=mode,
        port=port,
        pairing_address=pairing_address,
        profile_dir=profile_dir,
        poll_seconds=poll_seconds,
        cloud_api_url=cloud_api_url,
    ).__dict__)


@cmd_litework.command("status")
@any_caller
def litework_status() -> None:
    """Show guard and LiTeWork runtime health."""
    config = host.load_config()
    payload = host.guard_status(config)
    try:
        payload["live_terminals"] = len(host.live_terminals(config))
    except Exception as exc:  # noqa: BLE001 - status must survive an unreachable host
        payload["live_terminals"] = None
        payload["live_terminals_error"] = str(exc)
    payload["internal_session"] = host.detect_internal_session()
    _echo(payload)


@cmd_litework.command("sessions")
@any_caller
def litework_sessions() -> None:
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


@cmd_litework.command("ensure")
@any_caller
def litework_ensure() -> None:
    """Start a missing runtime without killing any existing process/session."""
    payload = host.ensure_runtime()
    _echo(payload)
    if not payload.get("ok"):
        raise click.exceptions.Exit(1)


@cmd_litework.command("reload")
@any_caller
def litework_reload() -> None:
    """Reload only the desktop renderer; terminal sessions are untouched."""
    _echo(host.reload_renderer())


@cmd_litework.command("restart")
@click.option("--yes", is_flag=True, help="Skip the interactive prompt (clients still drop).")
@access(allow={"external", "controller"})
def litework_restart(yes: bool) -> None:
    """Restart the host and fail if any terminal handle is lost.

    Terminals survive in the daemon, but every attached web/desktop client is
    disconnected, so this still needs a human behind it.
    """
    _refuse_internal_session("app restart")
    live = len(_live_terminal_rows())
    if not yes:
        click.confirm(
            f"Restart the LiTeWork host? {live} terminal session(s) stay alive in the "
            "daemon, but every attached browser/desktop client is disconnected",
            abort=True,
        )
    # The handover runs through the checkout's deployment tool, which rebuilds
    # nothing but does wait for the Host to answer; minutes, not seconds.
    click.echo("restarting the LiTeWork host (deployment handover, can take a few minutes)…",
               err=True)
    _echo(host.restart_preserving_sessions())


@cmd_litework.command("full-restart")
@click.option("--confirm", default="", help=f"First confirmation token: {host.FULL_RESTART_CONFIRMATION}")
@click.option(
    "--destroy-terminals",
    type=int,
    default=None,
    help="Acknowledge the exact number of live terminals this will destroy "
         "(see `omni litework sessions`). Omit it only when none are running.",
)
@access(allow={"external"})
def litework_full_restart(confirm: str, destroy_terminals: int | None) -> None:
    """Destroy all LiTeWork sessions and daemon state, then start clean."""
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
            "`omni litework sessions`."
        )
    click.confirm(
        f"This will close every LiTeWork terminal session ({len(live)} live) and the "
        "session daemon. Confirm the destructive restart",
        abort=True,
    )
    _echo(host.full_restart())


@cmd_litework.group("guard")
def litework_guard() -> None:
    """Manage the detached, non-destructive LiTeWork guard process."""


@litework_guard.command("start")
@any_caller
def litework_guard_start() -> None:
    _echo(host.start_guard())


@litework_guard.command("status")
@any_caller
def litework_guard_status() -> None:
    _echo(host.guard_status(host.load_config()))


@litework_guard.command("stop")
@any_caller
def litework_guard_stop() -> None:
    """Stop the watchdog. Terminal sessions and the running host are untouched."""
    _echo(host.stop_guard())
