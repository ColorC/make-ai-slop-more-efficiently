# [OMNI] origin=ai-ide ts=2026-05-25 type=cli
# [OMNI] material_id="material:cli.boss_sight.unified_command_router.py"
"""BOSS SIGHT cli 子命令 — 把原来的 17 个 function call tool 迁移成 omni cli 子命令.

用户原话 (2026-05-25):
> 错误范式，不要塞进function call，当内容多时用cli命令格式组织 [...]
> 用cli这样谁都可以是总控，需要一个注册机制，不能被多方使用，
> 也不能被subagent使用。可以被外部或者总控agent使用 [...]

落实:
- 全部 BOSS SIGHT 操作走 omni cli (Bash + click)
- caller-based access control 走 OMNI_CLI_CALLER env (external/controller/subagent)
- subagent 不能调 spawn/fork (防递归)
- function call 仅保留 submit_response (LLM 末步协议本身)

命令归类 (扩现有 group + 新增 3 个):
- 扩 omni worker: spawn / fork / signal / bind / unbind / bindings / status / audit-traces / archive
- 扩 omni plan: complete / audit / binder
- 新 omni review: submit / list / annotate / push / verdict
- 新 omni prompt: list
- 新 omni propose: change

实现:
为避免 logic 重复, cli 命令通过 _invoke_router(cls, args) 调原 Router 类的 _execute().
原 Router 类保留在 tools.py (作为内部 handler), 但 BossSightControllerWorker.TOOL_ROUTERS
不再 include 它们 — 控制器只用 Bash 调 cli.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click

from omnicompany.bus.memory import MemoryBus
from omnicompany.runtime.agent.agent_loop_tools import ToolContext

from .._access import any_caller, external_or_controller
from omnicompany.dashboard.boss_sight.controller.worker_contract import (
    PROVIDER_CLAUDE_CODE,
    STANDALONE_WORKER_PROVIDERS,
    WORKER_KIND_STANDALONE,
    WORKER_KINDS,
)


# ────────────────────────────────────────────────────────────────────────
# Router invoke helper (cli → Router._execute 桥接)
# ────────────────────────────────────────────────────────────────────────


_BUS: MemoryBus | None = None


def _ensure_bus() -> MemoryBus:
    """Lazy init shared MemoryBus per process. cli 每次调用过路用, 不持久化."""
    global _BUS
    if _BUS is None:
        _BUS = MemoryBus()
        # MemoryBus.connect() 是 async; cli 是 sync 入口, 用 asyncio.run 包一下
        try:
            asyncio.get_event_loop().run_until_complete(_BUS.connect())
        except RuntimeError:
            asyncio.run(_BUS.connect())
    return _BUS


def _invoke_router(router_cls, args: dict[str, Any]) -> str:
    """实例化 router + 调 _execute. 返回 str 输出 (cli 直接 echo)."""
    bus = _ensure_bus()
    router = router_cls(bus=bus)
    ctx = ToolContext(trace_id=f"cli-{router_cls.TOOL_NAME}", turn_number=0)
    try:
        return router._execute(args, ctx)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"{router_cls.TOOL_NAME} failed: {type(e).__name__}: {e}")


def _run_third_party_audit(
    *,
    against_conversation: str | None,
    provider: str,
    against_plan: str | None,
    model: str | None,
    repo_root: str | None,
    no_persist: bool,
    as_json: bool,
) -> None:
    """第三方 plan/对话落地审计 — 跑 async audit agent(自包 asyncio.run, 不复用同步 _invoke_router).

    services._core.plan_audit.run.* 内部各自 asyncio.run, 避免跨 loop. 报告打印 + 留档.
    """
    from omnicompany.packages.services._core.plan_audit.run import (
        run_conversation_audit,
        run_plan_audit,
        render_report,
        persist_report,
    )

    if against_conversation and against_plan:
        raise click.ClickException("--against-conversation 和 --against-plan 只能择一.")

    try:
        if against_conversation:
            result = run_conversation_audit(
                session_id=against_conversation, provider=provider,
                model=model, repo_root=repo_root,
            )
        else:
            result = run_plan_audit(plan_id=against_plan, model=model, repo_root=repo_root)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"plan audit failed: {type(e).__name__}: {e}")

    if not result.get("ok"):
        raise click.ClickException(result.get("error", "plan audit 失败(无 error 字段)."))

    # 留档(默认开). persist 失败不阻塞输出, 只 warn.
    if not no_persist:
        try:
            paths = persist_report(result, trace=result.get("meta", {}).get("session_id")
                                   or result.get("meta", {}).get("plan_id", ""))
            result["report_paths"] = paths
        except Exception as e:  # noqa: BLE001
            click.echo(f"[warn] 报告留档失败(非致命): {type(e).__name__}: {e}", err=True)

    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        click.echo(render_report(result))
        if result.get("report_paths"):
            click.echo(f"\n[报告已留档] md={result['report_paths']['md_path']} "
                       f"json={result['report_paths']['json_path']}")


# ────────────────────────────────────────────────────────────────────────
# omni worker 扩展 (spawn / fork / signal / bind / unbind / bindings / status / audit-traces / archive)
# ────────────────────────────────────────────────────────────────────────


def _register_worker_commands(cmd_worker: click.Group) -> None:
    """挂 BOSS SIGHT subagent 调度命令到现有 omni worker group."""

    from omnicompany.dashboard.boss_sight.controller.tools import (
        SpawnSubagentRouter,
        ForkSubagentForReportRouter,
        EmitEventRouter,
        BindPlanToWorkerRouter,
        UnbindPlanFromWorkerRouter,
        ListPlanWorkerBindingsRouter,
        AuditRecentSubagentTracesRouter,
        ListWorkerArchiveRouter,
    )

    @cmd_worker.command("spawn")
    @click.argument("plan_id")
    @click.argument("initial_prompt")
    @click.option("--kind", "worker_kind", type=click.Choice(list(WORKER_KINDS)),
                  default=WORKER_KIND_STANDALONE, show_default=True)
    @click.option("--provider", type=click.Choice(list(STANDALONE_WORKER_PROVIDERS)),
                  default=PROVIDER_CLAUDE_CODE, show_default=True)
    @click.option("--model-hint", type=click.Choice(["high", "low", "default", "auto"]), default="default")
    @click.option("--ctx-cap", "ctx_upper_bound_tokens", type=int, default=None,
                  help="ctx upper bound tokens. Default 400000 (codex auto-cap 256000).")
    @click.option("--cwd", default=None)
    @click.option("--extra-standards", multiple=True, help="Repeatable: extra standards file paths.")
    @click.option("--extra-templates", multiple=True, help="Repeatable: extra template file paths.")
    @click.option("--skip-plan-inject", is_flag=True)
    @click.option("--worktree-isolation", type=click.Choice(["none", "git_worktree"]),
                  default="none", show_default=True,
                  help="Run standalone worker in an isolated git worktree.")
    @click.option("--worktree-base", default=None,
                  help="Base ref for --worktree-isolation git_worktree. Default HEAD.")
    @click.option("--override-mandatory-block", is_flag=True,
                  help="Bypass §4.6.1 mandatory-material block gate.")
    @external_or_controller
    def cmd_worker_spawn(plan_id, initial_prompt, worker_kind, provider, model_hint,
                         ctx_upper_bound_tokens, cwd, extra_standards, extra_templates,
                         skip_plan_inject, worktree_isolation, worktree_base,
                         override_mandatory_block):
        """Spawn a subagent worker (BOSS SIGHT §5.1). Not callable by subagent (防递归)."""
        args = {
            "worker_kind": worker_kind, "plan_id": plan_id, "initial_prompt": initial_prompt,
            "provider": provider, "model_hint": model_hint,
            "extra_standards": list(extra_standards) or None,
            "extra_templates": list(extra_templates) or None,
            "skip_plan_inject": skip_plan_inject,
            "worktree_isolation": worktree_isolation,
            "override_mandatory_block": override_mandatory_block,
        }
        if worktree_base is not None:
            args["worktree_base"] = worktree_base
        if ctx_upper_bound_tokens is not None:
            args["ctx_upper_bound_tokens"] = ctx_upper_bound_tokens
        if cwd is not None:
            args["cwd"] = cwd
        click.echo(_invoke_router(SpawnSubagentRouter, args))

    @cmd_worker.command("fork")
    @click.argument("source_subagent_id")
    @click.argument("report_prompt")
    @click.option("--model-hint", type=click.Choice(["high", "low", "default"]), default="default")
    @external_or_controller
    def cmd_worker_fork(source_subagent_id, report_prompt, model_hint):
        """Fork a running subagent for report (§6.2, claude_code only)."""
        click.echo(_invoke_router(ForkSubagentForReportRouter, {
            "source_subagent_id": source_subagent_id,
            "report_prompt": report_prompt,
            "model_hint": model_hint,
        }))

    @cmd_worker.command("signal")
    @click.argument("event_type", type=click.Choice([
        "subagent.unblock", "subagent.shutdown", "subagent.send_message",
        "plan.todo_correct", "plan.log_completion", "reviewstage.submit",
    ]))
    @click.option("--subagent-id", "target_subagent_id", default=None)
    @click.option("--plan-id", "target_plan_id", default=None)
    @click.option("--payload", default="{}", help="JSON-encoded payload.")
    @external_or_controller
    def cmd_worker_signal(event_type, target_subagent_id, target_plan_id, payload):
        """Emit a control-flow event to a worker (§6.3 unblock/shutdown/etc)."""
        try:
            payload_dict = json.loads(payload)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"invalid --payload JSON: {e}")
        args = {"event_type": event_type, "payload": payload_dict}
        if target_subagent_id: args["target_subagent_id"] = target_subagent_id
        if target_plan_id: args["target_plan_id"] = target_plan_id
        click.echo(_invoke_router(EmitEventRouter, args))

    @cmd_worker.command("bind")
    @click.argument("plan_id")
    @click.argument("subagent_id")
    @click.option("--provider", type=click.Choice(list(STANDALONE_WORKER_PROVIDERS)),
                  default=PROVIDER_CLAUDE_CODE, show_default=True)
    @external_or_controller
    def cmd_worker_bind(plan_id, subagent_id, provider):
        """Bind plan ↔ worker (§5.1.2.1 一个 plan 一般绑一个 worker)."""
        click.echo(_invoke_router(BindPlanToWorkerRouter, {
            "plan_id": plan_id, "subagent_id": subagent_id, "provider": provider,
        }))

    @cmd_worker.command("unbind")
    @click.argument("plan_id")
    @external_or_controller
    def cmd_worker_unbind(plan_id):
        """Remove plan ↔ worker binding."""
        click.echo(_invoke_router(UnbindPlanFromWorkerRouter, {"plan_id": plan_id}))

    @cmd_worker.command("bindings")
    @any_caller
    def cmd_worker_bindings():
        """List all plan ↔ worker bindings."""
        click.echo(_invoke_router(ListPlanWorkerBindingsRouter, {}))

    @cmd_worker.command("audit-traces")
    @click.option("--lookback-hours", type=int, default=24, show_default=True)
    @click.option("--min-repeats", type=int, default=3, show_default=True)
    @click.option("--plan-id", "plan_id_filter", default=None)
    @any_caller
    def cmd_worker_audit_traces(lookback_hours, min_repeats, plan_id_filter):
        """Scan recent plan_completion_log for semantic loop suspects (§2.6)."""
        args = {"lookback_hours": lookback_hours, "min_repeats": min_repeats}
        if plan_id_filter: args["plan_id_filter"] = plan_id_filter
        click.echo(_invoke_router(AuditRecentSubagentTracesRouter, args))

    @cmd_worker.command("archive")
    @click.option("--filter", "filter_", default=None)
    @any_caller
    def cmd_worker_archive(filter_):
        """List omnicompany worker definitions + controller worker_archive (§2.11)."""
        args = {}
        if filter_: args["filter"] = filter_
        click.echo(_invoke_router(ListWorkerArchiveRouter, args))


# ────────────────────────────────────────────────────────────────────────
# omni plan 扩展 (complete / audit / binder)
# ────────────────────────────────────────────────────────────────────────


def _register_plan_commands(cmd_plan: click.Group) -> None:
    from omnicompany.dashboard.boss_sight.controller.tools import (
        RecordPlanCompletionRouter,
        AuditPlansForTodoRouter,
        ListPlanWorkerBindingsRouter,
    )

    @cmd_plan.command("complete")
    @click.argument("plan_id")
    @click.option("--status", type=click.Choice(["in_progress", "blocked", "partial", "done", "abandoned"]),
                  required=True)
    @click.option("--assessment", required=True, help="Short factual assessment.")
    @click.option("--todo-done", type=int, default=0)
    @click.option("--todo-total", type=int, default=0)
    @click.option("--produced", "produced_materials", multiple=True,
                  help="Repeatable: paths / descriptors of produced material.")
    @external_or_controller
    def cmd_plan_complete(plan_id, status, assessment, todo_done, todo_total, produced_materials):
        """Record a plan completion snapshot (§2.9)."""
        click.echo(_invoke_router(RecordPlanCompletionRouter, {
            "plan_id": plan_id, "status": status, "assessment": assessment,
            "todo_done": todo_done, "todo_total": todo_total,
            "produced_materials": list(produced_materials),
        }))

    @cmd_plan.command("audit")
    @click.option("--missing-todo", "audit_missing_todo", is_flag=True,
                  help="Find plans missing the §2.16.1 required todo checklist.")
    @click.option("--against-conversation", "against_conversation", default=None,
                  help="第三方落地审计(输入(1)): 审计某对话(session_id)里用户每条指示的落地情况.")
    @click.option("--provider", type=click.Choice(["claude_code", "codex"]), default="claude_code",
                  show_default=True, help="--against-conversation 的对话来源.")
    @click.option("--against-plan", "against_plan", default=None,
                  help="第三方落地审计(输入(2)): 审计某 plan(plan_id)+相关对话的落地情况.")
    @click.option("--model", default=None, help="审计 agent 用的模型. 省略=默认(qwen3.6-plus).")
    @click.option("--repo-root", default=None, help="审计目标仓库根(bash cwd). 省略=对话 cwd 或 omni 仓库根.")
    @click.option("--no-persist", is_flag=True, help="不把报告留档到 data/services/plan_audit/.")
    @click.option("--json", "as_json", is_flag=True, help="输出结构化 JSON(便于网页/管线消费), 不打印人读报告.")
    @click.option("--category", "category_filter", default=None)
    @click.option("--max", "max_results", type=int, default=50, show_default=True)
    @any_caller
    def cmd_plan_audit(audit_missing_todo, against_conversation, provider, against_plan,
                       model, repo_root, no_persist, as_json, category_filter, max_results):
        """Audit plans / conversations against requirements.

        三种模式(择一):
          --missing-todo                  : 旧行为, 扫缺 todo checklist 的 plan (§2.16.1).
          --against-conversation <sid>    : 第三方落地审计(输入(1)), 审计对话里用户指示落地.
          --against-plan <plan_id>        : 第三方落地审计(输入(2)), 审计 plan + 相关对话落地.
        """
        # 第三方落地审计走 async audit agent — 不能用 _invoke_router(它是同步 router).
        if against_conversation or against_plan:
            _run_third_party_audit(
                against_conversation=against_conversation, provider=provider,
                against_plan=against_plan, model=model, repo_root=repo_root,
                no_persist=no_persist, as_json=as_json,
            )
            return
        # 旧 missing-todo 模式保持原样不动.
        if not audit_missing_todo:
            raise click.ClickException(
                "must specify an audit mode: --missing-todo | --against-conversation <sid> | --against-plan <plan_id>"
            )
        args = {"max_results": max_results}
        if category_filter: args["category_filter"] = category_filter
        click.echo(_invoke_router(AuditPlansForTodoRouter, args))

    @cmd_plan.command("binder")
    @any_caller
    def cmd_plan_binder():
        """Show plan ↔ worker bindings (alias of `omni worker bindings`)."""
        click.echo(_invoke_router(ListPlanWorkerBindingsRouter, {}))


# ────────────────────────────────────────────────────────────────────────
# 新 omni review group (submit / list / annotate / push / verdict)
# ────────────────────────────────────────────────────────────────────────


@click.group("review")
def cmd_review() -> None:
    """BOSS SIGHT reviewstage: submit / list / annotate / push / verdict materials."""


@cmd_review.command("submit")
@click.option("--kind", required=True, type=click.Choice(["image", "markdown", "html", "key_question", "custom_web_template", "video", "webgame-spec", "plan", "static-report", "demo", "aigc-image", "agent-workflow-report"]),
              help="按内容格式选, 别按用途名望文生义: md 报告=markdown/plan/agent-workflow-report; "
                   "static-report/demo/html=自包含 HTML 网页(或 extra.live_url)。kind 决定渲染器, 错配会被拒。")
@click.option("--tier", required=True, type=click.Choice(["mandatory", "important", "processual", "ignored"]))
@click.option("--title", required=True)
@click.option("--plan-id", "source_plan_id", required=True)
@click.option("--subagent-id", "source_subagent_id", default=None)
@click.option("--file", "file_path", default=None)
@click.option("--content", "inline_content", default=None)
@click.option("--annotations-allowed/--no-annotations", default=True)
@click.option("--file-ext", default=None)
@click.option("--schema-id", "data_schema_id", default=None)
@click.option("--extra-json", "extra_json", default=None,
              help="JSON 对象 merge 进 material.extra (webgame-spec 三件套引用 / 兄弟材料 attached_to)。"
                   "禁止塞 project/track/version/version_family — 用同名正式选项。")
@click.option("--project", default=None,
              help="必填(store 会拒绝空值)。项目名录真源=决策库: `omni decisions list` 看已有项目, "
                   "或 `omni decisions record -p <project>` 先立项。'unfiled'=未分组待办。")
@click.option("--track", default=None,
              help="必填。阶段/轨道名, 如 信息审阅稿/交互审阅稿/工作报告。")
@click.option("--version", "version", type=int, default=None,
              help="必填。版本号(同 --version-family 内递增, 从 1 起)。")
@click.option("--version-family", "version_family", default=None,
              help="版本族(同一份稿的多版本共 family)。省略则默认=--title。")
@click.option("--links-json", "links_json", default=None,
              help='JSON 对象, 如 {"parent":"mat_xxx","supersedes":["mat_yyy"],"related":["mat_zzz"]}。')
# M2 Phase 2 步骤 4: subagent 在收尾时通过 omni review submit emit material 是核心协议
# (emit_material_protocol = A_explicit_tool, 见 plan.md). 把 submit 放开给三档 caller.
# annotate / push 仍受限 — 那是总控/外部的活, subagent 不应该自评和自推.
@any_caller
def cmd_review_submit(kind, tier, title, source_plan_id, source_subagent_id, file_path,
                      inline_content, annotations_allowed, file_ext, data_schema_id, extra_json,
                      project, track, version, version_family, links_json):
    """Submit a material to the review stage (§2.7).

    project/track/version 是强制标签(阻断校验在 store.create 唯一收口, 这里只透传;
    缺了会报错, 错误信息里会说明怎么补, 不是本命令自己重复一份规则)。

    错误样本:
      omni review submit --kind markdown --tier important --title X --plan-id P --content C
        → ERROR: project is required — 补 `--project <project>` ...
      omni review submit ... --project not-a-real-project ...
        → ERROR: project 'not-a-real-project' 不在项目名录里 ...
    """
    from omnicompany.dashboard.boss_sight.controller.tools import SubmitToReviewstageRouter
    args = {"kind": kind, "tier": tier, "title": title, "source_plan_id": source_plan_id,
            "annotations_allowed": annotations_allowed}
    if source_subagent_id: args["source_subagent_id"] = source_subagent_id
    if file_path: args["file_path"] = file_path
    if inline_content: args["inline_content"] = inline_content
    if file_ext: args["file_ext"] = file_ext
    if data_schema_id: args["data_schema_id"] = data_schema_id
    if extra_json: args["extra_json"] = extra_json
    if project is not None: args["project"] = project
    if track is not None: args["track"] = track
    if version is not None: args["version"] = version
    if version_family is not None: args["version_family"] = version_family
    if links_json: args["links_json"] = links_json
    click.echo(_invoke_router(SubmitToReviewstageRouter, args))


@cmd_review.command("filetree-diff")
@click.option("--tier", required=True, type=click.Choice(["mandatory", "important", "processual", "ignored"]))
@click.option("--title", required=True)
@click.option("--plan-id", "source_plan_id", required=True)
@click.option("--subagent-id", "source_subagent_id", default=None)
@click.option("--root", required=True, help="diff 根目录(git 仓或任意目录)")
@click.option("--ref", default=None, help="git ref 或 A..B 区间(走 git diff)")
@click.option("--snapshot", is_flag=True, help="目录快照(非 git, 全部当 added)")
@click.option("--since", default=None, help="时间窗起 ISO(配 --until, 按 mtime)")
@click.option("--until", default=None, help="时间窗止 ISO")
@click.option("--path", "paths", multiple=True, help="手动文件(可多个, 会校验存在/在 root 内)")
@click.option("--paths-file", default=None, help="每行一个文件路径")
@click.option("--attached-to", default=None, help="父 spec 材料 id(本 diff 作其兄弟材料)")
@click.option("--include-unchanged", is_flag=True, help="附 diff 目录同级未改文件(供前端'显示全部')")
@click.option("--no-inline-diff", "no_inline", is_flag=True, help="不内联 diff 文本")
@click.option("--no-preview", "no_preview", is_flag=True, help="不内嵌图片/html 预览")
@click.option("--project", default=None, help="必填(store 会拒绝空值)。项目名录真源=决策库。")
@click.option("--track", default=None, help="必填。阶段/轨道名。")
@click.option("--version", "version", type=int, default=None, help="必填。版本号(从 1 起)。")
@click.option("--version-family", "version_family", default=None, help="省略则默认=--title。")
@any_caller
def cmd_review_filetree_diff(tier, title, source_plan_id, source_subagent_id, root, ref, snapshot,
                             since, until, paths, paths_file, attached_to, include_unchanged, no_inline, no_preview,
                             project, track, version, version_family):
    """生成文件树 diff 并作为审阅材料(custom_web_template / filetree_diff_v1)提交。"""
    import json as _json
    from omnicompany.dashboard.boss_sight.reviewstage.filetree_diff import build_filetree_diff
    from omnicompany.dashboard.boss_sight.controller.tools import SubmitToReviewstageRouter
    path_list = list(paths)
    if paths_file:
        try:
            with open(paths_file, encoding="utf-8") as fh:
                path_list += [ln.strip() for ln in fh if ln.strip()]
        except OSError as e:
            raise click.ClickException(f"读 paths-file 失败: {e}")
    if ref:
        mode = "git_ref"
    elif since or until:
        mode = "time_window"
    elif path_list:
        mode = "manual"
    elif snapshot:
        mode = "directory"
    else:
        raise click.ClickException("需指定一种源: --ref / --since|--until / --path|--paths-file / --snapshot")
    try:
        payload = build_filetree_diff(
            root=root, mode=mode, ref=ref, since=since, until=until,
            paths=path_list, include_unchanged=include_unchanged, inline_diff=not no_inline,
            embed_preview=not no_preview,
        )
    except ValueError as e:
        raise click.ClickException(str(e))
    args = {
        "kind": "custom_web_template", "tier": tier, "title": title,
        "source_plan_id": source_plan_id, "annotations_allowed": True,
        "inline_content": _json.dumps(payload, ensure_ascii=False),
        "data_schema_id": "filetree_diff_v1",
    }
    if source_subagent_id:
        args["source_subagent_id"] = source_subagent_id
    if attached_to:
        args["extra_json"] = _json.dumps({"attached_to": attached_to})
    if project is not None: args["project"] = project
    if track is not None: args["track"] = track
    if version is not None: args["version"] = version
    if version_family is not None: args["version_family"] = version_family
    click.echo(_invoke_router(SubmitToReviewstageRouter, args))


@cmd_review.command("list")
@click.option("--status", type=click.Choice(["pending", "accepted", "rejected", "blocked"]), default=None)
@click.option("--tier", type=click.Choice(["mandatory", "important", "processual", "ignored"]), default=None)
@click.option("--plan-id", default=None)
@click.option("--max", "max_results", type=int, default=30, show_default=True)
@any_caller
def cmd_review_list(status, tier, plan_id, max_results):
    """List review stage materials."""
    from omnicompany.dashboard.boss_sight.controller.tools import ListReviewstageMaterialsRouter
    args = {"max_results": max_results}
    if status: args["status"] = status
    if tier: args["tier"] = tier
    if plan_id: args["plan_id"] = plan_id
    click.echo(_invoke_router(ListReviewstageMaterialsRouter, args))


@cmd_review.command("judge")
@click.argument("material_id")
@click.option("--context", default=None)
@click.option("--max-content-chars", type=int, default=6000, show_default=True)
@external_or_controller
def cmd_review_judge(material_id, context, max_content_chars):
    """Judge a reviewstage material and return model_hint advice."""
    from omnicompany.dashboard.boss_sight.controller.tools import JudgeReviewstageMaterialRouter
    args = {"material_id": material_id, "max_content_chars": max_content_chars}
    if context:
        args["context"] = context
    click.echo(_invoke_router(JudgeReviewstageMaterialRouter, args))


@cmd_review.command("annotate")
@click.argument("material_id")
@click.argument("content")
@click.option("--target", default=None, help="JSON-encoded target (location selector).")
@external_or_controller
def cmd_review_annotate(material_id, content, target):
    """Add an AI annotation to a material (§4.4 / §4.5)."""
    from omnicompany.dashboard.boss_sight.controller.tools import AnnotateMaterialRouter
    args = {"material_id": material_id, "content": content}
    if target:
        try:
            args["target"] = json.loads(target)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"invalid --target JSON: {e}")
    click.echo(_invoke_router(AnnotateMaterialRouter, args))


@cmd_review.command("push")
@click.argument("material_id")
@click.argument("reason")
@external_or_controller
def cmd_review_push(material_id, reason):
    """Push an existing material to the user (§2.10)."""
    from omnicompany.dashboard.boss_sight.controller.tools import PushMaterialToUserRouter
    click.echo(_invoke_router(PushMaterialToUserRouter, {
        "material_id": material_id, "reason": reason,
    }))


@cmd_review.command("relay")
@click.argument("material_id")
@click.option("--reason", required=True, help="带反馈的审阅意见(非 yes/no)")
@click.option("--plan-id", "plan_id", default=None, help="material 的源 plan(自动找不到时给)")
@click.option("--task-id", "task_id", default=None, help="直接指定回流到哪个 task")
@click.option("--verdict", type=click.Choice(["rejected", "annotated"]), default="rejected")
@click.option("--no-inject", is_flag=True, help="只定位+留痕, 不实际注入会话")
@click.option("--json", "as_json", is_flag=True)
@external_or_controller
def cmd_review_relay(material_id, reason, plan_id, task_id, verdict, no_inject, as_json):
    """审阅意见回流: 用户带反馈的驳回/批注 → 注入回原 agent 会话继续 (修断掉的回路)。"""
    import json as _json
    from omnicompany.packages.services._core.lifecycle.review_relay import relay_feedback
    res = relay_feedback(material_id, reason, plan_id=plan_id, task_id=task_id,
                         verdict=verdict, inject=not no_inject)
    if as_json:
        click.echo(_json.dumps(res, ensure_ascii=False, indent=2))
    else:
        click.echo(res.get("summary", _json.dumps(res, ensure_ascii=False)))
    if not res.get("ok"):
        raise SystemExit(1)


# ────────────────────────────────────────────────────────────────────────
# 新 omni prompt group (list)
# ────────────────────────────────────────────────────────────────────────


@click.group("prompt")
def cmd_prompt() -> None:
    """List prompt archive (Claude Code skills + omnicompany standards + 总控自家归档)."""


@cmd_prompt.command("list")
@click.option("--filter", "filter_", default=None)
@any_caller
def cmd_prompt_list(filter_):
    """List all prompts / skills / standards (§2.11)."""
    from omnicompany.dashboard.boss_sight.controller.tools import ListPromptArchiveRouter
    args = {}
    if filter_: args["filter"] = filter_
    click.echo(_invoke_router(ListPromptArchiveRouter, args))


# ────────────────────────────────────────────────────────────────────────
# 新 omni propose group (change)
# ────────────────────────────────────────────────────────────────────────


@click.group("propose")
def cmd_propose() -> None:
    """Propose changes to be reviewed by the external maintenance session (§2.13)."""


@cmd_propose.command("change")
@click.argument("kind", type=click.Choice(["prompt_modification", "guard_change", "summarize_to_component"]))
@click.option("--rationale", required=True)
@click.option("--content-draft", "content_draft", required=True, help="Concrete draft (markdown/diff/yaml).")
@click.option("--target-location", default=None)
@click.option("--component-type", type=click.Choice(["template", "standards", "guard", "prompt_template", "skill"]),
              default=None)
@external_or_controller
def cmd_propose_change(kind, rationale, content_draft, target_location, component_type):
    """Send a proposal — persisted to data/boss_sight/proposals/<ts>.json."""
    from omnicompany.dashboard.boss_sight.controller.tools import ProposeChangeRouter
    args = {"kind": kind, "rationale": rationale, "content_draft": content_draft}
    if target_location: args["target_location"] = target_location
    if component_type: args["component_type"] = component_type
    click.echo(_invoke_router(ProposeChangeRouter, args))


# ────────────────────────────────────────────────────────────────────────
# 新 omni agent group (whoami / request-review) — agent 自我身份 + 主动请审阅本对话
# 用户(2026-06-25): "作为 agent 你应当可以感知自己的身份信息并输入自己的身份信息,
#                    主动让用户专注于本对话审阅。"
# ────────────────────────────────────────────────────────────────────────


def _dashboard_base() -> str:
    import os
    return os.environ.get("OMNI_DASHBOARD_URL", "http://127.0.0.1:8210").rstrip("/")


def _post_dashboard(path: str, payload: dict[str, Any], *, timeout: float = 6.0) -> dict[str, Any] | None:
    """POST 到运行中的看板(走 8210 反代 boss-sight)。失败返回 None(调用方回落本地)。"""
    import urllib.error
    import urllib.request
    url = f"{_dashboard_base()}/api/boss-sight{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 本机回环
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


@click.group("agent")
def cmd_agent() -> None:
    """Agent 自我身份 / 主动请用户审阅本对话(work-report 控制台)。"""


@cmd_agent.command("whoami")
@click.option("--cwd", default=None, help="解析身份用的工作目录, 省略=当前目录。")
@click.option("--session-id", default=None, help="直接指定 session_id(跳过 cwd 解析)。")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_agent_whoami(cwd, session_id, as_json):
    """感知自己被派生出的身份(project-role-name + session)。"""
    import os
    cwd = cwd or os.getcwd()
    res = _post_dashboard("/agent/whoami", {"cwd": cwd, "session_id": session_id})
    if res is None:  # 看板不可达 → 本地解析
        from omnicompany.dashboard.boss_sight.services.residents import build_residents
        from omnicompany.dashboard.boss_sight.services.session_resolver import resolve
        residents = build_residents().get("residents", [])
        target = None
        if session_id:
            target = next((r for r in residents if r.get("session_id") == session_id), None)
        if target is None:
            rr = resolve(None, cwd, residents)
            if rr:
                target = next((r for r in residents if r.get("session_id") == rr["session_id"]), None)
        res = ({"resolved": True, "session_id": target.get("session_id"), "identity": target.get("identity"),
                "project": target.get("project"), "role": target.get("role"), "name": target.get("name"),
                "location": target.get("location"), "current_task": target.get("current_task")}
               if target else {"resolved": False, "cwd": cwd})
    if not res.get("resolved"):
        raise click.ClickException(f"未能从 cwd={cwd} 解析出本对话身份(无匹配 claude_code 会话)。可显式 --session-id。")
    if as_json:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        click.echo(f"{res.get('identity') or '(未命名)'}  · session={res.get('session_id')} · @ {res.get('location')}")


@cmd_agent.command("request-review")
@click.option("--headline", required=True, help="一句话工作报告(让用户知道该看什么)。")
@click.option("--cwd", default=None, help="省略=当前目录。")
@click.option("--session-id", default=None)
@click.option("--as", "as_identity", default=None, help="project-role-name 覆盖自动解析的身份。")
@click.option("--kind", default="conversation", type=click.Choice(["conversation", "material", "plan"]))
@click.option("--no-active", is_flag=True, help="不把本对话设为当前跟随上下文。")
@any_caller
def cmd_agent_request_review(headline, cwd, session_id, as_identity, kind, no_active):
    """主动举手: 让用户在审阅总览/多Agent 顶部专注于本对话, 给出指示。"""
    import os
    cwd = cwd or os.getcwd()
    ident = {"identity": "", "project": "", "role": "", "name": ""}
    if as_identity:
        parts = as_identity.split("-")
        if len(parts) >= 3:
            ident.update(identity=as_identity, project=parts[0], role=parts[1], name="-".join(parts[2:]))
    payload = {"headline": headline, "cwd": cwd, "session_id": session_id,
               "kind": kind, "set_active": not no_active, **ident}
    res = _post_dashboard("/agent/request-review", payload)
    if res is None:  # 看板不可达 → 本地落文件(看板起来后仍可见; active_context 跨进程无法设)
        from omnicompany.dashboard.boss_sight.services import agent_attention
        from omnicompany.dashboard.boss_sight.services.residents import build_residents
        from omnicompany.dashboard.boss_sight.services.session_resolver import resolve
        residents = build_residents().get("residents", [])
        sid = session_id
        if not sid:
            rr = resolve(None, cwd, residents)
            sid = rr["session_id"] if rr else None
        if sid and not as_identity:
            r = next((x for x in residents if x.get("session_id") == sid), None)
            if r:
                ident = {k: (ident[k] or r.get(k, "")) for k in ident}
        rec = agent_attention.request_attention(sid or "", headline=headline, kind=kind, **ident)
        click.echo(f"[本地] 已请求审阅本对话: {rec['headline']}  (session={sid or '未解析'}; 看板未运行, "
                   f"起来后顶部可见)")
        return
    sid = res.get("session_id")
    click.echo(f"已请求审阅本对话: {headline}  →  审阅总览/多Agent 顶部高亮 "
               f"(session={sid or '未解析'}, 身份{'已' if res.get('resolved_identity') else '未'}解析)")


# ────────────────────────────────────────────────────────────────────────
# 入口: main.py 调
# ────────────────────────────────────────────────────────────────────────


def register_boss_sight_commands(cli: click.Group, *, cmd_worker: click.Group, cmd_plan: click.Group) -> None:
    """挂所有 BOSS SIGHT cli 子命令到 cli 主入口.

    main.py 调:
        from .commands.boss_sight import register_boss_sight_commands
        register_boss_sight_commands(cli, cmd_worker=cmd_worker, cmd_plan=cmd_plan)
    """
    _register_worker_commands(cmd_worker)
    _register_plan_commands(cmd_plan)
    cli.add_command(cmd_review)
    cli.add_command(cmd_prompt)
    cli.add_command(cmd_propose)
    cli.add_command(cmd_agent)


__all__ = [
    "register_boss_sight_commands",
    "cmd_review", "cmd_prompt", "cmd_propose", "cmd_agent",
]
