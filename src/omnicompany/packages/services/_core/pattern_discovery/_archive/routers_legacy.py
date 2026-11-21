# [OMNI] origin=claude-code domain=pattern_discovery/routers.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.pattern_discovery.summary_reader_pattern_clusterer_induction_dispatcher.routers_legacy.py"
# OMNI-024 ALLOW: _archive/ 归档文件，不在标准位置属预期
# [OMNI] DEPRECATED 2026-04-22 — Stage 3 Clean Migration 完成, 业务代码已迁到 workers/*.py:
#   SummaryReaderRouter       → workers/summary_reader.py       (SummaryReaderWorker)
#   PatternClustererRouter    → workers/pattern_clusterer.py    (PatternClustererWorker)
#   InductionDispatcherRouter → workers/induction_dispatcher.py (InductionDispatcherWorker)
# 本文件仅保留作为历史参考, 不再被 workers/__init__.py 继承。
"""pattern_discovery routers — 3 个节点的 Router 实现 (DEPRECATED, 见文件头)

summary_reader (HARD)          — 确定性读取 compression_summaries
pattern_clusterer (SOFT)       — LLM 语义聚类（embedding 降级为 LLM 直判）
induction_dispatcher (SOFT)    — 对候选模式调用 trace-induction
"""

from __future__ import annotations

import json
import logging
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.routing.router import Router
from omnifactory.runtime.exec.sub_pipeline import SubPipelineRouter

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# [1] summary_reader — 确定性 DB 读取 (HARD)
# ═══════════════════════════════════════════════════════════

class SummaryReaderRouter(Router):
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

        from omnifactory.runtime.storage.db_access import open_db

        try:
            with open_db(db_path, readonly=True) as conn:
                # 检查表是否存在
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


# ═══════════════════════════════════════════════════════════
# [2] pattern_clusterer — LLM 语义聚类 (SOFT)
# ═══════════════════════════════════════════════════════════

_CLUSTER_PROMPT = """\
以下是 agent 在多个会话中执行的操作列表。
请找出其中**重复出现的操作模式**（目的相同或高度相似的操作）。

操作列表：
{activities_text}

请将相似的操作归为一组，输出严格 JSON（不要 markdown 代码块）：
{{
  "clusters": [
    {{
      "purpose_summary": "这组操作的共同目的（一句话）",
      "member_indices": [0, 3, 7],
      "count": 3,
      "domain": "推断的领域"
    }}
  ]
}}

规则：
- 只输出出现 >= {min_k} 次的组
- 按出现次数从高到低排序
- 如果没有重复模式，输出空 clusters 列表
"""


class PatternClustererRouter(Router):
    """LLM 对 activity purpose 做语义聚类。

    输入 pd.activities: {activities, min_cluster_size}
    输出 pd.candidates: {candidates, db_path}
    """

    FORMAT_IN = "pd.activities"
    FORMAT_OUT = "pd.candidates"
    DESCRIPTION = (
        "对展平的 activities 列表做语义聚类。LLM 判断哪些 activity 的 purpose "
        "相同或高度相似，归为一组。筛选出现次数 >= min_cluster_size 的组作为候选模式。"
        "优先使用 embedding 余弦相似度，不可用时降级为 LLM 直判。"
    )

    def __init__(self, *, client=None):
        self._client = client

    async def run(self, input_data: Any) -> Verdict:
        activities = input_data.get("activities", [])
        min_k = input_data.get("min_cluster_size", 3)

        if len(activities) < min_k:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"activities 数量 ({len(activities)}) 不足 min_cluster_size ({min_k})",
            )

        # 构造摘要文本
        lines = []
        for i, act in enumerate(activities):
            lines.append(f"[{i}] purpose: {act.get('purpose', '?')} | behavior: {act.get('behavior', '?')}")
        activities_text = "\n".join(lines)

        # 2026-04-21 铁律 A 修复: 移除 activities_text[:10000] 预防性截断
        # qwen3.6-plus 1M context 足够消化完整 activities 列表
        prompt = _CLUSTER_PROMPT.format(activities_text=activities_text, min_k=min_k)

        try:
            resp = self._client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是一个行为模式分析专家。",
            )
            data = _parse_json(resp.content[0].text)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"聚类 LLM 调用失败: {e}",
            )

        if not data or not data.get("clusters"):
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="未发现重复模式",
            )

        candidates = []
        for cluster in data["clusters"]:
            indices = cluster.get("member_indices", [])
            member_acts = [activities[i] for i in indices if i < len(activities)]
            # 收集关联的 trace 信息（如有）
            trace_ids = list({a.get("_session_id", "") for a in member_acts if a.get("_session_id")})

            candidates.append({
                "purpose_summary": cluster.get("purpose_summary", ""),
                "count": cluster.get("count", len(indices)),
                "activities": member_acts,
                "trace_ids": trace_ids,
                "domain": cluster.get("domain", ""),
            })

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "candidates": candidates,
                "db_path": input_data.get("db_path", ""),
            },
            diagnosis=f"发现 {len(candidates)} 个候选重复模式",
        )


# ═══════════════════════════════════════════════════════════
# [3] induction_dispatcher — 调用 trace-induction (SOFT)
# ═══════════════════════════════════════════════════════════

class InductionDispatcherRouter(SubPipelineRouter):
    """对每个候选模式调用 trace-induction 管线。

    输入 pd.candidates: {candidates, db_path}
    输出 pd.done: {processed, induced, details}

    对每个候选尝试调用 trace-induction。
    部分成功也算 PASS（记录每个候选的结果）。
    """

    TARGET_PIPELINE = "trace-induction"
    TARGET_MAX_STEPS = 50

    FORMAT_IN = "pd.candidates"
    FORMAT_OUT = "pd.done"
    DESCRIPTION = (
        "遍历候选重复模式列表，对每个候选调用 trace-induction 管线尝试自动沉淀。"
        "通过 SubPipelineRouter 共享 EventBus 保持可观测性。"
        "部分候选归纳失败不影响整体——记录每个的结果。"
    )

    def prepare_input(self, input_data: dict) -> dict:
        # 取第一个候选（逐个处理，后续可改为 SCATTER）
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
        # 简化：只处理第一个候选的结果
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

        # 逐个处理候选（简化版——后续可用 SCATTER 并行）
        details = []
        induced = 0

        for candidate in candidates:
            purpose = candidate.get("purpose_summary", "")
            db_path = input_data.get("db_path", "data/intent_traces.db")
            raw_ids = candidate.get("trace_ids", [])

            # compression_summaries 的 session_id ≠ intent_steps 的 trace_id
            # 验证 raw_ids 是否在 intent_steps 中存在，否则按关键词搜索
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

            # 调用 trace-induction
            try:
                from omnifactory.core.dispatch import dispatch
                from omnifactory.core.registry import discover
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

        # 标记已处理的摘要
        db_path = input_data.get("db_path", "")
        if db_path:
            try:
                from omnifactory.runtime.storage.db_access import open_db
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


def _validate_trace_ids(ids: list[str], db_path: str) -> list[str]:
    """验证 trace_ids 在 intent_steps 表中存在，返回有效的子集。"""
    if not ids:
        return []
    from omnifactory.runtime.storage.db_access import open_db
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
    import re as _re
    from omnifactory.runtime.storage.db_access import open_db

    # 提取关键词
    words = _re.findall(r'[A-Za-z]{3,}', purpose)
    if not words:
        return []

    try:
        with open_db(db_path, readonly=True) as conn:
            # 检查表是否存在
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='intent_steps'"
            ).fetchall()]
            if not tables:
                return []

            # 按关键词搜索 desc 字段
            conditions = " OR ".join(f"desc LIKE '%{w}%'" for w in words[:3])
            rows = conn.execute(
                f"SELECT DISTINCT trace_id FROM intent_steps WHERE {conditions} LIMIT ?",
                (limit,)
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


def _parse_json(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
