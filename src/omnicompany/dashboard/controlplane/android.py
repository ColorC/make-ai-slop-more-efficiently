# [OMNI] origin=ai-ide ts=2026-06-25 type=infra
# [OMNI] material_id="material:dashboard.controlplane.android.lofa_endpoints.py"
"""controlplane/android.py — LOFA 安卓局域网远程端专用端点。

挂在 dashboard 进程(8210, 可自由重启), 不挂 ccdaemon。

    POST /api/android/log     批量接收 App 端日志/网络日志/崩溃报告, 落 data/logs/android/<day>.jsonl
    GET  /api/android/log/tail 看板/调试用: 回看最近 N 条(默认 200), 便于 PC 端可观测

设计: App 端按 JSONL 攒日志(Timber FileTree + OkHttp 拦截器 + WebView console 汇入同一文件),
连得上本机时批量 POST 回传, 落本机磁盘, 之后在看板里查看(重度 log 一等公民)。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import mimetypes
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

android_router = APIRouter(prefix="/api/android", tags=["android-lofa"])
LOFA_WEB_SESSION_COOKIE = "omni_lofa_session"


def _ws_root() -> Path:
    """权威工作区根(与 reviewstage 一致), 不靠 cwd —— dashboard 进程 cwd 可能不是 omnicompany,
    cwd 兜底会把 releases/commands 落到错地方(OTA 读不到 manifest)。"""
    try:
        from omnicompany.core.config import omni_workspace_root
        return omni_workspace_root()
    except Exception:
        return Path(os.environ.get("OMNI_WORKSPACE_ROOT", str(Path.cwd())))

# ── B 档反向控制: 命令信道(device_id 维度) ───────────────────────────────────
# 即使手机只单向连本机(飞连, 本机够不着手机 adb), 本机也能驱动它:
#   host 入队命令(enqueue) → app 轮询取走(poll)在自己进程内执行 → 回传结果(result)。
# 存储: data/runtime/lofa_commands.json, {device_id: {queue:[...], results:[...]}}。
_cmd_lock = threading.Lock()
_automation_lock = threading.Lock()
_automation_events_lock = threading.Lock()
_automation_events: dict[str, threading.Event] = {}
_asset_lock = threading.Lock()

AUTOMATION_OPS = {
    "status",
    "screenshot",
    "ui_tree",
    "tap",
    "click_text",
    "set_text",
    "global_action",
    "launch_app",
    "launch_label",
    "stage_media",
    "review_remote_probe",
    "cleanup_debug_media",
}

LEGACY_COMMAND_OPS = {
    "ping",
    "toast",
    "navigate",
    "state",
    "screenshot",
    "ota_check",
    "ota_install",
}


def _cmd_file() -> Path:
    root = _ws_root()
    d = root / "data" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lofa_commands.json"


def _automation_file() -> Path:
    root = _ws_root()
    d = root / "data" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lofa_automation.json"


def _automation_token_file() -> Path:
    root = _ws_root()
    d = root / "data" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lofa_automation_control.token"


def _asset_lease_file() -> Path:
    root = _ws_root()
    directory = root / "data" / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "lofa_asset_leases.json"


def _asset_lease_dir() -> Path:
    root = _ws_root()
    directory = root / "data" / "runtime" / "lofa_assets"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def lofa_automation_control_token() -> str:
    """Return the local controller token, creating it atomically on first use."""
    path = _automation_token_file()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    temporary.replace(path)
    return value


def _authorized_controller(req: Request) -> bool:
    expected = lofa_automation_control_token()
    supplied = str(req.headers.get("x-lofa-control-token") or "")
    return bool(supplied) and secrets.compare_digest(supplied, expected)


def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _device_token_matches(device_id: str, token: str, bucket: dict | None = None) -> bool:
    if not token or not device_id:
        return False
    if bucket is None:
        with _automation_lock:
            bucket = _load_automation().get(device_id) or {}
    expected = str((bucket or {}).get("device_token_hash") or "")
    return bool(expected) and secrets.compare_digest(_token_digest(token), expected)


def _authorized_device(req: Request, device_id: str, bucket: dict | None = None) -> bool:
    supplied = str(req.headers.get("x-lofa-device-token") or "")
    return _device_token_matches(device_id, supplied, bucket)


def _web_session_device(req: Request) -> str:
    raw = str(req.cookies.get(LOFA_WEB_SESSION_COOKIE) or "")
    if "." not in raw:
        return ""
    encoded_device_id, token = raw.rsplit(".", 1)
    device_id = unquote(encoded_device_id).strip()
    return device_id if _device_token_matches(device_id, token) else ""


def _review_store():
    from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store

    return get_store()


def _review_remote_url(material_id: str) -> str | None:
    from omnicompany.dashboard.boss_sight.reviewstage.remote_preflight import (
        review_remote_material_url,
    )

    return review_remote_material_url(material_id, _ws_root())


def _material_sample_sha256(store, material) -> str:
    live_url = str((material.extra or {}).get("live_url") or "").strip()
    if live_url:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import build_live_url_shell

        return hashlib.sha256(
            build_live_url_shell(live_url, title=material.title or "").encode("utf-8")[:4096]
        ).hexdigest()
    if material.file_relpath:
        path = store.resolve_file_path(material)
        with path.open("rb") as stream:
            sample = stream.read(4096)
    elif material.inline_content is not None:
        sample = str(material.inline_content).encode("utf-8")[:4096]
    else:
        sample = b""
    if not sample:
        raise ValueError("review material has no canonical bytes to verify")
    return hashlib.sha256(sample).hexdigest()


def _record_review_remote_verification(
    *,
    device_id: str,
    source_ip: str,
    bucket: dict,
    command: dict,
    body: dict,
) -> dict:
    """Turn one authenticated phone probe into the store's short-lived receipt.

    The controller may enqueue a probe, but it cannot author this record. The
    result must return through the paired device-token channel and its first-
    4-KB hash must match the canonical material. A direct device source IP is
    preferred; protocol-v4 Android runtimes may attest their private local IP
    when an emulator or handset NAT presents the host IP to the server.
    """

    if command.get("op") != "review_remote_probe":
        raise ValueError("not a review remote probe")
    if "review_remote_probe" not in set(bucket.get("capabilities") or []):
        raise PermissionError("paired device has not registered review_remote_probe")
    if not bool(body.get("ok")) or not isinstance(body.get("result"), dict):
        raise ValueError("physical review probe did not succeed")
    if not source_ip or source_ip != str(bucket.get("ip") or ""):
        raise PermissionError("physical review probe source IP changed")

    args = command.get("args") if isinstance(command.get("args"), dict) else {}
    result = body["result"]
    material_id = str(args.get("material_id") or "").strip()
    remote_url = str(args.get("remote_url") or "").strip()
    kind = str(args.get("kind") or "").strip()
    if not material_id or not remote_url or not kind:
        raise ValueError("review probe command identity is incomplete")
    if (
        str(result.get("material_id") or "") != material_id
        or str(result.get("remote_url") or "") != remote_url
        or str(result.get("kind") or "") != kind
    ):
        raise ValueError("review probe result does not match the issued command")

    parsed = urlparse(remote_url)
    try:
        source_addr = ipaddress.ip_address(source_ip)
        target_addr = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise PermissionError("physical review probe requires explicit IP endpoints") from exc
    if parsed.scheme.lower() != "https" or source_addr.is_loopback or source_addr.is_unspecified:
        raise PermissionError("physical review probe did not originate from an independent device")
    network_path = "direct_source_ip"
    device_local_ip = str(result.get("device_local_ip") or "").strip()
    device_runtime = str(result.get("device_runtime") or "").strip()
    if source_addr == target_addr:
        try:
            device_local_addr = ipaddress.ip_address(device_local_ip)
        except ValueError as exc:
            raise PermissionError(
                "NAT review probe is missing a valid device-local address"
            ) from exc
        registered_local_ip = str(bucket.get("device_local_ip") or "").strip()
        if (
            int(bucket.get("protocol_version") or 0) < 4
            or device_runtime != "android-native"
            or str(bucket.get("device_runtime") or "") != device_runtime
            or not secrets.compare_digest(registered_local_ip, device_local_ip)
            or device_local_addr.is_loopback
            or device_local_addr.is_unspecified
            or device_local_addr == target_addr
        ):
            raise PermissionError(
                "NAT review probe lacks authenticated Android runtime attestation"
            )
        network_path = "authenticated_android_nat"

    expected_url = _review_remote_url(material_id)
    if not expected_url or remote_url != expected_url:
        raise ValueError("review probe URL is not the canonical LAN gateway material URL")
    if int(result.get("http_status") or 0) not in {200, 206}:
        raise ValueError("physical review probe returned an invalid HTTP status")
    if result.get("identity_ok") is not True or result.get("tls_verified") is not True:
        raise ValueError("physical review probe did not verify material identity and TLS")

    store = _review_store()
    # Review materials are also created by the CLI in a separate process.  The
    # daemon singleton may therefore have loaded its cache before the material
    # existed.  Refresh the file-signature cache at the trust boundary so a
    # genuine phone receipt is not rejected merely because the verifier worker
    # has a stale in-memory view.
    store.reload()
    material = store.get(material_id)
    if material is None:
        raise KeyError(material_id)
    material_kind = material.kind.value if hasattr(material.kind, "value") else str(material.kind)
    if kind != material_kind:
        raise ValueError("review probe kind does not match the canonical material")
    expected_sample = _material_sample_sha256(store, material)
    sample_sha256 = str(result.get("sample_sha256") or "").lower()
    if not secrets.compare_digest(sample_sha256, expected_sample):
        raise ValueError("physical review probe sample hash does not match the canonical material")

    preflight_url = str(((material.extra or {}).get("remote_preflight") or {}).get("remote_url") or "")
    if preflight_url and preflight_url != remote_url:
        raise ValueError("physical review probe URL does not match submit-host preflight")

    receipt = {
        "status": "pass",
        "scope": "physical_remote_device",
        "physical_remote_verified": True,
        "material_id": material_id,
        "remote_url": remote_url,
        "http_status": int(result["http_status"]),
        "content_type": str(result.get("content_type") or ""),
        "identity_ok": True,
        "tls_verified": True,
        "sample_sha256": sample_sha256,
        "sample_size": int(result.get("sample_size") or 0),
        "verifier": f"lofa-device:{device_id}",
        "device_id": device_id,
        "source_ip": source_ip,
        "device_local_ip": device_local_ip or None,
        "device_runtime": device_runtime or None,
        "network_path": network_path,
        "command_id": str(command.get("id") or ""),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "warning": None,
        "warnings": [],
    }
    store.patch_extra(
        material_id,
        {"remote_verification": receipt},
        by="remote-verification-service",
    )
    return receipt


def _automation_event(device_id: str) -> threading.Event:
    with _automation_events_lock:
        event = _automation_events.get(device_id)
        if event is None:
            event = threading.Event()
            _automation_events[device_id] = event
        return event


def _signal_automation(device_id: str) -> None:
    _automation_event(device_id).set()


def _load_automation() -> dict:
    path = _automation_file()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_automation(value: dict) -> None:
    path = _automation_file()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _load_asset_leases() -> dict:
    path = _asset_lease_file()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_asset_leases(value: dict) -> None:
    path = _asset_lease_file()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _device_record() -> dict:
    path = _device_file()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _load_cmds() -> dict:
    fp = _cmd_file()
    if not fp.is_file():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cmds(d: dict) -> None:
    try:
        _cmd_file().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _release_dir() -> Path:
    """LOFA APK 自更新发布目录: PC 构建后把 APK + manifest 放这, app 从 /apk/* 拉。"""
    root = _ws_root()
    d = root / "data" / "android" / "releases"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_dir() -> Path:
    root = _ws_root()
    d = root / "data" / "logs" / "android"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _device_file() -> Path:
    root = _ws_root()
    d = root / "data" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lofa_device.json"


def _record_device(ip: str, device_id: str = "") -> None:
    """记下"最近一次连上本机的手机源 IP" —— 扁平网下即手机真实 LAN IP。
    配合固定 adb 端口 5555, PC 侧守护脚本据此 `adb connect <ip>:5555` 自动反向调试,
    用户开一下 app 即可, 永不用再复制调试端口/IP。"""
    if not ip:
        return
    try:
        record = {}
        if _device_file().is_file():
            try:
                existing = json.loads(_device_file().read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    record.update(existing)
            except Exception:
                pass
        record.update({"ip": ip, "adb_port": 5555, "last_seen": time.time()})
        if device_id:
            record["device_id"] = device_id
        _device_file().write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


@android_router.post("/log")
async def android_log(req: Request):
    """批量接收安卓端日志, 按天 JSONL 追加落盘。

    body 接受 {"entries": [...]} 或单条对象或数组; 每条补 _recv_ts 服务端落地时间。
    顺便记下手机源 IP(供 PC 侧自动反向调试)。
    """
    _record_device(req.client.host if req.client else "")
    try:
        body = await req.json()
    except Exception:
        return {"ok": False, "error": "invalid_json", "received": 0}

    if isinstance(body, dict) and "entries" in body:
        entries = body["entries"]
    else:
        entries = body
    if not isinstance(entries, list):
        entries = [entries]

    day = time.strftime("%Y-%m-%d")
    fp = _log_dir() / f"{day}.jsonl"
    n = 0
    with fp.open("a", encoding="utf-8") as f:
        for e in entries:
            rec = {"_recv_ts": time.time()}
            if isinstance(e, dict):
                rec.update(e)
            else:
                rec["msg"] = e
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return {"ok": True, "received": n, "file": str(fp)}


@android_router.get("/log/tail")
def android_log_tail(day: str | None = None, limit: int = 200):
    """回看某天最近 N 条安卓端日志(默认今天/200 条), 供 PC 端可观测。"""
    day = day or time.strftime("%Y-%m-%d")
    fp = _log_dir() / f"{day}.jsonl"
    if not fp.is_file():
        return {"ok": True, "day": day, "count": 0, "entries": []}
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-max(1, min(limit, 5000)):]
    out = []
    for ln in tail:
        try:
            out.append(json.loads(ln))
        except Exception:
            out.append({"_raw": ln})
    return {"ok": True, "day": day, "count": len(out), "entries": out}


@android_router.post("/register")
async def android_register(req: Request):
    """App 连上本机时主动登记 —— 记下手机源 IP, 供 PC 侧 auto-adb 自动反向调试。"""
    ip = req.client.host if req.client else ""
    device_id = ""
    try:
        body = await req.json()
        if isinstance(body, dict):
            device_id = str(body.get("device_id") or "").strip()
    except Exception:
        pass
    from omnicompany.dashboard.controlplane.lan_access import (
        is_local_client_ip,
        is_known_lofa_device,
        record_native_device_pending,
    )

    if not is_local_client_ip(ip) and not device_id:
        return JSONResponse(
            {"ok": False, "error": "device_id_required"},
            status_code=400,
        )
    if not is_local_client_ip(ip) and not is_known_lofa_device(ip, device_id):
        request_id = record_native_device_pending(
            ip=ip,
            device_id=device_id,
            user_agent=req.headers.get("user-agent", ""),
        )
        return JSONResponse(
            {
                "ok": False,
                "error": "device_approval_required",
                "request_id": request_id,
                "approval_url": f"/device-access?request={request_id}",
            },
            status_code=403,
        )
    _record_device(ip, device_id)
    return {"ok": True, "ip": ip, "adb_port": 5555, "device_id": device_id}


@android_router.get("/device")
def android_device():
    """返回最近一次连上本机的手机 IP + 固定 adb 端口, 给 PC 侧守护脚本用。"""
    fp = _device_file()
    if not fp.is_file():
        return {"ok": True, "device": None}
    try:
        return {"ok": True, "device": json.loads(fp.read_text(encoding="utf-8"))}
    except Exception:
        return {"ok": True, "device": None}


# ── APK 自更新 (pull 式 OTA: 飞连下手机→PC 通, app 自己拉新 APK 装上, 不需要 adb) ──

@android_router.get("/apk/version")
def apk_version():
    """返回已发布 APK 的版本清单(versionCode/versionName/sha256/size)。app 拿它跟自身版本比对。"""
    fp = _release_dir() / "manifest.json"
    if not fp.is_file():
        return {"ok": True, "manifest": None}
    try:
        return {"ok": True, "manifest": json.loads(fp.read_text(encoding="utf-8"))}
    except Exception:
        return {"ok": True, "manifest": None}


@android_router.get("/apk/latest")
def apk_latest():
    """下载最新 APK。app 自更新时拉这个, 然后走 PackageInstaller 自安装。"""
    fp = _release_dir() / "lofa-latest.apk"
    if not fp.is_file():
        return JSONResponse({"ok": False, "error": "no_apk_published"}, status_code=404)
    return FileResponse(
        str(fp),
        media_type="application/vnd.android.package-archive",
        filename="lofa-latest.apk",
    )


@android_router.get("/install", response_class=HTMLResponse)
def apk_install_page():
    """手机浏览器打开这个页 → 点下载 → 装上带自更新的 LOFA(引导首装/旧版升级)。
    飞连下手机→PC 通, 所以离开扁平网也能在浏览器里装。装上后 app 内即可自更新。"""
    mani = {}
    fp = _release_dir() / "manifest.json"
    if fp.is_file():
        try:
            mani = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            mani = {}
    ver = mani.get("versionName") or "(未发布)"
    size_mb = round((mani.get("size") or 0) / 1024 / 1024, 1)
    has = (_release_dir() / "lofa-latest.apk").is_file()
    btn = (
        '<a class="btn" href="/api/android/apk/latest">下载并安装 LOFA ' + str(ver) + "</a>"
        if has else '<div class="warn">尚未发布 APK。请在 PC 跑 <code>publish-release.sh</code>。</div>'
    )
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>安装 LOFA</title><style>
 html,body{{margin:0;height:100%;background:#0f1115;color:#e6edf3;
  font-family:system-ui,-apple-system,"PingFang SC",sans-serif;display:grid;place-items:center}}
 .card{{width:min(420px,92%);background:#161a22;border:1px solid #283042;border-radius:16px;padding:26px}}
 h1{{font-size:20px;margin:0 0 6px}} p{{color:#8b97a8;font-size:14px;line-height:1.7;margin:8px 0}}
 .btn{{display:block;text-align:center;margin:20px 0 8px;background:#4493f8;color:#06131f;
  text-decoration:none;border-radius:10px;padding:15px;font-size:16px;font-weight:700}}
 .warn{{color:#f85149;font-size:14px;margin:16px 0}}
 ol{{color:#8b97a8;font-size:13px;line-height:1.9;padding-left:20px}} code{{color:#cbd5e1;background:#0b1220;padding:1px 5px;border-radius:4px}}
 .v{{color:#3fb950;font-size:13px}}
</style></head><body><div class="card">
 <h1>📱 安装 LOFA</h1>
 <p class="v">最新版本 {ver} · {size_mb} MB</p>
 {btn}
 <p>装好后，以后在 app 里点更新横幅即可<b>自更新</b>，不用再来这页。</p>
 <ol>
  <li>点上面按钮下载 APK</li>
  <li>若提示「未知来源」，允许「LOFA / 浏览器」安装应用</li>
  <li>点「安装」即可（覆盖升级，数据不丢）</li>
 </ol>
</div></body></html>"""


# ── 命令信道路由 ─────────────────────────────────────────────────────────────

@android_router.post("/commands/enqueue")
async def cmd_enqueue(req: Request):
    """本机/操作者给某设备下一条命令。body: {device_id, op, args?}。op ∈
    ping/toast/navigate/state/screenshot/ota_check/ota_install/eval。返回 command_id。"""
    if not _authorized_controller(req):
        return JSONResponse({"ok": False, "error": "controller authorization required"}, status_code=403)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip()
    op = str(body.get("op") or "").strip()
    if not device_id or op not in LEGACY_COMMAND_OPS:
        return JSONResponse(
            {"ok": False, "error": "device_id and an allowlisted legacy op are required"},
            status_code=400,
        )
    cmd = {"id": uuid.uuid4().hex[:12], "op": op, "args": body.get("args") or {},
           "ts": time.time(), "status": "pending"}
    with _cmd_lock:
        d = _load_cmds()
        d.setdefault(device_id, {"queue": [], "results": []})["queue"].append(cmd)
        _save_cmds(d)
    return {"ok": True, "command_id": cmd["id"], "device_id": device_id}


@android_router.get("/commands/poll")
def cmd_poll(req: Request, device_id: str):
    """App 轮询: 取走该设备的全部待办命令(取走即清队列)。顺带记手机源 IP。"""
    _record_device(req.client.host if req.client else "")
    with _cmd_lock:
        d = _load_cmds()
        bucket = d.get(device_id) or {"queue": [], "results": []}
        pending = list(bucket.get("queue", []))
        bucket["queue"] = []
        d[device_id] = bucket
        _save_cmds(d)
    return {"ok": True, "commands": pending}


@android_router.post("/commands/result")
async def cmd_result(req: Request):
    """App 回传命令执行结果。body: {device_id, command_id, ok, result?}。"""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip()
    if not device_id:
        return JSONResponse({"ok": False, "error": "device_id required"}, status_code=400)
    rec = {"command_id": body.get("command_id"), "ok": bool(body.get("ok")),
           "result": body.get("result"), "ts": time.time()}
    with _cmd_lock:
        d = _load_cmds()
        bucket = d.setdefault(device_id, {"queue": [], "results": []})
        bucket["results"] = (bucket.get("results", []) + [rec])[-50:]
        _save_cmds(d)
    return {"ok": True}


@android_router.get("/commands/results")
def cmd_results(device_id: str, limit: int = 20):
    """操作者读某设备最近的命令结果(双路验证的回程)。"""
    with _cmd_lock:
        d = _load_cmds()
        results = (d.get(device_id) or {}).get("results", [])
    return {"ok": True, "device_id": device_id, "results": results[-max(1, min(limit, 50)):]}


@android_router.get("/commands/devices")
def cmd_devices():
    """列出有过命令往来的设备(队列/结果计数)。"""
    with _cmd_lock:
        d = _load_cmds()
    return {"ok": True, "devices": [
        {"device_id": k, "queued": len(v.get("queue", [])), "results": len(v.get("results", []))}
        for k, v in d.items()]}


# Native accessibility automation uses its own queue. The Android service keeps
# polling while Xiaohongshu is in the foreground, so it cannot race with the
# legacy WebView command consumer above.


@android_router.api_route("/web-session/authorize", methods=["GET", "POST", "HEAD"])
async def authorize_web_session(req: Request) -> Response:
    """Caddy forward-auth target for a paired LOFA WebView session cookie."""
    device_id = _web_session_device(req)
    if not device_id:
        return JSONResponse(
            {"ok": False, "error": "LOFA device session required"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    return Response(
        status_code=204,
        headers={
            "X-Omni-Device-ID": device_id,
            "X-Omni-Device-Role": "lofa-device",
            "Cache-Control": "no-store",
        },
    )


@android_router.post("/automation/pairing-window")
async def automation_pairing_window(req: Request):
    if not _authorized_controller(req):
        return JSONResponse({"ok": False, "error": "controller authorization required"}, status_code=403)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip() if isinstance(body, dict) else ""
    duration = max(60.0, min(float(body.get("duration_seconds") or 900), 1800.0)) if isinstance(body, dict) else 900.0
    from omnicompany.dashboard.controlplane.lan_access import is_known_lofa_device

    if not device_id or not is_known_lofa_device("", device_id):
        return JSONResponse(
            {"ok": False, "error": "pairing requires an approved LOFA device identity"},
            status_code=409,
        )
    now = time.time()
    expires_at = now + duration
    record = _device_record()
    observed_ip = (
        str(record.get("ip") or "").strip()
        if str(record.get("device_id") or "").strip() == device_id
        else ""
    )
    with _automation_lock:
        data = _load_automation()
        bucket = data.setdefault(device_id, {"queue": [], "results": []})
        attempt_at = float(bucket.get("last_pair_attempt_at") or 0)
        attempt_hash = str(bucket.get("last_pair_attempt_token_hash") or "")
        attempt_recent = bool(attempt_hash) and attempt_at >= now - 120
        if bool(body.get("replace_token")):
            bucket.pop("device_token_hash", None)
        bucket["pairing_expires_at"] = expires_at
        if attempt_recent:
            bucket["pairing_token_hash"] = attempt_hash
            observed_ip = str(bucket.get("last_pair_attempt_ip") or observed_ip)
        else:
            bucket.pop("pairing_token_hash", None)
        _save_automation(data)
    return {
        "ok": True,
        "device_id": device_id,
        "pairing_expires_at": expires_at,
        "pairing_confirmation_ready": attempt_recent,
        "observed_ip": observed_ip,
    }


@android_router.post("/automation/pair")
async def automation_pair(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip() if isinstance(body, dict) else ""
    body_token = str(body.get("device_token") or "") if isinstance(body, dict) else ""
    header_token = str(req.headers.get("x-lofa-device-token") or "")
    if not device_id or not body_token or not secrets.compare_digest(body_token, header_token):
        return JSONResponse({"ok": False, "error": "device identity is incomplete"}, status_code=400)
    from omnicompany.dashboard.controlplane.lan_access import is_known_lofa_device

    if not is_known_lofa_device("", device_id):
        return JSONResponse({"ok": False, "error": "device approval required"}, status_code=403)
    ip = req.client.host if req.client else ""
    now = time.time()
    presented_hash = _token_digest(body_token)
    with _automation_lock:
        data = _load_automation()
        bucket = data.setdefault(device_id, {"queue": [], "results": []})
        existing_hash = str(bucket.get("device_token_hash") or "")
        if existing_hash:
            if not secrets.compare_digest(existing_hash, presented_hash):
                return JSONResponse({"ok": False, "error": "device token rejected"}, status_code=403)
        else:
            valid_window = float(bucket.get("pairing_expires_at") or 0) >= now
            confirmed_hash = str(bucket.get("pairing_token_hash") or "")
            token_confirmed = bool(confirmed_hash) and secrets.compare_digest(
                confirmed_hash,
                presented_hash,
            )
            if not (valid_window and token_confirmed):
                # Record only a digest for controller confirmation. The source IP is
                # retained as audit telemetry and is never compared for identity.
                bucket["last_pair_attempt_token_hash"] = presented_hash
                bucket["last_pair_attempt_at"] = now
                bucket["last_pair_attempt_ip"] = ip
                _save_automation(data)
                return JSONResponse(
                    {"ok": False, "error": "device pairing confirmation required"},
                    status_code=403,
                )
            bucket["device_token_hash"] = presented_hash
            bucket.pop("pairing_expires_at", None)
            bucket.pop("pairing_token_hash", None)
            bucket.pop("last_pair_attempt_token_hash", None)
            bucket.pop("last_pair_attempt_ip", None)
            bucket.pop("last_pair_attempt_at", None)
        bucket["protocol_version"] = int(body.get("protocol_version") or 0)
        bucket["capabilities"] = body.get("capabilities") if isinstance(body.get("capabilities"), list) else []
        bucket["device_runtime"] = str(body.get("device_runtime") or "")
        bucket["device_local_ip"] = str(body.get("device_local_ip") or "")
        bucket["last_seen"] = now
        bucket["ip"] = ip
        _save_automation(data)
    _record_device(ip, device_id)
    return {"ok": True, "device_id": device_id, "paired": True, "last_seen": now}


@android_router.post("/automation/register")
async def automation_register(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip() if isinstance(body, dict) else ""
    if not device_id:
        return JSONResponse({"ok": False, "error": "device_id required"}, status_code=400)
    ip = req.client.host if req.client else ""
    now = time.time()
    with _automation_lock:
        data = _load_automation()
        bucket = data.setdefault(device_id, {"queue": [], "results": []})
        if not _authorized_device(req, device_id, bucket):
            return JSONResponse({"ok": False, "error": "device authorization required"}, status_code=403)
        bucket["last_seen"] = now
        bucket["ip"] = ip
        bucket["protocol_version"] = int(body.get("protocol_version") or bucket.get("protocol_version") or 0)
        bucket["capabilities"] = body.get("capabilities") if isinstance(body.get("capabilities"), list) else bucket.get("capabilities", [])
        bucket["app_version"] = str(body.get("app_version") or "")
        bucket["app_version_code"] = int(body.get("app_version_code") or 0)
        bucket["device_runtime"] = str(body.get("device_runtime") or "")
        bucket["device_local_ip"] = str(body.get("device_local_ip") or "")
        _save_automation(data)
    _record_device(ip, device_id)
    return {"ok": True, "device_id": device_id, "last_seen": now}


@android_router.post("/automation/enqueue")
async def automation_enqueue(req: Request):
    if not _authorized_controller(req):
        return JSONResponse({"ok": False, "error": "controller authorization required"}, status_code=403)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip() if isinstance(body, dict) else ""
    op = str(body.get("op") or "").strip() if isinstance(body, dict) else ""
    if not device_id or op not in AUTOMATION_OPS:
        return JSONResponse(
            {"ok": False, "error": "device_id and an allowlisted automation op are required"},
            status_code=400,
        )
    now = time.time()
    ttl_seconds = max(5.0, min(float(body.get("ttl_seconds") or 90), 300.0))
    idempotency_key = str(body.get("idempotency_key") or uuid.uuid4().hex).strip()
    with _automation_lock:
        data = _load_automation()
        bucket = data.setdefault(device_id, {"queue": [], "results": []})
        if op == "review_remote_probe" and (
            not bucket.get("device_token_hash")
            or op not in set(bucket.get("capabilities") or [])
        ):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "a paired physical device with review_remote_probe capability is required",
                },
                status_code=409,
            )
        previous = next(
            (
                item for item in reversed(bucket.get("recent_commands", []))
                if item.get("idempotency_key") == idempotency_key
            ),
            None,
        )
        if previous is not None:
            return {
                "ok": True,
                "device_id": device_id,
                "command_id": previous["id"],
                "op": previous["op"],
                "deduplicated": True,
            }
        sequence = int(bucket.get("next_sequence") or 1)
        bucket["next_sequence"] = sequence + 1
        command = {
            "id": uuid.uuid4().hex[:16],
            "sequence": sequence,
            "op": op,
            "args": body.get("args") if isinstance(body.get("args"), dict) else {},
            "created_at": now,
            "expires_at": now + ttl_seconds,
            "idempotency_key": idempotency_key,
            "status": "pending",
        }
        bucket.setdefault("queue", []).append(command)
        bucket["recent_commands"] = (bucket.get("recent_commands", []) + [command])[-200:]
        _save_automation(data)
    _signal_automation(device_id)
    return {
        "ok": True,
        "device_id": device_id,
        "command_id": command["id"],
        "sequence": command["sequence"],
        "op": op,
        "expires_at": command["expires_at"],
        "deduplicated": False,
    }


@android_router.get("/automation/poll")
async def automation_poll(req: Request, device_id: str, wait_seconds: float = 25):
    device_id = str(device_id or "").strip()
    if not device_id:
        return JSONResponse({"ok": False, "error": "device_id required"}, status_code=400)
    ip = req.client.host if req.client else ""
    with _automation_lock:
        bucket = _load_automation().get(device_id) or {}
        if not _authorized_device(req, device_id, bucket):
            return JSONResponse({"ok": False, "error": "device authorization required"}, status_code=403)

    def take_pending() -> tuple[list[dict], float]:
        now = time.time()
        with _automation_lock:
            data = _load_automation()
            bucket = data.setdefault(device_id, {"queue": [], "results": []})
            queued = list(bucket.get("queue", []))
            inflight = bucket.get("inflight") if isinstance(bucket.get("inflight"), dict) else {}
            pending: list[dict] = []
            expired: list[dict] = []
            for command_id, record in list(inflight.items()):
                command = record.get("command") if isinstance(record, dict) else None
                if not isinstance(command, dict):
                    inflight.pop(command_id, None)
                    continue
                if float(command.get("expires_at") or 0) < now:
                    expired.append(command)
                    inflight.pop(command_id, None)
                elif float(record.get("lease_until") or 0) <= now:
                    pending.append(command)
                    record["lease_until"] = now + 60.0
                    record["delivery_count"] = int(record.get("delivery_count") or 0) + 1
            for command in queued:
                if float(command.get("expires_at") or 0) < now:
                    expired.append(command)
                    continue
                pending.append(command)
                inflight[str(command.get("id"))] = {
                    "command": command,
                    "lease_until": now + 60.0,
                    "delivery_count": 1,
                }
            bucket["queue"] = []
            bucket["inflight"] = inflight
            bucket["issued"] = (bucket.get("issued", []) + [item.get("id") for item in pending])[-200:]
            if expired:
                expiry_receipts = [
                    {
                        "command_id": item.get("id"),
                        "ok": False,
                        "result": {"error": "command expired before polling"},
                        "ts": now,
                    }
                    for item in expired
                ]
                bucket["results"] = (bucket.get("results", []) + expiry_receipts)[-100:]
            bucket["last_seen"] = now
            bucket["ip"] = ip
            _save_automation(data)
        return pending, now

    event = _automation_event(device_id)
    event.clear()
    pending, now = take_pending()
    if not pending:
        try:
            await asyncio.to_thread(event.wait, max(0.0, min(float(wait_seconds), 25.0)))
        except RuntimeError:
            pass
        event.clear()
        pending, now = take_pending()
    _record_device(ip, device_id)
    return {"ok": True, "device_id": device_id, "commands": pending, "last_seen": now}


@android_router.post("/automation/result")
async def automation_result(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip() if isinstance(body, dict) else ""
    command_id = str(body.get("command_id") or "").strip() if isinstance(body, dict) else ""
    if not device_id or not command_id:
        return JSONResponse({"ok": False, "error": "device_id and command_id required"}, status_code=400)
    receipt = {
        "command_id": command_id,
        "ok": bool(body.get("ok")),
        "result": body.get("result"),
        "ts": time.time(),
    }
    with _automation_lock:
        data = _load_automation()
        bucket = data.setdefault(device_id, {"queue": [], "results": []})
        if not _authorized_device(req, device_id, bucket):
            return JSONResponse({"ok": False, "error": "device authorization required"}, status_code=403)
        if command_id not in bucket.get("issued", []):
            return JSONResponse({"ok": False, "error": "unknown or unissued command"}, status_code=409)
        existing = next(
            (item for item in reversed(bucket.get("results", [])) if item.get("command_id") == command_id),
            None,
        )
        if existing is not None:
            return {"ok": True, "device_id": device_id, "command_id": command_id, "deduplicated": True}
        inflight = bucket.get("inflight") if isinstance(bucket.get("inflight"), dict) else {}
        command_record = inflight.get(command_id) if isinstance(inflight.get(command_id), dict) else {}
        command = command_record.get("command") if isinstance(command_record.get("command"), dict) else {}
        verification = None
        if command.get("op") == "review_remote_probe":
            try:
                verification = _record_review_remote_verification(
                    device_id=device_id,
                    source_ip=req.client.host if req.client else "",
                    bucket=bucket,
                    command=command,
                    body=body,
                )
            except (KeyError, PermissionError, ValueError) as exc:
                return JSONResponse(
                    {"ok": False, "error": f"remote verification rejected: {exc}"},
                    status_code=409,
                )
        bucket["results"] = (bucket.get("results", []) + [receipt])[-100:]
        inflight.pop(command_id, None)
        bucket["inflight"] = inflight
        bucket["last_seen"] = time.time()
        _save_automation(data)
    return {
        "ok": True,
        "device_id": device_id,
        "command_id": command_id,
        "deduplicated": False,
        "remote_verification_recorded": verification is not None,
    }


@android_router.get("/automation/results")
def automation_results(req: Request, device_id: str, limit: int = 20):
    if not _authorized_controller(req):
        return JSONResponse({"ok": False, "error": "controller authorization required"}, status_code=403)
    with _automation_lock:
        data = _load_automation()
        results = (data.get(device_id) or {}).get("results", [])
    return {
        "ok": True,
        "device_id": device_id,
        "results": results[-max(1, min(limit, 100)):],
    }


@android_router.get("/automation/result")
async def automation_result_wait(
    req: Request,
    device_id: str,
    command_id: str,
    wait_seconds: float = 12,
):
    if not _authorized_controller(req):
        return JSONResponse({"ok": False, "error": "controller authorization required"}, status_code=403)
    wait_seconds = max(0.0, min(float(wait_seconds), 30.0))
    deadline = time.monotonic() + wait_seconds
    while True:
        with _automation_lock:
            data = _load_automation()
            results = (data.get(device_id) or {}).get("results", [])
            receipt = next(
                (item for item in reversed(results) if item.get("command_id") == command_id),
                None,
            )
        if receipt is not None:
            return {"ok": True, "done": True, "device_id": device_id, "receipt": receipt}
        if time.monotonic() >= deadline:
            return {"ok": True, "done": False, "device_id": device_id, "command_id": command_id}
        await asyncio.sleep(0.2)


@android_router.get("/automation/devices")
def automation_devices(req: Request):
    if not _authorized_controller(req):
        return JSONResponse({"ok": False, "error": "controller authorization required"}, status_code=403)
    with _automation_lock:
        data = _load_automation()
    return {
        "ok": True,
        "devices": [
            {
                "device_id": device_id,
                "queued": len(bucket.get("queue", [])),
                "inflight": len(bucket.get("inflight", {})),
                "results": len(bucket.get("results", [])),
                "last_seen": bucket.get("last_seen"),
                "ip": bucket.get("ip", ""),
                "paired": bool(bucket.get("device_token_hash")),
                "protocol_version": bucket.get("protocol_version", 0),
                "capabilities": bucket.get("capabilities", []),
                "app_version": bucket.get("app_version", ""),
                "app_version_code": bucket.get("app_version_code", 0),
            }
            for device_id, bucket in data.items()
        ],
    }


@android_router.post("/automation/assets/lease")
async def automation_asset_lease(req: Request):
    if not _authorized_controller(req):
        return JSONResponse({"ok": False, "error": "controller authorization required"}, status_code=403)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip() if isinstance(body, dict) else ""
    paths = body.get("paths") if isinstance(body, dict) else None
    if not device_id or not isinstance(paths, list) or not 1 <= len(paths) <= 18:
        return JSONResponse({"ok": False, "error": "device_id and 1-18 asset paths are required"}, status_code=400)
    workspace = _ws_root().resolve()
    lease_id = uuid.uuid4().hex
    lease_directory = _asset_lease_dir() / lease_id
    lease_directory.mkdir(parents=True, exist_ok=False)
    expires_at = time.time() + max(60.0, min(float(body.get("duration_seconds") or 900), 1800.0))
    manifest: list[dict] = []
    asset_records: dict[str, dict] = {}
    try:
        for index, raw_path in enumerate(paths):
            source = Path(str(raw_path)).expanduser().resolve()
            try:
                source.relative_to(workspace)
            except ValueError:
                raise ValueError(f"asset is outside the OmniCompany workspace: {source}")
            if not source.is_file():
                raise ValueError(f"asset file is missing: {source}")
            payload = source.read_bytes()
            if len(payload) > 32 * 1024 * 1024:
                raise ValueError(f"asset exceeds 32 MiB: {source.name}")
            asset_id = f"{index:02d}-{uuid.uuid4().hex[:12]}"
            suffix = source.suffix.lower()
            staged_path = lease_directory / f"{asset_id}{suffix}"
            staged_path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            item = {
                "asset_id": asset_id,
                "display_name": source.name,
                "mime": mime,
                "size": len(payload),
                "sha256": digest,
                "url": f"/api/android/automation/assets/{lease_id}/{asset_id}",
            }
            manifest.append(item)
            asset_records[asset_id] = {**item, "path": str(staged_path)}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    with _asset_lock:
        leases = _load_asset_leases()
        leases[lease_id] = {
            "device_id": device_id,
            "created_at": time.time(),
            "expires_at": expires_at,
            "assets": asset_records,
        }
        _save_asset_leases(leases)
    return {
        "ok": True,
        "lease_id": lease_id,
        "device_id": device_id,
        "expires_at": expires_at,
        "assets": manifest,
    }


@android_router.get("/automation/assets/{lease_id}/{asset_id}")
def automation_asset_download(req: Request, lease_id: str, asset_id: str):
    with _asset_lock:
        lease = _load_asset_leases().get(str(lease_id)) or {}
    device_id = str(lease.get("device_id") or "")
    if not device_id or float(lease.get("expires_at") or 0) < time.time():
        return JSONResponse({"ok": False, "error": "asset lease is missing or expired"}, status_code=404)
    if not _authorized_device(req, device_id):
        return JSONResponse({"ok": False, "error": "device authorization required"}, status_code=403)
    asset = (lease.get("assets") or {}).get(str(asset_id)) or {}
    path = Path(str(asset.get("path") or ""))
    if not path.is_file():
        return JSONResponse({"ok": False, "error": "leased asset is missing"}, status_code=404)
    return FileResponse(
        str(path),
        media_type=str(asset.get("mime") or "application/octet-stream"),
        filename=str(asset.get("display_name") or path.name),
    )
