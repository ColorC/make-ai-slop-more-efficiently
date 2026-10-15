"""Read-only Dashboard projection for AI learning evidence and assessments."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from omnicompany.packages.services.ai_learning_observatory import LearningObservatoryService


ai_learning_observatory_router = APIRouter(prefix="/ai-learning-observatory", tags=["ai-learning-observatory"])


@ai_learning_observatory_router.get("/overview")
def overview() -> dict:
    return LearningObservatoryService().status()


@ai_learning_observatory_router.get("/projects/{project_id}")
def project_detail(project_id: str) -> dict:
    service = LearningObservatoryService()
    snapshot = service.store.latest_snapshot(project_id)
    assessment = service.store.latest_assessment(project_id)
    profile = service.evaluation_profile(project_id)
    if not snapshot and not assessment and not profile:
        raise HTTPException(status_code=404, detail=f"no observatory evidence for project {project_id}")
    return {
        "project_id": project_id,
        "evaluation_profile": profile,
        "snapshot": snapshot,
        "assessment": assessment,
    }


__all__ = ["ai_learning_observatory_router"]
