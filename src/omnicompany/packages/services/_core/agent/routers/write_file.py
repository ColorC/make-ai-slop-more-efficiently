# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-24T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.file_writer.whitelisted.py"
"""WriteFileRouter · 受限写文件 SingleTool (堵不如疏).

**设计哲学** (2026-04-24 用户明示):
  Worker prompt 再怎么说 "不要 write_file" 也拦不住 LLM 试. 与其禁, 不如提供**受限工具** —
  写入目标文件成功, 写入其他文件抛可理解的错, 错误消息把 LLM 推回正确路径.

**白名单机制**:
  从 ToolContext 读 `allowed_write_paths: tuple[str, ...]` — 绝对路径精确匹配.
  白名单为空 (ctx 未注入) 或路径不在白名单 → ToolExecutionError, 带清晰指引.

**典型用法** (Worker 侧):
```python
# 在 Worker 的 build_tool_context override:
def build_tool_context(self, *, input_data, turn, trace_id):
    ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
    target = str(Path(self._resolve_output_dir(input_data)) / self.OUTPUT_FILENAME)
    ctx["allowed_write_paths"] = (target,)
    return ctx
```

**边界**:
  - 只写入 utf-8 text. 二进制 → 其他 Tool (如 CopyFileRouter).
  - mkdir -p 父目录 (写入前保证目录存在).
  - 覆盖语义: 默认覆盖 (LLM 通常想重产). 若要 append, 扩参数.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


def _record_file_ownership(
    *,
    matched_root: Path,
    written_path: Path,
    existed_before: bool,
    ctx: ToolContext,
) -> None:
    """副作用: 记录 task ownership 到 `<matched_root>/.omni/file_ownership.jsonl` (append-only).

    Stage E P1.1 (2026-04-25): 让失败回路能从 file → task_id 反查原任务上下文.
    best-effort — 落盘失败只 warn 不抛, 主 write 已成功.
    `current_task_id` 由 Worker 的 build_tool_context 注入; 缺失记 'unknown'.
    """
    try:
        log_dir = matched_root / ".omni"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "file_ownership.jsonl"
        try:
            rel = str(written_path.relative_to(matched_root))
        except ValueError:
            rel = str(written_path)
        record = {
            "file": rel.replace("\\", "/"),
            "task_id": getattr(ctx, "current_task_id", "unknown") or "unknown",
            "trace_id": getattr(ctx, "trace_id", "") or "",
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "op": "modify" if existed_before else "create",
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[WriteFileRouter] ownership log failed (non-fatal): %s", e)


class WriteFileRouter(SingleToolRouter):
    # 写文件三种语义都可能 (create / overwrite / append) 视参数 mode
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = (
        "meta_io.fs.create_file",
        "meta_io.fs.overwrite_file",
        "meta_io.fs.append_to_file",
    )

    """Write text file to a path in the context's allowed_write_paths whitelist.

    Writing anywhere else raises a clear error that guides the LLM back to
    (a) the allowed target path, or (b) emitting output as assistant text.

    To use: inject `allowed_write_paths: tuple[str, ...]` into ToolContext
    via Worker's `build_tool_context()` override.
    """

    TOOL_NAME: ClassVar[str] = "write_file"
    DESCRIPTION: ClassVar[str] = (
        "Write UTF-8 text content to a file. "
        "Each caller has a pre-declared allowlist of target paths in the tool context. "
        "- If `path` matches the allowlist: file is overwritten and '{bytes} bytes written' is returned.\n"
        "- If `path` is anywhere else: the call is REFUSED with a message telling you where the allowed "
        "targets are. In that case, emit your output as assistant text instead — the Worker's "
        "extract_result step will persist it for you.\n"
        "- Parent directory is auto-created if missing.\n"
        "- Do NOT use for binary content; only UTF-8 text."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute (or resolvable) file path to write. Must match context allowlist.",
            },
            "content": {
                "type": "string",
                "description": "UTF-8 text to write. Will overwrite existing content.",
            },
        },
        "required": ["path", "content"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = (args.get("path") or "").strip()
        content = args.get("content", "")
        if not raw_path:
            raise ToolExecutionError("path is required")
        if not isinstance(content, str):
            raise ToolExecutionError(f"content must be string, got {type(content).__name__}")

        # 两种白名单模式, 从 ToolContext 读:
        #   (1) allowed_write_paths: tuple[str] — 精确路径白名单 (Stage C 的原始模式)
        #   (2) allowed_write_roots: tuple[str] — 目录树根, 递归允许其子路径 (Stage D 新增)
        # 两者任一匹配即通过. 都未声明 → 错误 (2026-04-24 堵不如疏, 从严默认)
        allowed_paths = getattr(ctx, "allowed_write_paths", None) or ()
        allowed_roots = getattr(ctx, "allowed_write_roots", None) or ()
        if not allowed_paths and not allowed_roots:
            raise ToolExecutionError(
                "write_file is not permitted in this tool context "
                "(neither allowed_write_paths nor allowed_write_roots declared). "
                "Emit your output as ASSISTANT TEXT instead — "
                "the Worker's extract_result step will persist the markdown to disk."
            )

        try:
            abs_path = str(Path(raw_path).resolve())
        except Exception as e:
            raise ToolExecutionError(f"cannot resolve path {raw_path!r}: {e}")

        # 精确匹配 (模式 1)
        paths_norm = {str(Path(p).resolve()) for p in allowed_paths}
        path_match = abs_path in paths_norm

        # 根树匹配 (模式 2): abs_path 在某 root 的子树里 (含 root 自身)
        roots_norm = []
        root_match = False
        matched_root: Path | None = None
        for r in allowed_roots:
            try:
                root_resolved = Path(r).resolve()
            except Exception:
                continue
            roots_norm.append(str(root_resolved))
            try:
                Path(abs_path).resolve().relative_to(root_resolved)
                root_match = True
                matched_root = root_resolved
                break
            except ValueError:
                continue

        if not (path_match or root_match):
            allow_lines = []
            if paths_norm:
                allow_lines.append("Allowed exact files:\n  - " + "\n  - ".join(sorted(paths_norm)))
            if roots_norm:
                allow_lines.append("Allowed root trees (recursive):\n  - " + "\n  - ".join(sorted(roots_norm)))
            allow_block = "\n".join(allow_lines) if allow_lines else "(no allowlist declared)"
            raise ToolExecutionError(
                f"write_file REFUSED: '{raw_path}' is outside this Worker's allowlist.\n"
                f"{allow_block}\n"
                f"If you want to write a different artifact, you have TWO correct options:\n"
                f"  (a) emit it as assistant TEXT (extract_result will persist per Worker protocol), OR\n"
                f"  (b) confine the path to an allowed file or a descendant of an allowed root.\n"
                f"Do not retry with a different path unless it matches the allowlist."
            )

        try:
            target = Path(abs_path)
            existed_before = target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception as e:
            raise ToolExecutionError(f"write failed: {e}")

        # Stage E P1.1 (2026-04-25): 副作用记 ownership 到匹配 root 的 .omni/file_ownership.jsonl.
        # 仅 root_match 路径触发 (路径白名单是单文件 sink, 不是任务工作树).
        if root_match and matched_root is not None:
            _record_file_ownership(
                matched_root=matched_root,
                written_path=target,
                existed_before=existed_before,
                ctx=ctx,
            )

        # L5 协议 (Wave 5+7, 2026-05-04): Write 成功后把 abs_path add 进 ctx.read_files,
        # 后续 Edit 视为已 read (LLM 自己写的内容知道当前状态). 不 add 会导致 Write→Edit 流被状态机拦.
        read_files = getattr(ctx, "read_files", None)
        if read_files is not None:
            try:
                read_files.add(str(target.resolve()))
            except Exception:
                pass

        return f"Wrote {len(content)} chars ({len(content.encode('utf-8'))} bytes) to {abs_path}"
