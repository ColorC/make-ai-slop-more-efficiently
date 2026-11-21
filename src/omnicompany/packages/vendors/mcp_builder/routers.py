# [OMNI] origin=claude-code domain=mcp_builder/routers.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:vendors.mcp_builder.agent_pipeline_router_chain.definitions.py"
import asyncio
from omnicompany.runtime.routing.router import Router
from omnicompany.protocol.anchor import Verdict, VerdictKind
class McpDevelopmentAnchorRouter(Router):
    """MCP Server Development Initiation"""
    DESCRIPTION = "MCP Server Development Initiation"
    FORMAT_IN = "mcp_builder.input_0"
    FORMAT_OUT = "mcp_builder.output_0"
    
    def run(self, input_data):
        from omnicompany.runtime.agent.agent_loop import run_agent
        from omnicompany.runtime.exec.tools import ALL_TOOLS
        
        
        # Load local tool requirements
        tlist = []
        for t in tlist:
             if t not in ALL_TOOLS:
                 ALL_TOOLS.append(t)
                 
        sys_prompt = "Create MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools.\n- The quality of an MCP server is measured by how well it enables LLMs to accomplish real-world tasks.\n- Four main phases: Deep Research and Planning, Implementation, Evaluation, Iteration."
        try:
            print(f"--- Running {self.__class__.__name__} ---")
            result = asyncio.run(run_agent(
                str(input_data), system_prompt=sys_prompt,
                origin="skill_pipeline", max_steps=1000,
                model="zhipu/glm-5.1"
            ))
            return Verdict(kind=VerdictKind.PASS, output={"text": result, "context": input_data})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

class StudyMcpProtocolRouter(Router):
    """Study MCP Protocol Documentation"""
    DESCRIPTION = "Study MCP Protocol Documentation"
    FORMAT_IN = "mcp_builder.output_0"
    FORMAT_OUT = "mcp_builder.output_1"
    
    def run(self, input_data):
        from omnicompany.runtime.agent.agent_loop import run_agent
        from omnicompany.runtime.exec.tools import ALL_TOOLS
        
        
        # Load local tool requirements
        tlist = []
        for t in tlist:
             if t not in ALL_TOOLS:
                 ALL_TOOLS.append(t)
                 
        sys_prompt = "Navigate the MCP specification starting with sitemap: https://modelcontextprotocol.io/sitemap.xml\n- Fetch specific pages with .md suffix for markdown format (e.g., https://modelcontextprotocol.io/specification/draft.md)\n- Key pages to review: Specification overview and architecture, Transport mechanisms (streamable HTTP, stdio), Tool/resource/prompt definitions"
        try:
            result = asyncio.run(run_agent(
                str(input_data), system_prompt=sys_prompt,
                origin="skill_pipeline", max_steps=100
            ))
            return Verdict(kind=VerdictKind.PASS, output={"text": result, "context": input_data})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

class StudyFrameworkDocsRouter(Router):
    """Study Framework Documentation"""
    DESCRIPTION = "Study Framework Documentation"
    FORMAT_IN = "mcp_builder.output_1"
    FORMAT_OUT = "mcp_builder.output_2"
    
    def run(self, input_data):
        from omnicompany.runtime.agent.agent_loop import run_agent
        from omnicompany.runtime.exec.tools import ALL_TOOLS
        
        
        # Load local tool requirements
        tlist = []
        for t in tlist:
             if t not in ALL_TOOLS:
                 ALL_TOOLS.append(t)
                 
        sys_prompt = "Recommended language: TypeScript (high-quality SDK support, good compatibility in many execution environments e.g. MCPB, AI models are good at generating TypeScript code, benefits from broad usage, static typing and good linting tools)\n- Transport: Streamable HTTP for remote servers using stateless JSON (simpler to scale and maintain, as opposed to stateful sessions and streaming responses). stdio for local servers.\n- TypeScript key imports: import { McpServer } from \"@modelcontextprotocol/sdk/server/mcp.js\"; import { StreamableHTTPServerTransport } from \"@modelcontextprotocol/sdk/server/streamableHttp.js\"; import { StdioServerTransport } from \"@modelcontextprotocol/sdk/server/stdio.js\"; import { z } from \"zod\";\n- Python key imports: from mcp.server.fastmcp import FastMCP; from pydantic import BaseModel, Field, field_validator, ConfigDict; from typing import Optional, List, Dict, Any; from enum import Enum; import httpx;\n- TypeScript server initialization: const server = new McpServer({ name: \"service-mcp-server\", version: \"1.0.0\" });\n- Python server initialization: mcp = FastMCP(\"service_mcp\")"
        try:
            result = asyncio.run(run_agent(
                str(input_data), system_prompt=sys_prompt,
                origin="skill_pipeline", max_steps=100
            ))
            return Verdict(kind=VerdictKind.PASS, output={"text": result, "context": input_data})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

class PlanImplementationRouter(Router):
    """Plan Implementation"""
    DESCRIPTION = "Plan Implementation"
    FORMAT_IN = "mcp_builder.output_2"
    FORMAT_OUT = "mcp_builder.output_3"
    
    def run(self, input_data):
        from omnicompany.runtime.agent.agent_loop import run_agent
        from omnicompany.runtime.exec.tools import ALL_TOOLS
        
        
        # Load local tool requirements
        tlist = []
        for t in tlist:
             if t not in ALL_TOOLS:
                 ALL_TOOLS.append(t)
                 
        sys_prompt = "API Coverage vs. Workflow Tools: Balance comprehensive API endpoint coverage with specialized workflow tools. Workflow tools can be more convenient for specific tasks, while comprehensive coverage gives agents flexibility to compose operations. Performance varies by client—some clients benefit from code execution that combines basic tools, while others work better with higher-level workflows. When uncertain, prioritize comprehensive API coverage.\n- Tool Naming and Discoverability: Clear naming is critical for LLM tool selection.\n- Review the service's API documentation to identify key endpoints, authentication requirements, and data models. Use web search and WebFetch as needed.\n- Tool Selection: Prioritize comprehensive API coverage. List endpoints to implement, starting with the most common operations."
        try:
            result = asyncio.run(run_agent(
                str(input_data), system_prompt=sys_prompt,
                origin="skill_pipeline", max_steps=100
            ))
            return Verdict(kind=VerdictKind.PASS, output={"text": result, "context": input_data})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

class ImplementMcpServerRouter(Router):
    """Implement MCP Server"""
    DESCRIPTION = "Implement MCP Server"
    FORMAT_IN = "mcp_builder.output_3"
    FORMAT_OUT = "mcp_builder.output_4"
    
    def run(self, input_data):
        from omnicompany.runtime.agent.agent_loop import run_agent
        from omnicompany.runtime.exec.tools import ALL_TOOLS
        
        
        # Load local tool requirements
        tlist = []
        for t in tlist:
             if t not in ALL_TOOLS:
                 ALL_TOOLS.append(t)
                 
        sys_prompt = "Server Naming - Python: {service}_mcp (e.g., slack_mcp, github_mcp, jira_mcp)\n- Server Naming - Node/TypeScript: {service}-mcp-server (e.g., slack-mcp-server)\n- Tool Naming: Use snake_case with service prefix, format: {service}_{action}_{resource}, example: slack_send_message, github_create_issue\n- Response Formats: Support both JSON and Markdown formats. JSON for programmatic processing, Markdown for human readability.\n- Pagination: Always respect limit parameter. Return has_more, next_offset, total_count. Default to 20-50 items.\n- Transport: Streamable HTTP for remote servers, multi-client scenarios. stdio for local integrations, command-line tools. Avoid SSE (deprecated in favor of streamable HTTP).\n- TypeScript tool registration pattern: server.registerTool(\"tool_name\", { title: \"Tool Display Name\", description: \"What the tool does\", ... })\n- Python tool registration pattern: @mcp.tool(name=\"tool_name\", annotations={...}) async def tool_function(params: InputModel) -> str: pass"
        try:
            result = asyncio.run(run_agent(
                str(input_data), system_prompt=sys_prompt,
                origin="skill_pipeline", max_steps=100
            ))
            return Verdict(kind=VerdictKind.PASS, output={"text": result, "context": input_data})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

class CreateEvaluationRouter(Router):
    """Create Evaluation Questions"""
    DESCRIPTION = "Create Evaluation Questions"
    FORMAT_IN = "mcp_builder.output_4"
    FORMAT_OUT = "mcp_builder.output_5"
    
    def run(self, input_data):
        from omnicompany.runtime.agent.agent_loop import run_agent
        from omnicompany.runtime.exec.tools import ALL_TOOLS
        
        
        # Load local tool requirements
        tlist = []
        for t in tlist:
             if t not in ALL_TOOLS:
                 ALL_TOOLS.append(t)
                 
        sys_prompt = "Create exactly 10 human-readable questions\n- Questions must be READ-ONLY, INDEPENDENT, NON-DESTRUCTIVE\n- Each question requires multiple tool calls (potentially dozens)\n- Answers must be single, verifiable values\n- Answers must be STABLE (won't change over time)\n- Output format: <evaluation><qa_pair><question>Your question here</question><answer>Single verifiable answer</answer></qa_pair></evaluation>\n- The measure of quality of an MCP server is NOT how well or comprehensively the server implements tools, but how well these implementations (input/output schemas, docstrings/descriptions, functionality) enable LLMs to accomplish real-world tasks."
        try:
            result = asyncio.run(run_agent(
                str(input_data), system_prompt=sys_prompt,
                origin="skill_pipeline", max_steps=100
            ))
            return Verdict(kind=VerdictKind.PASS, output={"text": result, "context": input_data})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

class RunEvaluationRouter(Router):
    """Run Evaluation Harness"""
    DESCRIPTION = "Run Evaluation Harness"
    FORMAT_IN = "mcp_builder.output_5"
    FORMAT_OUT = "mcp_builder.output_6"
    
    def run(self, input_data):
        from omnicompany.runtime.agent.agent_loop import run_agent
        from omnicompany.runtime.exec.tools import ALL_TOOLS
        import os
        
        from omnicompany.protocol.tool import tool
        import subprocess
        @tool
        def scripts_evaluation_py(args_str: str = "") -> str:
            """Execute scripts/evaluation.py with args."""
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts/evaluation.py")
            cmd = ["python" if "scripts/evaluation.py".endswith(".py") else "bash", script_path] + (args_str.split() if args_str else [])
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return res.stdout
            except subprocess.CalledProcessError as e:
                return f"Execution failed:\n{e.stderr}\n{e.stdout}"

        from omnicompany.protocol.tool import tool
        @tool
        def scripts_connections_py(args_str: str = "") -> str:
            """Execute scripts/connections.py with args."""
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts/connections.py")
            cmd = ["python" if "scripts/connections.py".endswith(".py") else "bash", script_path] + (args_str.split() if args_str else [])
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return res.stdout
            except subprocess.CalledProcessError as e:
                return f"Execution failed:\n{e.stderr}\n{e.stdout}"

        
        # Load local tool requirements
        tlist = [scripts_evaluation_py, scripts_connections_py]
        for t in tlist:
             if t not in ALL_TOOLS:
                 ALL_TOOLS.append(t)
                 
        sys_prompt = "Evaluation script: scripts/evaluation.py - evaluates MCP servers by running test questions against them using Claude\n- Connection handling: scripts/connections.py - supports stdio, SSE, and streamable HTTP connections via MCPConnection base class\n- Dependencies: anthropic>=0.39.0, mcp>=1.1.0\n- Evaluation prompt requires: 1) Use available tools to complete task, 2) Provide summary in <summary> tags, 3) Provide feedback on tools in <feedback> tags, 4) Provide final response in <response> tags\n- Summary requirements in evaluation: explain steps taken, which tools used in what order and why, inputs provided, outputs received, summary of how outputs informed next steps"
        try:
            result = asyncio.run(run_agent(
                str(input_data), system_prompt=sys_prompt,
                origin="skill_pipeline", max_steps=100
            ))
            return Verdict(kind=VerdictKind.PASS, output={"text": result, "context": input_data})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))
