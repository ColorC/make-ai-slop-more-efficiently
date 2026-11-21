# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-02T08:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="omni team 命令组 - load / validate / show yaml team. run 走现有 omni run + pipeline 注册"
# [OMNI] why="跟 G2 注册中心 + team_loader yaml 联动. 用户原始需求 6.3.1: team 暂时纯配置, 之后再看. 这层让 yaml team 可看 / 可验证"
# [OMNI] tags=cli,team,yaml,validate,show
# [OMNI] material_id="material:cli.commands.yaml_team_loader_and_runner.implementation.py"
"""omni team 命令组.

`omni team validate --from-yaml=<>` — 验证 yaml 文件能加载成合法 TeamSpec
`omni team show --from-yaml=<>` — 可视化 team 拓扑 (nodes / edges / 入口)
`omni team load --from-yaml=<>` — 加载 + 注册到 InstanceRegistry (供后续 omni run 用)

真跑 (yaml team → TeamRunner 执行) 需要 worker class 解析 + bindings 实例化, 工作量大,
暂走"加载验证 + 注册" 路线. 真跑通过 omni run <pipeline_name> 走已注册管线.
"""
from __future__ import annotations

import json
from pathlib import Path

import click


@click.group("team")
def cmd_team() -> None:
    """omni team yaml 命令组."""


@cmd_team.command("validate")
@click.option("--from-yaml", required=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False),
              help="team yaml 路径")
@click.option("--json", "as_json", is_flag=True)
def cmd_team_validate(from_yaml: str, as_json: bool) -> None:
    """验证 yaml 可加载成合法 TeamSpec."""
    from omnicompany.packages.services._core.team_loader import load_team_from_yaml
    try:
        team = load_team_from_yaml(from_yaml)
    except Exception as e:
        if as_json:
            click.echo(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            click.echo(f"FAIL · 加载失败: {e}", err=True)
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps({
            "ok": True, "id": team.id, "name": team.name,
            "entry": team.entry, "nodes": len(team.nodes), "edges": len(team.edges),
        }, ensure_ascii=False, indent=2))
    else:
        click.echo("PASS · yaml 合法")
        click.echo(f"  id={team.id} name={team.name}")
        click.echo(f"  entry={team.entry}  nodes={len(team.nodes)}  edges={len(team.edges)}")


@cmd_team.command("show")
@click.option("--from-yaml", required=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False))
def cmd_team_show(from_yaml: str) -> None:
    """可视化 yaml team 的拓扑 (nodes + edges)."""
    from omnicompany.packages.services._core.team_loader import load_team_from_yaml
    try:
        team = load_team_from_yaml(from_yaml)
    except Exception as e:
        click.echo(f"加载失败: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"Team: {team.id}")
    click.echo(f"  name        : {team.name}")
    click.echo(f"  description : {team.description}")
    click.echo(f"  entry       : {team.entry}")
    click.echo()
    click.echo(f"Nodes ({len(team.nodes)}):")
    for n in team.nodes:
        if n.anchor:
            fin = n.anchor.format_in
            fout = n.anchor.format_out
            click.echo(f"  - {n.id:30s} [{n.kind.value}/{n.maturity.value}]  {fin} -> {fout}")
        else:
            click.echo(f"  - {n.id:30s} [{n.kind.value}/{n.maturity.value}]")
    click.echo()
    click.echo(f"Edges ({len(team.edges)}):")
    for e in team.edges:
        cond = e.condition.value if e.condition else "always"
        click.echo(f"  - {e.source} --[{cond}]--> {e.target}")
    if team.tags:
        click.echo()
        click.echo(f"Tags: {team.tags}")


@cmd_team.command("run")
@click.option("--from-yaml", required=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False),
              help="team yaml 路径")
@click.option("--bindings-callable", required=True,
              help="返回 bindings dict 的 callable, 形 'module.path:func_name', 例 'my_pkg.workers:get_bindings'. "
                   "func 不接参数, 返回 dict[node_id -> Router 实例].")
@click.option("--input", "-i", "inputs", multiple=True,
              help="key=value 形式的输入参数")
@click.option("--json-input", "-j", default=None, help="JSON 格式的完整输入字典")
@click.option("--max-steps", type=int, default=50)
def cmd_team_run(
    from_yaml: str, bindings_callable: str,
    inputs: tuple, json_input: str | None, max_steps: int,
) -> None:
    """跑 yaml team (用户原始需求 6.3.1: team 暂时纯配置).

    跑前提:
      - yaml 合法 (`omni team validate` 已通过)
      - bindings_callable 返回 dict[node_id -> Router 实例] 给所有 anchor 节点
      - EventBus 自动建 (in-memory)

    bindings 自定义示例:
        # my_pkg/workers.py
        def get_bindings():
            from .csv_reader import CsvReaderRouter
            from .markdown_writer import MarkdownWriterRouter
            return {
                'csv_reader': CsvReaderRouter(...),
                'markdown_writer': MarkdownWriterRouter(...),
            }

        # 跑:
        omni team run --from-yaml=foo.yaml --bindings-callable=my_pkg.workers:get_bindings -i path=foo.csv
    """
    import asyncio
    import importlib
    import json as _json

    from omnicompany.packages.services._core.team_loader import load_team_from_yaml

    try:
        team = load_team_from_yaml(from_yaml)
    except Exception as e:
        click.echo(f"yaml 加载失败: {e}", err=True)
        raise SystemExit(1)

    # 解析 bindings_callable
    try:
        module_path, func_name = bindings_callable.rsplit(":", 1)
        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        bindings = func()
    except Exception as e:
        click.echo(f"bindings-callable 解析失败 ({bindings_callable}): {e}", err=True)
        raise SystemExit(1)

    if not isinstance(bindings, dict):
        click.echo(f"bindings 应是 dict[node_id -> Router], 实得 {type(bindings).__name__}", err=True)
        raise SystemExit(1)

    # 校验 anchor 节点都有 binding
    anchor_node_ids = [n.id for n in team.nodes if n.anchor]
    missing = [nid for nid in anchor_node_ids if nid not in bindings]
    if missing:
        click.echo(f"bindings 缺以下 anchor 节点 binding: {missing}", err=True)
        raise SystemExit(1)

    # 构建输入
    input_dict: dict = {}
    if json_input:
        try:
            input_dict = _json.loads(json_input)
        except _json.JSONDecodeError as e:
            click.echo(f"--json-input 解析失败: {e}", err=True)
            raise SystemExit(1)
    for kv in inputs:
        if "=" not in kv:
            click.echo(f"--input 形式错: {kv} (应是 key=value)", err=True)
            raise SystemExit(1)
        k, v = kv.split("=", 1)
        input_dict[k] = v

    # 创建 EventBus + TeamRunner
    try:
        from omnicompany.bus.memory import MemoryBus
        from omnicompany.runtime.exec.runner import TeamRunner
    except ImportError as e:
        click.echo(f"runtime 模块导入失败: {e}", err=True)
        raise SystemExit(1)

    bus = MemoryBus()
    runner = TeamRunner(
        pipeline=team, bindings=bindings, bus=bus,
        max_steps=max_steps, source=f"yaml:{Path(from_yaml).name}",
    )

    click.echo(f"开始跑 yaml team {team.id} (entry={team.entry}, nodes={len(team.nodes)})")
    try:
        result = asyncio.run(runner.run(input_dict))
        click.echo(f"OK 跑完, 结果: {result}")
    except Exception as e:
        click.echo(f"FAIL 跑挂: {type(e).__name__}: {e}", err=True)
        raise SystemExit(1)


@cmd_team.command("load")
@click.option("--from-yaml", required=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option("--name", default=None, help="注册名 (省略时用 team.id)")
@click.option("--package", default=None, help="package 路径 (省略时从 yaml 路径推断)")
def cmd_team_load(from_yaml: str, name: str | None, package: str | None) -> None:
    """加载 yaml team + 注册到 G2 中心 (用 omni register --kind=team 同源).

    注册后:
      omni lookup --kind=team --id=<id>
      omni team show --from-yaml=<path>
    """
    import subprocess
    import sys
    cmd = [sys.executable, "-m", "omnicompany.cli.main", "register",
           "--kind", "team", "--content", from_yaml]
    if name:
        cmd.extend(["--name", name])
    if package:
        cmd.extend(["--package", package])
    r = subprocess.run(cmd, capture_output=True, text=True)
    click.echo(r.stdout)
    if r.returncode != 0:
        click.echo(r.stderr, err=True)
        raise SystemExit(1)
