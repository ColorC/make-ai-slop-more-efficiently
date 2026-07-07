// 文风矩阵视图:registers(规则)/ voices(内心声道)/ style_matrix(情绪×场景→register)。
// 当前多为空 —— 顶部展示已认可原则"先语义后文风"(取自 notes 中 note-writing-principle),
// 并提示矩阵无认可版本(见否决案归档)。有数据则列出,可 select 编辑 + 新增。
import React from "react";
import { useStudio } from "../store";

export function StyleView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const registers = p.registers ?? [];
  const voices = p.voices ?? [];
  const matrix = p.style_matrix ?? [];

  const principle = (p.notes ?? []).find((n) => n.id === "note-writing-principle");

  const registerLabel = (id?: string | null): string => {
    if (!id) return "—";
    const r = registers.find((x) => x.id === id);
    return r ? (r.rule ? `${r.id}（${r.rule}）` : r.id) : id;
  };

  const addRegister = async () => {
    await s.createEntity("registers", { id: "reg-" + Date.now(), rule: "新规则" });
    onChanged?.();
  };
  const addVoice = async () => {
    await s.createEntity("voices", { id: "voice-" + Date.now() });
    onChanged?.();
  };
  const addMatrixEntry = async () => {
    await s.createEntity("style_matrix", { emotion: null, scene_type: null, register_id: null, style_config: null });
    onChanged?.();
  };

  return (
    <div>
      <h2 className="view-h">文风矩阵</h2>
      <p className="view-sub">文风不先于语义 —— 先把场景在干什么定下，再用语域 / 声道 / 矩阵决定它怎么落到字面。</p>

      <div className="card" style={{ borderColor: "var(--ok)" }}>
        <div className="row">
          <span className="badge ok">已认可原则</span>
          <b style={{ marginLeft: 6 }}>先语义后文风</b>
        </div>
        <div className="small" style={{ marginTop: 8 }}>
          {principle
            ? principle.text
            : "文风讨论的唯一已认可结论:先把语义(场景意图、客观事件、价值转向)固定，再谈文风落地。"}
        </div>
        <div className="small muted" style={{ marginTop: 8 }}>
          具体文风矩阵无认可版本(见否决案归档)。
        </div>
      </div>

      {/* 语域 registers */}
      <div className="row" style={{ marginTop: 14 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>语域 / 规则</h3>
        <span className="grow" style={{ flex: 1 }} />
        <button className="primary small" onClick={addRegister}>新增语域</button>
      </div>
      {registers.length === 0
        ? <p className="muted small" style={{ marginTop: 6 }}>暂无语域规则。</p>
        : (
          <div className="grid" style={{ marginTop: 6 }}>
            {registers.map((r) => (
              <div key={r.id} className="card clickable" onClick={() => s.select("registers", r.id)}>
                <div className="row">
                  <b style={{ flex: 1 }}>{r.id}</b>
                </div>
                {r.rule && <div className="small" style={{ marginTop: 6 }}>{r.rule}</div>}
              </div>
            ))}
          </div>
        )}

      {/* 内心声道 voices */}
      <div className="row" style={{ marginTop: 14 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>内心声道</h3>
        <span className="grow" style={{ flex: 1 }} />
        <button className="primary small" onClick={addVoice}>新增声道</button>
      </div>
      {voices.length === 0
        ? <p className="muted small" style={{ marginTop: 6 }}>暂无内心声道。</p>
        : (
          <div className="grid" style={{ marginTop: 6 }}>
            {voices.map((v) => (
              <div key={v.id} className="card clickable" onClick={() => s.select("voices", v.id)}>
                <div className="row">
                  <b style={{ flex: 1 }}>{v.id}</b>
                  {v.register_id && <span className="chip">{registerLabel(v.register_id)}</span>}
                </div>
                {v.syntax && <div className="small" style={{ marginTop: 6 }}><span className="muted">句法：</span>{v.syntax}</div>}
                {v.lexicon && <div className="small"><span className="muted">用词：</span>{v.lexicon}</div>}
                {v.taboos && <div className="small"><span className="muted">禁忌：</span>{v.taboos}</div>}
              </div>
            ))}
          </div>
        )}

      {/* 风格矩阵 style_matrix */}
      <div className="row" style={{ marginTop: 14 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>风格矩阵（情绪 × 场景 → 语域）</h3>
        <span className="grow" style={{ flex: 1 }} />
        <button className="primary small" onClick={addMatrixEntry}>新增条目</button>
      </div>
      {matrix.length === 0
        ? <p className="muted small" style={{ marginTop: 6 }}>矩阵为空。</p>
        : (
          <table className="matrix" style={{ marginTop: 6, width: "100%" }}>
            <thead>
              <tr><th>情绪</th><th>场景类型</th><th>语域</th><th>风格配置</th></tr>
            </thead>
            <tbody>
              {matrix.map((m, i) => (
                <tr key={i} className="card clickable" style={{ cursor: "pointer" }}
                  onClick={() => s.select("style_matrix", String(i))}>
                  <td>{m.emotion ?? "—"}</td>
                  <td>{m.scene_type ?? "—"}</td>
                  <td>{registerLabel(m.register_id)}</td>
                  <td>{m.style_config ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </div>
  );
}
