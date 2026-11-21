# [OMNI] origin=claude-code domain=services/pattern_discovery ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:core.pattern_discovery.pattern_clusterer.worker.py"
"""PatternClustererWorker — LLM 语义聚类 (SOFT, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import json
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


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


def _parse_json(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


class PatternClustererWorker(Worker):
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

    def run(self, input_data: Any) -> Verdict:
        activities = input_data.get("activities", [])
        min_k = input_data.get("min_cluster_size", 3)

        if len(activities) < min_k:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"activities 数量 ({len(activities)}) 不足 min_cluster_size ({min_k})",
            )

        lines = []
        for i, act in enumerate(activities):
            lines.append(f"[{i}] purpose: {act.get('purpose', '?')} | behavior: {act.get('behavior', '?')}")
        activities_text = "\n".join(lines)

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
