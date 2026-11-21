# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""MCP 工具集 + ListPeers · 第九波 (2026-05-04).

包含: MCPRouter / McpAuthRouter / ListMcpResourcesRouter / ReadMcpResourceRouter / ListPeersRouter

omnicompany 暂无生产级 MCP 集成, 这些工具都是 schema 完整的 stub:
  - 调用走 ctx.mcp_client (Worker 注入); 没有则要求 dry_run
  - 干跑模式 OMNI_MCP_DRY_RUN=1 返 mock
"""
from __future__ import annotations

import json
import logging
import os
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


def _require_client(ctx: ToolContext, attr: str = "mcp_client"):
    if os.environ.get("OMNI_MCP_DRY_RUN") == "1":
        return None  # dry-run 信号
    client = getattr(ctx, attr, None)
    if client is None:
        raise ToolExecutionError(
            f"no ctx.{attr} injected. Worker must provide MCP client. "
            f"For offline testing set OMNI_MCP_DRY_RUN=1."
        )
    return client


# ─── MCPRouter · 调任意 MCP 工具 ────────────────────────────────


class MCPRouter(SingleToolRouter):
    """Call a tool on a Model Context Protocol (MCP) server."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("*",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("*",)

    TOOL_NAME: ClassVar[str] = "MCP"
    DESCRIPTION: ClassVar[str] = (
        "Call a tool exposed by an MCP (Model Context Protocol) server.\n"
        "\n"
        "- server: registered MCP server name\n"
        "- tool: server's tool name\n"
        "- params: JSON-compatible argument dict per tool's schema"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "tool": {"type": "string"},
            "params": {"type": "object"},
        },
        "required": ["server", "tool"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        server = (args.get("server") or "").strip()
        tool = (args.get("tool") or "").strip()
        params = args.get("params") or {}
        if not server or not tool:
            raise ToolExecutionError("server and tool are required")

        client = _require_client(ctx)
        if client is None:  # dry-run
            return json.dumps({
                "server": server, "tool": tool, "params": params,
                "result": "(mock MCP response)", "dry_run": True,
            }, ensure_ascii=False)
        try:
            result = client.call_tool(server=server, tool=tool, params=params)
        except Exception as e:
            raise ToolExecutionError(f"MCP {server}/{tool} failed: {e}")
        return json.dumps(result, ensure_ascii=False, default=str)


# ─── McpAuthRouter · MCP 认证 ───────────────────────────────────


class McpAuthRouter(SingleToolRouter):
    """Authenticate with an MCP server (initial auth or re-auth flow)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "McpAuth"
    DESCRIPTION: ClassVar[str] = (
        "Authenticate with an MCP server. Returns auth status / OAuth URL if interactive."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "action": {"type": "string", "enum": ["start", "complete", "status"]},
            "code": {"type": "string", "description": "OAuth code (for action='complete')"},
        },
        "required": ["server", "action"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        server = (args.get("server") or "").strip()
        action = args.get("action", "")
        if not server:
            raise ToolExecutionError("server is required")
        if action not in ("start", "complete", "status"):
            raise ToolExecutionError(f"action must be start/complete/status, got {action!r}")

        client = _require_client(ctx)
        if client is None:
            return json.dumps({
                "server": server, "action": action,
                "status": "(mock auth)", "dry_run": True,
            }, ensure_ascii=False)

        try:
            if action == "start":
                result = client.start_auth(server=server)
            elif action == "complete":
                code = (args.get("code") or "").strip()
                if not code:
                    raise ToolExecutionError("complete requires code")
                result = client.complete_auth(server=server, code=code)
            else:
                result = client.auth_status(server=server)
        except Exception as e:
            raise ToolExecutionError(f"McpAuth {action} failed: {e}")
        return json.dumps(result, ensure_ascii=False, default=str)


# ─── ListMcpResourcesRouter ─────────────────────────────────────


class ListMcpResourcesRouter(SingleToolRouter):
    """List resources exposed by an MCP server."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "ListMcpResources"
    DESCRIPTION: ClassVar[str] = "List resources exposed by an MCP server."
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {"server": {"type": "string"}},
        "required": ["server"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        server = (args.get("server") or "").strip()
        if not server:
            raise ToolExecutionError("server is required")
        client = _require_client(ctx)
        if client is None:
            return json.dumps({
                "server": server,
                "resources": [{"uri": "mock://resource1", "name": "Mock 1"}],
                "dry_run": True,
            }, ensure_ascii=False)
        try:
            resources = client.list_resources(server=server)
        except Exception as e:
            raise ToolExecutionError(f"ListMcpResources failed: {e}")
        return json.dumps(resources, ensure_ascii=False, default=str)


# ─── ReadMcpResourceRouter ──────────────────────────────────────


class ReadMcpResourceRouter(SingleToolRouter):
    """Read a specific resource from an MCP server by URI."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "ReadMcpResource"
    DESCRIPTION: ClassVar[str] = "Fetch a resource from an MCP server."
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "uri": {"type": "string"},
        },
        "required": ["server", "uri"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        server = (args.get("server") or "").strip()
        uri = (args.get("uri") or "").strip()
        if not server or not uri:
            raise ToolExecutionError("server and uri are required")
        client = _require_client(ctx)
        if client is None:
            return json.dumps({
                "server": server, "uri": uri,
                "content": "(mock resource content)", "dry_run": True,
            }, ensure_ascii=False)
        try:
            content = client.read_resource(server=server, uri=uri)
        except Exception as e:
            raise ToolExecutionError(f"ReadMcpResource failed: {e}")
        return json.dumps(content, ensure_ascii=False, default=str)


# ─── ListPeersRouter ────────────────────────────────────────────


class ListPeersRouter(SingleToolRouter):
    """List peer agents currently connected to the same coordination layer."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "ListPeers"
    DESCRIPTION: ClassVar[str] = (
        "List peer agents currently active in the same coordination/team space.\n"
        "\n"
        "Useful for multi-agent setups where agents discover each other to delegate.\n"
        "Returns: name, role, status (active/idle/done), last seen."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        if os.environ.get("OMNI_LIST_PEERS_DRY_RUN") == "1":
            return json.dumps([
                {"name": "agent-1", "role": "explorer", "status": "active", "last_seen": "now"},
                {"name": "agent-2", "role": "writer", "status": "idle", "last_seen": "5m ago"},
            ], ensure_ascii=False)
        registry = getattr(ctx, "peer_registry", None)
        if registry is None:
            raise ToolExecutionError(
                "no ctx.peer_registry. For offline tests set OMNI_LIST_PEERS_DRY_RUN=1."
            )
        try:
            peers = registry.list_active() if hasattr(registry, "list_active") else list(registry)
        except Exception as e:
            raise ToolExecutionError(f"peer_registry.list_active failed: {e}")
        return json.dumps(peers, ensure_ascii=False, default=str)
