// 揭示层视图:按 surface→midpoint→true_end 排列,展示触发条件 / 改写 / 状态。
import React from "react";
import { useStudio } from "../store";
import type { Expr, RevealOrder } from "../types";

const ORDER_RANK: Record<RevealOrder, number> = { surface: 0, midpoint: 1, true_end: 2 };
const ORDER_LABEL: Record<RevealOrder, string> = { surface: "表层", midpoint: "中点", true_end: "真结局" };

// 把比较运算符渲染成可读符号
const OP_SYM: Record<string, string> = {
  gte: "≥", ">=": "≥", lte: "≤", "<=": "≤",
  gt: ">", lt: "<", eq: "=", "==": "=", ne: "≠", "!=": "≠",
};

function exprText(e: Expr): string {
  const sym = OP_SYM[e.op] ?? e.op;
  const val = e.value === undefined || e.value === null ? "" : String(e.value);
  return `${e.var} ${sym} ${val}`.trim();
}

export function RevealView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const layers = [...(p.reveal_layers || [])].sort(
    (a, b) => (ORDER_RANK[a.order] ?? 9) - (ORDER_RANK[b.order] ?? 9),
  );

  const add = async () => {
    await s.createEntity("reveal_layers", {
      id: "rl-" + Date.now(),
      order: "midpoint",
      title: "新揭示层",
      trigger: [],
      status: "todo",
    });
    onChanged?.();
  };

  return (
    <div>
      <h2 className="view-h">揭示层</h2>
      <p className="view-sub">叙事真相如何分层显形 —— 从表层一步步揭到真结局。</p>

      <div className="row" style={{ marginBottom: 12 }}>
        <button className="primary" onClick={add}>新增揭示层</button>
      </div>

      {layers.length === 0 && <p className="muted">暂无揭示层。</p>}

      <div className="grid">
        {layers.map((rl) => (
          <div key={rl.id} className="card clickable" onClick={() => s.select("reveal_layers", rl.id)}>
            <div className="row">
              <span className={`status-dot status-${rl.status}`} />
              <b style={{ flex: 1 }}>{rl.title || rl.id}</b>
              <span className="chip">{ORDER_LABEL[rl.order] ?? rl.order}</span>
            </div>

            <div className="small" style={{ marginTop: 8 }}>
              <div className="muted">触发条件</div>
              {(rl.trigger || []).length === 0
                ? <span className="muted">(无条件)</span>
                : (
                  <div className="wrap" style={{ marginTop: 3 }}>
                    {rl.trigger.map((e, i) => <span key={i} className="chip">{exprText(e)}</span>)}
                  </div>
                )}
            </div>

            {rl.rewrites && (
              <div className="small" style={{ marginTop: 8 }}>
                <div className="muted">改写</div>
                <div>{rl.rewrites}</div>
              </div>
            )}
            {rl.rewrites_controlling_idea && (
              <div className="small muted" style={{ marginTop: 4 }}>
                重写控制理念:{rl.rewrites_controlling_idea}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
