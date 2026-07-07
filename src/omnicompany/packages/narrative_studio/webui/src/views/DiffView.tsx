// 版本对照:选两个版本(或当前工作态)看逐载体增删差异。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

export function DiffView(_: { onChanged?: () => void }) {
  const s = useStudio();
  const [versions, setVersions] = useState<string[]>([]);
  const [a, setA] = useState("_working");
  const [b, setB] = useState("_working");
  const [diff, setDiff] = useState<any>(null);

  useEffect(() => { api.versions().then(setVersions).catch(() => {}); }, []);
  useEffect(() => { api.diff(a, b).then(setDiff).catch(() => setDiff(null)); }, [a, b, s.project]);

  const opts = ["_working", ...versions];
  const label = (v: string) => (v === "_working" ? "当前工作态" : v);
  const carriers = diff?.carriers ?? {};

  return (
    <div>
      <h2 className="view-h">版本对照</h2>
      <p className="view-sub">旧版 / 新版差在哪 —— 逐载体的新增 / 删除。</p>
      <div className="row" style={{ gap: 8, marginBottom: 12 }}>
        <select value={a} onChange={(e) => setA(e.target.value)} style={{ width: "auto" }}>
          {opts.map((v) => <option key={v} value={v}>A: {label(v)}</option>)}
        </select>
        <span className="muted">→</span>
        <select value={b} onChange={(e) => setB(e.target.value)} style={{ width: "auto" }}>
          {opts.map((v) => <option key={v} value={v}>B: {label(v)}</option>)}
        </select>
        {diff?.premise_changed && <span className="badge warn">立意有改动</span>}
      </div>

      {Object.keys(carriers).length === 0 && <p className="muted">两版一致(或同一版本)。</p>}
      <div className="grid">
        {Object.entries<any>(carriers).map(([carrier, d]) => (
          <div className="card" key={carrier}>
            <div className="row">
              <b>{carrier}</b>
              <span className="grow" style={{ flex: 1 }} />
              <span className="small muted">{d.a_count} → {d.b_count}</span>
            </div>
            {d.added?.length > 0 && <div className="small" style={{ color: "var(--done)" }}>+ 新增 {d.added.length}:{d.added.slice(0, 10).join(", ")}</div>}
            {d.removed?.length > 0 && <div className="small" style={{ color: "var(--err)" }}>− 删除 {d.removed.length}:{d.removed.slice(0, 10).join(", ")}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
