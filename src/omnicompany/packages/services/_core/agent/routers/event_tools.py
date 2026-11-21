# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""MonitorRouter / RemoteTriggerRouter / PushNotificationRouter · 事件类工具.

参考: 参考项目/claude-code-analysis/src/tools/{MonitorTool,RemoteTriggerTool,PushNotificationTool}/

Monitor: 跟踪 background 进程, 流式 stdout 当 notification.
RemoteTrigger: 远程触发外部 trigger (webhook / queue).
PushNotification: 给用户发非阻塞通知 (toast / desktop notification).

omnicompany 实现:
  - Monitor: 包装 BashBus 的 background subprocess + 标准 stdout 读取
  - RemoteTrigger: 写 .omni/trigger/<name>.trigger 文件 (外部 daemon 消费)
  - PushNotification: 写 .omni/notifications/<id>.json (omnicompany dashboard 显示)

干跑: OMNI_<TOOL>_DRY_RUN=1 不真启进程.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
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


# ─── MonitorRouter ───────────────────────────────────────────────


class MonitorRouter(SingleToolRouter):
    """Stream stdout from a background process or file (each line = an event)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "Monitor"
    DESCRIPTION: ClassVar[str] = (
        "Monitor a background process or follow a file (tail -f) for streaming events.\n"
        "\n"
        "- mode='process': runs `command` and yields stdout lines until exit / timeout\n"
        "- mode='tail': follows an existing log file (tail -f)\n"
        "- max_lines limits output (default 200)\n"
        "- timeout_sec limits wait (default 60s)"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["process", "tail"]},
            "command": {"type": "string", "description": "Bash command (mode=process)"},
            "file_path": {"type": "string", "description": "File to tail (mode=tail)"},
            "max_lines": {"type": "integer", "minimum": 1},
            "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600},
        },
        "required": ["mode"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        mode = args.get("mode", "")
        if mode not in ("process", "tail"):
            raise ToolExecutionError(f"mode must be 'process' or 'tail', got {mode!r}")
        max_lines = int(args.get("max_lines", 200))
        timeout_sec = int(args.get("timeout_sec", 60))

        if os.environ.get("OMNI_MONITOR_DRY_RUN") == "1":
            return json.dumps({
                "mode": mode,
                "lines": [f"[mock line {i+1}]" for i in range(min(max_lines, 5))],
                "exit_code": 0,
                "dry_run": True,
            }, ensure_ascii=False)

        if mode == "process":
            command = (args.get("command") or "").strip()
            if not command:
                raise ToolExecutionError("process mode requires `command`")
            return self._monitor_process(command, max_lines, timeout_sec)

        # tail
        file_path = (args.get("file_path") or "").strip()
        if not file_path:
            raise ToolExecutionError("tail mode requires `file_path`")
        return self._monitor_tail(file_path, max_lines, timeout_sec)

    def _monitor_process(self, command: str, max_lines: int, timeout_sec: int) -> str:
        try:
            proc = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except Exception as e:
            raise ToolExecutionError(f"failed to spawn process: {e}")

        lines: list[str] = []
        deadline = time.time() + timeout_sec
        try:
            for line in proc.stdout or []:
                lines.append(line.rstrip("\n"))
                if len(lines) >= max_lines:
                    break
                if time.time() > deadline:
                    break
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

        return json.dumps({
            "mode": "process",
            "command": command,
            "lines": lines,
            "exit_code": proc.returncode,
            "captured": len(lines),
        }, ensure_ascii=False, indent=2)

    def _monitor_tail(self, file_path: str, max_lines: int, timeout_sec: int) -> str:
        p = Path(file_path)
        if not p.exists():
            raise ToolExecutionError(f"file does not exist: {file_path}")

        # 简版 tail: 读现有内容末尾 + 等增量
        with p.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)  # 到末尾
            lines: list[str] = []
            deadline = time.time() + timeout_sec
            while time.time() < deadline and len(lines) < max_lines:
                line = f.readline()
                if line:
                    lines.append(line.rstrip("\n"))
                else:
                    time.sleep(0.1)

        return json.dumps({
            "mode": "tail",
            "file_path": file_path,
            "lines": lines,
            "captured": len(lines),
        }, ensure_ascii=False, indent=2)


# ─── RemoteTriggerRouter ─────────────────────────────────────────


class RemoteTriggerRouter(SingleToolRouter):
    """Trigger an external/remote handler by writing a trigger file (consumer agnostic)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.create_file",)

    TOOL_NAME: ClassVar[str] = "RemoteTrigger"
    DESCRIPTION: ClassVar[str] = (
        "Fire a one-shot trigger consumed by an external daemon / hook / queue.\n"
        "\n"
        "Writes a JSON file to .omni/triggers/<name>-<timestamp>.json with the payload.\n"
        "Consumer (sentinel / external service) picks it up. This tool only writes — "
        "no remote HTTP / RPC by default."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Trigger name"},
            "payload": {"description": "Arbitrary JSON-compatible payload"},
        },
        "required": ["name"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        name = (args.get("name") or "").strip()
        if not name:
            raise ToolExecutionError("name is required")
        if any(c in name for c in r' /\:*?"<>|'):
            raise ToolExecutionError(f"name must be filesystem-safe: {name!r}")
        payload = args.get("payload")

        base = Path(ctx.cwd) if ctx.cwd else Path.cwd()
        triggers_dir = base / ".omni" / "triggers"
        triggers_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        trigger_id = f"{name}-{ts}-{uuid.uuid4().hex[:6]}"
        trigger_path = triggers_dir / f"{trigger_id}.json"
        try:
            trigger_path.write_text(
                json.dumps({
                    "id": trigger_id,
                    "name": name,
                    "fired_at": _now_iso(),
                    "payload": payload,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            raise ToolExecutionError(f"failed to write trigger: {e}")

        return f"Trigger {trigger_id} fired (file: {trigger_path})"


# ─── PushNotificationRouter ──────────────────────────────────────


class PushNotificationRouter(SingleToolRouter):
    """Send a non-blocking notification to the user (omnicompany dashboard / desktop)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.create_file",)

    TOOL_NAME: ClassVar[str] = "PushNotification"
    DESCRIPTION: ClassVar[str] = (
        "Send a non-blocking notification to the user.\n"
        "\n"
        "- title + message (required) → toast / dashboard alert\n"
        "- severity: info / warning / urgent (default info)\n"
        "- Persists to .omni/notifications/<id>.json — dashboard / external listener consumes\n"
        "- Does NOT block; returns immediately"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "message": {"type": "string"},
            "severity": {"type": "string", "enum": ["info", "warning", "urgent"]},
        },
        "required": ["title", "message"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        title = (args.get("title") or "").strip()
        message = (args.get("message") or "").strip()
        severity = args.get("severity", "info")
        if not title:
            raise ToolExecutionError("title is required")
        if not message:
            raise ToolExecutionError("message is required")
        if severity not in ("info", "warning", "urgent"):
            raise ToolExecutionError(f"invalid severity: {severity!r}")

        base = Path(ctx.cwd) if ctx.cwd else Path.cwd()
        notif_dir = base / ".omni" / "notifications"
        notif_dir.mkdir(parents=True, exist_ok=True)

        nid = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        path = notif_dir / f"{nid}.json"
        try:
            path.write_text(
                json.dumps({
                    "id": nid,
                    "title": title,
                    "message": message,
                    "severity": severity,
                    "ts": _now_iso(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            raise ToolExecutionError(f"failed to write notification: {e}")

        return f"Notification {nid} sent (severity: {severity}): {title}"
