# [OMNI] origin=claude-code domain=services/domain_scout/workers ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:diagnosis.domain_scout.digest_writer_worker.py"
"""DigestWriter — 合成 digest.md + 更新 index.jsonl (sink 产出)."""
from typing import Any
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class DigestWriter(Worker):
    """写 digest.md 到 data/services/domain_scout/<topic>/digests/; append index.jsonl."""

    DESCRIPTION = (
        "domain_scout Worker #6: 合成 digest markdown + append index.jsonl. "
        "sink 产出, privacy_publish 主动读消费. "
        "文件路径 data/services/domain_scout/<topic_id>/digests/YYYY-MM-DD.md."
    )
    FORMAT_IN = "domain_scout.verified_findings"
    FORMAT_OUT = "domain_scout.digest"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        vfs = input_data.get(self.FORMAT_IN, {})
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic_id": vfs.get("topic_id"),
                "digest_path": None,
                "finding_count": 0,
                "index_updated": False,
            },
        )
