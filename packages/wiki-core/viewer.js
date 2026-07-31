// 免框架 wiki viewer：mountWikiViewer(rootEl, opts) → { open(pageOrRef), refresh, destroy }
// 渲染用吸收的 obsidian-md 核；wikilink 按 basename 对索引解析，点击在 viewer 内导航。
// opts.comments 传入 CommentStore（见 comments.js）即启用段落评论：圈选→评论、段落 pin。
// 注意：只引 render 门面（浏览器安全）；包入口 index.js 还 re-export 了 Node-only 的
// index-builder（node:fs / fast-glob），浏览器侧绝不能 import 它。
import { createRenderer, stripFrontmatter } from "./render.js";
import { paragraphHash } from "./comments.js";

function basename(path) {
  return path.replace(/\.md$/, "").split("/").pop();
}

export function mountWikiViewer(rootEl, opts = {}) {
  const apiBase = (opts.apiBase ?? "") + "/api/wiki";
  const onState = opts.onState || (() => {});
  let index = { pages: [] };
  let current = null; // { path, content }
  let destroyed = false;

  rootEl.classList.add("wiki-core");
  rootEl.innerHTML = `
    <header class="wiki-head">
      <button class="wiki-home" type="button" title="索引页">☰</button>
      <strong class="wiki-title"></strong>
      <span class="wiki-path"></span>
      <span class="wiki-head-actions">${opts.editable ? '<button class="wiki-edit" type="button" data-testid="wiki-edit">编辑</button>' : ""}</span>
    </header>
    <article class="wiki-body wiki-prose"></article>
    <div class="wiki-editor-host" style="display:none"></div>`;
  const titleEl = rootEl.querySelector(".wiki-title");
  const pathEl = rootEl.querySelector(".wiki-path");
  const bodyEl = rootEl.querySelector(".wiki-body");
  const editorHost = rootEl.querySelector(".wiki-editor-host");
  let editor = null; // 激活中的编辑器句柄

  const findByName = (name) => {
    const lower = String(name).toLowerCase();
    return (
      index.pages.find((p) => basename(p.path).toLowerCase() === lower) ||
      index.pages.find((p) => p.title.toLowerCase() === lower) ||
      null
    );
  };

  const md = createRenderer({
    anchorPermalink: true, // 标题加锚点 #slug，供 wiki://page#slug 精确跳转
    resolveLink: (name, anchor, alias) => {
      const hit = findByName(name);
      return {
        href: `#wiki:${encodeURIComponent(hit ? hit.path : name)}${anchor ? `#${encodeURIComponent(anchor)}` : ""}`,
        text: alias ?? name,
        broken: !hit,
      };
    },
    resolveAsset: (path) => ({ href: (opts.assetBase ?? "") + "/" + path.replace(/^\//, "") }),
    resolveEmbed: (name) => {
      const hit = findByName(name);
      return { href: `#wiki:${encodeURIComponent(hit ? hit.path : name)}`, targetSlug: name, broken: !hit };
    },
    resolveTag: (tag) => ({ href: `#wikitag:${encodeURIComponent(tag)}` }),
  });

  async function fetchJson(url, init) {
    const resp = await fetch(url, init);
    if (!resp.ok) throw new Error(`${url} → HTTP ${resp.status}`);
    return resp.json();
  }

  async function ensureIndex(force) {
    if (force || index.pages.length === 0) {
      index = await fetchJson(`${apiBase}/index`);
    }
  }

  async function open(pageOrRef) {
    exitEdit(); // 切页时退出编辑态
    await ensureIndex();
    const ref = typeof pageOrRef === "string" ? { page: pageOrRef } : (pageOrRef || {});
    let path = ref.page;
    const anchor = ref.anchor || null; // 标题 slug 或 "h=<段落hash>"
    if (path && !path.endsWith(".md")) {
      const hit = findByName(path);
      path = hit ? hit.path : `${path}.md`;
    }
    if (!path) path = "index.md";
    try {
      current = await fetchJson(`${apiBase}/file?path=${encodeURIComponent(path)}`);
    } catch {
      bodyEl.innerHTML = `<p class="wiki-missing">页面不存在：${escapeHtml(path)}（可在 docs/wiki 下创建）</p>`;
      titleEl.textContent = basename(path);
      pathEl.textContent = path;
      onState({ page: path, missing: true });
      return;
    }
    render();
    scrollToAnchor(anchor);
    onState({ page: current.path, missing: false });
  }

  function scrollToAnchor(anchor) {
    if (!anchor) return;
    let target = null;
    if (anchor.startsWith("h=")) {
      const h = anchor.slice(2);
      target = [...bodyEl.querySelectorAll("[data-wiki-hash]")].find((el) => el.dataset.wikiHash === h);
    } else {
      try { target = bodyEl.querySelector(`#${CSS.escape(anchor)}`); } catch { target = null; }
    }
    if (target) target.scrollIntoView({ block: "center" });
  }

  async function copyText(text, fromEl) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch { /* 忽略 */ }
      ta.remove();
    }
    if (fromEl) {
      const old = fromEl.textContent;
      fromEl.textContent = "已复制";
      fromEl.classList.add("is-copied");
      setTimeout(() => { fromEl.textContent = old; fromEl.classList.remove("is-copied"); }, 900);
    }
  }

  function render() {
    if (!current) return;
    const html = md.render(stripFrontmatter(current.content));
    titleEl.textContent = basename(current.path);
    pathEl.textContent = current.path;
    bodyEl.innerHTML = html;
    bodyEl.scrollTop = 0;
    decorateBlocks();
    void refreshCommentPins();
  }

  // ===== 段落评论（opts.comments 提供 CommentStore 时启用）=====
  const commentStore = opts.comments || null;
  let pageComments = [];
  const BLOCK_SELECTOR = "p, li, h1, h2, h3, h4, blockquote, .callout";

  // 每个块挂段落锚点(FNV-1a)+"复制段落链接"按钮(wiki://page#h=hash)。
  // hash/clean-text 在加按钮前算好存 dataset, 供评论 pin 与 demo→doc 跳转复用。
  function decorateBlocks() {
    if (!current) return;
    for (const el of bodyEl.querySelectorAll(BLOCK_SELECTOR)) {
      if (el.closest(".callout") && !el.classList.contains("callout")) continue;
      const text = el.textContent || "";
      el.dataset.wikiText = text;
      el.dataset.wikiHash = paragraphHash(text);
      el.classList.add("wiki-block");
      const anchor = document.createElement("button");
      anchor.type = "button";
      anchor.className = "wiki-anchor";
      anchor.setAttribute("data-testid", "wiki-anchor");
      anchor.title = "复制段落链接";
      anchor.textContent = "🔗";
      anchor.addEventListener("click", (e) => {
        e.stopPropagation();
        void copyText(`wiki://${current.path}#h=${el.dataset.wikiHash}`, anchor);
      });
      el.appendChild(anchor);
    }
  }

  async function refreshCommentPins() {
    if (!commentStore || !current) return;
    try {
      pageComments = await commentStore.list(current.path);
    } catch {
      pageComments = [];
    }
    const byHash = new Map();
    for (const c of pageComments) {
      const list = byHash.get(c.target.para_hash) || [];
      list.push(c);
      byHash.set(c.target.para_hash, list);
    }
    bodyEl.querySelectorAll(".wiki-pin").forEach((el) => el.remove());
    for (const el of bodyEl.querySelectorAll(BLOCK_SELECTOR)) {
      const list = byHash.get(el.dataset.wikiHash || paragraphHash(el.textContent || ""));
      if (!list) continue;
      // callout 内部段落不重复挂 pin（callout 容器本身已可命中）
      if (el.closest(".callout") && !el.classList.contains("callout")) continue;
      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = "wiki-pin";
      pin.setAttribute("data-testid", "wiki-pin");
      pin.textContent = `💬${list.length}`;
      pin.addEventListener("click", (e) => {
        e.stopPropagation();
        openCommentPanel(el, "");
      });
      el.appendChild(pin);
    }
  }

  function blockOfNode(node) {
    let el = node instanceof Element ? node : node?.parentElement;
    return el ? el.closest(BLOCK_SELECTOR) : null;
  }

  let selBtn = null;
  function hideSelButton() {
    selBtn?.remove();
    selBtn = null;
  }
  function onMouseUp() {
    if (!commentStore || editor) return;
    setTimeout(() => {
      hideSelButton();
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) return;
      const range = sel.getRangeAt(0);
      // 用选区起点找所属段落：整段三连击的选区常溢出到下一个块，
      // commonAncestorContainer 会落在整个 body 上。
      const block = blockOfNode(range.startContainer) || blockOfNode(range.commonAncestorContainer);
      if (!block || !bodyEl.contains(block)) return;
      const text = sel.toString().trim();
      if (!text) return;
      const rect = range.getBoundingClientRect();
      const host = rootEl.getBoundingClientRect();
      selBtn = document.createElement("button");
      selBtn.type = "button";
      selBtn.className = "wiki-sel-comment";
      selBtn.setAttribute("data-testid", "wiki-sel-comment");
      selBtn.textContent = "评论";
      selBtn.style.left = `${Math.max(8, rect.left - host.left)}px`;
      selBtn.style.top = `${rect.bottom - host.top + 6}px`;
      selBtn.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        hideSelButton();
        openCommentPanel(block, text);
      });
      rootEl.appendChild(selBtn);
    }, 0);
  }
  bodyEl.addEventListener("mouseup", onMouseUp);

  let panel = null;
  function closeCommentPanel() {
    panel?.remove();
    panel = null;
  }
  function openCommentPanel(blockEl, selectedText) {
    closeCommentPanel();
    const paraText = blockEl.dataset.wikiText ?? (blockEl.textContent || "").replace(/💬\d+$/, "");
    const hash = paragraphHash(paraText);
    const existing = pageComments.filter((c) => c.target.para_hash === hash);
    panel = document.createElement("div");
    panel.className = "wiki-comment-panel";
    panel.setAttribute("data-testid", "wiki-comment-panel");
    panel.innerHTML = `
      <div class="wcp-head"><strong>段落评论</strong><button type="button" class="wcp-close">✕</button></div>
      <div class="wcp-snippet">${escapeHtml(paraText.slice(0, 80))}${paraText.length > 80 ? "…" : ""}</div>
      ${selectedText ? `<div class="wcp-selected">圈选：${escapeHtml(selectedText.slice(0, 80))}</div>` : ""}
      <div class="wcp-list">${existing.map((c) => `<div class="wcp-item"><span class="wcp-author">${escapeHtml(c.author || "user")}</span>${escapeHtml(c.content)}</div>`).join("") || '<div class="wcp-empty">还没有评论。</div>'}</div>
      <textarea class="wcp-input" data-testid="wiki-comment-input" placeholder="对这一段写点什么…"></textarea>
      <div class="wcp-actions"><button type="button" class="wcp-submit" data-testid="wiki-comment-submit">提交</button></div>`;
    rootEl.appendChild(panel);
    panel.querySelector(".wcp-close").addEventListener("click", closeCommentPanel);
    panel.querySelector(".wcp-submit").addEventListener("click", async () => {
      const input = panel.querySelector(".wcp-input");
      const content = (input.value || "").trim();
      if (!content) return;
      try {
        await commentStore.add({ page: current.path, paraText, selectedText, content });
        closeCommentPanel();
        await refreshCommentPins();
      } catch (error) {
        input.placeholder = `提交失败：${error.message}`;
      }
    });
    panel.querySelector(".wcp-input").focus();
  }

  async function enterEdit() {
    if (!current || editor) return;
    bodyEl.style.display = "none";
    editorHost.style.display = "";
    editorHost.style.flex = "1";
    editorHost.style.minHeight = "0";
    const { mountWikiEditor } = await import("./editor.js"); // 懒加载 CM6
    editor = mountWikiEditor(editorHost, {
      content: current.content,
      onSave: async (content) => {
        const resp = await fetch(`${apiBase}/file?path=${encodeURIComponent(current.path)}`, { method: "PUT", body: content });
        if (!resp.ok) {
          alert(`保存失败: HTTP ${resp.status}`);
          return;
        }
        current = { ...current, content };
        exitEdit();
        render();
      },
      onCancel: exitEdit,
    });
  }

  function exitEdit() {
    if (editor) {
      editor.destroy();
      editor = null;
    }
    editorHost.style.display = "none";
    bodyEl.style.display = "";
  }

  rootEl.querySelector(".wiki-edit")?.addEventListener("click", () => void enterEdit());

  function onClick(event) {
    const a = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!a) return;
    const href = a.getAttribute("href") || "";
    if (href.startsWith("#wiki:")) {
      event.preventDefault();
      const raw = href.slice("#wiki:".length);
      const [p, anchor] = raw.split("#");
      void open({ page: decodeURIComponent(p), anchor: anchor ? decodeURIComponent(anchor) : null });
    } else if (href.startsWith("wiki://")) {
      // 作者手写的跨材料段落锚点(如演示步 links 回跳的目标 / spec 文档互引)
      event.preventDefault();
      const raw = href.slice("wiki://".length);
      const [p, anchor] = raw.split("#");
      void open({ page: decodeURIComponent(p), anchor: anchor ? decodeURIComponent(anchor) : null });
    } else if (href.startsWith("demo://")) {
      // demo://<tourId>#<stepId> → 宿主跳到对应演示步(opts.onDemoLink 接 mountDemoTour 句柄/postMessage)
      event.preventDefault();
      const [tourId, stepId] = href.slice("demo://".length).split("#");
      opts.onDemoLink?.(decodeURIComponent(tourId), stepId ? decodeURIComponent(stepId) : null);
    } else if (href.startsWith("mat://")) {
      // mat://<mat_id>[#stepId] → 宿主打开该审阅材料(opts.onMaterialLink 接 openTab/postMessage)
      event.preventDefault();
      const [matId, frag] = href.slice("mat://".length).split("#");
      opts.onMaterialLink?.(decodeURIComponent(matId), frag ? decodeURIComponent(frag) : null);
    } else if (href.startsWith("#wikitag:")) {
      event.preventDefault(); // 标签页后续阶段
    }
  }
  rootEl.addEventListener("click", onClick);
  rootEl.querySelector(".wiki-home").addEventListener("click", () => void open("index.md"));

  void open(opts.page || "index.md");

  return {
    open: (p) => void open(p),
    refresh: async () => {
      await ensureIndex(true);
      if (current) await open(current.path);
    },
    getCurrent: () => current,
    destroy: () => {
      if (destroyed) return;
      destroyed = true;
      exitEdit();
      closeCommentPanel();
      hideSelButton();
      bodyEl.removeEventListener("mouseup", onMouseUp);
      rootEl.removeEventListener("click", onClick);
      rootEl.innerHTML = "";
      rootEl.classList.remove("wiki-core");
    },
  };
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
