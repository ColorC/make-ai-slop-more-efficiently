# [OMNI] origin=claude-code domain=services/hypothesis/_archive/routers_legacy.py ts=2026-04-20T00:00:00Z type=router status=active
# [OMNI] material_id="material:learning.hypothesis.agent_node_loops.router_definitions.py"
# OMNI-024 ALLOW: _archive/ 归档文件，Router 类不在标准位置属预期 (Phase D Diamond shortcut)
"""hypothesis Experimenter — 主探索 agent 节点(v5 决策库收编版仅存部分)。

ExperimenterRouter (AgentNodeLoop):
  主 agent — 自由用 bash/read_file/glob/grep 探索，输出行为轨迹。

旧 markdown 版 ReflectorRouter、双脑 LockstepExperimenterRouter 及其配套工具
(EditRouter/WriteFileRouter/ValidateHypothesisDocRouter/FindSimilarFormatsRouter)
已随决策本体合并清单#1 拆除(khyp 文档体系退役);总结 agent 现为
workers/belief_reflector.py 的 BeliefReflectorRouter(直接维护统一决策库)。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import (
    CompactConfig,
    LoopConfig,
    PermissionConfig,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.packages.services._core.agent import (
    AgentNodeLoop,
    ExtractResultRouter,
    GlobRouter,
    GrepRouter,
    PromptBuilderRouter,
    ReadFileRouter,
    SingleToolRouter,
    ToolExecutionError,
)

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# hypothesis 域专用工具 Router（Phase C 迁移新增）
# ════════════════════════════════════════════════════════════════════════════

class BashRouter(SingleToolRouter):
    """bash 命令执行（复用 ToolExecutor.execute('bash', ...)）."""

    TOOL_NAME: ClassVar[str] = "bash"
    DESCRIPTION: ClassVar[str] = (
        "Executes a given bash command and returns its output.\n\n"
        "IMPORTANT: Avoid using this tool to run find, grep, cat, head, tail, sed, awk, or echo commands. "
        "Use the appropriate dedicated tool instead:\n"
        " - File search: Use glob (NOT find or ls)\n"
        " - Content search: Use grep (NOT grep or rg)\n"
        " - Read files: Use read_file (NOT cat/head/tail)\n"
        " - Edit files: Use edit (NOT sed/awk)\n"
        " - Write files: Use write_file (NOT echo >/cat <<EOF)\n\n"
        "Reserve bash for system commands and terminal operations (env checks, Python scripts, HTTP requests, etc.)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "description": {"type": "string", "description": "Clear description of what this command does"},
            "timeout": {"type": "number", "description": "Optional timeout in milliseconds (max 600000)"},
            "run_in_background": {"type": "boolean", "description": "Set to true to run in background"},
        },
        "required": ["command"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        return self._executor.execute("bash", args)


# ════════════════════════════════════════════════════════════════════════════
# ExperimenterRouter — 主 agent（自由探索）
# ════════════════════════════════════════════════════════════════════════════

_EXPERIMENTER_SYSTEM_PROMPT = """\
你是一个假设探索 agent。通过运行命令和读取文件，探索目标系统的行为规律并验证假设。

全部用简体中文思考和记录。

你拥有以下工具：
- bash: 执行任意 shell 命令。典型用途：列目录、查环境变量、跑 Python 脚本、发 HTTP 请求。
- read_file: 读取文件完整内容。
- glob: 按模式查找文件路径。
- grep: 在文件或目录内搜关键词。
- finish: 结束本轮探索。不需要输出内容。

工作原则：
- 每 2-3 步自问：这条路径是否在逼近 goal？如果连续没进展，主动换方向。
- 不要重复跑完全相同的命令。
- 优先走可能直接触达 goal 的路径，不要只做表面 CLI 探索。
- 可以自由写 Python 脚本、查注册表、读配置、发 HTTP 请求——任何你觉得能推进 goal 的手段。
- 观察到显著现象时，在自己的推理里标注"这可能是一条规律"，但不要硬塞工具调用去"记录"——总结 agent 会从你的行为轨迹里归纳。
"""


class _ExperimenterPromptBuilder(PromptBuilderRouter):
    """Experimenter 首轮 prompt 装配。"""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        store = input_data.get("store", {}) or {}
        session = input_data.get("session", {}) or {}

        goal = session.get("goal", "(未指定目标)")
        tools_hint = session.get("tools", [])
        iteration = store.get("iteration", 0)
        entries = store.get("entries", [])

        lines = [
            "## 探索目标",
            goal,
            "",
            "## 建议工具（参考）",
            f"{tools_hint}",
            "",
            f"## 当前假设库（第 {iteration} 轮，共 {len(entries)} 条）",
        ]
        if entries:
            for e in entries:
                label = {"living": "验证中", "stable": "已证实",
                         "deprecated": "已证伪"}.get(e.get("state", ""), "待验证")
                lines.append(
                    f"- [{label}] {e.get('id','')}: {e.get('predicted','') or e.get('trigger','')}"
                )
        else:
            lines.append("（暂无假设）")
        lines.append("")
        lines.append("请开始探索。所有工具调用的记录会自动传给总结 agent，你不需要格外记录。")
        lines.append("认为本轮探索已积累足够观察时调用 finish。")
        return [{"role": "user", "content": "\n".join(lines)}]


class _ExperimenterExtractResult(ExtractResultRouter):
    """从 messages 提取 tool_use + tool_result 对，输出 trace。"""

    def __init__(self, *, bus: Any, iteration_ref: dict):
        super().__init__(bus=bus)
        self._iteration_ref = iteration_ref  # 由 Experimenter.run 注入 iteration

    def extract(
        self, *, final_text: str, messages: list[dict], turn_count: int, stop_reason: str,
    ) -> Verdict:
        trace: list[dict] = []
        tool_use_by_id: dict[str, dict] = {}
        for msg in messages:
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "tool_use":
                    tool_use_by_id[block.get("id", "")] = {
                        "tool": block.get("name", ""),
                        "args": block.get("input", {}),
                    }
                elif btype == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    entry = tool_use_by_id.get(tool_use_id, {})
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_content = "\n".join(
                            c.get("text", "") if isinstance(c, dict) else str(c)
                            for c in result_content
                        )
                    trace.append({
                        "tool": entry.get("tool", ""),
                        "args": entry.get("args", {}),
                        "result": result_content,
                    })
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "iteration": self._iteration_ref.get("iteration", 0),
                "trace": trace,
                "turn_count": turn_count,
                "stop_reason": stop_reason,
            },
        )


class ExperimenterRouter(AgentNodeLoop):
    """主 agent：自由探索，输出行为轨迹。

    2026-04-18 Phase C 迁移到 packages.services.agent.AgentNodeLoop。
    """

    DESCRIPTION: ClassVar[str] = "假设探索 AgentNodeLoop：自由探索，输出行为轨迹"
    FORMAT_IN: ClassVar[str] = "hypothesis.store"
    FORMAT_OUT: ClassVar[str] = "hypothesis.factlog"

    NODE_PROMPT: ClassVar[str] = _EXPERIMENTER_SYSTEM_PROMPT
    LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
        max_turns=200,  # 铁律 B 死循环安全网
        compact=CompactConfig(auto_compact_enabled=False),
        permission=PermissionConfig(mode="default"),
    )
    TOOL_ROUTERS: ClassVar[list[type[SingleToolRouter]]] = [
        BashRouter, ReadFileRouter, GlobRouter, GrepRouter,
        # FinishRouter 会被基类自动追加
    ]

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("role", "runtime_main")
        # iteration 在 run(input_data) 时从 store 读；先预留可变引用给 ExtractResult 读
        self._iteration_ref: dict = {"iteration": 0}
        super().__init__(**kwargs)

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _ExperimenterPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _ExperimenterExtractResult(bus=bus, iteration_ref=self._iteration_ref)

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx["origin"] = input_data.get("origin", "internal-engine")
        ctx["domain"] = input_data.get("domain", "services/hypothesis")
        ctx["agent_name"] = input_data.get("agent_name", "ExperimenterRouter")
        return ctx

    async def run(self, input_data: Any) -> Verdict:
        # 把 iteration 推进 ref，供 ExtractResult 读
        if isinstance(input_data, dict):
            store = input_data.get("store", {}) or {}
            self._iteration_ref["iteration"] = store.get("iteration", 0)
        return await super().run(input_data)


