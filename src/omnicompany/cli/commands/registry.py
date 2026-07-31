# [OMNI] origin=claude-code domain=cli ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:cli.commands.registry_query_and_health.implementation.py"
"""omni registry — 注册体系查询命令组

子命令:
    omni registry list [--type router|format|...] [--package <pkg>]
                                列出已注册实体
    omni registry health [--type router|format] [--grade C,D,F]
                                显示健康档案摘要
    omni registry regressions --since <commit>
                                列出自指定 commit 以来等级下降的实体
    omni registry mark-strict <entity_id> [--unmark]
                                标记/取消实体为 strict member
    omni registry status [--type router|format]
                                列出所有 strict member 及其最新健康等级
    omni registry rebuild --from-headers
                                从 OmniMark 头 material_id 字段重建扁平索引
    omni registry whois <material_id>
                                按 material_id 查 file_path + 元数据
    omni registry whoami <file_path>
                                按 file_path 反查 material_id
    omni registry materials [--kind ...] [--json]
                                列 OmniMark material_id 索引全量
"""
import sys
from pathlib import Path

import click


def _get_archive_dir() -> Path:
    """返回默认健康档案目录（data/registry/health/）。"""
    from omnicompany.packages.services._core.registry import _DEFAULT_REGISTRY_DIR
    return _DEFAULT_REGISTRY_DIR / "health"


@click.group("registry")
def cmd_registry():
    """注册体系命令组：实体查询 + 健康档案 + 回归检测。"""


# ─── registry list ─────────────────────────────────────────────────────────────

@cmd_registry.command("list")
@click.option("--type", "type_filter", type=str, default=None,
              help="实体类型过滤（router / format / pipeline / ...），留空显示所有")
@click.option("--package", "pkg_filter", type=str, default=None,
              help="包路径前缀过滤（如 demogame.table_learning）")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="以 JSON 格式输出")
def cmd_registry_list(type_filter: str | None, pkg_filter: str | None, as_json: bool):
    """列出已注册实体。"""
    from omnicompany.packages.services._core.registry import get_registry

    reg = get_registry()

    types_to_scan: list[str] = []
    if type_filter:
        types_to_scan = [type_filter]
    else:
        reg_dir = reg.registry_dir
        if reg_dir.exists():
            types_to_scan = [
                d.name for d in sorted(reg_dir.iterdir())
                if d.is_dir() and not d.name.startswith(".") and d.name != "health"
            ]

    entries = []
    for t in types_to_scan:
        for entry in reg.iter_type(t):
            if pkg_filter and not entry.package.startswith(pkg_filter):
                continue
            entries.append(entry)

    if as_json:
        import json
        click.echo(json.dumps([e.to_dict() for e in entries], ensure_ascii=False, indent=2))
        return

    if not entries:
        click.echo("(无已注册实体)")
        return

    # 按类型分组展示
    by_type: dict[str, list] = {}
    for e in entries:
        by_type.setdefault(e.type, []).append(e)

    total = 0
    for t, elist in sorted(by_type.items()):
        click.echo(click.style(f"\n[{t}] ({len(elist)} 个)", fg="cyan", bold=True))
        for e in elist:
            pkg = f" [{e.package}]" if e.package else ""
            src = e.source_file
            click.echo(f"  {e.entity_id}{pkg}")
            click.echo(click.style(f"    {src}", fg="bright_black"))
        total += len(elist)

    click.echo()
    click.echo(click.style(f"共 {total} 个实体", bold=True))


# ─── registry health ───────────────────────────────────────────────────────────

_GRADE_COLOR = {"A": "green", "B": "green", "C": "yellow", "D": "red", "F": "red", "?": "white"}

@cmd_registry.command("health")
@click.option("--type", "type_filter", type=str, default=None,
              help="实体类型过滤（默认同时显示 router / format）")
@click.option("--grade", "grade_filter", type=str, default=None,
              help="等级过滤，逗号分隔（如 C,D,F）")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="以 JSON 格式输出")
def cmd_registry_health(type_filter: str | None, grade_filter: str | None, as_json: bool):
    """显示健康档案摘要（来自 data/registry/health/）。"""
    from omnicompany.packages.services._core.registry.archive import HealthArchive

    archive = HealthArchive(_get_archive_dir())

    allowed_grades: set[str] | None = None
    if grade_filter:
        allowed_grades = set(g.strip().upper() for g in grade_filter.split(","))

    types_to_scan: list[str] = []
    if type_filter:
        types_to_scan = [type_filter]
    else:
        archive_dir = _get_archive_dir()
        if archive_dir.exists():
            types_to_scan = [
                d.name for d in sorted(archive_dir.iterdir())
                if d.is_dir() and not d.name.startswith(".")
            ]

    if as_json:
        import json
        all_snaps = {}
        for t in types_to_scan:
            snaps = []
            for entity_id, snap in archive.iter_type(t):
                if allowed_grades and snap.grade not in allowed_grades:
                    continue
                snaps.append({
                    "entity_id": entity_id,
                    "grade": snap.grade,
                    "score": snap.score,
                    "timestamp": snap.timestamp,
                    "commit_hash": snap.commit_hash,
                    "summary": snap.summary,
                    "issues_count": len(snap.issues),
                })
            all_snaps[t] = snaps
        click.echo(json.dumps(all_snaps, ensure_ascii=False, indent=2))
        return

    grand_total = 0
    for t in types_to_scan:
        summary = archive.summary_by_type(t)
        total = summary["total"]
        if total == 0:
            continue

        # 打印该类型的等级分布
        by_grade = summary["by_grade"]
        dist_parts = []
        for g in ["A", "B", "C", "D", "F", "?"]:
            cnt = by_grade.get(g, 0)
            if cnt:
                dist_parts.append(click.style(f"{g}:{cnt}", fg=_GRADE_COLOR.get(g, "white")))
        dist_str = "  ".join(dist_parts)
        click.echo(click.style(f"\n[{t}] {total} 个实体", fg="cyan", bold=True) + "  " + dist_str)

        # 打印需要关注的实体（D/F 或通过 grade_filter 指定）
        focus_grades = allowed_grades or {"D", "F"}
        shown = 0
        for entity_id, snap in archive.iter_type(t):
            if snap.grade not in focus_grades:
                continue
            grade_colored = click.style(f"[{snap.grade}]", fg=_GRADE_COLOR.get(snap.grade, "white"), bold=True)
            click.echo(f"  {grade_colored} {entity_id}")
            click.echo(click.style(f"    {snap.summary}", fg="bright_black"))
            # 最多显示前 3 个失败 check
            for issue in snap.issues[:3]:
                obs = issue.get("observation", "")[:80]
                click.echo(click.style(f"    • {issue.get('check_id', '?')} — {obs}", fg="bright_black"))
            if len(snap.issues) > 3:
                click.echo(click.style(f"    … 共 {len(snap.issues)} 个问题", fg="bright_black"))
            shown += 1

        if shown == 0 and allowed_grades:
            click.echo(f"  (无等级 {grade_filter} 的实体)")
        grand_total += total

    if not types_to_scan or grand_total == 0:
        click.echo("(健康档案为空，请先运行增量诊断：python -m omnicompany.packages.services._core.registry.incremental)")
        return

    click.echo()
    if not allowed_grades or allowed_grades == {"D", "F"}:
        click.echo(click.style("提示: 使用 --grade A,B,C,D,F 查看全部等级，或 --grade A,B 只看优秀实体", fg="bright_black"))


# ─── registry regressions ─────────────────────────────────────────────────────

@cmd_registry.command("regressions")
@click.option("--since", "reference_commit", type=str, required=True,
              help="参考 git commit hash（短 hash 即可）")
@click.option("--type", "type_filter", type=str, default=None,
              help="实体类型过滤（默认全部）")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="以 JSON 格式输出")
def cmd_registry_regressions(reference_commit: str, type_filter: str | None, as_json: bool):
    """列出自指定 commit 以来健康等级下降的实体。"""
    from omnicompany.packages.services._core.registry.archive import HealthArchive

    archive = HealthArchive(_get_archive_dir())

    archive_dir = _get_archive_dir()
    types_to_scan: list[str] = []
    if type_filter:
        types_to_scan = [type_filter]
    elif archive_dir.exists():
        types_to_scan = [
            d.name for d in sorted(archive_dir.iterdir())
            if d.is_dir() and not d.name.startswith(".")
        ]

    all_entity_ids: list[str] = []
    for t in types_to_scan:
        for entity_id, _ in archive.iter_type(t):
            all_entity_ids.append(entity_id)

    if not all_entity_ids:
        click.echo("(健康档案为空，无法检测回归)")
        sys.exit(0)

    regressions = archive.regressions_since(all_entity_ids, reference_commit)

    if as_json:
        import json
        click.echo(json.dumps(regressions, ensure_ascii=False, indent=2))
        return

    if not regressions:
        click.echo(click.style(f"自 {reference_commit} 以来无回归（共检查 {len(all_entity_ids)} 个实体）", fg="green"))
        return

    click.echo(click.style(f"自 {reference_commit} 以来发现 {len(regressions)} 个回归:", fg="red", bold=True))
    click.echo()
    for r in regressions:
        before_g = r["before_grade"]
        after_g = r["after_grade"]
        before_colored = click.style(before_g, fg=_GRADE_COLOR.get(before_g, "white"))
        after_colored = click.style(after_g, fg=_GRADE_COLOR.get(after_g, "white"), bold=True)
        before_commit = r.get("before_commit", "?")
        after_commit = r.get("after_commit", "?")
        click.echo(f"  {r['entity_id']}")
        click.echo(f"    {before_colored} ({before_commit}) → {after_colored} ({after_commit})")
    click.echo()
    click.echo(click.style(f"共检查 {len(all_entity_ids)} 个实体", fg="bright_black"))


# ─── registry mark-strict ─────────────────────────────────────────────────────

@cmd_registry.command("mark-strict")
@click.argument("entity_id")
@click.option("--unmark", is_flag=True, default=False,
              help="取消 strict member 标记（而非标记）")
def cmd_registry_mark_strict(entity_id: str, unmark: bool):
    """标记实体为 strict member（质量门控对象）。

    strict member 的 attrs["strict_member"] = True，出现在 `omni registry status` 中。
    使用 --unmark 取消标记。

    示例:
        omni registry mark-strict router:demogame.table_learning.FieldClassifierRouter
        omni registry mark-strict format:demogame.table-schema --unmark
    """
    from omnicompany.packages.services._core.registry import get_registry

    reg = get_registry()
    entry = reg.read(entity_id)

    if entry is None:
        click.echo(click.style(f"实体 '{entity_id}' 未在注册表中找到。", fg="red"))
        click.echo("提示: 先运行 `omni registry list` 查看已注册实体。")
        sys.exit(1)

    was_strict = entry.attrs.get("strict_member", False)

    if unmark:
        if not was_strict:
            click.echo(f"'{entity_id}' 本来就不是 strict member，无需取消。")
            return
        entry.attrs.pop("strict_member", None)
        reg.write(entry)
        click.echo(click.style(f"✓ 已取消 strict member: {entity_id}", fg="yellow"))
    else:
        if was_strict:
            click.echo(f"'{entity_id}' 已是 strict member，无需重复标记。")
            return
        entry.attrs["strict_member"] = True
        reg.write(entry)
        click.echo(click.style(f"✓ 已标记为 strict member: {entity_id}", fg="green"))
        click.echo(click.style(
            "提示: 运行 `omni registry status` 查看所有 strict member 的健康等级。",
            fg="bright_black",
        ))


# ─── registry status ──────────────────────────────────────────────────────────

@cmd_registry.command("status")
@click.option("--type", "type_filter", type=str, default=None,
              help="实体类型过滤（router / format / ...），留空显示所有")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="以 JSON 格式输出")
def cmd_registry_status(type_filter: str | None, as_json: bool):
    """列出所有 strict member 及其最新健康等级。

    strict member 是通过 `omni registry mark-strict` 标记的质量门控对象。
    健康档案优先读取就近 .omni/health/ 目录，回退到中央 data/registry/health/。
    """
    from omnicompany.packages.services._core.registry import get_registry
    from omnicompany.packages.services._core.registry.archive import HealthArchive

    reg = get_registry()
    archive = HealthArchive(_get_archive_dir())

    # 收集 strict member
    all_entries = reg.list_all()
    strict_members = [
        e for e in all_entries
        if e.attrs.get("strict_member", False)
        and (type_filter is None or e.type == type_filter)
    ]

    if not strict_members:
        if type_filter:
            click.echo(f"(没有 {type_filter} 类型的 strict member)")
        else:
            click.echo("(尚未标记任何 strict member；使用 `omni registry mark-strict <entity_id>` 标记)")
        return

    # 构建状态列表
    rows = []
    for entry in strict_members:
        snap = archive.read_latest(entry.entity_id)
        if snap is None:
            # 尝试就近 .omni/health/
            snap = _read_proximity_snapshot(entry)

        grade = snap.grade if snap else "?"
        score = snap.score if snap else 0.0
        timestamp = snap.timestamp if snap else ""
        summary = snap.summary if snap else "(未诊断)"
        issues_count = len(snap.issues) if snap else 0

        rows.append({
            "entity_id": entry.entity_id,
            "type": entry.type,
            "package": entry.package,
            "grade": grade,
            "score": round(score, 3),
            "issues_count": issues_count,
            "timestamp": timestamp,
            "summary": summary,
        })

    if as_json:
        import json
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    # 按等级排序（F/D/C/B/A/?）
    _grade_order = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4, "?": -1}
    rows.sort(key=lambda r: (_grade_order.get(r["grade"], -1), r["entity_id"]))

    # 统计
    grade_counts: dict[str, int] = {}
    for r in rows:
        g = r["grade"]
        grade_counts[g] = grade_counts.get(g, 0) + 1

    dist_parts = []
    for g in ["A", "B", "C", "D", "F", "?"]:
        cnt = grade_counts.get(g, 0)
        if cnt:
            dist_parts.append(click.style(f"{g}:{cnt}", fg=_GRADE_COLOR.get(g, "white")))
    dist_str = "  ".join(dist_parts)

    click.echo(click.style(
        f"\n Strict Member 健康状态 ({len(rows)} 个)",
        fg="cyan", bold=True,
    ) + "  " + dist_str)
    click.echo()

    # 按类型分组
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)

    for t, trows in sorted(by_type.items()):
        click.echo(click.style(f"[{t}]", fg="cyan"))
        for r in trows:
            grade = r["grade"]
            grade_colored = click.style(f" {grade} ", fg=_GRADE_COLOR.get(grade, "white"), bold=True)
            score_str = f"{r['score']:.2f}"
            issues_str = (
                click.style(f"({r['issues_count']} 问题)", fg="red")
                if r["issues_count"] > 0
                else click.style("(0 问题)", fg="green")
            )
            ts_str = r["timestamp"][:10] if r["timestamp"] else "未诊断"
            click.echo(f"  [{grade_colored}] {score_str}  {r['entity_id']}  {issues_str}")
            click.echo(click.style(f"       {r['summary'][:80]}  [{ts_str}]", fg="bright_black"))
        click.echo()

    # 质量门状态
    failing = [r for r in rows if r["grade"] in ("D", "F")]
    undiagnosed = [r for r in rows if r["grade"] == "?"]
    if failing:
        click.echo(click.style(
            f"⚠ 质量门未通过: {len(failing)} 个 D/F 级 strict member", fg="red", bold=True,
        ))
    elif undiagnosed:
        click.echo(click.style(
            f"? {len(undiagnosed)} 个 strict member 尚未诊断", fg="yellow",
        ))
    else:
        click.echo(click.style("✓ 所有 strict member 健康等级 C 或以上", fg="green", bold=True))


def _read_proximity_snapshot(entry: "InstanceEntry"):  # type: ignore[name-defined]
    """尝试从就近 .omni/health/ 目录读取最新健康快照。"""
    try:
        from omnicompany.packages.services._core.registry import _DEFAULT_SOURCE_ROOT
        from omnicompany.packages.services._core.registry.archive import HealthSnapshot
        import json as _json

        source_file = entry.source_file
        if not source_file:
            return None
        # source_file 相对于 omnicompany 根目录（_DEFAULT_SOURCE_ROOT.parent）
        abs_source = _DEFAULT_SOURCE_ROOT.parent / source_file
        entity_type_dir = "routers" if entry.type == "router" else f"{entry.type}s"
        health_dir = abs_source.parent / ".omni" / "health" / entity_type_dir
        jsonl_path = health_dir / f"{entry.name}.jsonl"
        if not jsonl_path.exists():
            return None
        last_line = ""
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            return None
        return HealthSnapshot.from_dict(_json.loads(last_line))
    except Exception:
        return None


# ─── registry rebuild / materials / whois / whoami ────────────────────────────
# 2026-05-03 立: J 管线给 ~85% 文件批量写了 material_id 头, 这组命令把头里的
# material_id 同步成扁平索引, 让消费方 (G4 锁 / guardian / lap_auditor) 能按
# material_id 查 file_path 跟 kind. 详见 registry/material_index.py.

def _resolve_project_root() -> Path:
    """omnicompany 项目根 (有 src/omnicompany/ 子目录).

    _DEFAULT_SOURCE_ROOT 历史 off-by-1 落到 src/omnicompany/packages, 真根是再上 3 层.
    """
    from omnicompany.packages.services._core.registry import _DEFAULT_SOURCE_ROOT
    # src/omnicompany/packages → src/omnicompany → src → omnicompany_root
    return _DEFAULT_SOURCE_ROOT.parent.parent.parent


def _default_scan_scopes() -> list[Path]:
    """默认扫描范围 — omnicompany 项目根下 src/omnicompany + templates + docs."""
    project_root = _resolve_project_root()
    scopes = [
        project_root / "src" / "omnicompany",
        project_root / "templates",
        project_root / "docs",
    ]
    return [s for s in scopes if s.exists()]


@cmd_registry.command("rebuild")
@click.option("--from-headers", "from_headers", is_flag=True, default=False,
              help="从所有文件 OmniMark 头 material_id 字段重建扁平索引")
@click.option("--scope", "scopes_str", type=str, default=None,
              help="逗号分隔扫描根目录 (默认 src/omnicompany + templates + docs)")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="JSON 输出")
def cmd_registry_rebuild(from_headers: bool, scopes_str: str | None, as_json: bool):
    """重建注册索引.

    --from-headers: 从所有文件 OmniMark 头里的 material_id 字段同步出扁平索引.
    其他重建模式 (例如重新 AST 扫描 6 种实体) 已由 omni registry list 触发, 不必重跑.
    """
    if not from_headers:
        click.echo("错误: 必须指定 --from-headers (其他重建模式暂未实现)", err=True)
        sys.exit(1)

    from omnicompany.packages.services._core.registry.material_index import (
        get_material_id_index,
    )

    project_root = _resolve_project_root()
    if scopes_str:
        scopes = [Path(s.strip()) for s in scopes_str.split(",") if s.strip()]
        scopes = [s if s.is_absolute() else (project_root / s) for s in scopes]
    else:
        scopes = _default_scan_scopes()

    index = get_material_id_index()
    click.echo(click.style(
        f"扫描 {len(scopes)} 个根目录, 从 OmniMark 头同步 material_id 索引...",
        fg="cyan",
    ))
    for s in scopes:
        click.echo(click.style(f"  - {s}", fg="bright_black"))

    result = index.rebuild_from_headers(scopes, project_root)

    if as_json:
        import json
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    click.echo()
    click.echo(click.style("─── 索引重建完成 ───", fg="green", bold=True))
    click.echo(f"  扫描文件:        {result['total_scanned']}")
    click.echo(f"  含 OmniMark 头:  {result['total_with_header']}")
    click.echo(f"  含 material_id:  {result['total_with_material_id']}")
    click.echo(click.style(
        f"  写入索引:        {result['entries_written']}", fg="green",
    ))
    click.echo(f"  索引位置:        {index.index_path}")

    conflicts = result.get("conflicts") or []
    if conflicts:
        click.echo()
        click.echo(click.style(
            f"⚠ 检测到 {len(conflicts)} 条 material_id 冲突 (同 id 多文件):",
            fg="red", bold=True,
        ))
        for c in conflicts[:10]:
            click.echo(click.style(f"  {c['material_id']}", fg="red"))
            for f in c['files']:
                click.echo(click.style(f"    - {f}", fg="bright_black"))
        if len(conflicts) > 10:
            click.echo(click.style(
                f"  ... 共 {len(conflicts)} 条, 仅显示前 10",
                fg="bright_black",
            ))


@cmd_registry.command("whois")
@click.argument("material_id")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="JSON 输出")
def cmd_registry_whois(material_id: str, as_json: bool):
    """按 material_id 查 file_path + OmniMark 元数据."""
    from omnicompany.packages.services._core.registry.material_index import (
        get_material_id_index,
    )
    index = get_material_id_index()
    entry = index.lookup(material_id)
    if entry is None:
        click.echo(click.style(
            f"未找到 material_id '{material_id}' 的记录.", fg="red",
        ))
        click.echo(click.style(
            "提示: 先跑 `omni registry rebuild --from-headers` 同步索引.",
            fg="bright_black",
        ))
        sys.exit(1)

    if as_json:
        import json
        click.echo(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2))
        return

    click.echo(click.style(entry.material_id, fg="cyan", bold=True))
    click.echo(f"  file_path:  {entry.file_path}")
    if entry.kind:
        click.echo(f"  kind:       {entry.kind}")
    if entry.domain:
        click.echo(f"  domain:     {entry.domain}")
    if entry.origin:
        click.echo(f"  origin:     {entry.origin}")
    if entry.ts:
        click.echo(f"  ts:         {entry.ts}")
    if entry.agent:
        click.echo(f"  agent:      {entry.agent}")
    if entry.summary:
        click.echo(f"  summary:    {entry.summary}")
    if entry.why:
        click.echo(f"  why:        {entry.why}")
    if entry.tags:
        click.echo(f"  tags:       {', '.join(entry.tags)}")


@cmd_registry.command("whoami")
@click.argument("file_path")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="JSON 输出")
def cmd_registry_whoami(file_path: str, as_json: bool):
    """按 file_path 反查 material_id."""
    from omnicompany.packages.services._core.registry.material_index import (
        get_material_id_index,
    )
    index = get_material_id_index()
    mid = index.reverse_lookup(file_path)
    if mid is None:
        click.echo(click.style(
            f"未找到 file_path '{file_path}' 对应的 material_id.", fg="red",
        ))
        sys.exit(1)
    if as_json:
        import json
        entry = index.lookup(mid)
        click.echo(json.dumps(
            entry.to_dict() if entry else {"material_id": mid},
            ensure_ascii=False, indent=2,
        ))
        return
    click.echo(mid)


@cmd_registry.command("materials")
@click.option("--kind", "kind_filter", type=str, default=None,
              help="按 OmniMark type 字段过滤 (router/agent/material/team/data/...)")
@click.option("--package", "pkg_filter", type=str, default=None,
              help="按 file_path 前缀过滤")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="JSON 输出")
def cmd_registry_materials(kind_filter: str | None, pkg_filter: str | None, as_json: bool):
    """列 OmniMark material_id 索引全量."""
    from omnicompany.packages.services._core.registry.material_index import (
        get_material_id_index,
    )
    index = get_material_id_index()
    entries = index.list_all()
    if kind_filter:
        entries = [e for e in entries if e.kind == kind_filter]
    if pkg_filter:
        entries = [e for e in entries if e.file_path.startswith(pkg_filter)]
    entries.sort(key=lambda e: e.material_id)

    if as_json:
        import json
        click.echo(json.dumps(
            [e.to_dict() for e in entries], ensure_ascii=False, indent=2,
        ))
        return

    if not entries:
        click.echo("(索引为空; 先跑 `omni registry rebuild --from-headers`)")
        return

    by_kind: dict[str, list] = {}
    for e in entries:
        by_kind.setdefault(e.kind or "(unknown)", []).append(e)

    total = 0
    for k, elist in sorted(by_kind.items()):
        click.echo(click.style(f"\n[{k}] ({len(elist)} 个)", fg="cyan", bold=True))
        for e in elist[:50]:
            click.echo(f"  {e.material_id}")
            click.echo(click.style(f"    {e.file_path}", fg="bright_black"))
        if len(elist) > 50:
            click.echo(click.style(
                f"  ... 共 {len(elist)} 个, 仅显示前 50 (用 --json 看全)",
                fg="bright_black",
            ))
        total += len(elist)

    click.echo()
    meta = index.meta
    if meta:
        click.echo(click.style(
            f"索引位置: {index.index_path}", fg="bright_black",
        ))
        click.echo(click.style(
            f"重建时间: {meta.get('rebuilt_at', '-')}", fg="bright_black",
        ))
    click.echo(click.style(f"共 {total} 条 material_id", bold=True))
