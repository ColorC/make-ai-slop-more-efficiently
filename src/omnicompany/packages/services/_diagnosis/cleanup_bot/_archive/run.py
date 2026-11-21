# [OMNI] origin=claude-code domain=cleanup_bot/run.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:diagnosis.cleanup_bot.bindings_builder.py"
"""cleanup_bot — bindings"""

from __future__ import annotations

from omnifactory.packages.services._diagnosis.cleanup_bot.routers import (
    AnomalyDetectorRouter,
    EvidenceGathererRouter,
    RollbackPlannerRouter,
)
from omnifactory.runtime.llm.llm import LLMClient
from omnifactory.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    client = LLMClient.for_role("runtime_main", tools=[])
    return {
        "evidence_gatherer": EvidenceGathererRouter(),
        "anomaly_detector": AnomalyDetectorRouter(client=client),
        "rollback_planner": RollbackPlannerRouter(),
    }
