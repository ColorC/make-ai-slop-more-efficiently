// CM6 双模 markdown 编辑器：
//   实时模式（默认，类 Obsidian live-preview）——光标所在构造显源码，其余隐藏标记、按渲染态着装；
//   源码模式——纯 CM6 markdown。
// mountWikiEditor(rootEl, { content, onSave, onCancel }) → { getContent, destroy }
import { EditorState, Compartment } from "@codemirror/state";
import { EditorView, keymap, Decoration, ViewPlugin, WidgetType, lineNumbers } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { syntaxTree, syntaxHighlighting, HighlightStyle } from "@codemirror/language";
import { tags } from "@lezer/highlight";

class TextWidget extends WidgetType {
  constructor(text, cls) {
    super();
    this.text = text;
    this.cls = cls;
  }
  eq(other) {
    return other.text === this.text && other.cls === this.cls;
  }
  toDOM() {
    const span = document.createElement("span");
    span.className = this.cls;
    span.textContent = this.text;
    return span;
  }
  ignoreEvent() {
    return false;
  }
}

const WIKILINK_RE = /\[\[([^\[\]|#]+)(#[^\[\]|]*)?(?:\|([^\[\]]+))?\]\]/g;
const HIGHLIGHT_RE = /==([^=\n]+)==/g;

// 光标（主选区）与 [from,to] 是否相交 —— 相交则露出源码
function touches(state, from, to) {
  const sel = state.selection.main;
  return sel.from <= to && sel.to >= from;
}

function buildLivePreviewDecorations(view) {
  const ranges = [];
  const { state } = view;
  for (const { from, to } of view.visibleRanges) {
    // —— 语法树驱动：标题 / 强调 / 行内代码 / 引用行 ——
    syntaxTree(state).iterate({
      from,
      to,
      enter: (node) => {
        const name = node.name;
        if (/^ATXHeading[1-6]$/.test(name)) {
          const level = Number(name.slice("ATXHeading".length));
          const line = state.doc.lineAt(node.from);
          ranges.push({ from: line.from, to: line.from, deco: Decoration.line({ class: `cm-lp-h cm-lp-h${level}` }) });
          if (!touches(state, line.from, line.to)) {
            const mark = node.node.getChild("HeaderMark");
            if (mark) {
              const hideTo = Math.min(mark.to + 1, line.to); // 连同后面的空格
              ranges.push({ from: mark.from, to: hideTo, deco: Decoration.replace({}) });
            }
          }
        } else if (name === "StrongEmphasis" || name === "Emphasis") {
          const cls = name === "StrongEmphasis" ? "cm-lp-strong" : "cm-lp-em";
          ranges.push({ from: node.from, to: node.to, deco: Decoration.mark({ class: cls }) });
          if (!touches(state, node.from, node.to)) {
            for (const mark of node.node.getChildren("EmphasisMark")) {
              ranges.push({ from: mark.from, to: mark.to, deco: Decoration.replace({}) });
            }
          }
        } else if (name === "InlineCode") {
          ranges.push({ from: node.from, to: node.to, deco: Decoration.mark({ class: "cm-lp-code" }) });
          if (!touches(state, node.from, node.to)) {
            for (const mark of node.node.getChildren("CodeMark")) {
              ranges.push({ from: mark.from, to: mark.to, deco: Decoration.replace({}) });
            }
          }
        } else if (name === "Blockquote") {
          const first = state.doc.lineAt(node.from);
          const last = state.doc.lineAt(Math.min(node.to, state.doc.length));
          for (let n = first.number; n <= last.number; n++) {
            const ln = state.doc.line(n);
            ranges.push({ from: ln.from, to: ln.from, deco: Decoration.line({ class: "cm-lp-quote" }) });
          }
        }
      },
    });
    // —— 正则驱动：wikilink / ==高亮==（标准 markdown 树没有的 Obsidian 语法）——
    const text = state.doc.sliceString(from, to);
    for (const m of text.matchAll(WIKILINK_RE)) {
      const start = from + m.index;
      const end = start + m[0].length;
      if (touches(state, start, end)) continue;
      const display = m[3] ?? m[1];
      ranges.push({ from: start, to: end, deco: Decoration.replace({ widget: new TextWidget(display, "cm-lp-wikilink") }) });
    }
    for (const m of text.matchAll(HIGHLIGHT_RE)) {
      const start = from + m.index;
      const end = start + m[0].length;
      if (touches(state, start, end)) continue;
      ranges.push({ from: start, to: start + 2, deco: Decoration.replace({}) });
      ranges.push({ from: start + 2, to: end - 2, deco: Decoration.mark({ class: "cm-lp-mark" }) });
      ranges.push({ from: end - 2, to: end, deco: Decoration.replace({}) });
    }
  }
  // Decoration.set(sorted=true) 处理 line/mark/replace 的 startSide 排序（手写 builder 会因
  // 同位点 line 装饰与 replace 起点冲突而丢弃装饰，曾导致标题行样式失效）。
  try {
    return Decoration.set(ranges.map((r) => r.deco.range(r.from, r.to)), true);
  } catch {
    return Decoration.none; // 极端重叠时放弃本帧装饰，保编辑可用
  }
}

const livePreviewPlugin = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.decorations = buildLivePreviewDecorations(view);
    }
    update(update) {
      if (update.docChanged || update.selectionSet || update.viewportChanged) {
        this.decorations = buildLivePreviewDecorations(update.view);
      }
    }
  },
  { decorations: (v) => v.decorations },
);

const mdHighlight = HighlightStyle.define([
  { tag: tags.heading, color: "#ffd166" },
  { tag: tags.quote, color: "#bdb49e" },
  { tag: tags.monospace, color: "#9ecbff" },
  { tag: tags.link, color: "#9ecbff" },
  { tag: tags.url, color: "#7d96b8" },
  { tag: tags.processingInstruction, color: "#8d8470" },
  { tag: tags.meta, color: "#8d8470" },
]);

export function mountWikiEditor(rootEl, opts = {}) {
  rootEl.classList.add("wiki-editor");
  rootEl.innerHTML = `
    <div class="wiki-editor-toolbar">
      <button type="button" class="we-mode" data-testid="wiki-editor-mode">源码</button>
      <span class="we-hint">Ctrl+S 保存</span>
      <span class="we-spacer"></span>
      <button type="button" class="we-save" data-testid="wiki-editor-save">保存</button>
      <button type="button" class="we-cancel" data-testid="wiki-editor-cancel">取消</button>
    </div>
    <div class="wiki-editor-cm"></div>`;
  const cmHost = rootEl.querySelector(".wiki-editor-cm");
  const modeBtn = rootEl.querySelector(".we-mode");

  const liveMode = new Compartment();
  let isLive = true;

  const save = () => {
    if (opts.onSave) opts.onSave(view.state.doc.toString());
    return true;
  };

  const view = new EditorView({
    parent: cmHost,
    state: EditorState.create({
      doc: opts.content ?? "",
      extensions: [
        history(),
        keymap.of([{ key: "Mod-s", run: save, preventDefault: true }, indentWithTab, ...defaultKeymap, ...historyKeymap]),
        markdown({ base: markdownLanguage }),
        syntaxHighlighting(mdHighlight),
        EditorView.lineWrapping,
        liveMode.of(livePreviewPlugin),
      ],
    }),
  });

  modeBtn.addEventListener("click", () => {
    isLive = !isLive;
    modeBtn.textContent = isLive ? "源码" : "实时";
    view.dispatch({ effects: liveMode.reconfigure(isLive ? livePreviewPlugin : [lineNumbers()]) });
    view.focus();
  });
  rootEl.querySelector(".we-save").addEventListener("click", save);
  rootEl.querySelector(".we-cancel").addEventListener("click", () => opts.onCancel && opts.onCancel());

  view.focus();
  return {
    getContent: () => view.state.doc.toString(),
    destroy: () => {
      view.destroy();
      rootEl.innerHTML = "";
      rootEl.classList.remove("wiki-editor");
    },
  };
}
