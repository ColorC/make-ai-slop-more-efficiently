# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-02T04:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="omni lock 命令组 - G4 主动防御 CLI (status / scan / handle / config)"
# [OMNI] why="范围性开启 (用户硬规则) - lock enable/disable 切状态, scan 离线扫违规, handle 按分类处理"
# [OMNI] tags=cli,lock,protection,defense
# [OMNI] material_id="material:cli.protection.lock_command.implementation.py"
"""omni lock 命令组 (G4 主动防御).

`omni lock status` — 看锁状态 (开/关 + watched 范围 + 白名单条数)
`omni lock enable [--watched=<path>]` — 开锁 (可选窄范围)
`omni lock disable` — 关锁
`omni lock scan` — 离线扫描, 列违规候选 (不处理)
`omni lock handle [--mode=notice|evict|both] [--dry-run]` — 处理违规
`omni lock config` — 看 / 改 policy 配置
"""
from __future__ import annotations

import json

import click

from omnicompany.packages.services._core.protection import (
    DEFAULT_WATCHED_PATHS,
    DEFAULT_WHITELIST_PATTERNS,
    load_policy,
    save_policy,
    load_baseline,
    save_baseline,
    scan_violations,
    snapshot_current_as_baseline,
    handle_internal_misplace,
    handle_external_write,
    quarantine_dir,
)


@click.group("lock")
def cmd_lock() -> None:
    """omnicompany 主动防御 (G4 锁组).

    范围性开启 - watched_paths 列受锁目录, whitelist_patterns 列豁免.
    内部错位写入留 OMNI-LOCK-VIOLATION 注释, 外部直接写入移除留指导.

    详见: services/_core/protection/__init__.py
    """


@cmd_lock.command("status")
@click.option("--json", "as_json", is_flag=True)
def cmd_lock_status(as_json: bool) -> None:
    """看当前锁状态."""
    policy = load_policy()
    if as_json:
        click.echo(json.dumps(policy, ensure_ascii=False, indent=2))
        return
    click.echo(f"enabled            : {policy.get('enabled', False)}")
    click.echo(f"watched_paths      : {len(policy.get('watched_paths', []))} 项")
    for p in policy.get("watched_paths", []):
        click.echo(f"  - {p}")
    click.echo(f"whitelist_patterns : {len(policy.get('whitelist_patterns', []))} 项 (前 5 项)")
    for p in policy.get("whitelist_patterns", [])[:5]:
        click.echo(f"  - {p}")
    if len(policy.get("whitelist_patterns", [])) > 5:
        click.echo(f"  ... 还有 {len(policy.get('whitelist_patterns', [])) - 5} 项")


@cmd_lock.command("enable")
@click.option("--watched", multiple=True, default=None,
              help="覆盖默认 watched_paths (可多次, 留空则用 DEFAULT_WATCHED_PATHS)")
def cmd_lock_enable(watched: tuple[str, ...]) -> None:
    """开锁."""
    policy = load_policy()
    policy["enabled"] = True
    if watched:
        policy["watched_paths"] = list(watched)
    save_policy(policy)
    click.echo("OK 锁已开启")
    click.echo(f"  watched_paths: {policy['watched_paths']}")
    click.echo("  下一步: omni lock scan 看有没有现存违规")


@cmd_lock.command("disable")
def cmd_lock_disable() -> None:
    """关锁."""
    policy = load_policy()
    policy["enabled"] = False
    save_policy(policy)
    click.echo("OK 锁已关闭 (策略保留, 重新 enable 即恢复)")


# ── PHASE3 plan 命名一致 alias (open/close = enable/disable) ─────
@cmd_lock.command("open")
@click.option("--watched", multiple=True, default=None,
              help="覆盖默认 watched_paths (可多次, 留空则用 DEFAULT_WATCHED_PATHS)")
@click.pass_context
def cmd_lock_open(ctx, watched):
    """开锁 (= lock enable, PHASE3 plan 命名 alias)."""
    ctx.invoke(cmd_lock_enable, watched=watched)


@cmd_lock.command("close")
@click.pass_context
def cmd_lock_close(ctx):
    """关锁 (= lock disable, PHASE3 plan 命名 alias)."""
    ctx.invoke(cmd_lock_disable)


@cmd_lock.command("scan")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
@click.option("--limit", type=int, default=50, help="结果条数上限")
def cmd_lock_scan(as_json: bool, limit: int) -> None:
    """离线扫描所有违规候选 (不处理).

    走 watched_paths 内每个文件:
    - 在白名单 / 注册中心 → 跳过
    - event bus 找到 trace → internal_misplace
    - event bus 找不到 → external_write
    """
    policy = load_policy()
    violations = scan_violations(policy=policy)
    violations = violations[:limit]

    if as_json:
        click.echo(json.dumps([v.to_dict() for v in violations],
                              ensure_ascii=False, indent=2))
        return

    if not violations:
        click.echo("PASS · watched_paths 内无违规")
        return

    internal = [v for v in violations if v.classification == "internal_misplace"]
    external = [v for v in violations if v.classification == "external_write"]

    click.echo(f"扫描结果 ({len(violations)} 条违规):")
    click.echo(f"  internal_misplace : {len(internal)} 条 (内部代码写到错位置)")
    click.echo(f"  external_write    : {len(external)} 条 (外部直接写入)")
    click.echo()

    if internal:
        click.echo("== internal_misplace ==")
        for v in internal[:20]:
            click.echo(f"  [{v.tool or '?'}] {v.rel_path}")
            click.echo(f"    trace_id={v.trace_id} ts={v.timestamp}")
        if len(internal) > 20:
            click.echo(f"  ... 还有 {len(internal) - 20} 条")
        click.echo()

    if external:
        click.echo("== external_write ==")
        for v in external[:20]:
            click.echo(f"  {v.rel_path}")
        if len(external) > 20:
            click.echo(f"  ... 还有 {len(external) - 20} 条")
        click.echo()

    click.echo("处理建议:")
    click.echo("  omni lock handle --mode=notice  # 内部错位加 OMNI-LOCK-VIOLATION 注释")
    click.echo("  omni lock handle --mode=evict   # 外部直接写移到 quarantine + 留指导")
    click.echo("  omni lock handle --mode=both --dry-run  # 看一眼会做什么但不真做")


@cmd_lock.command("handle")
@click.option("--mode", type=click.Choice(["notice", "evict", "both"]), default="both",
              help="处理模式: notice (只处理内部错位) / evict (只处理外部写入) / both (全处理)")
@click.option("--dry-run", is_flag=True, help="预览, 不真改文件")
@click.option("--limit", type=int, default=50, help="处理条数上限")
def cmd_lock_handle(mode: str, dry_run: bool, limit: int) -> None:
    """处理 scan 找出的违规.

    notice 模式: 内部错位文件头加 OMNI-LOCK-VIOLATION 注释 (不删, 教正确写法)
    evict 模式: 外部直接写入移到 .omni/quarantine/ + 原地留 .OMNI-EVICTED.md 指导
    """
    policy = load_policy()
    violations = scan_violations(policy=policy)
    violations = violations[:limit]

    notices = 0
    evictions = 0
    skipped = 0
    reports: list[dict] = []

    for v in violations:
        if v.classification == "internal_misplace":
            if mode in ("notice", "both"):
                r = handle_internal_misplace(v, dry_run=dry_run)
                reports.append(r)
                if r["action"] == "noticed" or r["action"] == "dry_run":
                    notices += 1
                else:
                    skipped += 1
            else:
                skipped += 1
        elif v.classification == "external_write":
            if mode in ("evict", "both"):
                r = handle_external_write(v, dry_run=dry_run)
                reports.append(r)
                if r["action"] == "evicted" or r["action"] == "dry_run":
                    evictions += 1
                else:
                    skipped += 1
            else:
                skipped += 1
        else:
            skipped += 1

    label = "[DRY-RUN] " if dry_run else ""
    click.echo(f"{label}处理完成:")
    click.echo(f"  notice (内部错位)  : {notices} 条")
    click.echo(f"  evict  (外部写入)  : {evictions} 条")
    click.echo(f"  skipped            : {skipped} 条")

    if not dry_run and evictions > 0:
        click.echo(f"\n隔离区: {quarantine_dir().relative_to(quarantine_dir().parent.parent)}")


@cmd_lock.group("meta-io")
def cmd_lock_meta_io() -> None:
    """G4 锁的元 IO 灵活规则 (用户原话"什么目录扫/清/追根除")."""


@cmd_lock_meta_io.command("scan")
@click.option("--json", "as_json", is_flag=True)
def cmd_lock_meta_io_scan(as_json: bool) -> None:
    """扫所有 SingleToolRouter 子类是否声明了 CONSUMED/PRODUCED_META_IO.

    没声明的 tool 在 enforce_unregistered_tools=True 时会被 PreToolUse hook 阻断.
    """
    try:
        from omnicompany.packages.services._core.agent.routers.single_tool import SingleToolRouter
    except ImportError:
        click.echo("找不到 SingleToolRouter, 跳过", err=True)
        raise SystemExit(1)

    # 收集所有子类
    def all_subclasses(cls):
        s = set()
        for sub in cls.__subclasses__():
            s.add(sub)
            s |= all_subclasses(sub)
        return s

    # 触发 import 让所有 router 模块加载
    try:
        import omnicompany.packages.services._core.agent  # noqa: F401
    except ImportError:
        pass

    subclasses = sorted(all_subclasses(SingleToolRouter), key=lambda c: c.__name__)
    declared = []
    missing = []
    for cls in subclasses:
        # 检查类自身是否定义 CONSUMED/PRODUCED (不是继承基类的默认空)
        own_keys = set(cls.__dict__.keys())
        has_own = ("CONSUMED_META_IO" in own_keys) or ("PRODUCED_META_IO" in own_keys)
        consumed = getattr(cls, "CONSUMED_META_IO", ())
        produced = getattr(cls, "PRODUCED_META_IO", ())
        info = {
            "class": cls.__name__,
            "module": cls.__module__,
            "consumed": list(consumed),
            "produced": list(produced),
            "declared": has_own,
        }
        if has_own:
            declared.append(info)
        else:
            missing.append(info)

    if as_json:
        click.echo(json.dumps({
            "declared": declared, "missing": missing,
            "total": len(subclasses),
        }, ensure_ascii=False, indent=2))
        return

    click.echo(f"扫描 {len(subclasses)} 个 SingleToolRouter 子类:")
    click.echo(f"  [OK]   已声明 {len(declared)} 个")
    click.echo(f"  [MISS] 未声明 {len(missing)} 个")
    if missing:
        click.echo()
        click.echo("未声明的:")
        for info in missing:
            click.echo(f"  - {info['class']:35s}  ({info['module']})")
        click.echo()
        click.echo("修法: 在子类加 CONSUMED_META_IO / PRODUCED_META_IO ClassVar")
        click.echo("规范: docs/standards/cli/meta_io.md")


@cmd_lock_meta_io.command("enforce")
@click.option("--unregistered-tools", "ut", is_flag=True,
              help="启用: tool 没声明元 IO 时 PreToolUse 阻断")
@click.option("--unregistered-meta-io", "um", is_flag=True,
              help="启用: 调用未注册元 IO 时阻断")
@click.option("--off", is_flag=True, help="关元 IO 规则 (恢复全 false)")
def cmd_lock_meta_io_enforce(ut: bool, um: bool, off: bool) -> None:
    """切元 IO 规则 enforce 状态."""
    policy = load_policy()
    rules = policy.setdefault("meta_io_rules", {
        "enforce_unregistered_tools": False,
        "enforce_unregistered_meta_io": False,
        "watched_meta_io_per_path": [],
    })
    if off:
        rules["enforce_unregistered_tools"] = False
        rules["enforce_unregistered_meta_io"] = False
        save_policy(policy)
        click.echo("OK 元 IO 规则已关 (enforce_unregistered_tools/meta_io = False)")
        return
    if ut:
        rules["enforce_unregistered_tools"] = True
    if um:
        rules["enforce_unregistered_meta_io"] = True
    save_policy(policy)
    click.echo(f"OK enforce_unregistered_tools={rules['enforce_unregistered_tools']} "
              f"enforce_unregistered_meta_io={rules['enforce_unregistered_meta_io']}")
    click.echo("先 omni lock meta-io scan 看哪些 tool 没声明, 修齐再开 enforce")


@cmd_lock_meta_io.command("status")
def cmd_lock_meta_io_status() -> None:
    """看元 IO 规则状态."""
    policy = load_policy()
    rules = policy.get("meta_io_rules", {})
    click.echo(f"enforce_unregistered_tools  : {rules.get('enforce_unregistered_tools', False)}")
    click.echo(f"enforce_unregistered_meta_io: {rules.get('enforce_unregistered_meta_io', False)}")
    paths = rules.get("watched_meta_io_per_path", [])
    click.echo(f"watched_meta_io_per_path    : {len(paths)} 条规则")
    for p in paths[:5]:
        click.echo(f"  - {p}")


@cmd_lock_meta_io.command("add-rule")
@click.option("--path-prefix", required=True, help="规则路径前缀 (相对项目根, 例 data/_writable/)")
@click.option("--allowed", multiple=True, required=True,
              help="允许的元 IO (可多次), 例 --allowed=meta_io.fs.create_file --allowed=meta_io.fs.overwrite_file")
@click.option("--mode", type=click.Choice(["warn", "enforce"]), default="warn")
def cmd_lock_meta_io_add_rule(path_prefix: str, allowed: tuple[str, ...], mode: str) -> None:
    """加一条 watched_meta_io_per_path 规则.

    规则语义: 落 `path_prefix` 路径下的写入工具调用, 它产生的元 IO 必须在 `allowed` 集合内.
    不在 → 按 `mode` 处理 (warn 提示 / enforce 阻断).
    """
    policy = load_policy()
    rules = policy.setdefault("meta_io_rules", {
        "enforce_unregistered_tools": False,
        "enforce_unregistered_meta_io": False,
        "watched_meta_io_per_path": [],
    })
    rules.setdefault("watched_meta_io_per_path", []).append({
        "path_prefix": path_prefix,
        "allowed_meta_io": list(allowed),
        "mode": mode,
    })
    save_policy(policy)
    click.echo(f"OK 加规则: path_prefix={path_prefix!r} allowed={list(allowed)} mode={mode}")
    click.echo(f"当前共 {len(rules['watched_meta_io_per_path'])} 条 per-path 规则")


@cmd_lock_meta_io.command("clear-rules")
@click.confirmation_option(prompt="确认清空 watched_meta_io_per_path 所有规则?")
def cmd_lock_meta_io_clear_rules() -> None:
    """清空 watched_meta_io_per_path 全部规则."""
    policy = load_policy()
    rules = policy.setdefault("meta_io_rules", {})
    rules["watched_meta_io_per_path"] = []
    save_policy(policy)
    click.echo("OK 已清空 per_path 规则")


@cmd_lock.command("config")
@click.option("--show", is_flag=True, default=True, help="显示当前 policy")
@click.option("--reset", is_flag=True, help="重置为默认值")
def cmd_lock_config(show: bool, reset: bool) -> None:
    """看 / 重置 policy 配置."""
    if reset:
        save_policy({
            "enabled": False,
            "watched_paths": list(DEFAULT_WATCHED_PATHS),
            "whitelist_patterns": list(DEFAULT_WHITELIST_PATTERNS),
            "version": 1,
        })
        click.echo("OK policy 已重置为默认值")
        return
    policy = load_policy()
    click.echo(json.dumps(policy, ensure_ascii=False, indent=2))


@cmd_lock.command("mode")
@click.option("--set", "set_mode",
              type=click.Choice(["warn", "enforce", "off"]),
              help="设置 runtime_mode (PreToolUse hook 实时拦截模式)")
def cmd_lock_mode(set_mode: str | None) -> None:
    """看 / 改 PreToolUse 实时拦截模式.

    - warn (默认):    违规写入时 stderr 给提示但不阻断, 让用户/AI IDE 看到 + 继续工作
    - enforce:        违规写入时 stderr + exit 2 阻断, claude code 拒这次工具调用
    - off:            实时拦截关闭 (离线 scan 仍可用)

    切 enforce 前必须确认 baseline + whitelist 已配齐, 不然会大量阻断.
    """
    policy = load_policy()
    if set_mode is None:
        click.echo(f"runtime_mode : {policy.get('runtime_mode', 'warn')}")
        click.echo(f"enabled      : {policy.get('enabled', False)}")
        click.echo()
        click.echo("说明:")
        click.echo("  warn    - 违规 stderr 提示, 不阻断 (默认, 安全)")
        click.echo("  enforce - 违规 stderr + exit 2 阻断 claude 工具调用")
        click.echo("  off     - 实时拦截关闭 (离线 scan / handle 仍可用)")
        click.echo()
        click.echo("切换: omni lock mode --set=warn|enforce|off")
        return
    policy["runtime_mode"] = set_mode
    save_policy(policy)
    click.echo(f"OK runtime_mode = {set_mode}")
    if set_mode == "enforce":
        click.echo("注意: enforce 会阻断违规写入. 确认 baseline + whitelist 完备:")
        click.echo("  omni lock baseline           # 看 baseline 条数")
        click.echo("  omni lock scan               # 看当前违规")
        click.echo("  omni lock mode --set=warn    # 任何时候切回 warn")


@cmd_lock.command("baseline")
@click.option("--show", is_flag=True, help="显示当前 baseline 条数 (默认行为)")
@click.option("--snapshot", is_flag=True,
              help="把当前 watched 内非白名单 / 非已注册的文件全加进 baseline (grandfather)")
@click.option("--clear", is_flag=True, help="清空 baseline (撤回 grandfather)")
def cmd_lock_baseline(show: bool, snapshot: bool, clear: bool) -> None:
    """baseline (历史快照豁免) 管理.

    锁 enable 时立刻 scan 会找出大量历史文件 (项目代码 git 历史写入, event bus 没 trace).
    `baseline snapshot` 把当前现状全 grandfather 进基线, 之后 scan 只查**新写入**的文件.
    跟白名单不同, baseline 是固定路径列表, 文件被 promote 到注册中心后自动从 baseline 视角消失
    (在注册中心查得到优先).
    """
    if clear:
        save_baseline(set())
        click.echo("OK baseline 已清空")
        return
    if snapshot:
        n = snapshot_current_as_baseline()
        click.echo(f"OK baseline 写入 {n} 条历史路径 (grandfathered)")
        click.echo("  之后 scan 只查 baseline 之外的新写入. 旧文件 promote 到注册中心后从 baseline 消失.")
        return
    # 默认 show
    bl = load_baseline()
    click.echo(f"baseline 条数: {len(bl)}")
    if bl and len(bl) <= 20:
        for p in sorted(bl):
            click.echo(f"  {p}")
    elif bl:
        click.echo("  (前 5 条预览)")
        for p in sorted(bl)[:5]:
            click.echo(f"  {p}")
        click.echo("  ...")
