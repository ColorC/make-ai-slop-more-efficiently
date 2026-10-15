"""Operator controls for the durable Codex capacity retry facility."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import click

from .cc import _pty_daemon_base


def _request(path: str, *, method: str = "GET") -> dict:
    req = urllib.request.Request(f"{_pty_daemon_base()}{path}", method=method)
    # A batch may confirm several native submissions sequentially. A short
    # transport timeout must not obscure the durable server-side receipts.
    timeout = 120 if path == "/cc/capacity-retry/tick" else 30
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - loopback daemon
        value = json.loads(response.read() or b"{}")
    return value if isinstance(value, dict) else {}


@click.group("codex-capacity-retry")
def cmd_codex_capacity_retry() -> None:
    """Inspect, tick, or disable durable Codex capacity retries."""


@cmd_codex_capacity_retry.command("status")
def status() -> None:
    click.echo(json.dumps(_request("/cc/capacity-retry"), indent=2, ensure_ascii=False))


@cmd_codex_capacity_retry.command("tick")
def tick() -> None:
    click.echo(json.dumps(_request("/cc/capacity-retry/tick", method="POST"), indent=2, ensure_ascii=False))


@cmd_codex_capacity_retry.command("disable")
@click.option("--session-id", default=None, help="Disable only one exact managed session.")
def disable(session_id: str | None) -> None:
    suffix = ""
    if session_id:
        suffix = "?" + urllib.parse.urlencode({"session_id": session_id})
    click.echo(json.dumps(_request(f"/cc/capacity-retry/disable{suffix}", method="POST"), indent=2, ensure_ascii=False))
