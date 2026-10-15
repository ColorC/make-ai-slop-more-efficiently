"""omni cleanup — verified deletion of temp directories and expired batches.

`omni cleanup rmtree <绝对路径>...` is the cleanup tool named by the workspace AGENTS.md rule on
recursive deletion; the PreToolUse safety hook points agents here when it refuses ``rm -r``.
`omni cleanup reap` deletes records/copies batches whose manifests have expired.
Logic and checks: omnicompany.core.verified_cleanup and omnicompany.core.dir_archive.
"""
from __future__ import annotations

import json

import click


@click.group("cleanup")
def cmd_cleanup() -> None:
    """核验后删除临时目录与到期批次（hook 拦下的递归删除改用这里）。"""


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


_LABELS = {"deleted": "已删除", "dry-run": "可删除（未删）", "refused": "拒绝", "incomplete": "未删干净", "kept": "保留"}


def _print(results: list[dict]) -> None:
    for r in results:
        click.echo(f"{_LABELS[r['outcome']]}：{r['target']}")
        if r["outcome"] in {"deleted", "dry-run"}:
            click.echo(f"  {r['files']} 个文件，{r['dirs']} 个目录，{_mb(r['bytes'])}，树内链接 {r['checks'].get('internal_links', 0)} 个")
            basis = (r["checks"].get("archive") or {}).get("basis")
            if basis:
                click.echo(f"  依据：{basis}")
        for p in r["problems"]:
            click.echo(f"  原因：{p}")


def _shared_process_rows():
    """One process snapshot per command: scanning every process's cwd takes about a minute here
    (993 processes, 58.6 s on 2026-10-12), so taking it once per target made a 10-target run time out."""
    from omnicompany.core.verified_cleanup import _process_rows

    snapshot: list[dict] | None = None

    def rows() -> list[dict]:
        nonlocal snapshot
        if snapshot is None:
            snapshot = list(_process_rows())
        return snapshot

    return rows


def _needs_archive(targets: tuple[str, ...]) -> bool:
    from pathlib import Path

    from omnicompany.core.verified_cleanup import tmp_zone

    return any(not t or tmp_zone(Path(t)) is None for t in targets)


@cmd_cleanup.command("rmtree")
@click.argument("targets", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, help="只核验并报告，不删除")
@click.option("--reason", default="", help="为什么删（写进回执）")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_cleanup_rmtree(targets: tuple[str, ...], dry_run: bool, reason: str, as_json: bool) -> None:
    """核验并删除临时目录：<项目>/.omni/tmp/<任务>/，或目录语义档案给出删除依据的目录。

    \b
    逐个目标核验：字面绝对路径；在 .omni/tmp 之下（批次自带的 record.json 不是未到期的人看证据），
    或档案给出删除依据（记录/副本根下清单已到期的批次；tmp、cache 角色里登记了寿命的批次）；
    祖先不是链接；树内链接只能指回树内（先删链接对象，不跟随）；没有另有硬链接的文件；
    没有 Git 跟踪文件、仓库或 worktree；没有其它进程的程序、命令行或工作目录在里面。
    全部通过才整棵删除；任何一项不过就不删，并写明原因。每次都在
    %LOCALAPPDATA%\\OmniCleanup\\receipts.jsonl 追加一行回执。
    查目标在档案里是什么：omni guardian dirs explain <路径>。
    """
    from omnicompany.core.verified_cleanup import remove

    archive = None
    if _needs_archive(targets):
        from omnicompany.core.dir_archive import load_archive

        archive = load_archive()
    rows = _shared_process_rows()
    results = [remove(t, dry_run=dry_run, reason=reason, archive=archive, process_rows=rows) for t in targets]
    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print(results)
        click.echo(f"回执：{results[-1]['receipt_log']}")
    if any(r["outcome"] in {"refused", "incomplete"} for r in results):
        raise SystemExit(2)


@cmd_cleanup.command("snapshot-repository")
@click.argument("target", required=True)
@click.option("--proof", "proof_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="唯一快照恢复证明JSON的绝对路径")
@click.option("--dry-run", is_flag=True, help="只核验证明与目标，不删除")
@click.option("--reason", default="", help="为什么收尾（写进回执）")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_cleanup_snapshot_repository(target: str, proof_path: str, dry_run: bool, reason: str,
                                    as_json: bool) -> None:
    """按唯一typed recovery proof收尾 .omni/tmp 内的独立Git snapshot。

    默认 rmtree 仍拒绝嵌套Git仓库。此入口要求已登记的非主父worktree、clean standalone
    clone、覆盖全树的ZIP恢复映射及验证过的原生Git bundle；任一证据不匹配即保留目标。
    """
    from omnicompany.core.verified_cleanup import remove_git_snapshot

    result = remove_git_snapshot(target, proof_path, dry_run=dry_run, reason=reason,
                                 process_rows=_shared_process_rows())
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        label = _LABELS.get(result["outcome"], result["outcome"])
        click.echo(f"{label}：{result['target']}")
        for problem in result["problems"]:
            click.echo(f"  原因：{problem}")
        click.echo(f"回执：{result['receipt_log']}")
    if result["outcome"] in {"refused", "incomplete"}:
        raise SystemExit(2)


@cmd_cleanup.command("worktree")
@click.argument("paths", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, help="只核验并打归档提交，不还原、不删除、不移除")
@click.option("--reason", default="", help="为什么收尾（写进归档提交与回执）")
@click.option("--skip-archive-prefix", "skip_prefixes", multiple=True,
              help="不归档的相对路径前缀（必须已另行核实没有独有内容，例如与某分支逐文件一致的拷贝；可多次）")
@click.option("--unlink-link-to", "unlink_roots", multiple=True,
              help="目标落在这个根下的树外链接只解除链接对象（目标不动），例如发布树借用的正式内容目录；可多次")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_cleanup_worktree(paths: tuple[str, ...], dry_run: bool, reason: str, skip_prefixes: tuple[str, ...],
                         unlink_roots: tuple[str, ...], as_json: bool) -> None:
    """收尾已完成的 git worktree（不加 --force）。

    \b
    先核验：已登记、不是主 worktree、没有进行中的合并或变基、里面没有嵌套的已登记 worktree、
    没有进程在用、没有指向树外的非依赖链接（node_modules 这类依赖链接只解除链接对象）、同仓别的 worktree 没有链接指向这里。
    再保全：改动、未跟踪文件、以及不属于依赖/构建/缓存的被忽略文件，打成一个提交挂在
    refs/archive/worktree-closeout/<日期>/<名字>，逐个文件核对。最后才还原改动、按清单删未跟踪文件、
    git worktree remove；有残留目录时经 rmtree 的全部核验删除。分支保留。
    """
    from omnicompany.core.worktree_closeout import InboundLinks, close

    rows = _shared_process_rows()
    inbound = InboundLinks()
    results = [close(p, dry_run=dry_run, reason=reason, skip_prefixes=skip_prefixes, unlink_roots=unlink_roots,
                     process_rows=rows, inbound_links=inbound) for p in paths]
    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        labels = {"closed": "已收尾", "dry-run": "可收尾（未动）", "refused": "拒绝", "incomplete": "未完成"}
        for r in results:
            click.echo(f"{labels[r['outcome']]}：{r['worktree']}")
            if r.get("archive_commit"):
                click.echo(f"  归档：{r.get('archive_ref', '试运行未挂 ref')} = {r['archive_commit'][:12]}"
                           f"（改动 {len(r.get('modified', []))}、未跟踪 {len(r.get('untracked', []))}、"
                           f"被忽略需保全 {len(r.get('ignored_to_archive', []))}）")
            if r.get("branch"):
                click.echo(f"  分支保留：{r['branch']}")
            for p in r["problems"]:
                click.echo(f"  原因：{p}")
        click.echo(f"回执：{results[-1]['receipt_log']}")
    if any(r["outcome"] in {"refused", "incomplete"} for r in results):
        raise SystemExit(2)


@cmd_cleanup.command("reap")
@click.option("--root", "roots", multiple=True, help="只看这些记录根/副本根（绝对路径，可多次）；默认全部已登记的本机根")
@click.option("--dry-run", is_flag=True, help="只核验并报告，不删除")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_cleanup_reap(roots: tuple[str, ...], dry_run: bool, as_json: bool) -> None:
    """按清单回收到期的记录批次和副本：<记录根或副本根>/<业务>/<类别>/<批次>/。

    \b
    根取自各项目 directory_contract 的 records_root / copies_root，以及登记为 records、copies
    的区域与 external_roots（只处理本机）。批次没有清单、未到期、或是还没上传的人看证据，
    一律保留并写明原因；到期的批次再经 rmtree 的全部核验后才删。
    """
    from omnicompany.core.dir_archive import batch_candidates, load_archive
    from omnicompany.core.verified_cleanup import receipt_log_path, remove

    archive = load_archive()
    rows = _shared_process_rows()
    results = []
    for answer in batch_candidates(archive, roots=roots or None):
        verdict = answer["delete"]
        if verdict["allowed"]:
            results.append(remove(answer["target"], dry_run=dry_run, reason="清单到期回收：" + verdict["basis"],
                                  archive=archive, process_rows=rows))
        else:
            results.append({"target": answer["target"], "outcome": "kept", "problems": verdict["problems"]})
    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print(results)
        counts: dict[str, int] = {}
        for r in results:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        summary = "，".join(f"{_LABELS[k]} {v}" for k, v in counts.items()) or "没有找到批次"
        click.echo(f"合计：{summary}")
        click.echo(f"回执：{receipt_log_path()}")
    if any(r["outcome"] in {"refused", "incomplete"} for r in results):
        raise SystemExit(2)
