# [OMNI] origin=claude-code domain=services/pattern_discovery ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:core.pattern_discovery.summary_reader.worker.py"
"""SummaryReaderWorker — 确定性 DB 读取 (HARD, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import json
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class SummaryReaderWorker(Worker):
    """从 compression_summaries 表读取未处理摘要，展平 activities。

    输入 pd.trigger: {db_path, min_cluster_size, similarity_threshold}
    输出 pd.activities: {activities, summary_ids, db_path, ...}
    """

    FORMAT_IN = "pd.trigger"
    FORMAT_OUT = "pd.activities"
    DESCRIPTION = (
        "从 compression_summaries 表读取 checked=false 的未处理摘要，"
        "展平所有 activities 为列表。每条 activity 附加来源 session_id 和 summary_id。"
        "确定性 SQL 查询操作。"
    )

    def run(self, input_data: Any) -> Verdict:
        db_path = input_data.get("db_path", "data/intent_traces.db")
        min_k = input_data.get("min_cluster_size", 3)
        threshold = input_data.get("similarity_threshold", 0.85)

        from omnicompany.runtime.storage.db_access import open_db

        try:
            with open_db(db_path, readonly=True) as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='compression_summaries'"
                ).fetchall()
                if not tables:
                    return Verdict(
                        kind=VerdictKind.FAIL, output=input_data,
                        diagnosis="compression_summaries 表不存在",
                    )

                rows = conn.execute(
                    "SELECT id, session_id, activities FROM compression_summaries WHERE checked = 0"
                ).fetchall()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"数据库读取失败: {e}",
            )

        if not rows:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="没有未处理的 compression_summaries",
            )

        activities = []
        summary_ids = []
        for row in rows:
            sid = row["id"]
            session = row["session_id"]
            summary_ids.append(sid)
            try:
                acts = json.loads(row["activities"])
                for act in acts:
                    act["_summary_id"] = sid
                    act["_session_id"] = session
                    activities.append(act)
            except (json.JSONDecodeError, TypeError):
                continue

        if not activities:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="所有摘要的 activities 都为空",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "activities": activities,
                "summary_ids": summary_ids,
                "db_path": db_path,
                "min_cluster_size": min_k,
                "similarity_threshold": threshold,
            },
            diagnosis=f"读取 {len(rows)} 条摘要，展平 {len(activities)} 个 activities",
            confidence=1.0,
        )
