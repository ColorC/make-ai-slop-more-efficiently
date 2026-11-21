# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-02T06:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="omni meta-io 命令组 - 元 IO 列表 / 详情 / 按 kind 过滤"
# [OMNI] why="跟元 IO 实施层 (services/_core/meta_io) 联动. 让用户/agent 看已注册元 IO + 找'读什么资源用哪个 meta_io'"
# [OMNI] tags=cli,meta_io,query
# [OMNI] material_id="material:cli.meta_io.registry_query.implementation.py"
"""omni meta-io 命令组."""
from __future__ import annotations

import json

import click

from omnicompany.packages.services._core.meta_io import list_meta_io, get_meta_io


@click.group("meta-io")
def cmd_meta_io() -> None:
    """元 IO 命令组 (omnicompany 用户原始需求 6.6).

    元 IO = tool 层 IO 操作的语义原子单位. 详见 docs/standards/cli/meta_io.md.
    """


@cmd_meta_io.command("list")
@click.option("--kind", type=click.Choice(["read", "write", "mutate"]), help="按 kind 过滤")
@click.option("--target-type", help="按 target_type 过滤 (file / api / db / process / network)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_meta_io_list(kind: str | None, target_type: str | None, as_json: bool) -> None:
    """列已注册元 IO."""
    items = list_meta_io(kind=kind, target_type=target_type)
    if as_json:
        out = []
        for m in items:
            out.append({
                "id": m.id, "kind": m.kind.value, "target_type": m.target_type,
                "side_effect_scope": m.side_effect_scope, "is_atomic_semantic": m.is_atomic_semantic,
                "description": m.description, "tags": list(m.tags),
            })
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return
    click.echo(f"已注册 {len(items)} 条元 IO:")
    for m in items:
        click.echo(f"  [{m.kind.value:6s}] {m.id:40s}  → {m.side_effect_scope}")


@cmd_meta_io.command("describe")
@click.argument("meta_io_id")
@click.option("--json", "as_json", is_flag=True)
def cmd_meta_io_describe(meta_io_id: str, as_json: bool) -> None:
    """看一条元 IO 的详情."""
    m = get_meta_io(meta_io_id)
    if m is None:
        click.echo(f"未找到: {meta_io_id}", err=True)
        raise SystemExit(1)
    if as_json:
        click.echo(json.dumps({
            "id": m.id, "kind": m.kind.value, "target_type": m.target_type,
            "side_effect_scope": m.side_effect_scope, "is_atomic_semantic": m.is_atomic_semantic,
            "description": m.description, "tags": list(m.tags),
            "state_check": {
                "precondition": m.state_check.precondition,
                "postcondition": m.state_check.postcondition,
                "invariant": m.state_check.invariant,
            },
        }, ensure_ascii=False, indent=2))
        return
    click.echo(f"id                 : {m.id}")
    click.echo(f"kind               : {m.kind.value}")
    click.echo(f"target_type        : {m.target_type}")
    click.echo(f"side_effect_scope  : {m.side_effect_scope}")
    click.echo(f"is_atomic_semantic : {m.is_atomic_semantic}")
    click.echo(f"tags               : {list(m.tags)}")
    click.echo("description        :")
    click.echo(f"  {m.description}")
    if m.state_check.precondition or m.state_check.postcondition or m.state_check.invariant:
        click.echo("state_check:")
        if m.state_check.precondition:
            click.echo(f"  precondition  : {m.state_check.precondition}")
        if m.state_check.postcondition:
            click.echo(f"  postcondition : {m.state_check.postcondition}")
        if m.state_check.invariant:
            click.echo(f"  invariant     : {m.state_check.invariant}")


@cmd_meta_io.command("check-state")
@click.option("--json", "as_json", is_flag=True)
def cmd_meta_io_check_state(as_json: bool) -> None:
    """跑 MetaIOStateCheckHook 一次, 检查最近元 IO 调用是否健康.

    检查项:
    1. 未注册元 IO 出现 (audit log 里有 meta_io_id 但 META_IO_REGISTRY 没注册)
    2. 高失败率元 IO (≥ 5 次调用 + 失败率 ≥ 50%)

    用户 / cron 主动调. 项目内没动态 hook dispatcher, 所以走 CLI 周期触发或 hook.
    """
    import asyncio
    from omnicompany.packages.services._core.meta_io import MetaIOStateCheckHook

    hook = MetaIOStateCheckHook()
    signals = asyncio.run(hook.poll(db_path="", round_num=hook.POLL_EVERY))

    if as_json:
        out = []
        for s in signals:
            out.append({
                "format": s.format, "text": s.text, "node_id": s.node_id,
                "meta": s.meta,
            })
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if not signals:
        click.echo("PASS · 元 IO 状态健康 (扫最近 100 条审计无问题)")
        return

    click.echo(f"找到 {len(signals)} 条问题:")
    for s in signals:
        kind = s.meta.get("kind", "?")
        sev = s.meta.get("severity", "?")
        click.echo(f"  [{kind}/{sev}] {s.text}")
