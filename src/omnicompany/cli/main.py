# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:cli.entry_point.command_router.bootstrap.py"
"""omni CLI 入口 — 统一的执行、观测、管理命令。"""
# Load .env BEFORE any module that reads os.environ at import time (e.g. llm.py)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 修 Windows 终端 GBK 编码 — LLM 输出含 unicode 数学/箭头/中文字符时
# 默认 GBK stdout 会撞 UnicodeEncodeError. 这是反模式 C (llm_first.md §1.5):
# 修编码而非过滤字符. 保 stdout / stderr 接 UTF-8, 全字符不丢.
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import click
from .commands.trace import cmd_trace
from .commands.round_ import cmd_round
from .commands.node import cmd_node
from .commands.loops import cmd_loops
from .commands.pain import cmd_pain
from .commands.evo import cmd_evo
from .commands.domain import cmd_domain
from .commands.inquiry import cmd_inquiry
from .commands.guardian import cmd_guardian
from .commands.debt import cmd_debt
from .commands.registry import cmd_registry
from .commands.assistant import cmd_assistant
from .commands.human import cmd_human
from .commands.llm_audit import cmd_llm, cmd_pipeline
from .commands.docauthor import cmd_docauthor
from .commands.cc import cmd_cc
from .commands.identity import cmd_who, cmd_session, cmd_whoami
from .commands.plan import cmd_plan
from .commands.creation import cmd_new, cmd_sandbox
from .commands.guide import cmd_guide, cmd_reflect
from .commands.self_portrait import cmd_self_portrait
from .commands.registration import cmd_register, cmd_lookup, cmd_register_types
from .commands.protection import cmd_lock
from .commands.meta_io import cmd_meta_io
from .commands.team import cmd_team
from .commands.worker import cmd_worker
from .commands.context import cmd_context
from .commands.chat import cmd_chat
from .commands.progress import cmd_progress
from .commands.convos import cmd_convos
from .commands.workflow import cmd_workflow
from .commands.dashboard import cmd_dashboard
from .commands.project import cmd_project
from .commands.governance import cmd_governance
from .commands.vilo import cmd_vilo
from .commands.research import cmd_research
from .commands.decisions import cmd_decisions
from .commands.refs import cmd_refs

# 统一命令组（执行 + 观测 + 管理）
from .unified import (
    cmd_run, cmd_exec, cmd_replay,
    cmd_tail, cmd_trace_view, cmd_traces,
    cmd_pipelines, cmd_health,
    cmd_describe, cmd_routers, cmd_formats, cmd_nodes,
    cmd_errors, cmd_diagnose,
)


# ── S3e.4 (2026-04-08) Guardian 启动自检 ───────────────────
#
# 任何用 omni 命令的 agent / human 都会经过这个入口。在这里跑一次
# 极轻量的 sentinel:
#   - archmap.yaml 存在 + 可加载 + validate 通过
#   - enforce_mode 是 true (任何人偷偷翻成 false 立即可见)
#   - 没有未处理的 .omni/GUARDIAN_ALERT.md
#   - 没有异常的 .omni/DISABLE_BYPASS sentinel
#
# 任一异常 → stderr 打印一条响亮告警 (但不阻塞 CLI 启动)
# 这是"激活监督"的核心机制: 任何 agent 启动 omnicompany 都立刻知道
# Guardian 在/不在状态。
#
# 设置 OMNICOMPANY_SKIP_GUARDIAN_PRECHECK=1 可跳过(用于自举/测试场景)。

def _guardian_precheck() -> None:
    import os
    if os.environ.get("OMNICOMPANY_SKIP_GUARDIAN_PRECHECK") == "1":
        return
    try:
        from omnicompany.core.guarded_write import sentinel_check_guardian_integrity
        rep = sentinel_check_guardian_integrity()
    except Exception as e:
        # sentinel 自己挂了 — 这本身就是一个严重信号
        click.echo(
            click.style(
                f"\n🚨 OmniGuardian sentinel 启动失败: {e}\n"
                f"   你看到这个意味着 guarded_write / archmap loader 不可用,\n"
                f"   架构监督处于失效状态。请立刻检查 docs/archmap.yaml\n"
                f"   和 src/omnicompany/core/{{archmap,guarded_write}}.py。\n",
                fg="red", bold=True,
            ),
            err=True,
        )
        return

    if rep.get("ok"):
        return

    # 有 issue, 醒目展示
    click.echo("", err=True)
    click.echo(click.style(
        "🚨 OmniGuardian PRE-CHECK 发现问题:", fg="yellow", bold=True), err=True)
    for issue in rep.get("issues", []):
        click.echo(f"   ⚠ {issue}", err=True)
    if not rep.get("archmap_loadable"):
        click.echo(click.style(
            "   ➜ archmap.yaml 不可加载, Guardian 处于降级状态。\n"
            "     检查 docs/archmap.yaml 是否被删除/损坏, 然后跑:\n"
            "     omni guardian archmap validate", fg="yellow"), err=True)
    if rep.get("alert_present"):
        click.echo(click.style(
            "   ➜ 存在未处理的 .omni/GUARDIAN_ALERT.md, 请阅读并修复后删除该文件",
            fg="yellow"), err=True)
    if rep.get("bypass_active"):
        click.echo(click.style(
            f"   ➜ Guardian bypass 当前激活 ({rep['bypass_active']}), "
            "所有 write_file 不经审计", fg="red", bold=True), err=True)
    if rep.get("enforce_mode") is False:
        click.echo(click.style(
            "   ➜ enforce_mode=false (观察期模式), 软违规只警告不拦", fg="yellow"), err=True)
    click.echo("", err=True)


class _GracefulGroup(click.Group):
    """顶层命令组: 把"缺失的可选服务"从 ModuleNotFoundError traceback 降级成友好提示。

    发布版(白名单平台)不含某些内部/隐私服务(resume_steward / work_history /
    _authoring.docauthor 等); 调用依赖它们的子命令时优雅提示, 而非崩栈。
    内部版服务齐全 → 永不触发, 完全无感。
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ModuleNotFoundError as e:
            name = e.name or ""
            if name.startswith("omnicompany.packages.services"):
                raise click.ClickException(
                    f"此命令依赖的服务未包含在本构建中: {name}\n"
                    f"  (这是发布版裁掉的内部/可选服务; 完整能力见内部版或自行实现该服务。)"
                ) from None
            raise


@click.group(cls=_GracefulGroup)
def cli():
    """omnicompany CLI — 统一执行、观测、管理"""
    _guardian_precheck()


# ── 原有观察者命令（向后兼容）──
cli.add_command(cmd_trace)
cli.add_command(cmd_round)
cli.add_command(cmd_node)
cli.add_command(cmd_loops)
cli.add_command(cmd_pain)
cli.add_command(cmd_evo)
cli.add_command(cmd_domain)

# ── 统一执行命令 ──
cli.add_command(cmd_run)
cli.add_command(cmd_exec)
cli.add_command(cmd_replay)

# ── 统一观测命令 ──
cli.add_command(cmd_tail)
cli.add_command(cmd_trace_view, name="trace-view")  # 避免与旧 trace 冲突
cli.add_command(cmd_traces)

# ── 管理命令 ──
cli.add_command(cmd_pipelines)
cli.add_command(cmd_health)

# ── 自省命令 ──
cli.add_command(cmd_describe)
cli.add_command(cmd_routers)
cli.add_command(cmd_formats)
cli.add_command(cmd_nodes)

# ── 诊断命令 ──
cli.add_command(cmd_errors)
cli.add_command(cmd_diagnose)

# ── 用户询问 ──
cli.add_command(cmd_inquiry)

# ── 守护检查 ──
cli.add_command(cmd_guardian)
cli.add_command(cmd_debt)

# ── 注册体系查询 ──
cli.add_command(cmd_registry)

# ── 身份 (claude code session 跟 dashboard 共用一身份链) ──
cli.add_command(cmd_who)
cli.add_command(cmd_whoami)  # CLI-PHASE3 alias 跟 plan 命名一致
cli.add_command(cmd_session)
cli.add_command(cmd_plan)  # plan binding manager (list / current / use / show)

# ── 创建 / 沙盒 (8 种 kind 实例创建 + 沙盒指引) ──
cli.add_command(cmd_new)
cli.add_command(cmd_sandbox)

# ── 指引 + 反思 (CLI-PHASE3 第五段) ──
cli.add_command(cmd_guide)
cli.add_command(cmd_reflect)

# ── 自我画像 (CORE-SELF-STABILITY 第一阶段 · 2026-05-04) ──
cli.add_command(cmd_self_portrait)

# ── 注册中心 (显式注册 + 查询 + kind 类型列表) ──
cli.add_command(cmd_register)
cli.add_command(cmd_lookup)
cli.add_command(cmd_register_types)

# ── G4 主动防御 (锁组) ──
cli.add_command(cmd_lock)

# ── 元 IO (用户原始需求 6.6 — tool 操作 + 状态绑定) ──
cli.add_command(cmd_meta_io)

# ── team yaml 命令组 (验证 / 显示 / 加载) ──
cli.add_command(cmd_team)

# ── Assistant 上下文 + 外部任务派发（claude -p 对齐） ──
cli.add_command(cmd_assistant)

# ── Human Bus · 人类审批 inbox / resolve (A1 2026-04-23) ──
cli.add_command(cmd_human)

# ── LLM 调用档案查询 (Phase 2.5) ──
cli.add_command(cmd_llm)

# ── 管线级 audit-info (Phase 5.3) ──
cli.add_command(cmd_pipeline)

# ── docauthor 自动文档作者 (L2 工作流 · 2026-04-25) ──
cli.add_command(cmd_docauthor)

# ── Claude Code wrapper (ROADMAP 5b · 2026-05-02) ──
cli.add_command(cmd_cc)

# External agent workers (Codex / Claude Code)
cli.add_command(cmd_worker)

# Distributed progressive context resolver
cli.add_command(cmd_context)

# OmniChat session manager (chat session CRUD + plan binding + CC/Codex db browser)
cli.add_command(cmd_chat)

# 公开调研管线导航 + 统一研究库查询 (2026-06-14)
cli.add_command(cmd_research)

# 统一决策库 — 手记/召回/接树 (2026-06-18): 决策记录主线,源无关契约,提取后续往同一库灌数
cli.add_command(cmd_decisions)

# 本地资产发现 — 用公开内容/参考源前先查本地 (2026-06-14)
cli.add_command(cmd_refs)

# (omni board 三态 lane 工作板已于 2026-06-12 退役 — 项目唯一权威见 omni project / core/projects_registry)
cli.add_command(cmd_progress)
cli.add_command(cmd_convos)
cli.add_command(cmd_workflow)

# 驾驶舱免重启更新 (2026-06-11): ui/ext 热更新触发 + dashboard 进程重启 (不碰 ccdaemon)
cli.add_command(cmd_dashboard)

# 项目注册表 (2026-06-12): 首页项目工作板数据源, 用户+总控共同入口
cli.add_command(cmd_project)

# 治理部门 (2026-06-12): 计划治理(归属/汉化/格式) + 工作历史整理(原进化部门重组), 便宜模型干活
cli.add_command(cmd_governance)

# Vilo 内容管线 (2026-06-13 内化): 管线代码进 domains/vilo, 产物进 data/domains/vilo, 走 omni 调用
cli.add_command(cmd_vilo)

# BOSS SIGHT cli subcommands (2026-05-25): 把 17 个总控 function call tool 迁移到 omni cli.
# 新增 / 扩展:
#   omni worker {spawn,fork,signal,bind,unbind,bindings,audit-traces,archive}
#   omni plan   {complete,audit,binder}
#   omni review {submit,list,annotate,push}
#   omni prompt list
#   omni propose change
# caller-based access control: OMNI_CLI_CALLER ∈ {external, controller, subagent}.
try:
    from .commands.boss_sight import register_boss_sight_commands
    register_boss_sight_commands(cli, cmd_worker=cmd_worker, cmd_plan=cmd_plan)
except Exception as _e:  # noqa: BLE001
    import logging
    logging.getLogger(__name__).warning("boss_sight cli register failed: %s", _e)

if __name__ == "__main__":
    cli()

