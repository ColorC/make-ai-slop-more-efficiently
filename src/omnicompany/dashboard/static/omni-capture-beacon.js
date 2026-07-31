/* 统一捕获 · omni 表面信标
 * 任何 omni 网页(驾驶舱 / vilo demo / narrative_studio …)include 本脚本即成为"可被 poof 截图认出"的表面。
 * 做的事: 周期性把"我是谁(url/title/dpr) + 当前可见的 [data-omni-uri] 实体的视口矩形(CSS px)"
 * 上报给 dashboard 的 /api/boss-sight/captures/surface。poof 截图时用屏幕矩形跟这些做几何相交。
 * 见 plan docs/plans/dashboard/[2026-06-27]UNIVERSAL-CAPTURE。
 *
 * 用法: <script src="/omni-capture-beacon.js" defer></script>(同源)
 *   非同源(vilo demo 等)指向 dashboard: window.OMNI_CAPTURE_ENDPOINT='http://127.0.0.1:8210' 再 include。
 */
(function () {
  "use strict";
  if (window.__omniCaptureBeacon) return;
  window.__omniCaptureBeacon = true;

  // 入站链自检: 本页若嵌在 omnichat webview 外壳里, 加载即向宿主报一声,
  // 供扩展日志确认"页面→外壳→扩展"通道活着(排查"在 VSCode 打开点了没反应")。
  try {
    var __st = { __omnichat: true, type: "page-selftest", href: String(location.href).slice(0, 100) };
    if (window.parent && window.parent !== window) window.parent.postMessage(__st, "*");
    if (window.top && window.top !== window.parent) window.top.postMessage(__st, "*");
  } catch (e) {}

  var DASH = window.OMNI_CAPTURE_ENDPOINT ||
    (location.port === "5173" ? "http://127.0.0.1:8210" : "");

  var sid = null;
  try { sid = sessionStorage.getItem("omni_surface_id"); } catch (e) {}
  if (!sid) {
    sid = "s_" + Math.random().toString(36).slice(2) + "_" + Date.now().toString(36);
    try { sessionStorage.setItem("omni_surface_id", sid); } catch (e) {}
  }

  function collect() {
    var els = document.querySelectorAll("[data-omni-uri]");
    var out = [];
    var vw = window.innerWidth, vh = window.innerHeight;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var uri = el.getAttribute("data-omni-uri");
      if (!uri) continue;
      var r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      // 跳过完全在视口外的(滚走的卡片不上报, 减小载荷 + 避免误命中)
      if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
      out.push({
        omni_uri: uri,
        kind: el.getAttribute("data-omni-kind") || "",
        title: el.getAttribute("data-omni-title") || (el.textContent || "").trim().slice(0, 80),
        x: r.left, y: r.top, w: r.width, h: r.height
      });
    }
    return out;
  }

  // ── 通用 DOM 元素解析: 报"光标下元素", 不靠埋点也能认出任意元素 ──────────
  function cssPath(el) {
    var parts = [], cur = el;
    for (var d = 0; cur && cur.nodeType === 1 && d < 5; d++) {
      var seg = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift(seg + "#" + cur.id); break; }
      if (typeof cur.className === "string" && cur.className.trim())
        seg += "." + cur.className.trim().split(/\s+/).slice(0, 2).join(".");
      parts.unshift(seg);
      cur = cur.parentElement;
    }
    return parts.join(" > ");
  }
  function elementInfo(el) {
    var r = el.getBoundingClientRect();
    var sem = el.closest ? el.closest("[data-omni-uri]") : null;
    return {
      tag: el.tagName ? el.tagName.toLowerCase() : "",
      id: el.id || "",
      cls: (typeof el.className === "string" ? el.className : "").slice(0, 200),
      text: (el.innerText || el.textContent || "").trim().slice(0, 240),
      selector: cssPath(el),
      x: r.left, y: r.top, w: r.width, h: r.height,
      omni_uri: sem ? (sem.getAttribute("data-omni-uri") || "") : "",
      omni_kind: sem ? (sem.getAttribute("data-omni-kind") || "") : "",
      omni_title: sem ? (sem.getAttribute("data-omni-title") || "") : ""
    };
  }
  var hoverEl = null;

  // 内容原子采集: 叶子且有文本 / 媒体交互替换元素 / 埋点元素, 视口内、有可见框。给"选区里有哪些元素"做几何切分。
  // 只在结构变化(初始/间隔/滚动/尺寸/DOM 变更)时重算并缓存, 鼠标移动不重算 —— 保证移动轻量。
  var ATOM = /^(img|svg|canvas|video|button|a|input|select|textarea|label|summary)$/;
  function collectContent() {
    var out = [];
    var vw = window.innerWidth, vh = window.innerHeight;
    var all = document.body ? document.body.querySelectorAll("*") : [];
    for (var i = 0; i < all.length && out.length < 500; i++) {
      var el = all[i];
      var tag = el.tagName ? el.tagName.toLowerCase() : "";
      var isAtom = (el.childElementCount === 0 && (el.textContent || "").trim()) ||
                   ATOM.test(tag) || el.hasAttribute("data-omni-uri");
      if (!isAtom) continue;
      var r = el.getBoundingClientRect();
      if (r.width <= 1 || r.height <= 1) continue;
      if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue; // 视口外的略过
      out.push({
        tag: tag,
        text: (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120),
        selector: cssPath(el),
        x: r.left, y: r.top, w: r.width, h: r.height,
        omni_uri: el.getAttribute("data-omni-uri") || ""
      });
    }
    return out;
  }
  var contentEls = [];
  function recompute() { try { contentEls = collectContent(); } catch (e) { contentEls = []; } }

  function report() {
    var body;
    try {
      body = JSON.stringify({
        surface_id: sid,
        url: location.href,
        title: document.title,
        dpr: window.devicePixelRatio || 1,
        viewport: { w: window.innerWidth, h: window.innerHeight },
        entities: collect(),
        content_els: contentEls,
        hover: hoverEl
      });
    } catch (e) { return; }
    try {
      fetch(DASH + "/api/boss-sight/captures/surface", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true
      }).catch(function () {});
    } catch (e) {}
  }

  var t = null;
  function schedule() { // 结构变化(滚动/尺寸/DOM): 重算内容原子 + 上报, debounce 400ms
    if (t) return;
    t = setTimeout(function () { t = null; recompute(); report(); }, 400);
  }
  var th = null;
  function scheduleHover() { // 鼠标移动: 只更新悬停 + 上报, 不重算内容原子(轻量)
    if (th) return;
    th = setTimeout(function () { th = null; report(); }, 200);
  }

  var lastMove = 0;
  function onMove(e) {
    var now = Date.now();
    if (now - lastMove < 140) return; // 节流
    lastMove = now;
    var el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el) return;
    hoverEl = elementInfo(el);
    hoverEl.cx = e.clientX; hoverEl.cy = e.clientY;
    scheduleHover(); // 轻量: 只推悬停, 不重算内容原子
  }

  function start() {
    recompute();
    report();
    setInterval(function () { recompute(); report(); }, 1500);
    window.addEventListener("mousemove", onMove, true);
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    try {
      new MutationObserver(schedule).observe(document.body, {
        subtree: true, childList: true,
        attributes: true, attributeFilter: ["data-omni-uri"]
      });
    } catch (e) {}
  }

  if (document.body) start();
  else window.addEventListener("DOMContentLoaded", start);
})();
