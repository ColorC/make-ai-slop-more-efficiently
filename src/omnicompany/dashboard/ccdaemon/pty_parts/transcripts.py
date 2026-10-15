# [OMNI] origin=arch-ratchet-surgery ts=2026-08-17 type=infra
# 本文件是 ccdaemon/pty.py 的 exec 装载分片：无 import、不可独立 import；
# 名字在宿主模块 globals 中解析（与拆分前单文件语义等价）。

def _claude_jsonl_for(
    cwd: str, claude_session_id: str | None, provider: str = "claude_code"
) -> Path | None:
    """Best-effort path to claude's own conversation log so we can verify a
    session is actually resumable via `claude --resume`.

    Claude's encoding (re-derived from real `~/.claude/projects/`, 6/6 dirs):
      `C:\\Users\\alice`           → `C--Users-alice`
      `E:\\MyWorkspace\\myproject` → `E--MyWorkspace-myproject`
    Rule: every character outside `[A-Za-z0-9]` becomes exactly one `-`.

    The previous form mapped colon → `--` *and then* the separator → `-`, which
    yields `C---Users-alice` and matched no real directory on Windows, where
    every cwd starts with a drive letter and a colon — while still agreeing with
    the two examples above, so the drift never showed up in review. Callers read
    the resulting ``None`` as "this session has no transcript", so the miss was
    silent: subagent activity lost its per-turn mtime signal and review readback
    returned empty assistant text for every claude session.

    The session id is globally unique, so fall back to globbing it: a session
    whose workspace moved, or a future change to claude's slugging, must still
    resolve instead of silently reporting "no transcript".

    ``provider`` selects which account's store to search: ``claude_2`` keeps
    its transcripts under its own CLAUDE_CONFIG_DIR, so searching the primary
    account's tree would report "no transcript" for a healthy session.
    """
    if not claude_session_id:
        return None
    base = claude_home_for(provider) / "projects"
    if cwd:
        enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
        p = base / enc / f"{claude_session_id}.jsonl"
        if p.is_file():
            return p
    if not base.is_dir():
        return None
    return next(
        (p for p in base.glob(f"*/{claude_session_id}.jsonl") if p.is_file()),
        None,
    )


def _codex_jsonl_for(
    provider_session_id: str | None, provider: str = "codex"
) -> Path | None:
    """Return Codex's durable rollout log for a real provider session id.

    ``provider`` selects which account's store to search: ``codex_2`` keeps its
    rollouts under its own CODEX_HOME, so searching the primary account's tree
    would report "no transcript" for a perfectly healthy session.
    """
    if not provider_session_id:
        return None
    base = codex_home_for(provider) / "sessions"
    if not base.is_dir():
        return None
    matches = base.glob(f"*/*/*/rollout-*-{provider_session_id}.jsonl")
    return next((path for path in matches if path.is_file()), None)


def _zcode_rollout_for(provider_session_id: str | None) -> Path | None:
    """Return zcode's model I/O transcript for an exact native session id."""
    if not provider_session_id:
        return None
    path = (
        Path.home()
        / ".zcode"
        / "cli"
        / "rollout"
        / f"model-io-{provider_session_id}.jsonl"
    )
    return path if path.is_file() else None


def _kimi_state_for(provider_session_id: str | None) -> Path | None:
    """Return Kimi's durable state file only when the native session exists."""
    if not provider_session_id:
        return None
    session_name = str(provider_session_id)
    if not session_name.startswith("session_"):
        session_name = f"session_{session_name}"
    base = Path.home() / ".kimi-code" / "sessions"
    return next((path for path in base.glob(f"*/{session_name}/state.json") if path.is_file()), None)


def _opencode_db_for(provider_session_id: str | None) -> Path | None:
    """Return OpenCode's DB only if it contains the requested native session."""
    if not provider_session_id:
        return None
    db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if not db_path.is_file():
        return None
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.2) as db:
            found = db.execute(
                "SELECT 1 FROM session WHERE id = ? LIMIT 1",
                (str(provider_session_id),),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    return db_path if found else None


def _codebuddy_jsonl_for(provider_session_id: str | None) -> Path | None:
    """Return CodeBuddy's durable project transcript for a native session."""
    if not provider_session_id:
        return None
    base = Path.home() / ".codebuddy" / "projects"
    if not base.is_dir():
        return None
    return next(
        (
            path
            for path in base.glob(f"*/{provider_session_id}.jsonl")
            if path.is_file()
        ),
        None,
    )


def _qoder_jsonl_for(provider_session_id: str | None) -> Path | None:
    """Return Qoder's durable project transcript for a native session.

    Layout mirrors Claude Code's: `~/.qoder/projects/<slugged-cwd>/<session>.jsonl`.
    The slug truncates long paths and appends a hash, so glob on the session id
    instead of re-deriving the project directory name.
    """
    if not provider_session_id:
        return None
    base = Path.home() / ".qoder" / "projects"
    if not base.is_dir():
        return None
    return next(
        (
            path
            for path in base.glob(f"*/{provider_session_id}.jsonl")
            if path.is_file()
        ),
        None,
    )


def _kiro_session_record(
    provider_session_id: str | None,
) -> tuple[Path, dict[str, Any]] | None:
    """Read Kiro's durable session JSON once and validate its native id."""
    if not provider_session_id:
        return None
    path = (
        Path.home()
        / ".kiro"
        / "sessions"
        / "cli"
        / f"{provider_session_id}.json"
    )
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if str(payload.get("session_id") or "") != str(provider_session_id):
        return None
    return path, payload


def _kiro_session_for(provider_session_id: str | None) -> Path | None:
    """Return Kiro's durable session JSON only when its native id matches."""
    record = _kiro_session_record(provider_session_id)
    return record[0] if record is not None else None


def _conversation_jsonl_for(
    provider: str | None,
    cwd: str,
    provider_session_id: str | None,
) -> Path | None:
    if provider in CLAUDE_FAMILY_PROVIDER_IDS:
        return _claude_jsonl_for(cwd, provider_session_id, provider)
    if provider in CODEX_FAMILY_PROVIDER_IDS:
        return _codex_jsonl_for(provider_session_id, provider)
    if provider == "zcode":
        return _zcode_rollout_for(provider_session_id)
    if provider == "kimi":
        return _kimi_state_for(provider_session_id)
    if provider == "opencode":
        return _opencode_db_for(provider_session_id)
    if provider == "codebuddy":
        return _codebuddy_jsonl_for(provider_session_id)
    if provider == "kiro":
        return _kiro_session_for(provider_session_id)
    if provider == "qoder":
        return _qoder_jsonl_for(provider_session_id)
    return None
