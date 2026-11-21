# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""辅助工具集 · 第八波 (2026-05-04).

包含: SnipRouter / TerminalCaptureRouter / BriefRouter / CtxInspectRouter / LSPRouter

各工具都是"轻量便利"型, 实现简短.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── SnipRouter · 截取/聚焦内容 ─────────────────────────────────


class SnipRouter(SingleToolRouter):
    """Save a snippet (text or image path) for later retrieval. Lightweight clipboard."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.create_file",)

    TOOL_NAME: ClassVar[str] = "Snip"
    DESCRIPTION: ClassVar[str] = (
        "Save a named snippet to .omni/snips/ for retrieval/sharing.\n"
        "\n"
        "Use cases:\n"
        "- Cache an LLM-generated artifact for later reference (e.g. JSON config)\n"
        "- Capture an excerpt of a long file for repeated reference\n"
        "\n"
        "Operations: save / get / list / delete"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["save", "get", "list", "delete"]},
            "name": {"type": "string"},
            "content": {"type": "string", "description": "Required for save"},
        },
        "required": ["operation"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        op = args.get("operation", "")
        if op not in ("save", "get", "list", "delete"):
            raise ToolExecutionError(f"operation must be save/get/list/delete, got {op!r}")

        base = Path(ctx.cwd) if ctx.cwd else Path.cwd()
        snip_dir = base / ".omni" / "snips"
        snip_dir.mkdir(parents=True, exist_ok=True)

        if op == "list":
            files = sorted(snip_dir.glob("*.txt"))
            return "\n".join(f"- {f.stem}" for f in files) or "(no snips)"

        name = (args.get("name") or "").strip()
        if not name:
            raise ToolExecutionError(f"{op} requires `name`")
        if any(c in name for c in r' /\:*?"<>|'):
            raise ToolExecutionError(f"name must be filesystem-safe: {name!r}")

        snip_path = snip_dir / f"{name}.txt"

        if op == "save":
            content = args.get("content", "")
            if not isinstance(content, str):
                raise ToolExecutionError("content must be string")
            snip_path.write_text(content, encoding="utf-8")
            return f"Snip {name!r} saved ({len(content)} chars)"
        if op == "get":
            if not snip_path.exists():
                raise ToolExecutionError(f"snip {name!r} not found")
            return snip_path.read_text(encoding="utf-8")
        # delete
        if not snip_path.exists():
            raise ToolExecutionError(f"snip {name!r} not found")
        snip_path.unlink()
        return f"Snip {name!r} deleted"


# ─── TerminalCaptureRouter · 终端截图 ──────────────────────────


class TerminalCaptureRouter(SingleToolRouter):
    """Capture current terminal scrollback or take a screenshot of an active terminal."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.create_file",)

    TOOL_NAME: ClassVar[str] = "TerminalCapture"
    DESCRIPTION: ClassVar[str] = (
        "Capture terminal output for diagnostics.\n"
        "\n"
        "Mode:\n"
        "- 'scrollback': read last N lines from a log file (defaults to .omni/logs/<session>.log)\n"
        "- 'screenshot': take a screenshot of the focused terminal window (uses OS tool)\n"
        "\n"
        "Mostly useful for sub-agents reporting back what they saw."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["scrollback", "screenshot"]},
            "log_path": {"type": "string", "description": "Path to log file (scrollback)"},
            "max_lines": {"type": "integer", "minimum": 1, "maximum": 5000},
            "out_path": {"type": "string", "description": "PNG output path (screenshot)"},
        },
        "required": ["mode"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        mode = args.get("mode", "")
        if mode not in ("scrollback", "screenshot"):
            raise ToolExecutionError(f"mode must be scrollback/screenshot, got {mode!r}")

        if mode == "scrollback":
            log_path = (args.get("log_path") or "").strip()
            if not log_path:
                raise ToolExecutionError("scrollback requires `log_path`")
            p = Path(log_path)
            if not p.exists():
                raise ToolExecutionError(f"log not found: {log_path}")
            max_lines = int(args.get("max_lines", 200))
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = lines[-max_lines:]
            return "\n".join(tail)

        # screenshot — 干跑场景
        if os.environ.get("OMNI_TERMINAL_CAPTURE_DRY_RUN") == "1":
            return json.dumps({
                "mode": "screenshot",
                "result": "(mock screenshot, dry-run)",
                "path": args.get("out_path", "(unset)"),
            }, ensure_ascii=False)
        raise ToolExecutionError(
            "screenshot mode requires platform-specific tool integration "
            "(not implemented in this router; set OMNI_TERMINAL_CAPTURE_DRY_RUN=1 for offline tests). "
            "For now, prefer scrollback mode pointing to a log file."
        )


# ─── BriefRouter · 给用户发简短消息 ────────────────────────────


class BriefRouter(SingleToolRouter):
    """Send a brief message to the user (separate channel from main agent text).

    omnicompany 实现: 写到 .omni/user_briefs/<id>.json, 类似 PushNotification 但语义"对话回复".
    """

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.create_file",)

    TOOL_NAME: ClassVar[str] = "Brief"
    DESCRIPTION: ClassVar[str] = (
        "Send a brief message to the user (preserves chat-style continuity).\n"
        "\n"
        "Use this when:\n"
        "- Replying to a direct user query\n"
        "- Surfacing a key result (decision, blocker, file:line)\n"
        "- A 'checkpoint' between long task phases\n"
        "\n"
        "Keep messages tight. Markdown supported. Status: 'normal' (replying) or 'proactive' (initiating)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "status": {"type": "string", "enum": ["normal", "proactive"]},
        },
        "required": ["message"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        message = (args.get("message") or "").strip()
        status = args.get("status", "normal")
        if not message:
            raise ToolExecutionError("message is required")
        if status not in ("normal", "proactive"):
            raise ToolExecutionError(f"status must be normal/proactive, got {status!r}")

        base = Path(ctx.cwd) if ctx.cwd else Path.cwd()
        briefs_dir = base / ".omni" / "user_briefs"
        briefs_dir.mkdir(parents=True, exist_ok=True)
        bid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = briefs_dir / f"{bid}.json"
        path.write_text(
            json.dumps({
                "id": bid,
                "message": message,
                "status": status,
                "ts": _now_iso(),
                "turn": ctx.turn_number,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return f"Brief sent ({status}, {len(message)} chars)"


# ─── CtxInspectRouter · 看自己的上下文 ─────────────────────────


class CtxInspectRouter(SingleToolRouter):
    """Inspect the current ToolContext fields (cwd / project_root / turn / custom)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "CtxInspect"
    DESCRIPTION: ClassVar[str] = (
        "Inspect the agent's current ToolContext (cwd, project_root, turn, custom fields).\n"
        "\n"
        "Useful when:\n"
        "- The agent is unsure which directory it's operating from\n"
        "- Debugging tool dispatch in tests\n"
        "- Checking what allowlists / registries are injected"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "include_custom": {
                "type": "boolean",
                "description": "Include custom fields (default true)",
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        include_custom = bool(args.get("include_custom", True))
        info = {
            "cwd": ctx.cwd,
            "project_root": ctx.project_root,
            "permission_mode": getattr(ctx, "permission_mode", "?"),
            "turn_number": getattr(ctx, "turn_number", 0),
        }
        if include_custom:
            # ToolContext 是 dataclass + dict 透传, 取 __dict__ 里的额外字段
            seen = set(info)
            for k, v in ctx.__dict__.items():
                if k in seen or k.startswith("_"):
                    continue
                # 把不可序列化的对象转成类型名
                try:
                    json.dumps(v)
                    info[k] = v
                except (TypeError, ValueError):
                    info[k] = f"<{type(v).__name__}>"
        return json.dumps(info, ensure_ascii=False, indent=2)


# ─── LSPRouter · LSP 诊断/跳转 (简版) ──────────────────────────


class LSPRouter(SingleToolRouter):
    """Run LSP-style diagnostics on a file (uses ruff/pyright if available, else no-op)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "LSP"
    DESCRIPTION: ClassVar[str] = (
        "Run language-server-style diagnostics (currently: ruff for Python).\n"
        "\n"
        "Action:\n"
        "- 'diagnose': run linter/type-checker on file_path\n"
        "\n"
        "Returns JSON with errors / warnings / info. omnicompany 没专 LSP 集成, 这里走 ruff CLI."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["diagnose"]},
            "file_path": {"type": "string"},
        },
        "required": ["action", "file_path"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        action = args.get("action")
        if action != "diagnose":
            raise ToolExecutionError(f"only 'diagnose' supported (got {action!r})")
        file_path = (args.get("file_path") or "").strip()
        if not file_path:
            raise ToolExecutionError("file_path is required")
        p = Path(file_path)
        if not p.is_absolute():
            raise ToolExecutionError(f"file_path must be absolute: {file_path}")
        if not p.exists():
            raise ToolExecutionError(f"file does not exist: {file_path}")

        if not p.suffix == ".py":
            return json.dumps({
                "tool": "(no LSP backend for this file type)",
                "diagnostics": [],
            }, ensure_ascii=False)

        ruff = shutil.which("ruff")
        if not ruff:
            return json.dumps({
                "tool": "(ruff not installed)",
                "diagnostics": [],
                "hint": "pip install ruff",
            }, ensure_ascii=False)

        try:
            result = subprocess.run(
                [ruff, "check", "--output-format=json", str(p)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise ToolExecutionError("ruff timed out")
        except OSError as e:
            raise ToolExecutionError(f"ruff invocation failed: {e}")

        # ruff exit 0 = no issues, 1 = issues found
        try:
            diagnostics = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError:
            diagnostics = []
        return json.dumps({
            "tool": "ruff",
            "diagnostics": diagnostics,
            "n_issues": len(diagnostics),
        }, ensure_ascii=False, indent=2)
