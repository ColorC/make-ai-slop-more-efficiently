// 关系视图:relationshipGraph 投影 → reactflow 关系图(节点=角色,边=关系)。下方关系表可点行编辑。
import React, { useEffect, useMemo, useState } from "react";
import ReactFlow, { Background, Controls, MarkerType } from "reactflow";
import type { Node, Edge } from "reactflow";
import { useStudio } from "../store";
import { api } from "../api";
import type { Relationship } from "../types";

export function RelationshipsView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const [graph, setGraph] = useState<any>(null);

  useEffect(() => {
    api.relationshipGraph().then(setGraph).catch(() => {});
  }, [s.project]);

  const chars = p.characters ?? [];
  const rels = p.relationships ?? [];
  const nameOf = (cid?: string | null) => (cid ? (chars.find((c) => c.id === cid)?.name ?? cid) : "");

  // 节点来源:优先投影的 nodes,否则用 project.characters。环形布局自算 position。
  const nodes: Node[] = useMemo(() => {
    const raw: { id: string; label: string }[] = (() => {
      const gn = graph?.nodes;
      if (Array.isArray(gn) && gn.length) {
        return gn.map((n: any) => ({
          id: n?.id ?? n?.char_id ?? String(n),
          label: n?.label ?? n?.name ?? nameOf(n?.id ?? n?.char_id) ?? String(n),
        }));
      }
      return chars.map((c) => ({ id: c.id, label: c.name }));
    })();
    const n = Math.max(raw.length, 1);
    const radius = 80 + n * 38;
    const cx = radius + 60, cy = radius + 20;
    return raw.map((r, i) => {
      const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
      return {
        id: r.id,
        position: { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) },
        data: { label: r.label },
      };
    });
  }, [graph, chars]);

  // 边来源:优先投影的 edges,否则用 project.relationships。
  const edges: Edge[] = useMemo(() => {
    const ge = graph?.edges;
    if (Array.isArray(ge) && ge.length) {
      return ge.map((e: any, i: number) => ({
        id: e?.id ?? `redge_${i}`,
        source: e?.from ?? e?.source ?? e?.a,
        target: e?.to ?? e?.target ?? e?.b,
        label: e?.label ?? e?.nature ?? "",
        markerEnd: { type: MarkerType.ArrowClosed },
      }));
    }
    return rels.map((r) => ({
      id: r.id,
      source: r.a,
      target: r.b,
      label: r.label ?? r.nature ?? "",
      markerEnd: { type: MarkerType.ArrowClosed },
    }));
  }, [graph, rels]);

  const addRelationship = async () => {
    const id = "rel_" + Math.random().toString(36).slice(2, 8);
    const a = chars[0]?.id ?? "";
    const b = chars[1]?.id ?? chars[0]?.id ?? "";
    await s.createEntity("relationships", { id, a, b, nature: "", label: "", projection: "" });
    onChanged?.();
    s.select("relationships", id);
  };

  return (
    <div>
      <div className="row">
        <h2 className="view-h">关系</h2>
        <span className="grow" style={{ flex: 1 }} />
        <button className="primary" onClick={addRelationship}>新增关系</button>
      </div>
      <p className="view-sub">人物关系网 —— 点节点跳到角色,点边或下方表格行编辑关系。</p>

      <div className="rf-wrap">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          onNodeClick={(_, n) => s.jumpTo("characters", n.id)}
          onEdgeClick={(_, e) => s.select("relationships", e.id)}
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      <h3 className="view-h" style={{ fontSize: 14, marginTop: 16 }}>关系表（{rels.length}）</h3>
      {rels.length === 0 && <p className="muted">还没有关系。</p>}
      <div className="grid">
        {rels.map((r: Relationship) => (
          <div className="card clickable" key={r.id} onClick={() => s.select("relationships", r.id)}>
            <div className="row wrap">
              <span
                className="chip click"
                onClick={(e) => { e.stopPropagation(); s.jumpTo("characters", r.a); }}
              >
                {nameOf(r.a)}
              </span>
              <span className="muted">↔</span>
              <span
                className="chip click"
                onClick={(e) => { e.stopPropagation(); s.jumpTo("characters", r.b); }}
              >
                {nameOf(r.b)}
              </span>
              {r.nature && <span className="chip small">{r.nature}</span>}
              {r.label && <span className="muted small">{r.label}</span>}
            </div>
            {r.projection && <p className="small muted" style={{ margin: "6px 0 0" }}>{r.projection}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
