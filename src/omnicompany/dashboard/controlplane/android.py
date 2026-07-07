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

import json
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

android_router = APIRouter(prefix="/api/android", tags=["android-lofa"])


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


def _cmd_file() -> Path:
    root = _ws_root()
    d = root / "data" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lofa_commands.json"


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


def _record_device(ip: str) -> None:
    """记下"最近一次连上本机的手机源 IP" —— 扁平网下即手机真实 LAN IP。
    配合固定 adb 端口 5555, PC 侧守护脚本据此 `adb connect <ip>:5555` 自动反向调试,
    用户开一下 app 即可, 永不用再复制调试端口/IP。"""
    if not ip:
        return
    try:
        _device_file().write_text(
            json.dumps({"ip": ip, "adb_port": 5555, "last_seen": time.time()}, ensure_ascii=False),
            encoding="utf-8")
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
    _record_device(ip)
    return {"ok": True, "ip": ip, "adb_port": 5555}


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
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    device_id = str(body.get("device_id") or "").strip()
    op = str(body.get("op") or "").strip()
    if not device_id or not op:
        return JSONResponse({"ok": False, "error": "device_id and op required"}, status_code=400)
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
