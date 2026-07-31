# [OMNI] origin=claude-code-worker domain=dashboard/controlplane ts=2026-07-25T00:00:00+08:00 type=infra
# [OMNI] material_id="material:dashboard.controlplane.remote_nodes.py"
# [OMNI] summary="远程节点(家中电脑等)一键引导的 dashboard 路由层: 下载 bootstrap.bat + 落地页 + 装完回报 register + 节点列表。纯路由, 逻辑全委托 remote_nodes_core(冻住 API)。"
# [OMNI] why="多机互联近期收敛版要求主控机在 dashboard 上提供一键引导包下载口: 用户打开落地页拿到网址, 在家中 Windows 主机浏览器下载 bootstrap.bat 双击即装好 SSH+omnicompany 并自动注册回主控, 之后这台主控机的 agent 就能远控它。下载口返回的即 remote_nodes_core.render_bootstrap_bat 渲染的单文件 .bat, 仅内联公钥+回连地址, 绝不含私钥/token。"
# [OMNI] tags=remote-nodes,bootstrap,dashboard,router,home-node,register
"""controlplane/remote_nodes.py — 远程节点一键引导的 dashboard 路由层。

挂在 dashboard 进程(8210)。逻辑全部委托 :mod:`remote_nodes_core` 的冻住 API,
本模块只做 HTTP 薄壳: 下载口、落地页、装完回报、节点列表。

端点(prefix ``/api/remote-nodes``):

    GET  /bootstrap.bat   下载单文件引导包(render_bootstrap_bat 渲染; 仅公钥+公开配置)
    GET  /bootstrap       自包含 HTML 落地页(一个下载网址 + 三步说明)
    POST /register        盒子装完自动回报; 记节点信息+last_seen, 回填空着的 host/user
    GET  /                列全部已注册节点
    GET  /{node_id}       看单个节点; 不存在 404

安全模型(register):
    - LAN-local: 引导包回连地址是主控机本机 LAN IP, 盒子在局域网内 POST 回来。
    - 信任边界是 **SSH 密钥登录**, 不是 register 端点。register 只记录节点信息
      (lan_ip/ssh_user/...)和 last_seen, **不存任何密钥/口令**; 盒侧 SSH 仅密钥登录、
      防火墙只放 LocalSubnet, 绝不暴露公网(见 bootstrap.bat 盒侧脚本)。
    - register 顺手把 config/home.yaml 里**空着的** host/user 用盒子回报的 lan_ip/ssh_user
      回填(只填空, 不覆盖), 让"装好即远控"闭环。
    - 后续可加一次性配对 token(盒子引导时主控内联一个 nonce, register 校验它),
      但当前 LAN-local + SSH 密钥信任边界已足够家用场景。
"""
from __future__ import annotations

import getpass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from omnicompany.dashboard.controlplane.remote_nodes_core import (
    BOOTSTRAP_PATH,
    CONNECT_PATH,
    callback_base_url,
    ensure_ssh_keypair,
    issue_report_token,
    list_nodes,
    load_home_config,
    load_nodes,
    public_node_record,
    render_bootstrap_bat,
    save_home_config,
    save_node,
    stage_tunnel_pubkey,
    verify_nonce,
    verify_report_token,
)

remote_nodes_router = APIRouter(prefix="/api/remote-nodes", tags=["remote-nodes"])


# ── 下载口: 单文件 bootstrap.bat ─────────────────────────────────────────────
# 静态 GET 路由必须声明在 /{node_id} 之前, 否则会被通配吞掉。

def _download_connection_package(filename: str) -> Response:
    cfg = load_home_config()
    try:
        ensure_ssh_keypair(cfg)
        bat_text = render_bootstrap_bat(cfg)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"ok": False, "error": "bootstrap_render_failed", "detail": str(exc)},
            status_code=503,
        )
    return Response(
        content=bat_text.encode("utf-8"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@remote_nodes_router.get("/connect.bat")
def download_connect_bat() -> Response:
    """Friendly one-click package name for end users."""
    return _download_connection_package("omni-home-connect.bat")


@remote_nodes_router.get("/bootstrap.bat")
def download_bootstrap_bat() -> Response:
    """Backward-compatible bootstrap package endpoint."""
    return _download_connection_package("bootstrap.bat")


@remote_nodes_router.get("/bootstrap", response_class=HTMLResponse)
def bootstrap_landing_page() -> HTMLResponse:
    """自包含 HTML 落地页: 一个下载网址 + 三步说明 + 直接下载链接。

    下载网址 = :func:`callback_base_url` + BOOTSTRAP_PATH。不依赖任何前端构建,
    纯内联 CSS, 中文。本身即 UI(无需 SPA 入口)。
    """
    cfg = load_home_config()
    download_url = callback_base_url(cfg) + CONNECT_PATH

    # 注意: HTML 里不出现任何私钥/口令; 只展示公开的下载网址与说明。
    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>添加远程电脑 · omnicompany</title>
<style>
 html,body{{margin:0;background:#0f1115;color:#e6edf3;
  font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  display:grid;place-items:center;min-height:100vh}}
 .card{{width:min(560px,92%);background:#161a22;border:1px solid #283042;
  border-radius:16px;padding:28px;box-sizing:border-box}}
 h1{{font-size:22px;margin:0 0 8px}}
 .sub{{color:#8b97a8;font-size:14px;line-height:1.7;margin:0 0 18px}}
 .url-row{{display:flex;align-items:center;gap:8px;margin:14px 0;
  background:#0b1220;border:1px solid #283042;border-radius:8px;padding:10px 12px}}
 code{{color:#cbd5e1;flex:1;word-break:break-all;font-size:13px}}
 .copy{{flex-shrink:0;background:#21262d;color:#c9d1d9;border:1px solid #30363d;
  border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer}}
 .copy:hover{{background:#30363d}}
 .dl{{display:block;text-align:center;margin:18px 0 8px;background:#4493f8;
  color:#06131f;text-decoration:none;border-radius:10px;padding:14px;
  font-size:16px;font-weight:700}}
 .dl:hover{{background:#3a82e0}}
 ol{{color:#8b97a8;font-size:14px;line-height:2.0;padding-left:22px;margin:12px 0 4px}}
 ol li b{{color:#e6edf3}}
 .note{{color:#6e7681;font-size:12px;margin-top:14px;line-height:1.6;
  border-top:1px solid #21262d;padding-top:12px}}
</style></head><body><div class="card">
 <h1>🖥️ 添加远程电脑</h1>
 <p class="sub">在这台电脑(主控机)上生成了一键引导包。到家里那台要被远控的 Windows 电脑上,
  按下面三步操作, 装好后它就会自动登记回来, 之后主控机就能远控它。</p>

 <div class="url-row">
   <code id="dl-url">{download_url}</code>
   <button class="copy" onclick="navigator.clipboard.writeText(document.getElementById('dl-url').textContent).then(function(){{this.textContent='已复制'}}.bind(this))">复制</button>
 </div>

 <a class="dl" href="{download_url}" download>⬇ 直接下载 bootstrap.bat</a>

 <ol>
   <li>在<b>另一台电脑</b>(要被远控的那台)的浏览器里打开上面的网址, 下载 <code>bootstrap.bat</code></li>
   <li>把下载到的 <code>bootstrap.bat</code> <b>双击</b>运行</li>
   <li>弹出 UAC 提权时点<b>是</b>, 等它跑完(会自动装好 SSH、写入公钥、装好 omnicompany、注册回主控)</li>
 </ol>

 <p class="note">引导包只内联主控机的<b>公钥</b>与本机回连地址, 不含任何私钥或口令;
  盒侧 SSH 仅密钥登录、防火墙只放局域网, 不暴露公网。</p>
</div></body></html>"""
    return HTMLResponse(content=html)


# ── 装完回报: register ───────────────────────────────────────────────────────
# 安全模型见模块顶部 docstring: LAN-local, 信任边界是 SSH 密钥, 不存密钥。

@remote_nodes_router.post("/register")
async def register_node(req: Request) -> JSONResponse:
    """盒子装完后自动回报。body 字段(缺字段容忍):
    node_id/label/lan_ip/ssh_user/ssh_port/omnicompany_version/capabilities。

    - :func:`save_node` 记录节点信息并自动补 last_seen。
    - 记调用方 IP(``request.client.host``)进 ``registered_from``。
    - 回填: cfg["host"] 空且 body 有 lan_ip → 填; cfg["user"] 空且 body 有 ssh_user → 填。
      只填空, 不覆盖已有值。
    - **不存任何密钥/口令**。
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        body = {}

    node_id = str(body.get("node_id") or "").strip()
    if not node_id:
        return JSONResponse({"ok": False, "error": "node_id required"}, status_code=400)

    # 配对 nonce 校验(防同网段他人乱注册/乱投钥): 必须与引导包内联的一次性 nonce 匹配,
    # 且一次性(用后即废)。校验失败 → 403, 且【不写任何东西】(不落节点、不暂存公钥)。
    nonce = str(body.get("nonce") or "").strip()
    if not verify_nonce(nonce):
        return JSONResponse({"ok": False, "error": "invalid_nonce"}, status_code=403)

    tunnel_pubkey = str(body.get("tunnel_pubkey") or "").strip()

    raw_port = body.get("ssh_port")
    try:
        ssh_port = int(raw_port) if raw_port not in (None, "") else 22
    except (TypeError, ValueError):
        ssh_port = 22

    info: dict[str, Any] = {
        "label": str(body.get("label") or ""),
        "lan_ip": str(body.get("lan_ip") or ""),
        "ssh_user": str(body.get("ssh_user") or ""),
        "ssh_port": ssh_port,
        "omnicompany_version": str(body.get("omnicompany_version") or ""),
        "capabilities": body.get("capabilities") if isinstance(body.get("capabilities"), list) else [],
        "registered_from": req.client.host if req.client else "",
        "tunnel_pubkey": tunnel_pubkey,
    }
    save_node(node_id, info)

    # 暂存盒子隧道公钥(待主控提权 install 进 administrators_authorized_keys)。
    if tunnel_pubkey:
        stage_tunnel_pubkey(tunnel_pubkey)

    # 回填空着的 host/user, 让"装好即远控"闭环(只填空, 不覆盖)。
    cfg = load_home_config()
    lan_ip = str(body.get("lan_ip") or "").strip()
    ssh_user = str(body.get("ssh_user") or "").strip()
    changed = False
    if not str(cfg.get("host") or "").strip() and lan_ip:
        cfg["host"] = lan_ip
        changed = True
    if not str(cfg.get("user") or "").strip() and ssh_user:
        cfg["user"] = ssh_user
        changed = True
    if changed:
        save_home_config(cfg)

    report_token = issue_report_token(node_id)
    return {
        "ok": True,
        "node_id": node_id,
        "registered": True,
        "tunnel_key_staged": bool(tunnel_pubkey),
        "report_token": report_token,
    }


# ── 节点列表 / 单节点 ─────────────────────────────────────────────────────────
# 这两条通配路由放最后, 避免吞掉上面的静态 GET(/bootstrap 等)。

# Report-only endpoint. It updates inventory/heartbeat/log tails and never
# dispatches commands. The credential is scoped to this endpoint only.
MAX_LOG_TAIL_CHARS = 16 * 1024


def _bounded_tail(value: Any) -> str:
    text = str(value or "")
    return text[-MAX_LOG_TAIL_CHARS:]


@remote_nodes_router.post("/report")
async def report_node(req: Request) -> JSONResponse:
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        body = {}

    node_id = str(body.get("node_id") or "").strip()
    report_token = str(body.get("report_token") or "").strip()
    if not node_id or not verify_report_token(node_id, report_token):
        return JSONResponse({"ok": False, "error": "invalid_report_token"}, status_code=403)
    if node_id not in load_nodes():
        return JSONResponse({"ok": False, "error": "unknown_node"}, status_code=404)

    status = body.get("status") if isinstance(body.get("status"), dict) else {}
    bootstrap_tail = body.get("bootstrap_log_tail", body.get("log_tail", ""))
    tunnel_tail = body.get("tunnel_log_tail", "")
    now = datetime.now(timezone.utc).isoformat()
    save_node(
        node_id,
        {
            "last_report": now,
            "report_from": req.client.host if req.client else "",
            "status": status,
            "bootstrap_log_tail": _bounded_tail(bootstrap_tail),
            "tunnel_log_tail": _bounded_tail(tunnel_tail),
        },
    )
    return JSONResponse({"ok": True, "node_id": node_id, "reported": True, "received_at": now})


@remote_nodes_router.get("")
def list_nodes_endpoint() -> dict:
    """列全部已注册节点。"""
    return {"nodes": list_nodes()}


@remote_nodes_router.get("/{node_id}")
def get_node_endpoint(node_id: str) -> JSONResponse:
    """看单个节点; 不存在返回 404。"""
    nodes = load_nodes()
    info = nodes.get(node_id)
    if info is None:
        return JSONResponse({"ok": False, "error": "not_found", "node_id": node_id}, status_code=404)
    return public_node_record(node_id, info)
