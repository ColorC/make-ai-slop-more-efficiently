# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.origin_request_normalizer.worker.py"
"""OriginRequestLoaderWorker — agent-first 入口 (2026-04-23 · HARD).

Worker 协议:
  FORMAT_IN  = team_builder.material.request_trigger (kind.source)
  FORMAT_OUT = team_builder.material.origin_request

**职责**: HARD · 把 CLI `--text` (承载在 request_trigger) 包装成完整 origin_request material.
- 填充元信息: triggered_at (现在 UTC) / triggered_by ("cli") / body_path (规范路径)
- tags 按默认值
- 为 IntentAnalyzer + ReferenceScout 的下游做干净的 origin_request 输入

HARD 确定性: 无 LLM 调用.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class OriginRequestLoaderWorker(Worker):
    """HARD · 将 CLI request_trigger 包装为完整 origin_request material."""

    DESCRIPTION = (
        "agent-first 入口 Worker · HARD 确定性 · 把 CLI --text 参数包装成完整"
        " origin_request (含触发时间/触发者/tags/body_path 元信息)."
    )
    FORMAT_IN = "team_builder.material.request_trigger"
    FORMAT_OUT = "team_builder.material.origin_request"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )

        text = input_data.get("text") or input_data.get("request_text")
        if not text or not isinstance(text, str):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="request_trigger.text is empty or missing",
            )

        triggered_at = datetime.now(timezone.utc).isoformat()
        origin = {
            "request_text": text.strip(),
            "triggered_at": triggered_at,
            "triggered_by": input_data.get("triggered_by", "cli"),
            "tags": input_data.get("tags", []) or [],
            "body_path": (
                "data/services/team_builder/runs/<run_id>/.omni/origin_request.md"
            ),
        }
        target_package_path = input_data.get("target_package_path")
        if target_package_path is not None:
            if not isinstance(target_package_path, str) or not target_package_path.strip():
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={},
                    diagnosis="request_trigger.target_package_path must be a non-empty string",
                )
            origin["target_package_path"] = target_package_path.strip()

        return Verdict(
            kind=VerdictKind.PASS,
            output=origin,
        )
