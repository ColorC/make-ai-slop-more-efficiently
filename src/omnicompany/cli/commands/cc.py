# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:cli.claude_code.wrapper.settings_installer.py"
"""omni cc — native Claude Code / Codex lifecycle integration management.

Subcommands (single source of truth — dashboard install button calls these):
    omni cc install   [--provider claude_code|codex] [--scope project|user]
    omni cc uninstall [--scope project|user]   remove only the entries we own
    omni cc status    [--scope project|user]   show what's currently wired
"""

import json
import os
from pathlib import Path

import click

from omnicompany.dashboard.ccdaemon import codex_installer as ci
from omnicompany.dashboard.ccdaemon import installer as si


_CLAUDE_ALTERNATE_SOURCE_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_VERTEX",
}

_CLAUDE_PARENT_SESSION_ENV = {
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_PID",
}

#: 只属于"发起重启的那个 agent 会话"的账号与身份变量; 见 _ccdaemon_process_env。
_AGENT_SESSION_ACCOUNT_ENV = {
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "CODEX_SESSION_ID",
    "OMNI_CC_PTY_ID",
    "OMNI_AGENT_PROVIDER",
}


def _ccdaemon_process_env(source_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build a clean daemon environment for dashboard-owned agent sessions.

    ``omni cc daemon`` is often restarted from inside another agent CLI. Blindly
    copying that process environment made every later Dashboard Claude PTY inherit
    the launcher's alternate Anthropic endpoint/model and stale Claude session
    identity. Dashboard Claude sessions default to the official Claude login on
    disk; set ``OMNI_CC_CLAUDE_SOURCE=inherit`` only when custom endpoint/API-key
    inheritance is intentional.
    """
    env = dict(os.environ if source_env is None else source_env)
    launched_inside_agent_session = bool(env.get("OMNI_CC_PTY_ID") or env.get("CLAUDECODE"))
    for key in _CLAUDE_PARENT_SESSION_ENV:
        env.pop(key, None)
    if launched_inside_agent_session:
        # 从某个 agent 会话里重启 daemon 时, 该会话的账号家目录(第二账号的
        # CODEX_HOME / CLAUDE_CONFIG_DIR)和会话身份会被 daemon 继承, 之后面板起的
        # "官方一号"会话其实全跑在二号账号上, 台账也会记错发起会话(2026-09-05 实测)。
        # 只有会话里发起的重启才剥这些; 用户在系统环境里刻意设的家目录不受影响。
        for key in _AGENT_SESSION_ACCOUNT_ENV:
            env.pop(key, None)
    source = env.get("OMNI_CC_CLAUDE_SOURCE", "official").strip().lower()
    if source != "inherit":
        for key in _CLAUDE_ALTERNATE_SOURCE_ENV:
            env.pop(key, None)
    return env


def _integration_installer(provider: str):
    return ci if provider == "codex" else si


@click.group("cc")
def cmd_cc():
    """Native Claude Code/Codex integration commands (historical `cc` name)."""


@cmd_cc.command("install")
@click.option("--scope", type=click.Choice(["project", "user"]), default="project",
              help="Project scope is recommended; user scope affects every native session.")
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), default="claude_code",
              show_default=True)
def cc_install(scope: str, provider: str) -> None:
    """Wire Omnicompany lifecycle hooks into a native agent."""
    target = _integration_installer(provider)
    rep = target.install(scope=scope)  # type: ignore[arg-type]
    payload = {
        "provider": provider,
        "settings_path": rep.settings_path,
        "backup": rep.backup,
        "hooks_added_or_updated": rep.hooks_added,
        "hooks_unchanged": rep.hooks_unchanged,
        "note": rep.note,
    }
    if hasattr(rep, "mcp_added"):
        payload["mcp_added_or_updated"] = rep.mcp_added
    if hasattr(rep, "requires_trust"):
        payload["requires_trust"] = rep.requires_trust
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@cmd_cc.command("uninstall")
@click.option("--scope", type=click.Choice(["project", "user"]), default="project")
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), default="claude_code",
              show_default=True)
def cc_uninstall(scope: str, provider: str) -> None:
    """Remove only entries Omnicompany installed; preserve unrelated settings."""
    rep = _integration_installer(provider).uninstall(scope=scope)  # type: ignore[arg-type]
    rep["provider"] = provider
    click.echo(json.dumps(rep, indent=2, ensure_ascii=False))


@cmd_cc.command("status")
@click.option("--scope", type=click.Choice(["project", "user"]), default="project")
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), default="claude_code",
              show_default=True)
def cc_status(scope: str, provider: str) -> None:
    """Show whether the integration is currently installed at the given scope."""
    rep = _integration_installer(provider).status(scope=scope)  # type: ignore[arg-type]
    rep["provider"] = provider
    click.echo(json.dumps(rep, indent=2, ensure_ascii=False))


@cmd_cc.command("safety-install")
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), required=True)
@click.option("--scope", type=click.Choice(["project", "user"]), default="user")
@click.option("--settings-path", type=click.Path(path_type=Path),
              help="One explicit ancestor/project config to migrate; never scans projects.")
@click.option("--cleanup-only", is_flag=True,
              help="Remove the old Omni hooks from this scope; use with a user safety installation.")
@click.option("--receipt", type=click.Path(path_type=Path),
              help="Absolute unused JSON receipt path including all backup paths.")
def cc_safety_install(provider: str, scope: str, settings_path: Path | None,
                      cleanup_only: bool, receipt: Path | None) -> None:
    """Install only the fast destructive-operation guard, preserving unrelated hooks."""
    from omnicompany.core.safety_installer import install

    if settings_path is not None and not settings_path.is_absolute():
        raise click.UsageError("settings-path must be absolute")
    if receipt is not None and (not receipt.is_absolute() or receipt.exists()):
        raise click.UsageError("receipt must be an absolute, unused file path")
    try:
        result = install(provider=provider, scope=scope, path=settings_path,
                         add_hook=not cleanup_only)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    if receipt is not None:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        result["files"].append({"path": str(receipt), "purpose": "safety hook installation receipt"})
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@cmd_cc.command("repair-codex-launcher")
@click.option("--receipt", type=click.Path(path_type=Path), required=True)
def cc_repair_codex_launcher(receipt: Path) -> None:
    """Make the obsolete pinned Windows shims follow the installed npm CLI."""
    import subprocess

    if os.name != "nt":
        raise click.ClickException("this repair targets Windows Codex shims")
    if not receipt.is_absolute() or receipt.exists():
        raise click.UsageError("receipt must be an absolute, unused file path")
    appdata = Path(os.environ["APPDATA"])
    shim_dir = Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "cli-0.159.2-bin"
    entry = appdata / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if not entry.is_file():
        raise click.ClickException(f"installed npm Codex entry is missing: {entry}")
    verified = subprocess.run(["node", str(entry), "--version"], capture_output=True,
                              text=True, timeout=15, check=False)
    if verified.returncode != 0 or not verified.stdout.startswith("codex-cli "):
        raise click.ClickException(f"installed Codex failed --version: {entry}")
    replacements = {
        "codex.cmd": '@echo off\r\nnode "%APPDATA%\\npm\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\nexit /b %ERRORLEVEL%\r\n',
        "codex.ps1": "& node (Join-Path $env:APPDATA 'npm/node_modules/@openai/codex/bin/codex.js') @args\nexit $LASTEXITCODE\n",
    }
    artifacts = []
    receipt.parent.mkdir(parents=True, exist_ok=True)
    for name, body in replacements.items():
        target = shim_dir / name
        if not target.is_file() or target.is_symlink():
            raise click.ClickException(f"expected regular existing Codex shim: {target}")
        backup = receipt.parent / (name + ".before-launcher-repair")
        with backup.open("xb") as stream:
            stream.write(target.read_bytes())
        target.write_bytes(body.encode("utf-8"))
        artifacts.extend([{"path": str(target), "role": "Codex launcher forwarding to installed npm CLI"},
                          {"path": str(backup), "role": "original pinned launcher for rollback"}])
    result = {"version": verified.stdout.strip(), "entry": str(entry), "artifacts": artifacts}
    result["artifacts"].append({"path": str(receipt), "role": "launcher repair receipt"})
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


# ── ccdaemon lifecycle ([2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE) ──
# 独立 uvicorn 进程, 持有 chat / pty 真业务. 跟 dashboard 控制面进程拆开,
# 确保 AI IDE 改控制面任意文件触发 reload 都不影响 chat 会话.
#
# 2026-08-20 口径修正（实测验证）: `omni cc daemon restart` 不杀 PTY 会话。
# 每个 pty 会话由独立的 detached 宿主进程持有 ConPTY 和原生 CLI
# (pty.py _spawn_detached_host, DETACHED_PROCESS|NEW_PROCESS_GROUP), daemon 只是
# 桥; 新 daemon 启动时 _discover_host_sessions 按 data/pty_hosts/*.json 重新接管。
# 实测: 25 个存活 pty 会话重启后宿主进程零损失。真正会断的只有 in-process 的
# chat 类会话(NativeIdeAgent 跑在 daemon 内存里)和浏览器页签的 websocket(会自动重连)。

def _pty_daemon_base() -> str:
    """ccdaemon 本机基址(pty 页签操作用); 未跑就直接报错, 不静默吞。"""
    from omnicompany.dashboard.ccdaemon import lifecycle
    s = lifecycle.read_status()
    if not (getattr(s, "alive", False) and getattr(s, "port", None)):
        raise click.ClickException("ccdaemon 未运行(先 `omni cc daemon start`)")
    return f"http://127.0.0.1:{s.port}"


@cmd_cc.group("session")
def cc_session() -> None:
    """PTY 页签生命周期和经用户授权的精确输入。"""


def _safe_session_inspection(row: dict) -> dict:
    """Project operational metadata while excluding terminal/input secrets."""
    import uuid

    known = row.get("provider_runtime_known")
    reclaim = row.get("reclaim")
    raw_provider_session_id = row.get("provider_session_id")
    provider_session_id = None
    if isinstance(raw_provider_session_id, str):
        try:
            canonical_id = str(uuid.UUID(raw_provider_session_id))
            if raw_provider_session_id == canonical_id:
                provider_session_id = canonical_id
        except (ValueError, AttributeError):
            pass
    if not isinstance(reclaim, dict):
        reclaim = {}
    return {
        "pty_id": row.get("id"),
        "provider": row.get("provider"),
        "provider_session_id": provider_session_id,
        "status": row.get("status"),
        "cwd": row.get("cwd"),
        "alive": row.get("alive"),
        "working": row.get("working") if known is True else None,
        "working_known": known if isinstance(known, bool) else None,
        "working_source": row.get("working_source"),
        "provider_runtime_state": row.get("provider_runtime_state") if known is True else None,
        "provider_runtime_marker_at": row.get("provider_runtime_marker_at"),
        "child_pid": row.get("child_pid"),
        "host_pid": row.get("host_pid"),
        "host_port": row.get("host_port"),
        "hosted": row.get("hosted"),
        "subscribers": row.get("subscribers"),
        "started_at": row.get("started_at"),
        "last_detach_at": row.get("last_detach_at"),
        "last_submit_at": row.get("last_submit_at"),
        "last_output_at": row.get("last_output_at"),
        "origin": row.get("origin"),
        "visibility": row.get("visibility"),
        "reclaim": {
            key: reclaim.get(key)
            for key in ("required", "origin", "ttl_seconds", "deadline", "overdue")
        },
    }


def _list_inspectable_sessions() -> list[dict]:
    from omnicompany.packages.services._core.agent.control_plane import list_agent_workers

    try:
        rows = list_agent_workers()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from None
    return [_safe_session_inspection(row) for row in rows]


@cc_session.command("list")
def cc_session_list() -> None:
    """List safe PTY/provider/reclaim metadata without reading terminal contents."""
    click.echo(json.dumps({"items": _list_inspectable_sessions()}, indent=2, ensure_ascii=False))


@cc_session.command("inspect")
@click.argument("pty_id")
def cc_session_inspect(pty_id: str) -> None:
    """Inspect one exact PTY id using the native ccdaemon client."""
    rows = _list_inspectable_sessions()
    row = next((item for item in rows if item.get("pty_id") == pty_id), None)
    if row is None:
        click.echo(json.dumps({"status": "not_found", "pty_id": pty_id}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(row, indent=2, ensure_ascii=False))


@cc_session.command("input")
@click.argument("pty_id")
@click.option("--text", default=None, help="用户授权发送的正文；现有 PTY 服务分开写正文和 Enter 并确认提交。")
@click.option("--interrupt", is_flag=True, help="仅向此 PTY 发送 Ctrl+C，不关闭会话。")
@click.option("--user-authorized", is_flag=True, help="声明当前用户明确授权此次输入；普通协作用 omni agent send。")
def cc_session_input(pty_id: str, text: str | None, interrupt: bool, user_authorized: bool) -> None:
    """通过现有输入设施控制一个精确 PTY；不把传输成功当作业务完成。"""
    import re

    if not user_authorized:
        raise click.UsageError("需要 --user-authorized；普通协作请用 omni agent send")
    if not re.fullmatch(r"[0-9a-f]{16}", pty_id):
        raise click.UsageError("需要精确的 16 位 PTY id，不能传 provider 原生会话 id")
    if interrupt == (text is not None):
        raise click.UsageError("必须且只能指定 --text 或 --interrupt")
    if text is not None and not text.strip():
        raise click.UsageError("--text 不能为空")

    from omnicompany.packages.services._core.agent.control_plane import send_agent_input

    try:
        result = send_agent_input(
            pty_id, "\x03" if interrupt else text,
            submit=not interrupt, daemon_base_url=_pty_daemon_base(),
        )
    except Exception as exc:  # noqa: BLE001 - surface the existing transport error
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("ok"):
        raise SystemExit(1)


@cc_session.command("probe")
@click.argument("pty_id")
@click.option("--receipt", type=click.Path(path_type=Path), required=True)
def cc_session_probe(pty_id: str, receipt: Path) -> None:
    """Measure terminal WebSocket round trips without sending keyboard input."""
    import asyncio
    import contextlib
    import re
    import statistics
    import time
    import websockets

    if not re.fullmatch(r"[0-9a-f]{16}", pty_id):
        raise click.UsageError("an exact native PTY id is required")
    if not receipt.is_absolute() or receipt.exists():
        raise click.UsageError("receipt must be an absolute, unused file path")

    async def measure_unbounded(url):
        async with websockets.connect(url, open_timeout=5, close_timeout=2, max_size=None) as ws:
            await ws.send(json.dumps({"type": "attach", "last_seq": 0}))

            async def drain():
                async for _ in ws:
                    pass

            reader = asyncio.create_task(drain())
            samples = []
            try:
                for _ in range(30):
                    started = time.perf_counter()
                    pong = await ws.ping()
                    await asyncio.wait_for(pong, timeout=2)
                    samples.append((time.perf_counter() - started) * 1000)
                    await asyncio.sleep(0.1)
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
            return {"samples": len(samples), "p50_ms": round(statistics.median(samples), 3),
                    "p95_ms": round(sorted(samples)[28], 3), "max_ms": round(max(samples), 3)}

    async def measure(url):
        return await asyncio.wait_for(measure_unbounded(url), timeout=20)

    async def run():
        daemon = _pty_daemon_base().replace("http://", "ws://")
        suffix = f"/sessions/{pty_id}/ws?client_protocol=focused-visible-v1"
        direct, gateway = await asyncio.gather(
            measure(daemon + "/cc" + suffix),
            measure("ws://127.0.0.1:8210/api/cc" + suffix),
        )
        return {"sampled_at": time.time(), "pty_id": pty_id, "keyboard_input_sent": False,
                "direct": direct, "dashboard_gateway": gateway,
                "artifact": {"path": str(receipt), "role": "read-only terminal WebSocket round-trip probe"}}

    try:
        result = asyncio.run(run())
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    except Exception as exc:
        raise click.ClickException(f"terminal probe failed for {receipt}: {type(exc).__name__}: {exc}") from exc
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@cc_session.command("kill")
@click.argument("pty_ids", nargs=-1, required=True)
def cc_session_kill(pty_ids: tuple[str, ...]) -> None:
    """关掉一个或多个 pty 页签(参数是 pty id, 不是 provider 侧的会话 id)。"""
    import urllib.error
    import urllib.request

    base = _pty_daemon_base()
    results = []
    for sid in pty_ids:
        req = urllib.request.Request(f"{base}/cc/sessions/{sid}", method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - 本机 daemon
                r.read()
            results.append({"id": sid, "ok": True})
        except urllib.error.HTTPError as e:
            # 404 = 页签本来就不在了, 对"清掉它"这个意图来说等价于成功。
            results.append({"id": sid, "ok": e.code == 404, "status": e.code,
                            "detail": "已不存在" if e.code == 404 else e.reason})
        except Exception as e:  # noqa: BLE001
            results.append({"id": sid, "ok": False, "detail": str(e)})
    click.echo(json.dumps({"killed": results}, indent=2, ensure_ascii=False))
    if not all(r["ok"] for r in results):
        raise SystemExit(1)


@cc_session.command("output")
@click.argument("pty_id")
@click.option("--limit", type=click.IntRange(1, 100000), default=4000, show_default=True)
def cc_session_output(pty_id: str, limit: int) -> None:
    """Read the terminal output of one exact PTY without sending input."""
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"{_pty_daemon_base()}/cc/sessions/{pty_id}/output?limit={limit}", timeout=30
        ) as response:
            body = json.loads(response.read() or b"{}")
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(body, ensure_ascii=False, indent=2))


@cc_session.command("resume")
@click.argument("pty_ids", nargs=-1, required=True)
def cc_session_resume(pty_ids: tuple[str, ...]) -> None:
    """Resume ended provider PTYs through the account-aware recovery path."""
    _resume_pty_sessions(pty_ids, "resume")


@cc_session.command("migrate-codex-account")
@click.argument("pty_ids", nargs=-1, required=True)
def cc_session_migrate_codex_account(pty_ids: tuple[str, ...]) -> None:
    """Move exact codex_2 PTYs to codex-1, preserving native history."""
    _resume_pty_sessions(pty_ids, "migrate-codex-account")


def _resume_pty_sessions(pty_ids: tuple[str, ...], action: str) -> None:
    import urllib.error
    import urllib.request

    base = _pty_daemon_base()
    results = []
    for sid in pty_ids:
        req = urllib.request.Request(
            f"{base}/cc/sessions/{sid}/{action}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310 - 本机 daemon
                body = json.loads(response.read() or b"{}")
            results.append({
                "old_id": sid,
                "ok": True,
                "new_id": body.get("id"),
                "provider": body.get("provider"),
                "provider_session_id": body.get("provider_session_id"),
                "cwd": body.get("cwd"),
                "artifacts": body.get("artifacts", []),
                "source_kept_open": body.get("source_kept_open"),
            })
        except urllib.error.HTTPError as exc:
            detail = exc.reason
            try:
                detail = json.loads(exc.read() or b"{}").get("detail") or detail
            except Exception:  # noqa: BLE001 - error reporting only
                pass
            results.append({"old_id": sid, "ok": False, "status": exc.code, "detail": detail})
        except Exception as exc:  # noqa: BLE001 - surface the existing transport error
            results.append({"old_id": sid, "ok": False, "detail": str(exc)})
    click.echo(json.dumps({"resumed": results}, indent=2, ensure_ascii=False))
    if not all(row["ok"] for row in results):
        raise SystemExit(1)


@cc_session.command("mark-user")
@click.argument("pty_ids", nargs=-1, required=True)
def cc_session_mark_user(pty_ids: tuple[str, ...]) -> None:
    """Mark live PTYs as user-created so they are not agent-reclaimable."""
    import urllib.error
    import urllib.request

    base = _pty_daemon_base()
    results = []
    for sid in pty_ids:
        req = urllib.request.Request(
            f"{base}/cc/sessions/{sid}/origin",
            data=json.dumps({"origin": "human"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310 - 本机 daemon
                body = json.loads(response.read() or b"{}")
            results.append({"id": sid, "ok": True, "origin": body.get("origin")})
        except urllib.error.HTTPError as exc:
            detail = exc.reason
            try:
                detail = json.loads(exc.read() or b"{}").get("detail") or detail
            except Exception:  # noqa: BLE001 - error reporting only
                pass
            results.append({"id": sid, "ok": False, "status": exc.code, "detail": detail})
        except Exception as exc:  # noqa: BLE001 - surface the existing transport error
            results.append({"id": sid, "ok": False, "detail": str(exc)})
    click.echo(json.dumps({"marked_user": results}, indent=2, ensure_ascii=False))
    if not all(row["ok"] for row in results):
        raise SystemExit(1)


def _post_pty_session_action(
    action: str,
    pty_ids: tuple[str, ...],
    *,
    provider_session_id: str | None = None,
) -> list[dict[str, object]]:
    import urllib.error
    import urllib.request

    base = _pty_daemon_base()
    results: list[dict[str, object]] = []
    for sid in pty_ids:
        payload = json.dumps(
            {"provider_session_id": provider_session_id}
            if provider_session_id
            else {}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/cc/sessions/{sid}/{action}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310 - 本机 daemon
                body = json.loads(response.read() or b"{}")
            results.append({"id": sid, "ok": True, "status": body.get("status")})
        except urllib.error.HTTPError as exc:
            detail = exc.reason
            try:
                detail = json.loads(exc.read() or b"{}").get("detail") or detail
            except Exception:  # noqa: BLE001 - error reporting only
                pass
            results.append({"id": sid, "ok": False, "status": exc.code, "detail": detail})
        except Exception as exc:  # noqa: BLE001
            results.append({"id": sid, "ok": False, "detail": str(exc)})
    return results


@cc_session.command("restore")
@click.argument("pty_ids", nargs=-1, required=True)
@click.option(
    "--provider-session-id",
    default=None,
    help="仅单个 PTY 时可覆盖 provider 原生 session id，用于修复错误关联。",
)
def cc_session_restore(
    pty_ids: tuple[str, ...],
    provider_session_id: str | None,
) -> None:
    """把误杀墓碑修回 recoverable；仅在原生历史真实存在时成功。"""
    if provider_session_id and len(pty_ids) != 1:
        raise click.ClickException("--provider-session-id 只能与单个 PTY id 一起使用")
    results = _post_pty_session_action(
        "restore",
        pty_ids,
        provider_session_id=provider_session_id,
    )
    click.echo(json.dumps({"restored": results}, indent=2, ensure_ascii=False))
    if not all(bool(row["ok"]) for row in results):
        raise SystemExit(1)


@cmd_cc.group("daemon")
def cc_daemon() -> None:
    """ccdaemon lifecycle — start / stop / restart / status."""


@cc_daemon.command("start")
@click.option("--port", type=int, default=8201, help="Listen port (default 8201).")
@click.option("--host", default="127.0.0.1")
@click.option("--reload/--no-reload", default=False,
              help="Enable file watcher reload (default off — reload 会重启 daemon 进程。"
                   "PTY 会话由独立 detached 宿主进程持有、会自动重接，不受影响;"
                   "只有 in-process chat 类会话会断。改 ccdaemon 文件后请走 "
                   "`omni cc daemon restart`).")
def cc_daemon_start(port: int, host: str, reload: bool) -> None:
    """Start the ccdaemon process (background)."""
    import subprocess
    import sys
    import time
    from omnicompany.dashboard.ccdaemon import lifecycle

    s = lifecycle.read_status()
    if s.serving:
        click.echo(json.dumps({"ok": False, "reason": "already running",
                                "pid": s.pid, "port": s.port}, indent=2))
        return
    if s.zombie:
        # pid 在但 /health 不应答 = 僵尸(挂死/启动卡住)。旧版这里因 s.alive=True 直接
        # 返回 "already running" 拒绝拉新 → daemon 永远起不来。改为先杀僵尸再续启。
        click.echo(json.dumps({"ok": False, "reason": "zombie detected (alive but not serving); killing then restarting",
                                "pid": s.pid, "port": s.port}, indent=2))
        ctx = click.get_current_context()
        ctx.invoke(cc_daemon_stop, timeout=5.0)
        for _ in range(20):
            if not lifecycle.read_status().alive:
                break
            time.sleep(0.2)

    log_path = lifecycle.log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 启动前 log 滚动 — 防长期 dogfood log 无限增长 (>10MB 自动轮转, 留 5 份历史)
    rotated = lifecycle.rotate_log_if_oversize()
    env = _ccdaemon_process_env()
    env["OMNI_CC_DAEMON_PORT"] = str(port)
    cmd = [
        sys.executable, "-m", "uvicorn",
        "omnicompany.dashboard.ccdaemon.main:app",
        "--host", host, "--port", str(port),
    ]
    if reload:
        cmd.extend(["--reload",
                    "--reload-dir", str(lifecycle._data_dir().parent / "src" / "omnicompany" / "dashboard" / "ccdaemon")])

    log_fd = open(log_path, "ab")
    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS 让子进程不跟 CLI 父进程绑, ctrl+c CLI 时 daemon 不死
        creationflags = 0x00000008  # DETACHED_PROCESS
    proc = subprocess.Popen(
        cmd, stdout=log_fd, stderr=subprocess.STDOUT,
        env=env, creationflags=creationflags,
    )
    # 不立刻关 log_fd, Popen 持有引用, 子进程退出时由 OS 释放
    click.echo(json.dumps({
        "ok": True, "pid": proc.pid, "port": port, "log": str(log_path),
        "log_rotated": rotated,
        "note": "daemon spawned; check status with `omni cc daemon status`",
    }, indent=2, ensure_ascii=False))


def _stop_daemon_process(pid: int, timeout: float) -> int | None:
    """Stop only the verified daemon process and wait for actual exit.

    Never walk children: detached PTY hosts survive a daemon reload. psutil
    supplies correctly typed Windows process handles and reports access errors;
    unchecked ctypes calls previously reported success while the daemon lived.
    """
    import psutil

    try:
        process = psutil.Process(pid)
        command = process.cmdline()
        if not any(arg in {"omnicompany.dashboard.ccdaemon.main:app",
                           "omnicompany.dashboard.ccdaemon.main"} for arg in command):
            raise RuntimeError(f"PID {pid} is not the registered ccdaemon command")
        process.terminate()
        try:
            exit_code = process.wait(timeout=max(0.1, timeout))
        except psutil.TimeoutExpired:
            process.kill()
            exit_code = process.wait(timeout=max(1.0, timeout))
        if process.is_running():
            raise RuntimeError(f"PID {pid} remains alive after shutdown")
        return exit_code
    except psutil.NoSuchProcess:
        return None


@cc_daemon.command("stop")
@click.option("--timeout", type=float, default=5.0,
              help="Seconds to wait for graceful shutdown before kill -9 / TerminateProcess.")
def cc_daemon_stop(timeout: float) -> None:
    """Stop the running ccdaemon (graceful → force after timeout)."""
    from omnicompany.dashboard.ccdaemon import lifecycle

    s = lifecycle.read_status()
    if not s.alive:
        click.echo(json.dumps({"ok": False, "reason": "not running"}, indent=2))
        return

    pid = s.pid
    assert pid is not None
    try:
        exit_code = _stop_daemon_process(pid, timeout)
    except Exception as exc:
        # Preserve the marker on failure, and prevent `restart` from claiming
        # success by adopting the same process it just failed to stop.
        raise click.ClickException(f"ccdaemon PID {pid} did not stop: {type(exc).__name__}: {exc}") from exc

    try:
        marker_pid = int(lifecycle.pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        marker_pid = None
    if marker_pid == pid:
        lifecycle.clear_pid()
    click.echo(json.dumps({"ok": True, "killed_pid": pid, "exit_code": exit_code}, indent=2))


@cc_daemon.command("restart")
@click.option("--port", type=int, default=8201)
@click.option("--host", default="127.0.0.1")
@click.pass_context
def cc_daemon_restart(ctx: click.Context, port: int, host: str) -> None:
    """Stop then start. Equivalent to `stop` followed by `start`.

    PTY 页签不受影响: 它们由独立 detached 宿主进程持有, 新 daemon 启动时自动
    重新接管(2026-08-20 实测 25 个存活会话零损失)。会断的只有 in-process
    chat 类会话。
    """
    from omnicompany.dashboard.ccdaemon import lifecycle
    import time

    s = lifecycle.read_status()
    if s.alive:
        ctx.invoke(cc_daemon_stop, timeout=5.0)
        # 等 OS 真释放端口
        for _ in range(20):
            if not lifecycle.read_status().alive:
                break
            time.sleep(0.2)
    ctx.invoke(cc_daemon_start, port=port, host=host, reload=False)


@cc_daemon.command("status")
def cc_daemon_status() -> None:
    """Show ccdaemon pid / port / alive."""
    from omnicompany.dashboard.ccdaemon import lifecycle
    s = lifecycle.read_status()
    click.echo(json.dumps({
        "alive": s.alive,        # 进程在(pid 活)
        "serving": s.serving,    # /health 真应答 = 在服务
        "zombie": s.zombie,      # alive 但不 serving = 挂死, 需 restart
        "pid": s.pid,
        "port": s.port,
        "pid_file": str(lifecycle.pid_file()),
        "port_file": str(lifecycle.port_file()),
        "log_file": str(lifecycle.log_file()),
    }, indent=2, ensure_ascii=False))


@cc_daemon.command("diagnostics")
@click.option("--receipt", type=click.Path(path_type=Path), required=True,
              help="Absolute JSON receipt path; an existing receipt is never overwritten.")
def cc_daemon_diagnostics(receipt) -> None:
    """Read live session ownership, process generations and passive latency."""
    import time
    import urllib.request
    import psutil

    if not receipt.is_absolute() or receipt.exists():
        raise click.UsageError("receipt must be an absolute, unused file path")
    base = _pty_daemon_base()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(path):
        with opener.open(base + path, timeout=30) as response:
            return json.load(response)

    try:
        ptys = read("/cc/sessions?include_recoverable=false")["items"]
        chat_health = read("/cc/chat/health")
        latency = read("/cc/resources/latency")
        fields = ("id", "provider", "source", "alive", "working", "hosted", "host_pid", "child_pid", "cmd")
        live = [{key: item.get(key) for key in fields} for item in ptys if item.get("alive")]
        live_chat_count = int(chat_health["session_count"])
        processes = {}
        for item in live:
            for field in ("host_pid", "child_pid"):
                pid = item.get(field)
                if pid and str(pid) not in processes:
                    process = psutil.Process(pid)
                    processes[str(pid)] = {"created_at": process.create_time(), "exe": process.exe()}
        # Native Codex processes must also survive a proxy reload.
        for process in psutil.process_iter(["name", "create_time", "exe"]):
            if str(process.info["name"] or "").lower() == "codex.exe":
                processes[str(process.pid)] = {
                    "created_at": process.info["create_time"], "exe": process.info["exe"],
                }
        report = {"sampled_at": time.time(), "sessions": live, "live_chat_count": live_chat_count,
                  "processes": processes, "latency": latency,
                  "artifacts": [{"path": str(receipt), "role": "live session/process and latency evidence"}]}
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    except Exception as exc:
        raise click.ClickException(f"diagnostics failed for {receipt}: {type(exc).__name__}: {exc}") from exc
    click.echo(json.dumps({"sessions": len(live), "live_chat_count": live_chat_count,
                          "unhosted_sessions": [item["id"] for item in live
                                                if not item["hosted"] and item["source"] == "ccdaemon.pty"],
                          "processes": len(processes), "metrics": latency.get("metrics"),
                          "artifacts": report["artifacts"]}, ensure_ascii=False, indent=2))


# ── vendored CCUI (dashboard/chatui) lifecycle ([2026-06-24] 收编上游 CCUI 当聊天后端) ──
# 上游 claudecodeui 源码收编进 src/omnicompany/dashboard/chatui, 当独立 node 进程跑
# (生产构建: 单端口同时供 SPA + API + omni_agent provider)。dashboard 人用聊天入口
# 全屏导航到它。道路: docs/plans/dashboard/[2026-06-23]聊天后端迁上游CCUI/plan.md

def _chatui_paths():
    """返回 (data_dir, chatui_dir)。复用 ccdaemon lifecycle 的 data 根。"""
    from omnicompany.dashboard.ccdaemon import lifecycle
    data = lifecycle._data_dir()
    chatui = data.parent / "src" / "omnicompany" / "dashboard" / "chatui"
    return data, chatui


def _chatui_pid_file():
    return _chatui_paths()[0] / "chatui.pid"


def _chatui_port_file():
    return _chatui_paths()[0] / "chatui.port"


def _chatui_log_file():
    return _chatui_paths()[0] / "chatui.log"


def _chatui_read_pid():
    """读 pid 文件并探活; 不存活返回 None。"""
    import os
    import sys
    f = _chatui_pid_file()
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().strip())
    except (ValueError, OSError):
        return None
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return pid
        return None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


@cmd_cc.group("chatui")
def cc_chatui() -> None:
    """收编的上游 CCUI (dashboard/chatui) 生命周期 — setup / start / stop / restart / status。"""


def _port_open(host: str, port: int) -> bool:
    """TCP 探活: 端口能连上 = 服务在听。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _default_chatui_port() -> int:
    """默认端口: 优先 OMNI_CHATUI_PORT 环境变量, 否则 7348。
    与 controlplane health 读同一来源, 消除 spawner/health 端口 split-brain。"""
    import os
    raw = (os.environ.get("OMNI_CHATUI_PORT") or "").strip()
    try:
        return int(raw) if raw else 7348
    except ValueError:
        return 7348


def _chatui_build_stale(chatui) -> bool:
    """server/ 源码(.js/.ts/.mjs)是否比 dist-server 产物新 = 改了源忘重 build。
    产物不存在返回 False(那是 build missing, 不是 stale)。仅供 status/诊断, 不进 ensure 热路径。"""
    entry = chatui / "dist-server" / "server" / "index.js"
    if not entry.exists():
        return False
    try:
        build_mtime = entry.stat().st_mtime
    except OSError:
        return False
    newest = 0.0
    try:
        for p in (chatui / "server").rglob("*"):
            if p.is_file() and p.suffix in (".js", ".ts", ".mjs"):
                m = p.stat().st_mtime
                if m > newest:
                    newest = m
    except OSError:
        return False
    return newest > build_mtime


def _chatui_kill(pid: int) -> dict:
    """终止 chatui 进程 + 清 pid 文件。stop / restart 共用。"""
    import os
    import signal as _sig
    import sys
    try:
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 0)
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            os.kill(pid, _sig.SIGTERM)
    except OSError as e:
        return {"ok": False, "reason": f"kill failed: {e}"}
    try:
        _chatui_pid_file().unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "killed_pid": pid}


def ensure_chatui_running(port: int = 0, wait_ready_s: float = 8.0) -> dict:
    """确保 chatui node 服务在跑: 已活直接返回; 没活则 spawn(DETACHED_PROCESS 无窗口)+ 轮询 ready。

    纯函数, 无 click 依赖——`omni cc chatui start` 与控制面 `POST /api/cc/chatui/ensure`
    端点共用它(唯一规范抽象, 不重复造启动逻辑)。前端 /chat-standalone 跳转前打 ensure 端点,
    chatui 没起就在这里拉起来, 避免落"连接被拒"死页。port=0 时按 OMNI_CHATUI_PORT/7348 解析。
    """
    import os
    import subprocess
    import sys
    import time
    if not port:
        port = _default_chatui_port()
    data, chatui = _chatui_paths()
    entry = chatui / "dist-server" / "server" / "index.js"
    if not entry.exists():
        # 产物不入仓(gitignore) → fresh clone / 换机后没有。指向 setup 而非裸 npm, 让报错可执行。
        return {"ok": False,
                "reason": "build missing — 跑 `omni cc chatui setup`(npm 安装 + 构建)",
                "expected": str(entry), "setup_cmd": "omni cc chatui setup"}
    url = f"http://127.0.0.1:{port}"
    pid = _chatui_read_pid()
    if pid:
        return {"ok": True, "already": True, "pid": pid, "port": port,
                "url": url, "ready": _port_open("127.0.0.1", port)}
    data.mkdir(parents=True, exist_ok=True)
    log_path = _chatui_log_file()
    env = os.environ.copy()
    env["SERVER_PORT"] = str(port)
    env["HOST"] = "127.0.0.1"  # LAN access must traverse the authenticated 12443 gateway.
    env["VITE_IS_PLATFORM"] = "true"  # 免登录(代码已硬编码, 这里冗余兜底)
    log_fd = open(log_path, "ab")
    creationflags = 0x00000008 if sys.platform == "win32" else 0  # DETACHED_PROCESS — 无窗口
    proc = subprocess.Popen(
        ["node", str(entry)], cwd=str(chatui),
        stdout=log_fd, stderr=subprocess.STDOUT, env=env, creationflags=creationflags,
    )
    _chatui_pid_file().write_text(str(proc.pid))
    _chatui_port_file().write_text(str(port))
    deadline = time.time() + wait_ready_s
    ready = False
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            ready = True
            break
        time.sleep(0.25)
    return {"ok": True, "pid": proc.pid, "port": port, "url": url,
            "log": str(log_path), "ready": ready}


@cc_chatui.command("setup")
@click.option("--clean/--no-clean", default=False, help="先删 node_modules 再装(干净安装)。")
def cc_chatui_setup(clean: bool) -> None:
    """安装依赖 + 构建 chatui(换机 / fresh clone 后首次必跑)。

    dist/dist-server 与 node_modules 都不入仓(gitignore)→ clone 后必须跑这个,
    否则 `ensure` 报 build missing、前端落"连接被拒"死页。前台跑, 输出流到当前终端(无新窗口)。
    """
    import subprocess
    import sys
    _, chatui = _chatui_paths()
    if not (chatui / "package.json").exists():
        click.echo(json.dumps({"ok": False, "reason": "chatui dir not found", "expected": str(chatui)},
                              indent=2, ensure_ascii=False))
        raise SystemExit(1)
    if clean:
        import shutil
        nm = chatui / "node_modules"
        if nm.exists():
            click.echo(f"[setup] 删 {nm} …")
            shutil.rmtree(nm, ignore_errors=True)
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    for cmd, desc in (([npm, "install"], "安装依赖"), ([npm, "run", "build"], "构建(client + server)")):
        click.echo(f"[setup] {desc}: {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=str(chatui))  # 前台, 继承终端 stdio(用户主动调的构建, 无新窗口)
        if r.returncode != 0:
            click.echo(json.dumps({"ok": False, "step": desc, "returncode": r.returncode},
                                  indent=2, ensure_ascii=False))
            raise SystemExit(r.returncode)
    entry = chatui / "dist-server" / "server" / "index.js"
    click.echo(json.dumps({"ok": True, "built": entry.exists(), "entry": str(entry),
                           "next": "omni cc chatui start"}, indent=2, ensure_ascii=False))


@cc_chatui.command("start")
@click.option("--port", type=int, default=0, help="Listen port (default: OMNI_CHATUI_PORT or 7348).")
def cc_chatui_start(port: int) -> None:
    """启动收编的 chatui node 服务(生产构建, 后台 detached)。"""
    click.echo(json.dumps(ensure_chatui_running(port=port), indent=2, ensure_ascii=False))


@cc_chatui.command("stop")
def cc_chatui_stop() -> None:
    """停止 chatui 服务。"""
    pid = _chatui_read_pid()
    if not pid:
        click.echo(json.dumps({"ok": False, "reason": "not running"}, indent=2))
        return
    click.echo(json.dumps(_chatui_kill(pid), indent=2, ensure_ascii=False))


@cc_chatui.command("restart")
@click.option("--port", type=int, default=0, help="Listen port (default: OMNI_CHATUI_PORT or 7348).")
def cc_chatui_restart(port: int) -> None:
    """重启 chatui(stop + start)。改了 controller-cli.js / 重 build 后用它让运行态对齐产物。"""
    import time
    pid = _chatui_read_pid()
    if pid:
        _chatui_kill(pid)
        time.sleep(0.5)
    click.echo(json.dumps(ensure_chatui_running(port=port), indent=2, ensure_ascii=False))


@cc_chatui.command("status")
def cc_chatui_status() -> None:
    """显示 chatui pid / port / alive / 构建状态(built / stale)。"""
    _, chatui = _chatui_paths()
    pid = _chatui_read_pid()
    pf = _chatui_port_file()
    port = pf.read_text().strip() if pf.exists() else None
    built = (chatui / "dist-server" / "server" / "index.js").exists()
    click.echo(json.dumps({"alive": bool(pid), "pid": pid, "port": port,
                           "url": f"http://localhost:{port}" if port else None,
                           "built": built,
                           "stale": _chatui_build_stale(chatui),  # 源比产物新 = 该重 build/restart
                           "node_modules": (chatui / "node_modules").exists(),
                           "log_file": str(_chatui_log_file())},
                          indent=2, ensure_ascii=False))


# ── Remote Control 常驻 (官方 claude remote-control 后台无窗口 + 自重启 supervisor) ──
# 让官方 Claude app(手机/平板)/ claude.ai/code 随时接管本机 omnicompany 会话: 纯出站 HTTPS
# 经 Anthropic 中转, 无需公网/隧道/鉴权配置(走你的 claude.ai 账号)。本机进程一断会话即停、
# 网络断超 ~10min 进程会退出, 故由 supervisor 自重启托住。实现: dashboard/ccdaemon/remote_control.py。

_REMOTE_TASK_NAME = "OmniRemoteControl"
_REMOTE_KEEPALIVE_CRON = "cc-remote-keepalive"  # 旧兜底任务名(uninstall/install 时一并清, 已被 schtasks 取代)

# start/restart 共用的会话配置选项。默认一律 None/None-flag, 不在 CLI 层写死默认值:
# 没在命令行显式给的项, 走 _remote_cfg 取"上次保存的配置"(没保存才落 remote_control.DEFAULTS),
# 这样自启/保活跑的无参 `omni cc remote start` 不会把用户设过的 --permission-mode 等冲掉。
def _remote_start_options(f):
    f = click.option("--name", default=None,
                     help="会话名(claude.ai/code 与手机 app Code 标签里按此名找; 默认 OmniCompany)。")(f)
    f = click.option("--spawn", type=click.Choice(["same-dir", "worktree", "session"]), default=None,
                     help="新会话落点: same-dir(默认)共用 omnicompany 根; worktree 每会话独立 git worktree; "
                          "session 单会话。")(f)
    f = click.option("--permission-mode", "permission_mode", default=None,
                     type=click.Choice(["acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"]),
                     help="远程会话权限模式(默认逐条询问最安全; 手机端无法现场切, 想免确认就这里设 bypassPermissions)。")(f)
    f = click.option("--debug/--no-debug", default=None,
                     help="开 claude --verbose --debug-file(排查连不上时用; 平时不开省日志)。")(f)
    return f


def _remote_cfg(ctx: click.Context, name, spawn, permission_mode, debug) -> dict:
    """合并"命令行显式给的" + "上次保存的配置": 只有 source 为 COMMANDLINE 的项才覆盖保存值。"""
    from omnicompany.dashboard.ccdaemon import remote_control as rc
    saved = rc.load_config()

    def given(p):
        src = ctx.get_parameter_source(p)
        return src is not None and src.name == "COMMANDLINE"

    return {
        "name": name if given("name") else saved["name"],
        "spawn": spawn if given("spawn") else saved["spawn"],
        "permission_mode": permission_mode if given("permission_mode") else saved["permission_mode"],
        "debug": debug if given("debug") else saved["debug"],
    }


@cmd_cc.group("remote")
def cc_remote() -> None:
    """官方 Remote Control 常驻 — start / stop / restart / status / logs / install-autostart。

    手机/平板用官方 Claude app 远程接管本机 omnicompany 会话, 纯出站 HTTPS 无需公网/隧道。
    """


@cc_remote.command("start")
@_remote_start_options
@click.pass_context
def cc_remote_start(ctx: click.Context, name, spawn, permission_mode, debug) -> None:
    """后台无窗口起常驻 Remote Control(已在跑则 no-op; 无参时复用上次保存的配置)。"""
    from omnicompany.dashboard.ccdaemon import remote_control as rc
    cfg = _remote_cfg(ctx, name, spawn, permission_mode, debug)
    click.echo(json.dumps(rc.start(**cfg), indent=2, ensure_ascii=False))


@cc_remote.command("stop")
def cc_remote_stop() -> None:
    """停止常驻 Remote Control(杀 claude 子树 + supervisor)。"""
    from omnicompany.dashboard.ccdaemon import remote_control as rc
    click.echo(json.dumps(rc.stop(), indent=2, ensure_ascii=False))


@cc_remote.command("restart")
@_remote_start_options
@click.pass_context
def cc_remote_restart(ctx: click.Context, name, spawn, permission_mode, debug) -> None:
    """重启常驻 Remote Control(stop + start; 无参时复用上次保存的配置)。"""
    from omnicompany.dashboard.ccdaemon import remote_control as rc
    cfg = _remote_cfg(ctx, name, spawn, permission_mode, debug)
    click.echo(json.dumps(rc.restart(**cfg), indent=2, ensure_ascii=False))


@cc_remote.command("status")
def cc_remote_status() -> None:
    """看 supervisor / claude 子进程是否在跑 + 会话名 + 重起次数 + 上次退出。"""
    from omnicompany.dashboard.ccdaemon import remote_control as rc
    s = rc.read_status()
    s["connect_hint"] = ("手机/平板打开 Claude app → 底部 Code 标签 → 按会话名找(在线带绿点); "
                         "或浏览器开 https://claude.ai/code")
    if s.get("orphan_child"):
        s["warn"] = "检测到孤儿 claude 子进程(supervisor 已死), 下次 start 会自动清理"
    click.echo(json.dumps(s, indent=2, ensure_ascii=False))


@cc_remote.command("logs")
@click.option("-n", "--lines", "lines", type=int, default=40, show_default=True, help="显示末尾多少行")
def cc_remote_logs(lines: int) -> None:
    """看 supervisor 事件日志末尾(启动/退出/重起)。"""
    from omnicompany.dashboard.ccdaemon import remote_control as rc
    p = rc.log_file()
    if not p.exists():
        click.echo(f"(无日志: {p})")
        return
    try:
        tail = p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError as e:
        click.echo(f"读日志失败: {e}", err=True)
        return
    click.echo("\n".join(tail))


@cc_remote.command("install-autostart")
@click.option("--interval", type=int, default=10, show_default=True, help="检查/拉起间隔(分钟)")
@click.option("--uninstall", is_flag=True, help="撤销自启计划任务")
def cc_remote_install_autostart(interval: int, uninstall: bool) -> None:
    """自启 + 保活: 一条 schtasks MINUTE 任务每 N 分钟跑 `omni cc remote start`(幂等)。

    两层保活: supervisor 自重启循环(主力, 秒级, 扛网络抖动/~10min 超时退出)+ 本计划任务
    (≤N 分钟内恢复: 重启登录后拉起、supervisor 被杀后复活)。

    用 schtasks MINUTE(免管理员)。本机 schtasks ONLOGON 触发要管理员会被拒, 故用 MINUTE 周期任务
    等效替代。omni.EXE 是 console 子系统, 交互式计划任务直跑必弹可见控制台(每 N 分钟闪一次,
    2026-07-06 用户实锤), 所以外层必须包 GUI 子系统的 wscript.exe + scripts/run_hidden.vbs
    (窗口样式 0, 子进程控制台从创建起就不可见)。
    """
    import os as _os
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    from omnicompany.cli.commands.cron import _omni_exe
    from omnicompany.packages.services._governance import scheduler

    NO_WIN = 0x08000000 if _sys.platform == "win32" else 0
    old_cron = scheduler.cron_dir() / f"{_REMOTE_KEEPALIVE_CRON}.json"

    if uninstall:
        r = subprocess.run(["schtasks", "/Delete", "/TN", _REMOTE_TASK_NAME, "/F"],
                           capture_output=True, text=True, timeout=30, creationflags=NO_WIN)
        old_cron.unlink(missing_ok=True)
        click.echo(json.dumps({"ok": True, "schtasks_delete": (r.stdout or r.stderr).strip()},
                              indent=2, ensure_ascii=False))
        return

    exe = _omni_exe()
    if not exe.exists():
        raise click.UsageError(f"找不到 omni.exe: {exe}")
    old_cron.unlink(missing_ok=True)  # 旧 @hourly cron 兜底已被本任务取代, 清掉避免双重 start

    wscript = _Path(_os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe"
    vbs = _Path(__file__).resolve().parents[4] / "scripts" / "run_hidden.vbs"
    if wscript.exists() and vbs.exists():
        tr = f'"{wscript}" "{vbs}" "{exe}" cc remote start'
    else:
        tr = f'"{exe}" cc remote start'  # 回退: 会每次闪一个 omni.EXE 控制台
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", _REMOTE_TASK_NAME, "/TR", tr,
         "/SC", "MINUTE", "/MO", str(interval), "/F"],
        capture_output=True, text=True, timeout=30, creationflags=NO_WIN,
    )
    ok = r.returncode == 0
    out = (r.stdout or r.stderr).strip()
    click.echo(json.dumps({
        "ok": ok,
        "task": _REMOTE_TASK_NAME,
        "schedule": f"每 {interval} 分钟(Interactive only, 隐藏)",
        "action": tr,
        "schtasks": out,
        "note": ("自启 + 保活计划任务已装(登录态下每 %d 分钟确保常驻; 已活则 no-op)。" % interval)
                 if ok else "schtasks 创建失败(见 schtasks 字段)。",
    }, indent=2, ensure_ascii=False))
    if not ok:
        raise SystemExit(1)


# Exact native completion wait keeps CLI replacement inside existing lifecycle facilities.
from omnicompany.cli.commands.cc_reload import cmd_reload_after_turn

cmd_cc.add_command(cmd_reload_after_turn)
