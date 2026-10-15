# [OMNI] origin=claude-code ts=2026-09-12 type=service
# [OMNI] summary="画布写入身份(WriteIdentity): 机器(绑 owner)+运行时+厂商原生会话 id(jsonl 文件名)+目的/批号, 被动采集会话无感"
# [OMNI] why="业主 2026-09-12: agent 写入必须可溯源到机器/运行时/会话, 人写入必须标为意见; 目的可后补, 批量正式写入要从本地历史总结目的"
# [OMNI] tags=identity,canvas,provenance
"""写入身份（与 webworks/packages/material-core/note.ts 的 WriteIdentity 同形）。

Python 侧只做被动采集：从 `current_session_meta()`（hook 已落台账）、环境变量与
`config/machine-identity.yaml` 里读，读不到的留空，绝不编。TS 侧同名实现在
`webworks/packages/write-identity`；两边字段一一对应，落库前由 canvas-store 统一校验。
"""
from __future__ import annotations

import os
import platform
import socket
from pathlib import Path
from typing import Any

DEFAULT_MACHINE_IDENTITY_FILE = Path("C:/workspace/config/machine-identity.yaml")
WRITE_INPUT_METHODS = ("keyboard", "touch", "mouse", "pen", "paste", "drop", "voice", "drawing", "cli")


def machine_identity_file() -> Path:
    override = os.environ.get("OMNI_MACHINE_IDENTITY_FILE")
    return Path(override) if override else DEFAULT_MACHINE_IDENTITY_FILE


def _parse_flat_yaml(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key.strip():
            out[key.strip()] = value
    return out


def read_machine_identity_file() -> dict[str, str] | None:
    file = machine_identity_file()
    if not file.is_file():
        return None
    try:
        return _parse_flat_yaml(file.read_text(encoding="utf-8"))
    except OSError:
        return None


def machine_guid() -> str | None:
    """Windows 机器自身标识（注册表 MachineGuid）；非 Windows 返回 None。"""
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value).strip().lower() or None
    except OSError:
        return None


def machine_identity() -> dict[str, Any]:
    configured = read_machine_identity_file() or {}
    machine: dict[str, Any] = {"id": configured.get("id", "").strip() or machine_guid() or socket.gethostname()}
    label = configured.get("label", "").strip() or socket.gethostname()
    if label:
        machine["label"] = label
    owner = configured.get("owner", "").strip()
    if owner:
        machine["owner"] = owner
    os_name = configured.get("os", "").strip() or ("windows" if os.name == "nt" else platform.system().lower())
    if os_name:
        machine["os"] = os_name
    return machine


def _claude_config_dirs() -> list[Path]:
    dirs: list[Path] = []
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        dirs.append(Path(override))
    home = Path.home()
    try:
        for entry in sorted(home.iterdir()):
            if entry.is_dir() and entry.name.startswith(".claude"):
                dirs.append(entry)
    except OSError:
        pass
    seen: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return seen


def locate_session_file(provider: str | None, native_id: str) -> str | None:
    """厂商原生 id → 磁盘上的 jsonl；找不到返回 None（不猜）。"""
    if not native_id:
        return None
    lower = (provider or "").lower()
    want_claude = not lower or lower.startswith("claude")
    want_codex = not lower or lower.startswith("codex")
    if want_claude:
        for config_dir in _claude_config_dirs():
            projects = config_dir / "projects"
            if not projects.is_dir():
                continue
            for hit in projects.glob(f"*/{native_id}.jsonl"):
                return str(hit)
    if want_codex:
        sessions = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"
        if sessions.is_dir():
            for hit in sessions.glob(f"*/*/*/rollout-*{native_id}.jsonl"):
                return str(hit)
    return None


def current_write_identity(
    *,
    purpose: str | None = None,
    batch: str | None = None,
    captured_by: str = "cli",
) -> dict[str, Any]:
    """当前 CLI 进程的写入身份。跑在 agent 会话里 = agent；没有运行时线索 = system。"""
    from .resolver import binding_for_session, current_session_meta

    meta: dict[str, Any] = {}
    try:
        meta = dict(current_session_meta() or {})
    except Exception:  # noqa: BLE001 - 身份采集永不阻断写入
        meta = {}
    provider = str(meta.get("provider") or os.environ.get("OMNI_AGENT_PROVIDER") or "").strip()
    if not provider:
        if os.environ.get("CLAUDE_CODE_SESSION_ID"):
            provider = "claude_code"
        elif os.environ.get("CODEX_THREAD_ID"):
            provider = "codex"
    native_id = str(
        os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or meta.get("session_id")
        or meta.get("claude_session_id")
        or ""
    ).strip()
    model = ""
    try:
        record = binding_for_session(trace_id=meta.get("trace_id"), pty_id=meta.get("pty_id")) or {}
        model = str(record.get("model") or "").strip()
    except Exception:  # noqa: BLE001
        model = ""
    identity: dict[str, Any] = {
        "principal": "agent" if provider else "system",
        "machine": machine_identity(),
        "captured_by": captured_by,
        "at": _now_iso(),
    }
    if provider:
        runtime: dict[str, Any] = {"provider": provider}
        if model:
            runtime["model"] = model
        identity["runtime"] = runtime
    if native_id:
        session: dict[str, Any] = {"native_id": native_id}
        file = locate_session_file(provider, native_id)
        if file:
            session["file"] = file
        trace_id = str(meta.get("trace_id") or "").strip()
        if trace_id:
            session["trace_id"] = trace_id
        pty_id = str(meta.get("pty_id") or "").strip()
        if pty_id:
            session["pty_id"] = pty_id
        identity["session"] = session
    purpose_text = (purpose if purpose is not None else os.environ.get("OMNI_WRITE_PURPOSE") or "").strip()
    if purpose_text:
        identity["purpose"] = purpose_text[:2000]
    batch_text = (batch if batch is not None else os.environ.get("OMNI_WRITE_BATCH") or "").strip()
    if batch_text:
        identity["batch"] = batch_text[:200]
    return identity


def human_write_identity(name: str, *, input_method: str = "cli", device: str | None = None,
                         purpose: str | None = None) -> dict[str, Any]:
    """人经 CLI 代写时的身份（比如把口述意见录进画布）。"""
    if input_method not in WRITE_INPUT_METHODS:
        raise ValueError(f"input_method 必须是 {', '.join(WRITE_INPUT_METHODS)} 之一")
    identity: dict[str, Any] = {
        "principal": "human",
        "machine": machine_identity(),
        "human": {"name": name.strip(), "input": input_method, **({"device": device} if device else {})},
        "captured_by": "cli",
        "at": _now_iso(),
    }
    if purpose and purpose.strip():
        identity["purpose"] = purpose.strip()[:2000]
    return identity


def write_identity_label(identity: dict[str, Any] | None) -> str:
    """人看的一句话（与 TS writeIdentityLabel 同口径）。"""
    if not isinstance(identity, dict):
        return "—"
    machine = identity.get("machine") if isinstance(identity.get("machine"), dict) else {}
    if identity.get("principal") == "human":
        human = identity.get("human") if isinstance(identity.get("human"), dict) else {}
        labels = {"keyboard": "键盘", "touch": "触屏", "mouse": "鼠标", "pen": "手写笔", "paste": "粘贴",
                  "drop": "拖入", "voice": "语音", "drawing": "绘画", "cli": "命令行"}
        parts = [str(human.get("name") or "人"), labels.get(str(human.get("input")), str(human.get("input") or ""))]
        device = human.get("device") or machine.get("label")
        if device:
            parts.append(str(device))
        return " · ".join(p for p in parts if p)
    runtime = identity.get("runtime") if isinstance(identity.get("runtime"), dict) else {}
    session = identity.get("session") if isinstance(identity.get("session"), dict) else {}
    parts = [str(runtime.get("label") or runtime.get("provider") or identity.get("principal") or "")]
    if session.get("native_id"):
        parts.append(f"会话 {str(session['native_id'])[:8]}")
    parts.append(str(machine.get("label") or str(machine.get("id") or "")[:8]))
    return " · ".join(p for p in parts if p)


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
