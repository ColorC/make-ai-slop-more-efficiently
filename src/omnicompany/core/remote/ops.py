# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote 操作: run/ps/read/grep/ls/spawn/job/wait/kill/nodes/stats。脚本与参数一律走文件或 here-string, 不拼转义。"
"""High-level ``omni remote`` operations. Every function returns the receipt dict that is
also written to ``<receipt_dir>/result.json``."""
from __future__ import annotations

import json
import ntpath
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .backends import MODE_TRUSTED_EXEC, TransportError, fetch_nodes, node_summary
from .profiles import NodeProfile, load_profiles
from .pstext import (INLINE_LIMIT, RemoteRefused, assert_plain_payload, command_chars, here_string, here_string_safe, ps_bool,
                     ps_quote, render)
from .session import Session, save_text
from .store import LocalBatch, read_ledger

PS_COMMAND_LIMIT = 300
JOB_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
OUTPUT_LIMIT = 192 * 1024


def _guard(profile: NodeProfile, text: str, what: str) -> None:
    if profile.forbid_encoded_payload:
        assert_plain_payload(text, node=profile.name, what=what)


def _env_lines(profile: NodeProfile, owner: str | None = None) -> str:
    env = dict(profile.env)
    if _occupancy.enabled(profile):
        # who is calling, for demogame-unity / scripts on a shared node (occupancy claims use the same id)
        env.setdefault("OMNI_OCCUPANCY_OWNER", owner or _occupancy.default_owner())
    return "\n".join(f"    $env:{key} = {ps_quote(value)}" for key, value in env.items()
                     if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))


def _read_script(script: Path | None, inline: str | None = None) -> tuple[bytes, str]:
    if inline is not None:
        return inline.encode("utf-8"), inline
    if script is None or not script.is_file():
        raise RemoteRefused("input", f"脚本文件不存在: {script}")
    data = script.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RemoteRefused("input", f"脚本必须是 UTF-8: {script}: {error}") from error
    return data, text


def short_command(command: str, *, what: str = "ps -c") -> str:
    if "\n" in command or "\r" in command:
        raise RemoteRefused("ps-length", f"{what} 只收一行命令；多行脚本写进文件用 --file x.ps1")
    if len(command) > PS_COMMAND_LIMIT:
        raise RemoteRefused("ps-length", f"{what} 命令 {len(command)} 字符，超过 {PS_COMMAND_LIMIT}；"
                            "写进文件用 `omni remote run <node> --file x.ps1 [--args a.json]`")
    if not command.strip():
        raise RemoteRefused("ps-length", f"{what} 命令是空的")
    return command + "\n"


def _read_args(args: Path | None) -> bytes:
    if args is None:
        return b"{}"
    if not args.is_file():
        raise RemoteRefused("input", f"参数文件不存在: {args}")
    data = args.read_bytes()
    try:
        json.loads(data.decode("utf-8-sig"))
    except ValueError as error:
        raise RemoteRefused("input", f"参数文件不是合法 JSON: {args}: {error}") from error
    return data.decode("utf-8-sig").encode("utf-8")


def _fetch_snippet(dir_var: str, files: list[tuple[str, str]], *, prestaged: bool) -> str:
    """PowerShell that makes the op dir hold the delivered files (in-task http pull) or checks a pre-copied dir."""
    if prestaged:
        return (f"    if (-not (Test-Path -LiteralPath {dir_var} -PathType Container)) "
                f"{{ throw \"omni-remote: prestaged dir missing: $({dir_var})\" }}")
    lines = [
        f"    if (Test-Path -LiteralPath {dir_var}) {{ throw \"omni-remote: dir already exists: $({dir_var})\" }}",
        f"    New-Item -ItemType Directory -Path {dir_var} -Force | Out-Null",
    ]
    for url, target in files:
        lines.append(f"    Invoke-WebRequest -UseBasicParsing -Uri {ps_quote(url)} -OutFile (Join-Path {dir_var} {ps_quote(target)})")
    return "\n".join(lines)


def _stage(session: Session, action: str, parent: str, stage: str, files: list[str] | None = None) -> dict[str, Any]:
    body = render("stage.ps1", {
        "ACTION": ps_quote(action), "PARENT": ps_quote(parent), "STAGE": ps_quote(stage),
        "OWNED_FILES": ", ".join(ps_quote(f) for f in files or []), "OWNED_DIRS": "",
    })
    got = session.exec(body, timeout_s=120, label=f"stage-{action}")
    if not (got["data"] or {}).get("ok"):
        raise TransportError(f"stage {action} failed: {(got['data'] or {}).get('error') or got['native'].get('stderr')}")
    return got


def _deliver(session: Session, transport: Any, batch: LocalBatch, remote_parent: str, remote_dir: str) -> None:
    """Pre-copy transports: owned parent must exist, target dir must not, then copy the batch dir in."""
    _stage(session, "prepare", remote_parent, remote_dir)
    receipt = transport.copy_dir_into(batch.root, remote_parent)
    session.calls.append({"n": len(session.calls) + 1, "label": f"{transport.name}-in", "transport": receipt})


def _native_failure(got: dict[str, Any]) -> tuple[str, int | None]:
    native = got["native"]
    status = native.get("status")
    if status == "wait-timeout":
        return "wait-timeout", None
    if status in {"dispatch-failed", "rejected", "expired", "revoked", "invalid"}:
        return "dispatch", None
    if native.get("termination") == "timeout":
        return "timeout", None
    if native.get("termination") == "output_limit":
        return "output-limit", native.get("exit_code")
    return "result-missing", native.get("exit_code")


# ── shared-node safety: static danger rules + occupancy admission ───────────────
# (imported here, not in the header block, so this section stays a self-contained addition)
from . import danger as _danger, occupancy as _occupancy  # noqa: E402


def _danger_check(profile: NodeProfile, text: str, *, op: str, what: str) -> list[dict[str, Any]]:
    """Refuse block-level hits before anything is sent; return warn-level hits for the receipt."""
    return _danger.enforce(text, op=op, node=profile.name, what=what,
                           write_roots=profile.write_roots, owned_roots=profile.owned_roots)


def _run_admission(session: Session, profile: NodeProfile, script_text: str, admission: str,
                   reason: str | None, queue_timeout_s: int) -> dict[str, Any] | None:
    """run/ps: only a script that visibly starts a Unity editor goes through admission."""
    if not _occupancy.enabled(profile):
        return None
    request = _occupancy.build_request(profile, script_text=script_text, mem_gb=None, kind=None, project=None)
    if request["kind"] != "unity":
        return None
    if admission == "skip":
        if not (reason or "").strip():
            raise RemoteRefused("admission", "--admission skip 需要 --reason 说明为什么跳过准入（写进回执与台账）")
        return {"verdict": "skipped", "reason": reason, "request": request}
    return _occupancy.admit(profile, session.backend, request, queue_timeout_s=queue_timeout_s)


def _kill_check(session: Session, profile: NodeProfile, script_text: str, admission: str, reason: str | None,
                owner: str | None = None) -> dict[str, Any] | None:
    """A script that runs ``demogame-unity ... kill`` may not stop an editor another owner registered."""
    if not _occupancy.enabled(profile):
        return None
    kills = _occupancy.detect_unity_kill(script_text)
    if not kills:
        return None
    if admission == "skip" and (reason or "").strip():
        return {"verdict": "skipped", "reason": reason, "kills": kills}
    data = _occupancy.remote_call(profile, session.backend, "snapshot", timeout_s=120)
    if not data.get("ok"):
        return {"verdict": "error", "kills": kills, "warnings": [f"占用表采集失败，没能核对所有者：{data.get('error')}"],
                "receipt": data.get("receipt")}
    checked = _occupancy.kill_guard(data.get("snapshot") or {}, kills, owner or _occupancy.default_owner())
    checked["receipt"] = data.get("receipt")
    return checked


def _claim_started_unity(session: Session, profile: NodeProfile, admitted: dict[str, Any] | None) -> dict[str, Any] | None:
    """After a run that started a Unity editor, register it for the caller (estimate only; never consent to stop)."""
    request = (admitted or {}).get("request") or {}
    if (admitted or {}).get("verdict") not in {"admit", "skipped"} or request.get("kind") != "unity" or not request.get("project"):
        return None
    detected = request.get("detected") or {}
    if detected.get("batchmode") and "-quit" in str(detected.get("line") or ""):
        return None
    owner = _occupancy.default_owner()
    try:
        params = _occupancy.make_claim_params(
            claim_id=_occupancy.unity_claim_id(owner, request["project"]), owner=owner, kind="unity",
            project=request["project"], estimate_gb=request.get("mem_gb") or None, on_breach="alert",
            purpose=f"omni remote {session.op} {session.label}"[:160])
        got = _occupancy.remote_call(profile, session.backend, "claim", params)
    except (RemoteRefused, TransportError) as error:
        return {"ok": False, "error": str(error)}
    return {"ok": bool(got.get("ok")), "error": got.get("error"), "claim_id": params["claim_id"], "path": got.get("path")}


# ── run / ps ───────────────────────────────────────────────────────────────────

def run(profile: NodeProfile, backend: Any, script: Path | None, *, args: Path | None = None, cwd: str | None = None,
        timeout_s: int = 600, delivery: str = "auto", op: str = "run", label: str | None = None,
        inline: str | None = None, admission: str = "auto", admission_reason: str | None = None,
        queue_timeout_s: int = 0) -> dict[str, Any]:
    script_bytes, script_text = _read_script(script, inline)
    args_bytes = _read_args(args)
    _guard(profile, script_text, "脚本")
    _guard(profile, args_bytes.decode("utf-8"), "参数文件")
    danger_warnings = _danger_check(profile, script_text, op=op, what="脚本")
    session = Session(profile, backend, op, label=label or (script.name if script else op))
    own = op == "occupancy"  # the occupancy library itself is never re-checked (it is what the checks call)
    kill_checked = None if own else _kill_check(session, profile, script_text, admission, admission_reason)
    if kill_checked is not None and kill_checked.get("verdict") == "refuse":
        return _occupancy.refusal(session, kill_checked, extra={"danger_warnings": danger_warnings, "kill_guard": kill_checked})
    admitted = None if own else _run_admission(session, profile, script_text, admission, admission_reason, queue_timeout_s)
    if admitted is not None and admitted.get("verdict") not in {"admit", "skipped"}:
        return _occupancy.refusal(session, admitted, extra={"danger_warnings": danger_warnings})
    op_dir = session.r(ntpath.join(profile.runs_root, session.op_id))
    cwd_remote = session.r(cwd or profile.default_cwd or "")
    base_tokens = {
        "OP_ID": ps_quote(session.op_id), "NODE": ps_quote(profile.name), "OP_DIR": ps_quote(op_dir),
        "MODE": ps_quote("run"), "CWD": ps_quote(cwd_remote), "ENV": _env_lines(profile),
        "EMIT_OUTPUT": "$true", "OUTPUT_LIMIT": str(OUTPUT_LIMIT),
    }
    inline_ok = here_string_safe(script_text) and here_string_safe(args_bytes.decode("utf-8"))
    body = None
    if delivery in {"auto", "inline"} and inline_ok:
        body = render("runner.ps1", {**base_tokens, "DELIVERY": ps_quote("inline"), "FETCH": "",
                                     "SCRIPT_TEXT": here_string(script_text), "ARGS_TEXT": here_string(args_bytes.decode("utf-8")),
                                     "SCRIPT_BYTES": "0", "ARGS_BYTES": "0"})
        if delivery == "auto" and command_chars(session.prelude() + body) > INLINE_LIMIT:
            body = None
    if delivery == "inline" and body is None:
        raise RemoteRefused("delivery", "脚本含以 '@ 开头的行, 不能内联下发; 去掉 --delivery inline 让它走文件通道")
    used = "inline" if body is not None else "file"
    transport_name = None
    batch = None
    try:
        if body is not None:
            got = session.exec(body, timeout_s=timeout_s, label="run")
        else:
            batch = LocalBatch(session.op_id, "run script delivery")
            batch.path("source.ps1").write_bytes(script_bytes)
            batch.path("args.json").write_bytes(args_bytes)
            file_tokens = {**base_tokens, "DELIVERY": ps_quote("file"), "SCRIPT_TEXT": "$null", "ARGS_TEXT": "$null",
                           "SCRIPT_BYTES": str(len(script_bytes)), "ARGS_BYTES": str(len(args_bytes))}
            with backend.transport() as transport:
                transport_name = transport.name
                if transport.kind == "in-task":
                    urls = [(transport.offer(batch.root / "source.ps1"), "source.ps1"), (transport.offer(batch.root / "args.json"), "args.json")]
                    body = render("runner.ps1", {**file_tokens, "FETCH": _fetch_snippet("$__dir", urls, prestaged=False)})
                    got = session.exec(body, timeout_s=timeout_s, label="run")
                else:
                    _deliver(session, transport, batch, session.r(profile.runs_root), op_dir)
                    body = render("runner.ps1", {**file_tokens, "FETCH": _fetch_snippet("$__dir", [], prestaged=True)})
                    got = session.exec(body, timeout_s=timeout_s, label="run")
    except (TransportError, OSError, ValueError) as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "delivery": used, "transport": transport_name,
                               "first_error": {"phase": "transport", "message": str(error)},
                               "local_cleanup": batch.cleanup() if batch else None})
    remote = got["data"] if isinstance(got["data"], dict) and got["data"].get("schema") == "omni.remote.result/1" else None
    output_path = save_text(session, "output.txt", got["output"] if remote else (got["native"].get("stdout") or ""))
    stderr_path = save_text(session, "stderr.txt", got["native"].get("stderr") or "")
    if remote:
        exit_code = remote.get("exit_code")
        phase = remote.get("phase")
        first_error = remote.get("first_error")
    else:
        phase, exit_code = _native_failure(got)
        first_error = {"phase": phase, "message": (got["native"].get("stderr") or "").strip()[-2000:] or f"native status={got['native'].get('status')} termination={got['native'].get('termination')}"}
    remote_paths = (remote or {}).get("paths") or {}
    result = {
        "ok": exit_code == 0 and phase == "completed",
        "exit_code": exit_code,
        "phase": phase,
        "exit_source": (remote or {}).get("exit_source"),
        "first_error": first_error,
        "native_exit_code": got["native"].get("exit_code"),
        "exit_code_consistent": (remote is None) or got["native"].get("exit_code") == exit_code,
        "delivery": used,
        "transport": transport_name,
        "remote": remote,
        "direct_console": got["direct"][-4000:] if got["direct"] else "",
        "local_cleanup": batch.cleanup() if batch else None,
        "paths": {
            "output": output_path,
            "stderr": stderr_path,
            "remote_op_dir": remote_paths.get("op_dir") or op_dir,
            "remote_log": remote_paths.get("log"),
            "remote_result": remote_paths.get("result"),
            "remote_script": remote_paths.get("script"),
        },
    }
    if danger_warnings:
        result["danger_warnings"] = danger_warnings
    if admitted is not None:
        result["admission"] = admitted
    if kill_checked is not None:
        result["kill_guard"] = kill_checked
    if result["ok"]:
        claimed = _claim_started_unity(session, profile, admitted)
        if claimed is not None:
            result["claim"] = claimed
            result["paths"]["remote_claim"] = claimed.get("path")
    return session.finish(result, ledger_extra={"delivery": used})


def ps(profile: NodeProfile, backend: Any, command: str, *, cwd: str | None = None, timeout_s: int = 120) -> dict[str, Any]:
    text = short_command(command)
    return run(profile, backend, None, inline=text, cwd=cwd, timeout_s=timeout_s, op="ps", label="ps: " + command[:60])


# ── read / grep / ls (read-only) ─────────────────────────────────────────────

def readonly_channel(profile: NodeProfile, op: str) -> tuple[str, str]:
    if profile.readonly_mode and op in profile.l1_ops:
        return profile.readonly_mode, f"L1 适配器支持 {op}"
    if profile.readonly_mode:
        return MODE_TRUSTED_EXEC, (f"节点有 {profile.readonly_mode}，但它的适配器做不了 {op}"
                                   "（画像 l1_ops 未列出）；改走 L2 只读脚本，脚本不写任何文件")
    return MODE_TRUSTED_EXEC, "节点没有 L1 只读档；走 L2 只读脚本，脚本不写任何文件"


def _readonly(profile: NodeProfile, backend: Any, op: str, template: str, tokens: dict[str, str], *, timeout_s: int,
              label: str) -> dict[str, Any]:
    session = Session(profile, backend, op, label=label)
    mode, reason = readonly_channel(profile, op)
    if mode != MODE_TRUSTED_EXEC:  # pragma: no cover - no L1 content adapter exists yet
        raise RemoteRefused("channel", f"{op} 的 L1 通道尚未实现")
    try:
        got = session.exec(render(template, tokens), timeout_s=timeout_s, label=op, mode=mode)
    except TransportError as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "first_error": {"message": str(error)}})
    data = got["data"]
    if not isinstance(data, dict):
        phase, code = _native_failure(got)
        return session.finish({"ok": False, "phase": phase, "exit_code": code, "channel": mode, "channel_reason": reason,
                               "first_error": {"phase": phase, "message": (got["native"].get("stderr") or "").strip()[-2000:]}})
    return session.finish({"ok": bool(data.get("ok")), "phase": "completed" if data.get("ok") else "failed",
                           "exit_code": 0 if data.get("ok") else 2, "channel": mode, "channel_reason": reason,
                           "first_error": {"message": data.get("error")} if data.get("error") else None, "data": data})


def read(profile: NodeProfile, backend: Any, path: str, *, offset: int = 1, limit: int = 200, tail: int = 0,
         timeout_s: int = 120) -> dict[str, Any]:
    offset = max(1, int(offset))
    limit = max(1, min(int(limit), 2000))
    tail = max(0, min(int(tail), 2000))
    tokens = {"PATH": ps_quote(backend.map_path(path)), "OFFSET": str(offset), "LIMIT": str(limit), "TAIL": str(tail),
              "MAX_LINE": "2000", "MAX_CHARS": str(200_000)}
    return _readonly(profile, backend, "read", "read.ps1", tokens, timeout_s=timeout_s, label=f"read {path}")


def grep(profile: NodeProfile, backend: Any, pattern: str, path: str, *, glob: str | None = None, ignore_case: bool = False,
         fixed: bool = False, offset: int = 0, max_results: int = 200, engine: str = "auto", timeout_s: int = 300) -> dict[str, Any]:
    if engine not in {"auto", "rg", "select-string"}:
        raise RemoteRefused("input", f"--engine 只能是 auto / rg / select-string: {engine}")
    tokens = {"PATTERN": ps_quote(pattern), "PATH": ps_quote(backend.map_path(path)), "GLOB": ps_quote(glob or ""),
              "IGNORE_CASE": ps_bool(ignore_case), "FIXED": ps_bool(fixed), "OFFSET": str(max(0, int(offset))),
              "MAX": str(max(1, min(int(max_results), 2000))), "MAX_FILES": "50000", "MAX_LINE": "2000",
              "ENGINE": ps_quote(engine)}
    return _readonly(profile, backend, "grep", "grep.ps1", tokens, timeout_s=timeout_s, label=f"grep {pattern} {path}")


def ls(profile: NodeProfile, backend: Any, path: str, *, depth: int = 1, limit: int = 500, glob: str | None = None,
       timeout_s: int = 120) -> dict[str, Any]:
    tokens = {"PATH": ps_quote(backend.map_path(path)), "DEPTH": str(max(1, min(int(depth), 8))),
              "LIMIT": str(max(1, min(int(limit), 5000))), "GLOB": ps_quote(glob or "")}
    return _readonly(profile, backend, "ls", "ls.ps1", tokens, timeout_s=timeout_s, label=f"ls {path}")


# ── background jobs ───────────────────────────────────────────────────────────

def _check_job_name(name: str) -> None:
    if not JOB_NAME.match(name):
        raise RemoteRefused("input", f"作业名只能用字母、数字、- 和 _（1–64 位）: {name}")


def spawn(profile: NodeProfile, backend: Any, name: str, script: Path | None, *, args: Path | None = None, cwd: str | None = None,
          delivery: str = "auto", inline: str | None = None, window: str = "normal",
          occupancy_opts: dict[str, Any] | None = None) -> dict[str, Any]:
    _check_job_name(name)
    if window not in SHOW_WINDOW:
        raise RemoteRefused("input", f"--window 只能是 {'/'.join(SHOW_WINDOW)}: {window}")
    script_bytes, script_text = _read_script(script, inline)
    args_bytes = _read_args(args)
    _guard(profile, script_text, "作业脚本")
    _guard(profile, args_bytes.decode("utf-8"), "参数文件")
    danger_warnings = _danger_check(profile, script_text, op="spawn", what="作业脚本")
    opts = dict(occupancy_opts or {})
    if opts.get("admission") == "skip" and not (opts.get("reason") or "").strip():
        raise RemoteRefused("admission", "--admission skip 需要 --reason 说明为什么跳过准入（写进回执与台账）")
    session = Session(profile, backend, "spawn", label=f"spawn {name}")
    kill_checked = _kill_check(session, profile, script_text, str(opts.get("admission") or "auto"), opts.get("reason"),
                               owner=opts.get("owner"))
    if kill_checked is not None and kill_checked.get("verdict") == "refuse":
        return _occupancy.refusal(session, kill_checked, extra={"danger_warnings": danger_warnings, "kill_guard": kill_checked})
    request = admitted = None
    if _occupancy.enabled(profile):
        request = _occupancy.build_request(profile, script_text=script_text, mem_gb=opts.get("mem_gb"),
                                           kind=opts.get("kind"), project=opts.get("project"))
        if opts.get("admission") == "skip":
            admitted = {"verdict": "skipped", "reason": opts.get("reason"), "request": request}
        else:
            admitted = _occupancy.admit(profile, session.backend, request, queue_timeout_s=int(opts.get("queue_timeout_s") or 0))
            if admitted.get("verdict") == "error" and request.get("kind") != "unity" and not request.get("mem_gb"):
                # a light job must not be blocked because the table could not be read; Unity / declared memory still is
                admitted["verdict"] = "admit-unchecked"
            if admitted.get("verdict") not in {"admit", "admit-unchecked"}:
                return _occupancy.refusal(session, admitted, extra={"danger_warnings": danger_warnings})
    jobs_root = session.r(profile.jobs_root)
    job_parent = ntpath.join(jobs_root, name)
    job_dir = ntpath.join(job_parent, session.op_id)
    cwd_remote = session.r(cwd or profile.default_cwd or "")
    runner_text = session.prelude() + "\n" + render("runner.ps1", {
        "OP_ID": ps_quote(session.op_id), "NODE": ps_quote(profile.name), "OP_DIR": ps_quote(job_dir), "MODE": ps_quote("job"),
        "CWD": ps_quote(cwd_remote), "ENV": _env_lines(profile, opts.get("owner")), "EMIT_OUTPUT": "$false", "OUTPUT_LIMIT": str(OUTPUT_LIMIT),
        "DELIVERY": ps_quote("file"), "SCRIPT_TEXT": "$null", "ARGS_TEXT": "$null",
        "SCRIPT_BYTES": str(len(script_bytes)), "ARGS_BYTES": str(len(args_bytes)),
        "FETCH": _fetch_snippet("$__dir", [], prestaged=True),
    })
    session.guard(runner_text, "作业 runner")
    runner_bytes = runner_text.encode("utf-8")
    shell = profile.shell_path  # never mapped: the fake backend runs the real local powershell.exe too
    base = {"JOB_NAME": ps_quote(name), "JOBS_ROOT": ps_quote(jobs_root), "JOB_DIR": ps_quote(job_dir), "SHELL": ps_quote(shell),
            "CWD": ps_quote(cwd_remote), "OP_ID": ps_quote(session.op_id), "NODE": ps_quote(profile.name),
            "SHOW_WINDOW": str(SHOW_WINDOW[window])}
    body = None
    if delivery in {"auto", "inline"} and here_string_safe(script_text) and here_string_safe(args_bytes.decode("utf-8")):
        body = render("spawn.ps1", {**base, "DELIVERY": ps_quote("inline"), "FETCH": "",
                                    "SCRIPT_TEXT": here_string(script_text), "ARGS_TEXT": here_string(args_bytes.decode("utf-8")),
                                    "RUNNER_TEXT": here_string(runner_text), "SCRIPT_BYTES": "0", "ARGS_BYTES": "0", "RUNNER_BYTES": "0"})
        if delivery == "auto" and command_chars(session.prelude() + body) > INLINE_LIMIT:
            body = None
    used = "inline" if body is not None else "file"
    batch = None
    transport_name = None
    try:
        if body is not None:
            got = session.exec(body, timeout_s=120, label="spawn")
        else:
            batch = LocalBatch(session.op_id, "spawn delivery")
            batch.path("source.ps1").write_bytes(script_bytes)
            batch.path("args.json").write_bytes(args_bytes)
            batch.path("runner.ps1").write_bytes(b"\xef\xbb\xbf" + runner_bytes)
            file_tokens = {**base, "DELIVERY": ps_quote("file"), "SCRIPT_TEXT": "$null", "ARGS_TEXT": "$null", "RUNNER_TEXT": "$null",
                           "SCRIPT_BYTES": str(len(script_bytes)), "ARGS_BYTES": str(len(args_bytes)),
                           "RUNNER_BYTES": str(3 + len(runner_bytes))}  # UTF-8 BOM + text, as written
            with backend.transport() as transport:
                transport_name = transport.name
                if transport.kind == "in-task":
                    urls = [(transport.offer(batch.root / n), n) for n in ("source.ps1", "args.json", "runner.ps1")]
                    body = render("spawn.ps1", {**file_tokens, "FETCH": _fetch_snippet("$dir", urls, prestaged=False)})
                else:
                    _deliver(session, transport, batch, job_parent, job_dir)
                    body = render("spawn.ps1", {**file_tokens, "FETCH": _fetch_snippet("$dir", [], prestaged=True)})
                got = session.exec(body, timeout_s=120, label="spawn")
    except (TransportError, OSError, ValueError) as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "first_error": {"message": str(error)},
                               "local_cleanup": batch.cleanup() if batch else None})
    data = got["data"] if isinstance(got["data"], dict) else None
    ok = bool(data and data.get("ok"))
    paths = (data or {}).get("paths") or {}
    claimed = None
    if ok and request is not None and opts.get("claim", True):
        claimed = _spawn_claim(session, profile, name, data, request, opts)
    return session.finish({
        "ok": ok, "phase": "started" if ok else "failed", "exit_code": 0 if ok else 1, "delivery": used, "transport": transport_name,
        "first_error": None if ok else {"message": (data or {}).get("error") or got["native"].get("stderr")},
        "data": data, "local_cleanup": batch.cleanup() if batch else None, "window": window,
        "danger_warnings": danger_warnings, "admission": admitted, "claim": claimed, "kill_guard": kill_checked,
        "paths": {"remote_job_dir": paths.get("dir") or job_dir, "remote_registry": paths.get("registry"),
                  "remote_log": paths.get("log"), "remote_console": paths.get("console"), "remote_result": paths.get("result"),
                  "remote_claim": (claimed or {}).get("path")},
    }, ledger_extra={"job": name, "admission": (admitted or {}).get("verdict")})


SHOW_WINDOW = {"normal": -1, "hidden": 0, "minimized": 7}  # Win32_ProcessStartup.ShowWindow; 7 = SW_SHOWMINNOACTIVE


def _spawn_claim(session: Session, profile: NodeProfile, name: str, data: dict[str, Any], request: dict[str, Any],
                 opts: dict[str, Any]) -> dict[str, Any]:
    """Register the started job in the machine occupancy table (owner, pid + creation time, budget)."""
    explicit = bool(request.get("explicit_mem"))
    on_breach = opts.get("on_breach")
    if on_breach is None:
        on_breach = "stop" if (explicit or opts.get("deadline")) else None
    kind = request.get("kind") if request.get("kind") in _occupancy.KINDS else "job"
    try:
        params = _occupancy.make_claim_params(
            claim_id=f"job-{name}-{session.op_id}", owner=opts.get("owner") or _occupancy.default_owner(), kind=kind,
            pid=data.get("pid"), created_utc_ticks=data.get("created_utc_ticks"), project=request.get("project"),
            mem_gb=request.get("mem_gb") if explicit else None,
            estimate_gb=None if explicit else (request.get("mem_gb") or None),
            deadline=opts.get("deadline"), on_breach=on_breach, purpose=opts.get("purpose") or f"omni remote spawn {name}",
            job=name)
        got = _occupancy.remote_call(profile, session.backend, "claim", params)
    except (RemoteRefused, TransportError) as error:
        return {"ok": False, "error": str(error)}
    return {"ok": bool(got.get("ok")), "error": got.get("error"), "claim": got.get("claim"), "path": got.get("path")}


def _job_state(session: Session, profile: NodeProfile, name: str, tail: int) -> dict[str, Any]:
    body = render("job.ps1", {"JOB_NAME": ps_quote(name), "JOBS_ROOT": ps_quote(session.r(profile.jobs_root)), "TAIL": str(int(tail))})
    return session.exec(body, timeout_s=120, label="job")


def _job_receipt(got: dict[str, Any]) -> dict[str, Any]:
    data = got["data"] if isinstance(got["data"], dict) else None
    if data is None:
        phase, _code = _native_failure(got)
        return {"ok": False, "phase": phase, "exit_code": None, "first_error": {"message": got["native"].get("stderr")}}
    result = data.get("result") or {}
    paths = data.get("paths") or {}
    state = data.get("state")
    return {
        "ok": bool(data.get("ok")) and state != "unknown",
        "phase": state,
        "state": state,
        "exit_code": result.get("exit_code") if state in {"exited", "killed"} else None,
        "first_error": result.get("first_error") or ({"message": data.get("error")} if data.get("error") else None),
        "data": data,
        "paths": {"remote_job_dir": paths.get("dir"), "remote_log": paths.get("log"), "remote_console": paths.get("console"),
                  "remote_result": paths.get("result"), "remote_registry": data.get("registry")},
    }


def job(profile: NodeProfile, backend: Any, name: str, *, tail: int = 30) -> dict[str, Any]:
    _check_job_name(name)
    session = Session(profile, backend, "job", label=f"job {name}")
    try:
        got = _job_state(session, profile, name, tail)
    except TransportError as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "first_error": {"message": str(error)}})
    return session.finish(_job_receipt(got), ledger_extra={"job": name})


def wait(profile: NodeProfile, backend: Any, name: str, *, timeout_s: int = 3600, every_s: int = 15, tail: int = 40,
         sleep: Any = time.sleep) -> dict[str, Any]:
    """Deterministic local loop (no model in the loop): poll until the job leaves ``running`` or the cap is hit."""
    _check_job_name(name)
    timeout_s = max(1, min(int(timeout_s), 24 * 3600))
    every_s = max(2, min(int(every_s), 600))
    session = Session(profile, backend, "wait", label=f"wait {name}")
    started = time.monotonic()
    polls = 0
    state = None
    try:
        while True:
            polls += 1
            got = _job_state(session, profile, name, 0)
            state = (got["data"] or {}).get("state") if isinstance(got["data"], dict) else None
            if state != "running":
                break
            if time.monotonic() - started >= timeout_s:
                break
            sleep(min(every_s, max(1, timeout_s - (time.monotonic() - started))))
        final = _job_receipt(_job_state(session, profile, name, tail))
    except TransportError as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "first_error": {"message": str(error)},
                               "polls": polls})
    timed_out = final.get("state") == "running"
    final.update({"polls": polls, "waited_s": round(time.monotonic() - started, 1), "wait_timeout_s": timeout_s,
                  "timed_out": timed_out})
    if timed_out:
        final.update({"ok": False, "phase": "wait-timeout"})
    else:
        final["ok"] = final.get("state") == "exited" and final.get("exit_code") == 0
    return session.finish(final, ledger_extra={"job": name, "polls": polls})


def kill(profile: NodeProfile, backend: Any, name: str) -> dict[str, Any]:
    _check_job_name(name)
    session = Session(profile, backend, "kill", label=f"kill {name}")
    body = render("kill.ps1", {"JOB_NAME": ps_quote(name), "JOBS_ROOT": ps_quote(session.r(profile.jobs_root))})
    try:
        got = session.exec(body, timeout_s=120, label="kill")
    except TransportError as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "first_error": {"message": str(error)}})
    data = got["data"] if isinstance(got["data"], dict) else None
    ok = bool(data and data.get("ok"))
    return session.finish({"ok": ok, "phase": (data or {}).get("state") or "failed", "exit_code": 0 if ok else 3,
                           "first_error": None if ok else {"message": (data or {}).get("error") or got["native"].get("stderr")},
                           "data": data}, ledger_extra={"job": name})


# ── nodes / stats (local) ─────────────────────────────────────────────────────

def nodes(backend_name: str | None = None, *, occupancy: bool = True) -> dict[str, Any]:
    """Profiles + control-plane status; for online nodes with an occupancy table also its summary (read-only)."""
    result = _nodes_status(backend_name)
    if occupancy:
        from .backends import make_backend

        for row in result["nodes"]:
            profile = load_profiles()[row["profile"]["name"]]
            if not _occupancy.enabled(profile) or not row["status"].get("online"):
                continue
            try:
                got = _occupancy.summary(profile, make_backend(profile, backend_name), limit=20, timeout_s=60)
                row["occupancy"] = {"ok": got.get("ok"), "error": (got.get("first_error") or {}).get("message"),
                                    "result": (got.get("paths") or {}).get("result"), **(got.get("data") or {})}
            except (RemoteRefused, TransportError, OSError) as exc:
                row["occupancy"] = {"ok": False, "error": str(exc)}
    return result


def _nodes_status(backend_name: str | None = None) -> dict[str, Any]:
    profiles = load_profiles()
    live: dict[str, dict[str, Any]] = {}
    error = None
    if (backend_name or "desktop-node") != "fake":
        try:
            live = {str(n.get("node_id")): n for n in fetch_nodes()}
        except TransportError as exc:
            error = str(exc)
    rows = []
    for profile in profiles.values():
        status = {"paired": True, "online": True, "fake": True} if backend_name == "fake" else node_summary(live.get(profile.node_id))
        rows.append({"profile": profile.as_dict(), "status": status})
    return {"schema": "omni.remote.nodes/1", "ok": error is None, "nodes": rows, "control_plane_error": error}


def _parse_when(text: str | None, default: datetime) -> datetime:
    if not text:
        return default
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = map(int, text.split(":"))
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return datetime.fromisoformat(text)


def stats(*, since: str | None = None, until: str | None = None, node: str | None = None, by: str = "op") -> dict[str, Any]:
    start = _parse_when(since, datetime.min)
    end = _parse_when(until, datetime.max)
    groups: dict[str, dict[str, Any]] = {}
    total = 0
    for row in read_ledger():
        try:
            at = datetime.fromisoformat(str(row.get("at")))
        except ValueError:
            continue
        if not (start <= at <= end) or (node and row.get("node") != node):
            continue
        key = str(row.get("op")) if by == "op" else f"{row.get('node')} {row.get('op')}"
        group = groups.setdefault(key, {"key": key, "count": 0, "failed": 0, "refused": 0, "wall_s": 0.0, "chars": 0})
        group["count"] += 1
        total += 1
        if row.get("phase") == "refused":
            group["refused"] += 1
        elif not row.get("ok"):
            group["failed"] += 1
        group["wall_s"] += float(row.get("wall_s") or 0)
        group["chars"] += int(row.get("chars") or 0)
    rows = sorted(groups.values(), key=lambda g: (-g["wall_s"], -g["count"]))
    for group in rows:
        group["wall_s"] = round(group["wall_s"], 1)
        group["avg_s"] = round(group["wall_s"] / group["count"], 1) if group["count"] else 0
    return {"schema": "omni.remote.stats/1", "ok": True, "total": total, "groups": rows}
