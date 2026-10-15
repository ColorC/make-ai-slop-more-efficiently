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
原 Router 类保留在 tools.py 作为内部 handler；现行 Claude Code 控制器只用 Bash 调 cli，
不再存在第二套 Python Worker/TOOL_ROUTERS。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import click

from omnicompany.bus.memory import MemoryBus
from omnicompany.packages.services.boss_sight.reviewstage.links import review_material_open_url
from omnicompany.runtime.agent.agent_loop_tools import ToolContext

from .._access import any_caller, external_or_controller
from omnicompany.packages.services.boss_sight.controller.worker_contract import (
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


def _resolve_material_ref(ref: str) -> str:
    """材料引用解析: 接受内部 id 或**材料全名**(精确匹配)——人按名字找材料,
    不该要求记内部编号(2026-07-19 用户裁决: review 相关命令输出与输入都只报名字)。
    名字精确命中唯一才解析; 零命中/多命中如实报错并给候选。

    2026-08-20 审阅库融合: 读源换共享核心 review_store(画布 card.submission 投影;
    迁移卡保留旧 mat_* id, 两种 id 都按 card.id 命中)。内部治理件过滤沿用
    reviewstage 权威判定(对齐旧 store.list 默认 include_internal=False)。"""
    from omnicompany.packages.services._core import review_store
    from omnicompany.packages.services.boss_sight.reviewstage.store import (
        Material,
        is_internal_review_material,
    )
    if review_store.get_material(ref) is not None:
        return ref
    matches = [
        m for m in (Material.from_dict(row)
                    for row in review_store.list_materials(include_archived=True))
        if m.title == ref and not is_internal_review_material(m)
    ]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise click.ClickException(
            f"找不到材料 {ref!r}: 既不是有效 id 也不是材料全名(精确匹配); "
            f"请用 `omni review list` 按名字查")
    raise click.ClickException(
        f"材料名 {ref!r} 精确命中 {len(matches)} 条, 请改用更完整的名字区分: "
        + " / ".join(m.title for m in matches[:5]))


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

    from omnicompany.packages.services.boss_sight.controller.tools import (
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
    from omnicompany.packages.services.boss_sight.controller.tools import (
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
# 新 omni review group (submit / list / annotate / push / archive / verdict)
# ────────────────────────────────────────────────────────────────────────


@click.group("review")
def cmd_review() -> None:
    """审阅台: submit / list / verdict / annotate / push / archive。

    M4 已换源: 产物主语 = 札记 material 契约(画布卡 + submission 字段, review-queue 聚合);
    旧 reviewstage store 保留兼容读写(_check_mandatory_blockers 依赖), 不再是用户语义上的产物。
    """


@cmd_review.command("capabilities")
@click.option("--json-output", is_flag=True, help="输出完整机器可读能力目录。")
@any_caller
def cmd_review_capabilities(json_output: bool) -> None:
    """按需查看审阅场景、载体、关联、动作和实现状态。"""

    from omnicompany.packages.services.boss_sight.reviewstage.capabilities import (
        capability_catalog,
    )
    from omnicompany.packages.services.boss_sight.reviewstage.routes import get_store

    catalog = capability_catalog(get_store().format_registry)
    if json_output:
        click.echo(json.dumps(catalog, ensure_ascii=False, indent=2))
        return
    validation = catalog["validation"]
    click.echo(
        f"review capability catalog v{catalog['catalog_version']} "
        f"(delivery={catalog['delivery']}, valid={str(validation['ok']).lower()})"
    )
    facility_discovery = catalog["facility_discovery"]
    click.echo(
        "facility-manual="
        f"{facility_discovery['manual_path']} "
        f"load={facility_discovery['load_policy']}"
    )
    for profile in catalog["profiles"]:
        fallback = profile.get("fallback_by_carrier") or profile.get("fallback_profile_id") or "carrier"
        embed = profile.get("embed_renderer_id") or "generic-card"
        click.echo(
            f"- {profile['profile_id']} [{profile['status']}] "
            f"carriers={','.join(profile['accepted_carriers'])} "
            f"embed={embed} fallback={fallback}"
        )
    if validation["errors"]:
        for error in validation["errors"]:
            click.echo(f"ERROR: {error}")


@cmd_review.command("kinds")
@click.option("--json-output", is_flag=True, help="输出机器可读类型目录。")
@any_caller
def cmd_review_kinds(json_output: bool) -> None:
    """列出体系内已注册的材料类型(kind)与语义说明; omni review submit --type 从这里选。"""
    from omnicompany.packages.services.boss_sight.reviewstage.material_types import (
        default_review_format_registry,
        registered_review_kinds,
    )

    registry = default_review_format_registry()
    rows = []
    for kind in sorted(registered_review_kinds(registry)):
        fmt = next(
            (candidate for candidate in registry.all_formats() if f"review.kind.{kind}" in candidate.tags),
            None,
        )
        rows.append({
            "kind": kind,
            "name": fmt.name if fmt else "",
            "description": fmt.description if fmt else "",
            "builtin_only": fmt is None,
            "preconditions": list(fmt.semantic_preconditions) if fmt else [],
        })
    if json_output:
        click.echo(json.dumps({"kinds": rows}, ensure_ascii=False, indent=2))
        return
    click.echo(f"已注册材料类型 {len(rows)} 种(omni review submit --type 从这里选):")
    for row in rows:
        label = row["name"] or "(内置 kind, 无 Format 语义)"
        click.echo(f"- {row['kind']}  {label}")
        if row["description"]:
            click.echo(f"    {row['description']}")
        for precondition in row["preconditions"]:
            click.echo(f"    · {precondition}")


@cmd_review.command("guide")
@click.option(
    "--event",
    type=click.Choice(["submission_preflight", "embed_preflight", "material_open"]),
    default="submission_preflight",
    show_default=True,
)
@click.option("--kind", default="")
@click.option("--tier", default="")
@click.option("--title", default="")
@click.option("--plan-id", "source_plan_id", default="")
@click.option("--file", "file_path", default="")
@click.option("--content", "inline_content", default="")
@click.option("--schema-id", "data_schema_id", default="")
@click.option("--extra-json", default=None)
@click.option("--review-context-json", default=None)
@click.option(
    "--review-context-file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="从 UTF-8 JSON 文件读取 review_context；Windows 下推荐。",
)
@click.option("--project", default="")
@click.option("--track", default="")
@click.option("--version", type=int, default=None)
@click.option("--version-family", default="")
@click.option("--subject-id", default="")
@click.option("--subject-type", default="")
@click.option("--revision", type=int, default=None)
@click.option("--json-output", is_flag=True)
@any_caller
def cmd_review_guide(
    event,
    kind,
    tier,
    title,
    source_plan_id,
    file_path,
    inline_content,
    data_schema_id,
    extra_json,
    review_context_json,
    review_context_file,
    project,
    track,
    version,
    version_family,
    subject_id,
    subject_type,
    revision,
    json_output,
) -> None:
    """在提交、嵌入或打开时按需解析一次；不安装 hook。"""

    from omnicompany.packages.services.boss_sight.reviewstage.capabilities import (
        ReviewSubmissionIntent,
        resolve_review_submission,
    )
    from omnicompany.packages.services.boss_sight.reviewstage.routes import get_store

    def parse_object(raw: str | None, label: str) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"{label} must be a JSON object: {exc}") from exc
        if not isinstance(value, dict):
            raise click.UsageError(f"{label} must be a JSON object")
        return value

    if review_context_json and review_context_file:
        raise click.UsageError("use only one of --review-context-json or --review-context-file")
    if review_context_file:
        review_context_json = Path(review_context_file).read_text(encoding="utf-8")
    payload = {
        "kind": kind,
        "tier": tier,
        "title": title,
        "source_plan_id": source_plan_id,
        "file_path": file_path,
        "inline_content": inline_content,
        "data_schema_id": data_schema_id,
        "extra": parse_object(extra_json, "--extra-json"),
        "review_context": (
            parse_object(review_context_json, "--review-context-json/--review-context-file")
            if review_context_json
            else None
        ),
        "project": project,
        "track": track,
        "version": version,
        "version_family": version_family,
        "subject_id": subject_id,
        "subject_type": subject_type,
        "revision": revision,
    }
    result = resolve_review_submission(
        ReviewSubmissionIntent.from_mapping(payload),
        event=event,
        registry=get_store().format_registry,
    ).to_dict()
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    context = result["review_context"]
    trace = context["resolution"]
    capability = result["capability"]
    fallback = (
        capability.get("fallback_by_carrier", {}).get(kind)
        or capability.get("fallback_profile_id")
        or "generic-card"
    )
    click.echo(
        f"profile={context['profile_id']} blocked={str(result['blocked']).lower()} "
        f"routing={trace['routing_level']} selected_by={trace['selected_by']} "
        f"confidence={trace['confidence']} "
        f"renderer={capability.get('renderer_id') or 'carrier'} "
        f"embed={capability.get('embed_renderer_id') or 'generic-card'} "
        f"fallback={fallback}"
    )
    facility_discovery = result["facility_discovery"]
    if facility_discovery["consult_manual"]:
        search_terms = ",".join(facility_discovery["search_terms"]) or context["profile_id"]
        click.echo(
            f"MANUAL: {facility_discovery['manual_path']} "
            f"search={search_terms} reason={facility_discovery['reason']}"
        )
    reminders = context["reminders"]
    if not reminders:
        click.echo("ready: no reminder for this event")
        return
    for reminder in reminders:
        click.echo(
            f"{reminder['severity'].upper()}: {reminder['message']} "
            f"[{reminder['field_path']}]"
        )


# ── M4 审阅台换源: material 契约(画布卡 + submission)是新产物; 旧 store 保留兼容 ──
# 材质类 kind(直通画布卡); 旧 reviewstage Format kind 照旧走旧 store 校验。
_MATERIAL_KINDS = ("text", "file", "image", "video", "audio", "web")
_REVIEW_INBOX_CANVAS = "review-inbox"        # 解析不出会话时的默认收件画布(固定 id, 幂等)
_REVIEW_INBOX_TITLE = "审阅收件箱"
_REVIEW_GROUP_HINT = "提审"                   # 画布里有这个名字的组卡就插进去


def _material_card_source(kind: str, file_path: str | None, inline_content: str | None,
                          card_id: str) -> tuple[str, str, dict]:
    """kind → (card_kind, adapter, source)。text 卡正文走 notes_text_put 分居链;
    file/image/video/audio 走签名文件 token; web 走 link url(取 --content)。"""
    import mimetypes

    from .canvas import _invoke as _canvas_invoke

    if kind not in _MATERIAL_KINDS and inline_content is not None:
        kind = "text"  # 旧 kind(markdown 等) + --content → 文字卡(沿用 M1b 行为)
    if kind == "text":
        body = inline_content
        if body is None and file_path:
            body = Path(file_path).read_text(encoding="utf-8")
        if not body:
            raise click.ClickException("--kind text 需要正文: --content 或 --path/--file 指向文本文件")
        material = _canvas_invoke("notes_text_put", {"id": card_id, "content": body})
        return "text", "text", {"kind": "file", "path": material["path"],
                                "token": material["open_token"], "mime": material.get("mime"),
                                "editableId": card_id}
    if kind == "web":
        url = (inline_content or "").strip()
        # 根相对 URL(/path) 也放行: 同源应用页(如 /bilibili-video/<ep>/ 结构化网页影片),
        # 任意入口(8210 直连/12443 网关)都以当前 origin+会话解析, 比绝对 URL 更稳。
        if not url.startswith(("http://", "https://", "/")):
            raise click.ClickException("--kind web 需要 URL: 用 --content 传 http(s) 或根相对(/)链接")
        return "material", "web", {"kind": "link", "url": url}
    # file/image/video/audio + 旧 kind 带文件
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.dashboard.controlplane.overlay_notes import _sign_file_path

    src = Path(file_path or "")
    if not src.is_absolute():
        src = (omni_workspace_root() / (file_path or "")).resolve()
    if not src.is_file():
        raise click.ClickException(f"画布卡找不到文件: {file_path}")
    mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    adapter = ("image" if mime.startswith("image/") else "video" if mime.startswith("video/")
               else "audio" if mime.startswith("audio/") else "pdf" if mime == "application/pdf"
               else "web" if mime == "text/html"
               # text/html 不映射 web 时, 审阅台预览只显示 mime 签("text/html")而不是网页
               # (2026-08-18 EP2 送审实测); html 材料的正确预览形态就是 iframe 网页。
               else "auto")
    return "material", adapter, {"kind": "file", "path": str(src),
                                 "token": _sign_file_path(str(src)), "mime": mime}


def _place_card(document: dict, card: dict, group_id: str | None,
                upserts: list[dict]) -> None:
    """选位: --group 指定组 / 「提审」组(存在则) → 组内末尾(相对坐标); 否则平铺追加到
    现有卡 max y 下方。组高不够时顺带撑高组卡(一并 upsert)。"""
    cards = [c for c in document.get("cards") or [] if isinstance(c, dict)]
    group = None
    if group_id:
        group = next((c for c in cards if c.get("id") == group_id), None)
        if not group or group.get("kind") != "group":
            raise click.ClickException(f"--group 不是本画布的组卡: {group_id}")
    if group is None and not group_id:
        group = next((c for c in cards
                      if c.get("kind") == "group" and _REVIEW_GROUP_HINT in str(c.get("title") or "")), None)
    if group is not None:
        # 子卡坐标相对组卡(store/前端约定)
        children = [c for c in cards if c.get("parentId") == group.get("id")]
        bottom = max((float(c.get("y") or 0) + float(c.get("height") or 220) for c in children), default=34.0)
        card["parentId"] = group["id"]
        card["x"] = 28.0
        card["y"] = bottom + 20.0
        need = card["y"] + float(card.get("height") or 220) + 28.0
        if float(group.get("height") or 0) < need:
            grown = dict(group)
            grown["height"] = need
            upserts.append(grown)
        return
    max_bottom = max((float(c.get("y") or 0) + float(c.get("height") or 320) for c in cards), default=0.0)
    card["x"] = 60.0
    card["y"] = max_bottom + 40.0 if max_bottom else 72.0


def _new_review_card_id() -> str:
    """提审卡 id 生成器。card_id 由 cmd_review_submit 外层生成(同时作 wiki 幂等键
    后缀与 sources 引用), 测试可 monkeypatch 固定。"""
    import time
    import uuid as _uuid

    return f"review-{int(time.time() * 1000):x}-{_uuid.uuid4().hex[:8]}"


def _material_wiki_runtime():
    """项目 material-wiki 运行时。测试 monkeypatch 此函数隔离 manifest 与 canonical 根。"""
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.packages.services._core.material_wiki.runtime import MaterialWikiRuntime

    return MaterialWikiRuntime(omnicompany_root=omni_workspace_root())


def _wiki_depth2_listing(profile) -> str:
    """从 profile taxonomy 动态读深度 2 类目清单(不写死), 供缺 --wiki-placement 的报错用。"""
    rows = []
    for root in profile.taxonomy:
        for child in root.children:
            label = f"（{child.label}）" if child.label else ""
            rows.append(f"  - {root.id}/{child.id}{label}")
    return "\n".join(rows) or "  (profile 未定义深度 2 类目)"


_WIKI_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}


def _review_wiki_body(*, kind: str, title: str, file_path: str | None,
                      inline_content: str | None, canvas_card_ref: str) -> str:
    """wiki 本体正文: 文本类直接用提交正文(--content 或文本文件); image/video/audio/web
    等二进制/外链类生成引用型 stub(标题 + 来源指针 + 卡 ref)。

    web 类额外在首行写机读标记 `<!-- omni-live-material {...} -->`: 概览/wiki 阅读器
    据此把条目渲染成整页 live 网页(与审阅台同一本体), 而不是一篇指针文档。"""
    if kind == "web":
        url = (inline_content or "").strip()
        marker = json.dumps({"url": url, "card": canvas_card_ref}, ensure_ascii=False)
        return (
            f"<!-- omni-live-material {marker} -->\n\n"
            f"# {title}\n\n"
            "> live 网页材料: 阅读器整页直显本体; 以下为引用指针(源 URL 与画布审阅卡)。\n\n"
            f"- 来源: {url}\n"
            f"- 审阅卡: {canvas_card_ref}\n"
        )
    if kind != "web" and inline_content and inline_content.strip():
        return inline_content
    if file_path and (kind == "text" or Path(file_path).suffix.lower() in _WIKI_TEXT_SUFFIXES):
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pass  # 读不出文本就退到引用型 stub
    pointer = file_path or (inline_content or "").strip() or "(无)"
    return (
        f"# {title}\n\n"
        "> 二进制/外链类材料: wiki 只存引用, 本体见来源指针与画布审阅卡。\n\n"
        f"- 来源: {pointer}\n"
        f"- 审阅卡: {canvas_card_ref}\n"
    )


def _create_review_wiki_entry(service, profile, *, title: str, placement: tuple[str, ...],
                              body: str, canvas_card_ref: str, file_path: str | None,
                              card_id: str, dry_run: bool = False):
    """review submit 的 wiki 本体落点: status=profile.default_status, sources 含画布卡
    与文件来源, 幂等键 review-submit:<card_id>(同卡重放不双写)。"""
    from omnicompany.packages.services._core.material_wiki.adapters.markdown import (
        MaterialWikiStorageError,
    )
    from omnicompany.packages.services._core.material_wiki.schema import (
        MaterialSourceRef,
        MaterialWikiContractError,
    )

    sources = [MaterialSourceRef("canvas-card", canvas_card_ref)]
    if file_path:
        sources.append(MaterialSourceRef("file", str(file_path)))
    try:
        return service.create(
            title=title,
            placement_path=placement,
            body=body,
            status=profile.default_status,
            sources=sources,
            idempotency_key=f"review-submit:{card_id}",
            dry_run=dry_run,
        )
    except (MaterialWikiContractError, MaterialWikiStorageError, ValueError) as exc:
        raise click.ClickException(f"wiki 本体写入失败: {exc}") from exc


def _submit_material_card(*, kind: str, material_kind: str | None = None,
                          tier: str, title: str, source_plan_id: str,
                          file_path: str | None, inline_content: str | None,
                          project: str | None, track: str | None, version: int | None,
                          session: str | None, group: str | None,
                          card_id: str, extra: dict | None = None) -> dict:
    """M4 换源: 提交 = 画布材料卡(submission.state=unreviewed) + materials/ 正文。

    落点优先级: --session 画布 → 当前会话画布 → 审阅收件箱画布(幂等新建)。
    走 notes_canvas_mutate(实体级并发安全; 409 冲突重取 revision 重试一次)。
    card_id 由外层生成(供 wiki 幂等键与 sources 引用, 防半截状态)。
    返回 {card_id, canvas, session_id, title, revision, old_material_id}。
    """
    import time

    from omnicompany.packages.services._core.identity import current_session_meta
    from .canvas import _canvas_document, _canvas_key, _mutate, _session_from_canvas_key

    pty_id = (session or "").strip() or (current_session_meta() or {}).get("pty_id") or ""
    key = _canvas_key(pty_id) if pty_id else _REVIEW_INBOX_CANVAS
    actor = f"cli:review-submit:{pty_id or 'inbox'}"

    eff_file = file_path
    card_kind, adapter, source = _material_card_source(kind, eff_file, inline_content, card_id)

    last_error: Exception | None = None
    for attempt in (1, 2):  # 409 冲突重试一次(每次重取最新 revision 与选位)
        document = _canvas_document(key)
        card = {
            "id": card_id,
            "kind": card_kind,
            "title": title,
            "source": source,
            "adapter": adapter,
            "width": 560.0, "height": 420.0,
            "material": {
                "project": project, "track": track, "version": version,
                "plan_id": source_plan_id, "tier": tier, "kind": material_kind or kind,
                **({"extra": extra} if extra else {}),
            },
            "submission": {
                "state": "unreviewed",
                "submittedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                "submittedBy": actor,
            },
        }
        upserts: list[dict] = []
        _place_card(document, card, group, upserts)
        upserts.append(card)
        patch: dict = {"upsert_cards": upserts}
        if key == _REVIEW_INBOX_CANVAS and not document.get("title"):
            patch["set_title"] = True
            patch["title"] = _REVIEW_INBOX_TITLE
        try:
            result = _mutate(key, document, patch, actor=actor)
        except click.ClickException as exc:
            last_error = exc
            if "冲突" in str(exc) and attempt == 1:
                continue
            raise
        return {
            "card_id": card_id,
            "canvas": key,
            "session_id": _session_from_canvas_key(key) if key.startswith("session-") else "",
            "title": title,
            "revision": result.get("revision"),
        }
    raise click.ClickException(f"画布提审卡写入冲突未合并: {last_error}")


@cmd_review.command("submit")
@click.option("--kind", required=True, type=str,
              help="M4 换源后两类取值: ①材质类(画布卡直通) text|file|image|video|audio|web "
                   "(web 用 --content 传 URL; file/image 等用 --path 给本体); "
                   "②旧 reviewstage Format 值(markdown/static-report/... 以 `omni review capabilities` 为准), "
                   "照旧走旧店强制标签校验 + 画布卡双写。")
@click.option("--tier", required=True, type=str,
              help="审阅级别；常用 mandatory/important/processual/ignored，最终由 FormatRegistry 校验。")
@click.option("--title", required=True)
@click.option("--plan-id", "source_plan_id", default=None,
              help="所属 plan(可选; 用于按 plan 聚合与必验收阻断查)。")
@click.option("--subagent-id", "source_subagent_id", default=None)
@click.option("--file", "file_path", default=None)
@click.option("--content", "inline_content", default=None)
@click.option("--annotations-allowed/--no-annotations", default=True)
@click.option("--file-ext", default=None)
@click.option("--schema-id", "data_schema_id", default=None)
@click.option("--review-context-json", "review_context_json", default=None,
              help="一等审阅场景 JSON: profile_id/schema_id/references。")
@click.option("--review-context-file", "review_context_file",
              type=click.Path(exists=True, dir_okay=False), default=None,
              help="从 UTF-8 JSON 文件读取 review_context；Windows 下推荐。")
@click.option("--extra-json", "extra_json", default=None,
              help="JSON 对象 merge 进 material.extra (webgame-spec 三件套引用 / 兄弟材料 attached_to)。"
                   "禁止塞 project/track/version/version_family/subject_id/subject_type/revision — 用正式选项。")
@click.option("--type", "material_kind", default=None,
              help="材料类型(审阅 kind)。必须在体系内已注册: `omni review kinds` 看类型目录与语义, "
                   "新建类型=注册带 review.kind.* tag 的 Format。缺省时: --kind 为旧 Format 值则取 --kind, "
                   "--kind 为材质类(text|file|image|video|audio|web)则必填(由会话自动分类给出)。")
@click.option("--project", default=None,
              help="材料所属项目。缺省=当前会话绑定项目(omni session bind --project 或 plan frontmatter); "
                   "仍未解析出则必填。")
@click.option("--track", default=None,
              help="必填。阶段/轨道名, 如 信息审阅稿/交互审阅稿/工作报告。")
@click.option("--version", "version", type=int, default=None,
              help="必填。版本号(同 --version-family 内递增, 从 1 起)。")
@click.option("--version-family", "version_family", default=None,
              help="版本族(同一份稿的多版本共 family)。省略则默认=--title。")
@click.option("--subject-id", "subject_id", default=None,
              help="内容主体唯一 ID，如 EP0。B 站在册生产阶段必填。")
@click.option("--subject-type", "subject_type", default=None,
              help="内容主体类型，如 episode。B 站在册生产阶段必填。")
@click.option("--revision", "revision", type=int, default=None,
              help="跨阶段共享的整期修改版本，如 EP0 的 v13 填 13；不同于材料族自身 --version。")
@click.option("--links-json", "links_json", default=None,
              help='JSON 对象, 如 {"parent":"mat_xxx","supersedes":["mat_yyy"],"related":["mat_zzz"]}。')
@click.option("--links-json-file", "links_json_file", type=click.Path(exists=True, dir_okay=False), default=None,
              help="从 UTF-8 JSON 文件读取 links；Windows 下推荐，避免 shell 剥离双引号。")
@click.option("--attachment-of", default=None,
              help="图片附件所属的报告 material id；image/aigc-image 必填，禁止顶层单图送审。")
@click.option("--session", "session_id", default=None,
              help="目标会话画布(pty 会话 id)。缺省=当前会话; 解析不出 → 审阅收件箱画布。")
@click.option("--path", "material_path", default=None,
              help="file/image/video/audio 类材料本体文件(生成签名 source token); 等同 --file 的画布侧。")
@click.option("--group", "group_id", default=None,
              help="插进指定组卡(id)。缺省: 画布有「提审」组则进组, 否则平铺追加到末尾。")
@click.option("--wiki-placement", "wiki_placement", default=None,
              help="wiki 分类路径(/ 分隔, 如 bilibili_publish/pattern/p09_icom_box)。"
                   "项目已登记 material-wiki profile 时必填(本体入 wiki, 画布卡是同一材料的"
                   "裁决视图); 深度 2 类目清单见报错提示或项目 material-profile。")
@click.option("--no-wiki", "no_wiki", is_flag=True,
              help="显式豁免 wiki 归位(纯报告/截图等非本体材料); 必须同时给 --wiki-exempt-reason。")
@click.option("--wiki-exempt-reason", "wiki_exempt_reason", default=None,
              help="--no-wiki 的豁免理由, 记入卡 material.extra.wiki_exempt 供审计。")
@click.option("--json", "as_json", is_flag=True,
              help="JSON 输出: 新卡 id / 落点画布 key / 标题 / 旧店材料 id。")
@click.option("--dry-run", is_flag=True,
              help="只解析 profile、缺失上下文和提醒；不复制文件、不写 Material、不做远程探测。")
# M2 Phase 2 步骤 4: subagent 在收尾时通过 omni review submit emit material 是核心协议
# (emit_material_protocol = A_explicit_tool, 见 plan.md). 把 submit 放开给三档 caller.
# annotate / push 仍受限 — 那是总控/外部的活, subagent 不应该自评和自推.
@any_caller
def cmd_review_submit(kind, tier, title, source_plan_id, source_subagent_id, file_path,
                      inline_content, annotations_allowed, file_ext, data_schema_id,
                      review_context_json, review_context_file, extra_json, material_kind,
                      project, track, version, version_family, subject_id, subject_type, revision,
                      links_json, links_json_file, attachment_of, session_id,
                      material_path, group_id, wiki_placement, no_wiki, wiki_exempt_reason,
                      as_json, dry_run):
    """提交材料到审阅台（M4 起唯一产物 = 札记 material 契约：画布卡 + submission）。

    在画布建带 submission{state=unreviewed} 的材料卡(text 类正文落 materials/ 文件)。
    落点: --session 画布 → 当前会话画布 → 「审阅收件箱」画布(幂等)。
    --kind 两类取值: ①材质类(直通) text|file|image|video|audio|web(web 用 --content 传 URL;
    file/image 等用 --path/--file 给本体); ②旧 Format 值(markdown 等)照旧接受, 映射成画布卡。
    材料类型(--type / 旧 kind 值)必须在体系内已注册(review.kind.* Format 或内置 kind),
    否则拒绝提交; 新建类型 = 注册带 review.kind.<name> tag 的 Format。
    project 缺省 = 当前会话绑定项目。提交是会话行为, 侧栏概览页只置顶展示。
    wiki 归位(2026-08-21 用户裁定): 项目已登记 material-wiki profile 时, 送审必须带
    --wiki-placement(先写 wiki 本体再写画布卡, 卡 material.extra.wiki 双向互链), 或
    --no-wiki + --wiki-exempt-reason 显式豁免; 未登记项目维持现状(stderr 提醒不阻断)。
    """
    # M4: 旧店关系/上下文参数仍接受(兼容旧调用方如 article_sanitizer), 只做形态校验;
    # extra 记进卡 material.extra, 其余旧店字段随机制消亡不再生效。
    if review_context_json and review_context_file:
        raise click.UsageError("use only one of --review-context-json or --review-context-file")
    if review_context_file:
        review_context_json = Path(review_context_file).read_text(encoding="utf-8")
    for label, raw in (("review context", review_context_json),
                       ("extra_json", extra_json), ("links_json", None)):
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise click.UsageError(f"{label} must be a JSON object: {exc}") from exc
            if not isinstance(parsed, dict):
                raise click.UsageError(f"{label} must be a JSON object")
    if links_json and links_json_file:
        raise click.UsageError("use only one of --links-json or --links-json-file")
    if links_json_file:
        links_json = Path(links_json_file).read_text(encoding="utf-8")
    try:
        parsed_links = json.loads(links_json) if links_json else {}
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"links_json must be a JSON object: {exc}") from exc
    if not isinstance(parsed_links, dict):
        raise click.UsageError("links_json must be a JSON object")
    if attachment_of:
        declared_parent = parsed_links.get("parent")
        if declared_parent not in (None, attachment_of):
            raise click.UsageError("--attachment-of conflicts with links.parent")
        parsed_links["parent"] = attachment_of
    try:
        card_extra = json.loads(extra_json) if extra_json else {}
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"extra_json must be a JSON object: {exc}") from exc
    if parsed_links:
        card_extra["links"] = parsed_links
    # ── 项目与类型体系内校验(2026-08-14 用户裁定) ────────────────────────
    # 项目: 缺省 = 当前会话绑定项目(omni session bind / plan frontmatter)。
    if not (project or "").strip():
        from omnicompany.packages.services._core.identity import current_session_meta

        project = (current_session_meta() or {}).get("project") or None
    if not (project or "").strip():
        raise click.UsageError(
            "project 必填: 给 --project <project>, 或先绑定当前会话项目"
            "(omni session bind --project <id> / plan frontmatter project)。"
        )
    # 类型: 必须已在体系内注册(review.kind.* Format 或内置 kind); 新建类型 = 注册 Format。
    from omnicompany.packages.services.boss_sight.reviewstage.material_types import (
        default_review_format_registry,
        normalize_review_kind,
        normalize_review_tier,
        review_kind_format_preconditions,
    )

    registry = default_review_format_registry()
    effective_kind = material_kind or kind
    if material_kind is None and kind in _MATERIAL_KINDS:
        raise click.UsageError(
            f"--kind {kind} 是材质类(载体), 不含材料类型语义; 必须用 --type 指定"
            "体系内已注册的材料类型(如 agent-workflow-report)。"
            "查看现有类型: omni review capabilities; 新建类型 = 注册带 review.kind.* tag 的 Format。"
        )
    try:
        effective_kind = normalize_review_kind(effective_kind, registry)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        tier = normalize_review_tier(tier, registry)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    for precondition in review_kind_format_preconditions(effective_kind, registry):
        click.echo(f"[{effective_kind}] 要求: {precondition}", err=True)
    # M4: --path 并入 file_path(画布侧材料本体); 提前到 wiki 正文解析之前
    if material_path:
        if file_path and Path(material_path) != Path(file_path):
            raise click.UsageError("--path 与 --file 指向不同文件; 画布材料本体只认一个")
        file_path = material_path
    # 本体预检(防"wiki 已写、画布卡因缺本体失败"的半截状态): 除 web 外都要正文或文件
    if kind == "web":
        if not (inline_content or "").strip():
            raise click.UsageError("--kind web 需要 URL: 用 --content 传 http(s) 或根相对(/)链接")
    elif not inline_content and not file_path:
        raise click.UsageError(f"--kind {kind} 需要正文: --content 或 --path/--file 指向材料本体")
    # ── wiki 归位强制(2026-08-21 用户裁定, 方案 A: CLI 唯一收口处强制) ──────
    # 已登记 material-wiki profile 的项目: 缺 --wiki-placement 且未 --no-wiki 豁免 → 拒绝;
    # 未登记项目维持现状(stderr 提醒, 不阻断)。
    if wiki_placement and no_wiki:
        raise click.UsageError("--wiki-placement 与 --no-wiki 互斥")
    if no_wiki and not (wiki_exempt_reason or "").strip():
        raise click.UsageError(
            "--no-wiki 必须同时给 --wiki-exempt-reason(理由记入卡 material.extra.wiki_exempt 供审计)")
    if (wiki_exempt_reason or "").strip() and not no_wiki:
        raise click.UsageError("--wiki-exempt-reason 只与 --no-wiki 同用")
    from omnicompany.packages.services._core.identity import current_session_meta
    from omnicompany.packages.services._core.material_wiki.schema import MaterialWikiContractError
    from .canvas import _canvas_key

    pty_id = (session_id or "").strip() or (current_session_meta() or {}).get("pty_id") or ""
    canvas_key = _canvas_key(pty_id) if pty_id else _REVIEW_INBOX_CANVAS
    # 防半截状态的提交顺序: 类型/tier 校验(上方) → card_id → wiki 本体 → 画布卡
    card_id = _new_review_card_id()
    canvas_card_ref = f"{canvas_key}#{card_id}"
    wiki_runtime = _material_wiki_runtime()
    try:
        wiki_registration = wiki_runtime.registration(project)
    except MaterialWikiContractError:
        wiki_registration = None
    wiki_service = None
    wiki_profile = None
    placement: tuple[str, ...] = ()
    if wiki_registration is None:
        if wiki_placement:
            raise click.UsageError(
                f"项目 {project} 未登记 material-wiki profile, --wiki-placement 无处可落")
        if not no_wiki and not dry_run:
            # dry-run 的输出契约是纯 JSON 预览, 提醒改放 preview 字段, 不污染 stdout/stderr
            click.echo(
                f"提醒: 项目 {project} 未登记 material-wiki profile, 本次送审不落 wiki 本体",
                err=True)
    else:
        wiki_profile = wiki_runtime.profile(project)
        if not wiki_placement and not no_wiki:
            raise click.UsageError(
                f"项目 {project} 已登记 material-wiki profile ({wiki_profile.profile_id}): "
                "送审必须用 --wiki-placement 指定 wiki 分类(本体入 wiki, 画布卡是同一材料的裁决视图), "
                "或 --no-wiki --wiki-exempt-reason 显式豁免(纯报告/截图等非本体材料)。\n"
                f"{project} 深度 2 类目:\n{_wiki_depth2_listing(wiki_profile)}")
        if wiki_placement:
            placement = tuple(part.strip() for part in wiki_placement.split("/") if part.strip())
            if not placement:
                raise click.UsageError("--wiki-placement 不能为空")
            wiki_service = wiki_runtime.service(project)
    if dry_run:
        # M4: dry-run 预览画布卡 + wiki 本体(只预览不写; 旧 FormatRegistry 解析随机制消亡移除)
        preview = {"kind": kind, "material_kind": effective_kind, "tier": tier,
                   "title": title, "plan_id": source_plan_id, "project": project,
                   "session": session_id or "(当前会话/收件箱)", "group": group_id,
                   "file": file_path, "has_content": bool(inline_content),
                   "wiki_placement": wiki_placement, "no_wiki": no_wiki,
                   "wiki_registered": wiki_registration is not None}
        if wiki_service is not None:
            body = _review_wiki_body(kind=kind, title=title, file_path=file_path,
                                     inline_content=inline_content,
                                     canvas_card_ref=canvas_card_ref)
            receipt = _create_review_wiki_entry(
                wiki_service, wiki_profile, title=title, placement=placement, body=body,
                canvas_card_ref=canvas_card_ref, file_path=file_path,
                card_id=card_id, dry_run=True)
            preview["wiki_material_id"] = receipt.material_id
            preview["canonical_ref"] = receipt.canonical_ref
        click.echo(json.dumps(preview, ensure_ascii=False, indent=2))
        return
    # wiki 本体先行(幂等键 review-submit:<card_id>, 画布失败可凭收据重放/人工清理)
    wiki_receipt = None
    if wiki_service is not None:
        body = _review_wiki_body(kind=kind, title=title, file_path=file_path,
                                 inline_content=inline_content,
                                 canvas_card_ref=canvas_card_ref)
        wiki_receipt = _create_review_wiki_entry(
            wiki_service, wiki_profile, title=title, placement=placement, body=body,
            canvas_card_ref=canvas_card_ref, file_path=file_path,
            card_id=card_id, dry_run=False)
        card_extra["wiki"] = {"material_id": wiki_receipt.material_id,
                              "canonical_ref": wiki_receipt.canonical_ref,
                              "placement": "/".join(placement)}
    if no_wiki:
        card_extra["wiki_exempt"] = wiki_exempt_reason.strip()
    # M4: 唯一产物 = 画布提审卡(submission.state=unreviewed) + materials/ 正文; 旧店写已摘除
    placed = _submit_material_card(
        kind=kind, material_kind=effective_kind, tier=tier, title=title,
        source_plan_id=source_plan_id or "",
        file_path=file_path, inline_content=inline_content,
        project=project, track=track, version=version,
        session=session_id, group=group_id, card_id=card_id, extra=card_extra,
    )
    wiki_material_id = wiki_receipt.material_id if wiki_receipt else None
    canonical_ref = wiki_receipt.canonical_ref if wiki_receipt else None
    if as_json:
        click.echo(json.dumps({
            "ok": True, "card_id": placed["card_id"], "canvas": placed["canvas"],
            "session_id": placed["session_id"], "title": placed["title"],
            "revision": placed["revision"], "old_material_id": None,
            "wiki_material_id": wiki_material_id, "canonical_ref": canonical_ref,
        }, ensure_ascii=False, indent=2))
        return
    click.echo(f"审阅卡已提交: {placed['card_id']} → 画布 {placed['canvas']}"
               f" (rev {placed['revision']})")
    if wiki_receipt:
        replay = "（幂等重放）" if wiki_receipt.idempotent_replay else ""
        click.echo(f"wiki 本体{replay}: {wiki_material_id} → {canonical_ref}")
    elif no_wiki:
        click.echo(f"wiki 豁免: {wiki_exempt_reason.strip()}")


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
    from omnicompany.packages.services.boss_sight.reviewstage.filetree_diff import build_filetree_diff
    from omnicompany.packages.services.boss_sight.controller.tools import SubmitToReviewstageRouter
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


def _fetch_review_queue() -> list[dict]:
    """GET dashboard /lofa/overlay/review-queue（M4 起 omni review list 的默认读源）。"""
    import urllib.request

    url = f"http://127.0.0.1:{int(os.environ.get('OMNI_DASHBOARD_PORT', '8210'))}/lofa/overlay/review-queue"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"review-queue 不可达(dashboard 8210): {exc}") from None
    return list(payload.get("items") or [])


@cmd_review.command("list")
@click.option("--state", type=click.Choice(["unreviewed", "seen", "fully-passed", "not-fully-passed"]),
              default=None, help="按画布 submission 状态过滤")
@click.option("--session", "session_id", default=None, help="只看某会话画布的提审卡(pty 会话 id)")
@click.option("--max", "max_results", type=int, default=30, show_default=True)
@click.option("--include-archived", is_flag=True, help="含已归档卡(默认隐去)")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_review_list(state, session_id, max_results, include_archived, as_json):
    """列审阅台材料(M4 起唯一读源 = 札记画布 review-queue, submission 卡聚合)。

    旧 reviewstage store 已随机制消亡摘除; 考古旧数据用 `omni review capabilities`/只读迁移画布。
    """
    items = _fetch_review_queue()
    if not include_archived:
        items = [it for it in items if not (it.get("submission") or {}).get("archived")]
    if state:
        items = [it for it in items if (it.get("submission") or {}).get("state") == state]
    if session_id:
        items = [it for it in items if it.get("session_id") == session_id]
    items = items[:max_results]
    if as_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("(审阅队列空)")
        return
    for it in items:
        sub = it.get("submission") or {}
        reason = f" · {sub.get('reason')}" if sub.get("reason") else ""
        click.echo(f"[{sub.get('state', '?'):16s}] {it.get('title') or it.get('card')}"
                   f" · {it.get('session_id')}#{it.get('card')}"
                   f" · {it.get('canvasTitle') or it.get('canvas')}{reason}")


@cmd_review.command("verdict")
@click.argument("ref")
@click.option("--pass", "passed", is_flag=True, help="完全通过(fully-passed)")
@click.option("--fail", "failed", is_flag=True, help="未完全通过(not-fully-passed, 需 --reason 受控词表值)")
@click.option("--reason", default=None, help="受控词表: 模态/形态错误 / 内容错误/需迭代 / 方向错误 / 不需要审阅")
@click.option("--note", default=None, help="自由补充(可空; 追加在理由后)")
@click.option("--by", "actor", default="user:cli", help="裁决人标识(verdictBy)")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_review_verdict(ref, passed, failed, reason, note, actor, as_json):
    """给提审卡下裁决(M4 新读写的产物面)。ref = <会话id>#<卡id> / <画布key>#<卡id> / 裸卡id(队列里唯一时)。

    写回卡所属画布的 submission(fully-passed / not-fully-passed + 受控理由词表, 词表外不写);
    卡上带旧店材料 id 时同步写一份旧 reviewstage verdict(accepted/rejected)保持兼容。
    """
    from .canvas import (
        SUBMISSION_FAIL_REASONS,
        _canvas_key,
        _session_from_canvas_key,
        _verdict_card,
    )

    if passed == failed:
        raise click.ClickException("必须且只能给一个: --pass 或 --fail")
    if failed and reason and reason not in SUBMISSION_FAIL_REASONS:
        raise click.ClickException(f"reason 不在受控词表: {reason}(可选: {' / '.join(SUBMISSION_FAIL_REASONS)})")

    # 解析 ref → (canvas_key, card_id)
    if "#" in ref:
        left, _, card_id = ref.partition("#")
        key = left if left.startswith("session-") or left == _REVIEW_INBOX_CANVAS else _canvas_key(left)
    else:
        card_id = ref
        hits = [it for it in _fetch_review_queue() if it.get("card") == card_id]
        if len(hits) != 1:
            raise click.ClickException(
                f"裸卡 id 在审阅队列里{'找不到' if not hits else '不唯一'}: {card_id}"
                f"——用 <会话id>#<卡id> 形态")
        key = hits[0]["canvas"]

    out = _verdict_card(key, card_id, passed, reason, note, actor)

    if as_json:
        click.echo(json.dumps({"ok": True, **out}, ensure_ascii=False, indent=2))
        return
    click.echo(f"✓ 裁决 {out['card']} → {out['state']}"
               f"{' · ' + out['reason'] if out.get('reason') else ''} (画布 {out['canvas']})")


class _ReviewStoreReadAdapter:
    """把共享核心 review_store 包成 build_review_readback 期望的只读 store 接口。

    2026-08-20 审阅库融合: readback 是数据面读取, 换源到画布 card.submission 投影
    (Material.from_dict 无损还原成旧 Material 对象, readback 逻辑零改动)。正文/资产
    文件仍在旧库数据目录, resolve_file_path 委派旧 MaterialStore(仅文件资产解析, 只读)。
    """

    def __init__(self) -> None:
        from omnicompany.packages.services.boss_sight.reviewstage.routes import get_store

        self._file_assets = get_store()  # 仅 resolve_file_path: 旧库 files/ 资产定位

    def reload(self) -> None:
        """review_store 每次直读画布, 没有进程内缓存要刷。"""

    def list(self, *, include_archived: bool = False, include_internal: bool = False):
        from omnicompany.packages.services._core import review_store
        from omnicompany.packages.services.boss_sight.reviewstage.store import (
            Material,
            is_internal_review_material,
        )

        materials = [
            Material.from_dict(row)
            for row in review_store.list_materials(include_archived=include_archived)
        ]
        if not include_internal:
            materials = [m for m in materials if not is_internal_review_material(m)]
        return materials

    def get(self, material_id: str):
        from omnicompany.packages.services._core import review_store
        from omnicompany.packages.services.boss_sight.reviewstage.store import Material

        row = review_store.get_material(material_id)
        return Material.from_dict(row) if row is not None else None

    def resolve_file_path(self, material):
        return self._file_assets.resolve_file_path(material)


@cmd_review.command("readback")
@click.option("--session-id", default=None, help="省略时使用当前原生 agent 会话。")
@click.option("--conversation-id", default=None, help="显式指定用户可见的根对话。")
@click.option("--provider", default=None, type=click.Choice(["codex", "claude_code"]))
@click.option("--include-archived", is_flag=True)
@click.option("--max", "max_results", type=int, default=200, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_review_readback(session_id, conversation_id, provider, include_archived, max_results, as_json):
    """快速读回本对话送审材料, 只突出有理由且对话未说明的具体内容。"""
    from omnicompany.packages.services.boss_sight.reviewstage.readback import build_review_readback

    payload = build_review_readback(
        _ReviewStoreReadAdapter(),
        session_id=session_id,
        conversation_id=conversation_id,
        provider=provider,
        include_archived=include_archived,
        limit=max_results,
    )
    if not payload.get("context", {}).get("resolved"):
        raise click.ClickException("无法解析当前会话; 请显式传 --session-id 和 --provider。")
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    counts = payload["counts"]
    ctx = payload["context"]
    click.echo(
        f"conversation={ctx['conversation_id']} · 共 {counts['all']} 份 · "
        f"重点 {counts['highlight']} · 对话已说明 {counts['explained']} · "
        f"低关注 {counts['background']}"
    )
    labels = {
        "highlight": "重点: 有明确理由和内容, 但对话未说明",
        "explained": "已在对话说明",
        "background": "低关注: 未填写具体理由或内容不明确",
    }
    for presentation in ("highlight", "explained", "background"):
        rows = [item for item in payload["items"] if item["presentation"] == presentation]
        if not rows:
            continue
        click.echo(f"\n{labels[presentation]} ({len(rows)})")
        for row in rows:
            reason = f" · 理由: {row['reason']}" if row.get("reason") else ""
            url = review_material_open_url(_dashboard_base(), row["id"])
            click.echo(f"- {row['title']} · {url}{reason}")


@cmd_review.command("archive")
@click.argument("material_id")
@click.option(
    "--restore",
    is_flag=True,
    help="Restore a previously soft-archived material.",
)
@external_or_controller
def cmd_review_archive(material_id: str, restore: bool) -> None:
    """Soft-archive a material; --restore makes it visible again.

    2026-08-21: 旧库 router 查无此件时兜底写画布卡 submission.archived ——
    审阅库融合后新提交/review submit 的卡只有画布侧记录, 旧 reviewstage
    库没有, 不兜底的话这批卡永远归档不掉(报 material not found)。"""
    from omnicompany.packages.services.boss_sight.controller.tools import (
        ArchiveReviewstageMaterialRouter,
    )

    resolved = _resolve_material_ref(material_id)
    try:
        click.echo(_invoke_router(
            ArchiveReviewstageMaterialRouter,
            {"material_id": resolved, "archived": not restore},
        ))
        return
    except click.ClickException as exc:
        if "not found" not in str(exc):
            raise
    _archive_canvas_card(resolved, archived=not restore)


def _archive_canvas_card(card_id: str, *, archived: bool) -> None:
    """画布投影卡归档兜底: 直接 mutation 写 submission.archived。"""
    from omnicompany.cli.commands import canvas as canvas_cli
    from omnicompany.packages.services._core import review_store

    material = review_store.get_material(card_id)
    canvas_key = str((material or {}).get("canvas") or "")
    if not material or not canvas_key:
        raise click.ClickException(
            f"找不到材料 {card_id!r}: 旧库与画布投影都没有, 无法归档")
    document = canvas_cli._canvas_document(canvas_key)
    target = next((c for c in document.get("cards") or [] if c.get("id") == card_id), None)
    if not isinstance(target, dict):
        raise click.ClickException(f"画布 {canvas_key} 里没找到卡片 {card_id}")
    submission = target.get("submission") if isinstance(target.get("submission"), dict) else {}
    target["submission"] = {**submission, "archived": archived}
    canvas_cli._mutate(canvas_key, document, {"upsert_cards": [target]}, actor="cli:review-archive")
    review_store.invalidate_cache()
    action = "restored" if not archived else "archived"
    click.echo(f"{action}: name={material['title']!r} id={card_id} (canvas {canvas_key})")


@cmd_review.command("judge")
@click.argument("material_id")
@click.option("--context", default=None)
@click.option("--max-content-chars", type=int, default=6000, show_default=True)
@external_or_controller
def cmd_review_judge(material_id, context, max_content_chars):
    """Judge a reviewstage material and return model_hint advice."""
    from omnicompany.packages.services.boss_sight.controller.tools import JudgeReviewstageMaterialRouter
    args = {"material_id": _resolve_material_ref(material_id), "max_content_chars": max_content_chars}
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
    from omnicompany.packages.services.boss_sight.controller.tools import AnnotateMaterialRouter
    args = {"material_id": _resolve_material_ref(material_id), "content": content}
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
    from omnicompany.packages.services.boss_sight.controller.tools import PushMaterialToUserRouter

    output = _invoke_router(PushMaterialToUserRouter, {
        "material_id": _resolve_material_ref(material_id), "reason": reason,
    })
    if output.startswith("ERROR:"):
        raise click.ClickException(output.removeprefix("ERROR:").strip())
    click.echo(output)


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
    res = relay_feedback(_resolve_material_ref(material_id), reason, plan_id=plan_id, task_id=task_id,
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
    from omnicompany.packages.services.boss_sight.controller.tools import ListPromptArchiveRouter
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
    from omnicompany.packages.services.boss_sight.controller.tools import ProposeChangeRouter
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
    """统一 Agent Worker：启动、投递、恢复、预算、模型路由、互评与身份。"""


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
        from omnicompany.packages.services.boss_sight.services.residents import build_residents
        from omnicompany.packages.services.boss_sight.services.session_resolver import resolve
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
        from omnicompany.packages.services.boss_sight.services import agent_attention
        from omnicompany.packages.services.boss_sight.services.residents import build_residents
        from omnicompany.packages.services.boss_sight.services.session_resolver import resolve
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
