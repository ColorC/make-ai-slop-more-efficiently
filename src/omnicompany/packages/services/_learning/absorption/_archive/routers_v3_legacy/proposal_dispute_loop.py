# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-15T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.proposal_dispute.agent_loop.py"
#
# ⚠ DEPRECATED (2026-04-18) — 继承旧 runtime.agent.agent_node_loop.AgentNodeLoop。阶段 C 会迁到 packages.services.agent.AgentNodeLoop
# 违规：LLMClient/ToolDefinition.call 直调 + 内存 list[dict] 传参（非 Format+bus）。
# 重构计划：omnifactory/docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md
# 禁止基于本类新增实现；Guardian 会监控违规。
"""proposal_dispute_loop — Stage 3 反馈回路（AgentNodeLoop）

用途：人审 pending_proposals.md 后写 dispute_feedback.md 提出异议/补充方向，
     本 loop agent 读 dispute + 当前 proposals + report + 可读 repo，产出修订版 proposals.

对标 absorption-module-driven 的 HumanFeedbackGate + FeedbackRouter 回路，但是针对 proposal
层级（不是 report 层级）。

数据流:
  pending_proposals.md + dispute_feedback.md + report.md + repo_local_path
      ↓ AgentNodeLoop（工具: local_list / local_read / local_grep /
                       submit_revised_proposals / finish）
      ↓
  revised_proposals.md（落盘，覆盖或追加）

遵循原则:
- 无预防性截断（report 全量、dispute 全量、proposals 全量传入）
- 宽松预算（max_turns=1000）
- agent 主动搜索（不是预加载所有 hermes 文件）
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.core.config import resolve_domain_data_dir
from omnicompany.core.guarded_write import write_file as _guarded_write
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

_MODEL = "qwen3.6-plus"


# ══════════════════════════════════════════════════════════════════════
# 工具构建（闭包式，绑定 session 状态）
# ══════════════════════════════════════════════════════════════════════

_sessions: dict[str, dict] = {}


def _new_session(sess_id: str, *, repo_local_path: str) -> None:
    _sessions[sess_id] = {
        "repo_local_path": repo_local_path,
        "revised_proposals": None,
    }


def _get_session(sess_id: str) -> dict:
    return _sessions.get(sess_id, {})


def _make_dispute_tools(sess_id: str) -> list:
    from omnicompany.runtime.agent.agent_loop_tools import (
        FinishTool, ThinkTool, ToolDefinition,
    )

    def _state() -> dict:
        return _sessions.get(sess_id) or {}

    # ── local_list ───────────────────────────────
    LocalListTool = ToolDefinition(
        name="local_list",
        description="List directory contents in the repo being analyzed.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path from repo root, '' for root"}},
            "required": ["path"],
        },
        is_concurrency_safe=True, is_readonly=True,
    )

    def _local_list_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        repo_root = Path(state["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\")
        target = repo_root / rel if rel else repo_root
        if not target.exists():
            return f"Error: '{rel}' not found"
        if not target.is_dir():
            return f"Error: '{rel}' is not a directory"
        entries = []
        for p in sorted(target.iterdir()):
            kind = "dir" if p.is_dir() else "file"
            entries.append(f"{kind}\t{p.name}")
        return "\n".join(entries) if entries else "(empty)"

    LocalListTool.call = _local_list_call  # type: ignore[assignment]

    # ── local_read ──────────────────────────────
    LocalReadTool = ToolDefinition(
        name="local_read",
        description=(
            "Read a file from the repo. Returns line-numbered content. "
            "Use offset+limit for large files."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 0, "minimum": 0},
                "limit": {"type": "integer", "default": 800, "minimum": 10, "maximum": 2000},
            },
            "required": ["path"],
        },
        is_concurrency_safe=True, is_readonly=True,
    )

    def _local_read_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        repo_root = Path(state["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\")
        target = repo_root / rel
        if not target.exists():
            return f"Error: '{rel}' not found"
        if not target.is_file():
            return f"Error: '{rel}' is a directory"
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading {rel}: {e}"
        lines = content.splitlines()
        total = len(lines)
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 800)
        start = min(offset, total)
        end = min(start + limit, total)
        segment = lines[start:end]
        numbered = "\n".join(f"{i+1:5d}\t{ln}" for i, ln in enumerate(segment, start=start))
        header = f"=== {rel} ({total} lines, showing {start+1}-{end}) ===\n"
        return header + numbered

    LocalReadTool.call = _local_read_call  # type: ignore[assignment]

    # ── local_grep ──────────────────────────────
    LocalGrepTool = ToolDefinition(
        name="local_grep",
        description="Grep for a regex pattern in files under a subpath.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "subpath": {"type": "string", "default": ""},
                "glob": {"type": "string", "default": "*.py"},
                "max_matches": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            },
            "required": ["pattern"],
        },
        is_concurrency_safe=True, is_readonly=True,
    )

    def _local_grep_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        repo_root = Path(state["repo_local_path"])
        sub = (args.get("subpath") or "").strip("/\\")
        base = repo_root / sub if sub else repo_root
        glob = args.get("glob") or "*.py"
        try:
            pattern = re.compile(args.get("pattern") or "")
        except re.error as e:
            return f"Error: invalid regex: {e}"
        matches: list[str] = []
        max_matches = int(args.get("max_matches") or 50)
        for f in base.rglob(glob):
            if "__pycache__" in f.parts or ".venv" in f.parts:
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        rel = f.relative_to(repo_root).as_posix()
                        matches.append(f"{rel}:{i}: {line[:200]}")
                        if len(matches) >= max_matches:
                            return "\n".join(matches) + f"\n\n(hit max_matches={max_matches})"
            except Exception:
                continue
        return "\n".join(matches) if matches else "(no matches)"

    LocalGrepTool.call = _local_grep_call  # type: ignore[assignment]

    # ── submit_revised_proposals ────────────────
    SubmitTool = ToolDefinition(
        name="submit_revised_proposals",
        description=(
            "Submit the revised proposals list (replaces the current pending_proposals). "
            "Call this ONCE when you've addressed the dispute and produced the full revised list. "
            "Then call finish() to exit."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "proposals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "proposal_id": {"type": "string"},
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                            "omnifactory_status": {"type": "string",
                                "enum": ["缺失", "部分存在", "已有可改进"]},
                            "rationale": {"type": "string",
                                "description": "Why this is worth doing (tie to findings)."},
                            "hermes_reference": {"type": "string",
                                "description": "hermes file paths referenced."},
                            "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                            "change_from_previous": {"type": "string",
                                "description": "如 'new / unchanged / revised / removed'"},
                        },
                        "required": ["proposal_id", "title", "summary", "priority"],
                    },
                },
                "revision_summary": {"type": "string",
                    "description": "一段话总结本次修订相对原版做了什么变化（新增/修改/移除）。"},
            },
            "required": ["proposals", "revision_summary"],
        },
        # is_readonly=True 是对"文件系统/外部副作用"的声明——本工具只更新 session 字典
        is_concurrency_safe=True, is_readonly=True,
    )

    def _submit_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        proposals = args.get("proposals") or []
        state["revised_proposals"] = proposals
        state["revision_summary"] = args.get("revision_summary", "")
        return f"OK: {len(proposals)} revised proposals captured. Call finish() now."

    SubmitTool.call = _submit_call  # type: ignore[assignment]

    return [LocalListTool, LocalReadTool, LocalGrepTool, SubmitTool, ThinkTool, FinishTool]


# ══════════════════════════════════════════════════════════════════════
# SYSTEM prompt
# ══════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是 OmniCompany 提案修订 agent。

## 你的任务

人类审阅了 pending_proposals.md 后，在 dispute_feedback.md 写了异议或补充方向。
你需要读明白异议，结合 report 和原 proposals，修订出新的 proposals 列表。

## 可用工具

- local_list / local_read / local_grep — 主动去 repo 查证或找新的 findings
- submit_revised_proposals — 提交修订版 proposals（含 change_from_previous 字段标注变化）
- finish — 结束循环

## 原则

1. **人审意见是权威**。如果人说"漏了 X 领域"，就去 repo 查 X，补进来。
2. **不盲目全改**。原 proposals 里好的保留（change_from_previous="unchanged"）。
3. **功能层级**。只说需要什么能力，不指定具体文件路径。
4. **对照 OmniCompany 现状**。status 标 "缺失/部分存在/已有可改进"。
5. **证据驱动**。新加的提案必须 local_read 过对应 hermes 文件，说清楚在 hermes 哪里。

## 工作流程

1. 读 dispute_feedback + report + 原 proposals（已在首条消息中给出）
2. 识别异议类型（遗漏某领域 / 提案太泛 / 提案错误 / 优先级不对）
3. 按需 local_grep / local_read 去 hermes 查证
4. 调用 submit_revised_proposals
5. 调用 finish"""


# ══════════════════════════════════════════════════════════════════════
# Router + inner AgentNodeLoop
# ══════════════════════════════════════════════════════════════════════

class ProposalDisputeLoopRouter(Router):
    """Stage 3 反馈回路 — 基于人审 dispute 修订 proposals。

    FORMAT_IN:  absorption.proposal.dispute   (pending_proposals + dispute_feedback + report)
    FORMAT_OUT: absorption.proposal.revised   (修订后 proposals + 落盘路径)
    """

    DESCRIPTION = (
        "Stage 3 提案修订回路：AgentNodeLoop 读 dispute_feedback + 原 proposals + report，"
        "按需 local_read/local_grep hermes 查证，产出 revised_proposals.md"
    )
    FORMAT_IN = "absorption.proposal.dispute"
    FORMAT_OUT = "absorption.proposal.revised"

    _MODEL = _MODEL
    _sess_counter = 0

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL
        self._role = kwargs.get("role", "runtime_main")

    def _build_loop(self) -> Any:
        from omnicompany.runtime.agent.agent_node_loop import AgentNodeLoop
        from omnicompany.runtime.agent.agent_loop_config import (
            CompactConfig, LoopConfig, PermissionConfig,
        )

        class _DisputeLoop(AgentNodeLoop):
            DESCRIPTION = ProposalDisputeLoopRouter.DESCRIPTION
            FORMAT_IN = "absorption.proposal.dispute"
            FORMAT_OUT = "absorption.proposal.revised"
            SYSTEM_PROMPT: ClassVar[str] = _SYSTEM_PROMPT
            # 宽松预算：触发即 bug，不是任务需求
            LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
                max_turns=1000,
                compact=CompactConfig(
                    auto_compact_enabled=True,
                    auto_compact_threshold=0.85,
                ),
                permission=PermissionConfig(mode="readonly"),
            )
            TOOLS: ClassVar[list] = []

            def __init__(self_inner, outer: "ProposalDisputeLoopRouter", **kw: Any) -> None:
                kw.setdefault("role", outer._role)
                super().__init__(**kw)
                self_inner._outer = outer

            def build_initial_messages(self_inner, input_data: dict) -> list[dict]:
                from omnicompany.runtime.llm.llm import LLMClient

                repo_local_path = input_data.get("repo_local_path", "")
                if not repo_local_path:
                    raise ValueError("ProposalDisputeLoop: 缺少 repo_local_path")

                self_inner._outer._sess_counter += 1
                sess_id = f"pd_{self_inner._outer._sess_counter}"
                self_inner._sess_id = sess_id
                _new_session(sess_id, repo_local_path=repo_local_path)

                bound_tools = _make_dispute_tools(sess_id)
                self_inner._tools = bound_tools
                self_inner._tool_map = {t.name: t for t in self_inner._tools}
                tools_spec = [t.to_api_spec() for t in self_inner._tools]
                role = self_inner._outer._role
                self_inner._llm = LLMClient(role=role, tools=tools_spec)
                self_inner._llm_no_tools = LLMClient(role=role, tools=[])

                # 首条消息：注入所有上下文（全量，不截断）
                repo_name = input_data.get("repo_name", "unknown")
                pending_proposals = input_data.get("pending_proposals", "")
                dispute_feedback = input_data.get("dispute_feedback", "")
                report_md = input_data.get("report_md", "")

                content = f"""# 提案修订任务

Repo: {repo_name}

---

## 当前 pending_proposals.md（人类已审阅）

{pending_proposals}

---

## 人类异议 dispute_feedback.md

{dispute_feedback}

---

## 完整吸纳报告 report.md（供参考）

{report_md}

---

请开始：先理解异议类型，按需用工具 local_read/local_grep 查证 hermes-agent，
然后调用 submit_revised_proposals 提交修订版，最后 finish。"""

                return [{"role": "user", "content": content}]

            def extract_result(self_inner, final_text: str, messages: list[dict]) -> Verdict:
                sess = _get_session(self_inner._sess_id)
                revised = sess.get("revised_proposals")
                revision_summary = sess.get("revision_summary", "")

                if not revised:
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output={},
                        diagnosis="DisputeLoop 结束但未调用 submit_revised_proposals",
                    )

                return Verdict(
                    kind=VerdictKind.PASS,
                    output={
                        "revised_proposals": revised,
                        "revision_summary": revision_summary,
                        "final_message": final_text[:500],
                    },
                    confidence=0.85,
                    diagnosis=f"DisputeLoop: {len(revised)} revised proposals",
                )

        return _DisputeLoop(self)

    async def run(self, input_data: Any) -> Verdict:  # type: ignore[override]
        loop = self._build_loop()
        self._last_agent_loop = loop
        loop._outer_router_class = type(self).__name__
        result = await loop.run(input_data)

        # 落盘 revised_proposals.md
        if result.kind == VerdictKind.PASS:
            repo_name = input_data.get("repo_name", "unknown")
            revised = result.output.get("revised_proposals", [])
            revision_summary = result.output.get("revision_summary", "")
            repo_dir = resolve_domain_data_dir("absorption") / repo_name
            repo_dir.mkdir(parents=True, exist_ok=True)
            out_path = repo_dir / "revised_proposals.md"
            _write_revised_proposals(out_path, revised, revision_summary, repo_name)
            print(f"[ProposalDispute] {len(revised)} 条修订提案 → {out_path}")
            result.output["revised_proposals_path"] = str(out_path)

        return result


def _write_revised_proposals(path: Path, proposals: list[dict], summary: str, repo_name: str) -> None:
    """落盘人类可读的 revised_proposals.md."""
    lines = [
        f"# 修订版提案 — {repo_name}",
        "",
        f"**修订总结**: {summary}",
        "",
        f"共 {len(proposals)} 条：",
        "",
        "| ID | 标题 | 优先级 | status | 变化 |",
        "|---|---|---|---|---|",
    ]
    for p in proposals:
        pid = p.get("proposal_id", "?")
        title = p.get("title", "?")
        pri = p.get("priority", "?")
        st = p.get("omnifactory_status", "?")
        chg = p.get("change_from_previous", "?")
        lines.append(f"| {pid} | {title} | {pri} | {st} | {chg} |")
    lines += ["", "---", ""]
    for p in proposals:
        lines += [
            f"## {p.get('proposal_id','?')}: {p.get('title','?')}",
            "",
            f"**优先级**: {p.get('priority','?')} | **status**: {p.get('omnifactory_status','?')} | "
            f"**变化**: {p.get('change_from_previous','?')}",
            "",
            f"**摘要**: {p.get('summary','')}",
            "",
            f"**理由**: {p.get('rationale','')}",
            "",
            f"**hermes 参考**: {p.get('hermes_reference','')}",
            "",
        ]

    _guarded_write(
        path, "\n".join(lines),
        writer="internal-engine",
        domain="absorption",
        purpose="revised proposals after human dispute",
    )
