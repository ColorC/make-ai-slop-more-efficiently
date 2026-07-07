# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.controller.tools.py"
"""总控的 4 个自定义工具 (SingleToolRouter 子类).

落实块 1 · 总控本体 + 总控和人对接.

设计原则:
- 跟 omnicompany 已有 SOFT worker (team_supervisor / team_builder) 同套抽象
- LLM 调结构化工具产 typed 输出, 不用行内文本标记 + Python parser
- submit_response 是末步必调工具 (类比 submit_health_criteria)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import SingleToolRouter
from omnicompany.packages.services._core.agent.spawn_surface import (
    ENTRY_CONTROLLER_SPAWN,
    agent_spawn_metadata,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext

_log = logging.getLogger(__name__)


def _workspace_root() -> Path:
    # 委托到唯一权威 core.config.omni_workspace_root(), 不再硬编码 parents[N]
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(s: str, max_len: int = 60) -> str:
    """把任意字符串转 filesystem 安全的 filename 片段."""
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", s.strip())
    return clean[:max_len] or "untitled"


def _git_run(args: list[str], cwd: Path, *, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _find_git_root(start: Path) -> Path:
    result = _git_run(["rev-parse", "--show-toplevel"], start, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"not in a git repository: {start}")
    root = result.stdout.strip()
    if not root:
        raise RuntimeError(f"git root is empty for: {start}")
    return Path(root).resolve()


def _git_dirty_summary(repo_root: Path) -> str:
    result = _git_run(["status", "--porcelain"], repo_root, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def _make_spawn_worktree_name(plan_id: str) -> str:
    stem = _safe_filename(plan_id, max_len=48)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return _safe_filename(f"boss-{stem}-{stamp}", max_len=96)


def _create_spawn_worktree(ws: Path, plan_id: str, *, base_ref: str = "HEAD") -> dict[str, str]:
    repo_root = _find_git_root(ws)
    dirty = _git_dirty_summary(repo_root)
    if dirty:
        sample = "\n".join(dirty.splitlines()[:8])
        raise RuntimeError(
            "repository has uncommitted changes; refusing isolated spawn from stale HEAD:\n"
            + sample
        )

    name = _make_spawn_worktree_name(plan_id)
    branch = f"boss-sight/{name}"
    worktree_path = (repo_root / ".claude" / "worktrees" / "boss-sight" / name).resolve()
    if worktree_path.exists():
        raise RuntimeError(f"worktree path already exists: {worktree_path}")
    if worktree_path == repo_root:
        raise RuntimeError("worktree cwd assertion failed: isolated cwd equals repo root")

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = _git_run(
        ["worktree", "add", "-b", branch, str(worktree_path), base_ref],
        repo_root,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()[:500]}")
    if worktree_path.resolve() == repo_root.resolve():
        raise RuntimeError("worktree cwd assertion failed after creation")
    return {"path": str(worktree_path), "branch": branch, "base_ref": base_ref}


# ═══════════════════════════════════════════════════════════════════════
# submit_response — 末步必调工具
# 用户原话 §2.13 总控接收用户讨论. 此工具收总控给用户的回复.
# ═══════════════════════════════════════════════════════════════════════


class SubmitResponseRouter(SingleToolRouter):
    """末步工具: 提交总控给用户的回复 + 本轮元数据.

    ExtractResultRouter 从 messages 找最后一个 submit_response tool_use 取输入
    组 Verdict.
    """

    TOOL_NAME: ClassVar[str] = "submit_response"
    DESCRIPTION: ClassVar[str] = (
        "Final tool to call at the end of every turn. Submit your reply to the user, "
        "the wake-up kind classification, and any side actions taken this turn. "
        "Calling this tool ends the agent loop. Always call exactly once per turn."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "reply_to_user": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Plain-text reply shown to the user (Chinese or English). "
                    "Keep it concise — you are an executive secretary, not a reporter. "
                    "For user.message wake-up, this is required. For "
                    "subagent.completed / subagent.blocked wake-up, this can be a "
                    "short status notification (~ one sentence) or empty string if "
                    "you choose to stay silent."
                ),
            },
            "turn_summary": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "One-sentence summary of what you did this turn. Used by the "
                    "controller log for traceability and reflection."
                ),
            },
            "side_actions_taken": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "none",
                        "spawn_subagent",
                        "emit_event",
                        "propose_change",
                        "read_resources",
                    ],
                },
                "description": (
                    "Which side-action tools you called this turn (besides "
                    "submit_response). 'none' if no side actions."
                ),
            },
        },
        "required": ["reply_to_user", "turn_summary", "side_actions_taken"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        reply = args.get("reply_to_user", "")
        return f"response submitted ({len(reply)} chars to user)"


# ═══════════════════════════════════════════════════════════════════════
# spawn_subagent — 派 subagent 干活
# 用户原话 §2.2 + §5: 总控发 plan 给 subagent, goal 模式, ctx 上限默认 400k
# 块 1 阶段: 占位 _execute, 块 3 接调度器后真派发
# ═══════════════════════════════════════════════════════════════════════


# 块 3: 控制器 model 选择 / ctx 上限 → 收敛到 model_resolver 单一权威 (见 model_resolver.py)
from .model_resolver import resolve_model, CTX_CAP_BY_PROVIDER  # noqa: E402
from .router import decide_tier  # noqa: E402
from .worker_contract import (  # noqa: E402
    PROVIDER_CLAUDE_CODE,
    STANDALONE_WORKER_PROVIDERS,
    WORKER_KIND_STANDALONE,
    WORKER_KIND_TEAM,
    WORKER_KINDS,
)

# 块 3 R5: plan 内容注入时 size cap. 每个 plan / standard / template 文件最多注入这么多字节,
# 多余截断 (因为 plan 可能上万字, 直接注入会撞 ctx 上限).
_INJECT_FILE_SIZE_CAP = 12_000


def _read_file_clamped(path: Path, cap: int = _INJECT_FILE_SIZE_CAP) -> tuple[str, bool]:
    """读文件 + 截 cap 字节. 返回 (内容, 是否被截断)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", False
    if len(text) <= cap:
        return text, False
    return text[:cap] + f"\n\n... [truncated {len(text) - cap} chars at {cap} cap] ...", True


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _extract_frontmatter(plan_text: str) -> str:
    """从 plan.md 顶部抠 frontmatter (--- YAML ---). 没有就空串."""
    m = _FRONTMATTER_RE.match(plan_text)
    return m.group(1).strip() if m else ""


def _build_spawn_prompt(
    plan_id: str,
    initial_prompt: str,
    extra_standards: list[str] | None,
    extra_templates: list[str] | None,
    skip_plan_inject: bool,
    ws: Path,
) -> tuple[str, list[str]]:
    """组装 subagent 第一条 user message — 落实 §5.2 (plan + guard + standards + template).

    返回 (最终 prompt, 注入项 audit log).
    """
    sections: list[str] = []
    audit: list[str] = []

    # 1) plan 本身 (R5 头一项) — 找 docs/plans/<plan_id>/plan.md 或 brief.md
    if not skip_plan_inject:
        plan_path: Path | None = None
        candidates = [
            ws / "docs" / "plans" / plan_id / "plan.md",
            ws / "docs" / "plans" / plan_id / "brief.md",
        ]
        for c in candidates:
            if c.is_file():
                plan_path = c
                break
        if plan_path is not None:
            plan_text, truncated = _read_file_clamped(plan_path)
            sections.append(
                f"## Plan 内容 ({plan_path.relative_to(ws).as_posix()})\n\n{plan_text}"
            )
            audit.append(
                f"plan:{plan_path.relative_to(ws).as_posix()}"
                + (" [truncated]" if truncated else "")
            )

            # 2) frontmatter guard 字段 (R5 第二项) — allowed_write_roots 等
            fm = _extract_frontmatter(plan_text)
            if fm:
                sections.append(
                    f"## Plan frontmatter guard (写入受这些字段约束)\n\n```yaml\n{fm}\n```"
                )
                audit.append("frontmatter_guard")
        else:
            audit.append(f"plan_not_found:{plan_id}")

    # 3) standards (R5 第三项 — 可选)
    for std in (extra_standards or [])[:5]:  # cap 5 个
        p = (ws / std).resolve()
        if not str(p).startswith(str(ws.resolve())):
            continue  # path traversal 保护
        if not p.is_file():
            audit.append(f"standard_missing:{std}")
            continue
        text, truncated = _read_file_clamped(p)
        sections.append(f"## Standard ({std})\n\n{text}")
        audit.append(f"std:{std}" + (" [truncated]" if truncated else ""))

    # 4) templates (R5 第四项 — 可选)
    for tmpl in (extra_templates or [])[:5]:
        p = (ws / tmpl).resolve()
        if not str(p).startswith(str(ws.resolve())):
            continue
        if not p.is_file():
            audit.append(f"template_missing:{tmpl}")
            continue
        text, truncated = _read_file_clamped(p)
        sections.append(f"## Template ({tmpl})\n\n{text}")
        audit.append(f"tmpl:{tmpl}" + (" [truncated]" if truncated else ""))

    # 4.5) 运行环境提示 (2026-05-30: subagent 在 Windows 下 git-bash 跑 PowerShell 命令全挂)
    sections.append(
        "## 运行环境 · 必读 (避免 tool_use_error)\n\n"
        "本会话跑在 **Windows 10**, Bash 工具背后是 git-bash (`/usr/bin/bash`), 不是 PowerShell。\n\n"
        "**用 unix 命令**:\n"
        "- 列目录: `ls e:/path` (不是 `Get-ChildItem`)\n"
        "- 找文件: `find e:/path -name \"*.md\"` (不是 `Get-ChildItem -Recurse`)\n"
        "- 看文件: `cat e:/path/file.md` (不是 `Get-Content`)\n"
        "- 路径: 正斜杠 `e:/WindowsWorkspace/...` 或反斜杠双写 `e:\\\\WindowsWorkspace\\\\...`\n\n"
        "**不要直接调 PowerShell cmdlet** (`Get-ChildItem` / `Select-Object` / `Format-Table` / `Sort-Object`) — "
        "git-bash 不认, 会报 'command not found' 然后 sdk 把同批并行 Bash 全 cancel.\n\n"
        "**如果一定要跑 PowerShell**: 用 `powershell.exe -NoProfile -Command \"...\"`。"
    )
    audit.append("env_hint_windows")

    # 5) 收尾约定 — 提前到 "你的任务" 之前, 避免被大 plan 注入后位置太靠后弱化.
    #    M2 Phase 2A: subagent 反复出现 "Write 完 md 文件就 stop, 不调 omni review submit"
    #    的问题. 1A 调研结论是 prompt 引导虽有但被埋在末尾, 改文案 + 调位置 + 强示例.
    sections.append(
        "## 交付定义 (deliverable) · 必读, 任务成败由这一条判定\n\n"
        "**你的 deliverable 不是 markdown 文件, 是审阅台里的一条 material 记录 (mat_xxx ID).**\n\n"
        "- 拿到 `material submitted: id=mat_xxx` 这种输出 = 任务完成\n"
        "- 只 Write 了 md 文件然后停止 = **任务未完成 = 任务失败**, 不管文件写得多好\n"
        "- 没拿到 mat_xxx ID 就 stop, 等同于没干\n\n"
        "### 正确流程 (按顺序, 一步都不能跳)\n\n"
        "1. 用 Write 把审阅内容落到一个 md 文件 (路径建议 `e:/WindowsWorkspace/omnicompany/data/tmp/<任意名>.md`)\n"
        "2. **立刻**调 Bash 跑 `omni review submit ... --file <你刚写的 md 路径>` (见下面模板)\n"
        "3. 看到 stdout 里 `material submitted: id=mat_xxx` 字样 — 这一步是任务真正结束的标志\n"
        "4. 这时才能 stop\n\n"
        "### 提交命令模板\n\n"
        "```bash\n"
        "omni review submit \\\n"
        "  --kind markdown \\\n"
        "  --tier important \\\n"
        "  --title \"<一句话标题>\" \\\n"
        f"  --plan-id {plan_id} \\\n"
        "  --file <你 Write 的 md 文件绝对路径>\n"
        "```\n\n"
        "kind 选择: 文档类 `markdown`; 网页 `html`; 图 `image`; 选择题 `key_question`; "
        "自定义结构 `custom_web_template`.  \n"
        "tier 选择: `mandatory`=必验收阻断后续 spawn; `important`=重要随时可审 (默认选这个); "
        "`processual`=过程性弱审; `ignored`=不审阅.\n\n"
        "### 严禁的失败模式 (你做这些等于任务失败)\n\n"
        "- Write 完 md 文件直接 stop, 不调 `omni review submit`\n"
        "- 在最后一条消息里只**打印** `omni review submit ...` 命令文本, 没真正用 Bash 执行它\n"
        "- 说 \"我建议你执行 omni review submit ...\" 然后 stop — 不是建议, 是你自己必须执行\n"
        "- 因为觉得 \"内容已经写在 md 里了用户能看到\" 而跳过 submit — 用户**只看审阅台**, 不看散落的 md\n\n"
        "任何理由都不能跳过 submit 这一步. 提交失败 (报错) 就把报错贴出来重试, 不要静默 stop.\n\n"
        "你的身份 OMNI_CLI_CALLER=subagent 已经有权调 `omni review submit` "
        "(spawn / fork / bind 之类被 cli 层硬阻断, 别试)."
    )
    audit.append("emit_material_protocol_hint")

    # 6) 用户提的实际 task (放在收尾约定之后, 让 "怎么收尾" 先入栈, 再讲做什么)
    sections.append(f"## 你的任务\n\n{initial_prompt}")

    # 7) 最后再贴一条短提醒 — double tap, 防止长 plan + 长 task 之后又把收尾约定挤出注意力.
    sections.append(
        "## 最后再提醒一遍 (防止你写完 md 就 stop)\n\n"
        "stop 之前必须看到 `material submitted: id=mat_xxx`. 没看到就还没完成, 继续干, 不要 stop."
    )
    audit.append("final_reminder_submit")

    return "\n\n---\n\n".join(sections), audit


class SpawnSubagentRouter(SingleToolRouter):
    """派一个 subagent 干活. 块 3 真实施: HTTP POST 调 ccdaemon /cc/chat/sessions 启 subagent."""

    TOOL_NAME: ClassVar[str] = "spawn_subagent"
    DESCRIPTION: ClassVar[str] = (
        "Dispatch a subagent worker to execute a plan. Two worker kinds:\n"
        "- 'team_worker': omnicompany Team Worker (e.g. team_supervisor). Runs via "
        "  `omnicompany.core.dispatch.dispatch()` in a subprocess. Blocking up to 5 min.\n"
        "- 'standalone_plan_worker': claude code / codex / omni_agent CLI driven by "
        "  plan + guard + standards. Spawned via ccdaemon HTTP API, runs ASYNCHRONOUSLY. "
        "  Returns subagent_id immediately; you'll be woken up later by `subagent.completed` event.\n\n"
        "When you (controller) spawn a subagent, it automatically receives a header tag "
        "[from: BOSS-SIGHT controller, not_user: true] so it knows commands come from you, not user (§5.3)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "worker_kind": {
                "type": "string",
                "enum": list(WORKER_KINDS),
            },
            "provider": {
                "type": "string",
                "enum": list(STANDALONE_WORKER_PROVIDERS),
                "description": "For standalone_plan_worker only. Default claude_code.",
            },
            "plan_id": {
                "type": "string",
                "minLength": 1,
                "description": "omnicompany plan id, e.g. 'voxelcraft/[2026-05-17]MC-COMPANY-PIPELINE'. For team_worker this is the team id (e.g. 'team_supervisor').",
            },
            "initial_prompt": {
                "type": "string",
                "minLength": 10,
                "description": "What to ask the subagent to do (goal-mode framing). For team_worker this is wrapped into input_data.task.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the subagent. Default: workspace root.",
            },
            "ctx_upper_bound_tokens": {
                "type": "integer",
                "minimum": 10000,
                "maximum": 1_000_000,
                "description": "Subagent ctx upper bound. Default 400000; codex auto-caps at 256000.",
            },
            "model_hint": {
                "type": "string",
                "enum": ["high", "low", "default", "auto"],
                "description": "Model tier: 'high' (opus/gpt-5.5) for complex work; 'low' (sonnet/gpt-5.3-codex) for routine. (§2.12)",
            },
            "extra_standards": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of standards file paths (relative to workspace, e.g. "
                    "'docs/standards/protocol/cli-output.md') to attach to subagent's first message. "
                    "Used to inject extra context per §5.2 ('plan + guard + standards + template')."
                ),
            },
            "extra_templates": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of template file paths (relative to workspace, e.g. "
                    "'templates/plan_skeleton.md') to attach. Same §5.2 mechanism."
                ),
            },
            "skip_plan_inject": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, DO NOT pre-pend plan.md contents to subagent prompt. "
                    "Use only when subagent doesn't need plan context (rare)."
                ),
            },
            "worktree_isolation": {
                "type": "string",
                "enum": ["none", "git_worktree"],
                "default": "none",
                "description": (
                    "For standalone_plan_worker only. 'git_worktree' creates an isolated "
                    "git worktree and launches the worker there; default keeps existing cwd behavior."
                ),
            },
            "worktree_base": {
                "type": "string",
                "description": "Base ref for worktree_isolation=git_worktree. Default HEAD.",
            },
            "override_mandatory_block": {
                "type": "boolean",
                "default": False,
                "description": (
                    "块 4 R8 阻断 override. 默认 false: 若 plan 有未通过 mandatory material 直接拒派. "
                    "True 时跳过阻断 (例如首次启动 plan 还没有 material). 跳过原因应在 reply_to_user 说明."
                ),
            },
        },
        "required": ["worker_kind", "plan_id", "initial_prompt"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False  # 块 3: 真启 subagent, 写 cc_sessions.json + emit events

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        kind = args.get("worker_kind", "")
        plan_id = args.get("plan_id", "")
        initial_prompt = args.get("initial_prompt", "")

        # 块 4 R8: 必验收阻断. plan 内有 mandatory 未通过 material → 拒派.
        # 总控必须先 push_material_to_user / 等用户审阅后再 spawn.
        if not args.get("override_mandatory_block", False):
            blockers = _check_mandatory_blockers(plan_id)
            if blockers:
                lines = [
                    f"BLOCKED: plan {plan_id!r} has {len(blockers)} unaccepted mandatory material(s). "
                    "(§4.6.1: 必验收级未通过, 相关任务不继续)"
                ]
                for b in blockers[:5]:
                    lines.append(
                        f"  - mat_id={b['id']} status={b['status']} title={b['title']!r}"
                    )
                lines.append(
                    "请先用 push_material_to_user 推给用户审, 用户 accept 后再 spawn. "
                    "如确认要强行派 (例如初次启动), 传 override_mandatory_block=true."
                )
                return "\n".join(lines)

        if kind == WORKER_KIND_TEAM:
            return self._spawn_team_worker(plan_id, initial_prompt, args, ctx)
        if kind == WORKER_KIND_STANDALONE:
            return self._spawn_standalone_worker(plan_id, initial_prompt, args)
        return f"unknown worker_kind: {kind!r}"

    # ── team_worker: 委托 omnicompany 已有 DispatchTeamRouter (不重复造轮) ────────
    def _spawn_team_worker(self, team_id: str, initial_prompt: str, args: dict, ctx: ToolContext) -> str:
        """跑 omnicompany 已注册的 team (如 team_supervisor).

        **复用** omnicompany 自家 `DispatchTeamRouter` (走子进程隔离 + 标准 verdict 解析).
        总控**应当**做 fire-and-forget 的小 team; 跑超时的 team 改用 standalone_plan_worker.
        """
        from omnicompany.packages.services._core.team_supervisor.routers.dispatch_team import (
            DispatchTeamRouter,
        )

        delegate = DispatchTeamRouter()
        delegate_args = {
            "target_team_id": team_id,
            "input_data": {"task": initial_prompt},
            "max_steps": int(args.get("max_steps") or 200),
            "timeout_seconds": int(args.get("timeout_seconds") or 600),
        }

        try:
            raw_result = delegate._execute(delegate_args, ctx)
        except Exception as e:  # noqa: BLE001
            return f"team_worker {team_id} dispatch failed: {type(e).__name__}: {e}"

        try:
            result = json.loads(raw_result) if isinstance(raw_result, str) else (raw_result or {})
        except json.JSONDecodeError:
            return f"team_worker {team_id} returned non-JSON: {str(raw_result)[:300]}"

        verdict = result.get("verdict", "?")
        diagnosis = (result.get("diagnosis") or "")[:200]
        return f"team_worker {team_id} {verdict}: {diagnosis}"

    # ── standalone_plan_worker: HTTP POST 调 ccdaemon ──────────────────
    def _spawn_standalone_worker(self, plan_id: str, initial_prompt: str, args: dict) -> str:
        """让 ccdaemon 启一个 chat session (claude_code / codex / omni_agent) 跑 plan.

        sync HTTP POST 立刻返回 subagent_id. subagent 在后台跑, 完成时 chat.py emit
        subagent.completed 事件, controller_waker (块 3 R9) 唤起总控.

        复用 ccdaemon POST /cc/chat/sessions 端点 (块 3 新增 initial_prompt + from_controller + active_plan 字段).
        """
        import urllib.error
        import urllib.request

        provider = args.get("provider") or PROVIDER_CLAUDE_CODE
        model_hint = args.get("model_hint") or "default"
        auto_model_decision: dict[str, Any] | None = None
        if model_hint == "auto":
            auto_model_decision = decide_tier(
                kind="markdown",
                tier="important",
                title=plan_id,
                content=initial_prompt,
                context="spawn_subagent model selection",
            ).to_dict()
            model_hint = str(auto_model_decision.get("model_hint") or "default")
        model = resolve_model(provider, model_hint)
        ws = _workspace_root()
        cwd = args.get("cwd") or str(ws)
        worktree_meta: dict[str, str] | None = None
        isolation = args.get("worktree_isolation") or "none"
        if isolation not in {"none", "git_worktree"}:
            return f"standalone spawn failed: unknown worktree_isolation={isolation!r}"
        if isolation == "git_worktree":
            if args.get("cwd"):
                return "standalone spawn failed: worktree_isolation=git_worktree cannot be combined with explicit cwd"
            try:
                worktree_meta = _create_spawn_worktree(
                    ws,
                    plan_id,
                    base_ref=args.get("worktree_base") or "HEAD",
                )
            except Exception as e:  # noqa: BLE001
                return f"standalone spawn failed: worktree isolation: {type(e).__name__}: {e}"
            cwd = worktree_meta["path"]
            if Path(cwd).resolve() == _find_git_root(ws):
                return "standalone spawn failed: worktree cwd assertion failed"
        ctx_cap = args.get("ctx_upper_bound_tokens") or CTX_CAP_BY_PROVIDER.get(provider, 400_000)
        ctx_cap = min(int(ctx_cap), CTX_CAP_BY_PROVIDER.get(provider, 400_000))

        # R5: 注入 plan + guard + standards + template (§5.2)
        composed_prompt, inject_audit = _build_spawn_prompt(
            plan_id=plan_id,
            initial_prompt=initial_prompt,
            extra_standards=args.get("extra_standards") or [],
            extra_templates=args.get("extra_templates") or [],
            skip_plan_inject=bool(args.get("skip_plan_inject", False)),
            ws=ws,
        )

        body = {
            "cwd": cwd,
            "model": model,
            "provider": provider,
            "initial_prompt": composed_prompt,
            "from_controller": True,
            "active_plan": plan_id,
        }
        spawn_meta = agent_spawn_metadata(ENTRY_CONTROLLER_SPAWN)
        daemon_port = int(os.environ.get("OMNI_CC_DAEMON_PORT", "8201"))
        url = f"http://127.0.0.1:{daemon_port}/cc/chat/sessions"

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")[:300]
            except Exception:  # noqa: BLE001
                err_body = str(e)
            return f"standalone spawn failed: HTTP {e.code} — {err_body}"
        except urllib.error.URLError as e:
            return f"standalone spawn failed: ccdaemon unreachable at {url} — {e.reason}"
        except Exception as e:  # noqa: BLE001
            return f"standalone spawn failed: {type(e).__name__}: {e}"

        sid = meta.get("id", "?")
        _log.info(
            "spawn_subagent standalone: id=%s provider=%s model=%s plan=%s ctx_cap=%d cwd=%s worktree=%s inject=%s spawn_entry=%s",
            sid, provider, model or "(default)", plan_id, ctx_cap, cwd,
            (worktree_meta or {}).get("path") or "none",
            ",".join(inject_audit) or "none",
            spawn_meta["agent_spawn_entry"],
        )
        worktree_note = ""
        if worktree_meta:
            worktree_note = (
                f" worktree={worktree_meta['path']} branch={worktree_meta['branch']} "
                f"base={worktree_meta['base_ref']}."
            )
        auto_model_note = ""
        if auto_model_decision:
            reasons = auto_model_decision.get("reasons") or []
            if isinstance(reasons, list):
                reason_text = ";".join(str(r) for r in reasons[:2])
            else:
                reason_text = str(reasons)
            auto_model_note = f" auto_model_hint={model_hint} auto_reason={reason_text}."
        return (
            f"standalone subagent spawned: id={sid} provider={provider} "
            f"model={model or '(default)'} ctx_cap={ctx_cap}. "
            f"spawn_entry={spawn_meta['agent_spawn_entry']}. "
            f"cwd={cwd}."
            f"{worktree_note} "
            f"{auto_model_note} "
            f"injected: {', '.join(inject_audit) or 'none'}. "
            f"Running async; you'll be notified when it completes."
        )


# ═══════════════════════════════════════════════════════════════════════
# fork_subagent_for_report — fork subagent 进行汇报 (用户原话 §2.8 + §6.2)
# 不打断, 而是 fork
# ═══════════════════════════════════════════════════════════════════════


class ForkSubagentForReportRouter(SingleToolRouter):
    """Fork 一个 subagent 让 fork 出来的副本写汇报. 原 subagent 不打断继续跑.

    用户原话 §6.2: '进行汇报：总控 agent 可以要求 subagent 进行汇报, 如果在进行,
    不进行打断, 而是对其进行 fork 然后进行汇报.'

    实现: HTTP POST /cc/chat/sessions with `fork_from_provider_session_id=<源 claude_session_id>`.
    claude_code SDK 支持 `fork_session=True`. 新 session 继承对话历史, 但写入新 session_id.
    原 session 完全不受影响.

    用 standalone_plan_worker 路径, 因为 fork 的目的就是 "让另一个 worker 实例去做汇报".
    """

    TOOL_NAME: ClassVar[str] = "fork_subagent_for_report"
    DESCRIPTION: ClassVar[str] = (
        "Fork a running subagent (claude_code only for now) to write a report "
        "WITHOUT interrupting it (§6.2). New session inherits the source's "
        "conversation context but runs independently. Returns the new (fork) "
        "subagent's session id."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "source_subagent_id": {
                "type": "string",
                "minLength": 8,
                "description": (
                    "The session id (chat-xxx) of the running subagent to fork. "
                    "Use list_active subagent ids from your ctx."
                ),
            },
            "report_prompt": {
                "type": "string",
                "minLength": 10,
                "description": (
                    "What you want the fork to write a report about. E.g. "
                    "'Summarize what you've done so far on this plan in 200 words.'"
                ),
            },
            "model_hint": {
                "type": "string",
                "enum": ["high", "low", "default"],
                "description": "Optional. Default = same model as source.",
            },
        },
        "required": ["source_subagent_id", "report_prompt"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        import urllib.error
        import urllib.request

        source_id = args["source_subagent_id"]
        report_prompt = args["report_prompt"]
        model_hint = args.get("model_hint") or "default"

        # 先 GET 源 session meta 拿 claude_session_id + cwd + model
        daemon_port = int(os.environ.get("OMNI_CC_DAEMON_PORT", "8201"))
        list_url = f"http://127.0.0.1:{daemon_port}/cc/chat/sessions"
        try:
            with urllib.request.urlopen(list_url, timeout=10) as resp:
                page = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return f"fork failed: cannot fetch session list — {type(e).__name__}: {e}"

        items = page.get("items") or []
        source = next((it for it in items if it.get("id") == source_id), None)
        if source is None:
            return (
                f"fork failed: source subagent {source_id!r} not found in active sessions"
            )
        if source.get("provider") != PROVIDER_CLAUDE_CODE:
            return (
                f"fork failed: only claude_code provider supports fork "
                f"(source={source_id} provider={source.get('provider')})"
            )
        provider_sid = source.get("claude_session_id") or source.get("provider_session_id")
        if not provider_sid:
            return (
                f"fork failed: source {source_id} has no claude_session_id yet "
                f"(may still be in first turn). Wait a moment and retry."
            )

        # POST 新 session with fork field
        model = resolve_model(PROVIDER_CLAUDE_CODE, model_hint) or source.get("model")
        body = {
            "cwd": source.get("cwd"),
            "model": model,
            "provider": PROVIDER_CLAUDE_CODE,
            "initial_prompt": report_prompt,
            "from_controller": True,
            "active_plan": source.get("active_plan"),
            "fork_from_provider_session_id": provider_sid,
        }
        url = f"http://127.0.0.1:{daemon_port}/cc/chat/sessions"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")[:300]
            except Exception:  # noqa: BLE001
                err_body = str(e)
            return f"fork failed: HTTP {e.code} — {err_body}"
        except Exception as e:  # noqa: BLE001
            return f"fork failed: {type(e).__name__}: {e}"

        fork_id = meta.get("id", "?")
        _log.info(
            "fork_subagent_for_report: source=%s fork=%s provider_sid=%s",
            source_id, fork_id, provider_sid,
        )
        return (
            f"forked subagent: source={source_id} → fork={fork_id} "
            f"(provider_session_id={provider_sid}). "
            f"Original still running; fork will reply async."
        )


# ═══════════════════════════════════════════════════════════════════════
# emit_event — 控制流事件 (subagent 放行 / 终止 / plan todo 修正等)
# 用户原话 §6.3 阻断: 总控决定放行或终止
# ═══════════════════════════════════════════════════════════════════════


class EmitEventRouter(SingleToolRouter):
    """发控制流事件到 EventBus. 块 1 阶段记录 + log, 块 3 接 EventBus 真分发."""

    TOOL_NAME: ClassVar[str] = "emit_event"
    DESCRIPTION: ClassVar[str] = (
        "Emit a control-flow event. Use for subagent unblock / shutdown / "
        "plan todo correction etc. Do NOT use for user replies (use submit_response). "
        "Do NOT use for proposals (use propose_change)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "event_type": {
                "type": "string",
                "enum": [
                    "subagent.unblock",
                    "subagent.shutdown",
                    "subagent.send_message",
                    "plan.todo_correct",
                    "plan.log_completion",
                    "reviewstage.submit",
                ],
                "description": "Predefined event types this block supports.",
            },
            "target_subagent_id": {
                "type": "string",
                "description": "When event_type is subagent.* , the target subagent id.",
            },
            "target_plan_id": {
                "type": "string",
                "description": "When event_type is plan.* / reviewstage.* , the target plan id.",
            },
            "payload": {
                "type": "object",
                "description": "Event-specific payload (free-form, schema by event_type).",
            },
        },
        "required": ["event_type", "payload"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        et = args.get("event_type", "?")
        _log.info("emit_event: %s payload=%s", et, args.get("payload"))
        # TODO 块 3: 真发到 EventBus. 当前只 log 让总控知道"已发"
        return f"event recorded: {et} (block-3 will wire EventBus dispatch)"


# ═══════════════════════════════════════════════════════════════════════
# propose_change — 提议 (prompt / guard / summarize_to_component)
# 用户原话 §2.13 反思 + §2.5.1 调整 guard. AI 自决: 总控只提议不落地 (W-020 N-9)
# ═══════════════════════════════════════════════════════════════════════


class ProposeChangeRouter(SingleToolRouter):
    """发提议给外部维护会话审. 总控不直接改 prompt / guard / 写 template (§2.14)."""

    TOOL_NAME: ClassVar[str] = "propose_change"
    DESCRIPTION: ClassVar[str] = (
        "Propose a change to be reviewed by the external maintenance session. "
        "Three kinds:\n"
        "- 'prompt_modification': suggest revising your own system prompt (from reflection)\n"
        "- 'guard_change': suggest adjusting boundary guard (your own or subagent's)\n"
        "- 'summarize_to_component': summarize repeated user operations into "
        "template / standards / guard / prompt_template / skill\n"
        "Proposals are written to data/boss_sight/proposals/<ts>.json and reviewed "
        "by the external maintenance session before any actual file change. "
        "You CANNOT write files yourself (per user spec §2.14)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["prompt_modification", "guard_change", "summarize_to_component"],
            },
            "rationale": {
                "type": "string",
                "minLength": 10,
                "description": "Why are you proposing this? Be specific.",
            },
            "target_location": {
                "type": "string",
                "description": (
                    "Where this should be applied. For prompt_modification: "
                    "controller/prompts/system.md. For guard_change: a specific guard "
                    "file path. For summarize_to_component: where the new component "
                    "would live (e.g. docs/standards/protocol/foo.md)."
                ),
            },
            "component_type": {
                "type": "string",
                "enum": ["template", "standards", "guard", "prompt_template", "skill"],
                "description": (
                    "Only used when kind is summarize_to_component. The 5 component types "
                    "from user spec §2.13."
                ),
            },
            "content_draft": {
                "type": "string",
                "minLength": 10,
                "description": "Concrete draft (markdown / diff / yaml) for the proposed change.",
            },
        },
        "required": ["kind", "rationale", "content_draft"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False  # 写 proposals/ 目录是合规的 (boundary_guard 白名单)

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        kind = args.get("kind", "unknown")
        rationale = args.get("rationale", "")
        target_location = args.get("target_location", "")
        content_draft = args.get("content_draft", "")
        component_type = args.get("component_type", "")

        # 块 2: 真持久化到 data/boss_sight/proposals/<ts>_<kind>_<slug>.json
        ws = _workspace_root()
        proposals_dir = ws / "data" / "boss_sight" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)

        ts = _now_iso().replace(":", "-").replace(".", "_")[:25]
        slug = _safe_filename(target_location or component_type or "general")
        out_path = proposals_dir / f"{ts}_{kind}_{slug}.json"

        record = {
            "kind": kind,
            "rationale": rationale,
            "target_location": target_location,
            "component_type": component_type,
            "content_draft": content_draft,
            "created_at": _now_iso(),
            "trace_id": getattr(ctx, "trace_id", ""),
            "turn_number": getattr(ctx, "turn_number", 0),
            "status": "pending_review",
        }
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _log.info("proposal persisted: kind=%s path=%s", kind, out_path)
        rel = out_path.relative_to(ws).as_posix()
        return (
            f"proposal saved → {rel}\n"
            f"Awaiting external maintenance session to review and apply."
        )


# ═══════════════════════════════════════════════════════════════════════
# audit_plans_for_todo — 扫所有 plan 找缺 todo 的
# 用户原话 §2.16.1: "所有 plan 现在一定都要有 todo 列表用以标识进度"
# ═══════════════════════════════════════════════════════════════════════


_TODO_LINE_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s+", re.MULTILINE)


class AuditPlansForTodoRouter(SingleToolRouter):
    """扫所有 docs/plans/**/plan.md 找缺 todo 列表的, 让总控决定补哪些."""

    TOOL_NAME: ClassVar[str] = "audit_plans_for_todo"
    DESCRIPTION: ClassVar[str] = (
        "Scan all docs/plans/**/plan.md (also brief.md) and return a list of plans "
        "that are MISSING the todo checklist required by user spec §2.16.1 "
        "('所有 plan 现在一定都要有 todo 列表用以标识进度'). "
        "Use this when you suspect plans drifted off the rule, or when user asks for "
        "an audit. After getting the list, you decide whether to add todo to specific "
        "plans via the `edit` or `write_file` tool."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "category_filter": {
                "type": "string",
                "description": "Optional: only audit this category (e.g. 'dashboard', 'voxelcraft'). Empty = all.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "default": 50,
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        ws = _workspace_root()
        plans_dir = ws / "docs" / "plans"
        if not plans_dir.is_dir():
            return "docs/plans/ 目录不存在, 无 plan 可审"

        category_filter = (args.get("category_filter") or "").strip()
        max_results = int(args.get("max_results") or 50)

        missing: list[dict] = []
        scanned = 0
        for category_dir in plans_dir.iterdir():
            if not category_dir.is_dir():
                continue
            if category_filter and category_dir.name != category_filter:
                continue
            for plan_dir in category_dir.iterdir():
                if not plan_dir.is_dir():
                    continue
                entry = plan_dir / "plan.md"
                if not entry.is_file():
                    entry = plan_dir / "brief.md"
                if not entry.is_file():
                    continue
                scanned += 1
                try:
                    text = entry.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                todos = _TODO_LINE_RE.findall(text)
                if not todos:
                    rel = entry.relative_to(ws).as_posix()
                    missing.append({
                        "plan_id": f"{category_dir.name}/{plan_dir.name}",
                        "entry_path": rel,
                        "char_count": len(text),
                    })
                    if len(missing) >= max_results:
                        break
            if len(missing) >= max_results:
                break

        lines = [f"audit complete: scanned {scanned} plans"]
        if not missing:
            lines.append(f"all {scanned} plans have todo lists ✓ (§2.16.1 OK)")
        else:
            lines.append(f"{len(missing)} plan(s) MISSING todo list (违反 §2.16.1):")
            for m in missing:
                lines.append(f"  - {m['plan_id']}  ({m['char_count']} chars)  path={m['entry_path']}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# record_plan_completion — 写 data/boss_sight/plan_completion_log/
# 用户原话 §2.9: 总控记录 plan 完成情况
# ═══════════════════════════════════════════════════════════════════════


class RecordPlanCompletionRouter(SingleToolRouter):
    """记录某 plan 的完成情况快照到 data/boss_sight/plan_completion_log/."""

    TOOL_NAME: ClassVar[str] = "record_plan_completion"
    DESCRIPTION: ClassVar[str] = (
        "Append a plan completion snapshot to data/boss_sight/plan_completion_log/. "
        "Use after subagents finish work on a plan, or when user asks you to log "
        "current progress. The record includes plan_id, status, todo done/total, "
        "produced_materials list, and your assessment. "
        "Note: this is RECORDING (per user spec §2.9), not the FORMAL REPORT — "
        "reports themselves are subagent's work (§2.15)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "plan_id": {
                "type": "string",
                "minLength": 3,
                "description": "Plan id, e.g. 'voxelcraft/[2026-05-17]MC-COMPANY-PIPELINE'",
            },
            "status": {
                "type": "string",
                "enum": ["in_progress", "blocked", "partial", "done", "abandoned"],
            },
            "todo_done": {"type": "integer", "minimum": 0},
            "todo_total": {"type": "integer", "minimum": 0},
            "produced_materials": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths or descriptors of produced materials (subagent output, audits, etc).",
            },
            "assessment": {
                "type": "string",
                "minLength": 5,
                "description": "Your assessment of where the plan stands. Brief, factual.",
            },
        },
        "required": ["plan_id", "status", "assessment"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        plan_id = args["plan_id"]
        status = args["status"]
        todo_done = int(args.get("todo_done") or 0)
        todo_total = int(args.get("todo_total") or 0)
        produced = args.get("produced_materials") or []
        assessment = args["assessment"]

        ws = _workspace_root()
        log_dir = ws / "data" / "boss_sight" / "plan_completion_log"
        log_dir.mkdir(parents=True, exist_ok=True)

        ts = _now_iso().replace(":", "-").replace(".", "_")[:25]
        slug = _safe_filename(plan_id.replace("/", "_"))
        out_path = log_dir / f"{ts}_{slug}.json"

        record = {
            "plan_id": plan_id,
            "status": status,
            "todo_done": todo_done,
            "todo_total": todo_total,
            "produced_materials": produced,
            "assessment": assessment,
            "recorded_at": _now_iso(),
            "trace_id": getattr(ctx, "trace_id", ""),
        }
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rel = out_path.relative_to(ws).as_posix()

        # WORK-REPORT-AND-REVIEW-TYPES #5: plan 完成 → 自动产工作报告物料进审阅台
        review_note = ""
        if status == "done":
            try:
                from omnicompany.packages.services._core.lifecycle.work_report import (
                    build_work_report,
                    submit_versioned_report,
                )
                wr = build_work_report(plan_id)
                body = wr.get("markdown") or (
                    f"# Plan 完成报告 · {plan_id}\n\n{assessment}\n\n"
                    f"todo: {todo_done}/{todo_total}\n产物: {produced}\n"
                )
                # 版本化: 同 plan 旧报告归档 + 记历史版本, 待审只留最新(不堆积)
                sub = submit_versioned_report(plan_id, body, title=f"Plan 完成报告: {plan_id}")
                review_note = (f"; 已自动进审阅 material={sub.get('material_id', '?')}"
                               f" (第{sub.get('version', 1)}版)") if sub.get("ok") else ""
            except Exception:  # noqa: BLE001
                pass
        return f"plan_completion logged → {rel} (status={status}, {todo_done}/{todo_total}){review_note}"


# ═══════════════════════════════════════════════════════════════════════
# list_prompt_archive / list_worker_archive — §2.11 管理 prompt + worker
# ═══════════════════════════════════════════════════════════════════════


class ListPromptArchiveRouter(SingleToolRouter):
    """列出 omnicompany 现有 prompt 资源 + 总控自家归档.

    覆盖 §2.11: 总控记录和整理成套的 prompt 和 worker 内容, 汇报和管理已有的 prompt.

    扫:
    - omnicompany/.claude/skills/*/SKILL.md  (Claude Code skill)
    - docs/standards/cli/*.md                (CLI 规范)
    - docs/standards/protocol/*.md           (协议规范)
    - data/boss_sight/prompt_archive/*       (总控自家归档)
    """

    TOOL_NAME: ClassVar[str] = "list_prompt_archive"
    DESCRIPTION: ClassVar[str] = (
        "List all prompts / skills / standards inventoried in the omnicompany repo. "
        "Includes Claude Code skills, omnicompany CLI/protocol standards, and "
        "controller's own prompt_archive/. Use to answer '我们有哪些 prompt' "
        "(per user spec §2.11)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Optional substring filter on name/path.",
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        ws = _workspace_root()
        filt = (args.get("filter") or "").strip().lower()
        out_lines: list[str] = []

        def _gather(label: str, patterns: list[str]) -> None:
            seen: set[str] = set()
            entries: list[str] = []
            for pat in patterns:
                for p in ws.glob(pat):
                    if not p.is_file():
                        continue
                    rel = p.relative_to(ws).as_posix()
                    if rel in seen:
                        continue
                    seen.add(rel)
                    if filt and filt not in rel.lower():
                        continue
                    try:
                        size = p.stat().st_size
                    except OSError:
                        size = 0
                    entries.append(f"  - {rel}  ({size} bytes)")
            if entries:
                out_lines.append(f"## {label} ({len(entries)})")
                out_lines.extend(entries)
                out_lines.append("")

        _gather(
            "Claude Code skills (.claude/skills/)",
            [".claude/skills/*/SKILL.md", ".claude/skills/*/*.md"],
        )
        _gather(
            "CLI standards (docs/standards/cli/)",
            ["docs/standards/cli/*.md"],
        )
        _gather(
            "Protocol standards (docs/standards/protocol/)",
            ["docs/standards/protocol/*.md"],
        )
        _gather(
            "Controller prompt_archive (data/boss_sight/prompt_archive/)",
            ["data/boss_sight/prompt_archive/*.md", "data/boss_sight/prompt_archive/*.txt"],
        )

        if not out_lines:
            return f"no prompts matched filter={filt!r}"
        return "\n".join(out_lines).strip()


class ListWorkerArchiveRouter(SingleToolRouter):
    """列出 omnicompany 现有 worker 资源 + 总控自家归档.

    覆盖 §2.11: 总控记录和整理成套的 prompt 和 worker 内容.

    扫:
    - src/omnicompany/packages/services/_core/team_supervisor/workers/*.py
    - src/omnicompany/packages/services/_core/team_builder/workers/*.py
    - src/omnicompany/packages/services/_learning/kb/multi_agent/workers/*.py (if exists)
    - data/boss_sight/worker_archive/*

    注: 仅列出文件名 + 顶部 docstring 前 200 字, 不读全文 (代码层是 subagent 的活, 总控只索引).
    """

    TOOL_NAME: ClassVar[str] = "list_worker_archive"
    DESCRIPTION: ClassVar[str] = (
        "List all subagent worker definitions in the omnicompany repo + controller's "
        "own worker_archive/. Returns worker file path + top-of-file docstring excerpt. "
        "Use to answer '我们有哪些 worker' (per user spec §2.11). "
        "You CANNOT read worker source code in depth — that's subagent territory; "
        "this is indexing only."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Optional substring filter on name/path.",
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        ws = _workspace_root()
        filt = (args.get("filter") or "").strip().lower()
        out_lines: list[str] = []

        def _gather_workers(label: str, patterns: list[str]) -> None:
            entries: list[str] = []
            for pat in patterns:
                for p in ws.glob(pat):
                    if not p.is_file():
                        continue
                    rel = p.relative_to(ws).as_posix()
                    if filt and filt not in rel.lower():
                        continue
                    # 拿 module docstring 前 200 字
                    excerpt = ""
                    try:
                        head = p.read_text(encoding="utf-8", errors="replace")[:2000]
                        ds = re.search(r'^"""([\s\S]*?)"""', head, re.MULTILINE)
                        if ds:
                            excerpt = " ".join(ds.group(1).split())[:200]
                    except OSError:
                        pass
                    entries.append(f"  - {rel}\n      {excerpt}")
            if entries:
                out_lines.append(f"## {label} ({len(entries)})")
                out_lines.extend(entries)
                out_lines.append("")

        _gather_workers(
            "team_supervisor workers",
            ["src/omnicompany/packages/services/_core/team_supervisor/workers/*.py"],
        )
        _gather_workers(
            "team_builder workers",
            ["src/omnicompany/packages/services/_core/team_builder/workers/*.py"],
        )
        _gather_workers(
            "kb multi_agent workers",
            ["src/omnicompany/packages/services/_learning/kb/multi_agent/workers/*.py"],
        )
        _gather_workers(
            "Controller worker_archive",
            ["data/boss_sight/worker_archive/*.md", "data/boss_sight/worker_archive/*.json"],
        )

        if not out_lines:
            return f"no workers matched filter={filt!r}"
        return "\n".join(out_lines).strip()


# ═══════════════════════════════════════════════════════════════════════
# audit_recent_subagent_traces — 语义死循环监督 (块 3 R11)
# 用户原话 §2.6: 总控 agent 监督过程, 避免语义上死循环
# ═══════════════════════════════════════════════════════════════════════


class AuditRecentSubagentTracesRouter(SingleToolRouter):
    """扫近期 plan_completion_log + subagent 状态, 找语义死循环嫌疑.

    数据源 (块 3 R11):
    - data/boss_sight/plan_completion_log/*.json (含 auto + 手动两种)
    - 同 plan_id 内: 计 verdict_from_subagent / status 重复模式

    死循环嫌疑判定 (用户原话 §2.6 留作判定的扩展空间):
    - 同 plan 内 ≥ 3 条 partial / FAIL 记录 → 标红
    - 同 plan 内 turn 多但无 status=done → 标红
    """

    TOOL_NAME: ClassVar[str] = "audit_recent_subagent_traces"
    DESCRIPTION: ClassVar[str] = (
        "Scan recent plan_completion_log entries (last N hours) and flag plans where "
        "subagents may be stuck in semantic loops (per user spec §2.6). "
        "Returns a list of suspect plans + the evidence (verdict patterns)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "lookback_hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": 168,
                "default": 24,
                "description": "How many hours back to scan. Default 24.",
            },
            "min_repeats": {
                "type": "integer",
                "minimum": 2,
                "default": 3,
                "description": "Min identical non-done verdicts to flag a plan. Default 3.",
            },
            "plan_id_filter": {
                "type": "string",
                "description": "Optional: only audit this single plan id.",
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        lookback_h = int(args.get("lookback_hours") or 24)
        min_repeats = int(args.get("min_repeats") or 3)
        plan_filter = (args.get("plan_id_filter") or "").strip()

        ws = _workspace_root()
        log_dir = ws / "data" / "boss_sight" / "plan_completion_log"
        if not log_dir.is_dir():
            return "no plan_completion_log/ — nothing to audit"

        cutoff_ts = datetime.now(timezone.utc).timestamp() - lookback_h * 3600
        by_plan: dict[str, list[dict]] = {}
        scanned = 0
        for p in log_dir.glob("*.json"):
            try:
                if p.stat().st_mtime < cutoff_ts:
                    continue
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            scanned += 1
            pid = str(rec.get("plan_id") or "_unknown_")
            if plan_filter and pid != plan_filter:
                continue
            by_plan.setdefault(pid, []).append(rec)

        suspects: list[dict] = []
        for pid, recs in by_plan.items():
            statuses = [r.get("status") for r in recs]
            verdicts = [r.get("verdict_from_subagent") for r in recs if r.get("verdict_from_subagent")]
            # 死循环嫌疑 #1: ≥ N 条 partial/FAIL 且 0 条 done
            non_done = [s for s in statuses if s and s != "done"]
            done_count = sum(1 for s in statuses if s == "done")
            if len(non_done) >= min_repeats and done_count == 0:
                suspects.append({
                    "plan_id": pid,
                    "evidence": f"{len(non_done)} non-done records, 0 done",
                    "status_seq": statuses[-min_repeats * 2:],
                    "verdict_seq": verdicts[-min_repeats * 2:],
                })

        lines = [
            f"audit: scanned {scanned} records across {len(by_plan)} plans "
            f"(lookback {lookback_h}h, min_repeats={min_repeats})"
        ]
        if not suspects:
            lines.append("no loop suspects ✓")
        else:
            lines.append(f"{len(suspects)} plan(s) may be stuck:")
            for s in suspects:
                lines.append(
                    f"  - {s['plan_id']}  evidence: {s['evidence']}"
                )
                lines.append(f"      status_seq: {s['status_seq']}")
                if s["verdict_seq"]:
                    lines.append(f"      verdict_seq: {s['verdict_seq']}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# plan_worker_bindings — plan -> worker 注册表 (块 3 R13)
# 用户原话 §5.1.2.1: 一个 plan 绑定一个 worker, 几个 plan 有上下承接关系可共用 worker
# ═══════════════════════════════════════════════════════════════════════


def _bindings_path() -> Path:
    return _workspace_root() / "data" / "boss_sight" / "plan_worker_bindings.json"


def _load_bindings() -> dict[str, dict]:
    p = _bindings_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_bindings(data: dict[str, dict]) -> None:
    p = _bindings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class ListPlanWorkerBindingsRouter(SingleToolRouter):
    """列出当前所有 plan -> worker 绑定."""

    TOOL_NAME: ClassVar[str] = "list_plan_worker_bindings"
    DESCRIPTION: ClassVar[str] = (
        "List all plan -> subagent worker bindings (data/boss_sight/plan_worker_bindings.json). "
        "Use to decide whether to reuse an existing worker session for a plan (§5.1.2.1) "
        "instead of spawning a fresh one."
    )
    INPUT_SCHEMA: ClassVar[dict] = {"type": "object", "properties": {}, "required": []}
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        data = _load_bindings()
        if not data:
            return "no plan -> worker bindings registered yet"
        lines = [f"{len(data)} binding(s):"]
        for plan_id, info in sorted(data.items()):
            lines.append(
                f"  - {plan_id} → worker={info.get('subagent_id', '?')} "
                f"provider={info.get('provider', '?')} "
                f"bound_at={info.get('bound_at', '?')}"
            )
        return "\n".join(lines)


class BindPlanToWorkerRouter(SingleToolRouter):
    """注册一个 plan -> worker 绑定. 同 plan 重复 bind 覆盖."""

    TOOL_NAME: ClassVar[str] = "bind_plan_to_worker"
    DESCRIPTION: ClassVar[str] = (
        "Register a plan_id -> subagent_id binding. After binding, subsequent spawn_subagent "
        "calls for that plan should reuse this worker session via `claude --resume`. "
        "Use after you spawn a fresh subagent and want to lock it to a plan for continuity (§5.1.2.1)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "minLength": 3},
            "subagent_id": {"type": "string", "minLength": 8},
            "provider": {
                "type": "string",
                "enum": list(STANDALONE_WORKER_PROVIDERS),
            },
        },
        "required": ["plan_id", "subagent_id"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        plan_id = args["plan_id"]
        subagent_id = args["subagent_id"]
        provider = args.get("provider") or PROVIDER_CLAUDE_CODE
        data = _load_bindings()
        previous = data.get(plan_id)
        data[plan_id] = {
            "plan_id": plan_id,
            "subagent_id": subagent_id,
            "provider": provider,
            "bound_at": _now_iso(),
        }
        _save_bindings(data)
        if previous:
            return (
                f"plan {plan_id} REBOUND: was {previous.get('subagent_id')} → "
                f"now {subagent_id} ({provider})"
            )
        return f"plan {plan_id} bound to worker {subagent_id} ({provider})"


class UnbindPlanFromWorkerRouter(SingleToolRouter):
    """删 plan -> worker 绑定."""

    TOOL_NAME: ClassVar[str] = "unbind_plan_from_worker"
    DESCRIPTION: ClassVar[str] = (
        "Remove a plan_id -> subagent_id binding. Use when the worker is done with the "
        "plan or when you want to free the plan for a new worker."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "minLength": 3},
        },
        "required": ["plan_id"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        plan_id = args["plan_id"]
        data = _load_bindings()
        if plan_id not in data:
            return f"plan {plan_id} was not bound — nothing to remove"
        info = data.pop(plan_id)
        _save_bindings(data)
        return f"unbound plan {plan_id} from worker {info.get('subagent_id', '?')}"


# ═══════════════════════════════════════════════════════════════════════
# 块 4 审阅台工具组 (R2 + R7 + R8 阻断查询)
# ═══════════════════════════════════════════════════════════════════════


def _get_review_store():
    """全局 MaterialStore (单例, 跟 reviewstage routes / WS hub 共用同一份, 写后 WS 自动推).

    优先用 reviewstage.routes.get_store() 单例; 失败再 fallback 临时新建 (单测用).
    """
    try:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
        return get_store()
    except Exception:  # noqa: BLE001
        from omnicompany.dashboard.boss_sight.reviewstage import MaterialStore
        from omnicompany.dashboard.boss_sight.reviewstage.material_types import (
            default_review_format_registry,
        )
        ws = _workspace_root()
        return MaterialStore(
            root=ws / "data" / "boss_sight" / "reviewstage",
            format_registry=default_review_format_registry(),
        )


def _check_mandatory_blockers(plan_id: str) -> list[dict]:
    """块 4 R8: spawn 前查 plan 有没有未通过的 mandatory material. 返回 blocker meta 列表."""
    try:
        store = _get_review_store()
    except Exception:  # noqa: BLE001
        _log.exception("review store init failed; skip mandatory check")
        return []
    blockers = store.has_unaccepted_mandatory(plan_id)
    return [
        {
            "id": b.id,
            "title": b.title,
            "status": b.status.value if hasattr(b.status, "value") else b.status,
        }
        for b in blockers
    ]


_MATERIAL_KIND_TO_EXT = {
    "image": ".png",  # 默认 png; 用户可显式 file_ext=.jpg/.svg
    "markdown": ".md",
    "html": ".html",
    "key_question": ".json",
    "custom_web_template": ".json",
}


class SubmitToReviewstageRouter(SingleToolRouter):
    """提交一份 material 到审阅台 — 块 4 R2 落实 §2.7.

    四种 material kind (Phase A):
    - image: 必填 file_path (workspace 相对路径) 或 inline_content (base64)
    - markdown: 文档内容 inline_content (markdown 文本) 或 file_path
    - html: 网页 inline_content (完整 HTML) 或 file_path
    - key_question: inline_content 是 JSON 字符串 (含 question / options / explanation 字段)

    必须指定 tier (4 级):
    - mandatory: 必验收 (未通过会阻断该 plan 后续 spawn — §4.6.1)
    - important: 重要 (用户可随时审, 找到问题可调方向)
    - processual: 有意义过程性 (审阅意义有但不强)
    - ignored: 其余 (不主动审阅)
    """

    TOOL_NAME: ClassVar[str] = "submit_to_reviewstage"
    DESCRIPTION: ClassVar[str] = (
        "Submit a material to the review stage for user审阅 (per user spec §2.7). "
        "Built-in kinds: image / markdown / html / key_question / custom_web_template; "
        "extensions must be registered as Format tags review.kind.*. "
        "custom_web_template: subagent 出结构化数据 (JSON via inline_content) + "
        "extra.data_schema_id 指定渲染模板 (§4.2.5 元编程). "
        "4 tiers: mandatory (blocks plan progress on未通过) / important / processual / ignored. "
        "Mandatory materials 阻断 spawn_subagent for the same plan until user accepts. "
        "Source 必须 traceable: 至少给 source_plan_id, ideally also source_subagent_id."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "description": "Built-in kind or a Format-registered review.kind.* extension.",
            },
            "tier": {
                "type": "string",
                "description": "Built-in tier or a Format-registered review.tier.* extension.",
            },
            "title": {
                "type": "string",
                "minLength": 3,
                "maxLength": 200,
                "description": "Short human title shown in the list view.",
            },
            "source_plan_id": {
                "type": "string",
                "description": "Plan this material belongs to (用于按 plan 聚合 + 阻断查).",
            },
            "source_subagent_id": {
                "type": "string",
                "description": "Optional: 哪个 subagent 产出.",
            },
            "file_path": {
                "type": "string",
                "description": "Workspace-relative path of the file to submit. 用于 image / 大文档.",
            },
            "inline_content": {
                "type": "string",
                "description": "Inline content. 用于短 markdown / html / key_question JSON. 跟 file_path 二选一.",
            },
            "annotations_allowed": {
                "type": "boolean",
                "default": True,
                "description": "False 表示产物不允许 AI 批注 (例如小说类, §4.5).",
            },
            "file_ext": {
                "type": "string",
                "description": "Override file 扩展名 (image 默认 .png; 可 .jpg/.svg).",
            },
            "data_schema_id": {
                "type": "string",
                "description": (
                    "只对 kind=custom_web_template 有意义. "
                    "标识渲染载体的 schema id (e.g. 'branch_storyline_v1'). "
                    "前端按这个 id 找对应模板组件渲染. 没注册的 schema 会 fallback 用 markdown 显示 JSON."
                ),
            },
            "extra_json": {
                "type": "string",
                "description": (
                    "JSON 对象, merge 进 material.extra. webgame-spec 用它给三件套引用 "
                    "{\"demo\":..., \"doc\":..., \"filetree_diff\":...}; 兄弟材料用 {\"attached_to\":...}。"
                ),
            },
            "project": {
                "type": "string",
                "description": (
                    "Required (except人工快捷创建端点). 项目名录真源=决策库(omni decisions list 查已有,"
                    "omni decisions record -p <project> 立项)。'unfiled'=未分组。"
                ),
            },
            "track": {
                "type": "string",
                "description": "Required. 阶段/轨道名, 如 信息审阅稿/交互审阅稿/工作报告。",
            },
            "version": {
                "type": "integer",
                "minimum": 1,
                "description": "Required. 版本号(同 version_family 内递增, 从 1 起)。",
            },
            "version_family": {
                "type": "string",
                "description": "版本族(同一份稿的多版本共 family)。省略则默认=title。",
            },
            "links_json": {
                "type": "string",
                "description": (
                    "JSON 对象, 形如 {\"parent\": \"mat_xxx\", \"supersedes\": [\"mat_yyy\"], "
                    "\"related\": [\"mat_zzz\"]}。值是 material id 或 material id 列表。"
                ),
            },
        },
        "required": ["kind", "tier", "title", "source_plan_id"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        kind = args["kind"]
        tier = args["tier"]
        title = args["title"]
        source_plan_id = args["source_plan_id"]
        source_subagent_id = args.get("source_subagent_id")
        file_path = args.get("file_path")
        inline = args.get("inline_content")
        annotations_allowed = bool(args.get("annotations_allowed", True))
        project = args.get("project")
        track = args.get("track")
        version = args.get("version")
        version_family = args.get("version_family")
        links_json = args.get("links_json")
        links: dict[str, Any] | None = None
        if links_json:
            try:
                links = json.loads(links_json)
            except json.JSONDecodeError as e:
                return f"ERROR: links_json must be a JSON object: {e}"
            if not isinstance(links, dict):
                return "ERROR: links_json must be a JSON object"

        if not file_path and not inline:
            return "ERROR: must provide file_path or inline_content"

        try:
            store = _get_review_store()
        except Exception as e:  # noqa: BLE001
            return f"ERROR: review store init failed: {type(e).__name__}: {e}"

        ws = _workspace_root()

        # 优先 file_path → stage 拷贝
        file_relpath: str | None = None
        used_inline = None
        if file_path:
            src = (ws / file_path).resolve()
            if not str(src).startswith(str(ws.resolve())):
                return f"ERROR: file_path outside workspace: {file_path}"
            if not src.is_file():
                return f"ERROR: file not found: {file_path}"
            ext = args.get("file_ext") or src.suffix or _MATERIAL_KIND_TO_EXT.get(kind, ".bin")
            try:
                file_relpath = store.stage_file_from_path(src, suggested_ext=ext)
            except Exception as e:  # noqa: BLE001
                return f"ERROR: stage file failed: {type(e).__name__}: {e}"
        elif kind in {"image"} and inline:
            # image inline: 当 base64 字符串. 校验长度
            import base64
            try:
                data = base64.b64decode(inline, validate=True)
            except Exception:  # noqa: BLE001
                return "ERROR: image inline_content must be valid base64"
            ext = args.get("file_ext") or ".png"
            file_relpath = store.stage_file_from_bytes(data, ext=ext)
        else:
            used_inline = inline

        extra: dict[str, Any] = {}
        if kind == "custom_web_template" and args.get("data_schema_id"):
            extra["data_schema_id"] = args["data_schema_id"]

        extra_json = args.get("extra_json")
        if extra_json:
            try:
                parsed_extra = json.loads(extra_json)
            except json.JSONDecodeError as e:
                return f"ERROR: extra_json must be a JSON object: {e}"
            if not isinstance(parsed_extra, dict):
                return "ERROR: extra_json must be a JSON object"
            extra.update(parsed_extra)

        try:
            m = store.create(
                kind=kind, tier=tier, title=title,
                source_subagent_id=source_subagent_id,
                source_plan_id=source_plan_id,
                file_relpath=file_relpath,
                inline_content=used_inline,
                annotations_allowed=annotations_allowed,
                extra=extra,
                project=project,
                track=track,
                version=version,
                version_family=version_family,
                links=links,
            )
        except ValueError as e:
            return f"ERROR: {e}"

        # 块 5 R1: custom_web_template — extra.data_schema_id 走 store extra
        if kind == "custom_web_template" and args.get("data_schema_id"):
            try:
                m.extra["data_schema_id"] = args["data_schema_id"]
                store._persist(m)  # noqa: SLF001  — 测试/真使用都接受
                store._notify("updated", m)
            except Exception:  # noqa: BLE001
                _log.exception("failed to set data_schema_id")

        structure_warnings = m.extra.get("structure_warnings") or []
        warn_msgs = (
            [str(w.get("message")) for w in structure_warnings if isinstance(w, dict) and w.get("message")]
            if isinstance(structure_warnings, list) else []
        )

        # 双保证·设施半边: 读本 kind 注册 Format 的 semantic_preconditions 作友情提示回给 agent,
        # 并把结构校验缺口逐条列出。prompt 半边 = preconditions 里指向的 docs/standards/review/*。
        from omnicompany.dashboard.boss_sight.reviewstage.material_types import (
            review_kind_format_preconditions,
        )
        preconds = review_kind_format_preconditions(kind, store.format_registry)
        hint_lines: list[str] = []
        if preconds:
            hint_lines.append("友情提示 — 本类材料的审阅格式要求:")
            hint_lines.extend(f"  · {p}" for p in preconds)
        if warn_msgs:
            hint_lines.append("待补(结构校验):")
            hint_lines.extend(f"  · {w}" for w in warn_msgs)
        hint_note = ("\n" + "\n".join(hint_lines)) if hint_lines else ""

        warn_count_note = (
            f" structure_warnings={len(structure_warnings)}."
            if isinstance(structure_warnings, list) and structure_warnings else ""
        )
        return (
            f"material submitted: id={m.id} kind={kind} tier={tier} plan={source_plan_id}. "
            f"Status=pending — user will审阅 in 审阅台."
            + warn_count_note
            + (" mandatory! will block spawn for this plan until accepted." if tier == "mandatory" else "")
            + hint_note
        )


class JudgeReviewstageMaterialRouter(SingleToolRouter):
    """Return a model-tier routing suggestion for a reviewstage material."""

    TOOL_NAME: ClassVar[str] = "judge_reviewstage_material"
    DESCRIPTION: ClassVar[str] = (
        "Judge a reviewstage material and return an abstract routing suggestion. "
        "Does not change the material verdict/status. model_hint is resolved later by model_resolver.py."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "material_id": {"type": "string", "minLength": 3},
            "context": {
                "type": "string",
                "description": "Optional extra context for the routing judge.",
            },
            "max_content_chars": {
                "type": "integer",
                "default": 6000,
                "minimum": 0,
                "maximum": 20000,
            },
        },
        "required": ["material_id"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        mid = args["material_id"]
        cap = int(args.get("max_content_chars") or 6000)
        try:
            store = _get_review_store()
            m = store.get(mid)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: review store read failed: {type(e).__name__}: {e}"
        if m is None:
            return f"ERROR: material {mid} not found"

        content = m.inline_content or ""
        if not content and m.file_relpath:
            fp = store.resolve_file_path(m)
            if fp is not None and fp.is_file():
                try:
                    content = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
        content = content[:cap]
        kind = m.kind.value if hasattr(m.kind, "value") else str(m.kind)
        tier = m.tier.value if hasattr(m.tier, "value") else str(m.tier)
        warnings = m.extra.get("structure_warnings") or []
        if not isinstance(warnings, list):
            warnings = []
        decision = decide_tier(
            kind=kind,
            tier=tier,
            title=m.title,
            content=content,
            structure_warnings=warnings,
            context=args.get("context") or "",
        ).to_dict()
        payload = {
            "material_id": mid,
            "status_unchanged": m.status.value if hasattr(m.status, "value") else m.status,
            "kind": kind,
            "tier": tier,
            "decision": decision,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class PushMaterialToUserRouter(SingleToolRouter):
    """块 4 R7: 主动把已 submit 的 material 推到用户面前 (§2.10).

    场景: 总控从审阅材料堆里"挑用户需要的", e.g. 用户问'子任务 X 进展如何' →
    总控找到 X 相关的 material → push 让前端高亮/弹窗.
    """

    TOOL_NAME: ClassVar[str] = "push_material_to_user"
    DESCRIPTION: ClassVar[str] = (
        "Push an existing material to the user (highlight / toast). "
        "Use when user asks about progress on a specific subagent/plan or when you "
        "decide the material warrants attention right now (per §2.10)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "material_id": {"type": "string", "minLength": 3},
            "reason": {
                "type": "string",
                "minLength": 5,
                "description": "Short reason shown to user (e.g. '这是你问的 X 子任务的成品')",
            },
        },
        "required": ["material_id", "reason"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        mid = args["material_id"]
        reason = args["reason"]
        try:
            store = _get_review_store()
            m = store.mark_pushed(mid, reason=reason)
        except KeyError:
            return f"ERROR: material {mid} not found"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: push failed: {type(e).__name__}: {e}"
        return f"pushed: id={mid} title={m.title!r} reason={reason!r}"


class AnnotateMaterialRouter(SingleToolRouter):
    """块 4 R6: 总控给 material 加 AI 批注 (§4.4/§4.5).

    批注跟正式内容分离 — annotations panel 显示, 不污染 material 主体.
    某些产物 (例如小说) submit 时 annotations_allowed=false 则禁止批注.
    """

    TOOL_NAME: ClassVar[str] = "annotate_material"
    DESCRIPTION: ClassVar[str] = (
        "Add an AI annotation to a material (per §4.4 / §4.5). "
        "Annotations are historical commentary shown in a separate panel — they do NOT "
        "modify the material's primary content. Use to point out things for user attention."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "material_id": {"type": "string", "minLength": 3},
            "content": {
                "type": "string",
                "minLength": 5,
                "description": "The annotation text.",
            },
            "target": {
                "type": "object",
                "description": (
                    "Optional location. image: {x,y,w,h} (0-1 normalized). "
                    "markdown: {line_start, line_end}. "
                    "html: {selector: 'css.selector'}. "
                    "key_question: {question_index: int}."
                ),
            },
        },
        "required": ["material_id", "content"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        from omnicompany.dashboard.boss_sight.reviewstage import AnnotationKind
        mid = args["material_id"]
        content = args["content"]
        target = args.get("target") or {}
        try:
            store = _get_review_store()
            ann = store.add_annotation(
                mid, content=content, kind=AnnotationKind.ai,
                author="controller", target=target,
            )
        except KeyError:
            return f"ERROR: material {mid} not found"
        except PermissionError as e:
            return f"ERROR: {e}"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: annotate failed: {type(e).__name__}: {e}"
        return f"annotated: ann_id={ann.id} material={mid}"


class ListReviewstageMaterialsRouter(SingleToolRouter):
    """列审阅台 material — 给总控看用户面前是啥状态. §2.7 整理用."""

    TOOL_NAME: ClassVar[str] = "list_reviewstage_materials"
    DESCRIPTION: ClassVar[str] = (
        "List materials in the review stage. Filter by status/tier/plan. "
        "Use to know what's pending user attention before deciding next actions."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending", "accepted", "rejected", "blocked"],
            },
            "tier": {
                "type": "string",
                "enum": ["mandatory", "important", "processual", "ignored"],
            },
            "plan_id": {"type": "string"},
            "max_results": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        try:
            store = _get_review_store()
        except Exception as e:  # noqa: BLE001
            return f"ERROR: store init failed: {type(e).__name__}: {e}"
        items = store.list(
            status=args.get("status"),
            tier=args.get("tier"),
            plan_id=args.get("plan_id"),
        )
        max_n = int(args.get("max_results") or 30)
        items = items[:max_n]
        if not items:
            return "no materials match filter"
        lines = [f"{len(items)} material(s):"]
        for m in items:
            tier_v = m.tier.value if hasattr(m.tier, "value") else m.tier
            st_v = m.status.value if hasattr(m.status, "value") else m.status
            kind_v = m.kind.value if hasattr(m.kind, "value") else m.kind
            lines.append(
                f"  - {m.id} [{tier_v}/{st_v}] kind={kind_v} plan={m.source_plan_id} "
                f"title={m.title!r}"
            )
        return "\n".join(lines)


__all__ = [
    "SubmitResponseRouter",
    "SpawnSubagentRouter",
    "ForkSubagentForReportRouter",
    "EmitEventRouter",
    "ProposeChangeRouter",
    "AuditPlansForTodoRouter",
    "AuditRecentSubagentTracesRouter",
    "RecordPlanCompletionRouter",
    "ListPromptArchiveRouter",
    "ListWorkerArchiveRouter",
    "ListPlanWorkerBindingsRouter",
    "BindPlanToWorkerRouter",
    "UnbindPlanFromWorkerRouter",
    # 块 4 审阅台
    "SubmitToReviewstageRouter",
    "SubmitToReviewstageRouter",
    "JudgeReviewstageMaterialRouter",
    "PushMaterialToUserRouter",
    "AnnotateMaterialRouter",
    "ListReviewstageMaterialsRouter",
]
