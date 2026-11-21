# [OMNI] origin=claude-code domain=services/plan_audit ts=2026-06-19T00:00:00Z type=service
# [OMNI] material_id="material:core.plan_audit.module_aggregate.exports.py"
"""services.plan_audit — 第三方 plan/对话落地审计引擎.

用户原话 (2026-06-19):
> 新增一个第三方 plan audit 功能, 让一个至少有全部读取权限和 bash 的 agent 去进行 plan audit,
> 有两种输入方式:
>  (1) 输入一个对话 → 逐步整理本对话中用户发出的所有指示, 然后针对每一项指示寻找落地情况
>      (在对话中以及实际硬盘中), 对未落地的内容(不包括落地了然后删了的内容)整理出来;
>  (2) 输入一个 plan → 去寻找有读取和写入这个 plan 的对话, 进一步筛选真正在执行和起草这个
>      plan 的对话, 提取相关上下文, 获取用户的额外指示并总结落地情况.

落地形态:
- ConversationAuditor: 输入(1). 通过外置 AgentRuntime 获得受 grant 限制的
  read/list/search/分片读与 Git 历史证据，不拥有通用 shell 或写权限。
- PlanAuditor: 输入(2). 先定位真正在执行/起草 plan 的对话(关联挖掘在 discovery.py),
  再对每个相关对话跑同样的指示核对, 叠加 plan.md 的 exit_criteria.

铁律 (写进 NODE_PROMPT): "未落地不包括落地了然后删了的" —— 判定看对话历史中是否出现过
落地动作(改文件/建产物), 一旦出现过就算落地, 哪怕现在硬盘上没了. 不能只 grep 当前硬盘状态.
"""

from omnicompany.packages.services._core.plan_audit.auditor import (
    ConversationAuditor,
    PlanAuditor,
)
from omnicompany.packages.services._core.plan_audit.discovery import (
    find_conversation_by_session_id,
    load_full_transcript,
    discover_plan_conversations,
)
from omnicompany.packages.services._core.plan_audit.run import (
    run_conversation_audit,
    run_plan_audit,
    persist_report,
)

__all__ = [
    "ConversationAuditor",
    "PlanAuditor",
    "find_conversation_by_session_id",
    "load_full_transcript",
    "discover_plan_conversations",
    "run_conversation_audit",
    "run_plan_audit",
    "persist_report",
]
