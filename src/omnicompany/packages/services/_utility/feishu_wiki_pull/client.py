# [OMNI] origin=omnicompany domain=utility/feishu_wiki_pull ts=2026-07-22T14:09:05Z type=script status=active
# [OMNI] summary="实现collab platform应用鉴权、Wiki 递归分页、文档导出和文件下载协议"
# [OMNI] why="直接复用官方 Lark CLI 的底层协议而不读取或写入其配置、profile 与用户 token"
# [OMNI] tags=feishu,wiki,oauth,export,http

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from email.message import Message
from typing import Any, Mapping, Protocol


FEISHU_OPEN_BASE = "https://open.feishu.cn"
FEISHU_ACCOUNTS_BASE = "https://accounts.feishu.cn"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse: ...


class UrllibTransport:
    """Small transport that returns HTTP errors as regular responses."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=dict(headers or {}), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                status=error.code,
                headers=dict(error.headers.items()) if error.headers else {},
                body=error.read(),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise FeishuApiError(
                f"collab platform网络请求失败：{reason}",
                code="network_error",
                endpoint=url,
            ) from error


class FeishuApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        status: int | None = None,
        log_id: str = "",
        endpoint: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.log_id = log_id
        self.endpoint = endpoint

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "code": self.code,
            "http_status": self.status,
            "log_id": self.log_id,
            "endpoint": self.endpoint,
        }


@dataclass(frozen=True)
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class WikiNode:
    space_id: str
    node_token: str
    obj_token: str
    obj_type: str
    parent_node_token: str
    node_type: str
    title: str
    has_child: bool
    order_path: tuple[int, ...]
    folder_parts: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "node_token": self.node_token,
            "obj_token": self.obj_token,
            "obj_type": self.obj_type,
            "parent_node_token": self.parent_node_token,
            "node_type": self.node_type,
            "title": self.title,
            "has_child": self.has_child,
            "order_path": list(self.order_path),
            "folder_parts": list(self.folder_parts),
        }


@dataclass(frozen=True)
class DownloadPayload:
    body: bytes
    file_name: str
    content_type: str


def _json_object(response: HttpResponse, endpoint: str) -> dict[str, Any]:
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FeishuApiError(
            f"collab platform返回了不可解析的响应（HTTP {response.status}）",
            status=response.status,
            endpoint=endpoint,
        ) from error
    if not isinstance(value, dict):
        raise FeishuApiError(
            "collab platform响应不是 JSON 对象",
            status=response.status,
            endpoint=endpoint,
        )
    return value


def _response_log_id(response: HttpResponse) -> str:
    lowered = {key.lower(): value for key, value in response.headers.items()}
    return lowered.get("x-tt-logid", "") or lowered.get("x-request-id", "")


def _download_name(headers: Mapping[str, str], fallback: str) -> str:
    lowered = {key.lower(): value for key, value in headers.items()}
    disposition = lowered.get("content-disposition", "")
    if not disposition:
        return fallback
    message = Message()
    message["content-disposition"] = disposition
    name = message.get_filename()
    return urllib.parse.unquote(name) if name else fallback


class FeishuClient:
    """Direct Feishu OpenAPI client. It never reads or writes lark-cli state."""

    def __init__(
        self,
        access_token: str = "",
        *,
        transport: Transport | None = None,
        timeout: float = 30.0,
        max_retries: int = 4,
        sleep=time.sleep,
    ) -> None:
        self._access_token = access_token
        self._transport = transport or UrllibTransport()
        self._timeout = timeout
        self._max_retries = max_retries
        self._sleep = sleep

    @classmethod
    def tenant_access_token(
        cls,
        app_id: str,
        app_secret: str,
        *,
        transport: Transport | None = None,
        timeout: float = 30.0,
    ) -> str:
        endpoint = f"{FEISHU_ACCOUNTS_BASE}/oauth/v3/token"
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_secret,
            }
        ).encode("utf-8")
        response = (transport or UrllibTransport()).request(
            "POST",
            endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
            timeout=timeout,
        )
        data = _json_object(response, endpoint)
        token = str(data.get("access_token") or "")
        if response.status < 400 and int(data.get("code") or 0) == 0 and token:
            return token
        message = str(data.get("error_description") or data.get("msg") or data.get("error") or "应用鉴权失败")
        raise FeishuApiError(
            message,
            code=data.get("code") or data.get("error"),
            status=response.status,
            log_id=_response_log_id(response),
            endpoint=endpoint,
        )

    @classmethod
    def request_device_authorization(
        cls,
        app_id: str,
        app_secret: str,
        scopes: str,
        *,
        transport: Transport | None = None,
        timeout: float = 30.0,
    ) -> DeviceAuthorization:
        endpoint = f"{FEISHU_ACCOUNTS_BASE}/oauth/v1/device_authorization"
        requested = scopes.split()
        if "offline_access" not in requested:
            requested.append("offline_access")
        basic = base64.b64encode(f"{app_id}:{app_secret}".encode("utf-8")).decode("ascii")
        body = urllib.parse.urlencode(
            {"client_id": app_id, "scope": " ".join(requested)}
        ).encode("utf-8")
        response = (transport or UrllibTransport()).request(
            "POST",
            endpoint,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=body,
            timeout=timeout,
        )
        data = _json_object(response, endpoint)
        if response.status >= 400 or data.get("error"):
            raise FeishuApiError(
                str(data.get("error_description") or data.get("error") or "用户授权初始化失败"),
                code=data.get("error"),
                status=response.status,
                log_id=_response_log_id(response),
                endpoint=endpoint,
            )
        return DeviceAuthorization(
            device_code=str(data.get("device_code") or ""),
            user_code=str(data.get("user_code") or ""),
            verification_url=str(
                data.get("verification_uri_complete") or data.get("verification_uri") or ""
            ),
            expires_in=int(data.get("expires_in") or 600),
            interval=int(data.get("interval") or 5),
        )

    @classmethod
    def poll_device_access_token(
        cls,
        app_id: str,
        app_secret: str,
        device_code: str,
        *,
        interval: int = 5,
        expires_in: int = 600,
        transport: Transport | None = None,
        timeout: float = 30.0,
        sleep=time.sleep,
    ) -> str:
        endpoint = f"{FEISHU_OPEN_BASE}/open-apis/authen/v2/oauth/token"
        deadline = time.monotonic() + max(1, expires_in)
        current_interval = max(1, interval)
        active_transport = transport or UrllibTransport()
        while time.monotonic() < deadline:
            sleep(current_interval)
            body = urllib.parse.urlencode(
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": app_id,
                    "client_secret": app_secret,
                }
            ).encode("utf-8")
            response = active_transport.request(
                "POST",
                endpoint,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
                timeout=timeout,
            )
            data = _json_object(response, endpoint)
            token = str(data.get("access_token") or "")
            if response.status < 400 and token:
                return token
            error = str(data.get("error") or "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                current_interval = min(60, current_interval + 5)
                continue
            raise FeishuApiError(
                str(data.get("error_description") or error or "用户授权失败"),
                code=error or data.get("code"),
                status=response.status,
                log_id=_response_log_id(response),
                endpoint=endpoint,
            )
        raise FeishuApiError("用户授权已超时", code="expired_token", endpoint=endpoint)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        binary: bool = False,
    ) -> HttpResponse | dict[str, Any]:
        if not self._access_token:
            raise FeishuApiError("缺少 access token，无法调用collab platform OpenAPI")
        endpoint = f"{FEISHU_OPEN_BASE}{path}"
        if params:
            endpoint += "?" + urllib.parse.urlencode(
                {key: value for key, value in params.items() if value not in (None, "")}
            )
        headers = {"Authorization": f"Bearer {self._access_token}"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")

        for attempt in range(self._max_retries + 1):
            response = self._transport.request(
                method,
                endpoint,
                headers=headers,
                body=body,
                timeout=self._timeout,
            )
            if response.status == 429 or response.status >= 500:
                if attempt < self._max_retries:
                    self._sleep(min(8, 2**attempt))
                    continue
            if binary:
                if response.status >= 400:
                    payload = _json_object(response, endpoint)
                    raise self._api_error(payload, response, endpoint)
                return response

            payload = _json_object(response, endpoint)
            code = payload.get("code")
            if response.status < 400 and (code in (None, 0, "0")):
                value = payload.get("data", payload)
                if isinstance(value, dict):
                    return value
                return {"value": value}
            if (code == 99991400 or response.status == 429) and attempt < self._max_retries:
                self._sleep(min(8, 2**attempt))
                continue
            raise self._api_error(payload, response, endpoint)
        raise FeishuApiError("collab platform请求重试耗尽", endpoint=endpoint)

    @staticmethod
    def _api_error(payload: Mapping[str, Any], response: HttpResponse, endpoint: str) -> FeishuApiError:
        return FeishuApiError(
            str(payload.get("msg") or payload.get("message") or payload.get("error_description") or "collab platform API 请求失败"),
            code=payload.get("code") or payload.get("error"),
            status=response.status,
            log_id=str(payload.get("log_id") or _response_log_id(response)),
            endpoint=endpoint,
        )

    def list_children(self, space_id: str, parent_node_token: str = "") -> list[dict[str, Any]]:
        path = f"/open-apis/wiki/v2/spaces/{urllib.parse.quote(space_id, safe='')}/nodes"
        page_token = ""
        nodes: list[dict[str, Any]] = []
        while True:
            data = self._request(
                "GET",
                path,
                params={
                    "page_size": 50,
                    "parent_node_token": parent_node_token,
                    "page_token": page_token,
                },
            )
            assert isinstance(data, dict)
            items = data.get("items") or []
            if isinstance(items, list):
                nodes.extend(item for item in items if isinstance(item, dict))
            has_more = bool(data.get("has_more"))
            page_token = str(data.get("page_token") or "")
            if not has_more or not page_token:
                return nodes

    def walk_nodes(self, space_id: str) -> list[WikiNode]:
        queue: deque[tuple[str, tuple[int, ...], tuple[str, ...]]] = deque(
            [("", tuple(), tuple())]
        )
        visited: set[str] = set()
        result: list[WikiNode] = []
        while queue:
            parent_token, parent_order, parent_parts = queue.popleft()
            children = self.list_children(space_id, parent_token)
            for index, item in enumerate(children, start=1):
                node_token = str(item.get("node_token") or "")
                if not node_token or node_token in visited:
                    continue
                visited.add(node_token)
                title = str(item.get("title") or "未命名文档")
                folder = f"{index:03d}-{safe_component(title, 'untitled')}--{node_token[-8:]}"
                node = WikiNode(
                    space_id=str(item.get("space_id") or space_id),
                    node_token=node_token,
                    obj_token=str(item.get("obj_token") or ""),
                    obj_type=str(item.get("obj_type") or ""),
                    parent_node_token=str(item.get("parent_node_token") or parent_token),
                    node_type=str(item.get("node_type") or ""),
                    title=title,
                    has_child=bool(item.get("has_child")),
                    order_path=parent_order + (index,),
                    folder_parts=parent_parts + (folder,),
                )
                result.append(node)
                if node.has_child:
                    queue.append((node.node_token, node.order_path, node.folder_parts))
        return result

    def fetch_markdown(self, obj_token: str) -> str:
        path = f"/open-apis/docs_ai/v1/documents/{urllib.parse.quote(obj_token, safe='')}/fetch"
        data = self._request("POST", path, data={"format": "markdown"})
        assert isinstance(data, dict)
        document = data.get("document")
        if not isinstance(document, dict) or not isinstance(document.get("content"), str):
            raise FeishuApiError("Markdown fetch 响应缺少 data.document.content", endpoint=path)
        return str(document["content"])

    def export_document(
        self,
        obj_token: str,
        obj_type: str,
        file_extension: str,
        *,
        timeout: float = 180.0,
    ) -> DownloadPayload:
        created = self._request(
            "POST",
            "/open-apis/drive/v1/export_tasks",
            data={
                "token": obj_token,
                "type": obj_type,
                "file_extension": file_extension,
            },
        )
        assert isinstance(created, dict)
        ticket = str(created.get("ticket") or "")
        if not ticket:
            raise FeishuApiError("导出任务响应缺少 ticket")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._request(
                "GET",
                f"/open-apis/drive/v1/export_tasks/{urllib.parse.quote(ticket, safe='')}",
                params={"token": obj_token},
            )
            assert isinstance(status, dict)
            export_result = status.get("result")
            export_result = export_result if isinstance(export_result, dict) else {}
            job_status = int(export_result.get("job_status") or 0)
            file_token = str(export_result.get("file_token") or "")
            if job_status == 0 and file_token:
                response = self._request(
                    "GET",
                    f"/open-apis/drive/v1/export_tasks/file/{urllib.parse.quote(file_token, safe='')}/download",
                    binary=True,
                )
                assert isinstance(response, HttpResponse)
                return DownloadPayload(
                    body=response.body,
                    file_name=_download_name(
                        response.headers,
                        str(export_result.get("file_name") or f"{obj_token}.{file_extension}"),
                    ),
                    content_type=str(
                        {key.lower(): value for key, value in response.headers.items()}.get(
                            "content-type", "application/octet-stream"
                        )
                    ),
                )
            if job_status not in (0, 1, 2):
                message = str(export_result.get("job_error_msg") or f"导出任务失败：status={job_status}")
                raise FeishuApiError(message, code=job_status)
            self._sleep(2)
        raise FeishuApiError(f"导出任务等待超时：ticket={ticket}", code="export_timeout")

    def download_file(self, file_token: str) -> DownloadPayload:
        response = self._request(
            "GET",
            f"/open-apis/drive/v1/files/{urllib.parse.quote(file_token, safe='')}/download",
            binary=True,
        )
        assert isinstance(response, HttpResponse)
        return DownloadPayload(
            body=response.body,
            file_name=_download_name(response.headers, file_token),
            content_type=str(
                {key.lower(): value for key, value in response.headers.items()}.get(
                    "content-type", "application/octet-stream"
                )
            ),
        )


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_component(value: str, fallback: str = "item", max_length: int = 56) -> str:
    translated = "".join("_" if char in '<>:"/\\|?*\x00\r\n\t' else char for char in value)
    translated = " ".join(translated.split()).strip(" .")
    if not translated:
        translated = fallback
    if translated.upper() in _WINDOWS_RESERVED:
        translated = f"_{translated}"
    return translated[:max_length].rstrip(" .") or fallback
