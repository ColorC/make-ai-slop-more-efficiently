# [OMNI] origin=ai-ide ts=2026-06-06 type=cli
"""omni workflow — 确定性多-agent 编排 (fan-out → 收集 → 综合)。给总控用。

总控原来只能 `omni worker spawn` 单发, 编排靠 LLM 逐轮 ad-hoc。本命令组让总控一条命令
确定性地铺开 K 个 subagent (一任务一个), 全部完成后自动 spawn 一个综合 subagent 读取
fan-out 产物汇总。非阻塞: run 立刻返回 wf_id, 后续靠 ccdaemon 事件推进, `omni workflow
status <wf_id>` 看进度。

- run    : 起一个 workflow (--plan + 多个 --task [+ --synthesize])。
- status : 看某 workflow 的 fan-out/综合进度 + 各子任务的 subagent / 审阅台材料。
- list   : 列所有 workflow。
"""
from __future__ import annotations

import json
import urllib.request

import click

from .._access import any_caller, external_or_controller


def _daemon_base() -> str:
    from omnicompany.dashboard.ccdaemon.api import lifecycle

    s = lifecycle.read_status()
    if not (getattr(s, "alive", False) and getattr(s, "port", None)):
        raise click.ClickException("ccdaemon 未运行(先 `omni cc daemon start`)")
    return f"http://127.0.0.1:{s.port}"


def _http(method: str, url: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 本机 daemon
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        try:
            detail = json.loads(e.read().decode()).get("detail")
        except Exception:  # noqa: BLE001
            detail = str(e)
        raise click.ClickException(f"HTTP {e.code}: {detail}") from e


def _print_wf(wf: dict, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(wf, ensure_ascii=False, indent=2))
        return
    click.echo(
        f"  [{wf.get('status'):12}] {wf.get('id')}  {wf.get('title')}  "
        f"(plan={wf.get('plan_id')}; fan-out {wf.get('fanout_done')}/{wf.get('fanout_total')}"
        f"{'; synth' if wf.get('synth_spawned') else ''})"
    )
    for t in wf.get("tasks") or []:
        mats = ",".join(t.get("material_ids") or []) or "-"
        click.echo(
            f"      · {t.get('role'):10} {t.get('status'):8} subagent={t.get('subagent_id') or '-'} 材料={mats}"
        )
        prompt = (t.get("prompt") or "").replace("\n", " ")
        if prompt:
            click.echo(f"        {prompt[:90]}")


@click.group("workflow")
def cmd_workflow() -> None:
    """确定性多-agent 编排 (fan-out → 收集 → 综合)。给总控用。"""


@cmd_workflow.command("run")
@click.option("--plan", "plan_id", required=True, help="关联 plan id (必填)")
@click.option("--task", "tasks", multiple=True, required=True, help="一个 fan-out 子任务 prompt (可多次, 一任务一 subagent)")
@click.option("--title", default="", help="workflow 名字")
@click.option("--synthesize", default=None, help="可选: fan-out 全完成后综合阶段的 prompt; 不填=不综合")
@click.option("--provider", default="claude_code", type=click.Choice(["claude_code", "codex", "omni_agent"]))
@click.option("--model", default=None, help="模型短名(可选)")
@click.option("--cwd", default=None, help="工作目录(默认仓库根)")
@click.option("--json", "as_json", is_flag=True)
@external_or_controller
def cmd_workflow_run(plan_id, tasks, title, synthesize, provider, model, cwd, as_json) -> None:
    """起一个 workflow: --plan + 多个 --task [+ --synthesize]。非阻塞, 立刻返回 wf_id。"""
    base = _daemon_base()
    body = {
        "plan_id": plan_id,
        "tasks": list(tasks),
        "title": title,
        "synthesize": synthesize,
        "provider": provider,
        "model": model,
        "cwd": cwd,
    }
    wf = _http("POST", f"{base}/cc/workflow/run", body, timeout=120)
    if as_json:
        click.echo(json.dumps(wf, ensure_ascii=False, indent=2))
    else:
        click.echo(f"workflow 已起: {wf.get('id')} ({wf.get('fanout_total')} 个 fan-out 子任务"
                   f"{', 含综合' if wf.get('has_synthesize') else ''})")
        _print_wf(wf, False)
        click.echo(f"\n用 `omni workflow status {wf.get('id')}` 看进度。")


@cmd_workflow.command("status")
@click.argument("wf_id")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_workflow_status(wf_id, as_json) -> None:
    """看某 workflow 的 fan-out/综合进度。"""
    base = _daemon_base()
    wf = _http("GET", f"{base}/cc/workflow/{wf_id}")
    _print_wf(wf, as_json)


@cmd_workflow.command("list")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_workflow_list(as_json) -> None:
    """列所有 workflow。"""
    base = _daemon_base()
    d = _http("GET", f"{base}/cc/workflow")
    items = d.get("items") or []
    if as_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("(无 workflow)")
        return
    for wf in items:
        _print_wf(wf, False)


__all__ = ["cmd_workflow"]
