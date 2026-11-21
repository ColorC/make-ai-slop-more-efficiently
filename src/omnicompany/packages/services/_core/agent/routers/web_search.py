# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""WebSearchRouter · 网页搜索 SingleTool, 对齐 claude-code WebSearchTool.

参考: 参考项目/claude-code-analysis/src/tools/WebSearchTool/prompt.ts

实现:
  - 默认调 DuckDuckGo HTML 接口 (无需 API key, 但 rate limit 严)
  - 可通过环境变量 OMNI_WEB_SEARCH_BACKEND 切换 (duckduckgo / serper / brave)
  - 返回 markdown 格式的结果列表 (title + url + snippet)
  - 失败/限流 → ToolExecutionError 带清晰消息
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import ClassVar
from urllib.parse import quote_plus

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_DEFAULT_NUM_RESULTS = 10
_MAX_NUM_RESULTS = 25


class WebSearchRouter(SingleToolRouter):
    """Search the web, return markdown-formatted result list."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.web.search",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "WebSearch"
    DESCRIPTION: ClassVar[str] = (
        "Search the web for current information.\n"
        "\n"
        "- Returns markdown list of results (title + URL + snippet)\n"
        "- Use for current events, recent data, or info beyond model cutoff\n"
        "- Default backend: DuckDuckGo (no key); switch via OMNI_WEB_SEARCH_BACKEND env\n"
        "- Cite sources when using results in your response: [title](url)"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "num_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_NUM_RESULTS,
                "description": f"Max results (default {_DEFAULT_NUM_RESULTS}, max {_MAX_NUM_RESULTS})",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only return results from these domains (filter)",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exclude results from these domains",
            },
        },
        "required": ["query"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            raise ToolExecutionError("query is required")

        num_results = int(args.get("num_results", _DEFAULT_NUM_RESULTS))
        if not (1 <= num_results <= _MAX_NUM_RESULTS):
            raise ToolExecutionError(f"num_results must be 1..{_MAX_NUM_RESULTS}")

        allowed = set(args.get("allowed_domains") or [])
        blocked = set(args.get("blocked_domains") or [])

        # 干跑: 不调外网, 返 mock 结果 (但仍走过滤逻辑, 跟真模式行为一致)
        if os.environ.get("OMNI_WEB_SEARCH_DRY_RUN") == "1":
            results = [
                {
                    "title": f"Mock result {i+1} for '{query}'",
                    "url": f"https://example.com/result-{i+1}",
                    "snippet": f"Mock snippet {i+1} (OMNI_WEB_SEARCH_DRY_RUN=1)",
                }
                for i in range(num_results)
            ]
        else:
            backend = os.environ.get("OMNI_WEB_SEARCH_BACKEND", "duckduckgo").lower()
            if backend == "duckduckgo":
                results = self._search_duckduckgo(query, num_results)
            elif backend == "serper":
                results = self._search_serper(query, num_results)
            else:
                raise ToolExecutionError(
                    f"unknown backend {backend!r}. Supported: duckduckgo / serper"
                )

        # 域名过滤
        if allowed:
            results = [r for r in results if any(d in r.get("url", "") for d in allowed)]
        if blocked:
            results = [r for r in results if not any(d in r.get("url", "") for d in blocked)]

        if not results:
            return f"No web results for {query!r} (after domain filtering)"

        return self._format_markdown(query, results[:num_results])

    def _format_markdown(self, query: str, results: list[dict]) -> str:
        lines = [f"Search results for {query!r}:\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "(no title)")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            lines.append(f"{i}. [{title}]({url})")
            if snippet:
                lines.append(f"   {snippet}")
        return "\n".join(lines)

    def _mock_results(self, query: str, n: int) -> str:
        results = [
            {
                "title": f"Mock result {i+1} for '{query}'",
                "url": f"https://example.com/result-{i+1}",
                "snippet": f"Mock snippet {i+1} (OMNI_WEB_SEARCH_DRY_RUN=1)",
            }
            for i in range(n)
        ]
        return self._format_markdown(query, results)

    def _search_duckduckgo(self, query: str, n: int) -> list[dict]:
        """DuckDuckGo HTML 简易解析. 没 API key 但有 rate limit."""
        try:
            import urllib.request
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (omnicompany-WebSearchRouter)"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            raise ToolExecutionError(
                f"DuckDuckGo search failed: {e}. "
                f"Try setting OMNI_WEB_SEARCH_BACKEND=serper with SERPER_API_KEY."
            )

        results: list[dict] = []
        # 极简 regex 解析 (不用 BeautifulSoup 减依赖)
        # 模式: <a class="result__a" href="...">Title</a> ... <a class="result__snippet">...</a>
        for m in re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        ):
            href = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            results.append({"title": title, "url": href, "snippet": ""})
            if len(results) >= n:
                break
        return results

    def _search_serper(self, query: str, n: int) -> list[dict]:
        api_key = os.environ.get("SERPER_API_KEY", "").strip()
        if not api_key:
            raise ToolExecutionError(
                "SERPER_API_KEY env not set. Get one at https://serper.dev"
            )
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://google.serper.dev/search",
                data=json.dumps({"q": query, "num": n}).encode("utf-8"),
                headers={
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise ToolExecutionError(f"Serper search failed: {e}")

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in (data.get("organic") or [])[:n]
        ]
