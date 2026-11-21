# [OMNI] origin=kimi-code ts=2026-08-12 type=infra
# [OMNI] material_id="material:controlplane.overlay_snapshot.py"
"""controlplane/overlay_snapshot.py — 把活 web 页烘焙成单文件静态 HTML(材料卡冻结的服务端捕获)。

契约(对齐 OMNI-GAME-STUDIO 计划的单一权威口径):
  - 产物是纯衍生物: 源是活卡 URL, 重冻即覆盖重建, 永不反向成为权威;
  - 烘焙 = 剥 script/表单/交互元素, 同源 CSS 与图片内联, 内嵌 iframe/音视频降级静态占位;
  - 超阈值资源(单个 2MB / 合计 20MB)不再内联, 保留原引用——快照仍能开, 只是那部分要联网;
  - 捕获失败抛 ValueError, 调用方不落半成品文件。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_MAX_INLINE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_INLINE_BYTES = 20 * 1024 * 1024
_NAV_TIMEOUT_MS = 20_000
_SETTLE_MS = 1_500
_CREDENTIALS_FILE = Path.home() / "codeweb" / "omni-access-credentials.txt"
_WALL_MARKERS = ("受保护面", "需要登录")


def _gateway_credentials() -> dict | None:
    """读 Caddy 保护面的 Basic 凭据(网关 401 质询时由 Chromium 自动应答)。"""
    try:
        text = _CREDENTIALS_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    creds: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("Username:"):
            creds["username"] = line.split(":", 1)[1].strip()
        elif line.startswith("Password:"):
            creds["password"] = line.split(":", 1)[1].strip()
    return creds if creds.get("username") and creds.get("password") else None

# 页内烘焙: 返回 {"html": ..., "stats": {...}}。在页面上下文跑, 失败抛给驱动层。
_BAKE_JS = r"""
async ({ maxInline, maxTotal }) => {
  const stats = { cssInlined: 0, cssSkipped: 0, imgInlined: 0, imgSkipped: 0, placeholders: 0, bytes: 0 };
  // 1) 剥交互与脚本
  document.querySelectorAll("script,form,noscript").forEach((el) => el.remove());
  document.querySelectorAll("input,button,select,textarea").forEach((el) => el.remove());
  // 2) 同源 stylesheet 内联
  for (const link of [...document.querySelectorAll('link[rel~="stylesheet"]')]) {
    const style = document.createElement("style");
    try {
      const resp = await fetch(link.href);
      if (!resp.ok) throw new Error(String(resp.status));
      const text = await resp.text();
      style.textContent = text;
      stats.cssInlined += 1;
      stats.bytes += text.length;
    } catch {
      style.textContent = `/* 冻结快照: 样式表拉取失败 ${link.href} */`;
      stats.cssSkipped += 1;
    }
    link.replaceWith(style);
  }
  // 3) 图片内联为 data URI(超阈值/跨域失败则保留原 src)
  for (const img of [...document.querySelectorAll("img")]) {
    const src = img.currentSrc || img.src || "";
    if (!src || src.startsWith("data:")) continue;
    try {
      const resp = await fetch(src);
      if (!resp.ok) throw new Error(String(resp.status));
      const blob = await resp.blob();
      if (blob.size > maxInline || stats.bytes + blob.size > maxTotal) {
        stats.imgSkipped += 1;
        continue;
      }
      const dataUri = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("FileReader failed"));
        reader.readAsDataURL(blob);
      });
      img.src = dataUri;
      stats.imgInlined += 1;
      stats.bytes += blob.size;
    } catch {
      stats.imgSkipped += 1;
    }
  }
  // 4) 内嵌 iframe/音视频降级为静态占位条(快照不得暗依赖后端或网络服务)
  const note = (kind, ref) => {
    const div = document.createElement("div");
    div.style.cssText = "padding:18px;border:1px dashed #888;color:#888;font:13px sans-serif;"
      + "display:flex;align-items:center;justify-content:center;min-height:60px;box-sizing:border-box;";
    div.textContent = `冻结快照：内嵌${kind}未烘焙 (${ref || "无源"})`;
    stats.placeholders += 1;
    return div;
  };
  document.querySelectorAll("iframe").forEach((el) => el.replaceWith(note("页面", el.src)));
  document.querySelectorAll("video").forEach((el) => el.replaceWith(note("视频", el.currentSrc || el.src)));
  document.querySelectorAll("audio").forEach((el) => el.replaceWith(note("音频", el.currentSrc || el.src)));
  // 5) 锚点 target 一律新窗, 避免在 sandbox 里导航自身
  document.querySelectorAll("a[href]").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  return { html: "<!doctype html>\n" + document.documentElement.outerHTML, stats };
}
"""


def capture_page_to_single_html(url: str, out_path: Path) -> dict:
    """无头 Chromium 加载 url 并烘焙成单文件静态 HTML, 原子写到 out_path。返回烘焙统计。

    自动带 Caddy 保护面 Basic 凭据(~/codeweb/omni-access-credentials.txt);
    捕获到登录墙/凭据缺失时抛 ValueError, 不把登录页烘成快照。
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("只烘焙 http(s) 页面")
    from playwright.sync_api import sync_playwright  # 重依赖, 用到才 import

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context_kwargs: dict = {"viewport": {"width": 1600, "height": 1000}}
            credentials = _gateway_credentials()
            if credentials:
                context_kwargs["http_credentials"] = credentials
            page = browser.new_context(**context_kwargs).new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            except Exception as error:
                raise ValueError(f"页面加载失败: {error}") from None
            page.wait_for_timeout(_SETTLE_MS)
            try:
                baked = page.evaluate(
                    _BAKE_JS,
                    {"maxInline": _MAX_INLINE_BYTES, "maxTotal": _MAX_TOTAL_INLINE_BYTES},
                )
            except Exception as error:
                raise ValueError(f"页内烘焙失败: {error}") from None
        finally:
            browser.close()
    html = baked.get("html")
    if not isinstance(html, str) or len(html) < 32:
        raise ValueError("烘焙产物为空或异常")
    if "<script" in html.lower():
        raise ValueError("烘焙产物仍含 script, 拒绝落盘")
    if any(marker in html for marker in _WALL_MARKERS):
        raise ValueError("捕获到的是登录墙(受保护面), 不是真内容——检查网关凭据后再冻")
    encoded = html.encode("utf-8")
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_bytes(encoded)
    os.replace(tmp, out_path)  # 原子, 读者不会看到半截
    stats = baked.get("stats") if isinstance(baked.get("stats"), dict) else {}
    return {"bytes": len(encoded), **stats}


def bake_stats_line(stats: dict) -> str:
    """一行人读摘要, 给 CLI/日志用。"""
    keep = ("bytes", "cssInlined", "cssSkipped", "imgInlined", "imgSkipped", "placeholders")
    return json.dumps({k: stats.get(k, 0) for k in keep}, ensure_ascii=False)
