# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.web_fetch.http_getter.py"
"""WebFetchRouter · 通用 HTTP GET + HTML→text 清洗 SingleTool.

v1 · raw mode (2026-04-23):
  - 只做 GET, 不 POST
  - 30 秒超时, 超时 raise ToolExecutionError
  - HTML 用 html.parser 简单抽文本 (无 bs4 依赖)
  - 其他 MIME (json / text / markdown) 原样返回
  - 大响应 (>500KB) 截断首 500KB + 标注 '[TRUNCATED]'

v2 · LLM summarize 路径 (未来):
  - 接 `prompt` 参数, 抓到后喂 LLM 摘要, 省下游 context
  - 对齐 Claude Code WebFetch 行为

**Tool Description** 基本对齐 Claude Code WebFetch (`参考项目/claude-code-analysis` 不可查时按通用语义写).

铁律合规:
  - 不预防性截断 (除 500KB 物理上限, 内容本身不 `[:N]` 喂 LLM)
  - 失败 raise, 不静默
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_MAX_BYTES = 500_000  # 500KB 物理上限
_TIMEOUT_SEC = 30
_USER_AGENT = "OmniCompany-WebFetch/1.0 (+omnicompany.packages.services._core.agent)"


class _TextExtractor(HTMLParser):
    """极简 HTML→text: 保留可见文本, 丢 <script>/<style>, 段落加换行."""

    _BLOCK = {"p", "br", "li", "tr", "div", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip_depth:
            return
        data = data.strip()
        if data:
            self._chunks.append(data)
            self._chunks.append(" ")

    def extract(self) -> str:
        raw = "".join(self._chunks)
        # 合并空白但保段落换行
        lines = []
        for line in raw.split("\n"):
            line = " ".join(line.split())
            if line:
                lines.append(line)
        return "\n".join(lines)


class WebFetchRouter(SingleToolRouter):
    CONSUMED_META_IO = ("meta_io.http.get",)
    PRODUCED_META_IO = ()

    """Generic HTTP GET + HTML text extraction.

    No subclassing needed for normal use. Override `_allowed_domain(host)` if
    a package needs to restrict which domains can be fetched.
    """

    TOOL_NAME: ClassVar[str] = "web_fetch"
    DESCRIPTION: ClassVar[str] = (
        "- Fetches content from a specified URL and returns it as plain text.\n"
        "- HTML is stripped to visible text (scripts/styles removed, block tags separate lines).\n"
        "- JSON / plaintext / markdown are returned as-is.\n"
        "- GET only (no POST / auth). 30s timeout. Redirects followed.\n"
        "- Large responses (>500KB) are physically truncated with a [TRUNCATED] marker.\n"
        "- Usage: pass the target URL; output is the extracted text (possibly large — narrow with grep if needed).\n"
        "- On failure (timeout / 4xx / 5xx / DNS), raises an error with the underlying cause.\n"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch. Must include scheme (https:// or http://).",
            },
        },
        "required": ["url"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    # 子类可 override 做域名白名单. 默认允许所有 HTTP(S).
    def _allowed_domain(self, host: str) -> bool:
        return True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        url = (args.get("url") or "").strip()
        if not url:
            raise ToolExecutionError("url is required")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ToolExecutionError(f"unsupported scheme: {parsed.scheme!r} (only http/https)")
        if not parsed.netloc:
            raise ToolExecutionError(f"invalid URL (no host): {url!r}")
        if not self._allowed_domain(parsed.netloc):
            raise ToolExecutionError(f"domain not allowed by router policy: {parsed.netloc}")

        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
                status = resp.status
                ctype = resp.headers.get("Content-Type", "").lower()
                raw = resp.read(_MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            raise ToolExecutionError(f"HTTP {e.code} {e.reason} for {url}")
        except urllib.error.URLError as e:
            raise ToolExecutionError(f"URL error for {url}: {e.reason}")
        except TimeoutError:
            raise ToolExecutionError(f"timeout fetching {url} (>{_TIMEOUT_SEC}s)")
        except Exception as e:
            raise ToolExecutionError(f"fetch failed for {url}: {e}")

        truncated = False
        if len(raw) > _MAX_BYTES:
            raw = raw[:_MAX_BYTES]
            truncated = True

        # 解码
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            body = raw.decode(charset, errors="replace")
        except LookupError:
            body = raw.decode("utf-8", errors="replace")

        # HTML → text
        if "html" in ctype:
            extractor = _TextExtractor()
            try:
                extractor.feed(body)
                body = extractor.extract()
            except Exception as e:
                logger.warning("[WebFetch] HTML parse failed, falling back to raw: %s", e)

        header = f"[url={url} status={status} content_type={ctype or '?'} bytes={len(raw)}"
        if truncated:
            header += " TRUNCATED at 500KB"
        header += "]"
        return f"{header}\n\n{body}"
