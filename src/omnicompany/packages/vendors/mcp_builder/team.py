# [OMNI] origin=claude-code domain=mcp_builder/pipeline.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:vendors.mcp_builder.team_spec.builder.py"
from omnicompany.protocol.team import TeamSpec, TeamNode, TeamEdge, NodeKind
from omnicompany.protocol.anchor import AnchorSpec, TransformerSpec, ValidatorSpec, ValidatorKind, TransformMethod, Route, RouteAction, VerdictKind

def build_team() -> TeamSpec:
    return TeamSpec(
        id="mcp_builder",
        name="Imported skill",
        entry="mcp_development_anchor",
        description="Imported skill",
        nodes=[
        
        TeamNode(
            id="mcp_development_anchor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="mcp_development_anchor-spec",
                name="MCP Server Development Initiation",
                format_in="mcp_builder.input_0",
                format_out="mcp_builder.output_0",
                validator=ValidatorSpec(
                    id="mcp_development_anchor-v",
                    kind=ValidatorKind.SOFT,
                    description="Target service API to integrate with MCP"
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=3),
                }
            )
        ),
        TeamNode(
            id="study_mcp_protocol",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="study_mcp_protocol-spec",
                name="Study MCP Protocol Documentation",
                from_format="mcp_builder.output_0",
                to_format="mcp_builder.output_1",
                method=TransformMethod.LLM,
                description="MCP protocol sitemap URL"
            )
        ),
        TeamNode(
            id="study_framework_docs",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="study_framework_docs-spec",
                name="Study Framework Documentation",
                from_format="mcp_builder.output_1",
                to_format="mcp_builder.output_2",
                method=TransformMethod.LLM,
                description="Framework documentation URLs for chosen language"
            )
        ),
        TeamNode(
            id="plan_implementation",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="plan_implementation-spec",
                name="Plan Implementation",
                from_format="mcp_builder.output_2",
                to_format="mcp_builder.output_3",
                method=TransformMethod.LLM,
                description="Target service API documentation URL"
            )
        ),
        TeamNode(
            id="implement_mcp_server",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="implement_mcp_server-spec",
                name="Implement MCP Server",
                from_format="mcp_builder.output_3",
                to_format="mcp_builder.output_4",
                method=TransformMethod.LLM,
                description="Planned endpoint list and tool naming scheme"
            )
        ),
        TeamNode(
            id="create_evaluation",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="create_evaluation-spec",
                name="Create Evaluation Questions",
                from_format="mcp_builder.output_4",
                to_format="mcp_builder.output_5",
                method=TransformMethod.LLM,
                description="Implemented MCP server with available tools"
            )
        ),
        TeamNode(
            id="run_evaluation",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="run_evaluation-spec",
                name="Run Evaluation Harness",
                from_format="mcp_builder.output_5",
                to_format="mcp_builder.output_6",
                method=TransformMethod.LLM,
                description="XML evaluation file and running MCP server"
            )
        ),
        ],
        edges=[
        TeamEdge(source="mcp_development_anchor", target="study_mcp_protocol", condition=VerdictKind.PASS),
        TeamEdge(source="study_mcp_protocol", target="study_framework_docs", condition=VerdictKind.PASS),
        TeamEdge(source="study_framework_docs", target="plan_implementation", condition=VerdictKind.PASS),
        TeamEdge(source="plan_implementation", target="implement_mcp_server", condition=VerdictKind.PASS),
        TeamEdge(source="implement_mcp_server", target="create_evaluation", condition=VerdictKind.PASS),
        TeamEdge(source="create_evaluation", target="run_evaluation", condition=VerdictKind.PASS),
        TeamEdge(source="run_evaluation", target="implement_mcp_server", condition=VerdictKind.FAIL),
        ]
    )
