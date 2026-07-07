# [OMNI] origin=claude-code domain=omnicompany/cli ts=2026-07-04T00:00:00+08:00 type=cli status=active agent=claude
# [OMNI] summary="omni plan-complete-gate PLAN_ID —— 计划完成硬闸查询面。allow→exit 0; refuse→exit 2(打印 reason); 内部错误→exit 1。消费方=whatnow patch_task 的 run_cli 胶水(--json), 人也可直接查。"
# [OMNI] why="批3 §A: 完成流转拒绝要能从外部进程(Rust run_cli)按退出码判定, 同时保留可读 reason 文案供人查。只读, 无副作用。"
# [OMNI] tags=cli,gate,whatnow,plan-bindings,testmap
"""omni plan-complete-gate —— 计划完成硬闸查询面(只读)。"""
from __future__ import annotations

import json

import click

from .._access import any_caller


@click.command("plan-complete-gate")
@any_caller
@click.argument("plan_id")
@click.option("--json", "as_json", is_flag=True, help="输出结构化 JSON(供 whatnow run_cli 胶水消费)")
def cmd_plan_complete_gate(plan_id: str, as_json: bool) -> None:
    """判定 PLAN_ID 是否允许转完成态: allow(exit 0) / refuse(exit 2, 打印 reason)。

    内部错误(判定函数抛异常)→ exit 1。只读, 不修改绑定注册表/testmap 任何数据。
    """
    from omnicompany.packages.services._governance.plan_completion_gate import evaluate

    try:
        result = evaluate(plan_id)
    except Exception as e:  # noqa: BLE001 — 内部错误单独一档退出码(exit 1), 不能跟 refuse(exit 2) 混淆
        if as_json:
            click.echo(json.dumps({"allow": False, "error": str(e)}, ensure_ascii=False))
        else:
            click.echo(f"[plan-complete-gate] 内部错误: {e}", err=True)
        raise SystemExit(1) from e

    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mark = "ALLOW" if result["allow"] else "REFUSE"
        click.echo(f"[{mark}] {plan_id}: {result['reason']}")

    if not result["allow"]:
        raise SystemExit(2)


__all__ = ["cmd_plan_complete_gate"]
