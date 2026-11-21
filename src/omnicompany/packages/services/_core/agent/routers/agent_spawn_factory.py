# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T16:00:00Z type=infrastructure
"""AgentRouter 子 agent 工厂模块 — 真 spawn 用.

构造三种通用子 agent (general-purpose / Explore / Plan), 跟 claude code AgentTool 的
subagent_type 概念对齐. 工厂函数返 AgentNodeLoop 子类**实例**, AgentRouter
拿到后用 `asyncio.run(agent.run({...}))` 驱动.

警示 (反虚假声明铁律):
- 此模块只达到 L1 schema + L2 真 spawn 骨架. 真 NODE_PROMPT 是简版占位, 跟 cc
  原文未对齐; 真 LLM smoke 还没跑过. Wave 5 才会做 prompt 完整复刻 + 真 LLM
  dogfood. 当前不能声明 "Agent 工具对齐 claude code".
- 子 agent 的 LLMClient role 默认走 "ide_agent" (跟 IDEAgentLoop 一致), 这意味
  着真启会走 qwen-3.6-plus, 需要 API key. 测试可用 OMNI_AGENT_DRY_RUN=1 短路.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.single_tool import SingleToolRouter

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 通用子 agent NODE_PROMPT (简版 — Wave 5 复刻 cc 原文)
# ═══════════════════════════════════════════════════════════════════════

_GENERAL_PURPOSE_PROMPT = """You are a general-purpose sub-agent spawned by the main Claude Code agent.

Your task: read the user message carefully, use the provided tools to investigate
or perform the requested action, then call the `finish` tool with your final answer.

Tools available:
- Read / Glob / Grep — read-only file system inspection
- PowerShell — shell execution (use sparingly)
- Edit / Write — code modifications (use sparingly; prefer Edit over Write)
- Skill / ToolSearch — load instructions / look up deferred tool schemas
- finish — call this when you have your final answer

Format your final answer as concise markdown. Cite file paths as `<path>:<line>`."""


_EXPLORE_PROMPT = """You are an Explore sub-agent. You have READ-ONLY access to the codebase.

Your task: locate code, find symbols, answer questions like "where is X defined"
or "which files reference Y". Do NOT review designs, audit consistency, or analyze
beyond what was asked.

Tools available:
- Glob — find files by pattern
- Grep — search file contents (regex)
- Read — read file contents (line-bounded)
- finish — return your concise answer

When done, call `finish` with a markdown report:
- File paths cited as `<path>:<line>`
- Excerpts kept short (≤ 5 lines per snippet)
- No speculation beyond what you read"""


_PLAN_PROMPT = """You are a Plan sub-agent. Your job: design an implementation plan.

Use READ-ONLY tools to understand the current state, then return a step-by-step
plan in markdown. Identify critical files. Consider architectural trade-offs.

Tools available:
- Read / Glob / Grep — read-only inspection
- finish — return your plan markdown

Plan format:
1. Goal (one sentence)
2. Critical files (with paths + reason)
3. Steps (numbered, each with file path + change)
4. Trade-offs / risks
5. Verification approach

Do NOT implement. Just plan."""


# ═══════════════════════════════════════════════════════════════════════
# 子 agent 类
# ═══════════════════════════════════════════════════════════════════════

def _import_default_tools() -> dict[str, type[SingleToolRouter]]:
    """延迟 import 避免循环依赖 — agent_spawn.py 在 routers/__init__.py 里被引用.

    Wave 3 警示: BashRouter 需要 bash_bus= 参数, 跟 AgentNodeLoop 的标准
    `R(bus=bus)` 实例化合约不兼容. 暂不放入 sub-agent 默认工具集 (Wave 5 解).
    PowerShell 不需要 bash_bus, 用 subprocess 直跑, 故可入.
    """
    from omnicompany.packages.services._core.agent.routers import (
        FileReadRouter, FileEditRouter, WriteFileRouter,
        GlobRouter, GrepRouter,
        PowerShellRouter,
        SkillRouter, ToolSearchRouter,
    )
    return {
        "Read": FileReadRouter,
        "Edit": FileEditRouter,
        "Write": WriteFileRouter,
        "Glob": GlobRouter,
        "Grep": GrepRouter,
        "PowerShell": PowerShellRouter,
        "Skill": SkillRouter,
        "ToolSearch": ToolSearchRouter,
    }


class GeneralPurposeSubAgent(AgentNodeLoop):
    """通用子 agent — 默认工具子集 (Read/Edit/Write/Glob/Grep/PowerShell/Skill/ToolSearch).

    Wave 3 缺口: 没含 BashRouter (需 bash_bus 注入, 跟 AgentNodeLoop 默认实例化合约
    不兼容). 没含 AgentRouter (防递归 spawn). Wave 5 把 BashRouter 接入修.

    role='ide_agent' 跟 IDEAgentLoop 一致 (qwen-3.6-plus).
    """

    NODE_PROMPT: ClassVar[str] = _GENERAL_PURPOSE_PROMPT
    DESCRIPTION: ClassVar[str] = "GeneralPurposeSubAgent — partial default tool access"

    @classmethod
    def _build_tool_routers(cls) -> list[type[SingleToolRouter]]:
        t = _import_default_tools()
        return [
            t["Read"], t["Edit"], t["Write"], t["Glob"], t["Grep"],
            t["PowerShell"], t["Skill"], t["ToolSearch"],
        ]

    def __init__(self, *, bus: Any, model: str | None = None, role: str | None = None, config: Any | None = None):
        # 动态注入 TOOL_ROUTERS (绕开类变量循环依赖)
        type(self).TOOL_ROUTERS = self._build_tool_routers()
        super().__init__(model=model, role=role or "ide_agent", bus=bus, config=config)


class ExploreSubAgent(AgentNodeLoop):
    """只读子 agent — 用于代码搜索 / 符号定位 / "X 在哪定义" 等开放查询.

    工具集: Read / Glob / Grep (无 Bash, 无 Edit/Write).
    """

    NODE_PROMPT: ClassVar[str] = _EXPLORE_PROMPT
    DESCRIPTION: ClassVar[str] = "ExploreSubAgent — read-only code search"

    @classmethod
    def _build_tool_routers(cls) -> list[type[SingleToolRouter]]:
        t = _import_default_tools()
        return [t["Read"], t["Glob"], t["Grep"]]

    def __init__(self, *, bus: Any, model: str | None = None, role: str | None = None, config: Any | None = None):
        type(self).TOOL_ROUTERS = self._build_tool_routers()
        super().__init__(model=model, role=role or "ide_agent", bus=bus, config=config)


class PlanSubAgent(AgentNodeLoop):
    """规划子 agent — 设计实现 plan, 不执行.

    工具集跟 Explore 一致 (只读), prompt 不同.
    """

    NODE_PROMPT: ClassVar[str] = _PLAN_PROMPT
    DESCRIPTION: ClassVar[str] = "PlanSubAgent — design implementation plans"

    @classmethod
    def _build_tool_routers(cls) -> list[type[SingleToolRouter]]:
        t = _import_default_tools()
        return [t["Read"], t["Glob"], t["Grep"]]

    def __init__(self, *, bus: Any, model: str | None = None, role: str | None = None, config: Any | None = None):
        type(self).TOOL_ROUTERS = self._build_tool_routers()
        super().__init__(model=model, role=role or "ide_agent", bus=bus, config=config)


# ═══════════════════════════════════════════════════════════════════════
# Registry 工厂
# ═══════════════════════════════════════════════════════════════════════

# Type alias — factory(model: str | None) -> AgentNodeLoop instance
SubAgentFactory = Callable[..., AgentNodeLoop]


def build_default_subagent_registry(
    *,
    bus: Any,
    config: Any | None = None,
) -> dict[str, SubAgentFactory]:
    """构造默认 subagent_registry, AgentRouter ctx 注入用.

    Args:
        bus: EventBus (SQLiteBus / MemoryBus). AgentNodeLoop bus=None 会 raise.
        config: 可选 LoopConfig override.

    Returns:
        dict[subagent_type → factory]. factory(model=...) → AgentNodeLoop 实例.

    使用:
        ```python
        registry = build_default_subagent_registry(bus=bus)
        ctx_data = {"subagent_registry": registry, ...}
        # ctx_data 进 ToolDispatchRouter 的 context 字段
        ```
    """
    if bus is None:
        raise ValueError(
            "build_default_subagent_registry requires bus=. AgentNodeLoop refuses bus=None."
        )

    def _general(model: str | None = None) -> AgentNodeLoop:
        return GeneralPurposeSubAgent(bus=bus, model=model, config=config)

    def _explore(model: str | None = None) -> AgentNodeLoop:
        return ExploreSubAgent(bus=bus, model=model, config=config)

    def _plan(model: str | None = None) -> AgentNodeLoop:
        return PlanSubAgent(bus=bus, model=model, config=config)

    return {
        "general-purpose": _general,
        "Explore": _explore,
        "Plan": _plan,
    }


__all__ = [
    "GeneralPurposeSubAgent",
    "ExploreSubAgent",
    "PlanSubAgent",
    "build_default_subagent_registry",
    "SubAgentFactory",
]
