# [OMNI] origin=claude-code domain=services/_learning ts=2026-06-23T00:00:00Z type=team
# [OMNI] material_id="material:services._learning.conversation_sedimenter.init.py"
"""conversation-operation-sedimenter —— 事件型 team: 从 CC/codex 对话提取常见操作 → 提议可沉淀 team 骨架。

确定性 TraceReader 压缩对话 → gpt-5.5 Miner 聚类常见操作 → 确定性 Proposer 装骨架+写草稿。
入口 material=convop.request, 出口=convop.team_skeleton。
按名可跑: dispatch("conversation-operation-sedimenter", {"transcript_path": "...", "source": "claude-code"})。
"""
from .formats import ALL_FORMATS
from .workers import (
    ALL_WORKERS,
    ConversationOperationMinerWorker,
    ConversationTraceReaderWorker,
    TeamSkeletonProposerWorker,
)

__all__ = [
    "ALL_FORMATS", "ALL_WORKERS",
    "ConversationTraceReaderWorker", "ConversationOperationMinerWorker", "TeamSkeletonProposerWorker",
]
