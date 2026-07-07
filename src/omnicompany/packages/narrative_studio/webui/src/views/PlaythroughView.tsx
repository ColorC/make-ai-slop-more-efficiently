// 演练:沿节点图跑一遍(纯结构求值),展示轨迹、日志、终态、结局与揭示。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";
import type { PlaythroughResult } from "../types";

const STOP_LABEL: Record<string, string> = {
  ending: "到达结局节点",
  ending_trigger: "满足结局触发条件",
  no_edges: "无可走边",
  no_start: "找不到起点",
  missing_node: "节点缺失",
  max_steps: "超过最大步数",
  loop: "检测到死循环",
};

// 把一条 effect/expr dict 转成可读字符串。
function exprText(e: any): string {
  if (e == null) return "";
  if (typeof e === "string") return e;
  if (typeof e === "object" && "var" in e) {
    const v = e.value;
    const val = v === undefined || v === null ? "" : ` ${v}`;
    return `${e.var} ${e.op ?? ""}${val}`.trim();
  }
  try { return JSON.stringify(e); } catch { return String(e); }
}

function effectsText(applied: unknown): string {
  if (Array.isArray(applied)) return applied.map(exprText).filter(Boolean).join(", ");
  if (applied == null) return "";
  return exprText(applied);
}

export function PlaythroughView({ onChanged: _onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const [result, setResult] = useState<PlaythroughResult | null>(null);
  const [running, setRunning] = useState(false);
  const [choicesText, setChoicesText] = useState("");
  const [startText, setStartText] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const nodeTitle = (id: string) => {
    const n = p.nodes.find((x) => x.id === id);
    return n ? (n.title || n.id) : id;
  };

  const run = (choices: string[], start: string | null) => {
    setRunning(true);
    setErr(null);
    api.playthrough(choices, start ?? undefined)
      .then((r) => setResult(r))
      .catch((e) => setErr(String(e?.message ?? e)))
      .finally(() => setRunning(false));
  };

  // 首次进入自动跑一遍默认路径。
  useEffect(() => { run([], null); /* eslint-disable-next-line */ }, [s.project]);

  const onRunFromTop = () => { setChoicesText(""); setStartText(""); run([], null); };
  const onRunWithChoices = () => {
    const choices = choicesText.split(",").map((t) => t.trim()).filter(Boolean);
    run(choices, startText.trim() || null);
  };

  return (
    <div>
      <h2 className="view-h">演练</h2>
      <p className="view-sub">纯结构求值:从起点沿连接走一遍,看走到哪、变量怎么变、停在何处。</p>

      <div className="card">
        <div className="row wrap" style={{ marginBottom: 8 }}>
          <button className="primary" disabled={running} onClick={onRunFromTop}>从头跑</button>
          <button disabled={running} onClick={onRunWithChoices}>按选择重跑</button>
          {running && <span className="muted small">运行中…</span>}
        </div>
        <div className="row wrap">
          <div className="field" style={{ flex: 2, minWidth: 220, marginBottom: 0 }}>
            <label>选择序列(逗号分隔的 connection id 或 target,遇分叉按序消费)</label>
            <input value={choicesText} placeholder="例:e2, n5, e9"
              onChange={(e) => setChoicesText(e.target.value)} />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 140, marginBottom: 0 }}>
            <label>起点节点(留空=自动)</label>
            <input value={startText} placeholder="node id"
              onChange={(e) => setStartText(e.target.value)} />
          </div>
        </div>
      </div>

      {err && <div className="card"><span className="sev-high small">{err}</span></div>}

      {result && (
        <>
          {/* 停止原因 + 结局 */}
          <div className="card">
            <div className="row wrap">
              <span className="muted">停止原因:</span>
              <span className="chip">{STOP_LABEL[result.stopped_reason ?? ""] ?? result.stopped_reason ?? "未知"}</span>
              {result.ending && (
                <>
                  <span className="muted">结局:</span>
                  <a className="chip click" onClick={() => s.jumpTo("nodes", result.ending!.node_id)}>
                    {result.ending.name}（{result.ending.node_id}）
                  </a>
                </>
              )}
            </div>
          </div>

          {/* 访问序 */}
          <div className="card">
            <div className="row" style={{ marginBottom: 8 }}><b>访问节点序</b><span className="muted small">（{result.visited.length} 步）</span></div>
            {result.visited.length === 0 ? (
              <span className="muted small">未访问任何节点。</span>
            ) : (
              <div className="row wrap">
                {result.visited.map((id, i) => (
                  <React.Fragment key={`${id}-${i}`}>
                    <a className="chip click" onClick={() => s.jumpTo("nodes", id)}>{nodeTitle(id)}</a>
                    {i < result.visited.length - 1 && <span className="muted small">→</span>}
                  </React.Fragment>
                ))}
              </div>
            )}
          </div>

          {/* 逐步日志 */}
          <div className="card">
            <div className="row" style={{ marginBottom: 8 }}><b>逐步日志</b></div>
            {result.log.length === 0 ? (
              <span className="muted small">无日志。</span>
            ) : (
              <div className="grid">
                {result.log.map((step, i) => {
                  const eff = effectsText(step.applied_effects);
                  const edge = step.chosen_edge;
                  return (
                    <div key={`${step.node_id}-${i}`} className="card" style={{ marginBottom: 0 }}>
                      <div className="row wrap">
                        <span className="small muted" style={{ minWidth: 28 }}>#{i + 1}</span>
                        <a className="chip click" onClick={() => s.jumpTo("nodes", step.node_id)}>
                          {step.title || step.node_id}
                        </a>
                        {edge && (
                          <span className="small muted">
                            选边 <span className="chip">{(edge as any).label || (edge as any).edge_id}</span>
                            {" → "}
                            <a onClick={() => s.jumpTo("nodes", (edge as any).target)}>{nodeTitle((edge as any).target)}</a>
                          </span>
                        )}
                      </div>
                      {eff && <div className="small muted" style={{ marginTop: 4 }}>效果:{eff}</div>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* 揭示触发 */}
          <div className="card">
            <div className="row" style={{ marginBottom: 8 }}><b>触发的揭示层</b></div>
            {result.reveals_triggered.length === 0 ? (
              <span className="muted small">本次未触发任何揭示层。</span>
            ) : (
              <div className="row wrap">
                {result.reveals_triggered.map((rid) => (
                  <a key={rid} className="chip click" onClick={() => s.jumpTo("reveal_layers", rid)}>{rid}</a>
                ))}
              </div>
            )}
          </div>

          {/* 终态变量 */}
          <div className="card">
            <div className="row" style={{ marginBottom: 8 }}><b>最终状态(变量表)</b></div>
            {Object.keys(result.state).length === 0 ? (
              <span className="muted small">无变量。</span>
            ) : (
              <table className="matrix">
                <thead><tr><th>变量</th><th>值</th></tr></thead>
                <tbody>
                  {Object.entries(result.state).map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ textAlign: "left" }}>
                        <a onClick={() => s.jumpTo("variables", k)}>{k}</a>
                      </td>
                      <td>{typeof v === "boolean" ? (v ? "true" : "false") : String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* 停止时可走边(便于决定下一步 choices) */}
          {result.available.length > 0 && (
            <div className="card">
              <div className="row" style={{ marginBottom: 8 }}><b>停止处可走边</b><span className="muted small">（填入上方选择序列可继续分叉）</span></div>
              <div className="row wrap">
                {result.available.map((e) => (
                  <span key={e.edge_id} className="chip">
                    {e.label || e.edge_id} → {nodeTitle(e.target)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
