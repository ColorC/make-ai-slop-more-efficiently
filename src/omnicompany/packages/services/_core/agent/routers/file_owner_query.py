# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-25T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.file_ownership.query_tool.py"
"""FileOwnerQueryRouter · 反查文件归属任务 SingleTool (Stage E P1.3).

**用途** (2026-04-25):
  Stage E 修复回路 — 静态/单测/集成检查发现某文件出错, 需回流原任务上下文.
  本工具读 `<root>/.omni/file_ownership.jsonl` (WriteFileRouter 副作用产出),
  返回该 file 的 task_id + 完整修改历史.

**典型调用** (RepairAgentWorker 用):
  file_owner({file_path: "src/screens/MainBoardPrepare.js", root: "...autochess-ui-agent-v7"})
  → {task_id: "T-a08", trace_id: "...", last_op: "create", history: [...]}

**ctx 注入** (Worker 侧):
  ctx["allowed_ownership_roots"] = (autochess_ui_agent_v7_path,)  # 允许查询的 root 白名单
  没声明 → 从 args 拿但不强制
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


class FileOwnerQueryRouter(SingleToolRouter):
    CONSUMED_META_IO = ("meta_io.fs.read_file_text",)  # 读 ownership log
    PRODUCED_META_IO = ()

    """Query the task ownership of a file by reading the .omni/file_ownership.jsonl log.

    Returns the task_id that wrote the file, plus full modify history.
    Useful for the Stage E repair workflow: failure file → owner task → original spec.
    """

    TOOL_NAME: ClassVar[str] = "file_owner"
    DESCRIPTION: ClassVar[str] = (
        "Look up which task created or modified a given file. "
        "Reads the workspace's `.omni/file_ownership.jsonl` (an append-only log "
        "automatically updated by write_file). Use this when investigating a failure "
        "to find the original task that produced the bad file, so you can fetch its "
        "spec and evidence as repair context.\n\n"
        "Returns: {file, task_id (latest), trace_id (latest), last_op, last_ts, history: [...]}.\n"
        "If the file is not in the log: returns {file, task_id: 'unknown', history: []}."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "File path to query. Can be absolute, or relative to `root`. Will be resolved against `root` to look up in the ownership log.",
            },
            "root": {
                "type": "string",
                "description": "Workspace root containing `.omni/file_ownership.jsonl`. Defaults to first entry of context.allowed_ownership_roots.",
            },
        },
        "required": ["file_path"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        file_path_arg = (args.get("file_path") or "").strip()
        root_arg = (args.get("root") or "").strip()
        if not file_path_arg:
            raise ToolExecutionError("file_path is required")

        # 解析 root: args 优先, fallback 到 ctx.allowed_ownership_roots[0]
        if root_arg:
            root = Path(root_arg).resolve()
        else:
            allowed = getattr(ctx, "allowed_ownership_roots", None) or ()
            if not allowed:
                raise ToolExecutionError(
                    "no `root` arg given and ctx.allowed_ownership_roots is empty. "
                    "Provide root explicitly (workspace dir containing .omni/)."
                )
            root = Path(allowed[0]).resolve()

        log_path = root / ".omni" / "file_ownership.jsonl"
        if not log_path.exists():
            return json.dumps(
                {
                    "file": file_path_arg,
                    "root": str(root),
                    "task_id": "unknown",
                    "trace_id": "",
                    "last_op": None,
                    "last_ts": None,
                    "history": [],
                    "note": f"ownership log not found at {log_path}",
                },
                ensure_ascii=False,
                indent=2,
            )

        # 解析 file_path: 既允许绝对也允许相对 root
        try:
            fp_abs = Path(file_path_arg)
            if not fp_abs.is_absolute():
                fp_abs = (root / file_path_arg).resolve()
            else:
                fp_abs = fp_abs.resolve()
            try:
                rel_norm = str(fp_abs.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel_norm = str(fp_abs).replace("\\", "/")
        except Exception:
            rel_norm = file_path_arg.replace("\\", "/")

        history: list[dict] = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rec_file = (rec.get("file") or "").replace("\\", "/")
                    if rec_file == rel_norm or rec_file == file_path_arg.replace("\\", "/"):
                        history.append(rec)
        except Exception as e:
            raise ToolExecutionError(f"failed to read ownership log {log_path}: {e}")

        if not history:
            return json.dumps(
                {
                    "file": rel_norm,
                    "root": str(root),
                    "task_id": "unknown",
                    "trace_id": "",
                    "last_op": None,
                    "last_ts": None,
                    "history": [],
                    "note": "no ownership records for this file",
                },
                ensure_ascii=False,
                indent=2,
            )

        latest = history[-1]
        return json.dumps(
            {
                "file": rel_norm,
                "root": str(root),
                "task_id": latest.get("task_id", "unknown"),
                "trace_id": latest.get("trace_id", ""),
                "last_op": latest.get("op"),
                "last_ts": latest.get("ts"),
                "history": history,
            },
            ensure_ascii=False,
            indent=2,
        )
