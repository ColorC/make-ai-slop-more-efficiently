# [OMNI] origin=claude-code domain=services/agent ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:core.agent.workers.collection_aggregator.py"
"""services.agent Team 的 Worker 集合 (Phase D 2026-04-21 · BashWorker 2026-04-23).

12 个 Worker（Worker 直接封装 routers/ 内的 Router，无 _archive/ 因为 routers/ 已是新实现）:

  主 Router Worker (5):
  - PromptBuilderWorker:    装配初始 prompt (agent.prompt-request → agent.prompt-built)
  - ContextCompactWorker:   L1-L3 上下文压缩 (agent.context-request → agent.context-compacted)
  - LLMCallWorker:          LLM 调用 + 指数退避重试 (agent.llm-request → agent.llm-response)
  - ToolDispatchWorker:     工具路由 + 权限门 (agent.tool-request → agent.tool-response)
  - ExtractResultWorker:    提取最终结论 (agent.result-request → agent.result-final)

  SingleTool Worker (7):
  - SingleToolWorker:       SingleToolRouter 基类 Worker
  - GlobWorker:             文件模式匹配
  - GrepWorker:             内容搜索
  - ReadFileWorker:         文件读取
  - ListDirWorker:          目录列举
  - BashWorker:             Bash 命令 (走 BashBus, 通用基类, 子类 override 白名单)
  - FinishWorker:           agent loop 终止信号

注意: 无 _archive/ — routers/ 是本 Team 的正式实现，直接包装。
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter as _PromptBuilder
from omnicompany.packages.services._core.agent.routers.context_compact import ContextCompactRouter as _ContextCompact
from omnicompany.packages.services._core.agent.routers.llm_call import LLMCallRouter as _LLMCall
from omnicompany.packages.services._core.agent.routers.tool_dispatch import ToolDispatchRouter as _ToolDispatch
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter as _ExtractResult
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter as _SingleTool,
    GlobRouter as _Glob,
    GrepRouter as _Grep,
    ReadFileRouter as _ReadFile,
    ListDirRouter as _ListDir,
    FinishRouter as _Finish,
)
from omnicompany.packages.services._core.agent.routers.bash import BashRouter as _Bash


class PromptBuilderWorker(Worker, _PromptBuilder):
    """装配 Agent Loop 初始 prompt（系统指令 + 首轮 user messages）。"""


class ContextCompactWorker(Worker, _ContextCompact):
    """每轮 LLM 调用前做 L1-L3 上下文压缩（aging / 截断 / 滑窗）。"""


class LLMCallWorker(Worker, _LLMCall):
    """调用 LLM，含指数退避重试，解析 tool_use blocks。"""


class ToolDispatchWorker(Worker, _ToolDispatch):
    """按 tool_name 路由到具体 SingleToolWorker，含权限检查。"""


class ExtractResultWorker(Worker, _ExtractResult):
    """从完整对话历史提取 Agent Loop 的最终业务产物，返回 Verdict。"""


class SingleToolWorker(Worker, _SingleTool):
    """SingleToolRouter 基类 Worker，子类声明 TOOL_NAME / INPUT_SCHEMA / execute()。"""


class GlobWorker(Worker, _Glob):
    """文件模式匹配工具（glob 模式，返回匹配文件列表）。"""


class GrepWorker(Worker, _Grep):
    """内容搜索工具（ripgrep 语义，返回匹配行列表）。"""


class ReadFileWorker(Worker, _ReadFile):
    """文件读取工具（含偏移 / 行数限制，防超长截断铁律 A）。"""


class ListDirWorker(Worker, _ListDir):
    """目录列举工具（返回目录内容列表，含文件 / 子目录）。"""


class BashWorker(Worker, _Bash):
    """Bash 命令工具基类。子类 override TOOL_NAME / _validate_command 加业务白名单。

    构造必须传 `bash_bus` (带 Workspace 的 BashBus 实例), 由 BashBus 负责 cwd 校验
    + 危险命令黑名单 + 审计回流。
    """


class FinishWorker(Worker, _Finish):
    """Agent Loop 终止信号工具（LLM 调用 finish 后由 AgentNodeLoop 响应）。"""


ALL_WORKERS = [
    PromptBuilderWorker,
    ContextCompactWorker,
    LLMCallWorker,
    ToolDispatchWorker,
    ExtractResultWorker,
    SingleToolWorker,
    GlobWorker,
    GrepWorker,
    ReadFileWorker,
    ListDirWorker,
    BashWorker,
    FinishWorker,
]

__all__ = [
    "PromptBuilderWorker",
    "ContextCompactWorker",
    "LLMCallWorker",
    "ToolDispatchWorker",
    "ExtractResultWorker",
    "SingleToolWorker",
    "GlobWorker",
    "GrepWorker",
    "ReadFileWorker",
    "ListDirWorker",
    "BashWorker",
    "FinishWorker",
    "ALL_WORKERS",
]
