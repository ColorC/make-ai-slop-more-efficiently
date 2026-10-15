# [OMNI] origin=qoder ts=2026-08-17 type=cli
# [OMNI] material_id="material:cli.commands.convflow"
"""omni convflow — 惯例性工作流（conventional workflow）命令面。

与 demogame-auto-config 的 change 命令面同构：preview 纯求值零副作用，run 显式执行，
写入只存在于显式 commit-effects；gate 显式裁决。spec 是 JSON 文件（WorkflowSpec，
契约见 webworks @omni/material-core workflow.js 与 packages/services/_core/conventional_workflow）。
命名 convflow 是因为 `omni workflow` 已被多-agent 编排占用。
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from ...packages.services._core.conventional_workflow import (
    GatePending,
    WorkflowRunner,
    WorkerRegistry,
    load_spec,
)
from ...packages.services._core.conventional_workflow.runtime import Verdict, WorkerOutput

_RUNS_DIR = Path.home() / ".omni" / "conventional-workflow" / "runs"


def _demo_registry() -> WorkerRegistry:
    """Default local registry; external adapters are opt-in and never imported here."""
    registry = WorkerRegistry()

    def generate(context):
        return WorkerOutput(
            verdict=Verdict("PASS", note="dry-run generation"),
            outputs={"tables": [context.params.get("table", "BattlePass")]},
            effects=[{"op": "write-cell", "table": context.params.get("table", "BattlePass"), "dry_run": context.dry_run}],
        )

    def benchmark(context):
        return Verdict("PASS", note="demo benchmark stub")

    registry.register_worker("spark.config.change", generate)
    registry.register_worker("demo.generate", generate)
    registry.register_check("spark.config.benchmark", benchmark)
    registry.register_check("demo.check", benchmark)
    return registry


def _load_runner(spec_path: Path) -> WorkflowRunner:
    spec = load_spec(json.loads(spec_path.read_text(encoding="utf-8")))
    return WorkflowRunner(spec, _demo_registry(), runs_dir=_RUNS_DIR)


@click.group(name="convflow")
def cmd_convflow() -> None:
    """惯例性工作流：preview/run/status/gate/commit-effects（demogame change 面同构）。"""


@cmd_convflow.command("preview")
@click.argument("spec_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def _preview(spec_path: Path) -> None:
    """纯求值展开执行计划（模板层环 → run 层 DAG），零副作用。"""
    plan = _load_runner(spec_path).preview()
    click.echo(json.dumps(plan, ensure_ascii=False, indent=2))


@cmd_convflow.command("run")
@click.argument("spec_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--inputs", default="{}", help="JSON 输入")
@click.option("--live", is_flag=True, help="默认 dry-run；加 --live 才允许 worker 声明真实写入意图")
def _run(spec_path: Path, inputs: str, live: bool) -> None:
    try:
        parsed_inputs = json.loads(inputs)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"--inputs 不是合法 JSON: {exc}")
    try:
        runner = _load_runner(spec_path)
    except (ValueError, OSError) as exc:
        raise click.ClickException(f"spec 加载失败: {exc}")
    try:
        state = runner.run(inputs=parsed_inputs, dry_run=not live, spec_path=str(spec_path.resolve()))
    except GatePending as exc:
        raise click.ClickException(f"{exc}（用 convflow gate 裁决后续跑）")
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc))
    click.echo(json.dumps({"run_id": state["run_id"], "status": state["status"], "transactions": state["transactions"]}, ensure_ascii=False, indent=2))


@cmd_convflow.command("status")
@click.argument("run_id")
def _status(run_id: str) -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner.runs_dir = _RUNS_DIR
    click.echo(json.dumps(runner.load(run_id), ensure_ascii=False, indent=2))


def _state_runner(run_id: str) -> WorkflowRunner:
    bare = WorkflowRunner.__new__(WorkflowRunner)
    bare.runs_dir = _RUNS_DIR
    try:
        state = bare.load(run_id)
    except FileNotFoundError:
        return bare
    spec_path = state.get("spec_path")
    if spec_path and Path(spec_path).exists():
        return _load_runner(Path(spec_path))
    return bare


@cmd_convflow.command("gate")
@click.argument("run_id")
@click.argument("copy_id")
@click.option("--decision", type=click.Choice(["pass", "reject"]), required=True)
def _gate(run_id: str, copy_id: str, decision: str) -> None:
    """显式裁决确认门（pass 续跑下游；审批通过≠自动写入）。"""
    runner = _state_runner(run_id)
    try:
        state = runner.approve_gate(run_id, copy_id, decision)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc))
    click.echo(json.dumps({"gate": state["gates"][copy_id], "status": state["status"]}, ensure_ascii=False))


@cmd_convflow.command("commit-effects")
@click.argument("run_id")
@click.argument("tx_id")
def _commit(run_id: str, tx_id: str) -> None:
    """显式提交副作用事务——唯一写入入口（需门 pass 且无未覆盖 FAIL；演示桩只记录不落盘）。"""
    runner = _state_runner(run_id)
    try:
        tx = runner.commit_effects(run_id, tx_id)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc))
    click.echo(json.dumps(tx, ensure_ascii=False, indent=2))
