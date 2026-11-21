# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-24T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.browser_probe.playwright.py"
"""PlaywrightProbeRouter · 浏览器端视觉 + DOM + console 探针 SingleTool.

用途: Stage D DevAgent 验证 UI 任务时, curl 只能查 HTTP/静态内容,
但 "body 背景色 #0d1117 实际生效" / "点击 backdrop 触发 onDismiss" / "截屏对比"
都要真浏览器. PlaywrightProbeRouter 封装 async_playwright + chromium headless
暴露给 agent 最小接口.

**安全约束** (骨架级):
  - 仅允许 `http://127.0.0.1:*` / `http://localhost:*` / `file://` URL
    (外网 URL 绝对禁止, 避免 agent 被 prompt 注入诱导访问外部)
  - screenshot 落盘必须在 ToolContext.allowed_screenshot_roots 声明的根下
    (与 WriteFileRouter 同白名单机制)
  - wait_for 超时硬上限 30s

**返回字段** (JSON 序列化的 dict):
  - title
  - status
  - html (最多 100KB)
  - text (body.innerText, 供 LLM 读页面内容)
  - console_logs: list[str]
  - computed_styles: dict | None (若传 query_selectors + style_props)
  - screenshot_path: str | None (若 save_screenshot=true)
  - dom_counts: dict[str, int] (若 count_selectors 传入)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)

_MAX_HTML = 100_000
_MAX_TEXT = 50_000
_LOCAL_HOST_PATTERN = re.compile(r"^(127\.0\.0\.1|localhost|::1)$", re.IGNORECASE)


def _url_is_local(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme == "file":
        return True
    if p.scheme not in ("http", "https"):
        return False
    if not p.hostname:
        return False
    return bool(_LOCAL_HOST_PATTERN.match(p.hostname))


async def _probe(
    url: str,
    *,
    wait_for: str | None,
    wait_timeout_ms: int,
    screenshot_path: Path | None,
    click_selector: str | None,
    query_selectors_styles: dict,
    count_selectors: list[str],
) -> dict:
    from playwright.async_api import async_playwright  # lazy import

    result: dict[str, Any] = {
        "url": url,
        "title": None,
        "status": None,
        "html": None,
        "text": None,
        "console_logs": [],
        "page_errors": [],
        "computed_styles": {},
        "dom_counts": {},
        "screenshot_path": None,
        "clicked": None,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()

            # 捕 console + page errors
            page.on("console", lambda msg: result["console_logs"].append(
                f"[{msg.type}] {msg.text}"[:500]
            ))
            page.on("pageerror", lambda err: result["page_errors"].append(str(err)[:500]))

            response = await page.goto(url, wait_until="domcontentloaded", timeout=wait_timeout_ms)
            if response:
                result["status"] = response.status

            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=wait_timeout_ms)
                except Exception as e:
                    result["page_errors"].append(f"wait_for {wait_for!r} failed: {e}")

            result["title"] = await page.title()

            html_raw = await page.content()
            if len(html_raw) > _MAX_HTML:
                html_raw = html_raw[:_MAX_HTML] + "\n<!-- TRUNCATED at 100KB -->"
            result["html"] = html_raw

            try:
                body_text = await page.evaluate("() => document.body?.innerText || ''")
                if len(body_text) > _MAX_TEXT:
                    body_text = body_text[:_MAX_TEXT] + "\n[TRUNCATED at 50KB]"
                result["text"] = body_text
            except Exception as e:
                result["page_errors"].append(f"body.innerText probe failed: {e}")

            # computed_styles: { selector: { prop: value, ... } }
            for sel, props in query_selectors_styles.items():
                styles = {}
                try:
                    element_handle = await page.query_selector(sel)
                    if element_handle is None:
                        styles["__error"] = "selector not found"
                    else:
                        for prop in props:
                            val = await page.evaluate(
                                "([el, p]) => getComputedStyle(el).getPropertyValue(p)",
                                [element_handle, prop],
                            )
                            styles[prop] = val.strip()
                except Exception as e:
                    styles["__error"] = str(e)
                result["computed_styles"][sel] = styles

            # DOM 计数
            for sel in count_selectors:
                try:
                    count = await page.evaluate(
                        "(s) => document.querySelectorAll(s).length", sel,
                    )
                    result["dom_counts"][sel] = int(count)
                except Exception as e:
                    result["dom_counts"][sel] = f"error: {e}"

            # 点击交互
            if click_selector:
                try:
                    await page.click(click_selector, timeout=wait_timeout_ms)
                    result["clicked"] = click_selector
                    # 点击后再快照一下 dom 变化 (e.g., modal 消失)
                    await page.wait_for_timeout(300)
                    post_click_html = await page.content()
                    if len(post_click_html) > _MAX_HTML:
                        post_click_html = post_click_html[:_MAX_HTML] + "\n<!-- TRUNCATED -->"
                    result["html_after_click"] = post_click_html
                    # 点击后重算 dom_counts (用户 2026-04-24: click 前 count 不够用)
                    result["dom_counts_after_click"] = {}
                    for sel in count_selectors:
                        try:
                            count = await page.evaluate(
                                "(s) => document.querySelectorAll(s).length", sel,
                            )
                            result["dom_counts_after_click"][sel] = int(count)
                        except Exception as e:
                            result["dom_counts_after_click"][sel] = f"error: {e}"
                except Exception as e:
                    result["page_errors"].append(f"click {click_selector!r} failed: {e}")

            # 截屏
            if screenshot_path is not None:
                try:
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    result["screenshot_path"] = str(screenshot_path)
                except Exception as e:
                    result["page_errors"].append(f"screenshot failed: {e}")
        finally:
            await browser.close()

    return result


class PlaywrightProbeRouter(SingleToolRouter):
    CONSUMED_META_IO = ("meta_io.http.get",)
    PRODUCED_META_IO = ("meta_io.fs.create_file",)  # screenshot 落盘

    """Headless Chromium probe for local URLs.

    Context-injected safety:
      - allowed_screenshot_roots: tuple[str] — screenshot 落盘必须在某 root 下
    """

    TOOL_NAME: ClassVar[str] = "playwright_probe"
    DESCRIPTION: ClassVar[str] = (
        "Launch headless Chromium to probe a local URL (127.0.0.1 / localhost / file://). "
        "External URLs are REFUSED for security.\n"
        "\nReturns JSON with:\n"
        "- title, status, html (up to 100KB), text (body.innerText up to 50KB)\n"
        "- console_logs, page_errors (runtime JS errors)\n"
        "- computed_styles (if `query_selectors_styles` supplied: map selector → [css-prop ...])\n"
        "- dom_counts (if `count_selectors` supplied: list of selectors → count)\n"
        "- screenshot_path (if `save_screenshot` true; path must be in allowed_screenshot_roots)\n"
        "- html_after_click (if `click_selector` supplied)\n"
        "- dom_counts_after_click (if `click_selector` + `count_selectors` both supplied): "
        "re-counts after click for before/after comparison\n"
        "\nUse for:\n"
        "- Verifying CSS variables actually apply (check body computed color is #0d1117)\n"
        "- Verifying click → dismiss behavior on modals\n"
        "- Smoke-testing page renders without JS errors\n"
        "- Visual regression via screenshot diff\n"
        "\nDo NOT use for external sites (refused). For HTTP-only probes, use `web_fetch` instead."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Target URL (http://127.0.0.1:*, http://localhost:*, or file://)."},
            "wait_for": {"type": "string", "description": "CSS selector to wait for (optional)."},
            "wait_timeout_ms": {"type": "integer", "minimum": 100, "maximum": 30000, "description": "Timeout ms (default 10000, max 30000)."},
            "query_selectors_styles": {
                "type": "object",
                "description": "Map selector → [css-property names]. Returns computedStyle values.",
                "additionalProperties": {"type": "array", "items": {"type": "string"}},
            },
            "count_selectors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of selectors; returns dom_counts[selector] = number of matches.",
            },
            "click_selector": {"type": "string", "description": "After initial load, click this selector. Returns html_after_click."},
            "save_screenshot": {"type": "boolean", "description": "If true, save full-page PNG to screenshot_path."},
            "screenshot_path": {"type": "string", "description": "Absolute path (must be in allowed_screenshot_roots)."},
        },
        "required": ["url"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False  # chromium 启动开销, 串行安全
    IS_READONLY: ClassVar[bool] = False  # screenshot 会落盘

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        url = (args.get("url") or "").strip()
        if not url:
            raise ToolExecutionError("url is required")
        if not _url_is_local(url):
            raise ToolExecutionError(
                f"playwright_probe REFUSED: only local URLs allowed (127.0.0.1 / localhost / file://). "
                f"Got: {url!r}. External probe is out of scope for dev agent — "
                f"use web_fetch for external HTTP probes instead."
            )

        wait_for = args.get("wait_for")
        wait_timeout = int(args.get("wait_timeout_ms", 10000))
        wait_timeout = max(100, min(wait_timeout, 30000))

        query_selectors_styles = args.get("query_selectors_styles") or {}
        if not isinstance(query_selectors_styles, dict):
            raise ToolExecutionError("query_selectors_styles must be a dict")

        count_selectors = args.get("count_selectors") or []
        if not isinstance(count_selectors, list):
            raise ToolExecutionError("count_selectors must be a list")

        click_selector = args.get("click_selector")

        screenshot_path: Path | None = None
        if args.get("save_screenshot"):
            raw_sp = args.get("screenshot_path")
            if not raw_sp:
                raise ToolExecutionError("save_screenshot=true requires screenshot_path")
            allowed_shot_roots = getattr(ctx, "allowed_screenshot_roots", None) or ()
            if not allowed_shot_roots:
                raise ToolExecutionError(
                    "save_screenshot=true but no allowed_screenshot_roots in tool context — "
                    "Worker cannot save screenshots anywhere."
                )
            sp_abs = Path(raw_sp).resolve()
            ok = False
            roots_resolved = []
            for r in allowed_shot_roots:
                try:
                    rr = Path(r).resolve()
                except Exception:
                    continue
                roots_resolved.append(str(rr))
                try:
                    sp_abs.relative_to(rr)
                    ok = True
                    break
                except ValueError:
                    continue
            if not ok:
                listing = "\n  - ".join(sorted(roots_resolved))
                raise ToolExecutionError(
                    f"playwright_probe REFUSED: screenshot_path {sp_abs} outside allowed_screenshot_roots.\n"
                    f"Allowed:\n  - {listing}"
                )
            screenshot_path = sp_abs

        # 运行异步 probe (本 tool 同步 _execute, playwright 本身 async)
        try:
            result = asyncio.run(_probe(
                url,
                wait_for=wait_for,
                wait_timeout_ms=wait_timeout,
                screenshot_path=screenshot_path,
                click_selector=click_selector,
                query_selectors_styles=query_selectors_styles,
                count_selectors=count_selectors,
            ))
        except RuntimeError as e:
            # 若已在 event loop 内, asyncio.run 会抛 "loop already running"
            # AgentNodeLoop 内部有 loop, 需用 _to_thread 外包或 nest_asyncio
            if "loop is already running" in str(e) or "cannot be called" in str(e):
                # fallback: 新建独立 loop
                new_loop = asyncio.new_event_loop()
                try:
                    result = new_loop.run_until_complete(_probe(
                        url,
                        wait_for=wait_for,
                        wait_timeout_ms=wait_timeout,
                        screenshot_path=screenshot_path,
                        click_selector=click_selector,
                        query_selectors_styles=query_selectors_styles,
                        count_selectors=count_selectors,
                    ))
                finally:
                    new_loop.close()
            else:
                raise ToolExecutionError(f"probe failed: {e}")
        except Exception as e:
            raise ToolExecutionError(f"playwright_probe error: {e}")

        return json.dumps(result, ensure_ascii=False, indent=2)
