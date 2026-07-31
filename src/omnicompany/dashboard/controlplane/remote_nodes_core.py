# [OMNI] origin=claude-code-worker domain=dashboard/controlplane ts=2026-07-25T00:00:00+08:00 type=infra
# [OMNI] material_id="material:dashboard.controlplane.remote_nodes_core.py"
# [OMNI] summary="远程节点一键引导基础件: home 配置读写 + 节点注册表 + 状态文件 + LAN IP 探测 + SSH 密钥对管理 + 单文件 bootstrap.bat 渲染。CLI 与 dashboard 路由共用的冻住 API。"
# [OMNI] why="多机互联近期收敛版(R1-R4)要求: 主控机生成单文件 bootstrap.bat, 拷到家中 Windows 5070 主机双击即装好 SSH+omnicompany 并注册回来。bootstrap.bat 只内联公钥与公开配置, 绝不内联私钥/token/口令; 盒侧 SSH 仅密钥登录、防火墙只放本机局域网段、绝不暴露公网。路径一律走 core/config.py 的 omni_workspace_root()/resolve_service_data_dir(), 禁硬编码深度。"
# [OMNI] tags=remote-nodes,bootstrap,ssh,mesh,home-node,config,state,atomic-write
"""remote_nodes_core.py — 远程节点(家中电脑等)一键引导的基础件。

本模块是 CLI(`omni home ...`)与 dashboard 路由(`remote_nodes.py`)共用的纯逻辑层:
配置读写、节点注册表、状态文件、LAN IP 探测、SSH 密钥对管理、单文件 bootstrap.bat
渲染。公开 API 已冻住, 后续 CLI/路由只 import 不改签名。

设计要点(见模块顶部 [OMNI] 注释与 bootstrap_ref):
- 路径解析走 :func:`omni_workspace_root` / :func:`resolve_service_data_dir`, 不依赖 cwd,
  不硬编码 ``Path(__file__).parents[N]``。
- bootstrap.bat 是【纯函数渲染】: 给定 cfg 返回文本字符串, 不写盘、不联网; 公钥/回连地址/
  git 源在【生成时】内联进 .bat。
- bootstrap.bat 【只内联公钥和公开配置】,【绝不】内联任何私钥/token/口令/cookie; 渲染产物
  里不含 ``PRIVATE KEY`` 字样。
- 盒侧 SSH 配置(由 .bat 在盒子上执行)成【仅密钥登录、禁密码】、【防火墙只放 LocalSubnet】、
  【绝不】暴露公网。
- 引导【幂等】(可重复跑), 带【DryRun】(只打印不执行)。
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root, resolve_service_data_dir

# ── 冻住的常量 ──────────────────────────────────────────────────────────────

DEFAULT_HOME_CONFIG: dict[str, Any] = {
    "host": "",            # 盒子 SSH 主机(LAN IP 或 mesh 名), 装好后再填
    "port": 22,
    "user": "",            # 盒子 SSH 用户名, 装好后再填
    "label": "home-main",
    "node_id": "home-main",
    "ssh_key_env": "OMNI_HOME_SSH_KEY",  # 指向【私钥】路径的环境变量名
    "advertise_host": "",  # 本机 LAN IP; 空=自动探测; 引导包回连用
    "dashboard_port": 8210,
    "git_remote": "https://git-host.example.com/user/omnifactory-private.git",
    "install_dir": "C:/workspace/omnicompany",
    "tunnel_port": 8223,   # 主控 loopback 反向 SSH 隧道转发口(盒子 ssh -R 回连到这里)
    "master_user": "",    # 主控机 sshd 用户名; 渲染时为空则 getpass.getuser() 填
    "master_ssh_port": 22, # 主控机 sshd 端口(盒子 ssh -R 连它)
    "via_tunnel": True,   # omni home 是否经反向隧道连盒子(默认是; 盒子只能主动连出)
}

REGISTER_PATH: str = "/api/remote-nodes/register"
REPORT_PATH: str = "/api/remote-nodes/report"
BOOTSTRAP_PATH: str = "/api/remote-nodes/bootstrap.bat"
CONNECT_PATH: str = "/api/remote-nodes/connect.bat"


# ── 路径 ────────────────────────────────────────────────────────────────────

def home_config_path() -> Path:
    """``<root>/config/home.yaml`` — home 节点配置(人可编辑)。"""
    return omni_workspace_root() / "config" / "home.yaml"


def home_data_dir() -> Path:
    """``data/services/home/`` — home 节点运行态目录(自动建)。"""
    return resolve_service_data_dir("home")


def kit_dir() -> Path:
    """``data/services/home/kit/`` — 引导包暂存目录(生成/下载的 bootstrap.bat 等)。"""
    return home_data_dir() / "kit"


def state_file() -> Path:
    """``data/services/home/home.json`` — home 节点本地状态(配置快照 + 计数)。"""
    return home_data_dir() / "home.json"


def nodes_file() -> Path:
    """``data/services/home/nodes.json`` — 已注册的远程节点表({node_id: info})。"""
    return home_data_dir() / "nodes.json"


# ── 配置读写 ─────────────────────────────────────────────────────────────────

def load_home_config() -> dict[str, Any]:
    """读 yaml 合并 DEFAULT_HOME_CONFIG; yaml 缺失/缺字段都回落默认, 不抛。

    返回的 dict 一定包含 DEFAULT_HOME_CONFIG 的全部键(yaml 里缺的用默认补齐,
    yaml 里有但类型不符的也回落默认, 保证调用方拿到的字段集稳定)。
    """
    cfg = dict(DEFAULT_HOME_CONFIG)
    path = home_config_path()
    try:
        text = path.read_text(encoding="utf-8")
        data = _load_yaml(text)
    except OSError:
        data = {}
    if isinstance(data, dict):
        for k, default in DEFAULT_HOME_CONFIG.items():
            val = data.get(k, default)
            if isinstance(default, str) and isinstance(val, str):
                cfg[k] = val
            elif isinstance(default, bool) and isinstance(val, bool):
                cfg[k] = val
            elif isinstance(default, int) and isinstance(val, int) and not isinstance(val, bool):
                cfg[k] = val
            else:
                cfg[k] = default  # 类型不符 / 缺失 → 默认
    return cfg


def save_home_config(cfg: dict[str, Any]) -> None:
    """写 config/home.yaml(UTF-8 无 BOM); 只写 DEFAULT_HOME_CONFIG 已知的键。"""
    out = {k: cfg.get(k, default) for k, default in DEFAULT_HOME_CONFIG.items()}
    text = _dump_home_yaml(out)
    path = home_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── 状态文件 ─────────────────────────────────────────────────────────────────

def load_state() -> dict[str, Any]:
    """读 home.json; 缺/坏返回 {}。"""
    path = state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(st: dict[str, Any]) -> None:
    """原子写 home.json(.tmp.replace), 自动建父目录。"""
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ── 配对 nonce(防同网段他人乱注册)────────────────────────────────────────────
# nonce 只是"配对令牌"(明文内联进 bat, 主控只存 hash), 非长期机密;
# state 里暂存明文只为跨 render 复用, 用后即删。

def _sha256_hex(s: str) -> str:
    """对明文做 sha256 hex; nonce 校验时比 hash 不比明文。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def ensure_pending_nonce() -> str:
    """返回一个待用的一次性 nonce(明文)。

    state 里已有未用的就复用其明文, 否则新生成并存其 hash + 明文。明文只用于
    内联进 bat; 主控只靠 hash 校验。用 :func:`verify_nonce` 标记已用(一次性)。
    """
    st = load_state()
    if st.get("pending_nonce_hash") and not st.get("pending_nonce_used"):
        if st.get("pending_nonce_plain"):
            return str(st["pending_nonce_plain"])
    nonce = secrets.token_urlsafe(24)
    st["pending_nonce_plain"] = nonce
    st["pending_nonce_hash"] = _sha256_hex(nonce)
    st["pending_nonce_used"] = False
    write_state(st)
    return nonce

def verify_nonce(nonce: str) -> bool:
    """校验盒子回报的 nonce: hash 相等且未用。成功即标记已用(一次性)并删明文。"""
    if not nonce:
        return False
    st = load_state()
    if st.get("pending_nonce_used"):
        return False
    if st.get("pending_nonce_hash") != _sha256_hex(nonce):
        return False
    st["pending_nonce_used"] = True
    st.pop("pending_nonce_plain", None)
    write_state(st)
    return True


# ── 盒子隧道公钥暂存(盒子回报, 主控提权后写进 authorized_keys)─────────────────

# Low-privilege report tokens. Plaintext is returned once by /register; only
# a SHA-256 digest is persisted on the controller. These tokens authorize
# status/log reporting only and never grant command execution or SSH access.
def issue_report_token(node_id: str) -> str:
    """Issue/rotate a low-privilege report token for one node."""
    token = secrets.token_urlsafe(32)
    st = load_state()
    hashes = st.get("report_token_hashes")
    if not isinstance(hashes, dict):
        hashes = {}
    hashes[str(node_id)] = _sha256_hex(token)
    st["report_token_hashes"] = hashes
    write_state(st)
    return token


def verify_report_token(node_id: str, token: str) -> bool:
    """Verify a report token in constant time."""
    if not node_id or not token:
        return False
    hashes = load_state().get("report_token_hashes")
    if not isinstance(hashes, dict):
        return False
    expected = str(hashes.get(str(node_id)) or "")
    if not expected:
        return False
    return hmac.compare_digest(expected, _sha256_hex(token))


def stage_tunnel_pubkey(pubkey: str) -> None:
    """暂存盒子回报的隧道公钥(待主控提权 install)。空串也写入(清空)。"""
    st = load_state()
    st["staged_tunnel_pubkey"] = (pubkey or "").strip()
    write_state(st)

def read_staged_tunnel_pubkey() -> str:
    """读已暂存的盒子隧道公钥; 无则空串。"""
    return str(load_state().get("staged_tunnel_pubkey") or "").strip()


# ── 节点注册表 ───────────────────────────────────────────────────────────────

def load_nodes() -> dict[str, dict]:
    """{node_id: info}; 缺/坏返回 {}。"""
    path = nodes_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_node(node_id: str, info: dict) -> None:
    """upsert 一个节点, 补 last_seen(ISO UTC), 原子写 nodes.json。"""
    path = nodes_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes = load_nodes()
    record = dict(nodes.get(node_id, {}))
    record.update(info)
    record["node_id"] = node_id
    record["last_seen"] = _now_iso()
    nodes[node_id] = record
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def public_node_record(node_id: str, info: dict[str, Any]) -> dict[str, Any]:
    """Return the public node view, defensively removing secret fields."""
    rec = {
        key: value
        for key, value in dict(info).items()
        if key not in {"report_token", "report_token_hash", "nonce", "pending_nonce_plain"}
    }
    rec.setdefault("node_id", node_id)
    return rec


def list_nodes() -> list[dict]:
    """Return public node records without report tokens or token hashes."""
    return [public_node_record(node_id, info) for node_id, info in load_nodes().items()]


# ── LAN IP 探测 ───────────────────────────────────────────────────────────────

def detect_lan_ip() -> str:
    """UDP connect 探测本机出口 LAN IP; 失败返回 ``"127.0.0.1"``, 绝不抛。

    用一个连到公网目标的 UDP socket 让 OS 选出口接口, ``getsockname`` 即本机在该
    路由上的 LAN IP; 不真正发包。家用网络下即 LAN IP。
    """
    for target in ("8.8.8.8", "114.114.114.114", "223.5.5.5"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((target, 80))
            ip = s.getsockname()[0]
            s.close()
            if ip:
                return ip
        except OSError:
            try:
                s.close()
            except OSError:
                pass
            continue
    return "127.0.0.1"


# ── SSH 密钥对 ───────────────────────────────────────────────────────────────

def ssh_private_key_path(cfg: dict) -> Path:
    """取 cfg["ssh_key_env"] 环境变量指向的私钥路径; env 缺则默认
    ``home_data_dir()/"ssh"/"id_ed25519"``。
    """
    env_name = str(cfg.get("ssh_key_env") or "OMNI_HOME_SSH_KEY")
    raw = os.environ.get(env_name, "").strip()
    if raw:
        return Path(raw)
    return home_data_dir() / "ssh" / "id_ed25519"


def ensure_ssh_keypair(cfg: dict) -> Path:
    """私钥不存在则 ``ssh-keygen -t ed25519 -N ""`` 生成(建父目录); 返回私钥路径。

    幂等: 私钥已存在直接返回, 不覆盖。
    """
    priv = ssh_private_key_path(cfg)
    if priv.exists():
        return priv
    priv.parent.mkdir(parents=True, exist_ok=True)
    # -N "" 空口令; -f 指定输出; -C 注释; -t ed25519
    cmd = [
        "ssh-keygen", "-t", "ed25519", "-N", "",
        "-C", "omni-home@" + str(cfg.get("node_id") or "home-main"),
        "-f", str(priv),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise RuntimeError(
            f"生成 SSH 密钥对失败(需要 ssh-keygen 在 PATH): {e}"
        ) from e
    return priv


def read_public_key(cfg: dict) -> str:
    """读 ``<priv>.pub`` 文本(strip); 不存在抛清晰 RuntimeError(提示先跑 omni home init)。

    返回单行公钥(如 ``ssh-ed25519 AAAA... omni-home@home-main``)。
    """
    priv = ssh_private_key_path(cfg)
    pub = priv.with_suffix(priv.suffix + ".pub") if priv.suffix else priv.with_name(priv.name + ".pub")
    if not pub.exists():
        raise RuntimeError(
            f"找不到公钥文件 {pub}: 请先在主控机跑 `omni home init` 生成密钥对。"
        )
    try:
        return pub.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise RuntimeError(f"读取公钥 {pub} 失败: {e}") from e


def callback_base_url(cfg: dict) -> str:
    """``http://<advertise_host or detect_lan_ip()>:<dashboard_port>``。

    引导包回连主控机的地址。advertise_host 为空时自动探测本机出口 LAN IP。
    """
    host = str(cfg.get("advertise_host") or "").strip() or detect_lan_ip()
    port = int(cfg.get("dashboard_port") or 8210)
    return f"http://{host}:{port}"


# ── bootstrap.bat 渲染 ─────────────────────────────────────────────────────────

def render_bootstrap_bat(cfg: dict) -> str:
    """返回单文件一键 bootstrap.bat 的完整文本。

    【纯函数】: 给定 cfg 返回 str, 不写盘、不联网。公钥通过 :func:`read_public_key`
    从已生成的密钥对里读出来内联。bat 里【只内联公钥和公开配置】, 不含任何私钥/token。
    """
    pubkey = read_public_key(cfg)
    callback = callback_base_url(cfg)
    git_remote = str(cfg.get("git_remote") or DEFAULT_HOME_CONFIG["git_remote"])
    install_dir = str(cfg.get("install_dir") or DEFAULT_HOME_CONFIG["install_dir"])
    node_id = str(cfg.get("node_id") or DEFAULT_HOME_CONFIG["node_id"])
    label = str(cfg.get("label") or DEFAULT_HOME_CONFIG["label"])
    ssh_user = str(cfg.get("user") or "")
    ssh_port = int(cfg.get("port") or 22)
    # 主控机自身 IP: 盒子据此在其防火墙上显式放行主控入站 SSH(盒子与主控可能不同 /24, LocalSubnet 会漏)。
    master_ip = callback.split("://", 1)[-1].rsplit(":", 1)[0]
    # 反向隧道相关: 主控用户/主控 sshd 端口/主控 loopback 转发口 + 一次性配对 nonce。
    master_user = str(cfg.get("master_user") or "") or getpass.getuser()
    master_ssh_port = int(cfg.get("master_ssh_port") or 22)
    tunnel_port = int(cfg.get("tunnel_port") or DEFAULT_HOME_CONFIG["tunnel_port"])
    nonce = ensure_pending_nonce()

    # PS 脚本是【静态】常量, 配置全走 $env:OMNI_BOOTSTRAP_*, 渲染时只 base64 编码,
    # 避开一切引号/转义问题(历史上栽过 PowerShell 改写内联 JSON 引号)。
    ps_b64 = base64.b64encode(_BOOTSTRAP_PS.encode("utf-16-le")).decode("ascii")

    # 注意: .bat 本体必须保持【纯 ASCII】——cmd.exe 在中文 Windows 上按 GBK 解析批处理,
    # UTF-8 多字节(中文注释)会破坏行边界, 把 -ExecutionPolicy / if /i 等拆成碎命令执行。
    # 中文说明全部放进 base64 编码的 PowerShell 主体里(那里安全), .bat 文本只用英文注释。
    #
    # 执行方式: PS 本体较长, base64 已超过 Windows 单条命令行 ~32767 字符上限, 不能再用
    # `powershell -EncodedCommand <b64>`(会 "The system cannot execute the specified program")。
    # 改成: 把 b64 切成短行 echo 写进临时 .b64 文件(纯 ASCII, 每行远低于 8K 行长上限),
    # 再用一条 powershell 读出→去空白→base64 解码成 UTF-16-LE→写 .ps1→`-File` 执行。
    # 这样彻底绕开命令行长度天花板, 且 .bat 仍是单文件(b64 内联在下面, 纯 ASCII)。
    b64_chunk = 2000  # 每行 < 8K 安全; 纯 base64 字符, 无多字节/转义风险
    b64_lines = [ps_b64[i:i + b64_chunk] for i in range(0, len(ps_b64), b64_chunk)]
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "REM [OMNI] omnicompany home node bootstrap - single file, idempotent, supports -DryRun.",
        "REM [OMNI] Only PUBLIC config is inlined below; private key / token are NEVER inlined.",
        "REM [OMNI] This .bat MUST stay pure ASCII (cmd.exe mis-parses UTF-8 under GBK codepage).",
        "",
        "REM --- self-elevate to admin (net session succeeds only when already admin) ---",
        "net session >nul 2>&1 || (powershell -NoProfile -Command \"Start-Process '%~f0' -Verb RunAs -ArgumentList '%*'\" & exit /b)",
        "",
        "REM --- DryRun: first arg -DryRun means print-only, no system changes ---",
        "set OMNI_BOOTSTRAP_DRYRUN=0",
        "if /i \"%~1\"==\"-DryRun\" set OMNI_BOOTSTRAP_DRYRUN=1",
        "",
        "REM --- public config inlined at generation time (only these; no secret) ---",
        f"set OMNI_BOOTSTRAP_PUBKEY={_bat_escape(pubkey)}",
        f"set OMNI_BOOTSTRAP_CALLBACK={_bat_escape(callback)}",
        f"set OMNI_BOOTSTRAP_REGISTER_PATH={_bat_escape(REGISTER_PATH)}",
        f"set OMNI_BOOTSTRAP_REPORT_PATH={_bat_escape(REPORT_PATH)}",
        f"set OMNI_BOOTSTRAP_GIT_REMOTE={_bat_escape(git_remote)}",
        f"set OMNI_BOOTSTRAP_INSTALL_DIR={_bat_escape(install_dir)}",
        f"set OMNI_BOOTSTRAP_NODE_ID={_bat_escape(node_id)}",
        f"set OMNI_BOOTSTRAP_LABEL={_bat_escape(label)}",
        f"set OMNI_BOOTSTRAP_SSH_USER={_bat_escape(ssh_user)}",
        f"set OMNI_BOOTSTRAP_SSH_PORT={_bat_escape(str(ssh_port))}",
        f"set OMNI_BOOTSTRAP_MASTER_IP={_bat_escape(master_ip)}",
        f"set OMNI_BOOTSTRAP_MASTER_USER={_bat_escape(master_user)}",
        f"set OMNI_BOOTSTRAP_MASTER_SSH_PORT={_bat_escape(str(master_ssh_port))}",
        f"set OMNI_BOOTSTRAP_TUNNEL_PORT={_bat_escape(str(tunnel_port))}",
        f"set OMNI_BOOTSTRAP_NONCE={_bat_escape(nonce)}",
        "",
        "REM --- write embedded PS (base64, ASCII) to a temp .b64 in chunks, then decode+run ---",
        "set \"OMNI_BOOTSTRAP_B64=%TEMP%\\omni-bootstrap-%RANDOM%.b64\"",
        "set \"OMNI_BOOTSTRAP_PS1=%TEMP%\\omni-bootstrap-%RANDOM%.ps1\"",
        "if exist \"%OMNI_BOOTSTRAP_B64%\" del /q \"%OMNI_BOOTSTRAP_B64%\"",
    ]
    # 切成短行 echo 进 .b64 文件(首行 > 覆盖, 其余 >> 追加)。纯 base64, 全 ASCII。
    for idx, chunk in enumerate(b64_lines):
        redir = ">" if idx == 0 else ">>"
        lines.append(f'echo {chunk}{redir}"%OMNI_BOOTSTRAP_B64%"')
    lines += [
        "powershell -NoProfile -Command \""
        "$b=(Get-Content -Raw -LiteralPath $env:OMNI_BOOTSTRAP_B64) -replace '\\s',''; "
        "$d=[Convert]::FromBase64String($b); "
        "$bom=[byte[]](0xFF,0xFE); "
        "[IO.File]::WriteAllBytes($env:OMNI_BOOTSTRAP_PS1,$bom + $d); "
        "Remove-Item -LiteralPath $env:OMNI_BOOTSTRAP_B64 -Force -ErrorAction SilentlyContinue"
        "\"",
        "powershell -NoProfile -ExecutionPolicy Bypass -File \"%OMNI_BOOTSTRAP_PS1%\"",
        "if exist \"%OMNI_BOOTSTRAP_PS1%\" del /q \"%OMNI_BOOTSTRAP_PS1%\"",
        "",
        "REM --- keep this window open so the result stays readable (skipped under -DryRun) ---",
        "if \"%OMNI_BOOTSTRAP_DRYRUN%\"==\"0\" pause",
        "",
    ]
    return "\n".join(lines)


# ── 私有 helper ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_yaml(text: str) -> Any:
    """yaml.safe_load 封一层(避免在模块顶层 import yaml 失败时连 import 都炸)。"""
    import yaml
    return yaml.safe_load(text)


def _dump_home_yaml(cfg: dict[str, Any]) -> str:
    """序列化 home.yaml; 用 default_flow_style=False 保持可读。"""
    import yaml
    return yaml.safe_dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _bat_escape(value: str) -> str:
    """转义 .bat `set VAR=value` 行里的 batch 元字符, 防止公钥/路径里的特殊字符截断命令。

    `& | < > ^` 前加 `^`; `%` 写成 `%%`(bat 文件内字面 %)。公钥正常不含这些字符,
    这是防御性处理。
    """
    out = value.replace("^", "^^").replace("&", "^&").replace("|", "^|").replace("<", "^<").replace(">", "^>")
    out = out.replace("%", "%%")
    return out


# ── 盒侧 PowerShell 引导脚本(静态常量, 配置走 env)──────────────────────────
# 注意: 此字符串里【绝不】出现 "PRIVATE KEY" 字样; 只处理公钥(OMNI_BOOTSTRAP_PUBKEY)。
# 全程幂等(装前先查在不在), 支持 DryRun(只 Write-Host 不改系统)。

_BOOTSTRAP_PS = r'''
$ErrorActionPreference = 'Stop'
$dry = "$env:OMNI_BOOTSTRAP_DRYRUN" -eq '1'
function WStep($m) { Write-Host "[omni-bootstrap] $m" }
$homeDir = Join-Path $env:ProgramData 'omnicompany-home'
$bootstrapLog = Join-Path $env:TEMP 'omni-bootstrap.log'
if (-not $dry) {
    if (-not (Test-Path $homeDir)) { New-Item -ItemType Directory -Force -Path $homeDir | Out-Null }
    $bootstrapLog = Join-Path $homeDir 'bootstrap.log'
}
if ($dry) { WStep "DRY RUN: no system changes will be made." }
try { Start-Transcript -Path $bootstrapLog -Append -ErrorAction SilentlyContinue | Out-Null } catch {}

$pubkey      = $env:OMNI_BOOTSTRAP_PUBKEY
$callback    = $env:OMNI_BOOTSTRAP_CALLBACK
$regPath     = $env:OMNI_BOOTSTRAP_REGISTER_PATH
$reportPath  = $env:OMNI_BOOTSTRAP_REPORT_PATH
$gitRemote   = $env:OMNI_BOOTSTRAP_GIT_REMOTE
$installDir  = $env:OMNI_BOOTSTRAP_INSTALL_DIR
$nodeId      = $env:OMNI_BOOTSTRAP_NODE_ID
$label       = $env:OMNI_BOOTSTRAP_LABEL
$sshUser     = $env:OMNI_BOOTSTRAP_SSH_USER
$sshPort     = $env:OMNI_BOOTSTRAP_SSH_PORT
$masterIp    = $env:OMNI_BOOTSTRAP_MASTER_IP
$masterUser   = $env:OMNI_BOOTSTRAP_MASTER_USER
$masterSshPort = $env:OMNI_BOOTSTRAP_MASTER_SSH_PORT
$tunnelPort   = $env:OMNI_BOOTSTRAP_TUNNEL_PORT
$nonce        = $env:OMNI_BOOTSTRAP_NONCE
if (-not $sshUser) { $sshUser = $env:USERNAME }
if (-not $sshPort) { $sshPort = '22' }
if (-not $masterUser) { $masterUser = $env:USERNAME }
if (-not $masterSshPort) { $masterSshPort = '22' }
if (-not $tunnelPort) { $tunnelPort = '8223' }
WStep "config: callback=$callback regPath=$regPath node=$nodeId installDir=$installDir sshUser=$sshUser sshPort=$sshPort masterIp=$masterIp masterUser=$masterUser masterSshPort=$masterSshPort tunnelPort=$tunnelPort nonce=$nonce dry=$dry"

# ---- a0) 确保 OpenSSH Client(盒子要 ssh -R 回连主控)----
try {
    $clientCmd = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $clientCmd) {
        $clientCap = 'OpenSSH.Client~~~~0.0.1.0'
        if ($dry) { WStep "DRY: Add-WindowsCapability $clientCap (OpenSSH Client)" }
        else {
            Add-WindowsCapability -Online -Name $clientCap | Out-Null
            WStep "已安装 OpenSSH.Client"
        }
    } else { WStep "OpenSSH.Client 已就绪" }
} catch { WStep "OpenSSH Client 安装/查询失败(非致命): $($_.Exception.Message)" }

# ---- a1) 生成盒子隧道密钥对(只此盒用, 私钥永不出盒; 幂等 + dry 门控)----
# 私钥只内联进 PS 本体【不外泄】; 这里生成的 $tunnelPub 才会随注册 body 回传主控。
$tunnelKey = Join-Path $homeDir 'tunnel_id_ed25519'
$tunnelPub = ''
try {
    if (-not (Test-Path $homeDir)) {
        if ($dry) { WStep "DRY: create $homeDir" }
        else { New-Item -ItemType Directory -Force -Path $homeDir | Out-Null }
    }
    if (-not (Test-Path $tunnelKey)) {
        if ($dry) { WStep "DRY: 生成盒子隧道密钥 $tunnelKey" }
        else {
            # 空口令必须经 cmd /c 传 -P "" —— PowerShell 原生传空字符串会被吞, ssh-keygen 会退回去
            # 交互式问口令(无人值守下挂死/失败), 导致盒子隧道公钥回传为空。经 cmd /c 由 cmd 解析 "" 最稳。
            $kgCmd = "ssh-keygen -t ed25519 -P `"`" -f `"$tunnelKey`" -C omni-home-tunnel"
            Start-Process cmd -ArgumentList '/c',$kgCmd -Wait -NoNewWindow -ErrorAction SilentlyContinue
            icacls $tunnelKey /inheritance:r /grant 'SYSTEM:(F)' /grant 'BUILTIN\Administrators:(F)' | Out-Null
            WStep "已生成盒子隧道密钥(私钥仅 SYSTEM/Administrators 可读)"
        }
    } else { WStep "盒子隧道密钥已存在(复用)" }
    if (Test-Path "$tunnelKey.pub") { $tunnelPub = (Get-Content "$tunnelKey.pub" -Raw).Trim() }
} catch { WStep "盒子隧道密钥生成失败(非致命): $($_.Exception.Message)" }

function Add-AuthorizedKey($file, $key) {
    if (-not $key) { return }
    $dir = Split-Path $file -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $existing = ''
    if (Test-Path $file) { $existing = Get-Content $file -Raw -ErrorAction SilentlyContinue }
    if ($existing -and ($existing.Contains($key))) { return }
    Add-Content -Path $file -Value $key -Encoding utf8
}

# ---- a) 装/启用 OpenSSH Server ----
$capName = 'OpenSSH.Server~~~~0.0.1.0'
try {
    $cap = Get-WindowsCapability -Online -Name $capName -ErrorAction Stop
    if ($cap.State -ne 'Installed') {
        if ($dry) { WStep "DRY: Add-WindowsCapability $capName" }
        else { Add-WindowsCapability -Online -Name $capName | Out-Null; WStep "已安装 OpenSSH.Server" }
    } else { WStep "OpenSSH.Server 已安装" }
} catch { WStep "查询 OpenSSH capability 失败(非致命): $($_.Exception.Message)" }
if (-not $dry) {
    try { Start-Service sshd -ErrorAction SilentlyContinue } catch {}
    try { Set-Service -Name sshd -StartupType Automatic -ErrorAction SilentlyContinue } catch {}
}

# ---- b) 仅密钥登录(禁密码)----
$adminKeys = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
$userKeys  = Join-Path $env:USERPROFILE '.ssh\authorized_keys'
$sshdCfg   = Join-Path $env:ProgramData 'ssh\sshd_config'
if ($pubkey) {
    if ($dry) {
        WStep "DRY: 写公钥到 $adminKeys 与 $userKeys"
    } else {
        Add-AuthorizedKey $adminKeys $pubkey
        Add-AuthorizedKey $userKeys $pubkey
        icacls $adminKeys /inheritance:r /grant 'SYSTEM:(F)' /grant 'BUILTIN\Administrators:(F)' | Out-Null
        WStep "已写公钥(administrators_authorized_keys + ~/.ssh/authorized_keys 兜底)"
    }
} else { WStep "未提供 OMNI_BOOTSTRAP_PUBKEY, 跳过公钥写入" }

if (-not $dry -and (Test-Path $sshdCfg)) {
    try {
        $cfg = Get-Content $sshdCfg -Raw
        $changed = $false
        if ($cfg -notmatch '(?m)^\s*PubkeyAuthentication\s+yes') {
            $cfg = $cfg -replace '(?m)^\s*#?\s*PubkeyAuthentication[^\r\n]*$', 'PubkeyAuthentication yes'
            $changed = $true
        }
        if ($cfg -notmatch '(?m)^\s*PasswordAuthentication\s+no') {
            $cfg = $cfg -replace '(?m)^\s*#?\s*PasswordAuthentication[^\r\n]*$', 'PasswordAuthentication no'
            $changed = $true
        }
        if ($changed) {
            Set-Content -Path $sshdCfg -Value $cfg -Encoding utf8
            WStep "sshd_config 已调: PubkeyAuthentication yes / PasswordAuthentication no"
            try { Restart-Service sshd -ErrorAction SilentlyContinue } catch {}
        }
    } catch { WStep "sshd_config 调整失败(非致命): $($_.Exception.Message)" }
}

# ---- c) 防火墙: sshd 入站仅 LocalSubnet(禁公网)----
try {
    $ruleName = 'OpenSSH-Server-In-TCP-LAN'
    $rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $rule) {
        if ($dry) { WStep "DRY: 建 sshd 入站规则限 LocalSubnet" }
        else {
            New-NetFirewallRule -DisplayName $ruleName -Name $ruleName -Enabled True `
                -Direction Inbound -Protocol TCP -LocalPort $sshPort `
                -RemoteAddress LocalSubnet -Action Allow | Out-Null
            WStep "已建 sshd 防火墙规则(LocalSubnet only, 不放公网)"
        }
    } else {
        if (-not $dry) {
            $af = $rule | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue
            if ($af -and ($af.RemoteAddress -ne 'LocalSubnet')) {
                Set-NetFirewallRule -DisplayName $ruleName -RemoteAddress LocalSubnet
                WStep "已收窄 sshd 入站为 LocalSubnet"
            }
        }
    }
} catch { WStep "防火墙操作失败(非致命): $($_.Exception.Message)" }

# 收窄 Windows 自带默认 sshd 规则(Add-WindowsCapability 会建, 默认可能放 Any)到 LocalSubnet,
# 否则上面新建的 LAN 规则形同虚设(默认规则放 Any 时仍可从非局域网连入)。
try {
    if ($dry) { WStep "DRY: 收窄系统自带 sshd 规则到 LocalSubnet" }
    else {
        Get-NetFirewallRule -DisplayName 'OpenSSH SSH Server (sshd)' -ErrorAction SilentlyContinue | ForEach-Object {
            $af2 = $_ | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue
            if ($af2 -and ($af2.RemoteAddress -ne 'LocalSubnet')) {
                Set-NetFirewallRule -Name $_.Name -RemoteAddress LocalSubnet | Out-Null
                WStep "已把系统自带 sshd 规则收窄到 LocalSubnet"
            }
        }
    }
} catch { WStep "收窄系统 sshd 规则失败(非致命): $($_.Exception.Message)" }

# ---- c2) 显式放行主控机 IP 入站 SSH(盒子与主控可能不在同一 /24, 单靠 LocalSubnet 会漏)----
if ($masterIp) {
    $masterRuleName = 'OpenSSH-Server-In-TCP-Master'
    try {
        $masterRule = Get-NetFirewallRule -DisplayName $masterRuleName -ErrorAction SilentlyContinue
        if ($dry) { WStep "DRY: 放行主控机 $masterIp 入站 :$sshPort" }
        elseif (-not $masterRule) {
            New-NetFirewallRule -DisplayName $masterRuleName -Name $masterRuleName -Enabled True `
                -Direction Inbound -Protocol TCP -LocalPort $sshPort `
                -RemoteAddress $masterIp -Action Allow | Out-Null
            WStep "已放行主控机 $masterIp 入站 SSH"
        } else { WStep "主控机放行规则已存在" }
    } catch { WStep "放行主控机规则失败(非致命): $($_.Exception.Message)" }
}

# ---- 安装目录自适应: 配置的盘符(如主控的 E:)在盒子上可能不存在, 退回用户目录 ----
if ($installDir) {
    $driveLetter = ($installDir -split '[/\\]')[0]
    $driveOk = $false
    try { $driveOk = Test-Path $driveLetter } catch { $driveOk = $false }
    if (-not $driveOk) {
        $installDir = Join-Path $env:USERPROFILE 'omnicompany'
        WStep "配置的盘符 $driveLetter 在本机不存在, 安装目录改用: $installDir"
    }
}

# ---- d) omnicompany: clone 或 fetch(不强行 pull 覆盖; 失败不阻断注册)----
try {
if ($installDir) {
    $isRepo = Test-Path (Join-Path $installDir '.git')
    if (-not $isRepo) {
        if ($dry) { WStep "DRY: git clone $gitRemote $installDir" }
        else { git clone $gitRemote $installDir 2>&1 | ForEach-Object { WStep $_ } }
    } else {
        if ($dry) { WStep "DRY: git fetch(不 pull 覆盖本地改动)" }
        else {
            Push-Location $installDir
            try {
                git fetch origin 2>&1 | ForEach-Object { WStep $_ }
                $dirty = git status --porcelain 2>$null
                if ($dirty) { WStep "本地有未提交改动(dirty), 不覆盖, 仅 fetch 报告" }
                else { WStep "工作区干净" }
            } catch { WStep "git fetch 失败(非致命): $($_.Exception.Message)" }
            finally { Pop-Location }
        }
    }
    if (-not $dry) {
        $pyproj = Join-Path $installDir 'pyproject.toml'
        if (Test-Path $pyproj) {
            try { pip install -e $installDir 2>&1 | Select-Object -Last 3 | ForEach-Object { WStep $_ } }
            catch { WStep "pip install 失败(非致命): $($_.Exception.Message)" }
        }
        try { $oh = (omni --help 2>&1 | Select-Object -First 1); WStep "omni --help: $oh" }
        catch { WStep "omni --help 不可用(非致命)" }
    }
}
} catch { WStep "omnicompany 克隆/安装出错(非致命, 不阻断注册): $($_.Exception.Message)" }

# ---- e) Register with the controller and receive a report-only token ----
$registerResponse = $null
if ($callback -and $regPath) {
    $lanIp = '127.0.0.1'
    try {
        $udp = New-Object System.Net.Sockets.UdpClient
        $udp.Client.ReceiveTimeout = 500
        $udp.Connect('8.8.8.8', 80)
        $ep = $udp.Client.LocalEndPoint
        $lanIp = $ep.Address.ToString()
        $udp.Close()
    } catch { WStep "LAN IP probe failed; using $lanIp" }
    $body = @{
        node_id = $nodeId
        label = $label
        lan_ip = $lanIp
        ssh_user = $sshUser
        ssh_port = $sshPort
        omnicompany_version = ''
        capabilities = @('ssh', 'omnicompany', 'status-report')
        nonce = $nonce
        tunnel_pubkey = $tunnelPub
    } | ConvertTo-Json -Compress
    $url = $callback.TrimEnd('/') + $regPath
    if ($dry) {
        WStep "DRY: POST $url body=$body"
    } else {
        WStep "Registering with controller: POST $url (LAN IP=$lanIp)"
        try {
            $registerResponse = Invoke-RestMethod -Uri $url -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 15
            WStep "Registered with controller: $url"
        } catch { WStep "Controller registration failed (non-fatal): $($_.Exception.Message) | url=$url" }
    }
} else {
    WStep "Missing callback/register path; automatic registration skipped"
}

# ---- f) Durable one-way status/log reporter ----
# The token is report-only. It cannot execute commands or authenticate SSH.
try {
    if ($dry) {
        WStep "DRY: write report token/config/reporter and force-update OmniHomeNodeStatus"
    } elseif ($registerResponse -and $registerResponse.report_token -and $callback -and $reportPath) {
        $tokenFile = Join-Path $homeDir 'report_token.txt'
        $configFile = Join-Path $homeDir 'report_config.json'
        $reporterPs1 = Join-Path $homeDir 'report_status.ps1'
        [IO.File]::WriteAllText($tokenFile, ([string]$registerResponse.report_token), (New-Object Text.UTF8Encoding($false)))
        icacls $tokenFile /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
        $reportConfig = [ordered]@{
            callback = $callback
            report_path = $reportPath
            node_id = $nodeId
            install_dir = $installDir
        }
        [IO.File]::WriteAllText($configFile, ($reportConfig | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
        $reporterBody = @'
$ErrorActionPreference = 'Continue'
$homeDir = Join-Path $env:ProgramData 'omnicompany-home'
$configFile = Join-Path $homeDir 'report_config.json'
$tokenFile = Join-Path $homeDir 'report_token.txt'
if (-not (Test-Path $configFile) -or -not (Test-Path $tokenFile)) { exit 2 }
$config = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
$token = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
if (-not $token) { exit 3 }
function Read-LogTail([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    try {
        $text = (Get-Content -LiteralPath $Path -Tail 300 -ErrorAction Stop | Out-String)
        if ($text.Length -gt 16384) { return $text.Substring($text.Length - 16384) }
        return $text
    } catch { return "log read failed: $($_.Exception.Message)" }
}
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$gpus = @()
Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | ForEach-Object {
    $gpus += [ordered]@{
        name = [string]$_.Name
        adapter_ram_bytes = [int64]$_.AdapterRAM
        driver_version = [string]$_.DriverVersion
    }
}
$disks = @()
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue | ForEach-Object {
    $disks += [ordered]@{
        device_id = [string]$_.DeviceID
        size_bytes = [int64]$_.Size
        free_bytes = [int64]$_.FreeSpace
    }
}
$taskRows = [ordered]@{}
foreach ($taskName in @('OmniHomeNodeStatus', 'OmniHomeTunnel')) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
        $actions = @()
        foreach ($action in @($task.Actions)) {
            $actions += [ordered]@{
                execute = [string]$action.Execute
                arguments = [string]$action.Arguments
                working_directory = [string]$action.WorkingDirectory
            }
        }
        $taskRows[$taskName] = [ordered]@{
            state = [string]$task.State
            last_run_time = if ($taskInfo) { [string]$taskInfo.LastRunTime } else { '' }
            next_run_time = if ($taskInfo) { [string]$taskInfo.NextRunTime } else { '' }
            missed_runs = if ($taskInfo) { [int64]$taskInfo.NumberOfMissedRuns } else { 0 }
            last_task_result = if ($taskInfo) { [int64]$taskInfo.LastTaskResult } else { -1 }
            actions = $actions
        }
    } else {
        $taskRows[$taskName] = [ordered]@{ state = 'missing'; last_run_time = ''; next_run_time = ''; missed_runs = 0; last_task_result = -1; actions = @() }
    }
}
$tunnelPs1 = Join-Path $homeDir 'tunnel_loop.ps1'
$tunnelKey = Join-Path $homeDir 'tunnel_id_ed25519'
$knownHosts = Join-Path $homeDir 'known_hosts'
$tunnelLog = Join-Path $homeDir 'tunnel.log'
$sshCommand = Get-Command ssh.exe -ErrorAction SilentlyContinue
$tunnelScriptInfo = Get-Item -LiteralPath $tunnelPs1 -ErrorAction SilentlyContinue
$sshd = Get-Service sshd -ErrorAction SilentlyContinue
$status = [ordered]@{
    hostname = [string]$env:COMPUTERNAME
    os = [ordered]@{
        caption = if ($os) { [string]$os.Caption } else { '' }
        version = if ($os) { [string]$os.Version } else { '' }
        build_number = if ($os) { [string]$os.BuildNumber } else { '' }
    }
    gpu = $gpus
    ram_bytes = if ($os) { [int64]$os.TotalVisibleMemorySize * 1024 } else { 0 }
    disks = $disks
    sshd = if ($sshd) { [string]$sshd.Status } else { 'missing' }
    tasks = $taskRows
    tunnel_runtime = [ordered]@{
        ssh_path = if ($sshCommand) { [string]$sshCommand.Source } else { '' }
        script_exists = [bool]$tunnelScriptInfo
        script_length = if ($tunnelScriptInfo) { [int64]$tunnelScriptInfo.Length } else { 0 }
        script_last_write_utc = if ($tunnelScriptInfo) { [string]$tunnelScriptInfo.LastWriteTimeUtc.ToString('o') } else { '' }
        key_exists = Test-Path -LiteralPath $tunnelKey
        known_hosts_exists = Test-Path -LiteralPath $knownHosts
        log_exists = Test-Path -LiteralPath $tunnelLog
    }
    reporter_version = 2
}
$payload = [ordered]@{
    node_id = [string]$config.node_id
    report_token = $token
    status = $status
    bootstrap_log_tail = Read-LogTail (Join-Path $homeDir 'bootstrap.log')
    tunnel_log_tail = Read-LogTail (Join-Path $homeDir 'tunnel.log')
}
$body = $payload | ConvertTo-Json -Depth 10 -Compress
$url = ([string]$config.callback).TrimEnd('/') + [string]$config.report_path
Invoke-RestMethod -Uri $url -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 20 | Out-Null
'@
        [IO.File]::WriteAllText($reporterPs1, $reporterBody, (New-Object Text.UTF8Encoding($true)))
        $statusAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$reporterPs1`""
        $statusTriggers = @(
            (New-ScheduledTaskTrigger -AtStartup),
            (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
        )
        $statusPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName 'OmniHomeNodeStatus' -Action $statusAction -Trigger $statusTriggers -Principal $statusPrincipal -Force | Out-Null
        WStep "OmniHomeNodeStatus installed/updated (startup + every 5 minutes)"
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $reporterPs1
            WStep "Initial status/log report sent"
        } catch { WStep "Initial report failed (non-fatal): $($_.Exception.Message)" }
    } else {
        WStep "No report token returned; reporter setup skipped"
    }
} catch { WStep "Reporter setup failed (non-fatal): $($_.Exception.Message)" }

# ---- f2) Reverse SSH tunnel keep-alive ----
try {
    if ($masterIp -and $masterUser -and $tunnelPort) {
        $loopPs1 = Join-Path $homeDir 'tunnel_loop.ps1'
        $loopBody = @"
`$ErrorActionPreference = 'Continue'
`$homeDir = Join-Path `$env:ProgramData 'omnicompany-home'
`$key = Join-Path `$homeDir 'tunnel_id_ed25519'
`$kh = Join-Path `$homeDir 'known_hosts'
`$log = Join-Path `$homeDir 'tunnel.log'
New-Item -ItemType Directory -Path `$homeDir -Force | Out-Null
`$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
`$sshCommand = Get-Command ssh.exe -ErrorAction SilentlyContinue
`$sshPath = if (`$sshCommand) { [string]`$sshCommand.Source } else { 'missing' }
Add-Content -LiteralPath `$log -Value ("[{0}] tunnel_loop v2 started; identity={1}; powershell={2}; ssh={3}; key={4}; known_hosts={5}" -f (Get-Date).ToString('o'), `$identity, `$PSVersionTable.PSVersion, `$sshPath, (Test-Path -LiteralPath `$key), (Test-Path -LiteralPath `$kh)) -Encoding utf8
while (`$true) {
    if (-not `$sshCommand) {
        Add-Content -LiteralPath `$log -Value ("[{0}] ssh.exe is missing; retry in 30s" -f (Get-Date).ToString('o')) -Encoding utf8
        Start-Sleep -Seconds 30
        `$sshCommand = Get-Command ssh.exe -ErrorAction SilentlyContinue
        continue
    }
    Add-Content -LiteralPath `$log -Value ("[{0}] connecting reverse tunnel to $masterIp`:$masterSshPort -> 127.0.0.1:$tunnelPort" -f (Get-Date).ToString('o')) -Encoding utf8
    & `$sshCommand.Source -vv -N -R 127.0.0.1:$tunnelPort:127.0.0.1:22 $masterUser@$masterIp -p $masterSshPort -i `$key -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=`$kh -o IdentitiesOnly=yes *>> `$log
    `$code = `$LASTEXITCODE
    Add-Content -LiteralPath `$log -Value ("[{0}] ssh exited code={1}; retry in 5s" -f (Get-Date).ToString('o'), `$code) -Encoding utf8
    if ((Test-Path `$log) -and (Get-Item `$log).Length -gt 2097152) {
        `$tail = Get-Content -LiteralPath `$log -Tail 1200
        Set-Content -LiteralPath `$log -Value `$tail -Encoding utf8
    }
    Start-Sleep -Seconds 5
}
"@
        if ($dry) {
            WStep "DRY: write tunnel loop and force-update OmniHomeTunnel"
        } else {
            [IO.File]::WriteAllText($loopPs1, $loopBody, (New-Object Text.UTF8Encoding($true)))
            try { Stop-ScheduledTask -TaskName 'OmniHomeTunnel' -ErrorAction SilentlyContinue } catch {}
            Start-Sleep -Seconds 1
            $tAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$loopPs1`""
            $tTrigger = New-ScheduledTaskTrigger -AtStartup
            $tPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $tSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances StopExisting -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
            Register-ScheduledTask -TaskName 'OmniHomeTunnel' -Action $tAction -Trigger $tTrigger -Principal $tPrincipal -Settings $tSettings -Force | Out-Null
            Start-ScheduledTask -TaskName 'OmniHomeTunnel' -ErrorAction Stop
            WStep "OmniHomeTunnel v2 installed/updated and started (controller loopback :$tunnelPort)"
        }
    } else {
        WStep "Missing controller tunnel configuration; tunnel setup skipped"
    }
} catch { WStep "Reverse tunnel setup failed (non-fatal): $($_.Exception.Message)" }

# ---- g) finish ----
WStep "Log saved to: $bootstrapLog"
WStep "Done. No command input is required."
try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
'''
