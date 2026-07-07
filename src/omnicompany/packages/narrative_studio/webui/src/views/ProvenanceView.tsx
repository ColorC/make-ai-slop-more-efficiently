// 出处钻取(正向):列出所有讨论稿出处,选一个 → 看它落成了哪些实体。
import React, { useEffect, useMemo, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";
import type { Project, WorldNode } from "../types";

// provenance_forward 返回的 kind(单数)→ 载体名(jumpTo 用)
const KIND_TO_CARRIER: Record<string, string> = {
  premise: "premise",
  reveal_layer: "reveal_layers",
  character: "characters",
  relationship: "relationships",
  pressure: "pressures",
  beat: "beats",
  node: "nodes",
  ending: "endings",
  scene: "scenes",
  world: "world",
};

const KIND_LABEL: Record<string, string> = {
  premise: "立意", reveal_layer: "揭示层", character: "角色", relationship: "关系",
  pressure: "压力", beat: "节拍", node: "节点", ending: "结局", scene: "场景", world: "世界设定",
};

// 从 project 各带 provenance 的载体扫一遍 source,去重排序。
function collectSources(p: Project): string[] {
  const set = new Set<string>();
  const add = (prov?: { source?: string | null } | null) => {
    const src = prov?.source;
    if (src && src.trim()) set.add(src);
  };

  add(p.premise?.provenance);
  p.reveal_layers.forEach((r) => add(r.provenance));
  p.characters.forEach((c) => add(c.provenance));
  p.relationships.forEach((r) => add(r.provenance));
  p.pressures.forEach((pr) => add(pr.provenance));
  p.beats.forEach((b) => add(b.provenance));
  p.nodes.forEach((n) => add(n.provenance));
  p.endings.forEach((e) => add(e.provenance));
  p.scenes.forEach((s) => add(s.provenance));

  const walkWorld = (w: WorldNode) => {
    add(w.provenance);
    (w.children || []).forEach(walkWorld);
  };
  p.world.forEach(walkWorld);

  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

export function ProvenanceView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const sources = useMemo(() => collectSources(p), [p]);
  const [active, setActive] = useState<string>("");
  const [rows, setRows] = useState<any[]>([]);

  useEffect(() => {
    if (!active) { setRows([]); return; }
    api.provenanceForward(active).then((r) => setRows(Array.isArray(r) ? r : [])).catch(() => setRows([]));
  }, [active, s.project]);

  return (
    <div>
      <h2 className="view-h">出处钻取</h2>
      <p className="view-sub">每一段讨论稿 / 来源,正向追它落成了哪些实体——看想法是怎么变成作品的。</p>

      <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
        <div style={{ width: 240, flexShrink: 0 }}>
          <h3 className="view-h" style={{ fontSize: 14 }}>出处 ({sources.length})</h3>
          {sources.length === 0 && <p className="muted small">还没有任何实体标注 provenance.source。</p>}
          {sources.map((src) => (
            <div
              className={"card clickable" + (src === active ? "" : "")} key={src}
              onClick={() => setActive(src)}
              style={src === active ? { borderColor: "var(--accent)" } : undefined}
            >
              <b className="small">{src}</b>
            </div>
          ))}
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          {!active && <p className="muted">从左侧选一个出处。</p>}
          {active && (
            <>
              <h3 className="view-h" style={{ fontSize: 14 }}>
                「{active}」落成的实体 ({rows.length})
              </h3>
              {rows.length === 0 && <p className="muted small">这个出处目前没有关联到任何实体。</p>}
              {rows.map((r, i) => {
                const carrier = KIND_TO_CARRIER[r.kind] ?? r.kind;
                return (
                  <div className="card clickable" key={`${r.kind}:${r.id}:${i}`} onClick={() => s.jumpTo(carrier, r.id)}>
                    <div className="row">
                      <span className="chip small">{KIND_LABEL[r.kind] ?? r.kind}</span>
                      <b>{r.title || r.id}</b>
                    </div>
                  </div>
                );
              })}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
