# [OMNI] origin=claude-code-worker domain=omnicompany/cli ts=2026-08-06T00:00:00+08:00 type=cli status=active
# [OMNI] material_id="material:omnicompany.cli.commands.desktop_node.py"
# [OMNI] summary="omni desktop-node 命令组: 桌面派 Remote Execute Node 的控制端入口(ls/status/exec/task/revoke/audit/pairing)。补上此前只有 API 没有调用方的任务下发口。"
# [OMNI] why="desktop_nodes 控制面自 2026-08-04 起就有 POST /{node}/tasks, 但全仓没有任何东西调它 —— 唯一用法是手工 curl。这条 CLI 是那个缺口的补丁: 薄 HTTP 客户端, 只调 local-only 端点, 不复制控制面逻辑、不碰 SSH 派(remote_nodes*/home.py)、不新增任何节点侧能力。权限只减不增: 这里能一键收回, 不能一键授予 —— 授予永远由节点侧同意发起。"
# [OMNI] tags=cli,desktop-nodes,remote-execute-node,dispatch,revoke,audit
"""omni desktop-node —— 桌面派 Remote Execute Node 的控制端 CLI。

和 ``omni home``(SSH 派)完全无关: 那套装 sshd + 反向隧道 + 任意 shell, 这套节点
**不开任何入站端口**, 由节点主动出站长轮询, 拿的是控制端签名的结构化任务。

本模块只做一件事: 当那台桌面节点已经把权限档配好之后, 让控制端能真的把任务发下去、
把回执取回来。它是 ``/api/desktop-nodes`` 之上的薄 HTTP 客户端 ——

- **不**改任何授权语义: 能跑哪档、哪些能力, 由节点配对时登记的 grant 决定;
- **不**能提权: 控制端无法给自己发权限, 授予只能由桌面端(节点侧)发起同意;
- **能**收权: ``revoke`` 是控制端单方面可做的, 因为收紧从来不需要对方同意。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import click

from omnicompany.dashboard.controlplane.desktop_nodes_core import (
    BOOTSTRAP_DEFAULT_MINUTES,
    BOOTSTRAP_MAX_MINUTES,
    MODE_MAX_TIMEOUT_MS,
    MODE_READONLY,
    MODE_REQUIRED_CAPABILITIES,
    MODE_TRUSTED_EXEC,
    NODE_MODES,
    permission_profile,
    read_audit,
)

#: 节点最近一次长轮询超过这个时长, 就当它**没在轮询**(轮询窗口是 20 s)。
#: 注意: 没在轮询 ≠ 离线 —— 节点执行长任务期间本来就不轮询, 见 _liveness。
ONLINE_WINDOW_MS = 90_000
#: 节点自己产生的审计事件(task.settled 等)在这个时长内出现过, 就算它活着。
#: 比长轮询宽, 因为一个长任务期间节点可能几分钟才结算一次。
NODE_ACTIVITY_WINDOW_MS = 15 * 60 * 1000
#: 判活时向前扫多少条审计记录找节点自产事件。
NODE_ACTIVITY_SCAN = 200
#: 执行档默认 10 分钟, 可用 --timeout-ms 覆盖到 MODE_MAX_TIMEOUT_MS。
#: 信封 TTL = timeout + 90s, L1 仍卡 10 分钟, L2 放到 32 分钟(30 分钟任务 + 余量)。
MODE_DEFAULT_TIMEOUT_MS = {
    MODE_READONLY: 30_000,
    MODE_TRUSTED_EXEC: 600_000,
}
MODE_MAX_TTL_MS = {
    MODE_READONLY: 10 * 60 * 1000,
    MODE_TRUSTED_EXEC: 32 * 60 * 1000,
}
TERMINAL_STATUS = {"completed", "failed", "rejected", "revoked", "expired", "invalid"}

MODE_LABEL = {
    MODE_READONLY: "只读 L1",
    MODE_TRUSTED_EXEC: "执行 L2",
}
LEVEL_LABEL = {
    "all": "全部权限(只读 + 执行)",
    "partial": "部分权限",
    "none": "无授权",
    "revoked": "已吊销",
}


# ── HTTP helper(全部走本机控制端, 只调 local-only 端点)───────────────────────

def _base() -> str:
    import os
    return os.environ.get("OMNI_DASHBOARD_URL", "http://127.0.0.1:8210").rstrip("/")


def _controller_token() -> str:
    """The gateway secret, for the rare run that is not on the controller box.

    The default path is loopback, which the control plane already accepts as the
    operator.  Point `OMNI_DASHBOARD_URL` somewhere else and you also have to say
    who you are: `OMNI_CONTROLLER_TOKEN` carries the contents of
    `~/codeweb/omni-desktop-session.secret`, the same secret Caddy presents.
    """
    import os
    return os.environ.get("OMNI_CONTROLLER_TOKEN", "").strip()


def _call(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any]]:
    url = f"{_base()}/api/desktop-nodes{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "X-Omnicompany-Actor": "omni desktop-node"}
    token = _controller_token()
    if token:
        headers["X-Omnicompany-Controller-Auth"] = token
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — 本机回环
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {"ok": False, "error": body[:200] or f"HTTP {error.code}"}
    except (urllib.error.URLError, OSError) as error:
        raise click.ClickException(
            f"连不上控制端 {_base()}: {error}\n"
            "  dashboard 没起? 先 `omni dashboard status`。"
            "  换地址用 OMNI_DASHBOARD_URL。"
        ) from error
    except ValueError as error:
        raise click.ClickException(f"控制端返回的不是 JSON: {error}") from error


def _fetch_nodes() -> list[dict[str, Any]]:
    status, body = _call("GET", "")
    if status != 200 or not body.get("ok"):
        raise click.ClickException(f"列节点失败: {body.get('error') or status}")
    return [node for node in body.get("nodes") or [] if isinstance(node, dict)]


def _resolve_node(node_id: str) -> dict[str, Any]:
    """按完整 id 或唯一前缀取节点(node_id 是 36 位十六进制, 全打太长)。"""
    nodes = _fetch_nodes()
    exact = [node for node in nodes if node.get("node_id") == node_id]
    if exact:
        return exact[0]
    prefix = [node for node in nodes if str(node.get("node_id") or "").startswith(node_id)]
    if len(prefix) == 1:
        return prefix[0]
    if not prefix:
        known = ", ".join(str(node.get("node_id"))[:20] for node in nodes) or "(一个都没有)"
        raise click.ClickException(f"没有这个节点: {node_id}\n  已配对: {known}")
    raise click.ClickException(f"节点前缀 {node_id} 不唯一, 命中 {len(prefix)} 个, 请写长一点")


def _polling(node: dict[str, Any]) -> bool:
    """节点最近是否在长轮询取活。

    注意这**不等于**节点活着: 节点执行长任务期间不会去长轮询, 此值会变旧。
    判活请用 :func:`_liveness`。
    """
    last_seen = int(node.get("last_seen_ms") or 0)
    return bool(last_seen) and (int(time.time() * 1000) - last_seen) <= ONLINE_WINDOW_MS


def _last_node_activity_ms(node_id: str) -> int:
    """节点**自己**最近一次产生审计事件的时刻(如 task.settled)。

    这是比长轮询更硬的活性证据: 它由节点侧产生, 证明它真的在干活。
    """
    if not node_id:
        return 0
    for entry in read_audit(limit=NODE_ACTIVITY_SCAN, node_id=node_id):
        if str(entry.get("actor") or "") != "node":
            continue
        try:
            return int(entry.get("ts_ms") or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _liveness(node: dict[str, Any]) -> tuple[str, str]:
    """判节点活性, 返回 (状态, 说明)。

    三态而不是布尔, 因为"忙碌"和"离线"是完全不同的两件事, 混为一谈会让人
    去重启一台正在正常干活的机器(2026-09-03 实际发生过: 控制面显示离线,
    同期审计里 15 个任务全部 exit_code=0 正常结算)。
    """
    polling = _polling(node)
    activity_ms = _last_node_activity_ms(str(node.get("node_id") or ""))
    fresh_activity = bool(activity_ms) and (
        int(time.time() * 1000) - activity_ms
    ) <= NODE_ACTIVITY_WINDOW_MS

    if polling:
        return "在线", f"最近上报 {_ago(node.get('last_seen_ms'))}"
    if fresh_activity:
        return (
            "在线(执行中)",
            f"未在轮询但仍在结算任务, 最近一次 {_ago(activity_ms)}"
            " —— 忙碌不是离线, 不要据此重启节点",
        )
    return "离线", f"最近上报 {_ago(node.get('last_seen_ms'))}"


def _alive(node: dict[str, Any]) -> bool:
    """节点是否活着(在线或忙碌)。取代旧的 ``_online`` 判活语义。"""
    return not _liveness(node)[0].startswith("离线")


def _ago(timestamp_ms: Any) -> str:
    try:
        value = int(timestamp_ms or 0)
    except (TypeError, ValueError):
        return "—"
    if not value:
        return "—"
    seconds = max(0, int(time.time() * 1000) - value) // 1000
    if seconds < 90:
        return f"{seconds} 秒前"
    if seconds < 5400:
        return f"{seconds // 60} 分钟前"
    if seconds < 172800:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def _profile(node: dict[str, Any]) -> dict[str, Any]:
    """节点当前权限档。

    控制端进程比本文件旧时(改了控制面但还没重启 dashboard), 响应里不会带
    ``permissions``。那种情况下就地按同一个函数算一遍, 而不是谎报"无授权"。
    """
    profile = node.get("permissions")
    if isinstance(profile, dict):
        return profile
    return permission_profile(node)


#: `--command` 没指定启动器时, 按这个顺序从节点已批准的启动器里挑第一个。
COMMAND_PROGRAM_PREFERENCE = ("pwsh", "powershell", "cmd", "bash")


def _approved_programs(node: dict[str, Any], mode: str) -> list[str] | None:
    """节点自己上报的、该档已批准的启动器 program_id; 节点没上报时返回 None(未知)。

    节点在 ``status.grants[]`` 里按档上报 ``approved_program_ids``。控制端据此在
    下发前就挡掉一定会被节点拒绝的启动器, 而不是发下去才收到一句
    ``program is not approved by this grant``。
    """
    status = node.get("status")
    grants = status.get("grants") if isinstance(status, dict) else None
    if not isinstance(grants, list):
        return None
    found = False
    approved: list[str] = []
    for grant in grants:
        if not isinstance(grant, dict) or grant.get("mode") != mode:
            continue
        if grant.get("enabled") is False or grant.get("paused") is True:
            continue
        ids = grant.get("approved_program_ids")
        if not isinstance(ids, list):
            continue
        found = True
        for program_id in ids:
            if isinstance(program_id, str) and program_id not in approved:
                approved.append(program_id)
    return approved if found else None


def _permission_line(node: dict[str, Any]) -> str:
    profile = _profile(node)
    granted = profile.get("granted_modes") or []
    level = str(profile.get("level") or "none")
    detail = " + ".join(MODE_LABEL.get(mode, mode) for mode in granted) or "无"
    return f"{LEVEL_LABEL.get(level, level)} [{detail}]"


# ── 命令组 ────────────────────────────────────────────────────────────────────

@click.group("desktop-node")
def cmd_desktop_node() -> None:
    """桌面派 Remote Execute Node: 看权限档 / 下发任务 / 一键收权 / 查审计。

    \b
    omni desktop-node ls                     # 有哪些节点, 各自开到什么档
    omni desktop-node status <node>          # 单节点权限档 + 在线情况 + 下一步
    omni desktop-node exec <node> -- git --version
    omni desktop-node exec <node> -m trusted-exec-l2 -c "npm run build"
    omni desktop-node revoke <node> --all    # 一键收回全部权限
    omni desktop-node audit                  # 谁在什么时候开/关了什么
    omni desktop-node bootstrap              # 临时首次注册窗口: 开着没有/开/关
    """


@cmd_desktop_node.command("ls")
@click.option("--json", "as_json", is_flag=True, help="输出原始 JSON。")
def cmd_desktop_node_ls(as_json: bool) -> None:
    """列出已配对的桌面节点及其当前权限档。"""
    nodes = _fetch_nodes()
    if not as_json:
        _warn_if_bootstrap_open()
    if as_json:
        click.echo(json.dumps(nodes, ensure_ascii=False, indent=2))
        return
    if not nodes:
        click.echo("还没有配对过任何桌面节点。")
        click.echo("在桌面客户端 设置 → 安全 → Remote Execute Node 里配对一次。")
        return
    for node in nodes:
        node_id = str(node.get("node_id") or "?")
        # ● 在线(在轮询) / ◐ 忙碌(未轮询但仍在结算任务) / ○ 离线
        state, _detail = _liveness(node)
        mark = "●" if state == "在线" else ("○" if state.startswith("离线") else "◐")
        click.echo(f"{mark} {node_id}  {node.get('label') or ''}")
        click.echo(f"    权限档  {_permission_line(node)}")
        click.echo(f"    最近上报 {_ago(node.get('last_seen_ms'))}"
                   f"    配对于 {_ago(node.get('paired_at_ms'))}")


@cmd_desktop_node.command("status")
@click.argument("node_id")
@click.option("--json", "as_json", is_flag=True)
def cmd_desktop_node_status(node_id: str, as_json: bool) -> None:
    """看一个节点的权限档、在线情况, 以及要开满还差什么。"""
    node = _resolve_node(node_id)
    if as_json:
        click.echo(json.dumps(node, ensure_ascii=False, indent=2))
        return
    _warn_if_bootstrap_open()
    profile = _profile(node)
    click.echo(f"节点     {node.get('node_id')}  ({node.get('label') or '未命名'})")
    if node.get("bootstrap_enrolled"):
        click.echo("注册方式 经 bootstrap 临时窗口(非 operator 开窗) —— 见 audit")
    state, detail = _liveness(node)
    click.echo(f"在线     {state}（{detail}）")
    click.echo(f"权限档   {_permission_line(node)}")
    click.echo(f"能力集   {', '.join(profile.get('capabilities') or []) or '无'}")
    for mode in NODE_MODES:
        grant = (node.get("grants") or {}).get(mode) if isinstance(node.get("grants"), dict) else None
        if grant is None and mode == MODE_READONLY and node.get("grant_id"):
            grant = {"grant_id": node["grant_id"], "generation": node.get("generation")}
        label = MODE_LABEL.get(mode, mode)
        if grant:
            programs = _approved_programs(node, mode)
            program_bit = f"  启动器 {', '.join(programs) or '无'}" if programs is not None else ""
            click.echo(f"  · {label}  已授权  grant={grant.get('grant_id')} gen={grant.get('generation')}{program_bit}")
        else:
            click.echo(f"  · {label}  未授权")
    missing = profile.get("missing_modes") or []
    if missing and not node.get("revoked"):
        click.echo()
        click.echo("要开满还差: " + "、".join(MODE_LABEL.get(mode, mode) for mode in missing))
        click.echo("授予只能从节点侧发起(控制端无法给自己提权):")
        click.echo("  桌面客户端 → 设置 → 安全 → Remote Execute Node → 「一键开启所有权限」")


@cmd_desktop_node.command("exec")
@click.argument("node_id")
@click.argument("argv", nargs=-1)
@click.option("-m", "--mode", "mode", type=click.Choice(list(NODE_MODES)), default=MODE_READONLY,
              show_default=True, help="用哪一档授权跑。默认只读档 —— 不改默认值。")
@click.option("-p", "--program", default=None,
              help="节点上已批准的启动器 program_id(看 `status` 的「启动器」)。"
                   "默认: 只读档 git; 执行档 -c 从节点已批准的 pwsh > powershell > cmd > bash 里挑。")
@click.option("-c", "--command", "command", default=None,
              help="执行档便捷写法: 一整条命令行, 由已批准的 shell 启动器执行。")
@click.option("--cwd", "cwd_relative", default=".", show_default=True,
              help="相对授权根目录的工作目录。")
@click.option("--timeout-ms", type=int, default=None,
              help="单次任务上限。默认: 只读档 30000, 执行档 600000; 执行档可覆盖到 1800000。")
@click.option("--task-id", default=None,
              help="可选的固定 task id；仅执行档使用，用于把命令内的短期资源授权绑定到同一签名任务。")
@click.option("--watch", is_flag=True,
              help="执行档: 主进程退出后留下后代并持续监控, 不随任务结束杀掉(Unity launch 用)。")
@click.option("--capability", "capabilities", multiple=True,
              help="额外申请的能力(可重复)。默认只申请该档必需的那些。")
@click.option("--wait/--no-wait", default=True, show_default=True, help="等回执还是发完就走。")
@click.option("--wait-s", type=int, default=None,
              help="等回执最长多少秒。默认=任务 timeout + 90 秒。")
@click.option("--json", "as_json", is_flag=True, help="输出原始 JSON 回执。")
def cmd_desktop_node_exec(
    node_id: str,
    argv: tuple[str, ...],
    mode: str,
    program: str | None,
    command: str | None,
    cwd_relative: str,
    timeout_ms: int | None,
    task_id: str | None,
    watch: bool,
    capabilities: tuple[str, ...],
    wait: bool,
    wait_s: int | None,
    as_json: bool,
) -> None:
    """在桌面节点上跑一条命令并取回输出。

    \b
    omni desktop-node exec desktop-1af4 -- git --version
    omni desktop-node exec desktop-1af4 -m trusted-exec-l2 -c "npm run build"
    omni desktop-node exec desktop-1af4 -m trusted-exec-l2 -p cmd -- /C echo hi

    这不是交互式 shell: 无 stdin、无流式输出, 结果是一次性缓冲的 stdout/stderr。
    节点每 20 秒轮询一次, 所以从下发到开跑最多有一个轮询窗口的延迟。
    """
    node = _resolve_node(node_id)
    resolved_id = str(node["node_id"])
    profile = _profile(node)
    if mode not in (profile.get("granted_modes") or []):
        raise click.ClickException(
            f"节点没有 {MODE_LABEL.get(mode, mode)} 授权 —— 控制端不能自己补上。\n"
            f"  当前权限档: {_permission_line(node)}\n"
            "  去桌面客户端 设置 → 安全 → Remote Execute Node → 「一键开启所有权限」"
        )
    if task_id is not None:
        if mode != MODE_TRUSTED_EXEC:
            raise click.ClickException("--task-id 只在执行档 trusted-exec-l2 下有意义")
        if not task_id or len(task_id) > 96 or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in task_id
        ):
            raise click.ClickException("--task-id 仅接受 1–96 位字母、数字、连字符或下划线")

    approved = _approved_programs(node, mode)
    if command is not None and argv:
        raise click.ClickException("--command 和 -- argv 二选一, 不要同时给")
    if command is not None:
        if mode != MODE_TRUSTED_EXEC:
            raise click.ClickException("--command 只在执行档(trusted-exec-l2)下有意义")
        if program is None:
            # 节点上报了已批准列表: 按 pwsh > powershell > cmd > bash 挑第一个已批准的,
            # 不发注定被拒的任务(2026-10-13 IGO1ZHW 只批准 cmd/powershell, 发 pwsh 被
            # policy_rejected)。节点没上报列表: 用 Windows 自带、一键批准必在集合里的
            # powershell 5.1 —— pwsh 是可选安装, 没装 7 的机器上一律 policy_rejected。
            if approved is None:
                program = "powershell"
            else:
                program = next((p for p in COMMAND_PROGRAM_PREFERENCE if p in approved), None)
                if program is None:
                    raise click.ClickException(
                        f"节点 {node.get('label') or resolved_id} 的{MODE_LABEL.get(mode, mode)}授权"
                        f"只批准了启动器: {', '.join(approved) or '无'}; 其中没有 -c 能用的 shell。\n"
                        "  改用 `-p <启动器> -- <参数>`"
                    )
                if program != COMMAND_PROGRAM_PREFERENCE[0]:
                    click.echo(
                        f"ℹ 节点没批准 {COMMAND_PROGRAM_PREFERENCE[0]}, 本次 -c 用已批准的 {program}"
                        f"（已批准: {', '.join(approved)}）。",
                        err=True,
                    )
        if program in {"pwsh", "powershell"}:
            task_argv = ["-NoProfile", "-NonInteractive", "-Command", command]
        elif program == "cmd":
            task_argv = ["/C", command]
        elif program == "bash":
            task_argv = ["-lc", command]
        else:
            raise click.ClickException(f"--command 不知道怎么喂给启动器 {program}, 请改用 -- argv")
    elif program is None:
        # `-- git --version`: argv[0] 就是节点上那个 program_id, 剩下的才是参数。
        if not argv:
            raise click.ClickException("没给命令。用 `-- git --version` 或 `-c \"...\"`")
        program = argv[0]
        task_argv = list(argv[1:])
    else:
        # 显式给了 --program, 那么 -- 后面整段都是参数。
        task_argv = list(argv)

    if approved is not None and program not in approved:
        # 节点会以 policy_rejected 拒掉; 下发前就说清楚该换成哪个。
        node_name = node.get("label") or resolved_id
        if not approved:
            raise click.ClickException(f"节点 {node_name} 的{MODE_LABEL.get(mode, mode)}授权没有批准任何启动器。")
        suggest = next((p for p in COMMAND_PROGRAM_PREFERENCE if p in approved), approved[0])
        raise click.ClickException(
            f"节点 {node_name} 的{MODE_LABEL.get(mode, mode)}授权"
            f"没有批准启动器 {program}; 已批准: {', '.join(approved)}。\n"
            f"  改用 `-p {suggest}`" + (" (或 -c 不带 -p, 自动挑已批准的 shell)" if mode == MODE_TRUSTED_EXEC else "")
        )

    if watch and mode != MODE_TRUSTED_EXEC:
        raise click.ClickException("--watch 只在执行档 trusted-exec-l2 下有意义")
    if timeout_ms is not None and timeout_ms < 1_000:
        raise click.ClickException("--timeout-ms 至少 1000")
    ceiling = MODE_MAX_TIMEOUT_MS[mode]
    if timeout_ms is not None and timeout_ms > ceiling:
        raise click.ClickException(
            f"--timeout-ms {timeout_ms} 超过 {mode} 上限 {ceiling}"
        )
    effective_timeout = timeout_ms or MODE_DEFAULT_TIMEOUT_MS[mode]
    ttl_ms = min(max(120_000, effective_timeout + 90_000), MODE_MAX_TTL_MS[mode])
    effective_wait_s = wait_s if wait_s is not None else (effective_timeout // 1000) + 90
    requested = sorted(set(capabilities) | set(MODE_REQUIRED_CAPABILITIES[mode]))
    payload = {
        "mode": mode,
        "program_id": program,
        "argv": task_argv,
        "cwd_relative": cwd_relative,
        "timeout_ms": effective_timeout,
        "ttl_ms": ttl_ms,
        "watch": watch,
        "requested_capabilities": requested,
    }
    if task_id is not None:
        payload["task_id"] = task_id

    live_state, live_detail = _liveness(node)
    if live_state.startswith("离线"):
        click.echo(f"⚠ 节点{live_detail}, 可能不在线; 任务会排队等它回来。", err=True)
    elif live_state != "在线":
        # 忙碌: 节点活着但正在跑别的任务, 本任务会排在后面。这不是故障,
        # 不要因此重启节点或判它死亡。
        click.echo(f"ℹ 节点{live_detail}; 本任务会排在当前任务之后执行。", err=True)

    status, body = _call("POST", f"/{resolved_id}/tasks", payload)
    if status != 202 or not body.get("ok"):
        raise click.ClickException(f"下发失败 (HTTP {status}): {body.get('error') or body}")
    task_id = str(body["task_id"])
    click.echo(f"已下发 task={task_id}  mode={mode}  program={program}  argv={task_argv}", err=True)
    if not wait:
        click.echo(f"不等回执。取结果: omni desktop-node task {task_id}", err=True)
        return

    deadline = time.time() + max(1, effective_wait_s)
    task: dict[str, Any] = {}
    while True:
        task = _get_task(task_id)
        if str(task.get("status")) in TERMINAL_STATUS:
            break
        if time.time() >= deadline:
            click.echo(f"等 {effective_wait_s} 秒还没回执, 任务仍是 {task.get('status')}。"
                       f" 稍后 `omni desktop-node task {task_id}` 再看。", err=True)
            raise SystemExit(75)  # EX_TEMPFAIL
        time.sleep(1.0)

    raise SystemExit(_print_task(task, as_json=as_json))


def _get_task(task_id: str) -> dict[str, Any]:
    status, body = _call("GET", f"/tasks/{task_id}")
    if status != 200 or not body.get("ok"):
        raise click.ClickException(f"取任务失败 (HTTP {status}): {body.get('error') or body}")
    task = body.get("task")
    if not isinstance(task, dict):
        raise click.ClickException("控制端返回的任务格式不对")
    return task


def _print_task(task: dict[str, Any], *, as_json: bool) -> int:
    """打印回执, 返回该给 CLI 用的退出码(透传远端 exit code)。"""
    if as_json:
        click.echo(json.dumps(task, ensure_ascii=False, indent=2))
    receipt = task.get("receipt") if isinstance(task.get("receipt"), dict) else {}
    status = str(task.get("status") or "unknown")
    if not as_json:
        if receipt.get("stdout"):
            click.echo(receipt["stdout"], nl=False)
        if receipt.get("stderr"):
            click.echo(receipt["stderr"], nl=False, err=True)
        leftover = receipt.get("leftover_processes")
        leftover_bit = f" leftover={leftover}" if leftover else ""
        click.echo(
            f"[{status}] exit={receipt.get('exit_code')} "
            f"termination={receipt.get('termination')} "
            f"duration={receipt.get('duration_ms')}ms "
            f"queue={receipt.get('queue_latency_ms')}ms"
            f"{leftover_bit}",
            err=True,
        )
    if status == "completed":
        return int(receipt.get("exit_code") or 0)
    return int(receipt.get("exit_code") or 1) or 1


@cmd_desktop_node.command("task")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True)
def cmd_desktop_node_task(task_id: str, as_json: bool) -> None:
    """看一条已下发任务的状态与回执。"""
    raise SystemExit(_print_task(_get_task(task_id), as_json=as_json))


@cmd_desktop_node.command("revoke")
@click.argument("node_id")
@click.option("-m", "--mode", type=click.Choice(list(NODE_MODES)), default=None,
              help="只收回这一档, 另一档不受影响。")
@click.option("--all", "revoke_all", is_flag=True, help="整个节点吊销(全部档位一起收回)。")
@click.option("-y", "--yes", is_flag=True, help="不要二次确认。")
def cmd_desktop_node_revoke(node_id: str, mode: str | None, revoke_all: bool, yes: bool) -> None:
    """一键收回权限。

    收紧从不需要对方同意, 所以这一步控制端单方面就能做完 —— 节点下一次轮询会拿到
    410 并自行终止在跑的任务。``--all`` 之后节点要重新配对才能再上线。
    """
    if bool(mode) == revoke_all:
        raise click.ClickException("给 --mode <档位> 或 --all, 二选一")
    node = _resolve_node(node_id)
    resolved_id = str(node["node_id"])
    target = "全部档位(整节点吊销, 需重新配对)" if revoke_all else MODE_LABEL.get(mode, str(mode))
    if not yes:
        click.confirm(f"收回 {resolved_id} 的 {target}?", abort=True)
    path = f"/{resolved_id}/revoke" if revoke_all else f"/{resolved_id}/revoke/{mode}"
    status, body = _call("POST", path)
    if status != 200 or not body.get("ok"):
        raise click.ClickException(f"收回失败 (HTTP {status}): {body.get('error') or body}")
    click.echo(f"已收回: {target}")
    remaining = body.get("remaining")
    if remaining is not None:
        click.echo("剩余授权: " + ("、".join(MODE_LABEL.get(m, m) for m in remaining) or "无"))


@cmd_desktop_node.command("audit")
@click.option("--limit", type=int, default=30, show_default=True)
@click.option("--node", "node_filter", default=None, help="只看某个节点(完整 node_id)。")
@click.option("--json", "as_json", is_flag=True)
def cmd_desktop_node_audit(limit: int, node_filter: str | None, as_json: bool) -> None:
    """谁在什么时候开了/关了什么, 下发过哪些任务。"""
    query = f"/audit?limit={max(1, limit)}"
    if node_filter:
        query += f"&node_id={urllib.parse.quote(node_filter)}"
    status, body = _call("GET", query)
    if status == 404:
        # 控制面已经会写审计了, 但在跑的那个 dashboard 进程还是旧代码、没有这个端点。
        # 账本是本机文件, 直接读 —— 不为了看一眼日志去重启用户正在用的服务。
        entries = read_audit(max(1, limit), node_id=node_filter or "")
        click.echo("（控制端进程还没加载 /audit 端点, 已直接读本机账本）", err=True)
    elif status != 200 or not body.get("ok"):
        raise click.ClickException(f"读审计失败 (HTTP {status}): {body.get('error') or body}")
    else:
        entries = body.get("entries") or []
    if as_json:
        click.echo(json.dumps(entries, ensure_ascii=False, indent=2))
        return
    if not entries:
        click.echo("审计账本还是空的(它从本次改动之后才开始记)。")
        return
    for entry in entries:
        head = f"{entry.get('ts')}  {entry.get('event'):<18} {str(entry.get('node_id'))[:24]}"
        click.echo(head)
        detail = {
            key: value
            for key, value in entry.items()
            if key not in {"ts", "ts_ms", "event", "node_id", "actor"}
        }
        click.echo(f"    actor={entry.get('actor')}  {json.dumps(detail, ensure_ascii=False)}")


def _bootstrap_status() -> dict[str, Any]:
    """当前 bootstrap 窗口状态。控制端进程还没加载这个端点时按「关着」处理。"""
    status, body = _call("GET", "/bootstrap")
    if status == 404:
        return {"open": False, "unsupported": True}
    if status != 200 or not body.get("ok"):
        raise click.ClickException(f"读 bootstrap 状态失败 (HTTP {status}): {body.get('error') or body}")
    window = body.get("bootstrap")
    return window if isinstance(window, dict) else {"open": False}


def _remaining(window: dict[str, Any]) -> str:
    seconds = max(0, int(window.get("remaining_ms") or 0)) // 1000
    return f"{seconds // 60} 分 {seconds % 60} 秒" if seconds else "已到期"


def _warn_if_bootstrap_open() -> None:
    """开着窗口就在每一次列表/状态输出前说一句 —— 降级不该是隐形的。"""
    try:
        window = _bootstrap_status()
    except click.ClickException:
        return
    if not window.get("open"):
        return
    click.echo(
        f"⚠ bootstrap 窗口开着(安全性降级中): 未注册的机器现在可以自行开配对窗口。"
        f" 剩余 {_remaining(window)}。立刻关: omni desktop-node bootstrap --close",
        err=True,
    )


@cmd_desktop_node.command("bootstrap")
@click.option("--open", "do_open", is_flag=True, help="开启临时首次注册窗口。")
@click.option("--close", "do_close", is_flag=True, help="立刻关闭窗口。")
@click.option("--minutes", type=int, default=None,
              help=f"窗口时长, 上限 {BOOTSTRAP_MAX_MINUTES} 分钟。默认 {BOOTSTRAP_DEFAULT_MINUTES}。")
@click.option("--reason", default="", help="为什么要开(进审计账本)。")
@click.option("--json", "as_json", is_flag=True)
def cmd_desktop_node_bootstrap(
    do_open: bool,
    do_close: bool,
    minutes: int | None,
    reason: str,
    as_json: bool,
) -> None:
    """临时首次注册窗口: 让一台从没注册过的机器能自己开配对窗口。

    \b
    omni desktop-node bootstrap                          # 现在开着没有, 还剩多久
    omni desktop-node bootstrap --open --minutes 30 --reason "给 .247 首次注册"
    omni desktop-node bootstrap --close                  # 立刻关

    平时控制端只认两种主体: 本机(回环/网关私密)的 operator, 和已注册节点用自己的
    私钥自证。首次注册两个都不成立 —— 节点自证只对「已经认识的节点」有效 —— 所以
    第一次仍然要 operator 开窗。这个窗口就是那一步够不着时的**临时替代**:

    - 默认关, 不开就完全不存在这条路;
    - 有期限, 到点自动失效(--minutes 给时长, 上限见 --minutes 的帮助);
    - 谁开的、为什么开、期间谁注册了, 全进 `omni desktop-node audit`;
    - 一条命令立刻关。

    窗口开着也**不会**放宽别的东西: 不能给已有节点提权、不能让被吊销的节点自己
    回来、不给任何能力。它只是把「拿到 8 位配对码」这一步的门槛从 operator 降到
    「已经通过网关的调用方」, 码仍然要由节点侧认领并自行登记 grant。
    """
    if do_open and do_close:
        raise click.ClickException("--open 和 --close 二选一")

    if do_close:
        status, body = _call("POST", "/bootstrap/close")
        if status == 404:
            click.echo("控制端进程没有这个端点 —— 那它也没有窗口可开。当前等于关着。")
            return
        if status != 200 or not body.get("ok"):
            raise click.ClickException(f"关闭失败 (HTTP {status}): {body.get('error') or body}")
        window = body.get("bootstrap") or {}
        if as_json:
            click.echo(json.dumps(window, ensure_ascii=False, indent=2))
            return
        click.echo("已关闭 bootstrap 窗口。" if window.get("was_open") else "窗口本来就是关的。")
        return

    if do_open:
        payload = {"minutes": minutes or BOOTSTRAP_DEFAULT_MINUTES, "reason": reason}
        status, body = _call("POST", "/bootstrap", payload)
        if status == 404:
            # 控制面代码改了但进程还是改动之前起的 —— 说清楚要做什么, 别只丢一个 404。
            raise click.ClickException(
                "控制端进程还没加载 /bootstrap 端点: dashboard 是这次改动之前启动的。\n"
                "  让它生效: omni dashboard restart (只重启 8210, 不碰 ccdaemon/8201)。\n"
                "  用户正在用页面时, 这一下会让打开的页面短暂断一下 —— 先说一声再做。"
            )
        if status != 200 or not body.get("ok"):
            raise click.ClickException(f"开启失败 (HTTP {status}): {body.get('error') or body}")
        window = body.get("bootstrap") or {}
        if as_json:
            click.echo(json.dumps(window, ensure_ascii=False, indent=2))
            return
        click.echo(f"⚠ bootstrap 窗口已开启 —— 安全性临时降级, 剩余 {_remaining(window)}")
        click.echo(f"  到期时间 {time.strftime('%H:%M:%S', time.localtime(int(window.get('expires_at_ms') or 0) / 1000))}"
                   " (到点自动失效, 不需要人管)")
        click.echo("  这期间: 任何已通过网关的调用方都能开一个 5 分钟配对窗口。")
        click.echo("  用完立刻关: omni desktop-node bootstrap --close")
        return

    window = _bootstrap_status()
    if as_json:
        click.echo(json.dumps(window, ensure_ascii=False, indent=2))
        return
    if window.get("unsupported"):
        click.echo("控制端进程还没加载 /bootstrap 端点(dashboard 是改动之前启动的)。")
        click.echo("窗口按「关着」算 —— 端点不存在就没有这条路。")
        return
    if not window.get("open"):
        click.echo("bootstrap 窗口: 关(默认状态, 首次注册只能由 operator 开窗)")
        return
    click.echo(f"⚠ bootstrap 窗口: 开 —— 安全性降级中, 剩余 {_remaining(window)}")
    click.echo(f"  开启者 {window.get('opened_by') or '?'}")
    if window.get("reason"):
        click.echo(f"  理由   {window['reason']}")
    enrolled = window.get("enrolled") or []
    if enrolled:
        click.echo(f"  期间注册 {len(enrolled)} 次:")
        for entry in enrolled:
            click.echo(f"    · {entry.get('node_id')}  {MODE_LABEL.get(entry.get('mode'), entry.get('mode'))}")
    else:
        click.echo("  期间还没有节点注册")
    click.echo("  立刻关: omni desktop-node bootstrap --close")


@cmd_desktop_node.command("pairing")
def cmd_desktop_node_pairing() -> None:
    """开一个 5 分钟一次性配对窗口(节点侧拿这个码来登记一档授权)。"""
    status, body = _call("POST", "/pairing/open")
    if status != 200 or not body.get("ok"):
        raise click.ClickException(f"开配对窗口失败 (HTTP {status}): {body.get('error') or body}")
    click.echo(f"配对码     {body.get('code')}")
    click.echo(f"Pairing ID {body.get('pairing_id')}")
    click.echo(f"控制端指纹 {body.get('controller_fingerprint')}")
    click.echo("有效 5 分钟, 一次性。桌面客户端「一键开启所有权限」会自己开窗口, 无需手动。")
