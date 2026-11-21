# [OMNI] origin=claude-code domain=mcp_builder/run.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:vendors.mcp_builder.pipeline_runner.entrypoint.py"
from omnicompany.runtime.exec.session import PipelineSession
from .pipeline import build_pipeline
from .routers import (
    McpDevelopmentAnchorRouter,
    StudyMcpProtocolRouter,
    StudyFrameworkDocsRouter,
    PlanImplementationRouter,
    ImplementMcpServerRouter,
    CreateEvaluationRouter,
    RunEvaluationRouter
)

async def run(input_data: dict, test_mode: bool = False, db_path: str | None = None):
    pipeline = build_pipeline()
    bindings = {
        "mcp_development_anchor": McpDevelopmentAnchorRouter(),
        "study_mcp_protocol": StudyMcpProtocolRouter(),
        "study_framework_docs": StudyFrameworkDocsRouter(),
        "plan_implementation": PlanImplementationRouter(),
        "implement_mcp_server": ImplementMcpServerRouter(),
        "create_evaluation": CreateEvaluationRouter(),
        "run_evaluation": RunEvaluationRouter()
    }
    session = PipelineSession(pipeline=pipeline, bindings=bindings, source="mcp_builder", max_steps=1000)
    res = await session.run(input_data)
    return {"output": res.output, "trace_id": res.trace_id}
