# [OMNI] origin=claude-code domain=runtime/buses ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:runtime.buses.web_bus.http_requester.py"
"""WebBus · HTTP 请求统一入口 + 审计层.

收归散落的 `requests` / `httpx` / `openai SDK` 调用.

双重接入方式:
  1. **便捷接口** (轻量场景): `bus.get(url) / bus.post(url, json=...)` 用 stdlib urllib
  2. **审计接入** (SDK 场景): 业务 client (OpenAI / httpx) 内部调
     `bus.precheck_url(url, method)` + `bus.audit_request(...)` + `bus.audit_response(...)`
     transport 仍用 SDK, 只是把审核 + 审计挂上 bus

设计说明 (2026-04-23 用户):
  - 非长线独立工作, **暂不设安全网** (防注入/防事故留给未来长线联网工作)
  - 本版只做: 框架 + 基本原则审核 (URL 粗白名单) + 全量审计
"""
from __future__ import annotations

import json as _json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from omnicompany.runtime.buses.base import ServiceBus

# 已知合法主机 (粗白名单, MVP 不强制拦截 — 仅作警告审计).
# Phase 2 可收紧为"白名单外即拒", 但本版为 agent 灵活度留余地.
_KNOWN_HOSTS = frozenset(
    {
        # 本机 (精确)
        "localhost",
        "127.0.0.1",
    }
)

# 允许的主机后缀 · 以 "." 开头, 所有子域都放行.
_KNOWN_HOST_SUFFIXES = (
    # LLM providers
    ".openai.com",
    ".anthropic.com",
    ".aliyuncs.com",  # dashscope + 阿里云
    ".deepseek.com",
    # collab platform / Lark
    ".feishu.cn",
    ".larksuite.com",
    ".feishu-pre.cn",
    # GitHub
    ".github.com",
    ".githubusercontent.com",
    # Figma
    ".figma.com",
    # Unity
    ".unity.com",
    ".unity3d.com",
)


@dataclass
class WebResponse:
    """轻量响应对象 · 用于 WebBus 便捷接口返回."""

    status_code: int
    headers: dict
    body: bytes
    url: str

    @property
    def text(self) -> str:
        # 尝试 utf-8, 失败回 latin-1
        try:
            return self.body.decode("utf-8")
        except UnicodeDecodeError:
            return self.body.decode("latin-1", errors="replace")

    def json(self) -> Any:
        return _json.loads(self.text)


class WebBus(ServiceBus):
    """HTTP 请求统一入口.

    用法 A (便捷):
      bus = WebBus()
      resp = bus.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer xxx"})

    用法 B (审计接入):
      bus = WebBus()
      bus.precheck_url("https://api.openai.com/v1/chat/completions", "POST")
      corr_id = bus.audit_request(url=..., method="POST", payload_size=1024)
      # ... 用 httpx / openai SDK 真正调用 ...
      bus.audit_response(corr_id, status=200, body_size=2048)
    """

    bus_name = "web"

    def __init__(self, audit_log_path=None, *, enforce_host_whitelist: bool = False, workspace=None):
        """WebBus 构造.

        workspace 参数仅为与其他 bus 统一接口签名 (HTTP 无文件路径, workspace 不强制生效);
        未来长线联网时可用 workspace.read_prefixes 限制响应落盘位置.
        """
        super().__init__(audit_log_path=audit_log_path, workspace=workspace)
        self._enforce_whitelist = enforce_host_whitelist

    def precheck_url(self, url: str, method: str) -> None:
        """URL + method 基本审核.

        当前版本: 仅记录 host 是否已知; enforce_host_whitelist=True 时拒绝未知 host.
        """
        if not url or not isinstance(url, str):
            raise self._reject("request", "empty url", {"url": url, "method": method})
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise self._reject(
                "request",
                f"unsupported scheme: {parsed.scheme}",
                {"url": url, "method": method},
            )
        host = (parsed.hostname or "").lower()
        if self._enforce_whitelist and host:
            if host in _KNOWN_HOSTS:
                return
            if any(host == suffix[1:] or host.endswith(suffix) for suffix in _KNOWN_HOST_SUFFIXES):
                return
            raise self._reject(
                "request",
                f"host not in whitelist: {host}",
                {"url": url, "method": method, "host": host},
            )

    def audit_request(
        self,
        url: str,
        method: str,
        *,
        payload_size: int = 0,
        headers_keys: list[str] | None = None,
        note: str = "",
    ) -> str:
        """记录请求事件, 返回 correlation_id 以便关联响应."""
        corr_id = f"web-{int(time.time() * 1000000)}"
        parsed = urllib.parse.urlparse(url)
        self._audit(
            "request",
            {
                "correlation_id": corr_id,
                "url": url,
                "method": method,
                "host": parsed.hostname,
                "path": parsed.path,
                "payload_size": payload_size,
                "headers_keys": headers_keys or [],
                "note": note,
            },
        )
        return corr_id

    def audit_response(
        self,
        correlation_id: str,
        *,
        status: int,
        body_size: int = 0,
        elapsed_ms: float = 0.0,
        note: str = "",
    ) -> None:
        """记录响应事件."""
        self._audit(
            "response",
            {
                "correlation_id": correlation_id,
                "status": status,
                "body_size": body_size,
                "elapsed_ms": elapsed_ms,
                "note": note,
            },
        )

    # ------- 便捷接口 (stdlib urllib) -------

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        data: bytes | str | None = None,
        json: Any | None = None,
        timeout: float = 60.0,
    ) -> WebResponse:
        """通用请求入口. 自动走 precheck + audit."""
        self.precheck_url(url, method)
        if json is not None:
            if data is not None:
                raise ValueError("cannot pass both data and json")
            data = _json.dumps(json).encode("utf-8")
            headers = {**(headers or {}), "Content-Type": "application/json"}
        elif isinstance(data, str):
            data = data.encode("utf-8")

        payload_size = len(data) if data else 0
        corr_id = self.audit_request(
            url, method, payload_size=payload_size, headers_keys=list((headers or {}).keys())
        )

        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers or {})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                status = resp.status
                resp_headers = dict(resp.headers.items())
                resp_url = resp.url
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            status = e.code
            resp_headers = dict(e.headers.items()) if e.headers else {}
            resp_url = url
        except urllib.error.URLError as e:
            elapsed = (time.perf_counter() - start) * 1000
            self.audit_response(
                corr_id, status=-1, body_size=0, elapsed_ms=elapsed, note=f"URLError: {e.reason}"
            )
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        self.audit_response(corr_id, status=status, body_size=len(body), elapsed_ms=elapsed_ms)
        return WebResponse(status_code=status, headers=resp_headers, body=body, url=resp_url)

    def get(self, url: str, **kw) -> WebResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> WebResponse:
        return self.request("POST", url, **kw)

    def patch(self, url: str, **kw) -> WebResponse:
        return self.request("PATCH", url, **kw)

    def delete(self, url: str, **kw) -> WebResponse:
        return self.request("DELETE", url, **kw)
