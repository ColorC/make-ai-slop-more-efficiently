# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-18
# [OMNI] material_id="material:core.agent.routers.tool_executor.base_and_builtins.py"
"""SingleToolRouter — 单个工具的 Router 基类 + 5 个具体工具子类

每个具体工具一个 Router 子类，把：
- 工具名 + Anthropic API schema + 工具描述（用于 LLM）作为 ClassVar 声明
- 执行逻辑放在 `_execute(args, ctx) -> str` 里（同步，内部必要时再 to_thread）

与旧 ToolDefinition 的区别：
- 工具输入/输出是 Format (tool.<name>-request / tool.<name>-response)，经 bus 落盘
- 每次调用是一次完整 Router.run()，可独立单测、replay

本阶段先复用 `runtime/exec/tool_executor.ToolExecutor` 作为底层实现，
阶段 C/D 把 ripgrep/glob/list_dir 的具体实现完全搬进 Router（断掉 executor 依赖）。

Claude Code 1:1 对齐（plan §0.4 维度 1-7）：
- Schema 字段完整（已在 tool_executor 对齐）
- Description 严格复刻（从 agent_loop_tools 迁）
- 错误处理分支齐（超时 / 拒绝 / partial results）
- head_limit=0 作为 unlimited 逃生口
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.exec.tool_executor import ToolExecutor
from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.packages.services._core.agent._bus import (
    emit_router_input,
    emit_router_output,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# ToolExecutionError — 工具业务错误信号（对齐 CC throw new Error 模式）
# ═══════════════════════════════════════════════════════════════════════

class ToolExecutionError(Exception):
    """工具执行业务错误。子类 `_execute` 遇到无效参数 / 前置校验失败 /
    业务约束不满足时抛出。基类 run() 捕获后包成 `<tool_use_error>{msg}</tool_use_error>`
    + `is_error=True`（对齐 Anthropic tool_result 协议）。

    普通 `Exception` 也会被同样处理，但这个类更明确语义。

    来源: 参考项目/claude-code-analysis/src/tools/FileReadTool/FileReadTool.ts
    （工具 call() 用 `throw new Error(message)` 表达业务错误，不 return 字符串）
    """
    pass


def _resolve_path_from_ctx(path: str | Path, ctx: ToolContext) -> Path:
    """Resolve relative tool paths against the Agent workspace, not process cwd."""

    raw = Path(path).expanduser()
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        base = Path(getattr(ctx, "cwd", "") or os.getcwd()).expanduser().resolve()
        resolved = (base / raw).resolve()

    # General-purpose legacy agents omit allowed_read_roots and preserve their
    # established behavior. Runtime-profile agents always declare it so Pi and
    # AgentNodeLoop share the same fail-closed read boundary.
    allowed_read_roots = getattr(ctx, "allowed_read_roots", None) or ()
    if allowed_read_roots:
        allowed = False
        for item in allowed_read_roots:
            try:
                resolved.relative_to(Path(item).expanduser().resolve())
                allowed = True
                break
            except ValueError:
                continue
        if not allowed:
            raise ToolExecutionError(
                f"path REFUSED: {resolved} is outside allowed_read_roots"
            )
    return resolved


# ═══════════════════════════════════════════════════════════════════════
# SingleToolRouter 基类
# ═══════════════════════════════════════════════════════════════════════

class SingleToolRouter(Router):
    """一个具体工具的 Router 基类。

    FORMAT_IN  = agent.tool-request（通用，实际 tool_name 字段标识具体工具）
    FORMAT_OUT = agent.tool-response

    子类只需声明 TOOL_NAME / DESCRIPTION / INPUT_SCHEMA，实现 _execute()。
    """

    # ── 子类必须声明 ──
    TOOL_NAME: ClassVar[str] = ""
    DESCRIPTION: ClassVar[str] = ""
    INPUT_SCHEMA: ClassVar[dict] = {}
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False
    ERROR_RESULT_STYLE: ClassVar[str] = "tool_use_error_xml"

    # ── 元 IO 声明 (用户原始需求 6.6, 2026-05-02 加) ──
    # 规范: docs/standards/cli/meta_io.md
    # 子类**应当**声明本 tool 消费 / 产出哪些元 IO. 守护扫描 + G4 锁的
    # watched_meta_io 规则消费这两个字段.
    # ("*",) 表示"任意外部 IO" (例如 bash 通用工具), 视为弱声明.
    # 空 tuple 表示"不动外部" (例如 finish 工具).
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    # ── Router 元数据 ──
    FORMAT_IN: ClassVar[str] = "agent.tool-request"
    FORMAT_OUT: ClassVar[str] = "agent.tool-response"
    INPUT_KEYS: ClassVar[list[str]] = ["tool_name", "tool_args", "tool_use_id"]
    OUTPUT_KEYS: ClassVar[list[str]] = ["tool_name", "tool_use_id", "result", "is_error"]

    def __init__(self, *, executor: ToolExecutor | None = None, bus: Any | None = None):
        if bus is None:
            raise RuntimeError(
                f"{type(self).__name__} requires an EventBus (bus=...). "
                f"Silent no-op emit loses tool audit trail."
            )
        self._executor = executor or ToolExecutor()
        self._bus = bus


    # ── LLM 侧元数据（由 ToolDispatchRouter 收集后喂 LLM） ─────────

    @classmethod
    def to_api_spec(cls) -> dict:
        """Anthropic tools 参数格式。"""
        return {
            "name": cls.TOOL_NAME,
            "description": cls.DESCRIPTION,
            "input_schema": cls.INPUT_SCHEMA,
        }

    @property
    def router_name(self) -> str:
        return f"tool_{self.TOOL_NAME}"

    # ── 子类实现 ──────────────────────────────────────────────────

    @abstractmethod
    def _execute(self, args: dict, ctx: ToolContext) -> str:
        """执行工具，返回结果文本。子类必须实现。

        **错误信号协议（对齐 Claude Code）**：
        - 成功 → return 结果字符串
        - 失败 → **raise ToolExecutionError (或其他 Exception)**
          不要 return "[ERROR] ..." 字符串，Anthropic 协议层无法识别（is_error=False）

        基类 run() 捕获异常后：
        - 把异常 message 用 `<tool_use_error>...</tool_use_error>` XML 包裹
          （CC 约定：让 LLM 对错误有明确视觉/结构信号）
        - 设置 Verdict.output.is_error = True
          （Anthropic 协议层：tool_result block 的 is_error: true 字段让 LLM 知道这是失败）
        """
        ...

    # ── Router 入口 ────────────────────────────────────────────────

    async def run(self, input_data: Any) -> Verdict:
        pre = self.validate_input(input_data)
        if pre is not None:
            return pre

        trace_id = input_data.get("trace_id", "")
        tool_name = input_data.get("tool_name", self.TOOL_NAME)
        tool_args = input_data.get("tool_args", {}) or {}
        tool_use_id = input_data.get("tool_use_id", "")
        turn = input_data.get("turn", 0)
        ctx_data = input_data.get("context", {}) or {}
        ctx = self._build_ctx(ctx_data)
        # Give synchronous tool implementations enough run identity to emit
        # progress materials while they execute in ``asyncio.to_thread``.
        # These are observability fields, not additional authority.
        ctx.trace_id = trace_id  # type: ignore[attr-defined]
        ctx.tool_use_id = tool_use_id  # type: ignore[attr-defined]
        ctx.turn = turn  # type: ignore[attr-defined]

        # 名字校验（dispatch 应该已经分发对了，但防御一下）
        if tool_name != self.TOOL_NAME:
            diagnosis = f"{type(self).__name__} mismatched tool_name: expected {self.TOOL_NAME}, got {tool_name}"
            logger.warning(diagnosis)

        await emit_router_input(
            self._bus,
            trace_id=trace_id,
            router_name=self.router_name,
            format_id=self.FORMAT_IN,
            data={
                "tool_name": tool_name,
                "tool_use_id": tool_use_id,
                "turn": turn,
                "args": tool_args,
            },
        )

        t0 = time.time()
        is_error = False
        try:
            result = await asyncio.to_thread(self._execute, tool_args, ctx)
            if not isinstance(result, str):
                result = str(result)
        except Exception as exc:
            # CC 协议：<tool_use_error>...</tool_use_error> XML 包裹 + is_error=True
            # 来源: 参考项目/claude-code-analysis/src/services/tools/toolExecution.ts L480-482
            is_error = True
            err_msg = str(exc)
            result = (
                err_msg
                if self.ERROR_RESULT_STYLE == "plain"
                else f"<tool_use_error>{err_msg}</tool_use_error>"
            )
            logger.warning("[SingleToolRouter] %s failed: %s", self.TOOL_NAME, err_msg)
        duration_ms = (time.time() - t0) * 1000

        output = {
            "tool_name": self.TOOL_NAME,
            "tool_use_id": tool_use_id,
            "result": result,
            "is_error": is_error,
            "duration_ms": duration_ms,
            "turn": turn,
        }
        verdict = Verdict(kind=VerdictKind.PASS, output=output)

        await emit_router_output(
            self._bus,
            trace_id=trace_id,
            router_name=self.router_name,
            format_id=self.FORMAT_OUT,
            data={
                "tool_name": self.TOOL_NAME,
                "tool_use_id": tool_use_id,
                "is_error": is_error,
                "duration_ms": duration_ms,
                "result_preview": result[:2000],  # 截断展示用，原文已在 Verdict.output
                "turn": turn,
            },
            verdict_kind=verdict.kind.value,
        )
        return verdict

    # ── 工具上下文转换 ────────────────────────────────────────────

    def _build_ctx(self, ctx_data: dict) -> ToolContext:
        """从 Format 的 context dict 构造 ToolContext。

        未声明的字段（如业务子类注入的 prefab_name）透传到 `__dict__`，
        子类 `_execute` 可通过 `getattr(ctx, 'prefab_name', '')` 读取。
        """
        ctx = ToolContext(
            cwd=ctx_data.get("cwd", os.getcwd()),
            project_root=ctx_data.get("project_root", os.getcwd()),
            permission_mode=ctx_data.get("permission_mode", "default"),
            turn_number=ctx_data.get("turn_number", 0),
            trace_id=ctx_data.get("trace_id", ""),
            node_id=ctx_data.get("node_id", ""),
            origin=ctx_data.get("origin", "claude-code"),
            agent_name=ctx_data.get("agent_name", ""),
            domain=ctx_data.get("domain", ""),
        )
        import dataclasses
        declared = {f.name for f in dataclasses.fields(ToolContext)}
        for k, v in ctx_data.items():
            if k not in declared:
                setattr(ctx, k, v)
        return ctx


# ═══════════════════════════════════════════════════════════════════════
# Glob
# ═══════════════════════════════════════════════════════════════════════

class GlobRouter(SingleToolRouter):
    # Description 1:1 复刻 CC: 参考项目/claude-code-analysis/src/tools/GlobTool/prompt.ts
    # Wave 5 续 (2026-05-05): Agent 工具 spawn 已通 (Wave 3), 把最后一行的"multi-step exploration"
    # (Agent 未通时占位) 改回 cc 原文 "use the Agent tool instead".
    TOOL_NAME: ClassVar[str] = "glob"
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.list_directory",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()
    DESCRIPTION: ClassVar[str] = (
        "- Fast file pattern matching tool that works with any codebase size\n"
        "- Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\"\n"
        "- Returns matching file paths sorted by modification time\n"
        "- Use this tool when you need to find files by name patterns\n"
        "- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead"
    )
    # Schema 1:1 复刻 CC: 参考项目/claude-code-analysis/src/tools/GlobTool/GlobTool.ts L27-36
    # (CC 不含 head_limit；我保留作为本项目扩展 —— 大仓库下 agent 需要截断)
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior. Must be a valid directory path if provided.",
            },
            "head_limit": {
                "type": "integer",
                "minimum": 0,
                "description": "Results cap; defaults to 100. Pass 0 for unlimited (use sparingly — large result sets waste context).",
            },
        },
        "required": ["pattern"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        args = dict(args)
        args["path"] = str(_resolve_path_from_ctx(args.get("path") or ".", ctx))
        return self._executor.execute("glob", args)


# ═══════════════════════════════════════════════════════════════════════
# Grep
# ═══════════════════════════════════════════════════════════════════════

class GrepRouter(SingleToolRouter):
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = (
        "meta_io.fs.list_directory", "meta_io.fs.read_file_text",
    )
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    # Description 1:1 复刻 CC: 参考项目/claude-code-analysis/src/tools/GrepTool/prompt.ts
    # Wave 5 续 (2026-05-05): "Agent tool (or multi-step exploration)" 简化回 cc 原文
    # "Agent tool" — Agent spawn 已通 (Wave 3), 不再需要 multi-step 占位.
    # 注: 工具名 "grep" / "bash" 小写是 omnicompany 历史 (cc 是 "Grep" / "Bash"),
    # 改大小写会破历史调用方, 不在 prompt 复刻范围 — 保持小写
    TOOL_NAME: ClassVar[str] = "grep"
    DESCRIPTION: ClassVar[str] = (
        "A powerful search tool built on ripgrep\n\n"
        "  Usage:\n"
        "  - ALWAYS use grep for search tasks. NEVER invoke `grep` or `rg` as a bash command. "
        "The grep tool has been optimized for correct permissions and access.\n"
        "  - Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n"
        "  - Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") or type parameter (e.g., \"js\", \"py\", \"rust\")\n"
        "  - Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only file paths (default), \"count\" shows match counts\n"
        "  - Use Agent tool for open-ended searches requiring multiple rounds\n"
        "  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)\n"
        "  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`\n"
    )
    # Schema 1:1 复刻 CC: 参考项目/claude-code-analysis/src/tools/GrepTool/GrepTool.ts L33-89
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for in file contents",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in (rg PATH). Defaults to current working directory.",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\") - maps to rg --glob",
            },
            "type": {
                "type": "string",
                "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types.",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode: \"content\" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), \"files_with_matches\" shows file paths (supports head_limit), \"count\" shows match counts (supports head_limit). Defaults to \"files_with_matches\".",
            },
            "-A": {
                "type": "number",
                "description": "Number of lines to show after each match (rg -A). Requires output_mode: \"content\", ignored otherwise.",
            },
            "-B": {
                "type": "number",
                "description": "Number of lines to show before each match (rg -B). Requires output_mode: \"content\", ignored otherwise.",
            },
            "-C": {
                "type": "number",
                "description": "Alias for context.",
            },
            "context": {
                "type": "number",
                "description": "Number of lines to show before and after each match (rg -C). Requires output_mode: \"content\", ignored otherwise.",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output (rg -n). Requires output_mode: \"content\", ignored otherwise. Defaults to true.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search (rg -i)",
            },
            "head_limit": {
                "type": "integer",
                "minimum": 0,
                "description": "Limit output to first N lines/entries, equivalent to \"| head -N\". Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count (limits count entries). Defaults to 250 when unspecified. Pass 0 for unlimited (use sparingly — large result sets waste context).",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Skip first N lines/entries before applying head_limit, equivalent to \"| tail -n +N | head -N\". Works across all output modes. Defaults to 0.",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false.",
            },
        },
        "required": ["pattern"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        # 向下兼容：legacy case_insensitive → -i, legacy include → glob
        args = dict(args)
        if "case_insensitive" in args and "-i" not in args:
            args["-i"] = args.pop("case_insensitive")
        if "include" in args and "glob" not in args:
            args["glob"] = args.pop("include")
        args["path"] = str(_resolve_path_from_ctx(args.get("path") or ".", ctx))
        return self._executor.execute("grep", args)


# ═══════════════════════════════════════════════════════════════════════
# ReadFile
# ═══════════════════════════════════════════════════════════════════════

_FILE_UNCHANGED_STUB = (
    "[FILE_UNCHANGED] {path}\n"
    "(file already read this session at {last_read_iso}, mtime/size match cached state, "
    "contents unchanged. Skipping re-read to save tokens. To force re-read, modify the file "
    "or read with explicit offset/limit.)"
)


def _file_quick_signature(path_obj: Path) -> tuple[float, int] | None:
    """快速文件签名 — (mtime, size). 不读 bytes, 极快. None = stat 失败 (缺失/无权限)."""
    try:
        st = path_obj.stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


# CC FileReadTool BLOCKED_DEVICE_PATHS 对齐 — 读这些文件会无限 hang / 产 GB 数据
# Linux/Mac: /dev/* 大多无限 / /proc /sys 多数无限
# Windows: CON/NUL/PRN/AUX/COM1-9/LPT1-9 是 DOS device alias
_BLOCKED_DEVICE_PATTERNS = (
    "/dev/zero", "/dev/null", "/dev/urandom", "/dev/random",
    "/dev/full", "/dev/tty", "/dev/console",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/proc/kcore", "/proc/kmem", "/proc/kallsyms",  # 大或敏感
    # 注: Windows DOS device (CON/NUL/PRN/AUX) 不放这里 — substring match 会
    # 在 "configured_feature" / "console_log" 等正常路径里 false positive.
    # 改由下面 _WIN_DOS_DEVICE_RE 走 word boundary 处理.
)
_WIN_DOS_DEVICE_RE = re.compile(
    r"(?:^|[\\/])(con|nul|prn|aux|com[1-9]|lpt[1-9])(?:\.|$|[\\/])",
    re.IGNORECASE,
)


def _is_blocked_device(path: str) -> str | None:
    """检测 path 是否为 device file. 命中返 reason, 否则 None."""
    p_lower = path.replace("\\", "/").lower()
    for blocked in _BLOCKED_DEVICE_PATTERNS:
        if blocked.lower() in p_lower:
            return f"device file ({blocked}) — blocked to prevent infinite read"
    if _WIN_DOS_DEVICE_RE.search(path):
        return "Windows DOS device name (CON/NUL/PRN/AUX/COMx/LPTx) — blocked"
    # /proc/<pid> 类大多 OK, 但 /proc/<pid>/mem 是无限 + 危险
    if "/proc/" in p_lower and ("/mem" in p_lower or "/maps" in p_lower):
        return "/proc/<pid>/mem-style file — blocked (infinite/sensitive)"
    return None


def _read_text_with_encoding_fallback(path_obj: Path) -> tuple[str, str]:
    """读文件 + 自动检测编码. 返 (text, encoding_used).

    顺序: utf-8 strict → gbk → latin-1 (always succeeds 因为单字节映射全)
    没 chardet 依赖, 启发式覆盖中文/日文/西欧 99%.
    """
    raw = path_obj.read_bytes()
    # BOM 检测
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    # utf-8 strict
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # gbk (中文 Windows 默认)
    try:
        return raw.decode("gbk"), "gbk"
    except UnicodeDecodeError:
        pass
    # latin-1 (单字节, 万能 fallback — 但中文会乱)
    return raw.decode("latin-1"), "latin-1"


def _read_notebook_ipynb(path_obj: Path) -> str:
    """渲染 Jupyter notebook (.ipynb) 为 cat -n 风格文本.

    cells: code | markdown | raw, 各自标 cell type + index. code cell 含 outputs.
    跟 CC FileReadTool ipynb 处理对齐.
    """
    import json as _json
    raw = path_obj.read_text(encoding="utf-8", errors="replace")
    try:
        nb = _json.loads(raw)
    except _json.JSONDecodeError as e:
        return f"[ipynb parse error] {e}\n\nRaw bytes preview:\n{raw[:500]}"
    cells = nb.get("cells", [])
    parts = [f"[Jupyter notebook · {len(cells)} cells]\n"]
    for i, cell in enumerate(cells, 1):
        ct = cell.get("cell_type", "?")
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        parts.append(f"\n=== Cell {i} ({ct}) ===")
        parts.append(src.rstrip("\n"))
        if ct == "code":
            outs = cell.get("outputs", [])
            for j, out in enumerate(outs):
                ot = out.get("output_type", "?")
                if ot == "stream":
                    text = out.get("text", "")
                    if isinstance(text, list):
                        text = "".join(text)
                    parts.append(f"--- output[{j}] (stream/{out.get('name', 'stdout')}) ---")
                    parts.append(text.rstrip("\n")[:2000])
                elif ot in ("display_data", "execute_result"):
                    data = out.get("data", {})
                    if "text/plain" in data:
                        text = data["text/plain"]
                        if isinstance(text, list):
                            text = "".join(text)
                        parts.append(f"--- output[{j}] ({ot}/text/plain) ---")
                        parts.append(text.rstrip("\n")[:2000])
                    elif "image/png" in data:
                        parts.append(f"--- output[{j}] ({ot}/image/png, base64 omitted) ---")
                elif ot == "error":
                    ename = out.get("ename", "?"); evalue = out.get("evalue", "")
                    parts.append(f"--- output[{j}] (error) {ename}: {evalue} ---")
    return "\n".join(parts)


def _read_pdf(path_obj: Path) -> str:
    """读 PDF, 抽 text per page. 需要 pypdf / PyPDF2 / fitz 任一. 没 dep 提示用户装.

    跟 CC FileReadTool PDF 对齐 (CC 用 pdf-parse npm pkg).
    """
    # 优先级: pypdf > PyPDF2 > fitz (pymupdf)
    text_per_page = []
    backend = None
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path_obj))
        for i, page in enumerate(reader.pages, 1):
            try:
                text_per_page.append((i, page.extract_text() or ""))
            except Exception as e:
                text_per_page.append((i, f"[page {i} extract error: {e}]"))
        backend = "pypdf"
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
            reader = PdfReader(str(path_obj))
            for i, page in enumerate(reader.pages, 1):
                text_per_page.append((i, page.extract_text() or ""))
            backend = "PyPDF2"
        except ImportError:
            try:
                import fitz  # type: ignore
                doc = fitz.open(str(path_obj))
                for i, page in enumerate(doc, 1):
                    text_per_page.append((i, page.get_text() or ""))
                doc.close()
                backend = "PyMuPDF/fitz"
            except ImportError:
                return (
                    "[PDF read SKIPPED] no PDF backend installed.\n"
                    "Install one of: pypdf / PyPDF2 / pymupdf (e.g. `pip install pypdf`).\n"
                    "If you can't install, ask the user to extract text some other way."
                )
    parts = [f"[PDF · {len(text_per_page)} pages · backend={backend}]\n"]
    for page_no, txt in text_per_page:
        parts.append(f"\n=== Page {page_no} ===")
        parts.append(txt.rstrip("\n")[:3000])  # cap 3K per page
    return "\n".join(parts)


class ReadFileRouter(SingleToolRouter):
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file_text",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    # Description 1:1 复刻 CC: 参考项目/claude-code-analysis/src/tools/FileReadTool/prompt.ts renderPromptTemplate
    TOOL_NAME: ClassVar[str] = "read_file"
    DESCRIPTION: ClassVar[str] = (
        "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
        "Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. "
        "It is okay to read a file that does not exist; an error will be returned.\n\n"
        "Usage:\n"
        "- The file_path parameter must be an absolute path, not a relative path\n"
        "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
        "- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters\n"
        "- Results are returned using cat -n format, with line numbers starting at 1\n"
        "- This tool can only read files, not directories. To read a directory, use list_dir.\n"
        "- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.\n"
        "- Repeated full-file reads return [FILE_UNCHANGED] stub if file (mtime, size) unchanged since last read in this agent session — token-saver."
    )
    # Schema: 对齐 CC 的 `file_path` 为 primary；同时接受 `path` 作为向下兼容 alias
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from. Only provide if the file is too large to read at once",
                "minimum": 0,
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read. Only provide if the file is too large to read at once.",
                "exclusiveMinimum": 0,
            },
        },
        "required": ["file_path"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def __init__(self, *, bus: Any | None = None, **kw: Any):
        super().__init__(bus=bus, **kw)
        # File state cache: 全 read 路径 → (mtime, size, last_read_iso)
        # Per-Router-instance (= per AgentNodeLoop session, CC readFileState 等价).
        # Partial read (offset/limit) 不进 cache, 不查 cache (语义不同).
        self._file_state: dict[str, dict[str, Any]] = {}

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        # CC 用 file_path；接受 path 作为 legacy alias
        raw_path = args.get("file_path") or args.get("path", "")
        if not raw_path:
            raise ToolExecutionError("file_path is required (absolute path to a file)")
        path = str(_resolve_path_from_ctx(raw_path, ctx))

        # Optional exact-file allowlist for narrow correction agents.  General
        # agents omit this field and retain the normal READ_ANY behavior.
        allowed_read_files = getattr(ctx, "allowed_read_files", None) or ()
        if allowed_read_files:
            target = Path(path)
            allowed = {Path(item).expanduser().resolve() for item in allowed_read_files}
            if target not in allowed:
                listing = "\n  - ".join(str(item) for item in sorted(allowed))
                raise ToolExecutionError(
                    f"read_file REFUSED: {target} is outside allowed_read_files.\n"
                    f"Allowed files:\n  - {listing}"
                )
        offset = args.get("offset", 0)
        limit = args.get("limit", 2000)
        is_full_read = offset == 0 and limit >= 2000

        # CC FileReadTool BLOCKED_DEVICE_PATHS 对齐 (2026-05-04): 防 LLM 误读
        # /dev/zero 类 device file 导致 hang / 产 GB 数据.
        blocked_reason = _is_blocked_device(path)
        if blocked_reason:
            raise ToolExecutionError(
                f"read_file REFUSED: {path} → {blocked_reason}. "
                f"Don't read device files; pick a regular file."
            )

        # 多格式分发 (CC FileReadTool 对齐): .ipynb / .pdf 走专用解析
        # (image .png/.jpg 暂不接 — 需要改 SingleToolRouter 返回值支持 image content block)
        path_lower = path.lower()
        if path_lower.endswith(".ipynb"):
            try:
                p_obj = Path(path)
                if p_obj.is_file():
                    return _read_notebook_ipynb(p_obj)
            except Exception as e:
                raise ToolExecutionError(f"ipynb read failed: {e}")
        if path_lower.endswith(".pdf"):
            try:
                p_obj = Path(path)
                if p_obj.is_file():
                    return _read_pdf(p_obj)
            except Exception as e:
                raise ToolExecutionError(f"pdf read failed: {e}")

        # 全文读 + cache hit → 返 STUB 省 token (CC FileReadTool readFileState 等价)
        if is_full_read:
            try:
                path_obj = Path(path)
                sig = _file_quick_signature(path_obj)
                cached = self._file_state.get(str(path_obj.resolve()) if sig else path)
                if sig and cached and cached.get("sig") == sig:
                    return _FILE_UNCHANGED_STUB.format(
                        path=path,
                        last_read_iso=cached.get("last_read_iso", "unknown"),
                    )
            except Exception:
                pass  # cache 失败永远不阻塞真读

        # Encoding 自检 (CC FileReadTool charset detection 对齐, 2026-05-04):
        # 文件非 utf-8 → 用 _read_text_with_encoding_fallback 自己读 (gbk / latin-1 etc),
        # 用 cat -n 风格格式化, 绕开 executor 的 utf-8 hardcode.
        try:
            path_obj = Path(path)
            if path_obj.is_file():
                # 探一下前 4KB 看 utf-8 是否能解
                head_bytes = path_obj.read_bytes()[:4096]
                try:
                    head_bytes.decode("utf-8")
                    needs_fallback = False
                except UnicodeDecodeError:
                    needs_fallback = True
                if needs_fallback:
                    text, encoding_used = _read_text_with_encoding_fallback(path_obj)
                    lines = text.splitlines()
                    start = offset if offset > 0 else 0
                    end = min(start + (limit if limit < 2000 else 2000), len(lines))
                    numbered = "\n".join(
                        f"{i + 1:6d}\t{line}" for i, line in enumerate(lines[start:end], start=start)
                    )
                    return (
                        f"[encoding={encoding_used}, total_lines={len(lines)}, "
                        f"showing {start + 1}-{end}]\n{numbered}"
                    )
        except Exception:
            pass  # 任何探测失败回 executor 默认行为

        editor_args: dict[str, Any] = {"command": "view", "path": path}
        if offset > 0 or limit < 2000:
            editor_args["view_range"] = [offset + 1, offset + limit]
        result = self._executor.execute("str_replace_editor", editor_args)

        # 更新 cache (仅全文读 + 成功)
        if is_full_read:
            try:
                path_obj = Path(path)
                sig = _file_quick_signature(path_obj)
                if sig:
                    from datetime import datetime, timezone
                    self._file_state[str(path_obj.resolve())] = {
                        "sig": sig,
                        "last_read_iso": datetime.now(timezone.utc).isoformat(),
                    }
            except Exception:
                pass
        return result


# ═══════════════════════════════════════════════════════════════════════
# Edit (CC FileEditTool 对齐, 2026-05-04)
# ═══════════════════════════════════════════════════════════════════════


class EditRouter(SingleToolRouter):
    """Edit a file by exact-string replacement (CC FileEditTool 对齐).

    Why: WriteFileRouter 总写整文件, 改 1 行也得读全 + 写全 (token 浪费 + 风险大).
    Edit 只传 (old_string, new_string), agent 改 1 行只发 ~50 字节 patch.

    防误用:
    - old_string MUST 唯一存在文件中 (除非 replace_all=True), 否则不知改哪一处
    - new_string MUST 跟 old_string 不同, 否则 noop
    - 必须先用 read_file 确认文件存在 + 内容匹配预期 (基本素养)

    白名单: 跟 WriteFileRouter 同, 走 ctx.allowed_write_paths / allowed_write_roots.
    """

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.overwrite_file",)

    TOOL_NAME: ClassVar[str] = "edit"
    DESCRIPTION: ClassVar[str] = (
        "Performs exact string replacements in files.\n\n"
        "Usage:\n"
        "- You must use your `read_file` tool at least once on the file before editing it (this also primes our file_state cache so we know mtime/size).\n"
        "- When editing text from read_file output, ensure you preserve exact indentation (tabs/spaces) as shown.\n"
        "- ALWAYS prefer editing existing files; never create new ones unless required (use write_file for new files).\n"
        "- The edit FAILS if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique, or use `replace_all` to change every instance.\n"
        "- Use `replace_all` for renaming a variable across the file.\n"
        "- file_path must be inside ctx.allowed_write_paths or allowed_write_roots."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify.",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace. Must be unique in the file unless replace_all=true.",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with. Must differ from old_string.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false).",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = (args.get("file_path") or args.get("path") or "").strip()
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        replace_all = bool(args.get("replace_all", False))

        if not raw_path:
            raise ToolExecutionError("file_path is required (absolute path to a file)")
        path = str(_resolve_path_from_ctx(raw_path, ctx))
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolExecutionError("old_string and new_string must be strings")
        if old == new:
            raise ToolExecutionError("new_string must differ from old_string (got identical strings — noop)")
        if not old:
            raise ToolExecutionError("old_string cannot be empty (use write_file for new files)")

        # 写白名单 (沿用 WriteFileRouter 的两层模式)
        allowed_paths = getattr(ctx, "allowed_write_paths", None) or ()
        allowed_roots = getattr(ctx, "allowed_write_roots", None) or ()
        if not allowed_paths and not allowed_roots:
            raise ToolExecutionError(
                "edit REFUSED: ctx 缺 allowed_write_paths/allowed_write_roots. "
                "Worker.build_tool_context() 必须声明可写路径白名单."
            )
        try:
            target = Path(path).resolve()
        except Exception as e:
            raise ToolExecutionError(f"can't resolve file_path {path!r}: {e}")
        # 精确路径 OK
        ok = False
        for ap in allowed_paths:
            try:
                if target == Path(ap).resolve():
                    ok = True
                    break
            except Exception:
                continue
        # 树根 OK
        if not ok:
            for ar in allowed_roots:
                try:
                    target.relative_to(Path(ar).resolve())
                    ok = True
                    break
                except (ValueError, Exception):
                    continue
        if not ok:
            raise ToolExecutionError(
                f"edit REFUSED: {target} 不在 allowed_write_paths/allowed_write_roots 内.\n"
                f"allowed_paths: {list(allowed_paths)[:3]}\n"
                f"allowed_roots: {list(allowed_roots)[:3]}"
            )

        if not target.is_file():
            raise ToolExecutionError(
                f"edit REFUSED: {target} 不存在或不是文件. "
                f"先 write_file 创建, 或用 read_file 确认路径正确."
            )

        # 读 + 替换
        try:
            original = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise ToolExecutionError(f"read failed: {e}")

        count = original.count(old)
        if count == 0:
            raise ToolExecutionError(
                f"edit FAILED: old_string not found in {target.name}. "
                f"先 read_file 看现存内容确认 old_string 准确 (注意缩进/换行/Unicode 全角)."
            )
        if count > 1 and not replace_all:
            raise ToolExecutionError(
                f"edit FAILED: old_string matched {count} 处 in {target.name}. "
                f"传更长 old_string 包含上下文使唯一, 或用 replace_all=true 全部替换."
            )

        new_content = original.replace(old, new) if replace_all else original.replace(old, new, 1)
        try:
            target.write_text(new_content, encoding="utf-8")
        except Exception as e:
            raise ToolExecutionError(f"write failed: {e}")

        bytes_diff = len(new_content.encode("utf-8")) - len(original.encode("utf-8"))
        return (
            f"Edited {target} ({count} replacement{'s' if count > 1 else ''}, "
            f"{bytes_diff:+d} bytes net change). "
            f"file_state cache invalidated by mtime change — next read_file 会拿新内容."
        )


# ═══════════════════════════════════════════════════════════════════════
# ListDir
# ═══════════════════════════════════════════════════════════════════════

class ListDirRouter(SingleToolRouter):
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.list_directory",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "list_dir"
    DESCRIPTION: ClassVar[str] = (
        "List directory contents (files and subdirectories). "
        "Returns sorted listing with file sizes."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The directory path to list"},
        },
        "required": ["path"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        path = str(_resolve_path_from_ctx(args.get("path") or ".", ctx))
        target = Path(path)
        if not target.is_dir():
            return f"Error: '{path}' is not a directory."
        try:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            lines = [f"Contents of {path}:"]
            for entry in entries[:200]:
                kind = "dir" if entry.is_dir() else "file"
                try:
                    size = entry.stat().st_size if entry.is_file() else 0
                    lines.append(f"  [{kind:4}] {entry.name}" + (f"  ({size:,}B)" if size else ""))
                except OSError:
                    lines.append(f"  [{kind:4}] {entry.name}")
            if len(entries) > 200:
                lines.append(f"  ... ({len(entries) - 200} more entries)")
            return "\n".join(lines)
        except PermissionError:
            return f"Error: Permission denied for '{path}'."
        except Exception as e:
            return f"Error: {e}"


# ═══════════════════════════════════════════════════════════════════════
# Finish (终止信号)
# ═══════════════════════════════════════════════════════════════════════

class FinishRouter(SingleToolRouter):
    # finish 是 agent 内部信号工具, 不动外部 IO
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    """终止信号工具。LLM 调用它即触发 AgentNodeLoop 退出循环。

    实际 loop 退出逻辑在 AgentNodeLoop 主循环里识别 `tool_name == 'finish'`
    后直接走 ExtractResult，本 Router 只负责回显 result。
    """

    TOOL_NAME: ClassVar[str] = "finish"
    DESCRIPTION: ClassVar[str] = (
        "Complete the task and output the final result. "
        "Calling this tool terminates the agent loop."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "result": {"type": "string", "description": "The final result (JSON or plain text)"},
        },
        "required": ["result"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        return args.get("result", "done")
