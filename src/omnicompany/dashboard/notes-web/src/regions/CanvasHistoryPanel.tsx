import { useEffect, useMemo, useState } from "react";
import { History, Loader2, Search, Star, X } from "lucide-react";
import {
  formatCanvasUpdatedAt,
  type CanvasHistoryInvoke,
  type CanvasHistoryItem,
  type CanvasHistoryResult,
} from "./canvasHistory";

export function CanvasHistoryPanel({
  currentId,
  sessionId,
  invokeCommand,
  onOpen,
  onClose,
}: {
  currentId: string;
  sessionId: string;
  invokeCommand: CanvasHistoryInvoke;
  onOpen: (item: CanvasHistoryItem) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [items, setItems] = useState<CanvasHistoryItem[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 220);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    void invokeCommand<CanvasHistoryResult>("notes_canvas_history", {
      query: debounced,
      limit: 80,
      session_id: sessionId,
    })
      .then((result) => {
        if (!live) return;
        setItems(Array.isArray(result?.items) ? result.items : []);
        setLoading(false);
      })
      .catch((reason) => {
        if (!live) return;
        setError(String(reason));
        setLoading(false);
      });
    return () => { live = false; };
  }, [debounced, invokeCommand, sessionId]);

  const toggleFavorite = (item: CanvasHistoryItem) => {
    void invokeCommand<{ canvas_id: string; favorite: boolean }>("notes_canvas_favorite", {
      canvas_id: item.document_id,
    }).then((result) => {
      setItems((current) => {
        const next = current.map((row) => row.document_id === item.document_id
          ? { ...row, favorite: Boolean(result.favorite) }
          : row);
        const favorites = next.filter((row) => row.favorite);
        const rest = next.filter((row) => !row.favorite);
        return [...favorites, ...rest];
      });
    }).catch((reason) => setError(String(reason)));
  };

  const favorites = useMemo(() => items.filter((item) => item.favorite), [items]);
  const recent = useMemo(() => items.filter((item) => !item.favorite), [items]);

  return (
    <div className="material-dialog-backdrop" role="presentation" onPointerDown={onClose}>
      <section
        className="material-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="历史画布"
        data-testid="canvas-history-panel"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <header>
          <strong>历史画布</strong>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={17} /></button>
        </header>
        <p className="material-dialog-hint">只列出有内容的画布。默认显示会话最后标题，可搜索会话或画布内容。点开后成为这个会话窗口的当前画布。</p>
        <form className="material-search-form" onSubmit={(event) => event.preventDefault()}>
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索会话标题、会话内容或画布内容"
            aria-label="搜索历史画布"
          />
          <button type="button" disabled={loading}>{loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}搜索</button>
        </form>
        {error && <div className="material-dialog-error">{error}</div>}
        <div className="material-picker-list canvas-history-list">
          {loading && items.length === 0 && (
            <div className="material-preview-message"><Loader2 className="spin" size={16} />读取历史画布…</div>
          )}
          {!loading && items.length === 0 && (
            <div className="material-preview-message">没有找到有内容的画布</div>
          )}
          {favorites.length > 0 && <div className="canvas-history-label">收藏</div>}
          {favorites.map((item) => (
            <HistoryRow
              key={item.document_id}
              item={item}
              currentId={currentId}
              onOpen={onOpen}
              onFavorite={toggleFavorite}
            />
          ))}
          {recent.length > 0 && <div className="canvas-history-label">最近</div>}
          {recent.map((item) => (
            <HistoryRow
              key={item.document_id}
              item={item}
              currentId={currentId}
              onOpen={onOpen}
              onFavorite={toggleFavorite}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function HistoryRow({
  item,
  currentId,
  onOpen,
  onFavorite,
}: {
  item: CanvasHistoryItem;
  currentId: string;
  onOpen: (item: CanvasHistoryItem) => void;
  onFavorite: (item: CanvasHistoryItem) => void;
}) {
  const current = item.current || item.document_id === currentId;
  return (
    <div className={`canvas-history-row${current ? " current" : ""}`}>
      <button type="button" className="canvas-history-open" onClick={() => onOpen(item)}>
        <History size={16} />
        <span>
          <strong>{item.title}</strong>
          <small>
            {formatCanvasUpdatedAt(item.updated_at)}
            {item.card_count ? ` · ${item.card_count} 张卡` : ""}
            {item.snippet ? ` · ${item.snippet}` : ""}
          </small>
        </span>
      </button>
      <button
        type="button"
        className={`canvas-history-star${item.favorite ? " on" : ""}`}
        title={item.favorite ? "取消收藏" : "加入收藏"}
        aria-label={item.favorite ? "取消收藏" : "加入收藏"}
        onClick={() => onFavorite(item)}
      >
        <Star size={15} fill={item.favorite ? "currentColor" : "none"} />
      </button>
    </div>
  );
}
