// 场景视图:按出现序的卡片列表。每卡展示意图/客观事件/价值转向/出场角色/标签/状态,
// 可点开检查器编辑,可"钻取"展开语义规格 + 成文行。
import React, { useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

function statusClass(st: string): string {
  return st === "done" ? "status-done" : st === "tocomplete" ? "status-tocomplete" : "status-todo";
}

type BatchStatus = "todo" | "tocomplete" | "done";

export function ScenesView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  // sceneId -> drilldown 结果 | "loading"
  const [drill, setDrill] = useState<Record<string, any>>({});
  // 合并模式:已选第一场的 id(再点另一张卡作为 b 完成合并)
  const [mergeFrom, setMergeFrom] = useState<string | null>(null);
  // 多选模式
  const [multi, setMulti] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchStatus, setBatchStatus] = useState<BatchStatus>("done");
  const [busy, setBusy] = useState(false);

  const refresh = async () => { await s.reload(); onChanged?.(); };

  const doSplit = async (sc: { id: string }, e: React.MouseEvent) => {
    e.stopPropagation();
    const raw = prompt("在第几条客观事件后切分?", "1");
    if (raw === null) return;
    const at = parseInt(raw, 10);
    if (!Number.isFinite(at)) { alert("请输入数字。"); return; }
    setBusy(true);
    try {
      const r = await api.sceneSplit(sc.id, at);
      await refresh();
      if (r.warnings && r.warnings.length > 0) alert("拆分提示:\n" + r.warnings.join("\n"));
    } catch (err: any) {
      alert("拆分失败:" + String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const onMergeClick = (sc: { id: string }, e: React.MouseEvent) => {
    e.stopPropagation();
    setMergeFrom(sc.id);
  };

  const cancelMerge = () => setMergeFrom(null);

  const completeMerge = async (bId: string) => {
    const a = mergeFrom;
    if (!a) return;
    if (a === bId) { setMergeFrom(null); return; }
    setBusy(true);
    try {
      const r = await api.sceneMerge(a, bId);
      setMergeFrom(null);
      await refresh();
      if (r.warnings && r.warnings.length > 0) alert("合并提示:\n" + r.warnings.join("\n"));
    } catch (err: any) {
      alert("合并失败:" + String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const toggleMulti = () => {
    setMulti((m) => {
      if (m) setSelected(new Set());
      return !m;
    });
    setMergeFrom(null);
  };

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelected((cur) => {
      const n = new Set(cur);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const applyBatch = async () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    setBusy(true);
    try {
      await api.batchUpdate("scenes", ids, { status: batchStatus });
      setSelected(new Set());
      await refresh();
    } catch (err: any) {
      alert("批量改状态失败:" + String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const nameOf = (carrier: "characters" | "tags", id: string): string => {
    const arr: any[] = (p as any)[carrier] ?? [];
    const e = arr.find((x) => x.id === id);
    return e?.name ?? id;
  };

  const toggleDrill = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (drill[id] !== undefined) {
      setDrill((d) => {
        const n = { ...d };
        delete n[id];
        return n;
      });
      return;
    }
    setDrill((d) => ({ ...d, [id]: "loading" }));
    api.drilldown(id).then((res) => setDrill((d) => ({ ...d, [id]: res }))).catch(() => {
      setDrill((d) => ({ ...d, [id]: null }));
    });
  };

  const newScene = async () => {
    const id = "scene_" + Date.now().toString(36);
    await s.createEntity("scenes", {
      id,
      title: "新场景",
      links: { characters: [], places: [], lines: [] },
      preconditions: [], effects: [], choices: [],
      summary: {}, objective_events: [], causality: {}, value_shift: {},
      intent: {}, render_constraints: { voices: [], show_not_tell: [] },
      line_refs: [], tags: [], serves_ideas: [], status: "todo",
    });
    onChanged?.();
    s.select("scenes", id);
  };

  return (
    <div>
      <div className="row">
        <h2 className="view-h">场景</h2>
        <span className="grow" style={{ flex: 1 }} />
        <button className={multi ? "primary" : ""} onClick={toggleMulti}>{multi ? "退出多选" : "多选"}</button>
        <button className="primary" onClick={newScene}>新增场景</button>
      </div>
      <p className="view-sub">按出现序排列。每个场景是一次价值转向——先确认它在干什么，再去钻取语义与成文。</p>

      {mergeFrom && (
        <div className="card row" style={{ alignItems: "center" }}>
          <span className="small">合并模式：再点一张卡作为「合并入」目标</span>
          <span className="grow" style={{ flex: 1 }} />
          <button className="small" onClick={cancelMerge}>取消</button>
        </div>
      )}

      {p.scenes.length === 0 && <p className="muted">还没有场景。</p>}
      {p.scenes.map((sc) => {
        const vs = sc.value_shift;
        const hasShift = !!(vs && (vs.from || vs.to));
        const d = drill[sc.id];
        const isMergeSource = mergeFrom === sc.id;
        const onCardClick = () => {
          if (mergeFrom) { completeMerge(sc.id); return; }
          s.select("scenes", sc.id);
        };
        return (
          <div
            className={"card clickable" + (isMergeSource ? " sev-high" : "")}
            key={sc.id}
            onClick={onCardClick}
          >
            <div className="row">
              {multi && (
                <input
                  type="checkbox"
                  checked={selected.has(sc.id)}
                  onClick={(e) => toggleSelect(sc.id, e)}
                  onChange={() => { /* 受控:状态由 onClick 切换 */ }}
                />
              )}
              <span className={"status-dot " + statusClass(sc.status)} />
              <b>{sc.title ?? sc.id}</b>
              {isMergeSource && <span className="chip">合并源</span>}
              <span className="grow" style={{ flex: 1 }} />
              <button className="small" disabled={busy} onClick={(e) => doSplit(sc, e)}>拆分</button>
              <button className="small" disabled={busy} onClick={(e) => onMergeClick(sc, e)}>合并…</button>
              <button className="small" onClick={(e) => toggleDrill(sc.id, e)}>钻取</button>
            </div>

            {sc.intent_summary && <div className="small" style={{ marginTop: 6 }}>{sc.intent_summary}</div>}

            {sc.objective_events.length > 0 && (
              <ul className="small muted" style={{ margin: "6px 0", paddingLeft: 18 }}>
                {sc.objective_events.slice(0, 2).map((ev, i) => <li key={i}>{ev}</li>)}
                {sc.objective_events.length > 2 && <li>… 还有 {sc.objective_events.length - 2} 条</li>}
              </ul>
            )}

            <div className="small" style={{ marginTop: 4 }}>
              价值转向：{hasShift
                ? <span>{vs.from ?? "—"} → {vs.to ?? "—"}</span>
                : <span className="sev-high">未转</span>}
            </div>

            {(sc.links.characters.length > 0 || sc.tags.length > 0) && (
              <div className="wrap" style={{ marginTop: 8 }}>
                {sc.links.characters.map((cid) => (
                  <span key={"c" + cid} className="chip click"
                    onClick={(e) => { e.stopPropagation(); s.jumpTo("characters", cid); }}>
                    {nameOf("characters", cid)}
                  </span>
                ))}
                {sc.tags.map((tid) => (
                  <span key={"t" + tid} className="chip click"
                    onClick={(e) => { e.stopPropagation(); s.jumpTo("tags", tid); }}>
                    #{nameOf("tags", tid)}
                  </span>
                ))}
              </div>
            )}

            {d !== undefined && (
              <div onClick={(e) => e.stopPropagation()} style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                {d === "loading" ? <span className="muted small">加载中…</span> : d === null ? (
                  <span className="muted small">无钻取数据</span>
                ) : (
                  <Drilldown data={d} />
                )}
              </div>
            )}
          </div>
        );
      })}

      {multi && selected.size > 0 && (
        <div className="card row" style={{ alignItems: "center", position: "sticky", bottom: 0 }}>
          <span className="small">已选 {selected.size}</span>
          <span className="muted small">· 改状态</span>
          <select
            className="small"
            value={batchStatus}
            onChange={(e) => setBatchStatus(e.target.value as BatchStatus)}
          >
            <option value="todo">todo</option>
            <option value="tocomplete">tocomplete</option>
            <option value="done">done</option>
          </select>
          <span className="grow" style={{ flex: 1 }} />
          <button className="primary small" disabled={busy} onClick={applyBatch}>应用</button>
        </div>
      )}
    </div>
  );
}

function kv(label: string, v: any) {
  if (v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0)) return null;
  const text = Array.isArray(v) ? v.join("、") : typeof v === "object" ? JSON.stringify(v) : String(v);
  return (
    <div className="small" style={{ marginBottom: 3 }}>
      <span className="muted">{label}：</span>{text}
    </div>
  );
}

function Drilldown({ data }: { data: any }) {
  const sem = data.scene_semantic ?? {};
  const prose: any[] = data.prose ?? [];
  return (
    <div>
      {data.beat && (
        <div className="small muted" style={{ marginBottom: 6 }}>
          所属 beat：{data.beat.title ?? data.beat.id}{data.beat.function ? `（${data.beat.function}）` : ""}
        </div>
      )}
      <div className="muted small"><b>语义规格</b></div>
      {kv("客观事件", sem.objective_events)}
      {kv("因为何时", sem.causality?.why_now)}
      {kv("为何必然", sem.causality?.why_inevitable)}
      {kv("价值转向", sem.value_shift?.from || sem.value_shift?.to ? `${sem.value_shift?.from ?? "—"} → ${sem.value_shift?.to ?? "—"}` : null)}
      {kv("情绪", sem.intent?.emotion)}
      {kv("一拳", sem.intent?.punch)}
      {kv("余韵", sem.intent?.afterglow)}
      {kv("叙述距离", sem.render_constraints?.distance)}
      {kv("聚焦", sem.render_constraints?.focalization)}

      <div className="muted small" style={{ marginTop: 8 }}><b>成文行（{prose.length}）</b></div>
      {prose.length === 0 && <div className="muted small">尚未成文</div>}
      {prose.map((pl) => (
        <div key={pl.id} className="small" style={{ marginTop: 4 }}>
          {pl.speaker && <span className="muted">{pl.speaker}：</span>}
          {pl.text ? pl.text : <span className="sev-high">待成文</span>}
        </div>
      ))}
    </div>
  );
}
