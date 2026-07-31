# [OMNI] origin=claude-code domain=services/workflow_factory ts=2026-04-19
# [OMNI] material_id="material:core.team_builder.code_gen_loop_agent_node.routers_legacy.py"
"""code_gen_loop — 取代 4 个固定顺序 CodeGen*Router 的 AgentNodeLoop 版本。

**设计动机**（2026-04-19 smoke 实证驱动）：
旧 CodeGenRoutersRouter 单次 LLMClient.call(max_tokens=16384) 导致 routers.py
常被在 line 77 截断（`re.sub(r"^` 漏闭合），下游 syntax_fixer 也救不回来。
根本原因是"单次输出堵 token 预算"——这正是 agent-loop 该解决的场景。

**核心机制**：
- LLM 按文件**逐个**生成，每写一个立刻 py_compile 自检
- 截断/语法错时自己 read 回来 + 针对性修，不依赖下游 fixer
- 4 个文件全部 compile 过才 finish
- 继承 packages.services.agent.AgentNodeLoop 薄调度器

**FORMAT_IN**: wf.node_plan_augmented（含 framework_context + node_plan + format_chain）
**FORMAT_OUT**: wf.project_skeleton（files dict + pipeline_name + package_path + reports）

**替代**：
- pipeline.py 的 4 个节点 code_gen_formats / pipeline / routers / run → 本 1 节点
"""

from __future__ import annotations

import json
import logging
import py_compile as _py_compile
import re
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import LoopConfig
from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.packages.services._core.agent import (
    AgentNodeLoop,
    PromptBuilderRouter,
    ExtractResultRouter,
    SingleToolRouter,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)

_VALID_FILENAMES = ("formats.py", "pipeline.py", "routers.py", "run.py")


# ═════════════════════════════════════════════════════════════════════
# 工具 Router（对 code_gen_state 里的 files dict 做读写 + 自验）
# 共享状态通过 ctx.code_gen_state （由 CodeGenLoop.build_tool_context 注入）
# ═════════════════════════════════════════════════════════════════════


def _state_files(ctx: ToolContext) -> dict[str, str]:
    """从 ToolContext 取出共享 files dict（必存在，由 Loop 注入）。"""
    state = getattr(ctx, "code_gen_state", None)
    if state is None or not isinstance(state, dict):
        raise ToolExecutionError(
            "内部状态错误：ctx.code_gen_state 未注入。这是 CodeGenLoop 的 bug。"
        )
    files = state.setdefault("files", {})
    return files


# ── 100% 契约（registry 靠字符串 getattr 查这些函数名） ──────────
# 违反了导致下游 l2_import 直接 FAIL。见 framework_context 里的
# `pipeline_registry_lazy_loader_src` / `format_registry_dispatch_src` 真源码。
_REQUIRED_STRUCTURE: dict[str, tuple[re.Pattern, ...]] = {
    "pipeline.py": (
        re.compile(r"def\s+build_pipeline\s*\("),
        re.compile(r"PipelineSpec\s*\("),
    ),
    "run.py": (re.compile(r"def\s+build_bindings\s*\("),),
    "formats.py": (re.compile(r"def\s+register_formats\s*\("),),
}


class WriteFileRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "write_file"
    DESCRIPTION: ClassVar[str] = (
        "写入一个代码文件（会覆盖已写内容）。filename 必须是 "
        "formats.py / pipeline.py / routers.py / run.py 之一。"
        "content 是该文件的**完整** Python 代码。"
        "写完后建议立即调 py_compile 自检。"
        "注意：pipeline.py / run.py / formats.py 有 registry 契约结构要求（write 时会校验），"
        "初始骨架里已给出要保留的函数签名。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "enum": list(_VALID_FILENAMES)},
            "content": {"type": "string", "description": "完整 Python 代码"},
        },
        "required": ["filename", "content"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        filename = args.get("filename", "").strip()
        content = args.get("content", "")
        if filename not in _VALID_FILENAMES:
            raise ToolExecutionError(
                f"filename 必须是 {_VALID_FILENAMES}，收到 {filename!r}"
            )
        if not content or len(content) < 20:
            raise ToolExecutionError(
                f"content 太短（{len(content)} chars），疑似被截断。"
                f"请检查你的 tool_call arguments JSON 是否完整。"
            )
        # 契约结构硬校验（100% 不可违反的那部分）
        required = _REQUIRED_STRUCTURE.get(filename, ())
        missing = [pat.pattern for pat in required if not pat.search(content)]
        if missing:
            raise ToolExecutionError(
                f"{filename} 缺少 registry 契约结构（这些是 100% 强制要求，"
                f"不是风格建议；见 framework_context 的消费者真源码）：\n"
                f"  缺失 pattern: {missing}\n\n"
                f"**不能删掉这些函数包装**。请保留函数签名，"
                f"在函数体内填你的实现逻辑。"
                f"可先 read_written_file({filename!r}) 看初始骨架结构。"
            )
        files = _state_files(ctx)
        prev = files.get(filename, "")
        files[filename] = content
        action = "overwrite" if prev else "create"
        return (
            f"[OK] {action} {filename}: {len(content)} chars, {content.count(chr(10)) + 1} lines.\n"
            f"建议下一步：py_compile(filename={filename!r})"
        )


class PyCompileRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "py_compile"
    DESCRIPTION: ClassVar[str] = (
        "对已写入的一个文件跑 py_compile（只检查 Python 语法，不做 import）。"
        "filename 为 None 或空字符串时检查全部已写文件。"
        "语法错误返回详细位置 + 错误信息，供你针对性修复。"
        "**纪律：每写一个文件立刻 py_compile 一次，不要等全写完才检**。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "文件名（formats.py / pipeline.py / routers.py / run.py），空则检查全部",
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        files = _state_files(ctx)
        filename = (args.get("filename") or "").strip()
        if filename and filename not in _VALID_FILENAMES:
            raise ToolExecutionError(
                f"filename 必须是 {_VALID_FILENAMES} 或空字符串，收到 {filename!r}"
            )
        if not files:
            raise ToolExecutionError("还没写入任何文件。请先 write_file。")
        targets = [filename] if filename else [f for f in _VALID_FILENAMES if f in files]
        if not targets:
            raise ToolExecutionError(f"文件 {filename!r} 还没写入。请先 write_file。")

        errors: list[str] = []
        passed: list[str] = []
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # 全部先落盘（跨文件 import 不做，只做 syntax）
            for fn in targets:
                if fn not in files:
                    errors.append(f"{fn}: 未写入，跳过")
                    continue
                fp = td_path / fn
                fp.write_text(files[fn], encoding="utf-8")
                try:
                    _py_compile.compile(str(fp), doraise=True)
                    passed.append(fn)
                except _py_compile.PyCompileError as exc:
                    # 提取行号 + 错误行，LLM 根据这个精确修
                    msg = str(exc)
                    # 清洗临时路径前缀，用 filename 本身替换
                    msg = msg.replace(str(fp), fn)
                    errors.append(f"{fn}:\n{msg}")

        parts = []
        if passed:
            parts.append(f"[PASS] {len(passed)}/{len(targets)}: {', '.join(passed)}")
        if errors:
            parts.append("[FAIL] 以下文件有语法错，请 read_file 看实际内容后 write_file 覆盖修复：")
            parts.extend(errors)
            # 让 LLM 看到 is_error=True，但我们返回字符串，里面含具体错误
            # SingleToolRouter 默认会包成正常 tool_result，is_error 要靠抛异常
            # 但这里编译失败属于"工具本身成功执行，产出业务级错误"，应当当成正常结果返回
            # 让 LLM 读懂后自行决定下一步
        else:
            parts.append(f"全部 {len(passed)} 个文件 py_compile 通过。")
        return "\n\n".join(parts)


class ListFilesRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "list_files"
    DESCRIPTION: ClassVar[str] = (
        "列出当前已写入的文件 + 字符数 + 行数。用来确认进度。"
        "期望最终 4 个：formats.py / pipeline.py / routers.py / run.py。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {"type": "object", "properties": {}, "required": []}
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        files = _state_files(ctx)
        if not files:
            return "（空）还未写入任何文件"
        lines = []
        for fn in _VALID_FILENAMES:
            if fn in files:
                content = files[fn]
                lines.append(f"  {fn}: {len(content)} chars, {content.count(chr(10)) + 1} lines")
            else:
                lines.append(f"  {fn}: <未写入>")
        return f"已写入 {len(files)}/{len(_VALID_FILENAMES)} 文件：\n" + "\n".join(lines)


class ReadWrittenFileRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "read_written_file"
    DESCRIPTION: ClassVar[str] = (
        "读回已写入文件的内容（带行号）。用于 py_compile 报错后检查实际写了什么，"
        "以便针对性修复（例：看到 line 77 syntax error → read 确认原因 → write_file 修好）。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "enum": list(_VALID_FILENAMES)},
        },
        "required": ["filename"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        filename = (args.get("filename") or "").strip()
        if filename not in _VALID_FILENAMES:
            raise ToolExecutionError(f"filename 必须是 {_VALID_FILENAMES}")
        files = _state_files(ctx)
        if filename not in files:
            raise ToolExecutionError(f"{filename} 尚未写入")
        content = files[filename]
        body = "\n".join(f"{i+1:5d}\t{ln}" for i, ln in enumerate(content.splitlines()))
        return f"=== {filename} ({content.count(chr(10)) + 1} lines) ===\n{body}"


# ═════════════════════════════════════════════════════════════════════
# 自定义 PromptBuilderRouter：注入 framework_context + node_plan + format_chain
# ═════════════════════════════════════════════════════════════════════


_SYSTEM_PROMPT = """你是 OmniCompany LAP 工作流代码生成器（agent loop 版）。

## 任务
根据给定的 node_plan / format_chain / framework_context（框架真实源码），生成 4 个 Python 文件：formats.py / pipeline.py / routers.py / run.py

## 预置骨架（已经写了 3 个）

启动时 pipeline.py / run.py / formats.py 已预置**最小骨架**（不包含实现，只含 registry 契约结构）。
第一步**先 `list_files`** 看已有什么，再 **`read_written_file`** 读每个骨架，理解要保留什么结构。

骨架里的 `build_pipeline()` / `build_bindings()` / `register_formats()` 这些函数包装**必须保留**——
它们不是风格建议，是 registry 契约。**为什么必须保留**：看 framework_context 的
`pipeline_registry_lazy_loader_src` 和 `format_registry_dispatch_src` 字段——
那是真实的**调用方代码**，用 `getattr(mod, "build_pipeline")` / `getattr(mod, "register_formats")`
按字符串名查函数。函数不存在就 AttributeError → 你的管线无法注册。

写 pipeline.py 时，在 `def build_pipeline()` 里填 nodes/edges，返回 PipelineSpec 实例。
写 run.py 同理，在 `def build_bindings()` 里填 dict。
写 formats.py 时在 `def register_formats()` 里注册所有 Format。
routers.py 没骨架——从零写。

## 工作流程（严格遵守）

1. **第一步 list_files + read_written_file**：看所有预置骨架结构
2. **按顺序补**：先 formats.py → pipeline.py → routers.py → run.py
3. **每写完一个立刻 py_compile 自检**，PASS 才继续下一个
4. **遇到语法错**：read_written_file 看实际内容 → write_file 覆盖修复
5. **4 文件都 py_compile 通过后才 finish**

## 纪律

- 不要为了省 turn 一次性连写 4 文件再 compile
- 每个 write_file 的 content 是**完整** Python 源代码（不 ellipsis、不 TODO、不 pass）
- write_file 覆盖 pipeline.py / run.py / formats.py 时，**必须保留**骨架里的函数签名
  （WriteFileRouter 会硬校验 `def build_pipeline(` / `def build_bindings(` / `def register_formats(`，
   不满足拒绝写入）

## 遇到不确定的 API

直接引用 framework_context 里的真源码字段（Router / Verdict / AnchorSpec 等），不要靠印象。
"""


class _CodeGenPromptBuilder(PromptBuilderRouter):
    """装配首轮 user 消息：框架源码 + node_plan + format_chain。"""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        pipeline_name = input_data.get("pipeline_name") or input_data.get(
            "_requirement", {}
        ).get("pipeline_name") or "generated"
        package_path = input_data.get("package_path") or "omnicompany.packages.custom.generated"
        framework_context = input_data.get("framework_context") or {}
        node_plan_nodes = input_data.get("nodes") or []
        node_plan_edges = input_data.get("edges") or []
        format_chain = input_data.get("_format_chain") or input_data.get("format_chain") or {}
        entry_id = (node_plan_nodes[0].get("id") if node_plan_nodes else None) or "start"

        # 框架源码完整注入（不截断，铁律 A）
        fw_text = json.dumps(framework_context, ensure_ascii=False, indent=2)
        np_text = json.dumps(
            {"nodes": node_plan_nodes, "edges": node_plan_edges},
            ensure_ascii=False, indent=2,
        )
        fc_text = json.dumps(format_chain, ensure_ascii=False, indent=2)

        msg = f"""# 生成任务

**目标管线**: `{pipeline_name}`
**包路径**: `{package_path}`
**入口节点 id**: `{entry_id}`

## 1) format_chain（已设计的 Format 链）

```json
{fc_text}
```

## 2) node_plan（已设计的节点 + 边）

```json
{np_text}
```

## 3) framework_context（OmniCompany 框架真实源码，共 {len(framework_context)} 字段）

```json
{fw_text}
```

---

现在开始：按 **formats.py → pipeline.py → routers.py → run.py** 顺序。
每写一个 write_file 后**立刻** py_compile 那个文件；遇错 read_written_file 看现场 → 针对性修。
"""
        return [{"role": "user", "content": msg}]


# ═════════════════════════════════════════════════════════════════════
# 自定义 ExtractResultRouter：从共享状态构造 wf.project_skeleton
# ═════════════════════════════════════════════════════════════════════


class _CodeGenExtractResult(ExtractResultRouter):
    """从 Loop 实例的 _shared_state 里提取 files → 构造 project_skeleton."""

    def __init__(self, *, loop_ref: "CodeGenLoop", bus: Any = None):
        super().__init__(bus=bus)
        self._loop = loop_ref

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        state = self._loop._shared_state
        files = state.get("files", {}) or {}
        pipeline_name = state.get("pipeline_name", "generated")
        package_path = state.get("package_path", f"omnicompany.packages.custom.{pipeline_name}")

        # 最后一次 py_compile 验证
        compile_errors: list[dict] = []
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for fn, content in files.items():
                fp = td_path / fn
                fp.write_text(content, encoding="utf-8")
                try:
                    _py_compile.compile(str(fp), doraise=True)
                except _py_compile.PyCompileError as exc:
                    compile_errors.append({"file": fn, "error": str(exc).replace(str(fp), fn)})

        missing = [fn for fn in _VALID_FILENAMES if fn not in files]

        skeleton = {
            "pipeline_name": pipeline_name,
            "package_path": package_path,
            "files": files,
            "reports": {
                "compile": {
                    "l1_syntax": {
                        "passed": len(compile_errors) == 0,
                        "errors": compile_errors,
                    },
                },
                "code_gen_loop": {
                    "turn_count": turn_count,
                    "stop_reason": stop_reason,
                    "missing_files": missing,
                    "written_files": sorted(files.keys()),
                },
            },
        }

        # granted_tags 与旧 4 节点对齐（避免下游依赖断）
        granted = [f"code-gen-{fn.replace('.py', '')}" for fn in files]

        if missing:
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=skeleton,
                diagnosis=(
                    f"CodeGenLoop 结束但缺文件: {missing}; "
                    f"turn={turn_count}, stop={stop_reason}"
                ),
                granted_tags=granted,
            )
        if compile_errors:
            # 4 文件都写了但 compile 有错 —— 让下游 fix_chain 接手
            # 返回 PASS（下游 compile_checker 会 re-run 再 FAIL 进入 deterministic_fixer）
            return Verdict(
                kind=VerdictKind.PASS,
                output=skeleton,
                diagnosis=(
                    f"CodeGenLoop 产出 4 文件但 {len(compile_errors)} 个仍有语法错，"
                    f"交下游 fix_chain; turn={turn_count}"
                ),
                granted_tags=granted,
            )
        return Verdict(
            kind=VerdictKind.PASS,
            output=skeleton,
            diagnosis=f"CodeGenLoop 产出 4 文件全部 py_compile 通过; turn={turn_count}",
            granted_tags=granted + ["code-gen-all-compile-pass"],
        )


# ═════════════════════════════════════════════════════════════════════
# CodeGenLoop — 主入口
# ═════════════════════════════════════════════════════════════════════


class CodeGenLoop(AgentNodeLoop):
    """取代 4 个 CodeGen*Router 的 agent-loop 版本。

    输入：wf.node_plan_augmented（含 framework_context + node_plan + format_chain）
    输出：wf.project_skeleton（files dict + pipeline_name + package_path + reports）

    工具：write_file / py_compile / list_files / read_written_file / finish
    特性：每写一个文件立刻 py_compile 自检，截断/语法错自己修；
          4 文件全通过才 finish。
    """

    FORMAT_IN: ClassVar[str] = "wf.node_plan_augmented"
    FORMAT_OUT: ClassVar[str] = "wf.project_skeleton"
    DESCRIPTION: ClassVar[str] = (
        "Agent-loop 代码生成器：用 write_file + py_compile + read_written_file 工具"
        "逐文件生成 + 自验，取代固定 4 步顺序 LLM 调用，解决 routers.py 常被 token 预算"
        "截断的根因。4 文件全 compile 通过才 finish，否则 PARTIAL。"
    )

    TOOL_ROUTERS: ClassVar[list[type[SingleToolRouter]]] = [
        WriteFileRouter,
        PyCompileRouter,
        ListFilesRouter,
        ReadWrittenFileRouter,
    ]

    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    # 代码生成场景预算：给够轮数，让 compile 循环能自愈
    LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
        max_turns=60,  # 4 文件 × (写+compile+可能修 2 次) ≈ 20，留 3 倍余量
    )

    # 允许 __init__ 时无 bus（管线 bindings 构建时 bus 还没创建；
    # runner 在 run() 前会注入 self._bus，我们在 run() 里手动传播到子 router）
    ALLOW_NO_BUS: ClassVar[bool] = True

    def __init__(
        self,
        *,
        model: str | None = None,
        role: str = "ide_agent",
        bus: Any | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        # 共享可变状态（files dict + pipeline_name + package_path）
        # 通过 build_tool_context 注入到 ctx.code_gen_state
        self._shared_state: dict[str, Any] = {"files": {}}
        super().__init__(model=model, role=role, bus=bus, config=config)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _CodeGenExtractResult(loop_ref=self, bus=bus)

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _CodeGenPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_tool_context(
        self, *, input_data: dict, turn: int, trace_id: str,
    ) -> dict:
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx["code_gen_state"] = self._shared_state  # 共享 ref
        return ctx

    async def run(self, input_data: Any) -> Verdict:
        # 每次 run 重置共享状态，避免跨 run 污染
        if not isinstance(input_data, dict):
            input_data = {}
        pipeline_name, package_path = _derive_identity(input_data)
        # 预置骨架（100% contract 部分 —— LLM 必须保留这些函数签名，
        # 否则 WriteFileRouter 的硬校验会拒绝 overwrite）
        self._shared_state = {
            "files": _build_initial_skeletons(pipeline_name),
            "pipeline_name": pipeline_name,
            "package_path": package_path,
        }
        # bus 传播：__init__ 时 bus=None 构造了子 router；
        # runner 的 universal bus injection 已把 self._bus 注入，现在同步到子 router。
        self._propagate_bus_to_subrouters()
        enriched_input = {
            **input_data,
            "pipeline_name": pipeline_name,
            "package_path": package_path,
        }
        return await super().run(enriched_input)

    def _propagate_bus_to_subrouters(self) -> None:
        """把 self._bus（runner 注入）传播到所有子 Router。

        AgentNodeLoop.__init__ 用 bus=None 构造时，子 Router 的 _bus 也是 None。
        真正 run() 时 runner 已经 injected self._bus，需要同步到：
          - prompt_builder / context_compact / llm_call / extract_result
          - tool_dispatch 本体
          - tool_dispatch.routers (各 SingleToolRouter 实例)
        """
        bus = self._bus
        if bus is None:
            return
        for sub in (
            self._prompt_builder, self._context_compact,
            self._llm_call, self._tool_dispatch, self._extract_result,
        ):
            if sub is not None and hasattr(sub, "_bus"):
                sub._bus = bus
        dispatch_routers = getattr(self._tool_dispatch, "routers", None)
        if dispatch_routers:
            for tr in dispatch_routers:
                if hasattr(tr, "_bus"):
                    tr._bus = bus


# ═════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════


def _build_initial_skeletons(pipeline_name: str) -> dict[str, str]:
    """预置 3 个骨架文件（100% contract 部分）。

    为什么这么预置：
    - `build_pipeline` / `build_bindings` / `register_formats` 的函数名和签名是
      100% 强制 —— registry 端用 `getattr(mod, "build_pipeline")` 按字符串查，
      函数不存在就 AttributeError（l2_import FAIL）。
    - LLM 第一次 list_files 会看到骨架存在 → read_written_file 看到结构 →
      write_file 覆盖时保留骨架。
    - WriteFileRouter 的硬校验兜底：哪怕 LLM 忽略骨架，契约 pattern 缺失也会被拒。

    不预置 routers.py —— 那里是可变的 class 结构（不同管线节点数不同），
    让 LLM 从零写。
    """
    # pipeline.py
    pipeline_skeleton = '''"""由 workflow_factory.code_gen_loop 预置的骨架 —— LLM 需保留 build_pipeline() 包装。
此函数名和签名是 registry 契约（core/pipelines.py::_lazy 用字符串查找）。
"""
from __future__ import annotations

from omnicompany.protocol.anchor import (
    AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind,
)
from omnicompany.protocol.pipeline import (
    NodeKind, NodeMaturity, PipelineEdge, PipelineNode, PipelineSpec,
)


def build_pipeline() -> PipelineSpec:
    """构建本管线的拓扑。TODO: 填 nodes / edges / entry。"""
    raise NotImplementedError("LLM 应 write_file 覆盖填充")
'''

    # run.py
    run_skeleton = '''"""由 workflow_factory.code_gen_loop 预置的骨架 —— LLM 需保留 build_bindings() 包装。
此函数名和签名是 registry 契约（core/pipelines.py::_lazy_fn 用字符串查找）。
"""
from __future__ import annotations

from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    """返回 node_id → Router 实例 的映射。key 必须与 pipeline.py 的 PipelineNode.id 一一对应。"""
    raise NotImplementedError("LLM 应 write_file 覆盖填充")
'''

    # formats.py
    formats_skeleton = '''"""由 workflow_factory.code_gen_loop 预置的骨架 —— LLM 需保留 register_formats() 包装。
此函数名是 registry 契约（core/dispatch.py::_load_format_registry_for_domain 用 getattr 查找）。
"""
from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


def register_formats(registry: FormatRegistry) -> None:
    """把本域的 Format 注册进给定 registry。由 dispatch._load_format_registry_for_domain 调用。"""
    raise NotImplementedError("LLM 应 write_file 覆盖填充")
'''

    return {
        "pipeline.py": pipeline_skeleton,
        "run.py": run_skeleton,
        "formats.py": formats_skeleton,
    }


def _derive_identity(input_data: dict) -> tuple[str, str]:
    """从 input 里推 pipeline_name / package_path（复用旧 CodeGenBase 逻辑）。"""
    req = input_data.get("requirement_context", {}) or input_data.get("_requirement", {}) or {}
    pipeline_name = (
        input_data.get("pipeline_name")
        or req.get("pipeline_name")
        or (req.get("goal", "").split(":")[-1].strip().split(".")[0])
        or "generated"
    )
    domain = input_data.get("domain") or req.get("domain") or "custom"

    def _sanitize(s: str) -> str:
        s = (s or "").strip().replace(" ", "_").replace("-", "_")
        s = re.sub(r"[^\w]", "", s)
        return s if s and s.isascii() else ""

    pipeline_name = _sanitize(pipeline_name) or "generated"
    domain = _sanitize(domain) or "custom"
    package_path = f"omnicompany.packages.{domain}.{pipeline_name.replace('-', '_')}"
    return pipeline_name, package_path
