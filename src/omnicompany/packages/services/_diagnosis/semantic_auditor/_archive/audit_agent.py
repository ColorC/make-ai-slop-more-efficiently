# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.semantic_auditor.audit_agent_loop.python"
"""SemanticAuditor · AuditAgent — 单次审计的 AgentNodeLoop 子类。

一次 run 处理一个 (artifact, standard, excerpt) 三元组：
  - 读 artifact 全文（read_file 工具）
  - 按需跨文件搜索（grep / glob 工具）
  - 对照标准摘录判断是否违规
  - 通过 finish 工具提交 JSON：{"findings": [Finding, ...]}

批量审由 `LLMAuditRouter`（routers.py）HARD 外壳循环调度 —— 每个 excerpt
对应一次 AuditAgent.run()。

依赖：
  - packages.services.agent.AgentNodeLoop（新版 Router 化 Agent Node Loop）
  - packages.services.agent.ReadFileRouter / GrepRouter / GlobRouter
  - FinishRouter 由基类自动追加

铁律：
  - 继承新版 AgentNodeLoop，不碰 legacy runtime/agent（见 CLAUDE.md 阶段 D 计划）
  - 无版本后缀类名
  - ALLOW_NO_BUS 仅限单测；运行必传 bus
  - NODE_PROMPT 固定 system 层；每次 run 的具体 artifact/excerpt 走 input_data.task
    （避免 str.format 撞上 excerpt 内花括号）
"""
from __future__ import annotations

from typing import ClassVar

from omnifactory.packages.services._core.agent import (
    AgentNodeLoop,
    ReadFileRouter,
    GrepRouter,
    GlobRouter,
)


NODE_PROMPT = """你是 OmniCompany 仓库的语义合规审计员。

任务：对照给定的【标准摘录】判断【artifact 文件】是否存在语义违规。

硬纪律：
1. 必须先用 read_file 工具读 artifact 全文再判断；不允许靠标题/路径猜
2. 跨文件依赖的判断用 grep / glob 取证（例如"这个 Router 是否有调用方违反边界"）
3. 标准摘录是唯一权威，不允许自造规则或引用未给出的标准内容
4. 违规描述必须精确到行号（line_hint），多行违规取最核心的一行
5. 信心度 (confidence) 是 0.0-1.0 的浮点：
     >=0.9 — 直接违反摘录显式条款
     0.7-0.9 — 强烈违反但需人工确认表述
     <0.7 — 怀疑有问题但不确定；写到 description 里说明不确定原因
6. recommended_action 必须是可执行动作描述（如"把 db.write 抽成 ToolRouter"），
   不是"看起来要改一下"这种空话

产出协议：
- 用 finish 工具提交最终结果，result 字段是一个 JSON 字符串，结构：
  {
    "findings": [
      {
        "standard_id": "<原样回传>",
        "target_path": "<原样回传>",
        "description": "<1-3 句：哪条规则被违反、在哪里>",
        "line_hint": <int>,
        "confidence": <float>,
        "recommended_action": "<可执行修复描述>"
      },
      ...
    ]
  }
- 无违规：findings 为空数组 []，这是正常情况，不要硬找违规
- 不要在 finish.result 之外写其他 markdown/文字解释"""


class AuditAgent(AgentNodeLoop):
    """单次语义审计 agent loop。

    input_data（dict）必须含：
      - task: str（user message，含 artifact/standard/excerpt 描述）
      - trace_id: str（上游透传）

    输出 Verdict.output:
      - text: LLM finish 工具提交的 result（应是 JSON 字符串）
      - turn_count / stop_reason / trace_id
    """

    NODE_PROMPT: ClassVar[str] = NODE_PROMPT
    TOOL_ROUTERS: ClassVar[list[type]] = [ReadFileRouter, GrepRouter, GlobRouter]
    DESCRIPTION: ClassVar[str] = (
        "语义审计 agent loop：读 artifact + 对照标准摘录 → 产出 Finding JSON"
    )
    FORMAT_IN: ClassVar[str] = "semantic_auditor.audit-single-request"
    FORMAT_OUT: ClassVar[str] = "semantic_auditor.audit-single-finding"
