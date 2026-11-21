# [OMNI] origin=claude-code domain=protocol/pipeline ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:protocol.pipeline.deprecation_shim.py"
# DEPRECATED 2026-04-21 — 正名已迁至 omnicompany.protocol.team
# 此文件为过渡期 shim，保持旧 import 路径可用；后续消费者请改用 protocol.team。
#
# 迁移指引:
#   旧: from omnicompany.protocol.pipeline import PipelineSpec, PipelineNode, PipelineEdge
#   新: from omnicompany.protocol.team import TeamSpec, TeamNode, TeamEdge
from omnicompany.protocol.team import (  # noqa: F401
    STRICT_AUDIT_DEFAULT_RUNS,
    EdgeCheckResult,
    GraphChecker,
    GraphEdge,
    GraphNode,
    GraphSpec,
    InfoAuditMode,
    NodeKind,
    NodeMaturity,
    PipelineChecker,
    PipelineCheckResult,
    PipelineEdge,
    PipelineExecutionMode,
    PipelineNode,
    PipelineSpec,
    ScatterSpec,
    TeamChecker,
    TeamCheckResult,
    TeamEdge,
    TeamExecutionMode,
    TeamNode,
    TeamSpec,
    describe_agent_loop,
)
