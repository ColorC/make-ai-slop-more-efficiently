// 世界设定视图:p.world 是 WorldNode 树(children 递归)。渲染成可展开层级树,点节点编辑。
import React, { useState } from "react";
import { useStudio } from "../store";
import type { WorldNode } from "../types";

function TreeNode({
  node, depth, onSelect, isSel,
}: {
  node: WorldNode;
  depth: number;
  onSelect: (id: string) => void;
  isSel: (id: string) => boolean;
}) {
  const [open, setOpen] = useState(true);
  const kids = node.children ?? [];
  const hasKids = kids.length > 0;
  return (
    <div>
      <div
        className="card clickable"
        style={{ marginLeft: depth * 18, marginBottom: 4, borderColor: isSel(node.id) ? "var(--accent)" : undefined }}
        onClick={() => onSelect(node.id)}
      >
        <div className="row">
          {hasKids ? (
            <button
              style={{ padding: "0 6px", lineHeight: 1.4 }}
              onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
            >
              {open ? "▾" : "▸"}
            </button>
          ) : (
            <span style={{ width: 22, display: "inline-block" }} className="muted">·</span>
          )}
          <b>{node.name || node.id}</b>
          {hasKids && <span className="chip small muted">{kids.length}</span>}
        </div>
        {node.description && (
          <p className="small muted" style={{ margin: "6px 0 0" }}>{node.description}</p>
        )}
      </div>
      {open && hasKids && (
        <div>
          {kids.map((k) => (
            <TreeNode key={k.id} node={k} depth={depth + 1} onSelect={onSelect} isSel={isSel} />
          ))}
        </div>
      )}
    </div>
  );
}

export function WorldView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const world = p.world ?? [];

  const onSelect = (id: string) => s.select("world", id);
  const isSel = (id: string) => s.selection?.carrier === "world" && s.selection?.id === id;

  const addTop = async () => {
    const id = "world_" + Math.random().toString(36).slice(2, 8);
    await s.createEntity("world", {
      id, name: "新条目", description: "", custom_fields: {}, children: [],
    });
    onChanged?.();
    s.select("world", id);
  };

  return (
    <div>
      <div className="row">
        <h2 className="view-h">世界设定</h2>
        <span className="grow" style={{ flex: 1 }} />
        <button className="primary" onClick={addTop}>新增顶层条目</button>
      </div>
      <p className="view-sub">设定百科的层级树 —— 点条目编辑,点三角展开/收起子项。</p>

      {world.length === 0 && <p className="muted">还没有世界设定条目,点右上角新增。</p>}

      {world.map((n) => (
        <TreeNode key={n.id} node={n} depth={0} onSelect={onSelect} isSel={isSel} />
      ))}
    </div>
  );
}
