// 命令面板(Ctrl-P):模糊跳到任意实体或视图(Inky goto)。
import React, { useEffect, useMemo, useState } from "react";
import { useStudio } from "../store";
import { VIEWS } from "../views";

interface Item { kind: string; label: string; carrier?: string; id?: string; view?: string; }

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const s = useStudio();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);

  const items = useMemo<Item[]>(() => {
    const out: Item[] = VIEWS.map((v) => ({ kind: "视图", label: v.label, view: v.id }));
    const p = s.project;
    if (p) {
      const push = (carrier: string, arr: any[], lab: (e: any) => string) =>
        (arr || []).forEach((e) => out.push({ kind: carrier, label: lab(e), carrier, id: e.id ?? e.node_id }));
      push("characters", p.characters, (e) => e.name);
      push("scenes", p.scenes, (e) => e.title ?? e.id);
      push("nodes", p.nodes, (e) => e.title ?? e.id);
      push("variables", p.variables, (e) => `${e.namespace}.${e.name}`);
      push("tags", p.tags, (e) => e.name);
      push("reveal_layers", p.reveal_layers, (e) => e.title ?? e.id);
      push("endings", p.endings, (e) => e.name);
      push("relationships", p.relationships, (e) => e.label ?? `${e.a}↔${e.b}`);
      push("world", p.world, (e) => e.name);
    }
    return out;
  }, [s.project]);

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return items.slice(0, 50);
    return items.filter((i) => (i.label + i.kind).toLowerCase().includes(t)).slice(0, 50);
  }, [items, q]);

  useEffect(() => setSel(0), [q]);

  const go = (i: Item) => {
    if (i.view) s.setActiveView(i.view);
    else if (i.carrier && i.id) s.jumpTo(i.carrier, i.id);
    onClose();
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input autoFocus placeholder="跳到任意东西…(视图 / 角色 / 场景 / 变量 / 标签 …)" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setSel((x) => Math.min(x + 1, filtered.length - 1));
            if (e.key === "ArrowUp") setSel((x) => Math.max(x - 1, 0));
            if (e.key === "Enter" && filtered[sel]) go(filtered[sel]);
          }} />
        <div className="results">
          {filtered.map((i, idx) => (
            <div key={idx} className={"res" + (idx === sel ? " sel" : "")} onMouseEnter={() => setSel(idx)} onClick={() => go(i)}>
              <span className="k">{i.kind}</span><span>{i.label}</span>
            </div>
          ))}
          {!filtered.length && <div className="res muted">无匹配</div>}
        </div>
      </div>
    </div>
  );
}
