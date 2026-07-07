// 通用检查器:选中任意实体 → 右栏编辑。常用字段友好输入 + 全字段 JSON 兜底(任意字段可改)。
import React, { useEffect, useMemo, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

const SINGLETONS = new Set(["premise", "arc", "meta", "meta_progress", "audience", "background"]);
const FRIENDLY = ["name", "title", "proposition", "stance", "description", "text", "label", "nature", "rewrites", "intent_summary", "manifest"];

// 与后端 api._entity_id 同源:决定如何用 id 定位一个实体
export function entityId(carrier: string, e: any): string | undefined {
  if (!e) return undefined;
  switch (carrier) {
    case "endings": return e.node_id;
    case "variables": return `${e.namespace}.${e.name}`;
    case "stat_blocks": return e.name;
    case "failure_levels": return e.level;
    case "pacing": return `${e.kind}:${e.name}`;
    default: return e.id ?? e.node_id;
  }
}

function findEntity(project: any, carrier: string, id: string): any | null {
  if (SINGLETONS.has(carrier)) return project[carrier] ?? null;
  const arr = project[carrier];
  if (!Array.isArray(arr)) return null;
  return arr.find((e: any) => entityId(carrier, e) === id) ?? null;
}

export function Inspector({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const sel = s.selection!;
  const entity = useMemo(() => (s.project ? findEntity(s.project, sel.carrier, sel.id) : null), [s.project, sel]);
  const [draft, setDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);

  useEffect(() => {
    setDraft(entity ? JSON.stringify(entity, null, 2) : "");
    setDirty(false); setErr(null);
  }, [entity, sel.carrier, sel.id]);

  if (!entity) return (
    <div>
      <div className="row"><b>检查器</b><span className="grow" /><button onClick={s.clearSelection}>×</button></div>
      <p className="muted">未找到 {sel.carrier} / {sel.id}</p>
    </div>
  );

  const friendlyKeys = FRIENDLY.filter((k) => k in entity);

  const patchField = (k: string, v: any) => {
    try {
      const obj = JSON.parse(draft);
      obj[k] = v;
      setDraft(JSON.stringify(obj, null, 2));
      setDirty(true);
    } catch { /* draft 暂不合法,忽略 */ }
  };

  const save = async () => {
    let obj: any;
    try { obj = JSON.parse(draft); } catch (e: any) { setErr("JSON 不合法:" + e.message); return; }
    const id = SINGLETONS.has(sel.carrier) ? "_" : (entityId(sel.carrier, obj) ?? sel.id);
    try { await s.updateEntity(sel.carrier, id, obj); setDirty(false); setErr(null); onChanged?.(); }
    catch (e: any) { setErr(String(e.message ?? e)); }
  };

  const del = async () => {
    if (!confirm(`删除 ${sel.carrier} / ${sel.id}?`)) return;
    try { await s.deleteEntity(sel.carrier, sel.id); onChanged?.(); } catch (e: any) { setErr(String(e.message ?? e)); }
  };

  return (
    <div>
      <div className="row">
        <b>{entity.name ?? entity.title ?? entity.id ?? sel.id}</b>
        <span className="grow" />
        <button onClick={s.clearSelection}>×</button>
      </div>
      <div className="small muted" style={{ marginBottom: 10 }}>{sel.carrier}</div>

      {friendlyKeys.map((k) => (
        <div className="field" key={k}>
          <label>{k}</label>
          {(typeof entity[k] === "string" && (entity[k].length > 40 || k === "text" || k === "proposition" || k === "rewrites"))
            ? <textarea value={(JSON.parse(draftSafe(draft, entity))[k] ?? "") as string} onChange={(e) => patchField(k, e.target.value)} />
            : <input value={(JSON.parse(draftSafe(draft, entity))[k] ?? "") as string} onChange={(e) => patchField(k, e.target.value)} />}
        </div>
      ))}

      {"status" in entity && (
        <div className="field">
          <label>status</label>
          <select value={JSON.parse(draftSafe(draft, entity)).status ?? "todo"} onChange={(e) => patchField("status", e.target.value)}>
            <option value="todo">todo</option>
            <option value="tocomplete">tocomplete</option>
            <option value="done">done</option>
          </select>
        </div>
      )}

      {entity.provenance?.source && (
        <div className="field"><label>出处</label>
          <a onClick={() => s.setActiveView("provenance")}>{entity.provenance.source}</a></div>
      )}

      <details style={{ marginTop: 10 }}>
        <summary className="muted small">全部字段(JSON)</summary>
        <textarea style={{ minHeight: 200, fontFamily: "monospace", fontSize: 12 }}
          value={draft} onChange={(e) => { setDraft(e.target.value); setDirty(true); }} />
      </details>

      <details style={{ marginTop: 8 }}
        onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) api.history().then(setHistory).catch(() => {}); }}>
        <summary className="muted small">修订历史(全局快照,可还原)</summary>
        {history.length === 0 && <p className="muted small">暂无快照</p>}
        {history.map((ts) => (
          <div className="row small" key={ts} style={{ justifyContent: "space-between", marginTop: 4 }}>
            <span className="muted">{ts}</span>
            <button onClick={async () => {
              if (confirm("还原到该时间点?当前态会先自动留底,可再还原回来。")) {
                await api.restoreHistory(ts); await s.reload(); onChanged?.();
              }
            }}>还原</button>
          </div>
        ))}
      </details>

      {err && <p className="sev-high small">{err}</p>}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="primary" disabled={!dirty} onClick={save}>保存</button>
        {!SINGLETONS.has(sel.carrier) && <button onClick={del}>删除</button>}
      </div>
    </div>
  );
}

function draftSafe(draft: string, fallback: any): string {
  try { JSON.parse(draft); return draft; } catch { return JSON.stringify(fallback); }
}
