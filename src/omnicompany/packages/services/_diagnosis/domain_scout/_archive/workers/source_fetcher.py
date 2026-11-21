# [OMNI] origin=claude-code domain=services/domain_scout/workers ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:diagnosis.domain_scout.source_fetcher_worker.py"
"""SourceFetcher — 按 topic.sources 配置抓取 (RSS / GitHub API / 网页) skeleton."""
from typing import Any
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class SourceFetcher(Worker):
    """按 topic.sources 抓取原文, 产 fetch_batch. D5: 不做内容过滤, 只抓."""

    DESCRIPTION = (
        "domain_scout Worker #1: 按 topic.yaml 的 sources 声明 (rss / github_api / "
        "arxiv / 网页) 拉取原文. 不做相关性判定. 产 fetch_batch 给 DedupFilter."
    )
    FORMAT_IN = "domain_scout.scout_request"
    FORMAT_OUT = "domain_scout.fetch_batch"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get(self.FORMAT_IN, {})
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic_id": req.get("topic_id"),
                "fetched_at": None,
                "items": [],
            },
        )
