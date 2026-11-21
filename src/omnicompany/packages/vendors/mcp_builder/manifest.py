# [OMNI] origin=claude-code domain=mcp_builder/manifest.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:vendors.mcp_builder.skill_manifest.registry.py"
from omnicompany.protocol.resource import ResourceDomainManifest, ResourceFormat
from .routers import *

class SkillManifest(ResourceDomainManifest):
    prefix = "mcp_builder"
    formats = [ResourceFormat("output_2", "autogen"), ResourceFormat("output_5", "autogen"), ResourceFormat("input_0", "autogen"), ResourceFormat("output_3", "autogen"), ResourceFormat("output_1", "autogen"), ResourceFormat("output_0", "autogen"), ResourceFormat("output_6", "autogen"), ResourceFormat("output_4", "autogen")]
    routers = [McpDevelopmentAnchorRouter(), StudyMcpProtocolRouter(), StudyFrameworkDocsRouter(), PlanImplementationRouter(), ImplementMcpServerRouter(), CreateEvaluationRouter(), RunEvaluationRouter()]
