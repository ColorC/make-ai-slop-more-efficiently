// 全局查找替换浮层(Ctrl-H):跨载体文本替换,先预览命中再全部替换。
import React, { useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

export function ReplacePanel({ onClose, onChanged }: { onClose: () => void; onChanged?: () => void }) {
  const s = useStudio();
  const [find, setFind] = useState("");
  const [repl, setRepl] = useState("");
  const [hits, setHits] = useState<{ path: string; before: string }[] | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const preview = async () => {
    if (!find) return;
    setBusy(true);
    try { const r = await api.replace(find, repl, true); setHits(r.hits); setCount(r.count); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    if (!find) return;
    if (!confirm(`把所有「${find}」替换成「${repl}」?(全局,可经修订历史还原)`)) return;
    setBusy(true);
    try {
      const r = await api.replace(find, repl, false);
      setCount(r.count); setHits(r.hits);
      await s.reload(); onChanged?.();
      alert(`已替换 ${r.count} 处`);
    } finally { setBusy(false); }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input autoFocus placeholder="查找…" value={find}
          onChange={(e) => { setFind(e.target.value); setHits(null); setCount(null); }} />
        <input placeholder="替换为…" value={repl} onChange={(e) => setRepl(e.target.value)} />
        <div className="row" style={{ padding: "8px 14px", gap: 8 }}>
          <button onClick={preview} disabled={busy || !find}>预览命中</button>
          <button className="primary" onClick={apply} disabled={busy || !find}>全部替换</button>
          {count != null && <span className="muted small">命中 {count} 处</span>}
          <span className="grow" style={{ flex: 1 }} />
          <button onClick={onClose}>关闭</button>
        </div>
        {hits && (
          <div className="results">
            {hits.map((h, i) => (
              <div key={i} className="res small">
                <span className="k">{h.path}</span><span className="muted">{h.before}</span>
              </div>
            ))}
            {!hits.length && <div className="res muted">无命中</div>}
          </div>
        )}
      </div>
    </div>
  );
}
