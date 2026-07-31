# [OMNI] origin=claude-code domain=services/absorption/tools.py ts=2026-04-08T12:00:00Z
# [OMNI] material_id="material:learning.absorption.agent_session_toolset.py"
"""absorption.tools — LandmarkPicker AgentNodeLoop 的会话级工具集。

设计模仿 packages/domains/voxelcraft/routers/mod_explorer_agent.py:
- 工具是"会话绑定闭包", 每次 run 重建一组 ToolDefinition
- 会话状态放模块级 dict, 按 session_id 索引
- 结束后 extract_result() 从 state 提取 landmarks/sketches/gaps

本模块提供 8 个工具:
  1. gh_tree_list          — 列出某路径下的文件/子目录 (非递归)
  2. gh_file_read          — 读一个 GitHub 上的文件内容
  3. omni_capabilities     — 查询 OmniCompany 自身已有的能力快照
  4. think                 — 纯思考 (内置 ThinkTool 复用)
  5. submit_landmark       — 提交一个 evidence-backed landmark
  6. submit_landscape_sketch — 提交一个 repo 的速写画像
  7. submit_capability_gap — 提交一个真实对照出的 gap
  8. finish                — 终止循环 (内置 FinishTool 复用)

所有 submit_* 工具强制要求:
  - 引用至少一个真实读过的 file_path (landmark)
  - 或引用 omnicompany_snapshot 中的真实条目 (gap)
这是 L4 "证据链到行级" 的执行点。

置信度标记 (L7): 每个提交项必须带 confidence ∈ {high, medium, low} + reason。
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.runtime.exec.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


@dataclass
class SessionTool:
    """Session-bound tool descriptor used by LandmarkPicker wrapper routers."""

    name: str
    description: str
    input_schema: dict
    is_concurrency_safe: bool = False
    is_readonly: bool = False
    handler: Callable[[dict, ToolExecutor | None, ToolContext], str] | None = None

    def to_api_spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def call(self, args: dict, executor: ToolExecutor | None, ctx: ToolContext) -> str:
        if self.handler is None:
            return ""
        return self.handler(args, executor, ctx)


# ═══════════════════════════════════════════════════════════
# 会话状态 — 模块级 dict, 按 sess_id 索引
# ═══════════════════════════════════════════════════════════

_SESSION_STATE: dict[str, dict] = {}
"""Keyed by session_id (id(router) + counter)."""


def new_session(
    sess_id: str,
    facade_cards: list[dict],
    omni_snapshot: dict,
    upstream_input: dict | None = None,
) -> dict:
    """为一次 LandmarkPicker 运行创建新会话状态。

    upstream_input: 原始 input_data 字典 (用于保留 absorption_id/profile/repos 等
    向下游传递的关键字段), 由 LandmarkPicker.build_initial_messages 传入。
    """
    state = {
        "facade_cards": facade_cards,
        "omni_snapshot": omni_snapshot,
        "upstream_input": upstream_input or {},
        # 探索状态 (覆盖度审计依据)
        "listed_paths": [],       # list[{owner, name, path, count}]
        "read_files": [],          # list[{owner, name, path, lines_read, excerpt_hash}]
        # 产出状态 (最终结果)
        "landmarks": [],           # 每项含 evidence block
        "sketches": [],            # 每个 repo 一份
        "gaps": [],                # 每项对照 omni_snapshot
        "finish_summary": None,    # LLM 调 finish 时提交
    }
    _SESSION_STATE[sess_id] = state
    return state


def get_session(sess_id: str) -> dict | None:
    return _SESSION_STATE.get(sess_id)


def pop_session(sess_id: str) -> dict | None:
    return _SESSION_STATE.pop(sess_id, None)


# ═══════════════════════════════════════════════════════════
# gh CLI 底层 (复用 routers.py 风格, 独立副本避免循环 import)
# ═══════════════════════════════════════════════════════════

def _gh_api_json(path: str, timeout: int = 30) -> Any:
    """调 gh CLI, 返回解析后的 JSON。失败抛 RuntimeError。"""
    cmd = ["gh", "api", path]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise RuntimeError("gh CLI 未安装或不在 PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"gh api {path} 超时 ({timeout}s)") from e
    if result.returncode != 0:
        first_err = (result.stderr or "").strip().splitlines()[:1]
        raise RuntimeError(
            f"gh api {path} 失败 (rc={result.returncode}): {first_err[0] if first_err else 'unknown'}"
        )
    return json.loads(result.stdout) if result.stdout else None


def _compute_union_coverage(segments: list[dict]) -> int:
    """Given a list of {start, end} line ranges, return the union line count.

    Used so that repeated gh_file_read calls with different offsets accumulate
    correctly instead of double-counting overlap.
    """
    if not segments:
        return 0
    sorted_segs = sorted(segments, key=lambda s: s.get("start", 0))
    merged: list[list[int]] = []
    for s in sorted_segs:
        start = int(s.get("start", 0))
        end = int(s.get("end", start))
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


# ═══════════════════════════════════════════════════════════
# 工具工厂 — 每个 session 绑定一组工具闭包
# ═══════════════════════════════════════════════════════════

def make_tools_for_session(sess_id: str) -> list[SessionTool]:
    """为指定 session 构建工具列表。

    工具通过闭包捕获 sess_id, 所有副作用写到 _SESSION_STATE[sess_id]。
    """

    def _state() -> dict:
        return _SESSION_STATE[sess_id]

    # ───────────────────────────────────────────────────────
    # 1. gh_tree_list
    # ───────────────────────────────────────────────────────
    GhTreeListTool = SessionTool(
        name="gh_tree_list",
        description=(
            "List files/directories at a specific path inside a GitHub repository (non-recursive). "
            "Returns a JSON array of {name, type, path, size}. Use this to explore a specific "
            "sub-directory you're interested in. For repo root, use path=\"\". For a subdir, "
            "use path=\"codex-rs/core\" etc. The recursive whole-tree listing is already in "
            "your context under facade_card.tree_recursive — only call this tool if you need "
            "fresh or deeper data than the pre-fetched tree."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repo owner, e.g. 'openai'"},
                "name": {"type": "string", "description": "Repo name, e.g. 'codex'"},
                "path": {
                    "type": "string",
                    "description": "Path within repo, empty string for root",
                    "default": "",
                },
            },
            "required": ["owner", "name"],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _gh_tree_list_call(args: dict, executor: ToolExecutor, ctx: ToolContext) -> str:
        owner = args.get("owner", "")
        name = args.get("name", "")
        path = (args.get("path") or "").strip("/ ")
        if not owner or not name:
            return "Error: owner and name are required"
        api_path = f"repos/{owner}/{name}/contents"
        if path:
            api_path += f"/{path}"
        try:
            data = _gh_api_json(api_path)
        except RuntimeError as e:
            return f"Error: {e}"
        if data is None:
            return "Error: empty response"
        if not isinstance(data, list):
            # Single-file response
            return json.dumps(
                [{"name": data.get("name"), "type": data.get("type"), "path": data.get("path"), "size": data.get("size")}],
                ensure_ascii=False,
            )
        items = [
            {"name": d.get("name"), "type": d.get("type"), "path": d.get("path"), "size": d.get("size", 0)}
            for d in data
            if isinstance(d, dict)
        ]
        _state()["listed_paths"].append({
            "owner": owner,
            "name": name,
            "path": path or "<root>",
            "count": len(items),
        })
        return json.dumps({"path": path or "<root>", "items": items}, ensure_ascii=False)

    GhTreeListTool.call = _gh_tree_list_call  # type: ignore[assignment]

    # ───────────────────────────────────────────────────────
    # 2. gh_file_read
    # ───────────────────────────────────────────────────────
    GhFileReadTool = SessionTool(
        name="gh_file_read",
        description=(
            "Read the content of a single file from a GitHub repository. Returns line-numbered "
            "content (cat -n style). Use this whenever you need to make an evidence-backed claim. "
            "Depth matters more than breadth: for files ≤400 lines, ALWAYS read the entire file "
            "(just leave max_lines at default 1200). For files >400 lines, the default max_lines=1200 "
            "gives you a substantial chunk; if the default is not enough for a tier-1 decision, "
            "call the tool again with offset=<line> to read further segments. Tier-1 landmarks "
            "require substantial reading of the evidence file (either full read or at least half "
            "the file). Use offset to drill into specific sections of large files (e.g. read 1-1200, "
            "then 1200-2400 if needed)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "name": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Full path within the repo, e.g. 'codex-rs/core/src/agent.rs'",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "Line number to start reading from (0-indexed). Use this to read later segments of large files.",
                },
                "max_lines": {
                    "type": "integer",
                    "default": 1200,
                    "minimum": 20,
                    "maximum": 4000,
                    "description": "Max lines to read. Default 1200 is usually enough for a substantial read. Raise up to 4000 for very large single-file studies.",
                },
            },
            "required": ["owner", "name", "path"],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _gh_file_read_call(args: dict, executor: ToolExecutor, ctx: ToolContext) -> str:
        owner = args.get("owner", "")
        name = args.get("name", "")
        path = args.get("path", "")
        max_lines = int(args.get("max_lines", 1200))
        offset = int(args.get("offset", 0))
        if not owner or not name or not path:
            return "Error: owner, name, path are required"
        try:
            data = _gh_api_json(f"repos/{owner}/{name}/contents/{path}")
        except RuntimeError as e:
            return f"Error: {e}"
        if not isinstance(data, dict):
            return f"Error: path {path!r} is not a single file (got {type(data).__name__})"
        if data.get("type") != "file":
            return f"Error: path {path!r} is {data.get('type')}, not a file"
        # Size guard — 1MB cap (Rust files up to this size are plausible for deep dives)
        size = data.get("size", 0)
        if size > 1024 * 1024:
            return f"Error: file too large ({size} bytes); refuse to read files >1MB"
        content_b64 = data.get("content", "")
        try:
            content = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception as e:
            return f"Error: base64 decode failed: {e}"
        lines = content.split("\n")
        total = len(lines)
        start = min(offset, total)
        end = min(start + max_lines, total)
        segment = lines[start:end]
        # Line numbers must be 1-indexed to match the user's mental model.
        numbered = "\n".join(f"{i + 1:5d}\t{line}" for i, line in enumerate(segment, start=start))

        # Aggregate per-file read history. If called twice, accumulate lines_read
        # across the union of ranges (approximation: sum of segment lengths capped at total).
        read_log = _state()["read_files"]
        existing = next(
            (r for r in read_log if r.get("owner") == owner and r.get("name") == name and r.get("path") == path),
            None,
        )
        if existing:
            # track all segments seen so coverage audit knows how much we covered
            existing.setdefault("segments", []).append({"start": start, "end": end})
            union_covered = _compute_union_coverage(existing["segments"])
            existing["lines_read"] = min(union_covered, total)
            existing["total_lines"] = total
            existing["size_bytes"] = size
        else:
            read_log.append({
                "owner": owner,
                "name": name,
                "path": path,
                "total_lines": total,
                "lines_read": len(segment),
                "size_bytes": size,
                "segments": [{"start": start, "end": end}],
            })

        shown_range = f"lines {start + 1}-{end}" if total > 0 else "empty file"
        header = (
            f"=== {owner}/{name}:{path} "
            f"(total {total} lines, showing {shown_range}) ===\n"
        )
        return header + numbered

    GhFileReadTool.call = _gh_file_read_call  # type: ignore[assignment]

    # ───────────────────────────────────────────────────────
    # 3. omni_capabilities (query in-memory snapshot)
    # ───────────────────────────────────────────────────────
    OmniCapabilitiesTool = SessionTool(
        name="omni_capabilities",
        description=(
            "Query OmniCompany's own current capabilities snapshot. This tells you what "
            "OmniCompany ALREADY HAS so you can honestly judge whether an external landmark "
            "is genuinely new or overlaps with existing code. Categories:\n"
            "  - 'packages': all registered business/service/domain packages + docstrings\n"
            "  - 'registered_pipelines': all names from `omni pipelines` (e.g. 'absorption-survey')\n"
            "  - 'routers': list of existing Router class names found under src/omnicompany/\n"
            "  - 'tools': built-in AgentNodeLoop tools (ReadFileTool, BashTool, ...)\n"
            "  - 'core_modules': list of core/*.py and runtime/*.py module paths\n"
            "  - 'all': everything above\n"
            "You MUST call this tool at least once before submitting any capability_gap, "
            "and cite the specific matched OmniCompany piece in your gap.omnicompany_status."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["packages", "registered_pipelines", "routers", "tools", "core_modules", "all"],
                    "default": "all",
                },
                "filter": {
                    "type": "string",
                    "description": "Optional substring filter (case-insensitive)",
                    "default": "",
                },
            },
            "required": ["category"],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _omni_capabilities_call(args: dict, executor: ToolExecutor, ctx: ToolContext) -> str:
        category = args.get("category", "all")
        filt = (args.get("filter") or "").lower().strip()
        snapshot = _state()["omni_snapshot"]
        if not snapshot:
            return "Error: omni_snapshot not loaded in session state"

        def _filter_list(lst):
            if not filt:
                return lst
            return [x for x in lst if (filt in str(x).lower())]

        def _filter_dict(d):
            if not filt:
                return d
            return {k: v for k, v in d.items() if filt in k.lower() or filt in str(v).lower()}

        # 2026-04-18 零容忍截断：移除 [:6000]/[:8000]。若 agent 需要精准子集，用 category+filt 参数；
        # "all" 返回带 _sample 后缀的样例（命名明示）+ count，agent 看完可再按 category 细查。
        if category == "packages":
            return json.dumps(_filter_dict(snapshot.get("packages", {})), ensure_ascii=False, indent=2)
        if category == "registered_pipelines":
            return json.dumps(_filter_list(snapshot.get("registered_pipelines", [])), ensure_ascii=False)
        if category == "routers":
            return json.dumps(_filter_list(snapshot.get("routers", [])), ensure_ascii=False)
        if category == "tools":
            return json.dumps(_filter_list(snapshot.get("builtin_tools", [])), ensure_ascii=False)
        if category == "core_modules":
            return json.dumps(_filter_list(snapshot.get("core_modules", [])), ensure_ascii=False)
        # all —— 返回全量。_sample 字段含义退化为 _first_n（agent 可改调 category 查全量）。
        result = {
            "packages_count": len(snapshot.get("packages", {})),
            "packages": snapshot.get("packages", {}),
            "registered_pipelines": snapshot.get("registered_pipelines", []),
            "builtin_tools": snapshot.get("builtin_tools", []),
            "routers_count": len(snapshot.get("routers", [])),
            "routers": snapshot.get("routers", []),
            "core_modules_count": len(snapshot.get("core_modules", [])),
            "core_modules": snapshot.get("core_modules", []),
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    OmniCapabilitiesTool.call = _omni_capabilities_call  # type: ignore[assignment]

    # ───────────────────────────────────────────────────────
    # 4. submit_landmark
    # ───────────────────────────────────────────────────────
    SubmitLandmarkTool = SessionTool(
        name="submit_landmark",
        description=(
            "Record one evidence-backed landmark finding. Call this repeatedly to build up "
            "your candidate list (≤20 per repo, spread across tier 1/2/3). EVERY landmark "
            "MUST include an evidence block citing a file_path you actually read via "
            "gh_file_read. No confidence scores or ratings — instead, write honest prose. "
            "If evidence is thin, say so in prose inside why_interesting or why_this_evidence. "
            "For tier-1, the file_path must have been read substantially (half the file or "
            "600 lines minimum for files >400 lines, full read for files ≤400 lines)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "name": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Primary path for this landmark (file or directory)",
                },
                "why_interesting": {
                    "type": "string",
                    "description": (
                        "Honest prose: what this code/file does, what OmniCompany would gain "
                        "from absorbing it, and any caveats you noticed while reading. "
                        "3-5 sentences. No self-rating language."
                    ),
                },
                "tier": {"type": "integer", "enum": [1, 2, 3]},
                "evidence": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Exact file you read for evidence (may differ from path if path is a dir)",
                        },
                        "line_start": {"type": "integer", "minimum": 1},
                        "line_end": {"type": "integer", "minimum": 1},
                        "snippet": {
                            "type": "string",
                            "description": (
                                "Real code/docs from the file you read. Must be ≥5 lines. "
                                "Must be the actual content returned by gh_file_read, not paraphrased. "
                                "Quote the specific struct/function/section that supports your claim."
                            ),
                            "maxLength": 1500,
                        },
                        "why_this_evidence": {
                            "type": "string",
                            "description": (
                                "Prose: which concrete piece of the snippet proves your claim? "
                                "Name the specific struct / function / #[cfg] branch / config key. "
                                "Avoid vague phrases like 'shows X implementation'."
                            ),
                        },
                    },
                    "required": ["file_path", "snippet", "why_this_evidence"],
                },
                "compared_against_omnicompany": {
                    "type": "string",
                    "description": (
                        "Name the specific OmniCompany piece you compared this to: package "
                        "path / Router class / Tool name / core module path seen via "
                        "omni_capabilities. If you genuinely checked and found nothing, "
                        "write 'no match found for queries: X, Y'. Do NOT write 'none checked' "
                        "for tier-1 — tier-1 requires a real comparison."
                    ),
                },
            },
            "required": [
                "owner", "name", "path", "why_interesting", "tier",
                "evidence", "compared_against_omnicompany",
            ],
        },
        is_concurrency_safe=False,
        is_readonly=True,
    )

    def _submit_landmark_call(args: dict, executor: ToolExecutor, ctx: ToolContext) -> str:
        ev = args.get("evidence") or {}
        if not ev.get("file_path") or not ev.get("snippet"):
            return "Error: evidence.file_path and evidence.snippet are required"
        # Validate the file was actually read via gh_file_read
        read_paths = {r["path"] for r in _state()["read_files"]}
        if ev["file_path"] not in read_paths:
            return (
                f"Error: evidence.file_path {ev['file_path']!r} was NOT read via gh_file_read "
                f"in this session. Read it first, then submit. Read so far: {sorted(read_paths)[:10]}"
            )
        _state()["landmarks"].append(dict(args))
        return f"landmark recorded: tier={args['tier']} path={args['path']}"

    SubmitLandmarkTool.call = _submit_landmark_call  # type: ignore[assignment]

    # ───────────────────────────────────────────────────────
    # 5. submit_landscape_sketch
    # ───────────────────────────────────────────────────────
    SubmitLandscapeSketchTool = SessionTool(
        name="submit_landscape_sketch",
        description=(
            "Record one landscape sketch per repo. Call ONCE per unique owner/name. "
            "Synthesizes what this project is and how it structurally differs from "
            "OmniCompany. Must be grounded in files you actually read via gh_file_read — "
            "list those files explicitly. No confidence labels; if something is uncertain, "
            "write the uncertainty into the prose."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "name": {"type": "string"},
                "positioning": {
                    "type": "string",
                    "description": (
                        "2-4 sentences positioning the project: what it does, for whom, "
                        "and what architectural bets it makes. Prose, not bullet points."
                    ),
                },
                "core_abstractions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "what_it_does": {
                                "type": "string",
                                "description": "1-2 sentence prose description of the abstraction's role",
                            },
                            "evidence_file": {
                                "type": "string",
                                "description": "Which file in picker_read_files lets you make this claim",
                            },
                        },
                        "required": ["name", "what_it_does"],
                    },
                    "minItems": 1,
                    "maxItems": 6,
                    "description": "Key abstractions. Name precisely (e.g. 'Sandboxed child-process Exec Runtime' not 'execution').",
                },
                "diff_vs_omnicompany": {
                    "type": "string",
                    "description": (
                        "Prose comparison. Cite specific OmniCompany pieces from "
                        "omni_capabilities (package / Router / Tool / core module names). "
                        "Pattern: 'OmniCompany has X in Y; this project also has X but organized as Z; "
                        "this project additionally has W which OmniCompany lacks'. "
                        "If you are unsure about any comparison, write the uncertainty in prose."
                    ),
                },
                "files_relied_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths (from gh_file_read) that informed this sketch. Required — sketches without file backing are not allowed.",
                    "minItems": 1,
                },
            },
            "required": [
                "owner", "name", "positioning", "core_abstractions",
                "diff_vs_omnicompany", "files_relied_on",
            ],
        },
        is_concurrency_safe=False,
        is_readonly=True,
    )

    def _submit_sketch_call(args: dict, executor: ToolExecutor, ctx: ToolContext) -> str:
        state = _state()
        existing = {(s["owner"], s["name"]) for s in state["sketches"]}
        key = (args.get("owner"), args.get("name"))
        if key in existing:
            # Allow overwrite — take the last
            state["sketches"] = [s for s in state["sketches"] if (s["owner"], s["name"]) != key]
        state["sketches"].append(dict(args))
        return f"sketch recorded for {key[0]}/{key[1]}"

    SubmitLandscapeSketchTool.call = _submit_sketch_call  # type: ignore[assignment]

    # ───────────────────────────────────────────────────────
    # 6. submit_capability_gap
    # ───────────────────────────────────────────────────────
    SubmitCapabilityGapTool = SessionTool(
        name="submit_capability_gap",
        description=(
            "Record one capability gap — something the external repo has that OmniCompany "
            "LACKS or has only in a noticeably weaker form. MANDATORY pre-flight: you must "
            "run at least 3 omni_capabilities queries BEFORE submitting this gap, covering "
            "packages / routers (with filter) / builtin_tools or core_modules. Querying only "
            "core_modules misses packages/vendors/* and packages/services/* — this is a real "
            "trap that caused a false gap in the previous run. No confidence labels — if you "
            "are uncertain, write the uncertainty into the prose fields."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "The external repo owner"},
                "name": {"type": "string"},
                "gap_title": {
                    "type": "string",
                    "description": "Short name, e.g. 'Cross-platform sandboxed execution'",
                },
                "gap_description": {
                    "type": "string",
                    "description": (
                        "3-5 sentence prose: what the external repo has, why OmniCompany "
                        "would benefit, and any nuance (e.g. 'OmniCompany has SCATTER which "
                        "partially overlaps but differs in X')."
                    ),
                },
                "external_evidence": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "snippet": {
                            "type": "string",
                            "maxLength": 1500,
                            "description": "≥5 lines of real code from the file you read",
                        },
                        "why_this_proves_gap": {
                            "type": "string",
                            "description": (
                                "Prose: which concrete element of the snippet shows the "
                                "capability OmniCompany lacks?"
                            ),
                        },
                    },
                    "required": ["file_path", "snippet", "why_this_proves_gap"],
                },
                "omnicompany_current_state": {
                    "type": "string",
                    "description": (
                        "Prose. Quote specific OmniCompany names you found via "
                        "omni_capabilities (e.g. 'packages/vendors/mcp_builder exists and "
                        "handles X, but not Y'). If nothing matched, write 'no match found "
                        "for queries: X, Y, Z' explicitly listing what you searched."
                    ),
                },
                "omni_capabilities_queries_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "description": (
                        "Required ≥3 queries. Each entry is a short description like "
                        "'packages filter=mcp' or 'routers filter=sandbox'. Less than 3 "
                        "queries means your gap is a guess, not a verified gap."
                    ),
                },
            },
            "required": [
                "owner", "name", "gap_title", "gap_description",
                "external_evidence", "omnicompany_current_state",
                "omni_capabilities_queries_used",
            ],
        },
        is_concurrency_safe=False,
        is_readonly=True,
    )

    def _submit_gap_call(args: dict, executor: ToolExecutor, ctx: ToolContext) -> str:
        ev = args.get("external_evidence") or {}
        if not ev.get("file_path") or not ev.get("snippet"):
            return "Error: external_evidence.file_path and .snippet required"
        read_paths = {r["path"] for r in _state()["read_files"]}
        if ev["file_path"] not in read_paths:
            return (
                f"Error: external_evidence.file_path {ev['file_path']!r} was NOT read via "
                f"gh_file_read. Read it first or reference a file you actually read."
            )
        _state()["gaps"].append(dict(args))
        return f"gap recorded: {args['gap_title']}"

    SubmitCapabilityGapTool.call = _submit_gap_call  # type: ignore[assignment]

    # ───────────────────────────────────────────────────────
    # 7. Think (reuse built-in)
    # 8. Finish (reuse built-in; LLM also gives final summary text)
    # ───────────────────────────────────────────────────────

    return [
        GhTreeListTool,
        GhFileReadTool,
        OmniCapabilitiesTool,
        SubmitLandmarkTool,
        SubmitLandscapeSketchTool,
        SubmitCapabilityGapTool,
    ]
