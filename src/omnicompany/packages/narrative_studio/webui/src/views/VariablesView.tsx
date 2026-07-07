// 数值 / 状态视图:变量(按 namespace 分组)+ 可复用属性组 + 元进度 + 压力卡 + 失败分层。
// 每个变量可"引用反查"展开读写来源,来源里的 scene/node 可 jumpTo。
import React, { useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

// variableRefs.where 形如 "connection:e2" / "scene:s1" / "node:n1" / "ending:..." / "reveal:..."
// 映射成可跳转的载体 + id。
const WHERE_CARRIER: Record<string, string> = {
  connection: "connections",
  scene: "scenes",
  node: "nodes",
  ending: "endings",
  reveal: "reveal_layers",
};

function RefList({ refs, onJump }: { refs: any[]; onJump: (carrier: string, id: string) => void }) {
  if (!refs || refs.length === 0) return <span className="muted small">（无）</span>;
  return (
    <div className="wrap">
      {refs.map((r: any, i: number) => {
        const where: string = r.where ?? "";
        const kind: string = r.kind ?? where.split(":", 1)[0];
        const id = where.includes(":") ? where.slice(where.indexOf(":") + 1) : where;
        const carrier = WHERE_CARRIER[kind];
        return carrier ? (
          <span key={i} className="chip click" onClick={() => onJump(carrier, id)}>{where}</span>
        ) : (
          <span key={i} className="chip">{where}</span>
        );
      })}
    </div>
  );
}

export function VariablesView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  // varKey -> {reads,writes} | "loading"
  const [refs, setRefs] = useState<Record<string, any>>({});

  const toggleRefs = (key: string) => {
    if (refs[key] !== undefined) {
      setRefs((r) => {
        const n = { ...r };
        delete n[key];
        return n;
      });
      return;
    }
    setRefs((r) => ({ ...r, [key]: "loading" }));
    api.variableRefs(key).then((d) => setRefs((r) => ({ ...r, [key]: d }))).catch(() => {
      setRefs((r) => ({ ...r, [key]: { reads: [], writes: [] } }));
    });
  };

  // 按 namespace 分组
  const groups: Record<string, typeof p.variables> = {};
  for (const v of p.variables) {
    (groups[v.namespace] ?? (groups[v.namespace] = [])).push(v);
  }
  const namespaces = Object.keys(groups).sort();

  const varRow = (v: (typeof p.variables)[number]) => {
    const key = v.namespace + "." + v.name;
    const expanded = refs[key] !== undefined;
    return (
      <div className="card" key={key}>
        <div className="row">
          <a onClick={() => s.select("variables", key)}><b>{v.name}</b></a>
          <span className="chip small">{v.type}</span>
          {v.counter && <span className="chip small">counter</span>}
          <span className="muted small">默认 {String(v.default ?? "—")}</span>
          <span className="grow" style={{ flex: 1 }} />
          <button className="small" onClick={() => toggleRefs(key)}>引用反查</button>
        </div>
        {v.description && <div className="muted small" style={{ marginTop: 4 }}>{v.description}</div>}
        {expanded && (
          <div style={{ marginTop: 8 }}>
            {refs[key] === "loading" ? (
              <span className="muted small">加载中…</span>
            ) : (
              <div className="grid">
                <div>
                  <div className="muted small">reads（读）</div>
                  <RefList refs={refs[key].reads} onJump={s.jumpTo} />
                </div>
                <div>
                  <div className="muted small">writes（写）</div>
                  <RefList refs={refs[key].writes} onJump={s.jumpTo} />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div>
      <h2 className="view-h">数值 / 状态</h2>
      <p className="view-sub">变量、可复用属性组、元进度、压力与失败分层——叙事的状态机底盘。</p>

      {namespaces.length === 0 && <p className="muted">还没有变量。</p>}
      {namespaces.map((ns) => (
        <section key={ns} style={{ marginBottom: 18 }}>
          <div className="row" style={{ marginBottom: 6 }}>
            <b>{ns}</b>
            <span className="muted small">命名空间 · {groups[ns].length} 个变量</span>
          </div>
          {groups[ns].map(varRow)}
        </section>
      ))}

      {/* 元进度(局间留存变量):meta 字段不在 variables 列表里,整体走 meta_progress singleton 编辑 */}
      {p.meta_progress.fields.length > 0 && (
        <section style={{ marginBottom: 18 }}>
          <div className="row" style={{ marginBottom: 6 }}>
            <b>元进度</b>
            <span className="muted small">局间留存 · {p.meta_progress.fields.length} 项</span>
          </div>
          {p.meta_progress.fields.map((v) => (
            <div className="card" key={v.namespace + "." + v.name}>
              <div className="row">
                <a onClick={() => s.select("meta_progress", "_")}><b>{v.name}</b></a>
                <span className="chip small">{v.type}</span>
                {v.counter && <span className="chip small">counter</span>}
                <span className="muted small">默认 {String(v.default ?? "—")}</span>
              </div>
              {v.description && <div className="muted small" style={{ marginTop: 4 }}>{v.description}</div>}
            </div>
          ))}
        </section>
      )}

      {/* 可复用属性组 */}
      <section style={{ marginBottom: 18 }}>
        <div className="row" style={{ marginBottom: 6 }}>
          <b>属性组（stat blocks）</b>
          <span className="muted small">可复用字段组 · {p.stat_blocks.length} 组</span>
        </div>
        {p.stat_blocks.length === 0 && <p className="muted small">无</p>}
        {p.stat_blocks.map((sb) => (
          <div className="card clickable" key={sb.name} onClick={() => s.select("stat_blocks", sb.name)}>
            <b>{sb.name}</b>
            <div className="wrap" style={{ marginTop: 6 }}>
              {sb.fields.map((f, i) => <span key={i} className="chip small">{f}</span>)}
            </div>
            {sb.applies_to.length > 0 && (
              <div className="muted small" style={{ marginTop: 6 }}>适用于：{sb.applies_to.join("、")}</div>
            )}
          </div>
        ))}
      </section>

      {/* 压力卡 */}
      <section style={{ marginBottom: 18 }}>
        <div className="row" style={{ marginBottom: 6 }}>
          <b>压力（pressures）</b>
          <span className="muted small">累积型张力 · {p.pressures.length} 张</span>
        </div>
        {p.pressures.length === 0 && <p className="muted small">无</p>}
        {p.pressures.map((pr) => (
          <div className="card clickable" key={pr.id} onClick={() => s.select("pressures", pr.id)}>
            <b>{pr.name}</b>
            {pr.manifest && <div className="small" style={{ marginTop: 4 }}>表现：{pr.manifest}</div>}
          </div>
        ))}
      </section>

      {/* 失败分层 */}
      <section style={{ marginBottom: 18 }}>
        <div className="row" style={{ marginBottom: 6 }}>
          <b>失败分层（failure levels）</b>
          <span className="muted small">{p.failure_levels.length} 层</span>
        </div>
        {p.failure_levels.length === 0 && <p className="muted small">无</p>}
        {p.failure_levels.map((fl) => (
          <div className="card clickable" key={fl.level} onClick={() => s.select("failure_levels", fl.level)}>
            <b>{fl.level}</b>
            {fl.manifest && <div className="small" style={{ marginTop: 4 }}>表现：{fl.manifest}</div>}
            {fl.prereq_chain.length > 0 && (
              <div className="wrap" style={{ marginTop: 6 }}>
                <span className="muted small">前置链：</span>
                {fl.prereq_chain.map((c, i) => <span key={i} className="chip small">{c}</span>)}
              </div>
            )}
            {fl.warning && <div className="muted small" style={{ marginTop: 4 }}>预警：{fl.warning}</div>}
          </div>
        ))}
      </section>
    </div>
  );
}
