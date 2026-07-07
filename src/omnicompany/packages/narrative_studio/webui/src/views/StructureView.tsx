// 结构视图:结构图(段×线 散卡矩阵,默认)/大纲列表/时间线 三子视图,
// 外加节奏条(接触/深入期 + 七日切片)。
// 2026-07-04 作者:大纲不适合纯列表观看("空白极多/莫名极窄列"),换散卡矩阵为主视图;
// 认可状态(authority: 作者/拟)直接显示在卡上,不冒充定稿。
import React, { useEffect, useMemo, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";
import type { Beat, StoryLine } from "../types";

interface TLBeat { id: string; parent?: string | null; title?: string | null; position: number }
interface TLLine { id: string; title?: string | null; color?: string | null }
interface TimelineData {
  beats: TLBeat[];
  lines: TLLine[];
  cells: Record<string, Record<string, string[]>>;
  unplaced: string[];
}
interface OutlineScene { scene_id: string; title?: string | null; status?: string }
interface OutlineBeat {
  id: string; parent?: string | null; depth: number;
  title?: string | null; function?: string | null; status?: string;
  lane?: string | null; edges?: string[]; authority?: string | null;
  summary?: { sentence?: string | null } | null;
  scenes: OutlineScene[];
}

export function StructureView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const [tab, setTab] = useState<"board" | "timeline" | "outline">("board");
  const [tl, setTl] = useState<TimelineData | null>(null);
  const [outline, setOutline] = useState<OutlineBeat[]>([]);

  useEffect(() => {
    api.timeline().then((r: any) => setTl(r)).catch(() => {});
    api.outline().then((r: any) => setOutline(Array.isArray(r) ? r : [])).catch(() => {});
  }, [s.project]);

  const sceneTitle = useMemo(() => {
    const m: Record<string, string> = {};
    for (const sc of p.scenes || []) m[sc.id] = sc.title || sc.id;
    return m;
  }, [p.scenes]);

  // 节奏:phase=接触/深入期,day=七日切片
  const phases = (p.pacing || []).filter((m) => m.kind === "phase").sort((a, b) => a.position - b.position);
  const days = (p.pacing || []).filter((m) => m.kind === "day").sort((a, b) => a.position - b.position);

  return (
    <div>
      <h2 className="view-h">结构 / 大纲</h2>
      <p className="view-sub">骨架在哪 —— beat 与故事线如何把场景编织成节奏。</p>

      {(phases.length > 0 || days.length > 0) && (
        <div className="card">
          <div className="muted small" style={{ marginBottom: 6 }}>节奏</div>
          {phases.length > 0 && (
            <div className="wrap" style={{ marginBottom: days.length > 0 ? 8 : 0 }}>
              {phases.map((m, i) => (
                <span key={"p" + i} className="chip" title={m.core_event ?? m.main_pressure ?? ""}>
                  {m.name}{m.core_event ? ` · ${m.core_event}` : ""}
                </span>
              ))}
            </div>
          )}
          {days.length > 0 && (
            <div className="wrap">
              {days.map((m, i) => (
                <span key={"d" + i} className="chip" title={m.main_pressure ?? ""}>
                  {m.name}{m.main_pressure ? ` · ${m.main_pressure}` : ""}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="row" style={{ margin: "12px 0" }}>
        <button className={tab === "board" ? "primary" : ""} onClick={() => setTab("board")}>结构图(段×线)</button>
        <button className={tab === "outline" ? "primary" : ""} onClick={() => setTab("outline")}>列表</button>
        <button className={tab === "timeline" ? "primary" : ""} onClick={() => setTab("timeline")}>时间线</button>
      </div>

      {tab === "board" && <Board beats={p.beats || []} lines={p.storylines || []} onBeat={(id) => s.jumpTo("beats", id)} />}
      {tab === "timeline" && tl && <Timeline tl={tl} sceneTitle={sceneTitle} onScene={(id) => s.jumpTo("scenes", id)} />}
      {tab === "outline" && (
        <Outline
          beats={outline}
          allBeats={p.beats}
          onBeat={(id) => s.jumpTo("beats", id)}
          onScene={(id) => s.jumpTo("scenes", id)}
          onMoveScene={async (sceneId, targetBeatId) => {
            const scene = (p.scenes || []).find((sc) => sc.id === sceneId);
            if (!scene) return;
            await s.updateEntity("scenes", sceneId, { ...scene, beat: targetBeatId });
            onChanged?.();
          }}
          onMoveBeat={async (id, parent) => {
            const beat = (p.beats || []).find((b) => b.id === id);
            if (!beat) return;
            await s.updateEntity("beats", id, { ...beat, parent });
            onChanged?.();
          }}
        />
      )}
    </div>
  );
}

// 认可状态徽标:作者=绿,拟=黄 —— 状态直接长在内容上(台账纪律)
function AuthBadge({ authority }: { authority?: string | null }) {
  if (!authority) return null;
  const isAuthor = authority === "author";
  return <span className={`auth ${isAuthor ? "author" : "ai"}`}>{isAuthor ? "作者" : "拟"}</span>;
}

// 结构图:段(顶层 beat,主链)为列 × 故事线为行 的散卡矩阵。
// 段卡当列头;子卡按 lane 落格;点卡进右侧检查器。横向滚动,不挤窄列。
function Board({ beats, lines, onBeat }: { beats: Beat[]; lines: StoryLine[]; onBeat: (id: string) => void }) {
  const stages = useMemo(
    () => beats.filter((b) => !b.parent).sort((a, b) => (a.position ?? 0) - (b.position ?? 0)),
    [beats],
  );
  const laneRows = useMemo(() => {
    const known = new Set(lines.map((l) => l.id));
    const hasUnlaned = beats.some((b) => b.parent && (!b.lane || !known.has(b.lane)));
    return [...lines, ...(hasUnlaned ? [{ id: "", title: "未分线", color: null } as StoryLine] : [])];
  }, [beats, lines]);

  if (stages.length === 0) return <p className="muted">暂无大纲段。</p>;

  const cellBeats = (stageId: string, laneId: string): Beat[] =>
    beats
      .filter((b) => b.parent === stageId && ((b.lane || "") === laneId || (laneId === "" && !b.lane)))
      .sort((a, b) => (a.position ?? 0) - (b.position ?? 0));

  const gridTemplate = `130px repeat(${stages.length}, minmax(240px, 300px))`;

  return (
    <div className="board-wrap">
      <div className="board" style={{ gridTemplateColumns: gridTemplate }}>
        {/* 列头:段卡(主链,列序即 edges 顺序) */}
        <div className="lane-label" style={{ borderLeftColor: "transparent" }}>段 →</div>
        {stages.map((st) => (
          <div key={st.id} className="beat-card stage" onClick={() => onBeat(st.id)}>
            <div className="t">
              <span className={`status-dot status-${st.status ?? "todo"}`} />
              <span style={{ flex: 1 }}>{st.title || st.id}</span>
              <AuthBadge authority={st.authority} />
            </div>
            {st.summary?.sentence && <div className="s">{st.summary.sentence}</div>}
            {st.function && <div className="f">{st.function}</div>}
          </div>
        ))}
        {/* 行:故事线 × 段 */}
        {laneRows.map((ln) => (
          <React.Fragment key={ln.id || "_none"}>
            <div className="lane-label" style={ln.color ? { borderLeftColor: ln.color } : undefined}>
              {ln.title || ln.id || "未分线"}
            </div>
            {stages.map((st) => (
              <div key={st.id + (ln.id || "_none")} className="cell">
                {cellBeats(st.id, ln.id).map((b) => (
                  <div key={b.id} className="beat-card" onClick={() => onBeat(b.id)}>
                    <div className="t">
                      <span className={`status-dot status-${b.status ?? "todo"}`} />
                      <span style={{ flex: 1 }}>{b.title || b.id}</span>
                      <AuthBadge authority={b.authority} />
                    </div>
                    {b.summary?.sentence && <div className="s">{b.summary.sentence}</div>}
                  </div>
                ))}
              </div>
            ))}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// beat 缩进深度:沿 parent 链计数
function beatDepth(b: { id: string; parent?: string | null }, byId: Record<string, { parent?: string | null }>): number {
  let d = 0; let cur = b.parent; const seen = new Set<string>();
  while (cur && byId[cur] && !seen.has(cur)) { seen.add(cur); d++; cur = byId[cur].parent ?? null; }
  return d;
}

function Timeline({ tl, sceneTitle, onScene }: {
  tl: TimelineData; sceneTitle: Record<string, string>; onScene: (id: string) => void;
}) {
  const byId = useMemo(() => {
    const m: Record<string, TLBeat> = {};
    for (const b of tl.beats) m[b.id] = b;
    return m;
  }, [tl.beats]);

  // 列:故事线 + 一个"无故事线"列(line_id="")
  const hasUnlined = Object.values(tl.cells).some((row) => "" in row);
  const lineCols = [...tl.lines, ...(hasUnlined ? [{ id: "", title: "无故事线", color: null }] : [])];

  const cols = lineCols.length;
  const gridTemplate = `180px repeat(${cols}, minmax(120px, 1fr))`;

  if (tl.beats.length === 0) return <p className="muted">暂无 beat。</p>;

  return (
    <div>
      <div className="timeline" style={{ gridTemplateColumns: gridTemplate }}>
        <div className="tl-head">Beat ＼ 故事线</div>
        {lineCols.map((l) => (
          <div key={l.id || "_none"} className="tl-head" style={l.color ? { borderTop: `3px solid ${l.color}` } : undefined}>
            {l.title || l.id || "—"}
          </div>
        ))}

        {tl.beats.map((b) => {
          const depth = beatDepth(b, byId);
          const row = tl.cells[b.id] || {};
          return (
            <React.Fragment key={b.id}>
              <div className="tl-cell" style={{ paddingLeft: 6 + depth * 14 }}>
                <b className="small">{b.title || b.id}</b>
              </div>
              {lineCols.map((l) => {
                const ids = row[l.id] || [];
                return (
                  <div key={(l.id || "_none") + b.id} className="tl-cell">
                    {ids.map((sid) => (
                      <div key={sid} className="tl-card" onClick={() => onScene(sid)}>
                        {sceneTitle[sid] || sid}
                      </div>
                    ))}
                  </div>
                );
              })}
            </React.Fragment>
          );
        })}
      </div>

      {tl.unplaced.length > 0 && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="muted small" style={{ marginBottom: 6 }}>未归位场景(无所属 beat)</div>
          <div className="wrap">
            {tl.unplaced.map((sid) => (
              <span key={sid} className="chip click" onClick={() => onScene(sid)}>{sceneTitle[sid] || sid}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Outline({ beats, allBeats, onBeat, onScene, onMoveScene, onMoveBeat }: {
  beats: OutlineBeat[];
  allBeats: Beat[];
  onBeat: (id: string) => void;
  onScene: (id: string) => void;
  onMoveScene: (sceneId: string, targetBeatId: string) => void;
  onMoveBeat: (id: string, parent: string | null) => void;
}) {
  if (beats.length === 0) return <p className="muted">暂无 beat。</p>;
  const beatLabel = (b: Beat): string => b.title || b.id;
  // 排版修复(2026-07-04 作者:"空白极多/莫名极窄列"):卡全宽;function 长文本不再塞进
  // 不换行的 chip 去挤标题,改为独立成行、正常换行;正文句用正文字号。
  return (
    <div>
      {beats.map((b) => (
        <div key={b.id} style={{ marginLeft: b.depth * 18 }}>
          <div className="card clickable" style={{ marginBottom: 4 }} onClick={() => onBeat(b.id)}>
            <div className="row">
              {b.status && <span className={`status-dot status-${b.status}`} />}
              <b style={{ flex: 1 }}>{b.title || b.id}</b>
              <AuthBadge authority={b.authority} />
              {/* beat 移动到父…(选另一 beat 作 parent 或顶层) */}
              <select
                className="small"
                value={b.parent ?? ""}
                title="移动到父…"
                style={{ width: "auto", maxWidth: 180, flex: "0 0 auto" }}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => onMoveBeat(b.id, e.target.value === "" ? null : e.target.value)}
              >
                <option value="">顶层</option>
                {allBeats.filter((tb) => tb.id !== b.id).map((tb) => (
                  <option key={tb.id} value={tb.id}>父：{beatLabel(tb)}</option>
                ))}
              </select>
            </div>
            {b.summary?.sentence && <div style={{ marginTop: 6, lineHeight: 1.55 }}>{b.summary.sentence}</div>}
            {b.function && <div className="small muted" style={{ marginTop: 4, lineHeight: 1.5 }}>{b.function}</div>}
          </div>
          {b.scenes.length > 0 && (
            <div className="wrap" style={{ marginLeft: 18, marginBottom: 6 }}>
              {b.scenes.map((sc) => (
                <span key={sc.scene_id} className="chip click" style={{ alignItems: "center" }}>
                  <span className={`status-dot status-${sc.status ?? "todo"}`} style={{ marginRight: 5 }} />
                  <span onClick={() => onScene(sc.scene_id)}>{sc.title || sc.scene_id}</span>
                  {/* scene 移动到…(选目标 beat) */}
                  <select
                    className="small"
                    value={b.id}
                    title="移动到…"
                    style={{ marginLeft: 6 }}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onMoveScene(sc.scene_id, e.target.value)}
                  >
                    {allBeats.map((tb) => (
                      <option key={tb.id} value={tb.id}>{beatLabel(tb)}</option>
                    ))}
                  </select>
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
