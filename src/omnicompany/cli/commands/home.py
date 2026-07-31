# [OMNI] origin=claude-code-worker domain=omnicompany/cli ts=2026-07-25T00:00:00+08:00 type=cli status=active
# [OMNI] material_id="material:omnicompany.cli.commands.home.py"
# [OMNI] summary="omni home 命令组:主控机经 SSH 远控家中已引导好的 Windows 主机(status/ssh/run/omni/dashboard/init)。SSH 仅密钥、BatchMode、accept-new;run 默认 deny+白名单、高危需 --confirm;每次 run 落审计 JSONL。"
# [OMNI] why="多机互联 R1-R4 收敛版要求:用户在另一台电脑不必再操作第二个 agent。这条 CLI 是 remote_nodes_core 冻住 API 之上的薄包装——只 import 不改 core。绝不给 agent 无限制 shell:只读/状态类命令无确认可跑,删除/移动/批量写/开代理/读私密目录等高危命令必须 --confirm,每次 run 都落审计。host/user 未填时给清晰指引(先 init+盒子引导+回填 config/home.yaml),不静默失败。"
# [OMNI] tags=cli,home,remote-nodes,ssh,acl,audit,deny-by-default
"""omni home —— 远控家中 Windows 主机的命令组(remote_nodes_core 之上的薄包装)。

设计要点(见模块顶部 [OMNI] 注释):
- SSH 【仅密钥】登录:私钥取自 :func:`remote_nodes_core.ssh_private_key_path`,
  ``-o BatchMode=yes``(绝不弹密码框)、``-o StrictHostKeyChecking=accept-new``。
- ``omni home run`` 对 agent 自动执行【默认 deny + 白名单】:只读/状态类命令可无确认跑;
  高危命令(删除/移动/批量写/开代理/读私密目录等)【必须 --confirm 才跑】,
  【绝不】给 agent 无限制 shell。
- 每次 run(无论 auto 还是 confirmed)都落审计 JSONL。
- 路径一律走 :mod:`remote_nodes_core` / :mod:`core.config`,禁硬编码深度。
- host/user 未填时给清晰指引,不静默失败或乱猜。
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from omnicompany.core.config import omni_workspace_root
from omnicompany.dashboard.controlplane import remote_nodes_core
from omnicompany.dashboard.controlplane.remote_nodes_core import (
    BOOTSTRAP_PATH,
    callback_base_url,
    detect_lan_ip,
    ensure_ssh_keypair,
    home_config_path,
    home_data_dir,
    kit_dir,
    list_nodes,
    load_home_config,
    read_staged_tunnel_pubkey,
    render_bootstrap_bat,
    save_home_config,
    ssh_private_key_path,
)


# ── 传输 helper(模块级、可被 monkeypatch)─────────────────────────────────────

def _ssh(
    cfg: dict,
    remote_cmd: str,
    *,
    interactive: bool = False,
    timeout: int | None = None,
) -> tuple[int, str, str]:
    """经 SSH 在远端跑 ``remote_cmd``,返回 ``(returncode, stdout, stderr)``。

    SSH 【仅密钥】登录:私钥取自 :func:`remote_nodes_core.ssh_private_key_path`,
    固定 ``-o BatchMode=yes``(绝不弹密码框)、``-o StrictHostKeyChecking=accept-new``、
    ``-o ConnectTimeout=10``。

    ``interactive=True`` 时不 capture、inherit stdio(给人开 shell 用),
    返回 ``(rc, "", "")``。非 interactive 用 ``subprocess.run`` capture_output,
    返回 ``(returncode, stdout, stderr)``。

    经反向隧道(``cfg["via_tunnel"]`` 默认 True):目标 = ``127.0.0.1``, 端口 =
    ``cfg["tunnel_port"]``, 用户 = ``cfg["user"]``(盒子用户);用主控私钥 ``-i <priv>``。
    直连模式(``via_tunnel=False``):保留原 ``user@host`` 直连。

    直连模式 host/user 为空抛清晰 :class:`RuntimeError`(指引先 init + 盒子引导 + 回填 config)。
    隧道模式只要求 user 非空(host 恒为 127.0.0.1, 但隧道得先由盒子 ssh -R 连上)。
    """
    via_tunnel = bool(cfg.get("via_tunnel") if cfg.get("via_tunnel") is not None else True)
    user = str(cfg.get("user") or "").strip()
    priv = ssh_private_key_path(cfg)
    if via_tunnel:
        host = "127.0.0.1"
        port = int(cfg.get("tunnel_port") or 8223)
        if not user:
            raise RuntimeError(
                "home 节点 user 还没填,无法经隧道 SSH: 盒子引导回报会把盒子用户回填到 "
                "config/home.yaml; 若为空说明盒子还没装好/没回报, 请先在盒子双击 "
                "bootstrap.bat, 回报成功后再跑 `omni home authorize-tunnel` 放行隧道, "
                "最后 `omni home status` 验证。"
            )
    else:
        host = str(cfg.get("host") or "").strip()
        port = int(cfg.get("port") or 22)
        if not host or not user:
            raise RuntimeError(
                "home 节点 host/user 还没填,无法 SSH: 请先在主控机跑 `omni home init` "
                "生成密钥与引导包,把 bootstrap.bat 拷到家中盒子双击装好, "
                "再把盒子的 host/user 回填到 config/home.yaml 后重试。"
            )
    args = [
        "ssh", "-i", str(priv), "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        f"{user}@{host}",
    ]
    if remote_cmd:
        args.append(remote_cmd)

    if interactive:
        # 开交互 shell 给人用:不 capture、inherit stdio。
        proc = subprocess.run(args)
        return (proc.returncode, "", "")

    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return (proc.returncode, proc.stdout or "", proc.stderr or "")


# ── 白名单(默认 deny)──────────────────────────────────────────────────────────

# 只读/状态类命令前缀(小写,前后空格已规整)。匹配时对命令 strip + lower 后比前缀。
SAFE_COMMAND_PREFIXES: tuple[str, ...] = (
    "omni ",            # omni 自身受控命令(状态/查询类)
    "git fetch",
    "git pull",
    "git status",
    "git log",
    "nvidia-smi",
    "ipconfig",
    "hostname",
    "whoami",
    "type ",            # Windows type(读文件)
    "echo ",
    "dir ",
)

# 高危命令关键字(出现即视为非白名单 → 需 --confirm)。即使 SAFE 前缀命中也兜底拦截。
DANGER_KEYWORDS: tuple[str, ...] = (
    "rm ", "del ", "rmdir", "move ", "format ", "mkfs",
    "remove-item", "move-item", "copy-item", "set-content",
    "netsh", "proxy", "new-item", "start-process",
    "invoke-expression", "iex ", "curl ", "wget ",
    "shutdown", "restart", "reg ", "regedit",
    "takeown", "icacls", "cipher",
)


def _is_safe_command(cmd: str) -> bool:
    """规范化(strip + lower)后判命令是否在白名单(只读/状态类)。

    命中任一高危关键字(删除/移动/批量写/开代理/读私密目录等)即使前缀命中也判
    为非白名单 → 需 --confirm。
    """
    norm = (cmd or "").strip().lower()
    if not norm:
        return False
    # 含 home_node_status.ps1 的(状态采集脚本)直接放行。
    if "home_node_status.ps1" in norm:
        return True
    # 高危关键字兜底:即使前缀命中也拦。
    for kw in DANGER_KEYWORDS:
        if kw in norm:
            return False
    return any(norm.startswith(p) for p in SAFE_COMMAND_PREFIXES)


# ── 审计 ───────────────────────────────────────────────────────────────────────

def _audit(cfg: dict, cmd: str, permission: str, returncode: int) -> None:
    """追加一行审计 JSON 到 ``home_data_dir()/"audit.jsonl"``。

    字段: ts(iso-utc)/node_id/host/cmd/permission(auto|confirmed|omni-remote)/returncode。
    原子追加(建父目录),失败不致命。
    """
    try:
        path: Path = home_data_dir() / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "node_id": str(cfg.get("node_id") or ""),
            "host": str(cfg.get("host") or ""),
            "cmd": cmd,
            "permission": permission,
            "returncode": int(returncode),
        }
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        # 审计失败不致命:远控本身不应因审计写盘失败而中断。
        return


# ── 命令组 ─────────────────────────────────────────────────────────────────────

@click.group("home")
def cmd_home() -> None:
    """远控家中已引导好的 Windows 主机(SSH 仅密钥)。

    子命令: init 引导准备 / status 查状态 / ssh 开交互 shell /
    run 受控跑命令(默认 deny+白名单) / omni 在远端跑 omni / dashboard 打开远端驾驶舱。
    """


@cmd_home.command("init")
def home_init() -> None:
    """主控机引导准备:生成 SSH 密钥对 + 探测 LAN IP + 写 config/home.yaml 骨架 + 渲染 bootstrap.bat 到 kit。"""
    cfg = load_home_config()
    # 1. 生成(或确认已有)SSH 密钥对。
    priv = ensure_ssh_keypair(cfg)
    # 2. advertise_host 空则自动探测本机出口 LAN IP(引导包回连主控用)。
    if not str(cfg.get("advertise_host") or "").strip():
        cfg["advertise_host"] = detect_lan_ip()
    # 3. 写 config/home.yaml 骨架(人后续把盒子 host/user 填进来)。
    save_home_config(cfg)
    # 4. 渲染单文件 bootstrap.bat 到 kit/(只内联公钥,绝不内联私钥)。
    bat_text = render_bootstrap_bat(cfg)
    kit = kit_dir()
    kit.mkdir(parents=True, exist_ok=True)
    bat_path = kit / "bootstrap.bat"
    bat_path.write_text(bat_text, encoding="utf-8")

    base = callback_base_url(cfg)
    download_url = base.rstrip("/") + BOOTSTRAP_PATH
    click.echo("home 节点引导准备完成:")
    click.echo(f"  SSH 私钥路径     : {priv}")
    click.echo(f"  SSH 公钥路径     : {priv}.pub")
    click.echo(f"  home 配置路径    : {home_config_path()}")
    click.echo(f"  bootstrap.bat    : {bat_path}")
    click.echo(f"  下载网址(盒子访问): {download_url}")
    click.echo("")
    click.echo("下一步:")
    click.echo("  1) 在家中盒子上用浏览器打开上面的下载网址,拿到 bootstrap.bat;")
    click.echo("     (或直接把 kit/bootstrap.bat 拷到盒子)")
    click.echo("  2) 在盒子上双击 bootstrap.bat(自提权 + 装 OpenSSH + 仅密钥登录 + 防火墙只放 LocalSubnet);")
    click.echo("  3) 装好后把盒子的 host(LAN IP 或 mesh 名)与 user 填进 config/home.yaml;")
    click.echo("  4) 回主控机跑 `omni home status` 验证回连。")


@cmd_home.command("status")
def home_status() -> None:
    """查家中盒子状态:已填 host/user 则 SSH 跑状态脚本并打印 node_status yaml;否则列已注册节点 + 回填指引。"""
    cfg = load_home_config()
    host = str(cfg.get("host") or "").strip()
    user = str(cfg.get("user") or "").strip()
    if not host or not user:
        click.echo("home 节点 host/user 还没填,无法 SSH 查状态。")
        nodes = list_nodes()
        if nodes:
            click.echo("已注册的远程节点:")
            for n in nodes:
                click.echo(f"  - {n.get('node_id','?')}: host={n.get('lan_ip') or n.get('host') or '?'} "
                           f"user={n.get('ssh_user') or '?'} last_seen={n.get('last_seen','?')}")
        else:
            click.echo("(尚无已注册节点。)")
        click.echo("请先跑 `omni home init`,盒子双击 bootstrap.bat 装好后,把 host/user 回填到 "
                   "config/home.yaml,再跑 `omni home status`。")
        return
    install_dir = str(cfg.get("install_dir") or "").replace("\\", "/").rstrip("/")
    node_id = str(cfg.get("node_id") or "home-main")
    status_ps1 = f"{install_dir}/scripts/home_node_status.ps1"
    status_yaml = f"{install_dir}/data/node_status/{node_id}.yaml"
    # 先跑状态采集脚本(刷新 yaml),再 type 出来打印。两条分开 SSH,与远端默认 shell 无关。
    rc1, out1, err1 = _ssh(cfg, f'powershell -NoProfile -File "{status_ps1}" -NodeId "{node_id}"')
    if rc1 != 0:
        click.echo(f"状态采集脚本失败(rc={rc1}): {err1.strip() or out1.strip()}", err=True)
    rc2, out2, err2 = _ssh(cfg, f'type "{status_yaml}"')
    if rc2 != 0:
        click.echo(f"读取状态文件失败(rc={rc2}): {err2.strip() or out2.strip()}", err=True)
        if out2:
            click.echo(out2, nl=False)
        return
    click.echo(out2, nl=False)
    if not out2.endswith("\n"):
        click.echo("")


@cmd_home.command("ssh")
def home_ssh() -> None:
    """开一个交互 SSH shell 直连家中盒子(给人用,不审计、不白名单)。"""
    cfg = load_home_config()
    try:
        rc, _, _ = _ssh(cfg, "", interactive=True)
    except RuntimeError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    sys.exit(rc)


@cmd_home.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("cmd", nargs=-1, required=True)
@click.option("--confirm", is_flag=True, help="确认跑高危命令(默认 deny+白名单)。")
def home_run(cmd: tuple[str, ...], confirm: bool) -> None:
    """受控在远端跑命令。【默认 deny+白名单】:只读/状态类可无确认跑;高危需 --confirm。每次都落审计。"""
    cfg = load_home_config()
    cmd_str = " ".join(cmd).strip()
    if not cmd_str:
        raise click.UsageError("未提供命令。")

    safe = _is_safe_command(cmd_str)
    if safe:
        permission = "auto"
    elif confirm:
        permission = "confirmed"
    else:
        click.echo(
            f"高危命令需 --confirm 才跑(白名单默认 deny): {cmd_str}\n"
            f"  若确认要跑,加 --confirm 重试。绝不给 agent 无限制 shell。",
            err=True,
        )
        sys.exit(1)

    try:
        rc, out, err = _ssh(cfg, cmd_str)
    except RuntimeError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    _audit(cfg, cmd_str, permission, rc)
    if out:
        click.echo(out, nl=False)
    if err:
        click.echo(err, nl=False, err=True)
    sys.exit(rc)


@cmd_home.command(
    "omni",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("args", nargs=-1)
def home_omni(args: tuple[str, ...]) -> None:
    """在远端盒子跑 omni(cd <install_dir> && omni <args...>),落审计(permission=omni-remote)。"""
    cfg = load_home_config()
    install_dir = str(cfg.get("install_dir") or "").replace("\\", "/").rstrip("/")
    remote = f'cd "{install_dir}" && omni ' + " ".join(args).strip()
    remote = remote.rstrip()
    try:
        rc, out, err = _ssh(cfg, remote)
    except RuntimeError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    _audit(cfg, remote, "omni-remote", rc)
    if out:
        click.echo(out, nl=False)
    if err:
        click.echo(err, nl=False, err=True)
    sys.exit(rc)


@cmd_home.group("dashboard")
def home_dashboard() -> None:
    """远端驾驶舱:打开家中盒子上的 dashboard。"""


@home_dashboard.command("open")
def home_dashboard_open() -> None:
    """打开远端 dashboard(http://<host>:<dashboard_port>),打不开只打印不致命。"""
    cfg = load_home_config()
    host = str(cfg.get("host") or "").strip()
    port = int(cfg.get("dashboard_port") or 8210)
    if not host:
        click.echo("home 节点 host 还没填,无法打开远端 dashboard。请先 init + 盒子引导 + "
                   "回填 config/home.yaml 的 host。", err=True)
        sys.exit(1)
    url = f"http://{host}:{port}"
    click.echo(f"远端 dashboard: {url}")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception as e:
        click.echo(f"(webbrowser 打开失败,非致命: {e};请手动访问上面的网址。)", err=True)


# ── 反向隧道:授权盒子公钥进主控 authorized_keys ──────────────────────────────

def _tunnel_fingerprint(pubkey: str) -> str:
    """取公钥主体(去掉 comment)的前缀指纹, 供人核对。空串返回 '(空)'。"""
    if not pubkey:
        return "(空)"
    parts = pubkey.strip().split()
    if len(parts) < 2:
        return pubkey[:20]
    return parts[1][:20]


@cmd_home.command("authorize-tunnel")
def home_authorize_tunnel() -> None:
    """一次性把盒子隧道公钥(受限项)加进主控 administrators_authorized_keys + 放行盒子来源入站 22。

    需管理员权限运行(脚本会自检并提示以管理员身份跑)。盒子装好引导包后会回报其隧道公钥,
    本命令读出它并打印需在【主控机提权】运行的安装命令(指向 scripts/home_authorize_tunnel.ps1)。
    只需在主控跑一次;之后盒子隧道会自动连上。
    """
    pubkey = read_staged_tunnel_pubkey()
    cfg = load_home_config()
    tunnel_port = int(cfg.get("tunnel_port") or 8223)
    if not pubkey:
        click.echo("盒子还没回报隧道公钥,无法授权: 请先在盒子双击 bootstrap.bat 引导包, "
                    "等它注册回主控(回报里会带回盒子生成的隧道公钥), 再回来跑本命令。")
        click.echo("(若引导包跑过但此处仍空, 多半是 register 被一次性 nonce 校验挡了 "
                    "— 重渲引导包再跑一次即可。) ")
        sys.exit(1)

    click.echo(f"暂存的盒子隧道公钥指纹(前缀): {_tunnel_fingerprint(pubkey)}")
    click.echo(f"主控 loopback 转发口: 127.0.0.1:{tunnel_port}")
    try:
        repo_root = str(omni_workspace_root())
    except Exception:
        repo_root = "."
    ps1_path = f"{repo_root}/scripts/home_authorize_tunnel.ps1"
    # 在普通(非管理员) shell 里粘贴这条命令, 会弹 UAC 拉起一个管理员 PowerShell
    # 跑 home_authorize_tunnel.ps1。路径无空格故 ArgumentList 内不再嵌套引号, 避免
    # cmd->powershell 多层引号转义踩坑(历史上栽过)。
    install_cmd = (
        'powershell -NoProfile -ExecutionPolicy Bypass -Command "'
        "Start-Process powershell -Verb RunAs -ArgumentList "
        f"'-NoProfile -ExecutionPolicy Bypass -File {ps1_path}'"
        '"'
    )
    click.echo("")
    click.echo("需要在主控机【以管理员身份】运行下面这条命令(只跑一次):")
    click.echo(install_cmd)
    click.echo("")
    click.echo("说明: 该脚本会把盒子隧道密钥以受限项(restrict,permitlisten=127.0.0.1:%d)加进 "
               "主控 administrators_authorized_keys, 并放行盒子来源 IP 入站 22; "
               "之后盒子隧道会在数秒内自动连上, 回主控跑 `omni home status` 验证。"
               % tunnel_port)


@cmd_home.command("tunnel-status")
def home_tunnel_status() -> None:
    """探测主控 loopback 转发口是否在监听(在=盒子反向隧道已连上)。"""
    cfg = load_home_config()
    tunnel_port = int(cfg.get("tunnel_port") or 8223)
    host = "127.0.0.1"
    alive = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.5)
            s.connect((host, tunnel_port))
            alive = True
    except OSError:
        alive = False
    if alive:
        click.echo(f"反向隧道已连上: {host}:{tunnel_port} 在监听(盒子 ssh -R 已建立)。")
        click.echo("可直接 `omni home status` / `omni home ssh` 经隧道连盒子。")
    else:
        click.echo(f"反向隧道未连上: {host}:{tunnel_port} 无人监听。")
        click.echo("可能原因: 1) 盒子还没跑引导包; 2) 盒子隧道公钥还没授权进主控 "
                   "(跑 `omni home authorize-tunnel`); 3) 盒子侧计划任务 OmniHomeTunnel 未起。")


__all__ = ["cmd_home"]
