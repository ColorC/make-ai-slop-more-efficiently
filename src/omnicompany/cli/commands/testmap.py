# [OMNI] origin=claude-code domain=omnicompany/cli ts=2026-07-03T23:50:00+08:00 type=cli status=active
# [OMNI] summary="omni testmap —— 功能点-测试台账面: list/show/verify/gaps(只读查询, show/list 顶部/行尾带 OMNI-100 提醒与门禁红展示)+ sync(注册表接线, attrs 增写 reminders)+ gates-run(门禁真跑)+ review(理论覆盖再评, run_json_agent 性价比模型)。"
# [OMNI] why="用户 2026-07-03: 缺乏统一的功能点和对应测试管理设施, 需要能集中查询各仓 testmap.yaml 覆盖情况的 CLI, 首要消费方是 AI。巡检批补齐注册表接线+门禁真跑+LLM 再评三件; 标准位置留痕批(2026-07-04)补 OMNI-100 提醒读侧派生展示(上位 plan.md「分批与验收锚」)。"
# [OMNI] tags=cli,testmap,governance,test-ledger
"""omni testmap —— 功能点-测试台账面(list/show/verify/gaps/sync/gates-run/review)。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import click

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.agent.launch import run_json_agent
from omnicompany.packages.services._core.registry import get_registry
from omnicompany.packages.services._core.registry.instance import InstanceEntry
from omnicompany.packages.services._governance import testmap as testmap_lib


def _relpath(path, root) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _discover():
    root = omni_workspace_root()
    return root, testmap_lib.discover_testmaps(root)


def _registry_entry(app: str):
    """读注册表该 app 条目, 读不到返回 None(不报错 —— sync 没跑过是正常态)。

    显式传 <workspace_root>/data/services/registry(与 testmap_lib.sync_registry /
    run_gates 写入时用的路径一致) —— get_registry() 裸调的包相对默认目录与此不同,
    裸调会读到空注册表(见 _core/registry/__init__.py:_DEFAULT_REGISTRY_DIR)。
    """
    try:
        root = omni_workspace_root()
        registry = get_registry(root / "data" / "services" / "registry")
        return registry.read(f"testmap:{app}")
    except Exception:  # noqa: BLE001 — 注册表不可用不阻断查询面
        return None


def _red_gates(app: str) -> list[dict]:
    """注册表条目 attrs.gates 里 status=red 的门, 读不到条目/无 gates 键返回空列表。"""
    entry = _registry_entry(app)
    if entry is None:
        return []
    gates = (entry.attrs or {}).get("gates") or {}
    if not isinstance(gates, dict):
        return []
    reds = []
    for gate_id, g in gates.items():
        if isinstance(g, dict) and g.get("status") == "red":
            reds.append({
                "gate_id": gate_id,
                "ran_at": g.get("ran_at", ""),
                "log_path": g.get("log_path", ""),
            })
    return reds


@click.group("testmap")
def cmd_testmap() -> None:
    """功能点-测试台账: list/show/verify/gaps(真源跟随各业务仓, 本组只查询)。"""


@cmd_testmap.command("list")
@click.option("--json", "as_json", is_flag=True)
def testmap_list(as_json: bool) -> None:
    """列出全部已发现的 testmap: app、路径、功能点数、covered/gap/stale 计数。"""
    root, result = _discover()
    rows = []
    for tm in result.testmaps:
        findings = testmap_lib.verify_testmap(tm)
        status = testmap_lib.feature_status(tm, findings)
        counts = {"covered": 0, "gap": 0, "stale": 0}
        for s in status.values():
            counts[s] = counts.get(s, 0) + 1
        reminders = testmap_lib.collect_reminders(tm, root)
        red_gates = _red_gates(tm.app)
        rows.append({
            "app": tm.app,
            "path": _relpath(tm.path, root),
            "features": len(tm.features),
            "covered": counts["covered"],
            "gap": counts["gap"],
            "stale": counts["stale"],
            "reminders": len(reminders),
            "red_gates": [g["gate_id"] for g in red_gates],
        })

    if as_json:
        click.echo(json.dumps({
            "items": rows, "total": len(rows),
            "errors": result.errors, "rejected": result.rejected,
        }, ensure_ascii=False, indent=2))
        return

    if not rows:
        click.echo("未发现任何 testmap。")
    for r in rows:
        suffix = ""
        if r["reminders"]:
            suffix += f" reminders={r['reminders']}"
        if r["red_gates"]:
            suffix += " gates:" + ",".join(f"{g}=red" for g in r["red_gates"])
        click.echo(
            f"  {r['app']:<28} {r['path']:<60} "
            f"features={r['features']} covered={r['covered']} gap={r['gap']} stale={r['stale']}{suffix}"
        )
    click.echo(f"\n共 {len(rows)} 份 testmap")
    if result.errors:
        click.echo(f"\n加载失败({len(result.errors)}):")
        for e in result.errors:
            click.echo(f"  ✗ {e['path']}: {e['reason']}")
    if result.rejected:
        click.echo(f"\napp 标识冲突被拒绝({len(result.rejected)}):")
        for r in result.rejected:
            click.echo(f"  ✗ {r['path']}: {r['reason']}")


@cmd_testmap.command("show")
@click.argument("app")
@click.option("--json", "as_json", is_flag=True)
def testmap_show(app: str, as_json: bool) -> None:
    """显示某个 app 的全部功能点明细(id/what/should/tests 锚/实测 status)。"""
    root, result = _discover()
    tm = next((t for t in result.testmaps if t.app == app), None)
    if tm is None:
        raise click.UsageError(f"无此 app: {app}(omni testmap list 看全部已发现的 testmap)")

    findings = testmap_lib.verify_testmap(tm)
    status = testmap_lib.feature_status(tm, findings)
    reminders = testmap_lib.collect_reminders(tm, root)
    red_gates = _red_gates(tm.app)

    if as_json:
        payload = {
            "app": tm.app,
            "doc": tm.doc,
            "gates": [{"id": g.id, "cmd": g.cmd, "cwd": g.cwd} for g in tm.gates],
            "features": [{
                "id": f.id, "what": f.what, "should": f.should,
                "tests": [{"file": t.file, "cases": t.cases} for t in f.tests],
                "status": status.get(f.id),
            } for f in tm.features],
            "reminders": reminders,
            "red_gates": red_gates,
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    click.echo(f"app: {tm.app}")
    if reminders:
        latest = reminders[0]
        click.echo(
            f"⚠ 未消化提醒 {len(reminders)} 条(最近: {latest['detected_at']} "
            f"{latest['path']} — 源码改了但台账未更新)"
        )
    for rg in red_gates:
        click.echo(f"⚠ 门禁红: {rg['gate_id']}({rg['ran_at']}, 日志 {rg['log_path']})")
    if tm.doc:
        click.echo(f"doc: {tm.doc}")
    for g in tm.gates:
        click.echo(f"gate[{g.id}]: cwd={g.cwd} cmd={g.cmd}")
    click.echo(f"\n功能点({len(tm.features)}):")
    for f in tm.features:
        click.echo(f"  [{status.get(f.id)}] {f.id} — {f.what}")
        for s in f.should:
            click.echo(f"      should: {s}")
        for t in f.tests:
            click.echo(f"      tests: {t.file} cases={t.cases}")


@cmd_testmap.command("verify")
@click.argument("app", required=False)
@click.option("--json", "as_json", is_flag=True)
@click.option("--strict", is_flag=True, help="存在任何 finding/errors/rejected 时以非零退出")
def testmap_verify(app: str | None, as_json: bool, strict: bool) -> None:
    """跑 verify: 省略 app 则对全部已发现 testmap 核验锚有效性。"""
    _, result = _discover()
    targets = result.testmaps
    if app is not None:
        targets = [t for t in targets if t.app == app]
        if not targets:
            raise click.UsageError(f"无此 app: {app}(omni testmap list 看全部已发现的 testmap)")

    all_findings: list[dict] = []
    for tm in targets:
        all_findings.extend(testmap_lib.verify_testmap(tm))

    has_problem = bool(all_findings) or bool(result.errors) or bool(result.rejected)

    if as_json:
        click.echo(json.dumps({
            "findings": all_findings, "errors": result.errors, "rejected": result.rejected,
        }, ensure_ascii=False, indent=2))
    else:
        if not all_findings:
            click.echo("verify 通过, 无 finding。")
        for f in all_findings:
            click.echo(f"  [{f['kind']}] {f['app']}::{f['feature_id']} — {f['detail']}")
        if result.errors:
            click.echo(f"加载失败({len(result.errors)}): {result.errors}")
        if result.rejected:
            click.echo(f"app 冲突被拒绝({len(result.rejected)}): {result.rejected}")

    if strict and has_problem:
        raise SystemExit(1)


@cmd_testmap.command("gaps")
@click.option("--json", "as_json", is_flag=True)
def testmap_gaps(as_json: bool) -> None:
    """跨全部已发现 testmap, 列出 status != covered 的功能点。"""
    _, result = _discover()
    rows = []
    for tm in result.testmaps:
        findings = testmap_lib.verify_testmap(tm)
        status = testmap_lib.feature_status(tm, findings)
        for f in tm.features:
            s = status.get(f.id)
            if s != "covered":
                rows.append({"app": tm.app, "feature_id": f.id, "status": s, "what": f.what})

    if as_json:
        click.echo(json.dumps({"items": rows, "total": len(rows)}, ensure_ascii=False, indent=2))
        return

    if not rows:
        click.echo("无缺口: 全部功能点 covered。")
    for r in rows:
        click.echo(f"  [{r['status']}] {r['app']}::{r['feature_id']} — {r['what']}")
    click.echo(f"\n共 {len(rows)} 个缺口")


@cmd_testmap.command("sync")
@click.option("--json", "as_json", is_flag=True)
@click.option("--strict", is_flag=True, help="存在 gap/stale/errors/rejected 时以非零退出")
def testmap_sync(as_json: bool, strict: bool) -> None:
    """锚有效性巡检 + 注册表摘要回写: discover+verify 全量跑一遍, 写入 data/services/registry/testmap/。"""
    root = omni_workspace_root()
    summary = testmap_lib.sync_registry(root)

    total_gap = sum(c.get("gap", 0) for c in summary["counts"].values())
    total_stale = sum(c.get("stale", 0) for c in summary["counts"].values())
    has_problem = bool(total_gap or total_stale or summary["errors"] or summary["rejected"])

    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        click.echo(f"写入 {len(summary['written'])} 份 testmap 到注册表: {summary['written']}")
        for app, counts in summary["counts"].items():
            click.echo(f"  {app}: covered={counts['covered']} gap={counts['gap']} stale={counts['stale']}")
        if summary["errors"]:
            click.echo(f"加载失败({len(summary['errors'])}): {summary['errors']}")
        if summary["rejected"]:
            click.echo(f"app 冲突被拒绝({len(summary['rejected'])}): {summary['rejected']}")

    if strict and has_problem:
        raise SystemExit(1)


@cmd_testmap.command("gates-run")
@click.argument("app", required=False)
@click.option("--gate", "gate_id", default=None, help="只跑这一个 gate id(省略跑全部)")
@click.option("--timeout", "timeout_s", type=int, default=2400, show_default=True, help="每个门禁超时秒数")
@click.option("--json", "as_json", is_flag=True)
def testmap_gates_run(app: str | None, gate_id: str | None, timeout_s: int, as_json: bool) -> None:
    """门禁真跑: 在 testmap 目录下真执行 gate.cmd(隐藏窗口子进程), 红必须记红, 结果写回注册表。"""
    root = omni_workspace_root()
    result = testmap_lib.run_gates(app, gate_id, root, timeout_s=timeout_s)

    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if result.get("error"):
        click.echo(result["error"], err=True)
        raise SystemExit(1)

    any_red = False
    for app_name, gates in result.get("results", {}).items():
        click.echo(f"{app_name}:")
        if isinstance(gates, dict) and gates.get("error"):
            click.echo(f"  ✗ {gates['error']}")
            continue
        for gid, g in gates.items():
            mark = "✓" if g["status"] == "green" else "✗"
            if g["status"] != "green":
                any_red = True
            click.echo(f"  {mark} [{gid}] status={g['status']} exit_code={g['exit_code']} "
                       f"duration_s={g['duration_s']} log={g['log_path']}")
    if any_red:
        raise SystemExit(1)


@cmd_testmap.command("review")
@click.argument("app", required=False)
@click.option("--json", "as_json", is_flag=True)
def testmap_review(app: str | None, as_json: bool) -> None:
    """理论覆盖再评: 对目标 testmap 调一次 run_json_agent(gpt-5.5), 列证据不打分。"""
    root = omni_workspace_root()
    result = testmap_lib.discover_testmaps(root)
    targets = result.testmaps
    if app is not None:
        targets = [t for t in targets if t.app == app]
        if not targets:
            raise click.UsageError(f"无此 app: {app}(omni testmap list 看全部已发现的 testmap)")

    # 2026-07-04 修: 裸 get_registry() 走包相对默认目录(registry/__init__.py 的
    # parents[5] 解析到 src/data/, 不是仓根), 与 sync_registry/run_gates/_registry_entry
    # 写入用的 <root>/data/services/registry 对不上, 会读写到两份不同的注册表。
    # 显式传仓根路径, 与本文件 _registry_entry 同款。
    registry = get_registry(root / "data" / "services" / "registry")
    out_dir = root / "data" / "services" / "testmap" / "reviews"
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict] = {}
    had_error = False
    for tm in targets:
        task = testmap_lib.build_review_task(tm, root)
        agent_result = asyncio.run(run_json_agent(
            task=task,
            node_prompt=testmap_lib.REVIEW_NODE_PROMPT,
            model="gpt-5.5",
            result_schema=testmap_lib.REVIEW_RESULT_SCHEMA,
            project_root=str(root),
            caller="testmap.review",
        ))
        if not agent_result["ok"]:
            had_error = True
            reports[tm.app] = {"ok": False, "error": agent_result.get("error") or "run_json_agent 失败"}
            continue

        findings = (agent_result["final"] or {}).get("findings", [])
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        review_payload = {"app": tm.app, "ts": ts, "findings_count": len(findings), "findings": findings}
        report_path = out_dir / f"{tm.app}-{ts}.json"
        report_path.write_text(json.dumps(review_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        entity_id = f"testmap:{tm.app}"
        existing = registry.read(entity_id)
        existing_attrs = dict(existing.attrs) if existing is not None else {}
        attrs = {**existing_attrs, "review": {
            "ts": ts, "findings_count": len(findings), "findings": findings,
        }}
        registry.write(InstanceEntry(
            entity_id=entity_id, type="testmap", name=tm.app, package="",
            source_file=str(tm.path.resolve()), attrs=attrs,
        ))
        reports[tm.app] = {"ok": True, "findings_count": len(findings), "findings": findings,
                           "report_path": str(report_path)}

    if as_json:
        click.echo(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for app_name, r in reports.items():
            if not r["ok"]:
                click.echo(f"{app_name}: ✗ {r['error']}")
                continue
            click.echo(f"{app_name}: {r['findings_count']} 条 finding(落 {r['report_path']})")
            for f in r["findings"]:
                click.echo(f"  [{f.get('kind')}] {f.get('feature_id')}: {f.get('evidence')} — {f.get('detail')}")

    if had_error:
        raise SystemExit(1)


__all__ = ["cmd_testmap"]
