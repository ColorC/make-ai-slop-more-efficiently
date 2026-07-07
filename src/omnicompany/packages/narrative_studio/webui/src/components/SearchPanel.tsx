// 全局搜索浮层(Ctrl-F):跨载体检索,点结果跳转。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";
import type { SearchHit } from "../types";

// 与后端 queries.search 返回的 kind(载体单数名)对齐
const KIND_CARRIER: Record<string, string> = {
  scene: "scenes", character: "characters", beat: "beats",
  prose_line: "prose_lines", reveal_layer: "reveal_layers",
  node: "nodes", connection: "connections", ending: "endings",
  relationship: "relationships", storyline: "storylines",
  world_node: "world", tag: "tags", note: "notes",
  voice: "voices", register: "registers", pressure: "pressures",
  premise: "premise",
};

export function SearchPanel({ onClose }: { onClose: () => void }) {
  const s = useStudio();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);

  useEffect(() => {
    const t = setTimeout(() => {
      if (q.trim()) api.search(q).then(setHits).catch(() => setHits([]));
      else setHits([]);
    }, 150);
    return () => clearTimeout(t);
  }, [q]);

  const go = (h: SearchHit) => {
    const carrier = KIND_CARRIER[h.kind] ?? h.kind;
    s.jumpTo(carrier, h.id);
    onClose();
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input autoFocus placeholder="全文搜索(跨载体)…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="results">
          {hits.map((h, i) => (
            <div key={i} className="res" onClick={() => go(h)}>
              <span className="k">{h.kind}</span>
              <span>
                <div>{h.title}</div>
                {h.snippet && <div className="small muted">{h.snippet}</div>}
              </span>
            </div>
          ))}
          {q && !hits.length && <div className="res muted">无匹配</div>}
        </div>
      </div>
    </div>
  );
}
