// MaterialDocView.tsx — 材料画布 → 可编辑块文档(2026-08-14 UI 返工: 对标collab platform文档/Jupyter 块编辑)。
// 投影: 组嵌套深度→标题层级; 组内 order 升序→continues 拓扑→y/x 兜底; 根级散卡=引言区(最前)。
// 编辑: 文字块点击即编辑, 失焦/Ctrl+Enter 提交(notes_text_put + 标题 mutate), Enter=提交并下方
// 新建块, Esc 取消, 空块 Backspace 删除; 块间「+」插入; 删除/全屏 per 块。全部走 mutate/putText 透传。
import { useEffect, useMemo, useRef, useState } from "react";
import type { CardSubmission, CanvasMutationPatch } from "./materialCanvasModel";

export interface DocCard {
  id: string;
  kind: string;
  title: string;
  parentId?: string;
  order?: number;
  submission?: CardSubmission;
  adapter?: string;
  width?: number;
  height?: number;
  source?: {
    kind: string;
    path?: string;
    token?: string;
    editableId?: string;
    tiers?: { minZoom: number; token: string }[];
  };
  x: number;
  y: number;
  /** 原始卡记录(供 mutate upsert 无损回填) */
  record: Record<string, unknown>;
}

export interface DocRelation {
  id: string;
  source: string;
  target: string;
  label?: string;
  kind?: string;
}

interface TextMaterial {
  path: string;
  open_token: string;
  mime?: string;
}

interface DocViewProps {
  cards: DocCard[];
  relations: DocRelation[];
  fetchText: (editableId: string) => Promise<string>;
  putText: (editableId: string, content: string) => Promise<TextMaterial>;
  mutate: (patch: CanvasMutationPatch) => Promise<unknown>;
  imageUrl: (token: string) => string;
  /** 大纲开关提到顶栏行(2026-08-14 返工): DocView 挂载时把 toggle 注册给宿主 */
  registerOutlineToggle?: (fn: (() => void) | null) => void;
}

/** 组内排序(2026-08-14 修): order 升序 / continues 拓扑 / yx 兜底三键统一的比较器——
 *  双侧都有 order 比 order; 只有一侧有 order 时跟另一侧比 y/x(不再把带 order 的整块提到最前);
 *  都没有则 continues 拓扑层再 y/x。插入新块时 order 与 y 同时夹在邻居之间, 三键自洽。 */
function orderChildren(children: DocCard[], relations: DocRelation[]): DocCard[] {
  const ids = new Set(children.map((c) => c.id));
  const continues = relations.filter((r) => r.kind === "continues" && ids.has(r.source) && ids.has(r.target));
  const layer = new Map<string, number>();
  const preds = new Map<string, number>();
  const succs = new Map<string, string[]>();
  for (const c of children) { layer.set(c.id, 0); preds.set(c.id, 0); succs.set(c.id, []); }
  for (const r of continues) {
    preds.set(r.target, (preds.get(r.target) ?? 0) + 1);
    succs.get(r.source)!.push(r.target);
  }
  const queue = children.filter((c) => (preds.get(c.id) ?? 0) === 0).map((c) => c.id);
  const done = new Set<string>();
  while (queue.length) {
    const id = queue.shift()!;
    if (done.has(id)) continue;
    done.add(id);
    for (const next of succs.get(id) ?? []) {
      layer.set(next, Math.max(layer.get(next) ?? 0, (layer.get(id) ?? 0) + 1));
      preds.set(next, (preds.get(next) ?? 1) - 1);
      if ((preds.get(next) ?? 0) <= 0) queue.push(next);
    }
  }
  const byCoord = (a: DocCard, b: DocCard) => a.y - b.y || a.x - b.x;
  return [...children].sort((a, b) => {
    if (a.order !== undefined && b.order !== undefined) return a.order - b.order || byCoord(a, b);
    if (a.order !== undefined || b.order !== undefined) return byCoord(a, b); // 单侧有 order: 比坐标
    return (layer.get(a.id) ?? 0) - (layer.get(b.id) ?? 0) || byCoord(a, b);
  });
}

function submissionBadge(sub?: CardSubmission): string {
  if (!sub) return "";
  return sub.state === "unreviewed" ? "待看" : sub.state === "seen" ? "已看" : sub.state === "fully-passed" ? "✓ 通过" : "✗ 未过";
}

/** 拍平成线性的文档流: 引言区(根级散卡) → 各组(标题 + 组内块, 递归嵌套) */
type FlatItem =
  | { type: "heading"; card: DocCard; depth: number }
  | { type: "block"; card: DocCard };

function flatten(cards: DocCard[], relations: DocRelation[]): FlatItem[] {
  const byParent = new Map<string | undefined, DocCard[]>();
  for (const card of cards) {
    if (!byParent.has(card.parentId)) byParent.set(card.parentId, []);
    byParent.get(card.parentId)!.push(card);
  }
  const out: FlatItem[] = [];
  const walk = (parentId: string | undefined, depth: number) => {
    for (const card of orderChildren(byParent.get(parentId) ?? [], relations)) {
      if (card.kind === "group") {
        out.push({ type: "heading", card, depth });
        walk(card.id, depth + 1);
      } else {
        out.push({ type: "block", card });
      }
    }
  };
  walk(undefined, 0);
  return out;
}

export function MaterialDocView({ cards, relations, fetchText, putText, mutate, imageUrl, registerOutlineToggle }: DocViewProps) {
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [fullscreenId, setFullscreenId] = useState<string | null>(null);
  // 大纲默认显隐按容器宽度分档(≥1100 开 / <900 关 / 中间档保持用户选择); 手动选择记 localStorage, 跨档重置
  const [outlineOpen, setOutlineOpen] = useState(true);
  const outlineChoiceRef = useRef<string | null>(null);
  const outlineBandRef = useRef("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    try { outlineChoiceRef.current = window.localStorage.getItem("omni.docview.outline"); } catch { /* privacy */ }
    const root = rootRef.current;
    if (!root) return;
    const applyBand = (width: number) => {
      const band = width >= 1100 ? "wide" : width < 900 ? "narrow" : "mid";
      if (band === outlineBandRef.current) return;
      outlineBandRef.current = band;
      if (band === "wide") setOutlineOpen(true);
      else if (band === "narrow") setOutlineOpen(false);
      else if (outlineChoiceRef.current !== null) setOutlineOpen(outlineChoiceRef.current === "open");
    };
    applyBand(root.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) applyBand(entry.contentRect.width);
    });
    observer.observe(root);
    return () => observer.disconnect();
  }, []);
  const toggleOutline = () => {
    setOutlineOpen((current) => {
      const next = !current;
      outlineChoiceRef.current = next ? "open" : "closed";
      try { window.localStorage.setItem("omni.docview.outline", next ? "open" : "closed"); } catch { /* privacy */ }
      return next;
    });
  };
  // 大纲开关提到顶栏行: 挂载时把 toggle 注册给宿主, 卸载注销
  useEffect(() => {
    registerOutlineToggle?.(toggleOutline);
    return () => registerOutlineToggle?.(null);
  });

  // 批量预取全部文字/组卡正文(消 N+1)
  useEffect(() => {
    const ids = cards
      .filter((c) => (c.kind === "text" || c.kind === "group") && c.source?.kind === "file" && c.source.editableId)
      .map((c) => c.source!.editableId!);
    let live = true;
    void Promise.all(ids.map(async (id) => {
      try { return [id, await fetchText(id)] as const; } catch { return [id, "（正文读取失败）"] as const; }
    })).then((entries) => { if (live) setTexts(Object.fromEntries(entries)); });
    return () => { live = false; };
  }, [cards, fetchText]);

  const items = useMemo(() => flatten(cards, relations), [cards, relations]);
  const byId = useMemo(() => new Map(cards.map((c) => [c.id, c])), [cards]);
  const bodyOf = (card: DocCard) => (card.source?.editableId ? texts[card.source.editableId] : undefined);

  // 同父兄弟链(插入定位用): 与 flatten 同源的组内顺序
  const siblingsOf = (parentId: string | undefined) =>
    orderChildren(cards.filter((c) => c.parentId === parentId), relations);

  const commitEdit = async (card: DocCard, text: string, opts?: { insertAfter?: boolean }) => {
    const editableId = card.source?.editableId ?? card.id;
    const previous = bodyOf(card);
    setEditingId(null);
    if (previous !== undefined && text !== previous) {
      await putText(editableId, text);
      setTexts((current) => ({ ...current, [editableId]: text }));
      const firstLine = (text.trim().split("\n").find((line) => line.trim()) ?? "").slice(0, 60);
      if (firstLine && firstLine !== card.title) {
        await mutate({ upsert_cards: [{ ...card.record, title: firstLine } as never] });
      }
    }
    if (opts?.insertAfter) await insertAfterBlock(card, true);
  };

  const insertAfterBlock = async (card: DocCard, startEditing = false) => {
    const id = `doc-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    const material = await putText(id, "");
    const siblings = siblingsOf(card.parentId);
    const index = siblings.findIndex((s) => s.id === card.id);
    const next = siblings[index + 1];
    // order 与坐标都夹到邻居中间(排序三键自洽)。
    // 同行邻居(Δy≈0): y 保持不变、x 取横向中点——否则 y+40 会把新块挤到整行之后(2026-08-14 实测 bug)。
    const curOrder = card.order ?? index * 10;
    const nextOrder = next ? (next.order ?? (index + 1) * 10) : undefined;
    const order = nextOrder !== undefined ? (curOrder + nextOrder) / 2 : curOrder + 10;
    const sameRow = next ? Math.abs(next.y - card.y) < 10 : false;
    const x = sameRow && next ? card.x + (next.x - card.x) / 2 : card.x;
    const y = sameRow ? card.y : (next ? card.y + Math.max(40, (next.y - card.y) / 2) : card.y + (card.height ?? 220) + 40);
    const newCard = {
      id,
      kind: "text",
      title: "新块",
      adapter: "text",
      source: { kind: "file", path: material.path, token: material.open_token, mime: material.mime, editableId: id },
      x,
      y,
      width: card.width ?? 440,
      height: 220,
      parentId: card.parentId,
      order,
    };
    // 补 continues 边(前块→新块), 让拓扑序与 order/y 一致
    const relation = {
      id: `rel-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      source: card.id,
      target: id,
      kind: "continues",
    };
    await mutate({ upsert_cards: [newCard as never], upsert_relations: [relation as never] });
    if (startEditing) {
      setTexts((current) => ({ ...current, [id]: "" }));
      setDraft("");
      setEditingId(id);
    }
  };

  const deleteBlock = async (card: DocCard) => {
    if (editingId === card.id) setEditingId(null);
    await mutate({ remove_card_ids: [card.id] }); // 服务端原子连带删关联边
  };

  // Esc 关全屏
  useEffect(() => {
    if (!fullscreenId) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setFullscreenId(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreenId]);

  const scrollToBlock = (id: string) => {
    document.getElementById(`docblock-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightId(id);
    window.setTimeout(() => setHighlightId((current) => current === id ? null : current), 1500);
  };

  const renderBlock = (card: DocCard) => {
    const badge = submissionBadge(card.submission);
    const isEditing = editingId === card.id;
    const body = bodyOf(card);
    const highlighted = highlightId === card.id;
    if (card.kind === "text") {
      // 首句去重(2026-08-14): 标题=正文首行(或首行以标题为前缀)时, 非编辑态不再重复渲染标题
      const firstLine = (body ?? "").trim().split("\n").find((line) => line.trim())?.trim() ?? "";
      const titleTrim = card.title.trim();
      const showTitle = Boolean(titleTrim) && !(firstLine && (firstLine === titleTrim || firstLine.startsWith(titleTrim)));
      return (
        <div
          key={card.id}
          id={`docblock-${card.id}`}
          className={`doc-block doc-text-block${isEditing ? " editing" : ""}${highlighted ? " flash" : ""}`}
          onClick={() => {
            if (isEditing) return;
            setDraft(body ?? "");
            setEditingId(card.id);
          }}
        >
          <div className="doc-block-tools" onClick={(event) => event.stopPropagation()}>
            <button type="button" title="全屏" onClick={() => setFullscreenId(card.id)}>⤢</button>
            <button type="button" title="在下方插入块" onClick={() => void insertAfterBlock(card, true)}>＋</button>
            <button type="button" title="删除块" onClick={() => void deleteBlock(card)}>✕</button>
          </div>
          {(showTitle || isEditing) && (
            <div className="doc-card-title">
              {card.title}{badge && <span className={`submission-badge s-${card.submission!.state}`}>{badge}</span>}
            </div>
          )}
          {isEditing ? (
            <textarea
              ref={editorRef}
              className="doc-editor"
              autoFocus
              value={draft}
              placeholder="写点什么… Enter=提交并新建下一块, Shift+Enter=换行, Esc=取消"
              onChange={(event) => setDraft(event.currentTarget.value)}
              onBlur={() => void commitEdit(card, draft)}
              onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === "Escape") setEditingId(null);
                else if (event.key === "Enter" && event.ctrlKey) {
                  event.preventDefault();
                  void commitEdit(card, draft);
                } else if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void commitEdit(card, draft, { insertAfter: true });
                } else if (event.key === "Backspace" && draft === "") {
                  event.preventDefault();
                  void deleteBlock(card);
                }
              }}
            />
          ) : body === undefined ? (
            <p className="doc-loading">读取正文…</p>
          ) : (
            body.split(/\n{2,}/).filter(Boolean).map((para, i) => <p key={i}>{para}</p>)
          )}
        </div>
      );
    }
    const token = card.source?.kind === "file"
      ? (card.source.tiers?.[0]?.token ?? card.source.token ?? null)
      : null;
    return (
      <div
        key={card.id}
        id={`docblock-${card.id}`}
        className={`doc-block doc-figure-block${highlighted ? " flash" : ""}`}
      >
        <div className="doc-block-tools">
          <button type="button" title="全屏" onClick={() => setFullscreenId(card.id)}>⤢</button>
          <button type="button" title="在下方插入文字块" onClick={() => void insertAfterBlock(card, true)}>＋</button>
          <button type="button" title="删除块" onClick={() => void deleteBlock(card)}>✕</button>
        </div>
        {token
          ? <img src={imageUrl(token)} alt={card.title} loading="lazy" />
          : <div className="doc-loading">（非图片材料）</div>}
        <div className="doc-figure-caption">
          {card.title}{badge && <span className={`submission-badge s-${card.submission!.state}`}>{badge}</span>}
        </div>
      </div>
    );
  };

  // 大纲: 标题(组) + 顶层块
  const outlineItems = items.filter((item) => item.type === "heading" || item.card.parentId === undefined);
  const fullscreenCard = fullscreenId ? byId.get(fullscreenId) : undefined;

  return (
    <div className="doc-view" ref={rootRef}>
      <div className="doc-toolbar">
        <small>{cards.length} 卡 · {relations.length} 边</small>
      </div>
      <div className="doc-layout">
        {outlineOpen && (
          <nav className="doc-outline" aria-label="文档大纲">
            {outlineItems.map((item) => (
              <button
                key={item.card.id}
                type="button"
                className={`doc-outline-item${item.type === "heading" ? " heading" : ""}`}
                style={{ paddingLeft: item.type === "heading" ? 10 + item.depth * 14 : 24 }}
                onClick={() => scrollToBlock(item.card.id)}
              >
                {item.card.title}
              </button>
            ))}
            {!outlineItems.length && <div className="doc-loading">（空文档）</div>}
          </nav>
        )}
        <div className="doc-flow">
          {items.map((item) => item.type === "heading" ? (
            <div key={item.card.id} id={`docblock-${item.card.id}`} className={`doc-heading-wrap${highlightId === item.card.id ? " flash" : ""}`}>
              {item.depth === 0 && <h1>{item.card.title}</h1>}
              {item.depth === 1 && <h2>{item.card.title}</h2>}
              {item.depth === 2 && <h3>{item.card.title}</h3>}
              {item.depth >= 3 && <h4>{item.card.title}</h4>}
            </div>
          ) : renderBlock(item.card))}
          {!items.length && <div className="doc-loading">画布还没有内容可投影。</div>}
        </div>
      </div>

      {fullscreenCard && (
        <div className="doc-fullscreen" role="dialog" aria-modal="true" onClick={() => setFullscreenId(null)}>
          <div className="doc-fullscreen-body" onClick={(event) => event.stopPropagation()}>
            <header>
              <strong>{fullscreenCard.title}</strong>
              <button type="button" onClick={() => setFullscreenId(null)}>关闭（Esc）</button>
            </header>
            {fullscreenCard.kind === "text" ? (
              <textarea
                className="doc-editor fullscreen"
                autoFocus
                value={editingId === fullscreenCard.id ? draft : (bodyOf(fullscreenCard) ?? "")}
                onFocus={() => {
                  if (editingId !== fullscreenCard.id) {
                    setDraft(bodyOf(fullscreenCard) ?? "");
                    setEditingId(fullscreenCard.id);
                  }
                }}
                onChange={(event) => setDraft(event.currentTarget.value)}
                onKeyDown={(event) => {
                  event.stopPropagation();
                  if (event.key === "Escape") setFullscreenId(null);
                  if (event.key === "Enter" && event.ctrlKey) {
                    event.preventDefault();
                    void commitEdit(fullscreenCard, draft);
                  }
                }}
                onBlur={() => { if (editingId === fullscreenCard.id) void commitEdit(fullscreenCard, draft); }}
              />
            ) : (
              (() => {
                const token = fullscreenCard.source?.kind === "file"
                  ? (fullscreenCard.source.tiers?.[fullscreenCard.source.tiers.length - 1]?.token ?? fullscreenCard.source.token)
                  : null;
                return token
                  ? <img src={imageUrl(token)} alt={fullscreenCard.title} />
                  : <div className="doc-loading">（非图片材料）</div>;
              })()
            )}
          </div>
        </div>
      )}
    </div>
  );
}
