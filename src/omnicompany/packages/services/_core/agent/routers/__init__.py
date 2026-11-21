# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-18 type=infrastructure
# [OMNI] material_id="material:core.agent.routers.module_aggregate.exports.py"
"""services.agent.routers — Agent SingleToolRouter 集合 (Claude Code 对齐).

每个 Router 负责一个 Format_in → Format_out 的转换, 自动在 bus 上发
router.<name>.input / router.<name>.output 两条事件 (trace_id 贯穿).

工具清单 (2026-05-04 全面对齐):
  IO:           FileRead / FileEdit / Glob / Grep / NotebookEdit / WriteFile
  Bash 系列:    Bash / DevBash / PowerShell / REPL
  Todo + 计划:  TodoWrite / EnterPlanMode / ExitPlanMode / VerifyPlanExecution
  网络:         WebSearch / WebBrowser / WebFetch
  Worktree:     EnterWorktree / ExitWorktree
  事件:         ScheduleCron / Monitor / RemoteTrigger / PushNotification
  Agent 编排:   Agent / DiscoverSkills / Skill / ToolSearch / Workflow
  辅助:         AskUserQuestion / Sleep / Config / Snip / TerminalCapture / Brief / CtxInspect / LSP
  MCP:          MCP / McpAuth / ListMcpResources / ReadMcpResource / ListPeers
  测试:         OverflowTest / SyntheticOutput

不引清单 (claude.ai 产品特有, 跟 omnicompany 概念冲突或语义不适用):
  TeamCreate / TeamDelete (= TaskList, 跟 omnicompany Team=节点拓扑冲突)
  TaskCreate / TaskGet / TaskList / TaskOutput / TaskStop / TaskUpdate (依赖 claude Team)
  Tungsten / SubscribePR / SuggestBackgroundPR / SendMessage / SendUserFile

详见: docs/plans/agent-framework/[2026-05-03]CC-TOOLS-FULL-ALIGNMENT/plan.md
"""

# ── 已有工具 (本会话之前) ──
from .bash import BashRouter
from .dev_bash import DevBashRouter
from .write_file import WriteFileRouter
from .web_fetch import WebFetchRouter
from .single_tool import SingleToolRouter, ToolExecutionError, ToolContext

# ── 第二波: 核心 IO (2026-05-04) ──
from .file_read import FileReadRouter
from .file_edit import FileEditRouter
from .glob_search import GlobRouter
from .grep_search import GrepRouter
from .notebook_edit import NotebookEditRouter

# ── 第三波: Todo + Ask + Sleep + Config (2026-05-04) ──
from .todo_write import TodoWriteRouter
from .ask_user_question import AskUserQuestionRouter
from .sleep import SleepRouter
from .config_tool import ConfigToolRouter

# ── 第四波: 网络 (2026-05-04) ──
from .web_search import WebSearchRouter
from .web_browser import WebBrowserRouter
from .verify_plan_execution import VerifyPlanExecutionRouter

# ── 第五波: 计划模式 + Worktree (2026-05-04) ──
from .plan_mode import EnterPlanModeRouter, ExitPlanModeRouter
from .worktree import EnterWorktreeRouter, ExitWorktreeRouter

# ── 第六波: 事件 (2026-05-04) ──
from .cron_tools import ScheduleCronRouter
from .event_tools import (
    MonitorRouter,
    RemoteTriggerRouter,
    PushNotificationRouter,
)

# ── 第七波: Agent 编排 (2026-05-04) ──
from .agent_spawn import AgentRouter
# 注: agent_spawn_factory 不在此 re-export — 它 import AgentNodeLoop, 而 loop.py 又
# import routers/__init__.py, 形成循环依赖. 消费方直接 from
# omnicompany.packages.services._core.agent.routers.agent_spawn_factory import ...
from .skill_tools import (
    DiscoverSkillsRouter,
    SkillRouter,
    ToolSearchRouter,
)
from .workflow_tool import WorkflowRouter

# ── 第八波: 辅助 (2026-05-04) ──
from .aux_tools import (
    SnipRouter,
    TerminalCaptureRouter,
    BriefRouter,
    CtxInspectRouter,
    LSPRouter,
)

# ── 第九波: MCP / PowerShell / REPL / 测试 (2026-05-04) ──
from .mcp_tools import (
    MCPRouter,
    McpAuthRouter,
    ListMcpResourcesRouter,
    ReadMcpResourceRouter,
    ListPeersRouter,
)
from .shell_alt_tools import PowerShellRouter, REPLRouter
from .testing_tools import OverflowTestRouter, SyntheticOutputRouter


# ═══════════════════════════════════════════════════════════════════════
# 默认 vs 延后 (deferred) 工具集 (2026-05-04 第二波 P0, 真对齐 claude code)
# ═══════════════════════════════════════════════════════════════════════
#
# claude code 的实际行为:
#   - 默认载入 ~11 个核心工具到 LLM system tools (schema 注入到 LLM 提示)
#   - 其余 ~30 个 deferred — 只把工具名通过 system-reminder 告诉 LLM,
#     schema 不加载. LLM 想用 deferred 工具必须先调 ToolSearch 拉取 schema.
#   - 这避免 LLM system prompt 被 42 个工具的完整 schema 撑爆 (省几千 token)
#
# omnicompany default 集合 (10 个, 跟 claude code 顶部 functions 列表对齐):
#   Bash / Read / Edit / Write / Glob / Grep / Agent / Skill / ToolSearch / PowerShell
#   (claude code 还有 ScheduleWakeup, 是 /loop 内部专用, omnicompany 无对应概念不引)
#
# omnicompany deferred 集合 (其余 30+, 按需通过 ToolSearch 拉)
#
# 用法:
#   from omnicompany.packages.services._core.agent.routers import (
#       DEFAULT_TOOL_ROUTERS,        # 默认载入到 LLM system tools 的 11 个
#       DEFERRED_TOOL_ROUTERS,       # 延后载入, 只通过 ToolSearch 按需拉
#       ALL_TOOL_ROUTERS,            # 全部 (= default + deferred), 兼容旧调用
#       TOOLS_BY_NAME,               # 按名字索引全部工具
#   )

DEFAULT_TOOL_ROUTERS: list[type[SingleToolRouter]] = [
    # 核心 IO (5)
    FileReadRouter,    # Read
    FileEditRouter,    # Edit
    WriteFileRouter,   # write_file (注: 命名与 cc Write 不同, 是历史遗留)
    GlobRouter,        # Glob
    GrepRouter,        # Grep
    # Shell (2)
    BashRouter,        # bash (注: 命名与 cc Bash 不同, 是历史遗留)
    PowerShellRouter,  # PowerShell
    # Agent 编排核心 (3)
    AgentRouter,       # Agent (spawn 子 agent)
    SkillRouter,       # Skill (加载 skill instructions)
    ToolSearchRouter,  # ToolSearch (按需拉 deferred schema)
]

DEFERRED_TOOL_ROUTERS: list[type[SingleToolRouter]] = [
    # IO 扩展
    NotebookEditRouter,
    # Todo + 计划模式
    TodoWriteRouter,
    EnterPlanModeRouter, ExitPlanModeRouter,
    VerifyPlanExecutionRouter,
    # 网络
    WebSearchRouter, WebBrowserRouter, WebFetchRouter,
    # Worktree
    EnterWorktreeRouter, ExitWorktreeRouter,
    # 事件 / cron
    ScheduleCronRouter, MonitorRouter, RemoteTriggerRouter,
    PushNotificationRouter,
    # Skill 体系扩展 (DiscoverSkills / Workflow 是 Skill / ToolSearch 的辅助)
    DiscoverSkillsRouter, WorkflowRouter,
    # 辅助 / 诊断
    AskUserQuestionRouter, SleepRouter, ConfigToolRouter,
    SnipRouter, TerminalCaptureRouter, BriefRouter,
    CtxInspectRouter, LSPRouter,
    # 持久状态
    REPLRouter,
    # MCP 套件
    MCPRouter, McpAuthRouter, ListMcpResourcesRouter,
    ReadMcpResourceRouter, ListPeersRouter,
    # 测试 / 合规
    OverflowTestRouter, SyntheticOutputRouter,
]

# 兼容旧调用方
ALL_TOOL_ROUTERS: list[type[SingleToolRouter]] = (
    DEFAULT_TOOL_ROUTERS + DEFERRED_TOOL_ROUTERS
)

TOOLS_BY_NAME: dict[str, type[SingleToolRouter]] = {
    cls.TOOL_NAME: cls for cls in ALL_TOOL_ROUTERS
}

# 按 default / deferred 分别索引
DEFAULT_TOOLS_BY_NAME: dict[str, type[SingleToolRouter]] = {
    cls.TOOL_NAME: cls for cls in DEFAULT_TOOL_ROUTERS
}
DEFERRED_TOOLS_BY_NAME: dict[str, type[SingleToolRouter]] = {
    cls.TOOL_NAME: cls for cls in DEFERRED_TOOL_ROUTERS
}


def get_default_tool_specs() -> list[dict]:
    """返默认工具的 Anthropic API schema list (注入 LLM tools 参数)."""
    return [cls.to_api_spec() for cls in DEFAULT_TOOL_ROUTERS]


def get_deferred_tool_names_with_descriptions() -> list[dict]:
    """返 deferred 工具的"名字 + 一句话描述", 用于 system-reminder 告知 LLM 这些工具存在.

    Schema 不返 (deferred 的本意), LLM 要 schema 必须调 ToolSearch.
    """
    return [
        {
            "name": cls.TOOL_NAME,
            "description": (cls.DESCRIPTION or "").split("\n")[0][:200],
        }
        for cls in DEFERRED_TOOL_ROUTERS
    ]


def lookup_tool_schemas(names: list[str]) -> list[dict]:
    """按名字查 deferred 工具的完整 schema (供 ToolSearch 真实现用).

    Args:
        names: 工具名列表 (大小写敏感, 跟 TOOL_NAME 完全一致)

    Returns:
        匹配工具的 Anthropic API spec list (含 name / description / input_schema)
    """
    out: list[dict] = []
    for n in names:
        cls = TOOLS_BY_NAME.get(n)
        if cls is not None:
            out.append(cls.to_api_spec())
    return out


__all__ = [
    # 基类
    "SingleToolRouter", "ToolExecutionError", "ToolContext",
    # 注册表
    "ALL_TOOL_ROUTERS", "TOOLS_BY_NAME",
    "DEFAULT_TOOL_ROUTERS", "DEFAULT_TOOLS_BY_NAME",
    "DEFERRED_TOOL_ROUTERS", "DEFERRED_TOOLS_BY_NAME",
    # helper
    "get_default_tool_specs",
    "get_deferred_tool_names_with_descriptions",
    "lookup_tool_schemas",
    # 工具子类
    "FileReadRouter", "FileEditRouter", "GlobRouter", "GrepRouter",
    "NotebookEditRouter", "WriteFileRouter",
    "BashRouter", "DevBashRouter", "PowerShellRouter", "REPLRouter",
    "TodoWriteRouter", "EnterPlanModeRouter", "ExitPlanModeRouter",
    "VerifyPlanExecutionRouter",
    "WebSearchRouter", "WebBrowserRouter", "WebFetchRouter",
    "EnterWorktreeRouter", "ExitWorktreeRouter",
    "ScheduleCronRouter", "MonitorRouter", "RemoteTriggerRouter",
    "PushNotificationRouter",
    "AgentRouter", "DiscoverSkillsRouter", "SkillRouter",
    "ToolSearchRouter", "WorkflowRouter",
    "AskUserQuestionRouter", "SleepRouter", "ConfigToolRouter",
    "SnipRouter", "TerminalCaptureRouter", "BriefRouter",
    "CtxInspectRouter", "LSPRouter",
    "MCPRouter", "McpAuthRouter", "ListMcpResourcesRouter",
    "ReadMcpResourceRouter", "ListPeersRouter",
    "OverflowTestRouter", "SyntheticOutputRouter",
]
