# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.llm.behavior_preservation.summarizer.py"
"""compression_summary — L4 压缩前的行为保全摘要（轨迹归纳 Phase 1）

在 auto_compact() 触发前，先用独立 LLM 调用提取本轮对话中
agent 做了哪些具体事情，写入 compression_summaries 表。

后续 Pattern Discovery Agent 读取这些摘要，聚类发现重复模式，
驱动轨迹归纳流程（SOP 提取 → 需求文档 → Workflow Factory）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from omnicompany.runtime.storage.db_access import open_db

logger = logging.getLogger(__name__)

# ── 表定义 ────────────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS compression_summaries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    round_num         INTEGER NOT NULL,
    activities        TEXT NOT NULL,
    checked           BOOLEAN DEFAULT 0,
    matched_pipeline  TEXT DEFAULT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cs_checked ON compression_summaries (checked);",
    "CREATE INDEX IF NOT EXISTS idx_cs_session ON compression_summaries (session_id);",
]

_SUMMARY_PROMPT = """\
你是一个行为归档员。以下是一段对话历史，即将被压缩。
请提取这段对话中 agent 做了哪些具体事情。

要求：
- 每个 activity 必须有明确的 purpose（做什么）和 behavior（怎么做）
- behavior 要精确到具体工具和操作步骤，不要笼统描述
- 保留全部行为，不要遗漏
- 不要总结"讨论了什么"，只记录"实际做了什么"
- 如果 agent 没有做任何具体操作（纯讨论），返回空列表

输出严格 JSON（不要 markdown 代码块）：
{
  "activities": [
    {
      "purpose": "修改 TavernPool 配表的 PoolType 字段",
      "behavior": "读取 Excel → 按种族+是否新UP推断 PoolType → 写回 Excel",
      "tools_used": ["read_excel", "write_excel", "p4_submit"],
      "domain": "demogame/tavern",
      "input_artifacts": ["TavernPool.xlsm"],
      "output_artifacts": ["TavernPool.xlsm (modified)"]
    }
  ]
}
"""


def _ensure_table(db_path: str) -> None:
    """确保 compression_summaries 表存在。"""
    with open_db(db_path) as conn:
        conn.executescript(_CREATE_TABLE + "\n".join(_CREATE_INDEXES))


async def generate_compression_summary(
    messages: list[dict],
    llm_call,
    *,
    session_id: str,
    round_num: int,
    db_path: str,
) -> dict | None:
    """在 L4 压缩前生成行为保全摘要并写入数据库。

    Args:
        messages: 即将被压缩的完整消息列表
        llm_call: async callable(messages, system) -> str
        session_id: 当前会话 ID
        round_num: 第几轮压缩
        db_path: 数据库路径

    Returns:
        解析后的摘要 dict，或 None（生成失败时）
    """
    # 序列化消息为可读文本（复用 compact 的序列化逻辑）
    from omnicompany.runtime.agent.agent_loop_compact import _serialize_messages_for_compact

    history_text = _serialize_messages_for_compact(messages)

    # 2026-04-18 零容忍截断：不预判 context 大小。若真溢出，LLM API 会报
    # context_length_exceeded 异常，此处捕获后由上游 agent-loop 分片重试。
    # 见 docs/standards/llm_first.md 原则 3（零容忍版）。
    try:
        raw = await llm_call(
            [{"role": "user", "content": history_text}],
            _SUMMARY_PROMPT,
        )
    except Exception:
        logger.warning("[compression_summary] LLM 调用失败", exc_info=True)
        return None

    # 解析 JSON
    try:
        # 处理可能的 markdown 代码块包裹
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        summary = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("[compression_summary] JSON 解析失败: %s", raw[:200])
        return None

    activities = summary.get("activities", [])
    if not activities:
        logger.info("[compression_summary] 无行为记录（纯讨论会话）")
        return None

    # 写入数据库
    now = datetime.now(timezone.utc).isoformat()
    _ensure_table(db_path)
    with open_db(db_path) as conn:
        conn.execute(
            "INSERT INTO compression_summaries "
            "(session_id, timestamp, round_num, activities, checked) "
            "VALUES (?, ?, ?, ?, 0)",
            (session_id, now, round_num, json.dumps(activities, ensure_ascii=False)),
        )

    logger.info(
        "[compression_summary] 记录 %d 个 activities (session=%s, round=%d)",
        len(activities), session_id, round_num,
    )
    return summary
