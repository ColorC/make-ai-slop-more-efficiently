# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.hooks.shared_utilities.event_emitter.py"
"""Shared utilities for Claude Code and Codex hook scripts.

Hook scripts are short-lived (spawned once per event) and must:
  - read JSON from stdin
  - write JSON or text to stdout
  - exit 0 / 1 / 2 to signal allow / ask / block (or just 0 for non-permission events)

We keep this module tiny and dependency-free (sqlite3 + stdlib only) so hook
startup is sub-100ms.

References:
  - https://code.claude.com/docs/en/hooks-guide.md
  - https://learn.chatgpt.com/docs/hooks
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..provider_identity import (
    NATIVE_TRACE_PROVIDER_IDS,
    canonical_cli_provider_id,
    provider_event_source,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

# ─── stdin / stdout protocol ──────────────────────────────────────────────────


def read_stdin_json() -> dict[str, Any]:
    """Hooks always receive one JSON document on stdin (potentially with trailing newline)."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def hook_provider(payload: dict[str, Any] | None = None) -> str:
    """Return the canonical native-agent provider for this hook invocation.

    Installed commands pass ``--provider`` explicitly.  Environment and payload
    checks are retained for managed runners and direct tests; Claude remains the
    compatibility default for existing settings written before Codex hooks were
    supported.
    """

    explicit = os.environ.get("OMNI_AGENT_PROVIDER") or os.environ.get("OMNI_HOOK_PROVIDER")
    if not explicit:
        for index, arg in enumerate(sys.argv[1:]):
            if arg == "--provider" and index + 2 <= len(sys.argv[1:]):
                explicit = sys.argv[index + 2]
                break
            if arg.startswith("--provider="):
                explicit = arg.split("=", 1)[1]
                break
    if not explicit and payload:
        explicit = str(payload.get("provider") or "")
    if explicit:
        # Preserve the real provider instead of collapsing every non-Codex
        # hook to Claude. Unknown explicit values are still more truthful than
        # a fabricated Claude identity and remain auditable downstream.
        return canonical_cli_provider_id(explicit)
    if payload and hook_output_is_kimi(payload):
        return "kimi"
    return "claude_code"


def binding_provider(payload: dict[str, Any] | None = None) -> str:
    """Provider spelling used by the session binding/registry layers."""

    return hook_provider(payload)


def hook_source(payload: dict[str, Any] | None = None) -> str:
    """EventBus source spelling used by native hook events."""

    return provider_event_source(hook_provider(payload))


def provider_session_id(payload: dict[str, Any]) -> str:
    return str(payload.get("session_id") or payload.get("sessionId") or "")


_PATCH_FILE_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)
_PATCH_MOVE_RE = re.compile(r"^\*\*\* Move to:\s*(.+?)\s*$", re.MULTILINE)


def apply_patch_targets(command: str) -> list[tuple[str, str]]:
    """Extract ``(Edit|Write, path)`` targets from an apply_patch payload.

    Codex exposes file edits to hooks as one canonical ``apply_patch`` tool with
    the complete patch in ``tool_input.command``.  Claude exposes per-file
    Edit/Write calls.  Normalizing here lets existing write policy and plan
    synchronization logic evaluate every file in a multi-file Codex patch.
    """

    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in str(command or "").splitlines():
        file_match = _PATCH_FILE_RE.match(line)
        if file_match:
            action, raw_path = file_match.groups()
            item = ("Write" if action == "Add" else "Edit", raw_path.strip())
        else:
            move_match = _PATCH_MOVE_RE.match(line)
            if not move_match:
                continue
            item = ("Write", move_match.group(1).strip())
        if item[1] and item not in seen:
            seen.add(item)
            targets.append(item)
    return targets


def emit_additional_context(text: str) -> None:
    """Print a JSON envelope that Claude Code interprets as 'add this to the next turn's context'.

    Per hooks guide, this is the supported way to influence the upcoming LLM call
    without rewriting the user's prompt or system prompt (so prompt caching is preserved).
    """
    payload = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": text}}
    # The runtime accepts either generic `additionalContext` or event-scoped variant.
    # The simpler top-level `additionalContext` works for any pre-LLM hook.
    try:
        json.dump({"additionalContext": text}, sys.stdout)
        sys.stdout.write("\n")
    except OSError:
        pass


def emit_decision(allow: bool, reason: str = "") -> None:
    """Permission decision for PreToolUse hooks. exit code carries the meaning."""
    if reason:
        try:
            print(reason, file=sys.stderr)
        except OSError:
            pass
    sys.exit(0 if allow else 2)


def hook_output_is_kimi(payload: dict[str, Any] | None = None) -> bool:
    """Whether this hook invocation comes from Kimi Code CLI.

    Kimi never sends ``transcript_path`` in hook payloads (Claude Code always
    does), and Kimi does not understand Claude's ``additionalContext`` JSON
    envelope — for ``UserPromptSubmit`` it appends the hook's raw stdout text
    to context instead. Explicit provider flags (env / ``--provider`` argv /
    payload ``provider``) always win so Codex installs (which pass
    ``--provider=codex``) keep the JSON envelope; ``OMNI_HOOK_FORMAT=kimi|json``
    forces either side for tests.
    """

    fmt = os.environ.get("OMNI_HOOK_FORMAT", "").strip().lower()
    if fmt in ("kimi", "plain", "text"):
        return True
    if fmt in ("json", "claude", "codex"):
        return False
    if os.environ.get("OMNI_AGENT_PROVIDER") or os.environ.get("OMNI_HOOK_PROVIDER"):
        return False
    if any(a == "--provider" or a.startswith("--provider=") for a in sys.argv[1:]):
        return False
    if payload and payload.get("provider"):
        return False
    return bool(payload) and "hook_event_name" in payload and "transcript_path" not in payload


def emit_context(text: str, payload: dict[str, Any], event_name: str) -> None:
    """Emit context-injection output in the calling agent's wire format.

    Claude Code / Codex: ``{"hookSpecificOutput": {hookEventName, additionalContext}}``
    JSON envelope. Kimi Code CLI: plain stdout text (Kimi parses neither
    ``additionalContext`` nor any other structured context key; its
    UserPromptSubmit path appends raw stdout to the next turn's context).
    Observation-only events (SessionStart/PreCompact) have no context channel
    under Kimi — plain text is still emitted, harmlessly, so nothing needs to
    change if Kimi later supports it.
    """

    if hook_output_is_kimi(payload):
        try:
            sys.stdout.write(text + "\n")
        except OSError:
            pass
        return
    out = {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}
    try:
        json.dump(out, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    except OSError:
        pass


# ─── repo / plan discovery ────────────────────────────────────────────────────


def repo_root() -> Path:
    """Return the omnicompany workspace root."""
    try:
        from omnicompany.core.config import omni_workspace_root
        return omni_workspace_root()
    except Exception:
        here = Path.cwd().resolve()
        for d in (here, *here.parents):
            if (d / "src" / "omnicompany").is_dir() and (d / "docs").is_dir():
                return d
        return Path(__file__).resolve().parents[5]


PLAN_DIR_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\](.+)$")


def _read_cc_sessions_store(root: Path) -> dict[str, dict[str, Any]]:
    """Read the shared session registry through its cross-process guard.

    Schema mirrors pty_service._meta_store_path():
      { "<pty_id>": { claude_session_id, active_plan, started_at, ended_at, ... } }
    """
    try:
        # Late import keeps hook module import order simple while ensuring hook
        # readers participate in the same Windows-safe lock as daemon/CLI
        # writers. ``root`` remains in the signature for existing callers; the
        # canonical resolver also honours OMNI_CC_DAEMON_STATE_DIR.
        from omnicompany.dashboard.ccdaemon.pty import _read_meta_store

        # use_cache=True used to be wrong here for the wrong reason: every hook
        # invocation is a fresh subprocess, so pty.py's in-memory cache (keyed
        # on mtime/size/ino) starts empty regardless and this call always fell
        # through to a full json.loads() of the 9MB+ store (PERF-OBSERVATORY
        # P1.5b). It's now safe *and* fast: use_cache=True additionally checks
        # a cross-process marshal-backed disk cache keyed on that exact same
        # (path, mtime_ns, size, ino) tuple, so a hook only skips the parse
        # when the file provably has not changed since some process last read
        # or wrote it — never a staleness window, just a mtime-exact match.
        return _read_meta_store(use_cache=True)
    except (ImportError, json.JSONDecodeError, OSError, ValueError):
        return {}


def _historical_plan_for_claude_session(root: Path, claude_session_id: str) -> str | None:
    """If a previous PtySession with the same claude_session_id had active_plan, return it.

    Scenario: user closes browser tab → PTY reaps → user picks "resume" in dashboard
    → new PTY id but same claude_session_id (claude --resume). Without this, the new
    SessionStart hook would re-run mtime fallback and lose the prior plan binding.

    Returns plan_id (relative to docs/plans/) or None.
    """
    if not claude_session_id:
        return None
    store = _read_cc_sessions_store(root)
    matches: list[tuple[float, str]] = []
    for entry in store.values():
        if entry.get("claude_session_id") != claude_session_id:
            continue
        plan = entry.get("active_plan")
        if not plan:
            continue
        # prefer most recent (use ended_at, fall back to started_at)
        ts = entry.get("ended_at") or entry.get("started_at") or 0
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            ts_f = 0.0
        matches.append((ts_f, plan))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def detect_active_plan(
    root: Path | None = None,
    hint_cwd: str | None = None,
    claude_session_id: str | None = None,
    *,
    provider: str | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Which plan dir is the user currently bound to? Empty is a valid answer.

    Strategy (high → low priority):
      1. **Unified binding ledger**: provider + session id from
         ``cc_session_bindings.json`` (Claude and Codex).
      2. **Historical Claude PTY binding**: legacy ``cc_sessions.json`` fallback.
      3. **cwd-based**: if hint_cwd is inside a [date]NAME plan dir, use that.
      4. **None**: no signal, no guess. Hook should prompt the user to pick one
         explicitly via `omni plan use <id>` rather than silently grabbing whatever
         plan was last touched (mtime fallback was deliberately removed because the
         "last modified" plan is often not the plan the user is working on).
    """
    root = root or repo_root()
    plans = root / "docs" / "plans"
    if not plans.is_dir():
        return None

    effective_sid = session_id or claude_session_id
    effective_provider = provider or ("claude_code" if claude_session_id else None)

    # 1. provider-neutral binding ledger.
    if effective_sid and effective_provider:
        try:
            from omnicompany.packages.services._core.identity import binding_for_session

            record = binding_for_session(
                provider=effective_provider,
                session_id=effective_sid,
            )
            plan_id = record.get("active_plan")
            if plan_id:
                candidate = plans / str(plan_id)
                if candidate.is_dir():
                    return candidate
        except Exception:
            pass

    # 2. historical Claude PTY binding via cc_sessions.json.
    if effective_sid and effective_provider == "claude_code":
        plan_id = _historical_plan_for_claude_session(root, effective_sid)
        if plan_id:
            candidate = plans / plan_id
            if candidate.is_dir():
                return candidate

    # 3. cwd-based
    cwd_check = Path(hint_cwd or os.getcwd()).resolve()
    cur = cwd_check
    for _ in range(8):
        if PLAN_DIR_RE.match(cur.name):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent

    # 4. no signal → no plan. Don't guess.
    return None


def plan_id_of(plan_dir: Path) -> str:
    """Convert plan dir absolute path → catalogue id (e.g. `_infra/[2026-05-01]WEB-FOUNDATION`)."""
    plans = repo_root() / "docs" / "plans"
    try:
        rel = plan_dir.resolve().relative_to(plans.resolve())
        return str(rel).replace(os.sep, "/")
    except ValueError:
        return plan_dir.name


MAX_DEFAULT_CONTEXT_CHARS = 520


# ── 会话内注入中文计划名 (2026-08-20 用户裁定: 会话里要看到计划的中文命名) ──
_PLAN_TITLE_ZH: dict[str, str] | None = None


def _plan_title_zh_table() -> dict[str, str]:
    global _PLAN_TITLE_ZH
    if _PLAN_TITLE_ZH is None:
        table: dict[str, str] = {}
        try:
            import json as _json

            raw = _json.loads(
                (repo_root() / "data" / "registry" / "plan_governance.json").read_text(encoding="utf-8")
            )
            for pid, entry in (raw.get("plans") or {}).items():
                if isinstance(entry, dict) and entry.get("title_zh"):
                    table[str(pid)] = str(entry["title_zh"])
        except Exception:
            pass
        _PLAN_TITLE_ZH = table
    return _PLAN_TITLE_ZH


def plan_display_title(plan_id: str, plan_dir: Path, meta: dict[str, Any] | None = None) -> str:
    """治理覆盖表 title_zh > frontmatter title > 正文 H1 > slug 兜底。slug 本身不改。"""
    hit = _plan_title_zh_table().get(plan_id)
    if hit:
        return hit
    title = str((meta or {}).get("title") or "").strip()
    if title:
        return title
    # 只读 frontmatter (元数据), 绝不回落到正文 H1 ——
    # 注入上下文刻意不带 plan 正文 (见 test_session_start_uses_brief_pointer_without_plan_body)。
    try:
        head = (plan_dir / "plan.md").read_text(encoding="utf-8", errors="replace").splitlines()[:80]
    except Exception:
        return plan_id
    if not head or head[0].strip() != "---":
        return plan_id
    for line in head[1:]:
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return plan_id


def _relative_workspace_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace(os.sep, "/")
    except ValueError:
        return str(path)


def build_minimal_context(
    *,
    cwd: str,
    plan: Path | None,
    payload: dict[str, Any] | None = None,
    plan_meta: dict[str, Any] | None = None,
    reason: str | None = None,
) -> str:
    """Build the complete model-visible hook context.

    Default hook output is an index, not a summary of the indexed files.  Keep
    it below 520 characters so repeated starts, resumes and compactions cannot
    crowd out the user's actual request.
    """

    root = repo_root()
    suffix = f" ({reason})" if reason else ""
    lines = [f"# omnicompany context{suffix}"]
    binding: dict[str, Any] = {}
    if payload:
        try:
            from omnicompany.packages.services._core.identity import binding_for_session

            binding = binding_for_session(
                trace_id=trace_id_for(payload),
                provider=binding_provider(payload),
                session_id=provider_session_id(payload),
                pty_id=os.environ.get("OMNI_CC_PTY_ID"),
            ) or {}
        except Exception:
            binding = {}
    if not plan:
        lines.append(f"- workspace: `{cwd}`")
        lines.append("- active plan: none")
        for label, value in (
            ("project", binding.get("project")),
            ("workstream", binding.get("workstream")),
            ("topic", binding.get("topic")),
        ):
            clean = " ".join(str(value or "").split())
            if clean:
                lines.append(f"- {label}: {clean[:120]}")
        if binding.get("workstream"):
            lines.append("- resume: `omni workstream context`")
        elif not binding.get("project"):
            lines.append("- project lookup: `omni project find <称呼>`")
            lines.append("- skill route: `omni skill resolve <名称> --json` (只加载 canonical_path)")
            lines.append("- skills: load only the matching path when needed")
            lines.append("- durable work: `omni session bind --project <id> --workstream <id> --topic <one-focus>`")
        return "\n".join(lines)[:MAX_DEFAULT_CONTEXT_CHARS]

    plan_id = plan_id_of(plan)
    brief = plan / "brief.md"
    start_file = brief if brief.is_file() else plan / "plan.md"
    try:
        relative_plan = plan.resolve().relative_to(Path(cwd).resolve())
        plan_dir = str(relative_plan).replace(os.sep, "/") or "."
    except ValueError:
        plan_dir = _relative_workspace_path(plan, root)
    try:
        resume_path = str(start_file.resolve().relative_to(Path(cwd).resolve())).replace(os.sep, "/")
    except ValueError:
        resume_path = _relative_workspace_path(start_file, root)
    lines.extend([
        f"- 计划: {plan_display_title(plan_id, plan, plan_meta)}",
        f"- active plan: `{plan_id}`",
        f"- plan directory: `{plan_dir}`",
        f"- resume from: `{resume_path}`",
    ])

    meta = plan_meta or {}
    for label, value in (
        ("project", binding.get("project") or meta.get("project")),
        ("workstream", binding.get("workstream")),
        ("task", binding.get("task_id")),
        ("topic", binding.get("topic")),
    ):
        clean = " ".join(str(value or "").split())
        if clean:
            lines.append(f"- {label}: {clean[:160]}")
    # Keep the hook as a pointer.  Read brief.md only when the current task
    # actually needs plan-specific details; do not copy rule prose into every
    # Claude/Codex session start.
    if binding.get("workstream"):
        lines.append("- resume: `omni workstream context`")
    lines.append(f"- workspace: `{cwd}`")

    text = "\n".join(lines)
    if len(text) <= MAX_DEFAULT_CONTEXT_CHARS:
        return text
    return text[: MAX_DEFAULT_CONTEXT_CHARS - 1].rstrip() + "…"


# ─── swarm snapshot (SWARM-DISPATCH-PROACTIVITY P3) ──────────────────────────
# 一到两行"本机集群"快照, 只挂 SessionStart(不进 UserPromptSubmit, 不逐 prompt 重复)。
# 默认关闭。开关优先级: env OMNI_HOOK_SWARM_SNAPSHOT=on|off > flag 文件存在与否。
# 任何失败静默降级为空串 —— 快照绝不能拖慢或搞挂 hook。

SWARM_SNAPSHOT_ENV = "OMNI_HOOK_SWARM_SNAPSHOT"
_SWARM_SNAPSHOT_MAX_CHARS = 220


def _swarm_snapshot_flag_file() -> Path:
    return repo_root() / "config" / "hooks" / "swarm-snapshot.on"


def swarm_snapshot_enabled() -> bool:
    v = os.environ.get(SWARM_SNAPSHOT_ENV, "").strip().lower()
    if v in ("on", "1", "true"):
        return True
    if v in ("off", "0", "false"):
        return False
    try:
        return _swarm_snapshot_flag_file().is_file()
    except OSError:
        return False


def swarm_snapshot_line() -> str:
    """读 agent 注册表缓存(data/boss_sight/agent_registry.json), 出一行快照。

    不跑命令、不打网络、不烧 token; 注册表由 ccdaemon/omni agents refresh 维护,
    这里只消费。schema 变化或文件缺失 → 返回空串。
    """
    if not swarm_snapshot_enabled():
        return ""
    try:
        p = repo_root() / "data" / "boss_sight" / "agent_registry.json"
        entries = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(entries, dict):
            return ""
        # running 标志有僵尸(实测 48 条 running:true 大半是历史窗格);
        # 语义活跃 = running 且会话文件 30 分钟内动过(mtime 是原生会话 jsonl 的修改时间)。
        now = time.time()
        running = [
            e for e in entries.values()
            if isinstance(e, dict) and e.get("running") is True
            and isinstance(e.get("mtime"), (int, float)) and now - e["mtime"] < 1800
        ]
        if not running:
            return ""
        # P0/post 探针实证: worker 不会自己去读盘上的 AGENTS.md, 指针要带上
        # 授权语句和入口命令才构成决策时刻的可用信息(纯指针=又一跳摩擦)。
        line = (
            f"- swarm: 本机 {len(running)} 个 agent 在跑, 跨模型派发设施可用。"
            f"规格明确+可机械验收+体量够的机械活, 默认派发不必亲手做: "
            f"`omni worker run <provider> --spec <契约文件>` 或 `omni task dispatch`; "
            f"判据/分层/契约模板见工作区 AGENTS.md「集群派发」节"
        )
        return line[:_SWARM_SNAPSHOT_MAX_CHARS]
    except Exception:
        return ""


# ─── event bus (synchronous SQLite write) ────────────────────────────────────


def _events_db_path() -> Path:
    state_dir = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "ide_events.db"
    try:
        from omnicompany.core.config import resolve_unified_db_path
        return resolve_unified_db_path("ide_events.db")
    except Exception:
        pass
    return repo_root() / "data" / "ide_events.db"


def trace_id_for(stdin_payload: dict) -> str:
    """Pick the right trace_id for hook-emitted events.

    Prefer `OMNI_CC_PTY_ID` env (set by our PtyManager) so dashboard cc_session
    entities can find their own trace events. Fall back to claude's session_id
    if we weren't spawned through the wrapper (e.g. user runs `claude` directly
    from a terminal — events still land but won't be visible in the cc_session
    panel).
    """
    provider = hook_provider(stdin_payload)
    pty_id = os.environ.get("OMNI_CC_PTY_ID")
    if pty_id and provider not in NATIVE_TRACE_PROVIDER_IDS:
        return pty_id  # cc_session.id matches directly
    sid = provider_session_id(stdin_payload) or "unknown"
    prefix = "cc" if provider == "claude_code" else provider
    return f"{prefix}_{sid}"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS events (
        id          TEXT PRIMARY KEY,
        trace_id    TEXT NOT NULL,
        parent_id   TEXT,
        event_type  TEXT NOT NULL,
        source      TEXT NOT NULL,
        tags        TEXT NOT NULL DEFAULT '[]',
        timestamp   TEXT NOT NULL,
        data        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_events_trace ON events (trace_id, timestamp);
    """)


_INSERT_EVENT_SQL = (
    "INSERT INTO events (id, trace_id, parent_id, event_type, source, tags, timestamp, data) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

# ─── 可选的批量事件落库 (ccdaemon 常驻进程用, 2026-07 P0 批) ──────────────────
# 动机: emit_event 原实现每条事件都 新建连接+executescript+insert+commit+close,
# daemon 每条聊天消息 ≥2 次 (chat.py 的 normalized/raw 双发), 同步 sqlite IO 跑在
# 事件循环上, 是"任意请求随机慢到秒级"的来源之一。
# enable_batched_events() 后 (ccdaemon lifespan 调一次): emit_event 只 push 进
# queue, 后台 daemon 线程每 ~100ms 或满 50 条批量 executemany+commit (单连接复用)。
# 未启用时 (hooks 子进程等短生命周期场景) 保持原同步路径逐字不变。

_EVENT_BATCH_INTERVAL_S = 0.1
_EVENT_BATCH_MAX = 50


class _BatchedEventWriter:
    """单后台线程的批量落库器。失败只 log 不抛 — 事件流不能反过来拖死业务。

    队列条目协议: ("evt", db_path, row) / ("flush", done_event) / ("stop",)。
    """

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._conn: sqlite3.Connection | None = None
        self._conn_path: str | None = None
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            threading.Thread(target=self._run, name="ide-events-batch-writer", daemon=True).start()

    def submit(self, db_path: str, row: tuple) -> None:
        self._queue.put(("evt", db_path, row))

    def flush(self, timeout: float = 5.0) -> None:
        """把队列里已提交的事件同步写完 (atexit / 测试收口用)。"""
        if not self._started:
            return
        done = threading.Event()
        self._queue.put(("flush", done))
        done.wait(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        """写完残余并退出线程 (测试重置用)。"""
        if not self._started:
            return
        done = threading.Event()
        self._queue.put(("flush", done))
        done.wait(timeout)
        self._queue.put(("stop",))

    def _connect(self, db_path: str) -> sqlite3.Connection:
        if self._conn is not None and self._conn_path == db_path:
            return self._conn
        self._close_conn()
        conn = sqlite3.connect(db_path, timeout=2.0)
        _ensure_schema(conn)
        self._conn = conn
        self._conn_path = db_path
        return conn

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        self._conn = None
        self._conn_path = None

    def _write_batch(self, items: list[tuple[str, tuple]]) -> None:
        try:
            # 按 db_path 分组 (daemon 实际只有一个; 测试切 STATE_DIR 时会换连接)
            by_path: dict[str, list[tuple]] = {}
            for db_path, row in items:
                by_path.setdefault(db_path, []).append(row)
            for db_path, rows in by_path.items():
                conn = self._connect(db_path)
                conn.executemany(_INSERT_EVENT_SQL, rows)
                conn.commit()
        except sqlite3.Error as e:
            # 失败只 log 不抛; 连接可能坏了, 丢掉下批重建
            try:
                print(f"[cc_wrapper] batched event write failed: {e}", file=sys.stderr)
            except OSError:
                pass
            self._close_conn()

    def _run(self) -> None:
        pending: list[tuple[str, tuple]] = []
        deadline: float | None = None
        while True:
            timeout = (
                _EVENT_BATCH_INTERVAL_S
                if deadline is None
                else max(0.0, deadline - time.monotonic())
            )
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = ("tick",)
            kind = item[0]
            if kind == "evt":
                if not pending:
                    deadline = time.monotonic() + _EVENT_BATCH_INTERVAL_S
                pending.append((item[1], item[2]))
            elif kind == "stop":
                if pending:
                    self._write_batch(pending)
                self._close_conn()
                return
            # 超时空转 (tick) / flush / 满批 → 落库
            if pending and (kind in {"tick", "flush"} or len(pending) >= _EVENT_BATCH_MAX):
                self._write_batch(pending)
                pending = []
                deadline = None
            if kind == "flush":
                item[1].set()


_BATCHED_WRITER: _BatchedEventWriter | None = None


def enable_batched_events() -> None:
    """启用批量事件落库 (由 ccdaemon 启动时调用一次)。幂等, 重复调用不重复起线程。

    hooks 子进程等短生命周期场景不要调 — 它们需要"返回即落盘"的同步语义。
    """
    global _BATCHED_WRITER
    if _BATCHED_WRITER is None:
        writer = _BatchedEventWriter()
        writer.start()
        _BATCHED_WRITER = writer
        atexit.register(flush_batched_events)


def flush_batched_events(timeout: float = 5.0) -> None:
    """把已提交未落库的事件同步写完 (atexit 兜底, 防进程退出丢尾部事件)。"""
    writer = _BATCHED_WRITER
    if writer is not None:
        writer.flush(timeout)


def _reset_batched_events_for_test() -> None:
    """测试专用: flush + 停线程 + 清全局, 让下个用例从干净状态开始。"""
    global _BATCHED_WRITER
    writer = _BATCHED_WRITER
    _BATCHED_WRITER = None
    if writer is not None:
        writer.stop()


def emit_event(
    trace_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    parent_id: str | None = None,
    source: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Write one event. Returns the new event id.

    批量模式 (enable_batched_events 后): 只 push 队列即返回, 后台线程攒批落库,
    读侧 (traces/events API) 最坏多 ~100ms 延迟。未启用: 同步直写, 返回即落盘。
    """
    eid = uuid.uuid4().hex
    ts = datetime.now(timezone.utc).isoformat()
    db_path = _events_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    source = source or hook_source()
    body = {
        "id": eid,
        "trace_id": trace_id,
        "parent_id": parent_id,
        "event_type": event_type,
        "source": source,
        "tags": tags or [],
        "timestamp": ts,
        "payload": payload,
    }
    row = (
        eid, trace_id, parent_id, event_type, source,
        json.dumps(tags or []), ts, json.dumps(body, ensure_ascii=False),
    )
    writer = _BATCHED_WRITER
    if writer is not None:
        writer.submit(str(db_path), row)
        return eid
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        try:
            _ensure_schema(conn)
            conn.execute(_INSERT_EVENT_SQL, row)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as e:
        # never break the hook on a logging failure
        try:
            print(f"[cc_wrapper] event emit failed: {e}", file=sys.stderr)
        except OSError:
            pass
    return eid


# ─── audit / debug helper ────────────────────────────────────────────────────


def append_audit(scope: str, payload: dict[str, Any]) -> None:
    """Append one JSONL line to `data/cc_hooks_audit.jsonl` for debugging hooks
    that produced no observable effect. Bounded by file size — caller's burden."""
    p = repo_root() / "data" / "cc_hooks_audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), "scope": scope, "payload": payload}
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
