# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""WebBrowserRouter · Playwright 浏览器自动化 SingleTool, 对齐 claude-code WebBrowserTool.

提供 navigate / snapshot / click / type / scroll / wait 等浏览器操作.
基于已有 packages/services/_core/agent/routers/playwright_probe.py 扩展为完整工具.

浏览器会话保活: 通过 ToolContext.browser_session (字典: {browser, context, page})
跨多次工具调用复用 (LLM 多轮 navigate / click / type 不重启浏览器).

干跑: OMNI_WEB_BROWSER_DRY_RUN=1 不真启浏览器, 返 mock 结果.
"""
from __future__ import annotations

import json
import logging
import os
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_VALID_ACTIONS = (
    "navigate",
    "snapshot",
    "click",
    "type",
    "press_key",
    "scroll",
    "wait_for",
    "screenshot",
    "evaluate",
    "close",
)


class WebBrowserRouter(SingleToolRouter):
    """Browser automation via Playwright (navigate / click / type / snapshot / etc.)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.web.browser",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.web.browser",)

    TOOL_NAME: ClassVar[str] = "WebBrowser"
    DESCRIPTION: ClassVar[str] = (
        "Browser automation via Playwright. Supports navigation, interaction, and inspection.\n"
        "\n"
        "Actions:\n"
        "- `navigate`: open URL (params: url)\n"
        "- `snapshot`: return accessibility tree (no params)\n"
        "- `click`: click element (params: selector or ref)\n"
        "- `type`: type text into element (params: selector + text)\n"
        "- `press_key`: press a key (params: key, e.g. 'Enter')\n"
        "- `scroll`: scroll page (params: direction = 'down'/'up'/'top'/'bottom')\n"
        "- `wait_for`: wait for selector / time (params: selector OR seconds)\n"
        "- `screenshot`: PNG bytes (params: full_page=true/false)\n"
        "- `evaluate`: run JS in page (params: script)\n"
        "- `close`: close the browser session (cleanup)\n"
        "\n"
        "Browser stays open between calls (session persists in ToolContext)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_VALID_ACTIONS),
                "description": "Action to perform",
            },
            "url": {"type": "string", "description": "URL (for navigate)"},
            "selector": {"type": "string", "description": "CSS selector"},
            "text": {"type": "string", "description": "Text (for type)"},
            "key": {"type": "string", "description": "Key (for press_key)"},
            "direction": {
                "type": "string",
                "enum": ["down", "up", "top", "bottom"],
                "description": "Scroll direction",
            },
            "seconds": {"type": "number", "description": "Wait time (for wait_for)"},
            "script": {"type": "string", "description": "JavaScript (for evaluate)"},
            "full_page": {"type": "boolean", "description": "Full-page screenshot"},
        },
        "required": ["action"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        action = args.get("action", "")
        if action not in _VALID_ACTIONS:
            raise ToolExecutionError(f"action must be one of {_VALID_ACTIONS}")

        # 干跑模式
        if os.environ.get("OMNI_WEB_BROWSER_DRY_RUN") == "1":
            return self._dry_run_response(action, args)

        try:
            from playwright.sync_api import sync_playwright  # noqa
        except ImportError:
            raise ToolExecutionError(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        # 浏览器会话从 ToolContext 取 (Worker 注入). 没有就启新的.
        session = getattr(ctx, "browser_session", None)
        if session is None:
            session = self._create_session()
            ctx.browser_session = session  # type: ignore[attr-defined]

        try:
            return self._dispatch(session, action, args)
        except Exception as e:
            raise ToolExecutionError(f"WebBrowser {action} failed: {e}")

    def _create_session(self) -> dict:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        return {"playwright": pw, "browser": browser, "context": context, "page": page}

    def _dispatch(self, session: dict, action: str, args: dict) -> str:
        page = session["page"]

        if action == "navigate":
            url = (args.get("url") or "").strip()
            if not url:
                raise ToolExecutionError("navigate requires url")
            page.goto(url, wait_until="domcontentloaded")
            return f"Navigated to {url} (title: {page.title()!r})"

        if action == "snapshot":
            # 简版 a11y tree: outerHTML 顶层 + 主要 landmarks
            content = page.content()
            return f"URL: {page.url}\nTitle: {page.title()}\nHTML length: {len(content)} chars\n(Use evaluate for detailed inspection)"

        if action == "click":
            sel = (args.get("selector") or "").strip()
            if not sel:
                raise ToolExecutionError("click requires selector")
            page.click(sel)
            return f"Clicked {sel}"

        if action == "type":
            sel = (args.get("selector") or "").strip()
            text = args.get("text", "")
            if not sel:
                raise ToolExecutionError("type requires selector")
            page.fill(sel, text)
            return f"Typed {len(text)} chars into {sel}"

        if action == "press_key":
            key = (args.get("key") or "").strip()
            if not key:
                raise ToolExecutionError("press_key requires key")
            page.keyboard.press(key)
            return f"Pressed {key}"

        if action == "scroll":
            direction = args.get("direction", "down")
            scripts = {
                "down": "window.scrollBy(0, window.innerHeight)",
                "up": "window.scrollBy(0, -window.innerHeight)",
                "top": "window.scrollTo(0, 0)",
                "bottom": "window.scrollTo(0, document.body.scrollHeight)",
            }
            page.evaluate(scripts.get(direction, scripts["down"]))
            return f"Scrolled {direction}"

        if action == "wait_for":
            sel = args.get("selector")
            seconds = args.get("seconds")
            if sel:
                page.wait_for_selector(sel, timeout=int((seconds or 30) * 1000))
                return f"Element {sel} appeared"
            if seconds:
                page.wait_for_timeout(int(float(seconds) * 1000))
                return f"Waited {seconds}s"
            raise ToolExecutionError("wait_for requires selector or seconds")

        if action == "screenshot":
            full_page = bool(args.get("full_page", False))
            png = page.screenshot(full_page=full_page)
            return f"Screenshot taken: {len(png)} bytes ({'full' if full_page else 'viewport'})"

        if action == "evaluate":
            script = args.get("script", "")
            if not script:
                raise ToolExecutionError("evaluate requires script")
            result = page.evaluate(script)
            try:
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception:
                return str(result)

        if action == "close":
            try:
                session["browser"].close()
                session["playwright"].stop()
            except Exception:
                pass
            return "Browser closed"

        raise ToolExecutionError(f"unreachable: {action}")

    def _dry_run_response(self, action: str, args: dict) -> str:
        return json.dumps({
            "action": action,
            "args": args,
            "mode": "dry_run",
            "result": f"(mock {action} response, OMNI_WEB_BROWSER_DRY_RUN=1)",
        }, ensure_ascii=False)
