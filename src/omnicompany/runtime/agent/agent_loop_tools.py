# [OMNI] origin=codex domain=runtime/agent ts=2026-06-13T00:00:00Z
# [OMNI] material_id="material:runtime.agent.tool_context_compat.py"
"""Shared tool context for router-based agent tools.

The legacy tool registry was removed in the AgentNodeLoop Phase D cleanup.
This module stays only as the import home for ToolContext while callers
migrate their import path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolContext:
    """Execution context passed from AgentNodeLoop to SingleToolRouter tools."""

    cwd: str = ""
    project_root: str = ""
    permission_mode: str = "default"
    turn_number: int = 0
    trace_id: str = ""
    node_id: str = ""
    origin: str = "claude-code"
    agent_name: str = ""
    domain: str = ""


__all__ = ["ToolContext"]
