# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.mcp_server.tool_provider.py"
"""omnicompany MCP server — exposes read-only tools to Claude Code via stdio.

Run: `python -m omnicompany.dashboard.cc_wrapper.mcp_server`

Wired into Claude Code via:
    {"mcpServers": {"omnicompany": {"command": "python",
                                    "args": ["-m", "omnicompany.dashboard.cc_wrapper.mcp_server"]}}}

Tool surface is intentionally small (≤10) and read-only. Internal mutations
remain Claude's job (Edit/Write); we just give it visibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import mcp.types as mt
from mcp.server import Server
from mcp.server.stdio import stdio_server

# Suppress server's own log spam — they go to stderr but Claude shows them.
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("cc_wrapper.mcp")

# ── reuse existing dashboard scan funcs (no HTTP indirection) ────────────────

from omnicompany.dashboard.controlplane import notes as notes_api
from omnicompany.dashboard.controlplane import plans as plans_api
from omnicompany.dashboard.controlplane import workers as workers_api
from omnicompany.dashboard.controlplane import catalogue as catalogue_api


def _project_root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def _events_db() -> Path:
    return _project_root() / "data" / "ide_events.db"


# ── tool implementations ─────────────────────────────────────────────────────


def tool_list_workers(filter: str | None = None, limit: int = 50) -> dict:
    items = workers_api._scan()
    if filter:
        f = filter.lower()
        items = [it for it in items if f in it["id"].lower() or f in it.get("name", "").lower()]
    return {"items": items[:limit], "total_unfiltered": len(workers_api._scan())}


def tool_get_worker(id: str) -> dict:
    items = workers_api._scan()
    found = next((it for it in items if it["id"] == id), None)
    if not found:
        return {"error": f"worker not found: {id}"}
    pkg_dir = Path(found["file_path"]).parent
    design_md = pkg_dir / "DESIGN.md"
    out = dict(found)
    out["design_md"] = design_md.read_text(encoding="utf-8") if design_md.is_file() else None
    return out


def tool_list_teams(filter: str | None = None, limit: int = 50) -> dict:
    items = catalogue_api._scan_teams_cached(catalogue_api._root_token())
    if filter:
        f = filter.lower()
        items = [it for it in items if f in it["id"].lower() or f in it.get("name", "").lower()]
    return {"items": items[:limit], "total_unfiltered": len(catalogue_api._scan_teams_cached(catalogue_api._root_token()))}


def tool_get_team(id: str) -> dict:
    return catalogue_api._get_one(catalogue_api._scan_teams_cached, "team", id)


def tool_list_materials(filter: str | None = None, limit: int = 50) -> dict:
    items = catalogue_api._scan_materials_cached(catalogue_api._root_token())
    if filter:
        f = filter.lower()
        items = [it for it in items if f in it["id"].lower() or f in it.get("name", "").lower()]
    return {"items": items[:limit], "total_unfiltered": len(catalogue_api._scan_materials_cached(catalogue_api._root_token()))}


def tool_list_plans(filter: str | None = None, archived: bool = False, limit: int = 100) -> dict:
    items = plans_api._scan()
    if not archived:
        items = [it for it in items if not it.get("archived")]
    if filter:
        f = filter.lower()
        items = [it for it in items
                 if f in it["id"].lower() or f in (it.get("topic", "") or "").lower()]
    # trim noisy `files` list to count only
    trimmed = [{k: v for k, v in it.items() if k != "files"} for it in items[:limit]]
    return {"items": trimmed, "total_unfiltered": len(plans_api._scan())}


def tool_get_plan(id: str) -> dict:
    pr = plans_api._plans_root()
    folder = pr / id
    if not folder.is_dir():
        return {"error": f"plan not found: {id}"}
    plan_md = folder / "plan.md"
    plan_md_content = plan_md.read_text(encoding="utf-8") if plan_md.is_file() else None
    files = []
    try:
        for f in sorted(folder.rglob("*.md")):
            files.append(str(f.relative_to(folder)).replace(os.sep, "/"))
    except OSError:
        pass
    m = plans_api.DATE_RE.match(folder.name)
    return {
        "id": id,
        "topic": m.group(2) if m else folder.name,
        "date": m.group(1) if m else None,
        "folder_path": str(folder.relative_to(_project_root())).replace(os.sep, "/"),
        "plan_md": plan_md_content,
        "md_files": files,
    }


def tool_search_notes(query: str, limit: int = 20) -> dict:
    if not query:
        return {"items": [], "total": 0}
    docs = notes_api._docs_root()
    items = notes_api._scan_cached(notes_api._docs_token())
    ql = query.lower()
    hits = []
    for it in items:
        try:
            text = (docs / (it["id"] + ".md")).read_text(encoding="utf-8")
        except OSError:
            continue
        if ql in text.lower() or ql in it["id"].lower():
            idx = text.lower().find(ql)
            snippet = text[max(0, idx - 60):idx + 120].replace("\n", " ") if idx >= 0 else text[:180]
            hits.append({"id": it["id"], "title": it["title"], "snippet": snippet})
            if len(hits) >= limit:
                break
    return {"items": hits, "total_matched": len(hits)}


def tool_read_note(id: str) -> dict:
    docs = notes_api._docs_root()
    p = docs / (id + ".md")
    if not p.is_file():
        return {"error": f"note not found: {id}"}
    return {"id": id, "content": p.read_text(encoding="utf-8")}


def tool_recent_traces(limit: int = 20) -> dict:
    db = _events_db()
    if not db.is_file():
        return {"items": [], "note": "no events.db yet"}
    try:
        conn = sqlite3.connect(str(db), timeout=2.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT trace_id, MIN(timestamp) AS started_at, MAX(timestamp) AS ended_at, "
            "COUNT(*) AS event_count "
            "FROM events GROUP BY trace_id ORDER BY ended_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"items": [], "error": str(e)}
    return {"items": [dict(r) for r in rows]}


def tool_get_trace(trace_id: str, limit: int = 200) -> dict:
    db = _events_db()
    if not db.is_file():
        return {"error": "no events.db"}
    try:
        conn = sqlite3.connect(str(db), timeout=2.0)
        rows = conn.execute(
            "SELECT data FROM events WHERE trace_id=? ORDER BY timestamp LIMIT ?",
            (trace_id, limit),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    events = []
    for (raw,) in rows:
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return {"trace_id": trace_id, "events": events, "count": len(events)}


# ── tool registration ────────────────────────────────────────────────────────

TOOLS: list[mt.Tool] = [
    mt.Tool(name="omni_list_workers", description="List omnicompany workers (308 worker.py files). Optional substring filter on id/name.",
            inputSchema={"type": "object", "properties": {
                "filter": {"type": "string", "description": "case-insensitive substring on id/name"},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500}}}),
    mt.Tool(name="omni_get_worker", description="Get one worker's metadata + DESIGN.md content (if present).",
            inputSchema={"type": "object", "required": ["id"], "properties": {
                "id": {"type": "string", "description": "Worker id like `domains/voxelcraft/block/workers/block_designer`"}}}),
    mt.Tool(name="omni_list_teams", description="List Team definitions (49 `team*.py` files under packages/).",
            inputSchema={"type": "object", "properties": {
                "filter": {"type": "string"}, "limit": {"type": "integer", "default": 50}}}),
    mt.Tool(name="omni_get_team", description="Get one team's full record (source + DESIGN.md).",
            inputSchema={"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}),
    mt.Tool(name="omni_list_materials", description="List Format/Material definitions (formats.py + materials.py).",
            inputSchema={"type": "object", "properties": {
                "filter": {"type": "string"}, "limit": {"type": "integer", "default": 50}}}),
    mt.Tool(name="omni_list_plans", description="List active plans under docs/plans/. Pass archived=true to include archive.",
            inputSchema={"type": "object", "properties": {
                "filter": {"type": "string"}, "archived": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100}}}),
    mt.Tool(name="omni_get_plan", description="Get a plan's plan.md content + md file list.",
            inputSchema={"type": "object", "required": ["id"], "properties": {
                "id": {"type": "string", "description": "Plan id like `_infra/[2026-05-01]WEB-FOUNDATION`"}}}),
    mt.Tool(name="omni_search_notes", description="Substring full-text search across docs/**/*.md. Returns id+title+snippet.",
            inputSchema={"type": "object", "required": ["query"], "properties": {
                "query": {"type": "string"}, "limit": {"type": "integer", "default": 20}}}),
    mt.Tool(name="omni_read_note", description="Read one note's full content by id (without .md, e.g. `standards/terminology`).",
            inputSchema={"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}),
    mt.Tool(name="omni_recent_traces", description="Recent trace_ids from event bus (newest first).",
            inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}),
    mt.Tool(name="omni_get_trace", description="All events of one trace_id, ordered by timestamp.",
            inputSchema={"type": "object", "required": ["trace_id"], "properties": {
                "trace_id": {"type": "string"}, "limit": {"type": "integer", "default": 200}}}),
]

DISPATCH = {
    "omni_list_workers": tool_list_workers,
    "omni_get_worker": tool_get_worker,
    "omni_list_teams": tool_list_teams,
    "omni_get_team": tool_get_team,
    "omni_list_materials": tool_list_materials,
    "omni_list_plans": tool_list_plans,
    "omni_get_plan": tool_get_plan,
    "omni_search_notes": tool_search_notes,
    "omni_read_note": tool_read_note,
    "omni_recent_traces": tool_recent_traces,
    "omni_get_trace": tool_get_trace,
}


# ── server ────────────────────────────────────────────────────────────────────


def _build_server() -> Server:
    server = Server("omnicompany")

    @server.list_tools()
    async def _list_tools() -> list[mt.Tool]:
        return TOOLS

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[mt.TextContent]:
        fn = DISPATCH.get(name)
        if fn is None:
            return [mt.TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]
        try:
            result = fn(**(arguments or {}))
        except TypeError as e:
            return [mt.TextContent(type="text", text=json.dumps({"error": f"bad args: {e}"}))]
        except Exception as e:
            logger.exception("tool %s crashed", name)
            return [mt.TextContent(type="text", text=json.dumps({"error": f"tool crashed: {e}"}))]
        return [mt.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

    return server


async def amain() -> None:
    server = _build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
