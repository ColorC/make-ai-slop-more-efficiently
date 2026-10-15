"""前端观测事件枢纽 —— 2026-08-16「远端画布打不开只能靠猜」的对策。

远端浏览器里的真实故障(WS 握手失败/前端 JS 异常/fetch 4xx-5xx)在服务端日志里
只有间接痕迹(某请求没来), 排障只能靠推断。本模块给前端一个上报通道:

- POST /api/diagnostics/frontend/events  前端批量上报(错误/心跳/业务事件)
- GET  /api/diagnostics/frontend        查询活跃实例与最近事件(agent/诊断用)

存储: 内存 ring(5000) + 追加落盘 logs/frontend-telemetry.jsonl(每行含实例头,
重启不丢历史)。上报端点不新增鉴权面: 网关侧经既有会话(cookie/Basic)可达,
dashboard 本体 8210 与其它 API 同级。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

frontend_diagnostics_router = APIRouter(
    prefix="/api/diagnostics/frontend",
    tags=["frontend-diagnostics"],
)

_MAX_EVENTS = 5000
_HEARTBEAT_WINDOW_SECONDS = 5 * 60

# 落盘目录可注入(测试/隔离场景用): 设 OMNI_FRONTEND_TELEMETRY_DIR 覆盖默认仓库 logs/。
# 惰性解析(每次落盘时读一次环境变量), 而非模块加载期固定路径,
# 这样测试用 monkeypatch.setenv 即可隔离, 不需要重新 import 模块。
_LOG_DIR_ENV = "OMNI_FRONTEND_TELEMETRY_DIR"
_LOG_FILE_NAME = "frontend-telemetry.jsonl"
_LOG_MAX_BYTES = 20 * 1024 * 1024

# 内存 ring: [(received_at, instance_id, event)]
_ring: deque[tuple[float, str, dict[str, Any]]] = deque(maxlen=_MAX_EVENTS)


def _log_dir() -> Path:
    override = os.environ.get(_LOG_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "logs"


def _log_file() -> Path:
    return _log_dir() / _LOG_FILE_NAME


def _append_log(instance: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """落盘尽力而为: 磁盘故障(或任何落盘异常)不能让观测端点 5xx。"""
    try:
        log_dir = _log_dir()
        log_file = log_dir / _LOG_FILE_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        # 体积轮转: 超阈值时把旧文件挪成 .1(覆盖上一份), 参考
        # ccdaemon.resource_monitor._append_jsonl 的做法。
        if log_file.exists() and log_file.stat().st_size >= _LOG_MAX_BYTES:
            archive = log_file.with_suffix(log_file.suffix + ".1")
            os.replace(log_file, archive)
        with log_file.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(
                    {"received_at": time.time(), "instance": instance, "event": event},
                    ensure_ascii=False, separators=(",", ":"),
                ) + "\n")
    except Exception:
        pass


class FrontendEvent(BaseModel):
    t: float = Field(default_factory=time.time)
    kind: str = Field(max_length=64)
    level: str = Field(default="info", max_length=16)
    message: str = Field(default="", max_length=4000)
    data: dict[str, Any] = Field(default_factory=dict)


class FrontendReport(BaseModel):
    instance: dict[str, Any] = Field(default_factory=dict)
    # 上限截断在端点层做(超量丢弃多余而非 422 拒绝整包): 上报通道可用性优先。
    events: list[FrontendEvent] = Field(default_factory=list)


def _summarize() -> dict[str, Any]:
    now = time.time()
    instances: dict[str, dict[str, Any]] = {}
    for received_at, instance_id, event in _ring:
        rec = instances.setdefault(instance_id, {
            "instance_id": instance_id,
            "origin": event.get("_origin") or "",
            "surface": event.get("_surface") or "",
            "last_seen": received_at,
            "first_seen": received_at,
            "online": False,
            "events": 0,
            "errors": 0,
            "last_event": "",
        })
        rec["last_seen"] = max(rec["last_seen"], received_at)
        rec["first_seen"] = min(rec["first_seen"], received_at)
        rec["events"] += 1
        if event.get("level") in ("error", "warn"):
            rec["errors"] += 1
        rec["last_event"] = f"{event.get('kind')}: {event.get('message', '')}"[:160]
    for rec in instances.values():
        rec["online"] = now - float(rec["last_seen"]) <= _HEARTBEAT_WINDOW_SECONDS
    ordered = sorted(instances.values(), key=lambda r: -float(r["last_seen"]))
    return {
        "now": now,
        "total_events": len(_ring),
        "instances": ordered[:40],
    }


@frontend_diagnostics_router.post("/events")
async def receive_frontend_events(report: FrontendReport) -> JSONResponse:
    instance = dict(report.instance)
    instance_id = str(instance.get("instance_id") or "unknown")[:80]
    events: list[dict[str, Any]] = []
    for ev in report.events[:200]:
        event: dict[str, Any] = {
            "t": ev.t, "kind": ev.kind, "level": ev.level,
            "message": ev.message, "data": ev.data,
            # 实例上下文冗余进每条事件, 落盘后单行自含。
            "_origin": str(instance.get("origin") or "")[:200],
            "_surface": str(instance.get("surface") or "")[:40],
        }
        events.append(event)
        _ring.append((time.time(), instance_id, event))
    # Telemetry is diagnostic-only. JSONL rotation/open/write must not make a
    # remote dashboard wait behind a slow filesystem while it is serving PTY
    # and tab-state requests.
    await asyncio.to_thread(_append_log, instance, events)
    return JSONResponse({"ok": True, "accepted": len(events)}, headers={"Cache-Control": "no-store"})


@frontend_diagnostics_router.get("")
async def query_frontend_diagnostics(
    request: Request,
    limit: int = 100,
    level: str = "",
    instance: str = "",
) -> JSONResponse:
    limit = max(1, min(limit, 1000))
    summary = _summarize()
    out: list[dict[str, Any]] = []
    for received_at, instance_id, event in reversed(_ring):
        if level and event.get("level") not in level.split(","):
            continue
        if instance and instance_id != instance:
            continue
        row = dict(event)
        row.pop("_origin", None)
        row.pop("_surface", None)
        row["received_at"] = received_at
        row["instance_id"] = instance_id
        row["origin"] = event.get("_origin") or ""
        row["surface"] = event.get("_surface") or ""
        out.append(row)
        if len(out) >= limit:
            break
    return JSONResponse(
        {**summary, "events": out},
        headers={"Cache-Control": "no-store"},
    )
