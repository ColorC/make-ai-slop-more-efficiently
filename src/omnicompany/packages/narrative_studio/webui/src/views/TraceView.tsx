// 贯穿追踪:选一个追踪对象(角色/变量/标签/主旨),把它在整部里出现的地方拉直成有序列表。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

type TraceKind = "character" | "variable" | "tag" | "idea";

// variableRefs 返回的 where(如 "scene:s1:precond")→ 可跳转的 (carrier, id)
function whereToTarget(kind: string, where: string): { carrier: string; id: string } | null {
  const parts = where.split(":");
  const id = parts[1];
  if (!id) return null;
  switch (kind) {
    case "connection": return { carrier: "connections", id };
    case "node": return { carrier: "nodes", id };
    case "scene": return { carrier: "scenes", id };
    case "ending": return { carrier: "endings", id };
    case "reveal": return { carrier: "reveal_layers", id };
    default: return null;
  }
}

export function TraceView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const [kind, setKind] = useState<TraceKind>("character");
  const [target, setTarget] = useState<string>("");
  const [result, setResult] = useState<any>(null);

  // 当前 kind 的可选对象列表:[{ value, label }]
  const options: { value: string; label: string }[] = (() => {
    switch (kind) {
      case "character":
        return p.characters.map((c) => ({ value: c.id, label: c.name || c.id }));
      case "variable":
        return p.variables.map((v) => {
          const key = `${v.namespace}.${v.name}`;
          return { value: key, label: key };
        });
      case "tag":
        return p.tags.map((t) => ({ value: t.id, label: t.name || t.id }));
      case "idea":
        return (p.premise.controlling_ideas || []).map((idea) => ({ value: idea, label: idea }));
      default:
        return [];
    }
  })();

  // 切换追踪类型时,默认选第一个对象
  useEffect(() => {
    setTarget(options.length ? options[0].value : "");
    setResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, s.project]);

  // 选定对象后拉取对应投影
  useEffect(() => {
    if (!target) { setResult(null); return; }
    const run = async () => {
      try {
        if (kind === "character") setResult(await api.characterScenes(target));
        else if (kind === "variable") setResult(await api.variableRefs(target));
        else if (kind === "tag") setResult(await api.tagOccurrences(target));
        else if (kind === "idea") setResult(await api.ideaAlignment(target));
      } catch { setResult(null); }
    };
    run();
  }, [kind, target, s.project]);

  return (
    <div>
      <h2 className="view-h">贯穿追踪</h2>
      <p className="view-sub">挑一个角色 / 变量 / 标签 / 主旨,看它在整部作品里被用在了哪里,每项可点击跳转。</p>

      <div className="card">
        <div className="row" style={{ gap: 10 }}>
          <div className="field" style={{ marginBottom: 0, minWidth: 140 }}>
            <label>追踪对象类型</label>
            <select value={kind} onChange={(e) => setKind(e.target.value as TraceKind)}>
              <option value="character">角色</option>
              <option value="variable">变量</option>
              <option value="tag">标签 / 伏笔</option>
              <option value="idea">控制主旨</option>
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, flex: 1 }}>
            <label>具体对象</label>
            <select value={target} onChange={(e) => setTarget(e.target.value)} disabled={!options.length}>
              {!options.length && <option value="">(无可选对象)</option>}
              {options.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {kind === "character" && <CharacterTrace rows={result} onJump={s.jumpTo} />}
      {kind === "variable" && <VariableTrace data={result} onJump={s.jumpTo} />}
      {kind === "tag" && <TagTrace rows={result} onJump={s.jumpTo} />}
      {kind === "idea" && <IdeaTrace rows={result} onJump={s.jumpTo} />}
    </div>
  );
}

type Jump = (carrier: string, id: string) => void;

function StatusChip({ status }: { status?: string }) {
  if (!status) return null;
  return <span className="row small"><span className={`status-dot status-${status}`} /> {status}</span>;
}

function Empty() {
  return <p className="muted small">这个对象在整部作品里还没有被用到。</p>;
}

function CharacterTrace({ rows, onJump }: { rows: any; onJump: Jump }) {
  if (!Array.isArray(rows)) return null;
  if (rows.length === 0) return <Empty />;
  return (
    <>
      <p className="muted small">按叙述顺序,共 {rows.length} 个出场场景:</p>
      {rows.map((r: any, i: number) => (
        <div className="card clickable" key={r.scene_id + i} onClick={() => onJump("scenes", r.scene_id)}>
          <div className="row">
            <b>{r.title || r.scene_id}</b>
            <span className="grow" style={{ flex: 1 }} />
            {r.beat && <span className="chip small">节拍 {r.beat}</span>}
            {r.pov && <span className="chip small">POV {r.pov}</span>}
          </div>
        </div>
      ))}
    </>
  );
}

function VariableTrace({ data, onJump }: { data: any; onJump: Jump }) {
  if (!data || (!Array.isArray(data.reads) && !Array.isArray(data.writes))) return null;
  const reads: any[] = data.reads || [];
  const writes: any[] = data.writes || [];
  if (reads.length === 0 && writes.length === 0) return <Empty />;
  const refRow = (r: any, label: string, i: number) => {
    const t = whereToTarget(r.kind, r.where);
    return (
      <div
        className={"card" + (t ? " clickable" : "")} key={label + i + r.where}
        onClick={() => { if (t) onJump(t.carrier, t.id); }}
      >
        <div className="row">
          <span className="chip small">{label}</span>
          <b>{r.where}</b>
          <span className="grow" style={{ flex: 1 }} />
          <span className="muted small">{r.kind}</span>
        </div>
      </div>
    );
  };
  return (
    <>
      <h3 className="view-h" style={{ fontSize: 14, marginTop: 14 }}>写入 ({writes.length})</h3>
      {writes.length === 0 ? <p className="muted small">无处写入。</p> : writes.map((r, i) => refRow(r, "写", i))}
      <h3 className="view-h" style={{ fontSize: 14, marginTop: 14 }}>读取 ({reads.length})</h3>
      {reads.length === 0 ? <p className="muted small">无处读取。</p> : reads.map((r, i) => refRow(r, "读", i))}
    </>
  );
}

function TagTrace({ rows, onJump }: { rows: any; onJump: Jump }) {
  if (!Array.isArray(rows)) return null;
  if (rows.length === 0) return <Empty />;
  return (
    <>
      <p className="muted small">按叙述顺序,共 {rows.length} 处出现:</p>
      {rows.map((r: any, i: number) => {
        const carrier = r.kind === "line" ? "prose_lines" : "scenes";
        return (
          <div className="card clickable" key={r.kind + r.id + i} onClick={() => onJump(carrier, r.id)}>
            <div className="row">
              <span className="chip small">{r.kind === "line" ? "成文行" : "场景"}</span>
              <b>{r.title || r.id}</b>
            </div>
          </div>
        );
      })}
    </>
  );
}

function IdeaTrace({ rows, onJump }: { rows: any; onJump: Jump }) {
  if (!Array.isArray(rows)) return null;
  if (rows.length === 0) return <Empty />;
  return (
    <>
      <p className="muted small">承载此主旨的场景,按叙述顺序,共 {rows.length} 个:</p>
      {rows.map((r: any, i: number) => (
        <div className="card clickable" key={r.scene_id + i} onClick={() => onJump("scenes", r.scene_id)}>
          <div className="row">
            <b>{r.title || r.scene_id}</b>
            <span className="grow" style={{ flex: 1 }} />
            <StatusChip status={r.status} />
          </div>
        </div>
      ))}
    </>
  );
}
