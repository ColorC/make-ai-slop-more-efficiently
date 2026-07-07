// 健康检查:按 severity 分组列出问题;尽力从 location/ref 解析现场跳转。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";
import type { HealthIssue } from "../types";

type Sev = "high" | "medium" | "low";
const SEV_ORDER: Sev[] = ["high", "medium", "low"];
const SEV_LABEL: Record<Sev, string> = { high: "高", medium: "中", low: "低" };

// location 前缀(health.py 口径)→ store 载体名。
const PREFIX_CARRIER: Record<string, string> = {
  scene: "scenes",
  connection: "connections",
  node: "nodes",
  ending: "endings",
  variable: "variables",
  prose_line: "prose_lines",
  tag: "tags",
  reveal_layer: "reveal_layers",
  character: "characters",
  premise: "premise",
};

// 从形如 "scene:s1" 或 "ending:n3|ending:n4" 的 location 解析出第一个可跳转目标。
function parseTarget(loc?: string): { carrier: string; id: string } | null {
  if (!loc) return null;
  const first = loc.split("|")[0].trim();
  const idx = first.indexOf(":");
  if (idx < 0) {
    // 无冒号(如 "premise")
    const carrier = PREFIX_CARRIER[first];
    return carrier ? { carrier, id: "premise" } : null;
  }
  const prefix = first.slice(0, idx);
  const id = first.slice(idx + 1);
  const carrier = PREFIX_CARRIER[prefix];
  if (!carrier) return null;
  return { carrier, id };
}

export function HealthView({ onChanged: _onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const [issues, setIssues] = useState<HealthIssue[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setLoaded(false);
    api.health()
      .then((d: any) => { setIssues(Array.isArray(d) ? (d as HealthIssue[]) : []); setLoaded(true); })
      .catch(() => setLoaded(true));
  }, [s.project]);
  void p; // p 用于触发依赖即可,本视图数据来自投影

  const counts: Record<Sev, number> = { high: 0, medium: 0, low: 0 };
  for (const it of issues) {
    if (it.severity in counts) counts[it.severity as Sev] += 1;
  }

  const grouped: Record<Sev, HealthIssue[]> = { high: [], medium: [], low: [] };
  for (const it of issues) {
    if (it.severity in grouped) grouped[it.severity as Sev].push(it);
  }

  return (
    <div>
      <h2 className="view-h">健康检查</h2>
      <p className="view-sub">对单一真源做静态体检,按严重度列出问题并尽力跳到现场。</p>

      <div className="row wrap" style={{ marginBottom: 14 }}>
        <span className="badge err">高 {counts.high}</span>
        <span className="badge warn">中 {counts.medium}</span>
        <span className="badge">低 {counts.low}</span>
        <span className="muted small">共 {issues.length} 条</span>
      </div>

      {loaded && issues.length === 0 && (
        <div className="card"><span className="muted">没有发现问题,一切健康。</span></div>
      )}

      {SEV_ORDER.map((sev) => {
        const list = grouped[sev];
        if (list.length === 0) return null;
        return (
          <div key={sev} style={{ marginBottom: 12 }}>
            <div className={`row sev-${sev}`} style={{ marginBottom: 6 }}>
              <b>{SEV_LABEL[sev]} 严重度</b>
              <span className="muted small">({list.length})</span>
            </div>
            {list.map((it, i) => {
              const target = parseTarget(it.location);
              return (
                <div key={`${it.code}-${i}`} className="card">
                  <div className="row">
                    <span className={`sev-${sev}`} style={{ fontWeight: 600 }}>●</span>
                    <span>{it.message}</span>
                  </div>
                  <div className="row wrap small muted" style={{ marginTop: 6 }}>
                    <span className="chip">{it.code}</span>
                    {it.location && (
                      target
                        ? <a className="chip click" onClick={() => s.jumpTo(target.carrier, target.id)}>→ {it.location}</a>
                        : <span className="chip">{it.location}</span>
                    )}
                    {it.ref && <span>依据:{it.ref}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
