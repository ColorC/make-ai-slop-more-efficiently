// 分布 / 对照:角色×beat 出场矩阵 + POV 配比 + 故事线供给 + 各幕长度。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

interface DistData {
  character_matrix: Record<string, Record<string, number>>;
  pov_ratio: Record<string, number>;
  line_supply: Record<string, number>;
  act_lengths: Record<string, number>;
}

export function DistributionView({ onChanged: _onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const [data, setData] = useState<DistData | null>(null);

  useEffect(() => {
    api.distribution().then((d: any) => setData(d as DistData)).catch(() => {});
  }, [s.project]);

  const charName = (id: string) => p.characters.find((c) => c.id === id)?.name ?? id;
  const beatName = (id: string) => {
    const b = p.beats.find((x) => x.id === id);
    return b ? (b.title || b.id) : id;
  };
  const lineName = (id: string) => {
    const sl = p.storylines.find((x) => x.id === id);
    return sl ? (sl.title || sl.id) : id;
  };

  if (!data) return (
    <div>
      <h2 className="view-h">分布 / 对照</h2>
      <p className="view-sub">从单一真源算出的出场、视角、供给与篇幅分布。</p>
      <p className="muted">加载中…</p>
    </div>
  );

  // ① 矩阵:行=出现在矩阵里的角色,列=出现在任意角色计数里的 beat(按 beats 声明序)
  const charIds = Object.keys(data.character_matrix);
  const beatIdSet = new Set<string>();
  for (const row of Object.values(data.character_matrix)) {
    for (const bid of Object.keys(row)) beatIdSet.add(bid);
  }
  const beatIds = p.beats.map((b) => b.id).filter((id) => beatIdSet.has(id));
  // 防止矩阵里有不在 beats 表里的 id
  for (const bid of beatIdSet) if (!beatIds.includes(bid)) beatIds.push(bid);

  // ② POV:按占比降序
  const povRows = Object.entries(data.pov_ratio).sort((a, b) => b[1] - a[1]);

  // ③ 故事线供给:按数量降序
  const lineRows = Object.entries(data.line_supply).sort((a, b) => b[1] - a[1]);
  const lineMax = lineRows.reduce((m, [, n]) => Math.max(m, n), 0) || 1;

  // ④ 各幕长度:按 beats 声明序排列(顶层),否则原序
  const actRows = Object.entries(data.act_lengths).sort(
    (a, b) => p.beats.findIndex((x) => x.id === a[0]) - p.beats.findIndex((x) => x.id === b[0]),
  );
  const actMax = actRows.reduce((m, [, n]) => Math.max(m, n), 0) || 1;

  return (
    <div>
      <h2 className="view-h">分布 / 对照</h2>
      <p className="view-sub">从单一真源算出的出场、视角、供给与篇幅分布。</p>

      {/* ① 角色×beat 出场矩阵 */}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}><b>角色 × 节拍 出场矩阵</b></div>
        {charIds.length === 0 ? (
          <p className="muted small">尚无角色出场记录(场景 links.characters 为空)。</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="matrix">
              <thead>
                <tr>
                  <th>角色 \ 节拍</th>
                  {beatIds.map((bid) => (
                    <th key={bid}>
                      <a onClick={() => s.jumpTo("beats", bid)}>{beatName(bid)}</a>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {charIds.map((cid) => (
                  <tr key={cid}>
                    <td style={{ textAlign: "left" }}>
                      <a onClick={() => s.jumpTo("characters", cid)}>{charName(cid)}</a>
                    </td>
                    {beatIds.map((bid) => {
                      const n = data.character_matrix[cid]?.[bid] ?? 0;
                      return <td key={bid} className={n ? "" : "muted"}>{n || ""}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ② POV 配比 */}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}><b>POV 视角配比</b></div>
        {povRows.length === 0 ? (
          <p className="muted small">尚无场景设定 POV(links.pov 为空)。</p>
        ) : (
          <div className="grid">
            {povRows.map(([cid, ratio]) => (
              <div key={cid} className="row">
                <a style={{ minWidth: 120 }} onClick={() => s.jumpTo("characters", cid)}>{charName(cid)}</a>
                <div style={{ flex: 1, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 6, height: 14, overflow: "hidden" }}>
                  <div style={{ width: `${Math.round(ratio * 100)}%`, height: "100%", background: "var(--accent)" }} />
                </div>
                <span className="small muted" style={{ minWidth: 44, textAlign: "right" }}>{Math.round(ratio * 100)}%</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ③ 故事线供给 */}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}><b>故事线供给(关联场景数)</b></div>
        {lineRows.length === 0 ? (
          <p className="muted small">尚无场景挂故事线(links.lines 为空)。</p>
        ) : (
          <div className="grid">
            {lineRows.map(([lid, n]) => (
              <div key={lid} className="row">
                <span style={{ minWidth: 120 }}>{lineName(lid)}</span>
                <div style={{ flex: 1, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 6, height: 14, overflow: "hidden" }}>
                  <div style={{ width: `${Math.round((n / lineMax) * 100)}%`, height: "100%", background: "var(--accent2)" }} />
                </div>
                <span className="small muted" style={{ minWidth: 44, textAlign: "right" }}>{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ④ 各幕长度 */}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}><b>各幕长度(顶层节拍场景数)</b></div>
        {actRows.length === 0 ? (
          <p className="muted small">尚无场景归入节拍。</p>
        ) : (
          <div className="grid">
            {actRows.map(([bid, n]) => (
              <div key={bid} className="row">
                <a style={{ minWidth: 120 }} onClick={() => s.jumpTo("beats", bid)}>{beatName(bid)}</a>
                <div style={{ flex: 1, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 6, height: 14, overflow: "hidden" }}>
                  <div style={{ width: `${Math.round((n / actMax) * 100)}%`, height: "100%", background: "var(--ok)" }} />
                </div>
                <span className="small muted" style={{ minWidth: 44, textAlign: "right" }}>{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
