// 完成度仪表盘:总体进度环 + 各载体三态条 + 空字段下钻补全。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";
import type { Completeness, EmptyItem } from "../types";

// empties 的 entity_kind → 载体名(jumpTo 用)
const KIND_TO_CARRIER: Record<string, string> = {
  scene: "scenes",
  character: "characters",
  prose_line: "prose_lines",
  reveal_layer: "reveal_layers",
  premise: "premise",
};

// 载体名 → 中文标签
const CARRIER_LABEL: Record<string, string> = {
  characters: "角色",
  scenes: "场景",
  beats: "节拍",
  prose_lines: "成文行",
  reveal_layers: "揭示层",
  dossier: "角色档案",
};

function ProgressRing({ percent }: { percent: number }) {
  const r = 46;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.max(0, Math.min(100, percent)) / 100);
  return (
    <svg width={120} height={120} viewBox="0 0 120 120">
      <circle cx={60} cy={60} r={r} fill="none" stroke="var(--border)" strokeWidth={12} />
      <circle
        cx={60} cy={60} r={r} fill="none" stroke="var(--done)" strokeWidth={12}
        strokeDasharray={c} strokeDashoffset={off} strokeLinecap="round"
        transform="rotate(-90 60 60)"
      />
      <text x={60} y={58} textAnchor="middle" fill="var(--fg)" fontSize={22} fontWeight={600}>
        {percent.toFixed(1)}%
      </text>
      <text x={60} y={76} textAnchor="middle" fill="var(--muted)" fontSize={11}>已完成</text>
    </svg>
  );
}

function StatusBar({ todo, tocomplete, done, total }: { todo: number; tocomplete: number; done: number; total: number }) {
  const t = total || 1;
  const seg = (n: number, varName: string) =>
    n > 0 ? <div style={{ width: `${(n / t) * 100}%`, background: `var(${varName})` }} /> : null;
  return (
    <div className="row" style={{ height: 10, borderRadius: 5, overflow: "hidden", background: "var(--bg)", gap: 0 }}>
      {seg(done, "--done")}
      {seg(tocomplete, "--tocomplete")}
      {seg(todo, "--todo")}
    </div>
  );
}

export function CompletenessView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const [data, setData] = useState<Completeness | null>(null);
  const [empties, setEmpties] = useState<EmptyItem[]>([]);

  useEffect(() => {
    api.completeness().then(setData).catch(() => {});
    api.empties().then(setEmpties).catch(() => {});
  }, [s.project]);

  const overall = data?.overall;
  const carriers = data ? Object.entries(data.by_carrier) : [];

  return (
    <div>
      <h2 className="view-h">完成度</h2>
      <p className="view-sub">把整部作品的三态进度拉直看:总体、各载体、以及还空着的关键字段。</p>

      <div className="card">
        <div className="row" style={{ gap: 20, alignItems: "center" }}>
          {overall ? <ProgressRing percent={overall.percent_done} /> : <span className="muted">加载中…</span>}
          {overall && (
            <div className="grid" style={{ gridTemplateColumns: "auto auto", columnGap: 24, rowGap: 4 }}>
              <span className="muted">总条目</span><b>{overall.total}</b>
              <span className="row"><span className="status-dot status-done" /> 完成</span><b>{overall.done}</b>
              <span className="row"><span className="status-dot status-tocomplete" /> 待完善</span><b>{overall.tocomplete}</b>
              <span className="row"><span className="status-dot status-todo" /> 待做</span><b>{overall.todo}</b>
            </div>
          )}
        </div>
      </div>

      <h3 className="view-h" style={{ fontSize: 14, marginTop: 16 }}>各载体</h3>
      {carriers.length === 0 && <p className="muted small">暂无统计</p>}
      {carriers.map(([carrier, b]) => (
        <div className="card" key={carrier}>
          <div className="row" style={{ marginBottom: 6 }}>
            <b>{CARRIER_LABEL[carrier] ?? carrier}</b>
            <span className="grow" style={{ flex: 1 }} />
            <span className="muted small">
              {b.done}/{b.total} 完成{b.empty > 0 ? ` · ${b.empty} 项缺字段` : ""}
            </span>
          </div>
          <StatusBar todo={b.todo} tocomplete={b.tocomplete} done={b.done} total={b.total} />
        </div>
      ))}

      <h3 className="view-h" style={{ fontSize: 14, marginTop: 16 }}>空字段待补全</h3>
      {empties.length === 0 && <p className="muted small">没有缺失的关键字段,真不错。</p>}
      {empties.map((it, i) => {
        const carrier = KIND_TO_CARRIER[it.entity_kind] ?? it.entity_kind;
        return (
          <div
            className="card clickable" key={`${it.entity_kind}:${it.entity_id}:${i}`}
            onClick={() => s.jumpTo(carrier, it.entity_id)}
          >
            <div className="row">
              <b>{it.title || it.entity_id}</b>
              <span className="chip small">{CARRIER_LABEL[carrier] ?? it.entity_kind}</span>
            </div>
            <div className="wrap" style={{ marginTop: 6 }}>
              {it.missing_fields.map((f) => (
                <span className="chip small sev-medium" key={f}>缺 {f}</span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
