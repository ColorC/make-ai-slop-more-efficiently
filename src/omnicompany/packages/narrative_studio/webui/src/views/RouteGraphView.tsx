// 路线/分支视图:用 reactflow 画节点图(节点按存储的 x/y 定位)。
// 结局节点底色不同;带条件的边用不同色。点节点跳到对应载体。
import React, { useEffect, useMemo, useState } from "react";
import ReactFlow, { Background, Controls, MarkerType } from "reactflow";
import type { Node as RFNode, Edge as RFEdge } from "reactflow";
import "reactflow/dist/style.css";
import { useStudio } from "../store";
import { api } from "../api";

interface RGNode { id: string; type: string; title?: string | null; x: number; y: number; route?: string | null }
interface RGEdge { id: string; source: string; target: string; label?: string | null; has_condition: boolean; has_effects: boolean }
interface RouteGraph { nodes: RGNode[]; edges: RGEdge[] }

const ENDING_BG = "#3a2230";
const ENDING_BORDER = "#c08bff";
const NODE_BG = "#21252c";
const NODE_BORDER = "#2c313a";
const COND_STROKE = "#e6b450"; // 带条件的边
const PLAIN_STROKE = "#8b94a3";

export function RouteGraphView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const [g, setG] = useState<RouteGraph | null>(null);
  const [badEdges, setBadEdges] = useState<Set<string>>(new Set());
  const [badNodes, setBadNodes] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.routeGraph().then((r: any) => setG(r)).catch(() => {});
    api.health().then((issues) => {
      const be = new Set<string>();
      const bn = new Set<string>();
      for (const iss of issues) {
        if (iss.severity !== "high" && iss.severity !== "medium") continue;
        const loc = iss.location;
        if (!loc) continue;
        const idx = loc.indexOf(":");
        if (idx < 0) continue;
        const kind = loc.slice(0, idx);
        const id = loc.slice(idx + 1);
        if (!id) continue;
        if (kind === "connection") be.add(id);
        else if (kind === "node") bn.add(id);
        else if (kind === "ending") bn.add(id); // ending 的 node_id 即图节点 id
      }
      setBadEdges(be);
      setBadNodes(bn);
    }).catch(() => {});
  }, [s.project]);

  // 已存在的场景 id 集合(用于把 scene 型节点跳到场景)
  const sceneIds = useMemo(() => new Set((p.scenes || []).map((sc) => sc.id)), [p.scenes]);

  const nodes: RFNode[] = useMemo(() => {
    if (!g) return [];
    return g.nodes.map((n) => {
      const isEnding = n.type === "ending";
      const bad = badNodes.has(n.id);
      const baseLabel = `${n.title || n.id}`;
      return {
        id: n.id,
        position: { x: n.x, y: n.y },
        data: { label: bad ? `⚠ ${baseLabel}` : baseLabel, nodeType: n.type },
        style: {
          background: isEnding ? ENDING_BG : NODE_BG,
          border: bad ? "1px solid var(--err)" : `1px solid ${isEnding ? ENDING_BORDER : NODE_BORDER}`,
          boxShadow: bad ? "0 0 0 1px var(--err)" : undefined,
          borderRadius: 8,
          color: "#d7dce4",
          padding: "6px 10px",
          fontSize: 12,
          minWidth: 90,
        },
      };
    });
  }, [g, badNodes]);

  const edges: RFEdge[] = useMemo(() => {
    if (!g) return [];
    return g.edges.map((e) => {
      const bad = badEdges.has(e.id);
      const stroke = bad ? "var(--err)" : e.has_condition ? COND_STROKE : PLAIN_STROKE;
      const rawLabel = e.label ?? undefined;
      const label = bad ? `⚠ ${e.label ?? ""}`.trimEnd() : rawLabel;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label,
        style: { stroke, strokeWidth: bad ? 2 : 1.5 },
        labelStyle: { fill: "#d7dce4", fontSize: 11 },
        labelBgStyle: { fill: "#1b1e24" },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
      };
    });
  }, [g, badEdges]);

  const onNodeClick = (_: unknown, n: RFNode) => {
    const meta = g?.nodes.find((x) => x.id === n.id);
    // scene 型节点用 node_ref 反查对应场景(兼容 importer/新建/拆分产出的任意场景 id)
    if (meta?.type === "scene") {
      const sc = (s.project?.scenes || []).find((x) => x.node_ref === n.id);
      if (sc) { s.jumpTo("scenes", sc.id); return; }
    }
    if (meta?.type === "ending") { s.jumpTo("endings", n.id); return; }
    s.jumpTo("nodes", n.id);
  };

  if (!g) return (
    <div>
      <h2 className="view-h">路线 / 分支</h2>
      <p className="view-sub">玩家如何穿过这张图 —— 节点、条件分支与结局。</p>
      <p className="muted">加载中…</p>
    </div>
  );

  return (
    <div>
      <h2 className="view-h">路线 / 分支</h2>
      <p className="view-sub">玩家如何穿过这张图 —— 节点、条件分支与结局。</p>

      <div className="rf-wrap">
        <ReactFlow nodes={nodes} edges={edges} fitView onNodeClick={onNodeClick}>
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      <div className="wrap" style={{ marginTop: 10 }}>
        <span className="chip"><span className="status-dot" style={{ background: NODE_BG, border: `1px solid ${NODE_BORDER}`, marginRight: 5 }} />普通节点</span>
        <span className="chip"><span className="status-dot" style={{ background: ENDING_BG, border: `1px solid ${ENDING_BORDER}`, marginRight: 5 }} />结局节点</span>
        <span className="chip"><span className="status-dot" style={{ background: PLAIN_STROKE, marginRight: 5 }} />普通边</span>
        <span className="chip"><span className="status-dot" style={{ background: COND_STROKE, marginRight: 5 }} />带条件的边</span>
        <span className="chip"><span className="status-dot" style={{ background: "var(--err)", marginRight: 5 }} />⚠ 健康问题(就地标记)</span>
      </div>

      <div className="wrap" style={{ marginTop: 8 }}>
        <span className="small muted">
          红色描边 / ⚠ 前缀标记了存在 high/medium 健康问题的节点与边({badNodes.size} 节点、{badEdges.size} 边)。
        </span>
        <button
          className="chip click"
          onClick={() => { s.setActiveView("health"); onChanged?.(); }}
        >
          查看健康清单 →
        </button>
      </div>
    </div>
  );
}
