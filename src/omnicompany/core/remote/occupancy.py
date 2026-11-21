# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-24T02:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote 共用机占用表/准入/看门狗：远端 occupancy.json 是真源，claims 登记所有者与预算，alerts.jsonl 告警，只按 pid+创建时间收同意了 stop 的登记。"
"""Machine occupancy for shared ``omni remote`` nodes (GAMEBOX first).

Remote layout under ``<staging_root>\\occupancy``::

    occupancy.json     latest table (the file is the source of truth; refreshed by every snapshot and the watchdog)
    claims\\<id>.json   one registration: owner, pid + creation time, project, budget {mem_gb, deadline, on_breach}, purpose
    alerts.jsonl       breaches and machine pressure (de-duplicated), plus every stop the watchdog made
    policy.json        thresholds actually in force (written from ``[nodes.<name>.occupancy]``)

Nothing here stops a process unless its own claim says ``on_breach = "stop"``; then only by pid + creation time.
"""
from __future__ import annotations

import fnmatch
import getpass
import json
import ntpath
import os
import re
import socket
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from .pstext import RemoteRefused, here_string, ps_quote, render

WATCHDOG_JOB = "occupancy-watchdog"
CLAIM_ID = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
KINDS = ("unity", "job", "server", "recording", "experiment", "watchdog", "other")

DEFAULT_POLICY: dict[str, Any] = {
    # physical memory left free after the new start (same 8 GB line as gamewright record_health memory_pressure)
    "reserve_gb": 8.0,
    # a start that declares no memory only needs the machine not to be critical
    "light_reserve_gb": 2.0,
    # commit headroom (commit limit - committed) left after the start; 10-23 night ran the commit limit out at 172 GB
    "commit_reserve_gb": 16.0,
    "light_commit_reserve_gb": 4.0,
    # sum of registered budgets may not exceed physical total minus this
    "pool_reserve_gb": 8.0,
    # other processes at or above this private size are listed as desktop / unregistered occupants
    "other_min_gb": 2.0,
    # expected steady private memory of a Unity editor when the caller gives no --mem-gb
    "unity_default_gb": 12.0,
    "project_estimates": [],
    "project_hints": [],
    "alert_repeat_min": 30,
    "watchdog_interval_s": 60,
    "watchdog_hours": 24,
    "queue_every_s": 30,
}

_UNITY_START = (
    re.compile(r"""(?i)\bUnity(?:\.exe)?['"]?[^\r\n]*?\s-projectPath\s+(?:"([^"]+)"|'([^']+)'|([^\s'"`|;)]+))"""),
    re.compile(r"""(?i)\b(?:demogame-unity|unity_cli\.cli)\b[^\r\n]*?\blaunch\s+(?:"([^"]+)"|'([^']+)'|([^\s'"`|;)-][^\s'"`|;)]*))"""),
)


# ── policy / identity ─────────────────────────────────────────────────────────

def enabled(profile: Any) -> bool:
    occ = getattr(profile, "occupancy", None) or {}
    return bool(occ) and bool(occ.get("enabled", True))


def policy(profile: Any) -> dict[str, Any]:
    merged = dict(DEFAULT_POLICY)
    for key, value in (getattr(profile, "occupancy", None) or {}).items():
        if key != "enabled":
            merged[key] = value
    return merged


def default_owner() -> str:
    explicit = os.environ.get("OMNI_OCCUPANCY_OWNER", "").strip()
    if explicit:
        return explicit
    pty = os.environ.get("OMNI_CC_PTY_ID", "").strip()
    if pty:
        return f"pty:{pty}"
    for key, prefix in (("CLAUDE_CODE_SESSION_ID", "claude"), ("CODEX_SESSION_ID", "codex")):
        value = os.environ.get(key, "").strip()
        if value:
            return f"{prefix}:{value}"
    return f"user:{getpass.getuser()}@{socket.gethostname()}"


def parse_deadline(text: str | None, *, now: datetime | None = None) -> str | None:
    """'90m' / '2h' / '1d' / 'HH:MM' (next occurrence) / ISO -> ISO with local offset."""
    if not text:
        return None
    now = now or datetime.now().astimezone()
    raw = text.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([mhd])", raw, re.I)
    if m:
        amount = float(m.group(1))
        unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2).lower()]
        return (now + timedelta(**{unit: amount})).isoformat(timespec="seconds")
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hour, minute = map(int, raw.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        return at.isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise RemoteRefused("input", f"--deadline 看不懂: {text}（用 90m / 2h / 1d / HH:MM / ISO 时间）") from error
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


# ── Unity start detection + estimates ─────────────────────────────────────────

def detect_unity_start(text: str) -> dict[str, Any] | None:
    """A static guess whether a dispatched script starts a Unity editor (literal project path when visible)."""
    from .danger import strip_comments

    for _number, line in strip_comments(text):
        if not line.strip():
            continue
        for pattern in _UNITY_START:
            m = pattern.search(line)
            if m:
                project = next((g for g in m.groups() if g), None)
                if project and (project.startswith("$") or project.startswith("(")):
                    project = None
                return {"kind": "unity", "project": ntpath.normpath(project.replace("/", "\\")) if project else None,
                        "batchmode": bool(re.search(r"(?i)(^|\s)-batchmode\b", line)), "line": line.strip()[:200]}
    return None


_UNITY_KILL = re.compile(r"""(?i)\b(?:demogame-unity|unity_cli\.cli)\b[^\r\n]*?\bkill\b""")
_KILL_PROJECT = re.compile(r"""(?i)(?:^|\s)(?:-P|--project)\s+(?:"([^"]+)"|'([^']+)'|([^\s'"`|;)]+))""")


def detect_unity_kill(text: str) -> list[dict[str, Any]]:
    """``demogame-unity [-P X] kill [--project X]`` lines in a dispatched script (literal project when visible)."""
    from .danger import strip_comments

    found = []
    for number, line in strip_comments(text):
        if not _UNITY_KILL.search(line):
            continue
        m = _KILL_PROJECT.search(line)
        project = next((g for g in m.groups() if g), None) if m else None
        if project and (project.startswith("$") or project.startswith("(")):
            project = None
        found.append({"line": number, "text": line.strip()[:200],
                      "project": ntpath.normpath(project.replace("/", "\\")) if project else None})
    return found


def kill_guard(snap: dict[str, Any], kills: list[dict[str, Any]], caller: str) -> dict[str, Any]:
    """Refuse killing an editor another owner registered; warn when the editor has no registration."""
    entries = list(snap.get("entries") or [])
    refused, warnings = [], []
    for kill in kills:
        if not kill.get("project"):
            warnings.append(f"第 {kill['line']} 行 demogame-unity kill 看不出工程路径，无法核对所有者：{kill['text']}")
            continue
        for entry in entries:
            if not _same_project(kill["project"], entry):
                continue
            if entry.get("owner_source") == "claim" and entry.get("owner") != caller:
                refused.append(f"第 {kill['line']} 行要结束的 {kill['project']} 是 {describe_entry(entry)}")
            elif entry.get("owner_source") != "claim":
                warnings.append(f"第 {kill['line']} 行要结束的 {kill['project']} 没有登记所有者（{owner_label(entry)}）；"
                                "确认是自己开的再结束，以后用 omni remote occupancy <节点> claim 登记")
    message = None
    if refused:
        message = ("拒绝结束别人登记的 Unity 编辑器：" + "；".join(refused)
                   + "。什么都没下发。资源不够就等它结束或找所有者（omni remote occupancy <节点> 看是谁），不要替别人关。")
    return {"verdict": "refuse" if refused else "admit", "refused": refused, "warnings": warnings, "message": message,
            "kills": kills, "caller": caller}


def unity_claim_id(owner: str, project: str) -> str:
    """Deterministic per owner + project, so relaunching the same project replaces the old registration."""
    slug = lambda s: re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-")  # noqa: E731
    return f"unity-{slug(owner)[:40]}-{slug(project)[-48:]}".rstrip("-.")[:96]


def _norm(path: str | None) -> str:
    return ntpath.normpath(str(path).replace("/", "\\")).rstrip("\\").lower() if path else ""


def estimate_gb(pol: dict[str, Any], project: str | None) -> tuple[float, str]:
    p = _norm(project)
    for item in pol.get("project_estimates") or []:
        pattern = _norm(item.get("match"))
        if p and pattern and fnmatch.fnmatchcase(p, pattern):
            return float(item.get("gb")), f"policy project_estimates「{item.get('match')}」{item.get('note') or ''}".strip()
    return float(pol.get("unity_default_gb") or 12.0), "policy unity_default_gb"


# ── remote calls ──────────────────────────────────────────────────────────────

def _lib(mapper: Callable[[str], str], profile: Any) -> str:
    """``mapper`` maps remote paths (``Session.r`` / ``backend.map_path``; the fake backend relocates them)."""
    pol = policy(profile)
    return render("occ.ps1", {
        "OCC_ROOT": ps_quote(mapper(profile.occupancy_root)),
        "JOBS_ROOT": ps_quote(mapper(profile.jobs_root)),
        "NODE": ps_quote(profile.name),
        "POLICY": here_string(json.dumps(pol, ensure_ascii=False, indent=1)),
    })


RESULT_BEGIN = "OMNI-OCC-BEGIN"
RESULT_END = "OMNI-OCC-END"


def parse_result(text: str) -> dict[str, Any] | None:
    if RESULT_BEGIN not in text:
        return None
    body = text.rsplit(RESULT_BEGIN, 1)[1].split(RESULT_END, 1)[0]
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def remote_call(profile: Any, backend: Any, action: str, params: dict[str, Any] | None = None, *,
                timeout_s: int = 120) -> dict[str, Any]:
    """One occupancy action through the ordinary ``omni remote run`` delivery (the library is too long for one
    command line, so it rides run's file channel). Every call has its own run receipt; the parsed result is
    also saved next to it as ``occupancy-<action>.json``."""
    from . import ops
    from .store import write_json

    script = _lib(backend.map_path, profile) + "\n" + render("occupancy.ps1", {
        "ACTION": ps_quote(action), "PARAMS": here_string(json.dumps(params or {}, ensure_ascii=False)),
    })
    receipt = ops.run(profile, backend, None, inline=script, op="occupancy", label=f"occupancy {action}",
                      timeout_s=timeout_s)
    paths = receipt.get("paths") or {}
    output = ""
    if paths.get("output") and os.path.isfile(paths["output"]):
        with open(paths["output"], encoding="utf-8") as handle:
            output = handle.read()
    data = parse_result(output)
    if data is None:
        err = receipt.get("first_error") or {}
        data = {"ok": False, "error": err.get("message") or f"occupancy {action}: no result (phase={receipt.get('phase')})"}
    data["receipt"] = paths.get("result")
    data["wall_s"] = receipt.get("wall_s")
    data["run_calls"] = receipt.get("calls") or []
    if paths.get("receipt_dir"):
        data["parsed"] = str(write_json(__import__("pathlib").Path(paths["receipt_dir"]) / f"occupancy-{action}.json", data))
    return data


def _finish(profile: Any, backend: Any, op_label: str, data: dict[str, Any], *,
            extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """CLI-shaped result for an occupancy action (its run receipt is in data.receipt)."""
    ok = bool(data.get("ok"))
    return {"schema": "omni.remote.op/1", "op": "occupancy", "node": profile.name, "backend": backend.name,
            "label": op_label, "ok": ok, "phase": "completed" if ok else "failed", "exit_code": 0 if ok else 2,
            "wall_s": data.get("wall_s"), "calls": data.get("run_calls") or [],
            "first_error": None if ok else {"message": data.get("error")}, "data": data,
            "paths": {"result": data.get("receipt"), "occupancy_result": data.get("parsed"), **(extra or {}).get("paths", {})},
            **{k: v for k, v in (extra or {}).items() if k != "paths"}}


def _require(profile: Any) -> None:
    if not enabled(profile):
        raise RemoteRefused("occupancy", f"节点 {profile.name} 没有开占用表（config/remote_nodes.toml 加 [nodes.{profile.name}.occupancy]）")


def snapshot(profile: Any, backend: Any) -> dict[str, Any]:
    _require(profile)
    data = remote_call(profile, backend, "snapshot")
    return _finish(profile, backend, "occupancy snapshot", data)


def summary(profile: Any, backend: Any, *, limit: int = 20, timeout_s: int = 60) -> dict[str, Any]:
    _require(profile)
    data = remote_call(profile, backend, "summary", {"limit": int(limit)}, timeout_s=timeout_s)
    return _finish(profile, backend, "occupancy summary (read-only)", data)


def alerts(profile: Any, backend: Any, *, limit: int = 50) -> dict[str, Any]:
    _require(profile)
    data = remote_call(profile, backend, "alerts", {"limit": max(1, min(int(limit), 2000))})
    return _finish(profile, backend, "occupancy alerts (read-only)", data)


def make_claim_params(*, claim_id: str, owner: str, kind: str, pid: int | None = None, created_utc_ticks: str | None = None,
                      project: str | None = None, mem_gb: float | None = None, deadline: str | None = None,
                      on_breach: str | None = None, purpose: str | None = None, job: str | None = None,
                      estimate_gb: float | None = None, force: bool = False) -> dict[str, Any]:
    """``mem_gb`` is a budget the watchdog checks; ``estimate_gb`` only reserves room in the pool (never a breach)."""
    if not CLAIM_ID.match(claim_id):
        raise RemoteRefused("input", f"登记名只能用字母、数字、. - _（1–96 位）: {claim_id}")
    if kind not in KINDS:
        raise RemoteRefused("input", f"--kind 只能是 {'/'.join(KINDS)}: {kind}")
    if on_breach not in (None, "stop", "alert"):
        raise RemoteRefused("input", f"--on-breach 只能是 stop / alert: {on_breach}")
    if pid is None and not project:
        raise RemoteRefused("input", "登记要给 --pid，或给 --project 认领该工程唯一的 Unity 编辑器")
    budget = None
    if mem_gb or deadline or on_breach or estimate_gb:
        budget = {"mem_gb": float(mem_gb) if mem_gb else None, "estimate_gb": float(estimate_gb) if estimate_gb else None,
                  "deadline": parse_deadline(deadline),
                  "on_breach": on_breach or ("stop" if (mem_gb or deadline) else "alert")}
    return {"claim_id": claim_id, "owner": owner, "kind": kind, "pid": pid, "created_utc_ticks": created_utc_ticks,
            "project": project, "budget": budget, "purpose": purpose or "", "job": job,
            "machine": socket.gethostname(), "force": bool(force)}


def claim(profile: Any, backend: Any, **kwargs: Any) -> dict[str, Any]:
    _require(profile)
    params = make_claim_params(**kwargs)
    data = remote_call(profile, backend, "claim", params)
    return _finish(profile, backend, f"occupancy claim {params['claim_id']}", data,
                   extra={"paths": {"remote_claim": data.get("path")}})


def release(profile: Any, backend: Any, claim_id: str, *, owner: str, force: bool = False) -> dict[str, Any]:
    _require(profile)
    if not CLAIM_ID.match(claim_id):
        raise RemoteRefused("input", f"登记名只能用字母、数字、. - _: {claim_id}")
    data = remote_call(profile, backend, "release", {"claim_id": claim_id, "owner": owner, "force": bool(force)})
    return _finish(profile, backend, f"occupancy release {claim_id}", data,
                   extra={"paths": {"remote_claim": data.get("path")}})


# ── admission ─────────────────────────────────────────────────────────────────

def owner_label(entry: dict[str, Any]) -> str:
    source = entry.get("owner_source")
    if source == "claim":
        job = f" 作业 {entry['job']}" if entry.get("job") else ""
        return f"{entry.get('owner')}（已登记{job}{('：' + entry['purpose']) if entry.get('purpose') else ''}）"
    if source == "job":
        return f"omni remote 作业 {entry.get('job')}（未登记所有者）"
    if source == "inferred":
        return f"无主（推断：{entry.get('owner_hint')}）"
    if entry.get("kind") == "other":
        return "桌面/未登记进程"
    return "无主"


def describe_entry(entry: dict[str, Any]) -> str:
    what = entry.get("project") or ", ".join(entry.get("unity_projects") or [])
    budget = entry.get("budget") or {}
    extra = f" 预算 {budget.get('mem_gb')} GB" if budget.get("mem_gb") else ""
    if entry.get("bridge_port"):
        extra += f" demogame-unity 端口 {entry['bridge_port']}"
    return (f"{entry.get('tree_private_gb')} GB {entry.get('kind')} {entry.get('name')}{(' ' + what) if what else ''}"
            f" — {owner_label(entry)}"
            f" pid {entry.get('pid')}{extra}")


def _same_project(project: str, entry: dict[str, Any]) -> bool:
    want = _norm(project)
    return bool(want) and any(_norm(p) == want for p in [entry.get("project"), *(entry.get("unity_projects") or [])] if p)


def decide(snap: dict[str, Any], pol: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    mem = snap.get("memory") or {}
    entries = list(snap.get("entries") or [])
    mem_gb = float(request.get("mem_gb") or 0.0)
    light = mem_gb <= 0
    reserve = float(pol["light_reserve_gb"] if light else pol["reserve_gb"])
    commit_reserve = float(pol["light_commit_reserve_gb"] if light else pol["commit_reserve_gb"])
    checks: list[dict[str, Any]] = []
    if request.get("kind") == "unity" and request.get("project"):
        same = [e for e in entries if _same_project(request["project"], e)]
        checks.append({"rule": "same-project", "ok": not same, "occupants": [describe_entry(e) for e in same],
                       "say": f"同工程 {request['project']} 已有编辑器在跑：{'; '.join(describe_entry(e) for e in same)}"})
    if mem.get("available_gb") is not None:
        have = float(mem["available_gb"])
        need = round(mem_gb + reserve, 2)
        checks.append({"rule": "physical", "ok": have >= need, "have_gb": have, "need_gb": need,
                       "say": f"物理可用 {have} GB < 申请 {mem_gb:g} + 保留 {reserve:g} = {need} GB"})
    if mem.get("commit_free_gb") is not None:
        have = float(mem["commit_free_gb"])
        need = round(mem_gb + commit_reserve, 2)
        checks.append({"rule": "commit", "ok": have >= need, "have_gb": have, "need_gb": need,
                       "say": f"提交余量 {have} GB < 申请 {mem_gb:g} + 保留 {commit_reserve:g} = {need} GB"})
    if not light and mem.get("total_gb") is not None:
        pool = float(mem["total_gb"]) - float(pol["pool_reserve_gb"])
        used = float(snap.get("registered_budget_gb") or 0.0)
        checks.append({"rule": "budget-pool", "ok": used + mem_gb <= pool, "have_gb": round(pool - used, 2), "need_gb": mem_gb,
                       "say": f"已登记预算 {used:g} GB + 申请 {mem_gb:g} GB 超过可分配 {pool:g} GB"})
    failed = [c for c in checks if not c["ok"]]
    occupants = [describe_entry(e) for e in sorted(entries, key=lambda e: -float(e.get("tree_private_gb") or 0))[:8]]
    verdict = "admit" if not failed else "refuse"
    message = None
    if failed:
        source = request.get("mem_source")
        asked = f"（申请量来源：{source}）" if source and not light else ""
        message = ("准入不通过：" + "；".join(c["say"] for c in failed) + f"{asked}。当前占用（按内存）："
                   + "；".join(f"{i + 1}) {o}" for i, o in enumerate(occupants))
                   + "。不要替别人关进程：等它们结束（加 --queue-timeout 秒让本机排队重查）、"
                   "按占用表找所有者，或确认自己的申请量（--mem-gb）。")
    return {"verdict": verdict, "request": request, "checks": checks, "failed": [c["rule"] for c in failed],
            "queueable": bool(failed) and all(c["rule"] != "same-project" for c in failed),
            "memory": mem, "registered_budget_gb": snap.get("registered_budget_gb"), "occupants": occupants,
            "table_generated_at": snap.get("generated_at"), "message": message}


def admit(profile: Any, backend: Any, request: dict[str, Any], *, queue_timeout_s: int = 0,
          sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Take a fresh snapshot and decide; when only resources are short and a queue time is given, re-check
    deterministically (no model in the loop) until admitted or the time is up."""
    pol = policy(profile)
    every = max(5, int(pol.get("queue_every_s") or 30))
    started = time.monotonic()
    rounds = 0
    while True:
        rounds += 1
        data = remote_call(profile, backend, "snapshot", timeout_s=120)
        if not data.get("ok"):
            return {"verdict": "error", "request": request, "rounds": rounds, "receipt": data.get("receipt"),
                    "message": f"占用表采集失败，无法判断能不能起：{data.get('error')}（回执 {data.get('receipt')}）。"
                               "没声明内存的普通作业照常放行；Unity / 声明了内存的，确认机器状况后用 "
                               "--admission skip --reason \"<为什么>\"。"}
        decision = decide(data.get("snapshot") or {}, pol, request)
        decision["rounds"] = rounds
        decision["waited_s"] = round(time.monotonic() - started, 1)
        decision["table"] = ((data.get("snapshot") or {}).get("paths") or {}).get("table")
        decision["receipt"] = data.get("receipt")
        if decision["verdict"] == "admit" or not decision["queueable"]:
            return decision
        remaining = queue_timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            if queue_timeout_s > 0:
                decision["message"] = f"排队 {decision['waited_s']} 秒仍不够。" + (decision["message"] or "")
            return decision
        sleep(min(every, max(1.0, remaining)))


def build_request(profile: Any, *, script_text: str, mem_gb: float | None, kind: str | None,
                  project: str | None) -> dict[str, Any]:
    pol = policy(profile)
    detected = detect_unity_start(script_text)
    kind = kind or (detected or {}).get("kind") or "job"
    project = project or (detected or {}).get("project")
    source = "--mem-gb"
    estimate = None
    if mem_gb is None and kind == "unity":
        estimate, source = estimate_gb(pol, project)
    elif mem_gb is None:
        source = "未声明（只挡机器已危急）"
    return {"kind": kind, "project": project, "mem_gb": mem_gb if mem_gb is not None else (estimate or 0.0),
            "mem_source": source, "explicit_mem": mem_gb is not None, "detected": detected}


def refusal(session: Any, decision: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return session.finish({
        "ok": False, "phase": "refused", "exit_code": None,
        "refused": {"rule": "admission", "message": decision.get("message"), "detail": {"failed": decision.get("failed")}},
        "first_error": {"phase": "admission", "message": decision.get("message")},
        "admission": decision, **(extra or {}),
    })


# ── watchdog ──────────────────────────────────────────────────────────────────

def watchdog_script(backend: Any, profile: Any, *, hours: float, interval_s: int) -> str:
    return _lib(backend.map_path, profile) + "\n" + render("occ_watchdog.ps1", {
        "INTERVAL": str(max(10, int(interval_s))), "HOURS": repr(float(max(0.01, min(hours, 168.0)))),
    })
