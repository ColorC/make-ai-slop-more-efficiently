# [OMNI] origin=claude-code domain=services/pattern_discovery ts=2026-04-22T00:00:00Z type=worker
# [OMNI] OMNI-004 NOTE: run() 为 async 不可避免 — 继承 SubTeamWorker, 基类 run()
# [OMNI] material_id="material:core.pattern_discovery.induction_dispatcher.worker.py"
#   本身就是 async (runtime/exec/sub_pipeline.py:81), 因为需要 `await dispatch(...)` 调
#   子管线. 此合法场景, 非 AgentNodeLoop-style 违反.
"""InductionDispatcherWorker — 调用 trace-induction (SOFT, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import re as _re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.exec.sub_pipeline import SubTeamWorker


def _validate_trace_ids(ids: list[str], db_path: str) -> list[str]:
    """验证 trace_ids 在 intent_steps 表中存在，返回有效的子集。"""
    if not ids:
        return []
    from omnicompany.runtime.storage.db_access import open_db
    try:
        with open_db(db_path, readonly=True) as conn:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT DISTINCT trace_id FROM intent_steps WHERE trace_id IN ({placeholders})",
                ids,
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


def _search_traces_by_purpose(purpose: str, db_path: str, limit: int = 3) -> list[str]:
    """从 intent_steps 表按 purpose 关键词搜索相关 trace_id。"""
    from omnicompany.runtime.storage.db_access import open_db

    words = _re.findall(r'[A-Za-z]{3,}', purpose)
    if not words:
        return []

    try:
        with open_db(db_path, readonly=True) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='intent_steps'"
            ).fetchall()]
            if not tables:
                return []

            conditions = " OR ".join(f"desc LIKE '%{w}%'" for w in words[:3])
            rows = conn.execute(
                f"SELECT DISTINCT trace_id FROM intent_steps WHERE {conditions} LIMIT ?",
                (limit,)
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


class InductionDispatcherWorker(Worker, SubTeamWorker):
    """对每个候选模式调用 trace-induction 管线。

    输入 pd.candidates: {candidates, db_path}
    输出 pd.done: {processed, induced, details}

    部分成功也算 PASS（记录每个候选的结果）。
    """

    TARGET_PIPELINE = "trace-induction"
    TARGET_MAX_STEPS = 50

    FORMAT_IN = "pd.candidates"
    FORMAT_OUT = "pd.done"
    DESCRIPTION = (
        "遍历候选重复模式列表，对每个候选调用 trace-induction 管线尝试自动沉淀。"
        "通过 SubTeamWorker 共享 EventBus 保持可观测性。"
        "部分候选归纳失败不影响整体——记录每个的结果。"
    )

    def prepare_input(self, input_data: dict) -> dict:
        candidates = input_data.get("candidates", [])
        if not candidates:
            return {}
        first = candidates[0]
        return {
            "purpose": first.get("purpose_summary", ""),
            "trace_ids": ",".join(first.get("trace_ids", [])),
            "domain": first.get("domain", ""),
            "db_path": input_data.get("db_path", "data/intent_traces.db"),
        }

    def extract_output(self, sub_result: Any, input_data: dict) -> dict:
        candidates = input_data.get("candidates", [])
        detail = {
            "purpose": candidates[0].get("purpose_summary", "") if candidates else "",
            "result": sub_result if isinstance(sub_result, dict) else {"raw": str(sub_result)[:200]},
        }
        induced = 1 if isinstance(sub_result, dict) and sub_result.get("status") == "registered" else 0

        return {
            "processed": 1,
            "induced": induced,
            "details": [detail],
            "remaining_candidates": len(candidates) - 1,
        }

    async def run(self, input_data: Any) -> Verdict:
        candidates = input_data.get("candidates", [])
        if not candidates:
            return Verdict(
                kind=VerdictKind.PASS,
                output={"processed": 0, "induced": 0, "details": []},
                diagnosis="无候选模式需要处理",
            )

        details = []
        induced = 0

        for candidate in candidates:
            purpose = candidate.get("purpose_summary", "")
            db_path = input_data.get("db_path", "data/intent_traces.db")
            raw_ids = candidate.get("trace_ids", [])

            trace_ids = _validate_trace_ids(raw_ids, db_path)
            if not trace_ids:
                trace_ids = _search_traces_by_purpose(purpose, db_path, limit=3)

            if not trace_ids:
                details.append({
                    "purpose": purpose,
                    "status": "skipped",
                    "reason": "无法找到关联的 trace（compression_summaries 和 intent_steps 未关联）",
                })
                continue

            sub_input = {
                "purpose": purpose,
                "trace_ids": ",".join(trace_ids),
                "domain": candidate.get("domain", ""),
                "db_path": db_path,
            }

            try:
                from omnicompany.core.dispatch import dispatch
                from omnicompany.core.registry import discover
                discover()
                result = await dispatch(
                    "trace-induction", sub_input, max_steps=50,
                )
                if isinstance(result, dict) and result.get("status") == "registered":
                    induced += 1
                details.append({
                    "purpose": sub_input["purpose"],
                    "status": result.get("status", "unknown") if isinstance(result, dict) else "unknown",
                })
            except Exception as e:
                details.append({
                    "purpose": sub_input["purpose"],
                    "status": "failed",
                    "reason": str(e),
                })

        db_path = input_data.get("db_path", "")
        if db_path:
            try:
                from omnicompany.runtime.storage.db_access import open_db
                with open_db(db_path) as conn:
                    conn.execute("UPDATE compression_summaries SET checked = 1 WHERE checked = 0")
            except Exception:
                pass

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "processed": len(candidates),
                "induced": induced,
                "details": details,
            },
            diagnosis=f"处理 {len(candidates)} 个候选，成功归纳 {induced} 个",
        )
