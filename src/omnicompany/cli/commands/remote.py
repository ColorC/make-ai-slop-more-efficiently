# [OMNI] origin=claude-code domain=omnicompany/cli ts=2026-10-17T00:00:00+08:00 type=cli status=active
# [OMNI] summary="omni remote: 统一远程执行层 CLI。固定子命令 + 短参数, 脚本与参数走文件, 结果以 result.json 为准。"
"""omni remote —— 在远端 Windows 节点上像在本机一样工作。

底层执行走 desktop-node（L2 trusted-exec-l2, Job Object 管进程树）；文件走节点画像里的
http-pull 或 scp；节点差异写在 ``config/remote_nodes.toml``。逻辑在
``omnicompany.core.remote``，这里只做参数与输出。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import click

from omnicompany.core.remote import ops, presence, transfer
from omnicompany.core.remote.backends import TransportError, make_backend
from omnicompany.core.remote.profiles import NodeProfile, load_profiles, resolve
from omnicompany.core.remote.pstext import RemoteRefused
from omnicompany.core.remote.session import refused
from omnicompany.core.remote.store import ledger_path

PATH_LABELS = {
    "result": "本机结果 result.json",
    "receipt_dir": "本机回执目录（命令/原生回执）",
    "ledger": "本机台账",
    "output": "脚本输出（本机副本）",
    "stderr": "原生 stderr（本机副本）",
    "remote_op_dir": "远端运行目录",
    "remote_log": "远端日志",
    "remote_result": "远端 result.json",
    "remote_script": "远端脚本",
    "remote_job_dir": "远端作业目录",
    "remote_registry": "远端作业登记",
    "remote_console": "远端控制台日志",
    "remote_dest": "远端目标目录",
    "remote_backup": "远端覆盖前备份",
    "remote_stage": "远端中转目录",
    "remote_src": "远端来源",
    "local_dest": "本机目标目录",
    "local_backup": "本机覆盖前备份",
}

EXIT_HELP = (
    "退出码：run/ps/wait 透传远端脚本的真实退出码；read/grep/ls 成功 0、远端失败 2；"
    "本机拒绝（白名单、超长、编码载荷）2；传输失败 1；任务超时 124；本机等待到上限 75。"
)


class Ctx:
    def __init__(self, as_json: bool, backend: str | None, config: str | None) -> None:
        self.as_json = as_json
        self.backend = backend
        self.config = Path(config) if config else None


def _ctx() -> Ctx:
    return click.get_current_context().find_object(Ctx) or Ctx(False, None, None)


def _profile(node: str) -> NodeProfile:
    ctx = _ctx()
    return resolve(node, load_profiles(ctx.config))


def _exit_code(result: dict[str, Any]) -> int:
    if result.get("phase") == "refused":
        return 2
    code = result.get("exit_code")
    if isinstance(code, int):
        if sys.platform != "win32" and not 0 <= code <= 255:
            return 1
        return code
    phase = result.get("phase")
    if phase == "wait-timeout":
        return 75
    if phase == "timeout":
        return 124
    return 0 if result.get("ok") else 1


def _print_paths(result: dict[str, Any]) -> None:
    for key, value in (result.get("paths") or {}).items():
        if value:
            click.echo(f"  {PATH_LABELS.get(key, key)}: {value}", err=True)


def _print_error(result: dict[str, Any]) -> None:
    err = result.get("first_error")
    if not err:
        return
    where = ""
    if err.get("line"):
        where = f" 第 {err.get('line')} 行"
        if err.get("line_text"):
            where += f"「{str(err.get('line_text'))[:160]}」"
    click.echo(f"  首错[{err.get('phase') or result.get('phase')}]{where}: {err.get('message')}", err=True)


def _status_line(result: dict[str, Any]) -> None:
    calls = result.get("calls") or []
    tasks = ",".join(str(c.get("task_id")) for c in calls if c.get("task_id"))
    click.echo(
        f"--- omni remote {result.get('op')} node={result.get('node')} backend={result.get('backend')} "
        f"ok={result.get('ok')} exit={result.get('exit_code')} phase={result.get('phase')} "
        f"wall={result.get('wall_s')}s tasks={tasks or '-'}",
        err=True,
    )


def _print_safety(result: dict[str, Any]) -> None:
    for hit in result.get("danger_warnings") or []:
        click.echo(f"  ⚠ 提醒[{hit.get('rule_id')}] {hit.get('file') or ''}第 {hit.get('line')} 行：{hit.get('excerpt')}"
                   f"\n    {hit.get('fix')}", err=True)
    admission = result.get("admission") or {}
    if admission.get("verdict") == "admit":
        req = admission.get("request") or {}
        click.echo(f"  准入通过：申请 {req.get('mem_gb')} GB（{req.get('mem_source')}）可用 {(admission.get('memory') or {}).get('available_gb')} GB"
                   f" 提交余量 {(admission.get('memory') or {}).get('commit_free_gb')} GB"
                   + (f"，排队 {admission.get('waited_s')}s" if admission.get("rounds", 1) > 1 else ""), err=True)
    elif admission.get("verdict") == "skipped":
        click.echo(f"  准入已跳过：{admission.get('reason')}", err=True)
    elif admission.get("verdict") == "admit-unchecked":
        click.echo(f"  ⚠ 占用表没读到，按普通作业放行：{admission.get('message')}", err=True)
    for line in (result.get("kill_guard") or {}).get("warnings") or []:
        click.echo(f"  ⚠ {line}", err=True)
    claim = result.get("claim") or {}
    if claim:
        if claim.get("ok"):
            c = claim.get("claim") or claim
            click.echo(f"  已登记占用 {c.get('claim_id')} 所有者 {c.get('owner') or '-'} 预算 {c.get('budget') or '-'}", err=True)
        else:
            click.echo(f"  ⚠ 已启动但占用登记失败：{claim.get('error')}（补登：omni remote occupancy {result.get('node')} claim --pid … 或 --project …）", err=True)


def _emit(result: dict[str, Any], human: Callable[[dict[str, Any]], None] | None = None, *, as_json: bool = False) -> None:
    if as_json or _ctx().as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if human:
            human(result)
        _print_safety(result)
        _status_line(result)
        _print_error(result)
        _print_paths(result)
    raise SystemExit(_exit_code(result))


def _guarded(op: str, node: str | None, action: Callable[[NodeProfile, Any], dict[str, Any]],
             human: Callable[[dict[str, Any]], None] | None, as_json: bool) -> None:
    profile = backend = None
    try:
        profile = _profile(node) if node else None
        backend = make_backend(profile, _ctx().backend) if profile else None
        result = action(profile, backend)
    except RemoteRefused as error:
        result = refused(profile, backend, op, error)
        if not (as_json or _ctx().as_json):
            click.echo(f"omni remote {op}: 拒绝[{error.rule}] {error}", err=True)
    except TransportError as error:
        result = {"schema": "omni.remote.op/1", "op": op, "node": getattr(profile, "name", node), "ok": False,
                  "phase": "transport", "exit_code": None, "first_error": {"phase": "transport", "message": str(error)}}
    _emit(result, human, as_json=as_json)


_json_option = click.option("--json", "as_json", is_flag=True, help="输出结构化 JSON（同全局 --json）。")


@click.group("remote")
@click.option("--json", "as_json", is_flag=True, help="所有子命令输出结构化 JSON（result.json 同款）。")
@click.option("--backend", type=click.Choice(["desktop-node", "fake"]), default=None,
              help="执行后端。默认 desktop-node；fake = 本机替身（远端路径映射到 OMNI_REMOTE_FAKE_ROOT 下）。也可设 OMNI_REMOTE_BACKEND。")
@click.option("--config", type=click.Path(dir_okay=False), default=None,
              help="节点画像 TOML（默认 omnicompany/config/remote_nodes.toml，或 OMNI_REMOTE_CONFIG）。")
@click.pass_context
def cmd_remote(ctx: click.Context, as_json: bool, backend: str | None, config: str | None) -> None:
    """统一远程执行层：固定子命令 + 短参数，脚本和参数都走文件，结果以 result.json 为准。

    共用机（GAMEBOX）先读 docs/projects/agent-longrun-remote/rnode-shared-use.md：占用登记 / 准入 / 看门狗 / 危险写法拦截，`omni remote occupancy <node>` 看谁在用。

    \b
    omni remote nodes                                   # 画像 + 在线状态
    omni remote run <node> --file x.ps1 [--args a.json] [--cwd D] [--timeout S]
    omni remote ps <node> -c "Get-Date"                 # 只收 ≤300 字符的一行
    omni remote read <node> <path> [--offset N --limit N | --tail N]
    omni remote grep <node> <pattern> <path> [--glob G] [-i] [-F]
    omni remote ls <node> <path> [--depth N]
    omni remote push <node> <本地文件或目录>... <远端目录>   # 白名单、覆盖前备份、从不删除
    omni remote pull <node> <远端文件或目录> <本地目录>
    omni remote share [--to N] <本地文件或目录>... [--name X]  # 放进用户所在机器 share_root\\X\\（不给 --to 自动检测），同名再推即增量同步
    omni remote presence                                # 用户此刻在哪台：各节点距上次键鼠输入秒数
    omni remote spawn <node> <作业名> --file job.ps1    # 后台长任务（脱离单次 exec）
    omni remote job|wait|kill <node> <作业名>            # 状态 / 本机确定性等待 / 只杀登记的进程树
    omni remote stats                                   # 按台账汇总次数、耗时、失败

    \b
    每次操作都在本机写回执目录（command-*.ps1 下发原文、native-*.json 原生回执、result.json），
    并追加一行台账；远端 run 另写 <staging>\\runs\\<op_id>\\{script.ps1,args.json,output.log,result.json}。
    脚本里可用 $OmniArgs（args.json 解析结果）、$OmniOpDir、Add-OmniArtifact <路径>；
    args.json 顶层键与脚本 param() 同名的自动按参数传入。
    """
    ctx.obj = Ctx(as_json, backend, config)


@cmd_remote.command("nodes")
@click.option("--no-occupancy", is_flag=True, help="不读共用机占用表摘要（只看画像与控制面）。")
@_json_option
def cmd_nodes(no_occupancy: bool, as_json: bool) -> None:
    """列出节点画像和 desktop-node 在线状态、已授权档位、已批准启动器；开了占用表的在线节点附占用与告警摘要（只读）。"""
    try:
        if _ctx().config:
            os.environ["OMNI_REMOTE_CONFIG"] = str(_ctx().config)
        result = ops.nodes(_ctx().backend or os.environ.get("OMNI_REMOTE_BACKEND"), occupancy=not no_occupancy)
    except RemoteRefused as error:
        raise click.ClickException(str(error)) from error
    if as_json or _ctx().as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for row in result["nodes"]:
        p, s = row["profile"], row["status"]
        mark = "●" if s.get("online") else "○"
        click.echo(f"{mark} {p['name']}  {p['node_id']}  ({', '.join(p['aliases'])})")
        click.echo(f"    执行 {p['exec_channel']}/{p['exec_mode']}  文件 {p['file_channel']}"
                   f"  禁编码载荷={'是' if p['forbid_encoded_payload'] else '否'}")
        if s.get("paired"):
            click.echo(f"    授权档 {', '.join(s.get('granted_modes') or []) or '-'}  启动器 {s.get('approved_programs') or '-'}"
                       f"  最近上报 {s.get('last_seen_s_ago')} 秒前")
        else:
            click.echo("    控制面里没有这个 node_id（未配对或已吊销）")
        click.echo(f"    写入白名单 {p['write_roots']}")
        click.echo(f"    中转 {p['staging_root']}  作业 {p['jobs_root']}  备份 {p['backup_root']}")
        if row.get("occupancy") is not None:
            for line in occupancy_lines(p["name"], row["occupancy"], top=4):
                click.echo("    " + line)
    if result.get("control_plane_error"):
        click.echo(f"⚠ 控制面不可用: {result['control_plane_error']}", err=True)
    click.echo(f"台账 {ledger_path()}", err=True)


def _human_run(result: dict[str, Any]) -> None:
    if result.get("direct_console"):
        click.echo(result["direct_console"])
    output_path = (result.get("paths") or {}).get("output")
    if output_path and Path(output_path).is_file():
        text = Path(output_path).read_text(encoding="utf-8")
        if text:
            click.echo(text, nl=not text.endswith("\n"))
    stderr_path = (result.get("paths") or {}).get("stderr")
    if stderr_path and Path(stderr_path).is_file():
        text = Path(stderr_path).read_text(encoding="utf-8").strip()
        if text:
            click.echo(text, err=True)


@cmd_remote.command("run")
@click.argument("node")
@click.option("--file", "script", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="本机 PowerShell 脚本（UTF-8）。可以有 param()。")
@click.option("--args", "args", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="参数 JSON 文件；顶层键与 param() 同名的按参数传入，其余在 $OmniArgs。")
@click.option("--cwd", default=None, help="远端工作目录（默认画像 default_cwd，空则本次运行目录）。")
@click.option("--timeout", "timeout_s", type=int, default=600, show_default=True, help="秒，上限 1800。更长的用 spawn。")
@click.option("--delivery", type=click.Choice(["auto", "inline", "file"]), default="auto", show_default=True,
              help="auto: 能内联就内联（明文 here-string），超长或含 '@ 行自动走文件通道。")
@click.option("--admission", type=click.Choice(["auto", "skip"]), default="auto", show_default=True,
              help="脚本里看得出要起 Unity 时，开了占用表的节点先做准入；skip 需配 --reason。")
@click.option("--reason", "admission_reason", default=None, help="--admission skip 的理由（写进回执与台账）。")
@click.option("--queue-timeout", "queue_timeout_s", type=int, default=0, show_default=True,
              help="准入资源不够时本机排队重查的最长秒数（0 = 立即拒绝并说明谁占着）。")
@_json_option
def cmd_run(node: str, script: Path, args: Path | None, cwd: str | None, timeout_s: int, delivery: str, admission: str,
            admission_reason: str | None, queue_timeout_s: int, as_json: bool) -> None:
    """在远端跑一个脚本文件，取回真实退出码与 result.json。

    runner 固定 -NoProfile -NonInteractive、UTF-8 输出、$ProgressPreference='SilentlyContinue'、
    $ErrorActionPreference='Stop'；未捕获异常记为 phase=script 并给出行号。退出码语义同
    powershell -File：显式 exit N 为 N，未捕获异常为 1，正常结束为 0。
    下发前静态拦危险写法（按名杀进程、碰桌面壳、注入、写系统目录），规则 config/remote_danger_rules.json。
    """
    _guarded("run", node, lambda p, b: ops.run(p, b, script, args=args, cwd=cwd, timeout_s=timeout_s, delivery=delivery,
                                                admission=admission, admission_reason=admission_reason,
                                                queue_timeout_s=queue_timeout_s),
             _human_run, as_json)


@cmd_remote.command("ps")
@click.argument("node")
@click.option("-c", "--command", "command", required=True, help=f"一行 PowerShell，最多 {ops.PS_COMMAND_LIMIT} 字符；更长的写文件用 run --file。")
@click.option("--cwd", default=None, help="远端工作目录。")
@click.option("--timeout", "timeout_s", type=int, default=120, show_default=True)
@_json_option
def cmd_ps(node: str, command: str, cwd: str | None, timeout_s: int, as_json: bool) -> None:
    """跑一行短命令（同 run 的 runner 与 result.json）。超长直接拒绝并提示改用 --file。"""
    _guarded("ps", node, lambda p, b: ops.ps(p, b, command, cwd=cwd, timeout_s=timeout_s), _human_run, as_json)


def _human_read(result: dict[str, Any]) -> None:
    data = result.get("data") or {}
    if data.get("binary"):
        click.echo(f"(二进制文件 {data.get('path')}，{data.get('size')} 字节，不输出内容)", err=True)
        return
    for line in data.get("lines") or []:
        number = line.get("n")
        click.echo(f"{number if number is not None else '?':>6}\t{line.get('text')}")
    if data.get("ok"):
        last = (data.get("lines") or [{}])[-1].get("n") if data.get("lines") else None
        total = data.get("total_lines")
        click.echo(f"[{data.get('path')}] {data.get('size')} 字节, 行 {data.get('start_line')}–{last}"
                   f"{f' / 共 {total} 行' if total is not None else ''}", err=True)
        if data.get("has_more") and last:
            click.echo(f"  还有更多: --offset {int(last) + 1}", err=True)


@cmd_remote.command("read")
@click.argument("node")
@click.argument("path")
@click.option("--offset", type=int, default=1, show_default=True, help="起始行号（1 起）。")
@click.option("--limit", type=int, default=200, show_default=True, help="最多返回行数（上限 2000）。")
@click.option("--tail", type=int, default=0, help="只看最后 N 行（给了就忽略 offset/limit）。")
@click.option("--timeout", "timeout_s", type=int, default=120, show_default=True)
@_json_option
def cmd_read(node: str, path: str, offset: int, limit: int, tail: int, timeout_s: int, as_json: bool) -> None:
    """只读：带行号读远端文件，按 offset/limit 或 tail 分页（不改任何文件）。"""
    _guarded("read", node, lambda p, b: ops.read(p, b, path, offset=offset, limit=limit, tail=tail, timeout_s=timeout_s),
             _human_read, as_json)


def _human_grep(result: dict[str, Any]) -> None:
    data = result.get("data") or {}
    for match in data.get("matches") or []:
        click.echo(f"{match.get('path')}:{match.get('line')}: {match.get('text')}")
    if data.get("ok"):
        click.echo(f"[{data.get('engine')}] {data.get('returned')} 条（跳过 {data.get('offset')}）"
                   f"{'，扫描文件 ' + str(data.get('files_scanned')) if data.get('engine') == 'select-string' else ''}", err=True)
        if data.get("has_more"):
            click.echo(f"  还有更多: --offset {int(data.get('offset') or 0) + int(data.get('returned') or 0)}", err=True)


@cmd_remote.command("grep")
@click.argument("node")
@click.argument("pattern")
@click.argument("path")
@click.option("--glob", default=None, help="文件名过滤，如 *.cs 或 src/**/*.ps1。")
@click.option("-i", "--ignore-case", is_flag=True)
@click.option("-F", "--fixed", is_flag=True, help="按字面串匹配（不当正则）。")
@click.option("--offset", type=int, default=0, show_default=True, help="跳过前 N 条匹配（翻页）。")
@click.option("--max", "max_results", type=int, default=200, show_default=True)
@click.option("--engine", type=click.Choice(["auto", "rg", "select-string"]), default="auto", show_default=True,
              help="auto: 远端有 rg 用 rg，否则 Select-String。")
@click.option("--timeout", "timeout_s", type=int, default=300, show_default=True)
@_json_option
def cmd_grep(node: str, pattern: str, path: str, glob: str | None, ignore_case: bool, fixed: bool, offset: int,
             max_results: int, engine: str, timeout_s: int, as_json: bool) -> None:
    """只读：在远端文件或目录里搜内容，不跟随链接/联接点，结果分页。"""
    _guarded("grep", node, lambda p, b: ops.grep(p, b, pattern, path, glob=glob, ignore_case=ignore_case, fixed=fixed,
                                                  offset=offset, max_results=max_results, engine=engine, timeout_s=timeout_s),
             _human_grep, as_json)


def _human_ls(result: dict[str, Any]) -> None:
    data = result.get("data") or {}
    for entry in data.get("entries") or []:
        size = "" if entry.get("size") is None else entry.get("size")
        click.echo(f"{entry.get('type'):<4} {size!s:>12}  {str(entry.get('mtime'))[:19]}  {entry.get('path')}")
    if data.get("ok"):
        click.echo(f"[{data.get('path')}] {data.get('returned')} 项{'（已截断）' if data.get('truncated') else ''}", err=True)


@cmd_remote.command("ls")
@click.argument("node")
@click.argument("path")
@click.option("--depth", type=int, default=1, show_default=True, help="递归层数（上限 8；不进入链接）。")
@click.option("--limit", type=int, default=500, show_default=True)
@click.option("--glob", default=None, help="只列文件名匹配的文件（目录照列）。")
@click.option("--timeout", "timeout_s", type=int, default=120, show_default=True)
@_json_option
def cmd_ls(node: str, path: str, depth: int, limit: int, glob: str | None, timeout_s: int, as_json: bool) -> None:
    """只读：列远端目录（类型、大小、修改时间）。"""
    _guarded("ls", node, lambda p, b: ops.ls(p, b, path, depth=depth, limit=limit, glob=glob, timeout_s=timeout_s),
             _human_ls, as_json)


def _human_transfer(result: dict[str, Any]) -> None:
    data = result.get("data") or {}
    entries = (data.get("files") if result.get("op") in {"push", "share"} else data.get("local_files")) or []
    for entry in entries:
        click.echo(f"{entry.get('action'):<9} {entry.get('target')}" + (f"  (备份 {entry.get('backup')})" if entry.get("backup") else ""))
    if data:
        click.echo(f"新增 {data.get('new', 0)}  更新 {data.get('updated', 0)}  未变 {data.get('unchanged', 0)}", err=True)


@cmd_remote.command("push")
@click.argument("node")
@click.argument("paths", nargs=-1, required=True)
@click.option("--into", is_flag=True, help="推目录时把内容直接放进远端目录（远端目录名与本地目录名不同才需要）。")
@_json_option
def cmd_push(node: str, paths: tuple[str, ...], into: bool, as_json: bool) -> None:
    """本机文件/目录 → 远端目录（最后一个参数）。只写白名单；覆盖前备份；从不删除；写后逐个比对字节。"""
    if len(paths) < 2:
        raise click.UsageError("至少要给一个本地路径和一个远端目录")
    sources, remote_dir = [Path(p) for p in paths[:-1]], paths[-1]
    _guarded("push", node, lambda p, b: transfer.push(p, b, sources, remote_dir, into=into), _human_transfer, as_json)


def _share_nodes(to: str | None) -> tuple[list[str], list[dict[str, Any]]]:
    """--to wins; otherwise every node with a share_root is probed for the user's last input."""
    if to:
        return [resolve(to, load_profiles(_ctx().config)).name], []
    candidates = [p for p in load_profiles(_ctx().config).values() if p.share_root]
    backends = {p.name: make_backend(p, _ctx().backend) for p in candidates}  # click context is thread-local
    probes = presence.probe_all(candidates, lambda p: backends[p.name])
    return presence.pick(probes), probes


def _human_presence(probes: list[dict[str, Any]], chosen: list[str]) -> None:
    for item in probes:
        mark = "→" if item["node"] in chosen else " "
        if item.get("idle_s") is None:
            click.echo(f"{mark} {item['node']:<18} 检测失败: {item.get('error')}", err=True)
        else:
            click.echo(f"{mark} {item['node']:<18} 距上次键鼠输入 {item['idle_s']} 秒  用户 {item.get('user')}"
                       f"  前台 {item.get('foreground') or '-'} {(item.get('foreground_title') or '')[:40]}", err=True)


@cmd_remote.command("presence")
@_json_option
def cmd_presence(as_json: bool) -> None:
    """用户此刻在哪台机器：问每个配了 share_root 的节点距上次键鼠输入多少秒（10 分钟内算在用）。"""
    try:
        chosen, probes = _share_nodes(None)
    except RemoteRefused as error:
        click.echo(f"omni remote presence: 拒绝[{error.rule}] {error}", err=True)
        raise SystemExit(2)
    if as_json or _ctx().as_json:
        click.echo(json.dumps({"chosen": chosen, "probes": probes}, ensure_ascii=False, indent=2))
    else:
        _human_presence(probes, chosen)
        click.echo(" ".join(chosen))


@cmd_remote.command("share")
@click.argument("paths", nargs=-1, required=True)
@click.option("--to", "to", default=None, help="目标节点；不给就自动检测用户此刻在哪台（10 分钟内有键鼠输入的都推）。")
@click.option("--name", default=None, help="共享目录名（默认取第一个本地路径的名字）；同名再推即原地增量同步。")
@_json_option
def cmd_share(paths: tuple[str, ...], to: str | None, name: str | None, as_json: bool) -> None:
    """本机文件/目录 → 用户所在机器的 share_root\\<名字>\\；输出每个文件的远端完整路径。"""
    sources = [Path(p) for p in paths]
    try:
        nodes, probes = _share_nodes(to)
    except RemoteRefused as error:
        click.echo(f"omni remote share: 拒绝[{error.rule}] {error}", err=True)
        raise SystemExit(2)
    if probes and not (as_json or _ctx().as_json):
        _human_presence(probes, nodes)
    codes = []
    for node in nodes:
        try:
            _guarded("share", node, lambda p, b: transfer.share(p, b, sources, name=name), _human_transfer, as_json)
        except SystemExit as done:
            codes.append(int(done.code or 0))
    raise SystemExit(max(codes or [0]))


@cmd_remote.command("pull")
@click.argument("node")
@click.argument("remote")
@click.argument("local_dir", type=click.Path(file_okay=False, path_type=Path))
@_json_option
def cmd_pull(node: str, remote: str, local_dir: Path, as_json: bool) -> None:
    """远端文件/目录 → 本机目录。本机已有且内容不同的先备份到 OmniRemote\\backup\\<op_id>；从不删除。"""
    _guarded("pull", node, lambda p, b: transfer.pull(p, b, remote, local_dir), _human_transfer, as_json)


def _human_job(result: dict[str, Any]) -> None:
    data = result.get("data") or {}
    if result.get("op") == "spawn":
        if result.get("phase") == "refused":
            click.echo("未启动：准入不通过（原因见下方首错；没有下发任何东西）", err=True)
            return
        click.echo(f"已启动 {data.get('name')} pid={data.get('pid')} created={data.get('created')}", err=True)
        return
    if result.get("op") == "kill":
        click.echo(f"{data.get('state')}: 结束 {data.get('killed')}  跳过 {data.get('skipped')}", err=True)
        return
    for line in data.get("log_tail") or []:
        click.echo(line)
    if data.get("console_tail"):
        click.echo("----- console -----", err=True)
        for line in data.get("console_tail"):
            click.echo(line, err=True)
    click.echo(f"作业 {data.get('name')} state={data.get('state')} pid={data.get('pid')} "
               f"后代={[d.get('pid') for d in data.get('descendants') or []]}"
               + (f" 等待 {result.get('waited_s')}s/{result.get('polls')} 次" if result.get("op") == "wait" else ""), err=True)


@cmd_remote.command("spawn")
@click.argument("node")
@click.argument("name")
@click.option("--file", "script", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="后台作业脚本（同 run 的 runner，结束写 result.json）。")
@click.option("-c", "--command", "command", default=None, help=f"一行短命令（≤{ops.PS_COMMAND_LIMIT} 字符），与 --file 二选一。")
@click.option("--args", "args", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--cwd", default=None)
@click.option("--mem-gb", type=click.FloatRange(min=0.0), default=None,
              help="内存预算（GB，作业整棵进程树私有内存）。给了就参与准入，并默认 --on-breach stop。")
@click.option("--deadline", default=None, help="截止：90m / 2h / 1d / HH:MM / ISO。给了默认 --on-breach stop。")
@click.option("--on-breach", type=click.Choice(["stop", "alert"]), default=None,
              help="超预算/超截止时看门狗怎么做：stop = 只收这棵登记树（pid+创建时间）；alert = 只告警。")
@click.option("--owner", default=None, help="所有者（默认 pty:<OMNI_CC_PTY_ID>，或 OMNI_OCCUPANCY_OWNER）。")
@click.option("--purpose", default=None, help="用途一句话，进占用表。")
@click.option("--project", default=None, help="Unity 工程路径（脚本里看不出时手动给；用于同工程检查与内存估算）。")
@click.option("--kind", type=click.Choice(["unity", "job", "server", "recording", "experiment", "other"]), default=None)
@click.option("--admission", type=click.Choice(["auto", "skip"]), default="auto", show_default=True)
@click.option("--reason", "admission_reason", default=None, help="--admission skip 的理由。")
@click.option("--queue-timeout", "queue_timeout_s", type=int, default=0, show_default=True,
              help="资源不够时本机确定性排队重查的最长秒数（0 = 立即拒绝并说明谁占着）。")
@click.option("--window", type=click.Choice(["normal", "hidden", "minimized"]), default="normal", show_default=True,
              help="作业控制台窗口：hidden 不显示；minimized 最小化且不抢焦点。")
@click.option("--no-claim", is_flag=True, help="不写占用登记（不建议；看门狗与准入将看不到所有者）。")
@_json_option
def cmd_spawn(node: str, name: str, script: Path | None, command: str | None, args: Path | None, cwd: str | None,
              mem_gb: float | None, deadline: str | None, on_breach: str | None, owner: str | None, purpose: str | None,
              project: str | None, kind: str | None, admission: str, admission_reason: str | None, queue_timeout_s: int,
              window: str, no_claim: bool, as_json: bool) -> None:
    """起后台长任务：WMI 创建、脱离单次 exec 的作业对象；登记 pid + 创建时间，日志写远端作业目录。

    \b
    开了占用表的节点（GAMEBOX）：起之前做准入（同工程编辑器、物理可用、提交余量、登记预算池），
    不够就拒绝并列出谁占着（或 --queue-timeout 排队）；起之后自动登记所有者与预算。
    下发前静态拦危险写法（config/remote_danger_rules.json）。
    """
    if (script is None) == (command is None):
        raise click.UsageError("--file 和 -c 二选一")
    opts = {"mem_gb": mem_gb, "deadline": deadline, "on_breach": on_breach, "owner": owner, "purpose": purpose,
            "project": project, "kind": kind, "admission": admission, "reason": admission_reason,
            "queue_timeout_s": queue_timeout_s, "claim": not no_claim}

    def action(p: NodeProfile, b: Any) -> dict[str, Any]:
        inline = ops.short_command(command, what="spawn -c") if command is not None else None
        return ops.spawn(p, b, name, script, args=args, cwd=cwd, inline=inline, window=window, occupancy_opts=opts)

    _guarded("spawn", node, action, _human_job, as_json)


@cmd_remote.command("job")
@click.argument("node")
@click.argument("name")
@click.option("--tail", type=int, default=30, show_default=True, help="附带日志最后 N 行。")
@_json_option
def cmd_job(node: str, name: str, tail: int, as_json: bool) -> None:
    """看一个登记作业的状态（running / exited / killed）、后代进程、result.json 与日志尾。查询本身成功即退出 0。"""
    def action(p: NodeProfile, b: Any) -> dict[str, Any]:
        result = ops.job(p, b, name, tail=tail)
        result["job_exit_code"] = result.get("exit_code")
        result["exit_code"] = 0 if result.get("ok") else 3
        return result

    _guarded("job", node, action, _human_job, as_json)


@cmd_remote.command("wait")
@click.argument("node")
@click.argument("name")
@click.option("--timeout", "timeout_s", type=int, default=3600, show_default=True, help="本机最多等多少秒（上限 24h）。")
@click.option("--every", "every_s", type=int, default=15, show_default=True, help="查询间隔秒（本机确定性循环，不经模型）。")
@click.option("--tail", type=int, default=40, show_default=True)
@_json_option
def cmd_wait(node: str, name: str, timeout_s: int, every_s: int, tail: int, as_json: bool) -> None:
    """本机确定性等待作业结束（带上限），结束后透传作业的真实退出码；到上限仍在跑退出 75。"""
    _guarded("wait", node, lambda p, b: ops.wait(p, b, name, timeout_s=timeout_s, every_s=every_s, tail=tail), _human_job, as_json)


@cmd_remote.command("kill")
@click.argument("node")
@click.argument("name")
@_json_option
def cmd_kill(node: str, name: str, as_json: bool) -> None:
    """只结束登记过的作业：核对 pid + 创建时间，连同后代逐个按 pid 结束；绝不按进程名。"""
    _guarded("kill", node, lambda p, b: ops.kill(p, b, name), _human_job, as_json)


def _age(iso: str | None) -> str:
    from datetime import datetime

    if not iso:
        return "?"
    import re

    try:
        # PowerShell 'o' format has 7 fractional digits; older fromisoformat only takes up to 6
        then = datetime.fromisoformat(re.sub(r"(\.\d{1,6})\d+", lambda m: m.group(1), str(iso).replace("Z", "+00:00")))
        seconds = int((datetime.now(then.tzinfo) - then).total_seconds())
    except ValueError:
        return str(iso)
    return f"{seconds} 秒前" if seconds < 120 else (f"{seconds // 60} 分钟前" if seconds < 7200 else f"{seconds // 3600} 小时前")


def occupancy_lines(node: str, data: dict[str, Any], *, top: int = 12) -> list[str]:
    """Human lines for one node's occupancy table (+ alerts); shared by `nodes` and `occupancy`."""
    from omnicompany.core.remote.occupancy import describe_entry

    if not data.get("ok"):
        return [f"占用表读取失败：{data.get('error')}（omni remote occupancy {node} 重试）"]
    table = data.get("table") if "table" in data else data.get("snapshot")
    if not table:
        return [f"占用表还没有数据：omni remote occupancy {node}（采集一次），"
                f"omni remote occupancy {node} watchdog start（常驻刷新）"]
    mem = table.get("memory") or {}
    alive = data.get("watchdog_alive") if "watchdog_alive" in data else (table.get("watchdog") or {}).get("alive")
    flags = ",".join(table.get("machine_flags") or []) or "正常"
    lines = [f"占用 {str(table.get('generated_at'))[:19]}（{_age(table.get('generated_at'))}，{table.get('producer')}；"
             f"看门狗 {'●在跑' if alive else '○没在跑'}）机器 {flags}：物理可用 {mem.get('available_gb')}/{mem.get('total_gb')} GB"
             f"  提交 {mem.get('commit_gb')}/{mem.get('commit_limit_gb')} GB  登记预算 {table.get('registered_budget_gb')} GB"]
    entries = table.get("entries") or []
    for entry in entries[:top]:
        mark = "‼" if entry.get("breaches") else " "
        lines.append(f"  {mark} {describe_entry(entry)}" + (f"  超限 {','.join(entry['breaches'])}" if entry.get("breaches") else ""))
    if len(entries) > top:
        lines.append(f"    …另 {len(entries) - top} 项（omni remote occupancy {node}）")
    stale = table.get("stale_claims") or []
    if stale:
        lines.append(f"  已失效登记 {len(stale)} 条（进程已不在）：" + ", ".join(str(s.get("claim_id")) for s in stale[:5]))
    alerts = [a for a in (data.get("alerts") or data.get("new_alerts") or []) if isinstance(a, dict)]
    if alerts:
        last = alerts[-1]
        what = last.get("entry") and describe_entry(last["entry"]) or (last.get("memory") and f"可用 {last['memory'].get('available_gb')} GB")
        lines.append(f"  告警 {len(alerts)} 条（最近 {str(last.get('at'))[:19]} {last.get('kind')} {last.get('action')}：{what}）"
                     f"  全部：omni remote occupancy {node} alerts")
    paths = table.get("paths") or {}
    if paths.get("table"):
        lines.append(f"  远端占用表 {paths.get('table')}  告警 {paths.get('alerts')}")
    return lines


@cmd_remote.command("occupancy")
@click.argument("node")
@click.argument("action", required=False, default="show",
                type=click.Choice(["show", "alerts", "claim", "release", "watchdog"]))
@click.argument("target", required=False, default=None)
@click.option("--pid", type=int, default=None, help="claim：登记的进程 pid（核对创建时间）。")
@click.option("--project", default=None, help="claim：Unity 工程路径（认领该工程唯一的编辑器）。")
@click.option("--kind", type=click.Choice(["unity", "job", "server", "recording", "experiment", "other"]), default=None)
@click.option("--mem-gb", type=click.FloatRange(min=0.0), default=None, help="claim：内存预算 GB（进程树私有内存）。")
@click.option("--deadline", default=None, help="claim：截止 90m / 2h / 1d / HH:MM / ISO。")
@click.option("--on-breach", type=click.Choice(["stop", "alert"]), default=None,
              help="claim：超限时看门狗 stop（只收这棵树，pid+创建时间）或 alert；给了预算/截止默认 stop。")
@click.option("--purpose", default=None, help="claim：用途一句话。")
@click.option("--owner", default=None, help="所有者（默认 pty:<OMNI_CC_PTY_ID> 或 OMNI_OCCUPANCY_OWNER）。")
@click.option("--claim-id", default=None, help="claim：登记名（默认 <kind>-<pid>）。")
@click.option("--force", is_flag=True, help="claim/release：覆盖别人名下的登记（只在所有者已确认时用）。")
@click.option("--limit", type=int, default=50, show_default=True, help="alerts：最近 N 条。")
@click.option("--hours", type=float, default=None, help="watchdog start：寿命小时（默认 policy watchdog_hours）。")
@click.option("--interval", "interval_s", type=int, default=None, help="watchdog start：间隔秒（默认 policy）。")
@_json_option
def cmd_occupancy(node: str, action: str, target: str | None, pid: int | None, project: str | None, kind: str | None,
                  mem_gb: float | None, deadline: str | None, on_breach: str | None, purpose: str | None, owner: str | None,
                  claim_id: str | None, force: bool, limit: int, hours: float | None, interval_s: int | None, as_json: bool) -> None:
    """共用机占用表：谁在用、用多少、预算与告警（远端 occupancy.json 是真源）。

    \b
    omni remote occupancy gamebox                         # 采集一次并显示（同时刷新远端 occupancy.json）
    omni remote occupancy gamebox alerts [--limit 50]     # 看门狗与采集写下的告警（只读）
    omni remote occupancy gamebox claim --project C:/vcs/record/Client --mem-gb 40 --purpose "1.8.3 录像"
    omni remote occupancy gamebox claim --pid 1234 --kind server --deadline 3h --on-breach alert
    omni remote occupancy gamebox release <登记名>        # 只有所有者能释放
    omni remote occupancy gamebox watchdog start|status|stop
    """
    from omnicompany.core.remote import occupancy as occ

    who = owner or occ.default_owner()

    def act(p: NodeProfile, b: Any) -> dict[str, Any]:
        if action == "show":
            return occ.snapshot(p, b)
        if action == "alerts":
            return occ.alerts(p, b, limit=limit)
        if action == "claim":
            the_kind = kind or ("unity" if project and pid is None else "other")
            cid = claim_id or f"{the_kind}-{pid or 'project'}-{__import__('time').strftime('%Y%m%d-%H%M%S')}"
            return occ.claim(p, b, claim_id=cid, owner=who, kind=the_kind, pid=pid, project=project, mem_gb=mem_gb,
                             deadline=deadline, on_breach=on_breach, purpose=purpose, force=force)
        if action == "release":
            if not target:
                raise RemoteRefused("input", "release 要给登记名：omni remote occupancy <node> release <登记名>")
            return occ.release(p, b, target, owner=who, force=force)
        sub = target or "status"
        if sub == "start":
            occ._require(p)
            pol = occ.policy(p)
            script_text = occ.watchdog_script(b, p, hours=hours or float(pol["watchdog_hours"]),
                                              interval_s=interval_s or int(pol["watchdog_interval_s"]))
            return ops.spawn(p, b, occ.WATCHDOG_JOB, None, inline=script_text, window="hidden", occupancy_opts={
                "admission": "skip", "reason": "occupancy watchdog: one PowerShell loop, < 200 MB", "kind": "watchdog",
                "owner": who, "purpose": "machine occupancy watchdog (alerts; stops only consented claims)"})
        if sub == "stop":
            return ops.kill(p, b, occ.WATCHDOG_JOB)
        if sub == "status":
            result = ops.job(p, b, occ.WATCHDOG_JOB, tail=10)
            result["job_exit_code"] = result.get("exit_code")
            result["exit_code"] = 0 if result.get("ok") else 3
            return result
        raise RemoteRefused("input", f"watchdog 只认 start / status / stop: {sub}")

    def human(result: dict[str, Any]) -> None:
        data = result.get("data") or {}
        if action == "show" and data.get("ok"):
            for line in occupancy_lines(node, data, top=40):
                click.echo(line)
        elif action == "alerts" and data.get("ok"):
            for alert in data.get("alerts") or []:
                if not isinstance(alert, dict):
                    continue
                entry = alert.get("entry")
                what = occ.describe_entry(entry) if entry else f"可用 {(alert.get('memory') or {}).get('available_gb')} GB"
                click.echo(f"{str(alert.get('at'))[:19]} {alert.get('kind'):<12} {alert.get('action'):<10} {what}"
                           + (f" {alert.get('breaches')}" if alert.get("breaches") else ""))
            click.echo(f"[{data.get('path')}] {len(data.get('alerts') or [])} 条", err=True)
        elif action in {"claim", "release"} and data.get("ok"):
            c = data.get("claim") or {}
            click.echo(f"{'已登记' if action == 'claim' else '已释放'} {c.get('claim_id')} 所有者 {c.get('owner')} pid {c.get('pid')} "
                       f"{c.get('name')} {c.get('project') or ''} 预算 {c.get('budget')}", err=True)
        elif action == "watchdog":
            _human_job(result)

    _guarded("occupancy", node, act, human, as_json)


@cmd_remote.command("stats")
@click.option("--since", default=None, help="起点：ISO 时间或当天 HH:MM。")
@click.option("--until", default=None)
@click.option("--node", default=None, help="只看某个节点画像名。")
@click.option("--by", type=click.Choice(["op", "node"]), default="op", show_default=True)
@_json_option
def cmd_stats(since: str | None, until: str | None, node: str | None, by: str, as_json: bool) -> None:
    """按本机台账汇总各类操作的次数、失败、拒绝、耗时与下发字符数。"""
    result = ops.stats(since=since, until=until, node=node, by=by)
    if as_json or _ctx().as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    click.echo(f"{'次数':>4} {'失败':>4} {'拒绝':>4} {'耗时s':>8} {'平均s':>6} {'字符':>8}  类别")
    for g in result["groups"]:
        click.echo(f"{g['count']:>4} {g['failed']:>4} {g['refused']:>4} {g['wall_s']:>8} {g['avg_s']:>6} {g['chars']:>8}  {g['key']}")
    click.echo(f"共 {result['total']} 次；台账 {ledger_path()}", err=True)


cmd_remote.epilog = EXIT_HELP


if __name__ == "__main__":  # python -m omnicompany.cli.commands.remote ...
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    cmd_remote(prog_name="omni remote")
