# [OMNI] origin=claude-code domain=dashboard/boss_sight/reviewstage ts=2026-06-25T13:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.reviewstage.preview.py"
"""审阅材料封面预览 —— 无头浏览器(Playwright headless)给每张卡截真预览图。

用户(2026-06-25): "预览图没有,封面没有" + "不要在用户不需要的时候弹窗"。
→ headless 截图天然无窗口(对齐 [[windows_machine_security_constraints]] 零窗口原则)。

- 网页类(html/demo/static-report/custom_web_template/webgame-spec):有 url → 导航截图;
  否则 inline_content 是整页 HTML → set_content 截图。
- 文本类(markdown/plan/agent-workflow-report):正文渲染成暗色 HTML → 截图(真·渲染预览,
  不是裸文字)。
- 图片自己就是封面(不在此);视频留 ▶。

封面缓存在 store.root/covers/<id>_<ver>.png(ver=updated_at 短哈希,材料变了自动失效)。
Playwright sync API 不能在 asyncio loop 线程跑 → 路由层用 run_in_executor 丢线程池调本模块。
"""
from __future__ import annotations

import hashlib
import html as _html
import os
import threading
from pathlib import Path
from typing import Any

# 网页/图:整页概览,缩到卡宽看仍 OK。文本:窄幅大字渲染,缩到卡宽后字仍可读(用户
# 2026-06-25:"文本类更大一点,过小的文本不具备阅览意义")。
WEB_VIEWPORT = {"width": 960, "height": 600}
TEXT_VIEWPORT = {"width": 600, "height": 460}
_RENDER_VERSION = "2"  # 渲染策略版本号:改了它 → 所有旧封面失效、重截
WEB_KINDS = {"html", "demo", "static-report", "custom_web_template", "webgame-spec"}
TEXT_KINDS = {"markdown", "plan", "agent-workflow-report"}
COVER_KINDS = WEB_KINDS | TEXT_KINDS

# 一次只跑一个浏览器(刷新串行,省内存,避免并发起多个 chromium)。
_gen_lock = threading.Lock()


def _origin() -> str:
    return os.environ.get("OMNI_DASHBOARD_URL", "http://127.0.0.1:8210").rstrip("/")


def cover_dir(store_root: Path) -> Path:
    d = store_root / "covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ver(m: Any) -> str:
    raw = str(getattr(m, "updated_at", "") or getattr(m, "created_at", "") or "") + "|" + _RENDER_VERSION
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def cover_path(store_root: Path, material_id: str, ver: str) -> Path:
    return cover_dir(store_root) / f"{material_id}_{ver}.png"


def _kind(m: Any) -> str:
    k = getattr(m, "kind", "")
    return k.value if hasattr(k, "value") else str(k)


def is_cover_kind(m: Any) -> bool:
    return _kind(m) in COVER_KINDS


def _full_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return _origin() + url
    return _origin() + "/" + url


def _doc_html(text: str) -> str:
    """文本正文 → 暗色渲染 HTML(markdown 优先,缺库退 <pre>)。"""
    text = (text or "").strip()
    try:
        import markdown as _md  # type: ignore
        body = _md.markdown(text[:8000], extensions=["fenced_code", "tables"])
    except Exception:  # noqa: BLE001 — 无 markdown 库 → 纯文本兜底
        body = "<pre style='white-space:pre-wrap'>" + _html.escape(text[:4000]) + "</pre>"
    # 字号按"渲染宽 600 → 卡宽约 300(0.5 缩放)"反推: 26px 渲染 ≈ 13px 上屏, 可读。
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "html,body{margin:0;background:#0d1117;color:#e6edf3;"
        "font-family:'Segoe UI',system-ui,'Microsoft YaHei',sans-serif}"
        "body{padding:26px 30px;font-size:26px;line-height:1.5}"
        "h1,h2,h3{color:#e6edf3;margin:.35em 0 .25em;line-height:1.25}"
        "h1{font-size:38px}h2{font-size:31px;color:#58a6ff}h3{font-size:28px}"
        "code,pre{background:#161b22;border-radius:6px;font-family:Consolas,monospace}"
        "pre{padding:12px;overflow:hidden;font-size:21px}code{padding:2px 7px;font-size:23px}"
        "a{color:#58a6ff}table{border-collapse:collapse}td,th{border:1px solid #30363d;padding:6px 10px}"
        "p{margin:.45em 0}ul,ol{margin:.4em 0 .4em 1.3em}"
        "</style></head><body>" + body + "</body></html>"
    )


def _render_spec(m: Any) -> tuple[str, str] | None:
    """('url', full_url) | ('html', inline_html) | ('doc', doc_html) | ('file','') | None。

    mode 决定截图视口:url/html=网页大视口;doc=文本窄幅大字视口。
    """
    kind = _kind(m)
    extra = getattr(m, "extra", None) or {}
    inline = getattr(m, "inline_content", None)
    if kind in WEB_KINDS:
        url = extra.get("live_url") or extra.get("url") or ""
        if url:
            return ("url", _full_url(str(url)))
        if inline and "<" in inline:
            return ("html", inline)
        return None
    if kind in TEXT_KINDS:
        text = inline or ""
        if not text and getattr(m, "file_relpath", None):
            return ("file", "")  # 调用方负责读文件(store 解析路径)
        if text:
            return ("doc", _doc_html(text))
        return None
    return None


def generate_for(materials: list[Any], store_root: Path, *, read_text=None) -> dict[str, Any]:
    """给一批材料生成缺失/过期的封面。read_text(m)->str 供文件型文本材料取正文。

    返回 {generated:[ids], skipped:[ids], errors:[(id,msg)], available:bool}。
    """
    todo: list[tuple[Any, str, str]] = []  # (m, mode, payload)
    skipped: list[str] = []
    for m in materials:
        if not is_cover_kind(m):
            continue
        ver = _ver(m)
        out = cover_path(store_root, m.id, ver)
        if out.exists() and out.stat().st_size > 0:
            skipped.append(m.id)
            continue
        spec = _render_spec(m)
        if spec is None:
            continue
        mode, payload = spec
        if mode == "file" and read_text is not None:
            try:
                payload = _doc_html(read_text(m) or "")
                mode = "doc"
            except Exception:  # noqa: BLE001
                continue
        if mode == "file":
            continue
        todo.append((m, mode, payload))

    if not todo:
        return {"generated": [], "skipped": skipped, "errors": [], "available": True}

    with _gen_lock:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:  # noqa: BLE001 — 没装 playwright → 不挡,前端退文字封面
            return {"generated": [], "skipped": skipped, "errors": [("_import", str(e))], "available": False}

        generated: list[str] = []
        errors: list[tuple[str, str]] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(device_scale_factor=1)
                for m, mode, payload in todo:
                    ver = _ver(m)
                    out = cover_path(store_root, m.id, ver)
                    vp = TEXT_VIEWPORT if mode == "doc" else WEB_VIEWPORT
                    try:
                        page.set_viewport_size(vp)
                        if mode == "url":
                            try:
                                page.goto(payload, wait_until="networkidle", timeout=8000)
                            except Exception:  # noqa: BLE001 — 导航失败(路由不在/超时)退 inline
                                inline = getattr(m, "inline_content", None)
                                if inline and "<" in inline:
                                    page.set_content(inline, wait_until="load", timeout=5000)
                                else:
                                    raise
                        else:  # html(网页内联) / doc(文本渲染)
                            page.set_content(payload, wait_until="load", timeout=6000)
                        page.wait_for_timeout(350)
                        page.screenshot(path=str(out), clip={"x": 0, "y": 0, **vp})
                        generated.append(m.id)
                    except Exception as e:  # noqa: BLE001
                        errors.append((m.id, f"{type(e).__name__}: {e}"))
            finally:
                browser.close()
    return {"generated": generated, "skipped": skipped, "errors": errors, "available": True}
