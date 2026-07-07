// 情节推理视图:把每场的因果(why_now/why_inevitable)与价值转向(from→to)
// 串成按场景顺序的"推理链"。无认可场景时给空状态引导。
import React from "react";
import { useStudio } from "../store";

function statusClass(st: string): string {
  return st === "done" ? "status-done" : st === "tocomplete" ? "status-tocomplete" : "status-todo";
}

export function ReasoningView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const scenes = p.scenes ?? [];

  if (scenes.length === 0) {
    return (
      <div>
        <h2 className="view-h">情节推理</h2>
        <p className="view-sub">沿场景顺序审视每一拍的因果与价值转向 —— 为何此刻发生、为何必然，价值从何处转到何处。</p>
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>
            情节与推理尚无认可版本，可在「情节」层新建场景后于此审因果 / 价值转变。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 className="view-h">情节推理</h2>
      <p className="view-sub">沿场景顺序审视每一拍的因果与价值转向 —— 为何此刻发生、为何必然，价值从何处转到何处。</p>

      <div className="grid">
        {scenes.map((sc, i) => {
          const c = sc.causality ?? {};
          const vs = sc.value_shift ?? {};
          const hasCausality = !!(c.why_now || c.why_inevitable);
          const hasShift = !!(vs.from || vs.to);
          return (
            <div key={sc.id} className="card clickable" onClick={() => { s.jumpTo("plot", sc.id); onChanged?.(); }}>
              <div className="row">
                <span className="muted small" style={{ minWidth: 24 }}>#{i + 1}</span>
                <span className={"status-dot " + statusClass(sc.status)} />
                <b style={{ flex: 1 }}>{sc.title || sc.id}</b>
              </div>

              <div className="small" style={{ marginTop: 8 }}>
                <div className="muted">因果链</div>
                {hasCausality ? (
                  <div style={{ marginTop: 3 }}>
                    {c.why_now && <div><span className="muted">为何此刻：</span>{c.why_now}</div>}
                    {c.why_inevitable && <div><span className="muted">为何必然：</span>{c.why_inevitable}</div>}
                  </div>
                ) : (
                  <span className="sev-high">未填因果</span>
                )}
              </div>

              <div className="small" style={{ marginTop: 8 }}>
                <div className="muted">价值转向</div>
                {hasShift
                  ? <div style={{ marginTop: 3 }}>{vs.from ?? "—"} → {vs.to ?? "—"}</div>
                  : <span className="sev-high">未转</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
