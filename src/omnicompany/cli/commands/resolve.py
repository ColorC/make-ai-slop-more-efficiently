# [OMNI] origin=claude-code domain=cli/commands ts=2026-07-02T00:00:00Z type=router status=active agent=claude
# [OMNI] summary="omni resolve CLI: 解析任意引用(双链/omni URI/裸id)到真源位置, --verify 跑回指自检, --json 结构化; resolve rebuild-index 触发材料索引重建(--incremental 增量)。"
# [OMNI] why="语义OS目标架构3.2统一引用解析器的命令面。命名选 resolve(动作语义, 无冲突; refs 已被 research 域占用)。薄壳, 逻辑全在 packages/services/_core/registry/resolver.py。"
# [OMNI] tags=cli,resolve,unified-reference,resolver
# [OMNI] material_id="material:cli.commands.unified_reference_resolve.implementation.py"
"""omni resolve — 统一引用解析命令组。

    omni resolve <引用> [--verify] [--json]
        解析一个引用(类型化双链 [[kind:id]] / URI omni://kind/id / 裸 id),
        打印命中的适配器 / 真源位置 / 存在性 / 元信息。--verify 附回指自检。

    omni resolve rebuild-index [--incremental] [--scope a,b,c] [--json]
        重建(或增量刷新)MaterialIdIndex —— material 适配器的底层索引。
"""
from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import click


class _ResolveGroup(click.Group):
    """裸 `omni resolve <引用>` 不是已知子命令时, 回退到 `ref` 子命令解析该引用。

    (Click 的 group + positional argument 会把子命令名吞成参数; 用回退解析规避。)
    """

    def resolve_command(self, ctx, args):
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            return super().resolve_command(ctx, ["ref", *args])
        return super().resolve_command(ctx, args)


@click.group("resolve", cls=_ResolveGroup)
def cmd_resolve():
    """统一引用解析: 双链 / omni URI / 裸 id → 真源位置 + 自检。

    用法:
      omni resolve <引用> [--verify] [--json]      解析一个引用
      omni resolve rebuild-index [--incremental]   重建材料索引
    """


@cmd_resolve.command("ref")
@click.argument("reference")
@click.option("--verify", is_flag=True, default=False, help="跑回指自检(真源存在性+廉价指纹), 失真显式报")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
@click.pass_context
def cmd_resolve_ref(ctx: click.Context, reference: str, verify: bool, as_json: bool):
    """解析一个引用(直接 `omni resolve <引用>` 亦可, 会自动路由到这里)。"""
    from omnicompany.packages.services._core.registry.resolver import resolve_reference

    result = resolve_reference(reference, verify=verify)

    if as_json:
        click.echo(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        ctx.exit(0 if result.exists else 1)

    # 人读输出
    if result.error and not result.exists:
        click.echo(click.style(f"✗ 无法解析: {reference}", fg="red", bold=True))
        click.echo(click.style(f"  kind:     {result.kind}", fg="bright_black"))
        click.echo(click.style(f"  id:       {result.id}", fg="bright_black"))
        click.echo(click.style(f"  原因:     {result.error}", fg="red"))
        if result.location:
            click.echo(click.style(f"  查询位置: {result.location}", fg="bright_black"))
        ctx.exit(1)

    click.echo(click.style(f"✓ {result.kind}", fg="green", bold=True) + f"  {result.id}")
    click.echo(f"  真源位置: {result.location}")
    click.echo(f"  适配器:   {result.resolver}")
    if result.version:
        click.echo(f"  版本:     {result.version}")
    if result.anchor:
        click.echo(f"  锚点:     {result.anchor}")
    for k, v in (result.meta or {}).items():
        click.echo(click.style(f"    {k}: {v}", fg="bright_black"))

    if verify:
        if result.verified:
            click.echo(click.style(f"  回指自检: ✓ {result.verify_note}", fg="green"))
        else:
            click.echo(click.style(f"  回指自检: ✗ 失真 — {result.verify_note}", fg="red", bold=True))
            ctx.exit(2)
    ctx.exit(0)


@cmd_resolve.command("rebuild-index")
@click.option("--incremental", is_flag=True, default=False,
              help="增量刷新(只重扫比索引新的文件); 默认全量重建")
@click.option("--scope", "scopes_str", type=str, default=None,
              help="逗号分隔扫描根目录(默认 src/omnicompany + templates + docs)")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
def cmd_resolve_rebuild_index(incremental: bool, scopes_str: str | None, as_json: bool):
    """重建 / 增量刷新 material 适配器的底层 MaterialIdIndex。"""
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.packages.services._core.registry.material_index import (
        get_material_id_index,
    )

    project_root = omni_workspace_root()
    if scopes_str:
        scopes = [Path(s.strip()) for s in scopes_str.split(",") if s.strip()]
        scopes = [s if s.is_absolute() else (project_root / s) for s in scopes]
    else:
        scopes = [
            project_root / "src" / "omnicompany",
            project_root / "templates",
            project_root / "docs",
        ]
        scopes = [s for s in scopes if s.exists()]

    index = get_material_id_index()
    click.echo(click.style(
        f"{'增量刷新' if incremental else '全量重建'} material_id 索引 ({len(scopes)} 个根目录)...",
        fg="cyan",
    ))
    for s in scopes:
        click.echo(click.style(f"  - {s}", fg="bright_black"))

    if incremental:
        result = index.refresh_incremental(scopes, project_root)
    else:
        result = index.rebuild_from_headers(scopes, project_root)

    if as_json:
        click.echo(_json.dumps(result, ensure_ascii=False, indent=2))
        return

    click.echo()
    click.echo(click.style("─── 索引写入完成 ───", fg="green", bold=True))
    if incremental:
        click.echo(f"  模式:     {result.get('mode')}")
        click.echo(f"  重扫文件: {result.get('total_rescanned')}")
        click.echo(click.style(f"  更新条目: {result.get('entries_updated')}", fg="green"))
        click.echo(f"  索引总量: {result.get('entries_total')}")
    else:
        click.echo(f"  扫描文件:       {result['total_scanned']}")
        click.echo(f"  含 material_id:  {result['total_with_material_id']}")
        click.echo(click.style(f"  写入索引:        {result['entries_written']}", fg="green"))
    click.echo(f"  索引位置:        {index.index_path}")

    conflicts = result.get("conflicts") or []
    if conflicts:
        click.echo()
        click.echo(click.style(f"⚠ {len(conflicts)} 条 material_id 冲突(同 id 多文件):", fg="red", bold=True))
        for c in conflicts[:10]:
            click.echo(click.style(f"  {c['material_id']}", fg="red"))
            for f in c["files"]:
                click.echo(click.style(f"    - {f}", fg="bright_black"))


__all__ = ["cmd_resolve"]
