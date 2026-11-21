"""omni guardian dirs — read the directory semantic archive.

Standard: docs/standards/_global/directory_semantic_archive.md.  Everything here is read-only:
declarations are edited where they live (workspace.yaml, PROJECT_INDEX directory_contract,
batch manifests); these commands only explain, list, validate and reconcile them.
"""
from __future__ import annotations

import json

import click


@click.group("dirs")
def cmd_guardian_dirs() -> None:
    """目录语义档案：查一个路径是什么、列全部登记、校验登记、对账现实（只读）。"""


def _echo_json(data) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


@cmd_guardian_dirs.command("explain")
@click.argument("paths", nargs=-1, required=True)
@click.option("--host", default="local", show_default=True, help="远端机器名；本机写 local")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_dirs_explain(paths: tuple[str, ...], host: str, as_json: bool) -> None:
    """查路径属于谁、什么角色、谁写谁读、留多久、能不能删。"""
    from omnicompany.core.dir_archive import explain, load_archive

    archive = load_archive()
    answers = [explain(p, host=host, archive=archive) for p in paths]
    if as_json:
        _echo_json(answers)
        return
    for a in answers:
        click.echo(a["target"])
        if a.get("error"):
            click.echo(f"  错误：{a['error']}")
            continue
        if a["undeclared"]:
            click.echo("  未登记：按正式内容保护")
        else:
            click.echo(f"  角色：{a['role']}（{'正式' if a['formal'] else '临时'}）  归属：{a['owner'] or '-'}  状态：{a['status']}")
            click.echo(f"  登记：{a['declared_in']}")
            for label, key in (("产生者", "producer"), ("寿命", "lifecycle"), ("说明", "note")):
                if a.get(key):
                    click.echo(f"  {label}：{a[key]}")
            if a["consumers"]:
                click.echo("  消费者：" + "；".join(a["consumers"]))
        if a.get("batch"):
            click.echo(f"  批次清单：{a['batch']['file']}")
        if a.get("worktree"):
            wt = a["worktree"]
            click.echo(f"  worktree：{wt['root']}（管理目录 {wt['admin_dir']}，清单 {'有' if wt.get('copy_json') else '无'}）")
        for link in a["links"]:
            click.echo(f"  链接：{link}")
        verdict = a["delete"]
        if verdict["allowed"]:
            click.echo(f"  可删：{verdict['basis']}")
        else:
            for p in verdict["problems"]:
                click.echo(f"  不可删：{p}")


@cmd_guardian_dirs.command("list")
@click.option("--project", default=None, help="只列这个项目的登记")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_dirs_list(project: str | None, as_json: bool) -> None:
    """列出全部登记（档案的投影，随时从真源重算）。"""
    from dataclasses import asdict

    from omnicompany.core.dir_archive import load_archive

    archive = load_archive()
    rows = [asdict(d) for d in archive.declarations if project is None or d.owner == project]
    if as_json:
        _echo_json({"declarations": rows, "errors": archive.errors})
        return
    for r in rows:
        host = "" if r["host"] == "local" else f"[{r['host']}] "
        click.echo(f"{host}{r['path']}  {r['role']}  {r['owner'] or '-'}  ({r['layer']})")
    click.echo(f"合计 {len(rows)} 条登记；读取错误 {len(archive.errors)} 条（omni guardian dirs check 查看）")


@cmd_guardian_dirs.command("check")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_dirs_check(as_json: bool) -> None:
    """校验 workspace.yaml 与各项目目录契约；有错误时退出码 2。"""
    from omnicompany.core.dir_archive import load_archive

    archive = load_archive()
    if as_json:
        _echo_json({"errors": archive.errors, "declarations": len(archive.declarations)})
    else:
        for e in archive.errors:
            where = f"{e.get('source')}" + (f" :: {e['path']}" if e.get("path") else "")
            click.echo(f"{e.get('code')}：{e.get('message')}  [{where}]")
        click.echo(f"{len(archive.declarations)} 条登记，{len(archive.errors)} 个错误")
    if archive.errors:
        raise SystemExit(2)


@cmd_guardian_dirs.command("drift")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_dirs_drift(as_json: bool) -> None:
    """对账：未登记目录、登记了却不存在的路径、到期未清的批次、没有清单的批次（只看一层，不跟随链接）。"""
    from omnicompany.core.dir_archive import drift, load_archive

    report = drift(load_archive())
    if as_json:
        _echo_json(report)
        return
    labels = {"undeclared": "未登记", "declared-missing": "登记了但不存在", "expired-unreaped": "到期未清",
              "no-manifest": "批次无清单"}
    counts: dict[str, int] = {}
    for f in report["findings"]:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
        extra = f"  （{f.get('scope') or f.get('declared_in') or f.get('basis') or ''}）"
        click.echo(f"{labels.get(f['kind'], f['kind'])}：{f['path']}{extra}")
    click.echo("合计：" + ("，".join(f"{labels.get(k, k)} {v}" for k, v in counts.items()) or "无发现"))
    click.echo(f"没有目录契约的项目 {len(report['projects_without_contract'])} 个：" + "、".join(report["projects_without_contract"]))
    tracked = report.get("tracked_unlabelled") or {}
    click.echo(f"项目根第一层由 Git 跟踪、未细分登记的目录 {sum(tracked.values())} 个（按正式内容处理，不列出）")
    regenerable = report.get("regenerable_unlabelled") or {}
    click.echo(f"项目根第一层依赖/构建/缓存名字的未登记目录 {sum(regenerable.values())} 个（只计数；删除依据不变）")
    click.echo(f"契约约定了位置、尚未创建的目录 {len(report.get('declared_not_created') or [])} 个（--json 列出）")
    click.echo(f"登记读取错误 {len(report['errors'])} 个（omni guardian dirs check 查看）")
    click.echo(f"范围：{report['scope']}")
