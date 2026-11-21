# [OMNI] origin=ai-ide ts=2026-07-16 type=infra
# [OMNI] material_id="material:dashboard.controlplane.android_tunnel.py"
"""controlplane/android_tunnel.py — adb 反向调试隧道 · PC 中继。

飞连(ZTNA)只允许手机→PC 单向连出，adb 不能由 PC 主动连手机。本中继让手机侧桥
(原生 DevTunnelService；本步骤用 mock 桥代替)主动连出到 PC 的 WS，把手机本地
adbd 的字节流反向穿回 PC。PC 侧照常 `adb connect 127.0.0.1:6555`，adb 服务器连到
本中继的 TCP 面，中继在这条 TCP 连接与手机桥之间做透明的原始字节双向转发。

链路::

    adb connect 127.0.0.1:6555
        │ (raw adb 字节流)
    本中继 TCP 面 (127.0.0.1:6555)
        │  {"op":"open"} / binary / {"op":"close"}  over  WS /api/devtunnel/ws
    手机桥 (手机主动连出，穿飞连)
        │  Socket(127.0.0.1, 5555)
    手机 adbd

约束:
- 对字节流保持透明，不解析 adb 协议(adb 自己在这条连接上做 stream 复用)。
- adb 面只绑 127.0.0.1(仅本机 adb 可连，不对外暴露)。
- 单条 adb TCP 连接 ↔ 单个已注册手机桥 WS(adb 服务器对每台设备只保持一条传输连接)。
- 手机桥接入 WS 须带 device token(query `?token=` 或头 `X-LOFA-Device-Token`)。

控制协议(WS 帧):
- 文本帧 JSON `{"op":"open"}`  中继→桥：为新 adb 连接打开一个到 adbd 的 socket。
- 文本帧 JSON `{"op":"close"}` 双向：一侧连接关闭时通知对端关闭。
- 二进制帧：透明的 adb↔adbd 原始字节。

既能作为 router 被 dashboard(8210) `include_router` 挂载，也能 `run_standalone`
独立起(WS + adb 面 TCP server)自成一体，供不重启 live dashboard 的隔离验证。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger("omnicompany.dashboard.controlplane.android_tunnel")

devtunnel_router = APIRouter(prefix="/api/devtunnel", tags=["devtunnel"])

# 单帧转发块大小；adb 协议自身分包，中继只按 socket 读到多少转多少。
_CHUNK = 65536


class DevTunnelRelay:
    """一台手机桥 ↔ 一条 adb TCP 连接的原始字节中继。

    单例即可：adb 服务器对每台设备只维持一条传输连接，手机桥也只需一个。新的桥/新的
    adb 连接接入时顶替旧的(reconnect 友好)。
    """

    def __init__(self, expected_token: str | None = None) -> None:
        # 步骤①：单一共享 token。后续可换成按 query 的 device_id 查 android.py
        # automation 的 device_token_hash(见 controlplane/android.py `_authorized_device`)。
        self.expected_token: str | None = (
            expected_token
            if expected_token is not None
            else os.environ.get("LOFA_DEVTUNNEL_TOKEN")
        )
        self.adb_face: str | None = None

        self._bridge: WebSocket | None = None
        self._adb_writer: asyncio.StreamWriter | None = None
        self._adb_seq: int = 0            # 递增，用于识别当前活跃 adb 连接
        self._active_seq: int = 0
        self._adb_close_notified: bool = False  # 桥已发起关闭，finally 不再回发
        self._send_lock = asyncio.Lock()  # 串行化对手机桥 WS 的写(避免并发帧交错)
        self._tcp_server: asyncio.AbstractServer | None = None

    # ── 状态(供健康检查/就绪探测) ─────────────────────────────────────────────
    @property
    def bridge_connected(self) -> bool:
        return self._bridge is not None

    @property
    def adb_connected(self) -> bool:
        return self._adb_writer is not None

    # ── 鉴权 ─────────────────────────────────────────────────────────────────
    def _token_ok(self, supplied: str) -> bool:
        expected = self.expected_token
        if not expected:
            return False  # 未配置令牌 → 一律拒绝(安全默认)
        return bool(supplied) and secrets.compare_digest(str(supplied), str(expected))

    # ── 对手机桥的串行写 ───────────────────────────────────────────────────────
    async def _bridge_send_bytes(self, data: bytes) -> bool:
        ws = self._bridge
        if ws is None:
            return False
        async with self._send_lock:
            if ws is not self._bridge or ws.client_state != WebSocketState.CONNECTED:
                return False
            try:
                await ws.send_bytes(data)
                return True
            except Exception:
                return False

    async def _bridge_send_text(self, text: str) -> bool:
        ws = self._bridge
        if ws is None:
            return False
        async with self._send_lock:
            if ws is not self._bridge or ws.client_state != WebSocketState.CONNECTED:
                return False
            try:
                await ws.send_text(text)
                return True
            except Exception:
                return False

    # ── 手机桥 WS 接入(手机→PC 方向) ────────────────────────────────────────────
    async def handle_bridge(self, ws: WebSocket) -> None:
        supplied = ws.query_params.get("token") or ws.headers.get("x-lofa-device-token") or ""
        if not self._token_ok(supplied):
            # 握手阶段拒绝：客户端 connect 直接失败(令牌错要让对端明确知道)。
            try:
                await ws.close(code=1008)  # policy violation
            except Exception:
                pass
            logger.warning("devtunnel bridge rejected: bad token")
            return

        await ws.accept()
        old = self._bridge
        self._bridge = ws
        if old is not None and old is not ws:
            try:
                await old.close()
            except Exception:
                pass
        logger.info("devtunnel bridge connected: %s", ws.client)

        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is not None:
                    # 桥→adb：写回当前活跃 adb 连接。
                    w = self._adb_writer
                    if w is not None and not w.is_closing():
                        try:
                            w.write(data)
                            await w.drain()
                        except (ConnectionError, OSError):
                            pass
                    continue
                text = msg.get("text")
                if text is not None:
                    try:
                        op = json.loads(text).get("op")
                    except Exception:
                        op = None
                    if op == "close":
                        await self._on_bridge_close()
        except WebSocketDisconnect:
            pass
        finally:
            if self._bridge is ws:
                self._bridge = None
                await self._on_bridge_close()
        logger.info("devtunnel bridge disconnected")

    async def _on_bridge_close(self) -> None:
        """手机桥发起关闭(adbd EOF)或桥掉线：关掉当前 adb 连接，标记已通知避免回发。"""
        self._adb_close_notified = True
        w = self._adb_writer
        self._adb_writer = None
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    # ── adb 面 TCP server ─────────────────────────────────────────────────────
    async def start_tcp_server(self, host: str, port: int) -> None:
        self._tcp_server = await asyncio.start_server(self._handle_adb_conn, host, port)
        self.adb_face = f"{host}:{port}"
        logger.info("devtunnel adb face listening on %s:%s", host, port)

    async def stop_tcp_server(self) -> None:
        srv = self._tcp_server
        self._tcp_server = None
        if srv is not None:
            srv.close()
            try:
                await srv.wait_closed()
            except Exception:
                pass

    async def _handle_adb_conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._bridge is None:
            writer.close()  # 没有桥可服务，直接拒
            return

        self._adb_seq += 1
        seq = self._adb_seq
        old = self._adb_writer
        self._adb_writer = writer
        self._active_seq = seq
        self._adb_close_notified = False
        if old is not None and old is not writer:
            try:
                old.close()  # 顶替旧连接(reconnect)
            except Exception:
                pass

        # 让桥(重新)打开到 adbd 的 socket；op:open 在桥侧隐含 teardown 旧 socket。
        if not await self._bridge_send_text(json.dumps({"op": "open"})):
            if self._active_seq == seq:
                self._adb_writer = None
            writer.close()
            return

        try:
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    break  # adb 客户端关闭
                if not await self._bridge_send_bytes(data):
                    break
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
            # 仅仍活跃的连接、且非桥先发起关闭时，才回发 op:close。
            if self._active_seq == seq:
                self._adb_writer = None
                if not self._adb_close_notified:
                    await self._bridge_send_text(json.dumps({"op": "close"}))


# 模块级单例：既供 router 端点使用，也供 run_standalone 复用。
_relay = DevTunnelRelay()


@devtunnel_router.websocket("/ws")
async def devtunnel_ws(ws: WebSocket) -> None:
    """手机桥接入口。字节走 WS 二进制帧，控制走 JSON 文本帧。"""
    await _relay.handle_bridge(ws)


@devtunnel_router.get("/status")
async def devtunnel_status() -> dict:
    """就绪/健康探测：桥是否已注册、adb 连接是否活跃、adb 面监听地址。"""
    return {
        "bridge_connected": _relay.bridge_connected,
        "adb_connected": _relay.adb_connected,
        "adb_face": _relay.adb_face,
    }


def run_standalone(
    adb_port: int = 6555,
    ws_port: int = 8211,
    token: str | None = None,
    adb_host: str = "127.0.0.1",
    ws_host: str = "127.0.0.1",
    ws_impl: str = "wsproto",
    ws_ping_interval: float = 20.0,
) -> None:
    """独立起中继：uvicorn 跑 WS(手机面) + asyncio TCP server 跑 adb 面。

    两者共用同一事件循环(TCP server 在 uvicorn 的 startup 钩子里起)，从而共享中继状态。
    供隔离验证用，不影响 live dashboard(8210)。

    ws_impl 默认 wsproto：legacy 的 `websockets` 实现把 keepalive ping / 自动 pong 的写帧
    走 asyncio drain()，会与本中继在同一连接上的并发 send_bytes drain 撞上 legacy
    `_drain_helper` 的 `assert waiter is None` —— 大流量下(如 install 大 APK 的持续发送遇上
    20s keepalive ping)必崩连、传输中断。wsproto 实现所有帧都走 transport.write()，并用
    pause_writing/resume_writing 做真正的发送背压，既不撞该断言又保留 keepalive 与背压。
    wsproto 未装时退化到 websockets-sansio(同样不撞断言，但无发送背压)。
    """
    import uvicorn
    from fastapi import FastAPI

    if token is not None:
        _relay.expected_token = token

    resolved_ws = ws_impl
    if ws_impl == "wsproto":
        try:
            import wsproto  # noqa: F401
        except Exception:
            resolved_ws = "websockets-sansio"
            logger.warning("wsproto 未安装，退化 ws 实现为 websockets-sansio(无发送背压)")

    app = FastAPI(title="LOFA devtunnel relay (standalone)")
    app.include_router(devtunnel_router)

    @app.on_event("startup")
    async def _start() -> None:  # noqa: D401
        await _relay.start_tcp_server(adb_host, adb_port)

    @app.on_event("shutdown")
    async def _stop() -> None:  # noqa: D401
        await _relay.stop_tcp_server()

    uvicorn.run(
        app,
        host=ws_host,
        port=ws_port,
        log_level="info",
        ws=resolved_ws,
        ws_ping_interval=ws_ping_interval,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LOFA adb 反向隧道 PC 中继(独立运行)")
    parser.add_argument("--adb-port", type=int, default=6555, help="adb 面 TCP 端口(只绑本机)")
    parser.add_argument("--ws-port", type=int, default=8211, help="手机桥 WS 端口")
    parser.add_argument("--adb-host", default="127.0.0.1", help="adb 面绑定地址(须为本机)")
    parser.add_argument("--ws-host", default="127.0.0.1", help="WS 绑定地址")
    parser.add_argument(
        "--token",
        default=os.environ.get("LOFA_DEVTUNNEL_TOKEN"),
        help="手机桥接入用的共享 device token",
    )
    parser.add_argument("--ws-impl", default="wsproto",
                        help="uvicorn websocket 实现(默认 wsproto，见 run_standalone 注释)")
    parser.add_argument("--ws-ping-interval", type=float, default=20.0,
                        help="WS keepalive ping 间隔(秒)；压测时调小以逼出并发写竞态")
    args = parser.parse_args()
    run_standalone(
        adb_port=args.adb_port,
        ws_port=args.ws_port,
        token=args.token,
        adb_host=args.adb_host,
        ws_host=args.ws_host,
        ws_impl=args.ws_impl,
        ws_ping_interval=args.ws_ping_interval,
    )
