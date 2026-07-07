// 受众与预期管理:显示并编辑 audience 单对象。
// segments(受众分群 name+note 列表,可增删)、stance(对待受众的基调)、
// expectations(期待管理:每条 promise→payoff)、resonance_targets(共鸣余韵目标)。
// 一切修改都整体回写 updateEntity('audience','_',{...})。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import type { Provenance } from "../types";

// v2 单对象载体 Audience(契约,与 types.ts/后端一致:expectations 是字符串列表)
interface AudienceSegment { name?: string | null; note?: string | null; }
interface Audience {
  segments: AudienceSegment[];
  stance?: string | null;
  expectations: string[];
  resonance_targets: string[];
  provenance?: Provenance | null;
}

const EMPTY_AUD: Audience = { segments: [], stance: "", expectations: [], resonance_targets: [] };

export function AudienceView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const aud: Audience = { ...EMPTY_AUD, ...((p as unknown as { audience?: Audience }).audience ?? {}) };

  // 本地缓冲:stance + 新增共鸣目标输入
  const [stance, setStance] = useState<string>(aud.stance ?? "");
  const [newReso, setNewReso] = useState<string>("");
  const [resoDrafts, setResoDrafts] = useState<Record<number, string>>({});
  const [newExp, setNewExp] = useState<string>("");
  const [expDrafts, setExpDrafts] = useState<Record<number, string>>({});

  useEffect(() => {
    setStance(aud.stance ?? "");
    setResoDrafts({});
    setExpDrafts({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p]);

  const save = async (patch: Partial<Audience>) => {
    const next: Audience = { ...aud, ...patch };
    await s.updateEntity("audience", "_", next);
    onChanged?.();
  };

  // —— stance ——
  const commitStance = async () => {
    if (stance === (aud.stance ?? "")) return;
    await save({ stance });
  };

  // —— segments ——
  const addSegment = async () =>
    save({ segments: [...aud.segments, { name: "", note: "" }] });
  const patchSegment = async (i: number, patch: Partial<AudienceSegment>) => {
    const arr = aud.segments.slice();
    arr[i] = { ...arr[i], ...patch };
    await save({ segments: arr });
  };
  const removeSegment = async (i: number) => {
    const arr = aud.segments.slice();
    arr.splice(i, 1);
    await save({ segments: arr });
  };

  // —— expectations(期待管理:许诺→兑现,一句话字符串列表)——
  const addExpectation = async () => {
    const v = newExp.trim();
    if (!v) return;
    setNewExp("");
    await save({ expectations: [...aud.expectations, v] });
  };
  const commitExpectation = async (i: number) => {
    const v = (expDrafts[i] ?? "").trim();
    if (v === (aud.expectations[i] ?? "")) return;
    if (!v) { await removeExpectation(i); return; }
    const arr = aud.expectations.slice();
    arr[i] = v;
    await save({ expectations: arr });
  };
  const removeExpectation = async (i: number) => {
    const arr = aud.expectations.slice();
    arr.splice(i, 1);
    await save({ expectations: arr });
  };

  // —— resonance_targets(字符串列表)——
  const addResonance = async () => {
    const v = newReso.trim();
    if (!v) return;
    setNewReso("");
    await save({ resonance_targets: [...aud.resonance_targets, v] });
  };
  const commitResonance = async (i: number) => {
    const v = (resoDrafts[i] ?? "").trim();
    if (v === (aud.resonance_targets[i] ?? "")) return;
    if (!v) { await removeResonance(i); return; }
    const arr = aud.resonance_targets.slice();
    arr[i] = v;
    await save({ resonance_targets: arr });
  };
  const removeResonance = async (i: number) => {
    const arr = aud.resonance_targets.slice();
    arr.splice(i, 1);
    await save({ resonance_targets: arr });
  };

  return (
    <div>
      <h2 className="view-h">受众与预期管理</h2>
      <p className="view-sub">写给谁看、用什么姿态对待他们、许诺了什么又怎么兑现,以及想留下的余韵。</p>

      {/* 基调 */}
      <div className="field">
        <label>基调(对待受众的姿态)</label>
        <textarea
          value={stance}
          placeholder="挑衅 / 共谋 / 安抚 / 引导…用什么态度跟受众相处?"
          onChange={(e) => setStance(e.target.value)}
          onBlur={commitStance}
        />
      </div>

      {/* 受众分群 */}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="muted small" style={{ flex: 1 }}>受众分群　共 {aud.segments.length} 群</span>
          <button className="primary small" onClick={addSegment}>新增分群</button>
        </div>
        <div className="grid">
          {aud.segments.length === 0 && <span className="muted small">还没有分群,点右上新增一群。</span>}
          {aud.segments.map((seg, i) => (
            <div className="card" style={{ margin: 0 }} key={i}>
              <div className="row">
                <input
                  style={{ flex: "0 0 200px" }}
                  value={seg.name ?? ""}
                  placeholder="分群名(如:核心玩家)"
                  onChange={(e) => patchSegment(i, { name: e.target.value })}
                />
                <input
                  style={{ flex: 1 }}
                  value={seg.note ?? ""}
                  placeholder="这群人的画像 / 诉求…"
                  onChange={(e) => patchSegment(i, { note: e.target.value })}
                />
                <button onClick={() => removeSegment(i)}>删除</button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 期待管理(许诺→兑现,一句话) */}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="muted small" style={{ flex: 1 }}>期待管理(许诺 → 兑现)　共 {aud.expectations.length} 条</span>
        </div>
        <div className="grid">
          {aud.expectations.length === 0 && <span className="muted small">还没有期待条目,在下方新增一条。</span>}
          {aud.expectations.map((t, i) => (
            <div className="row" key={i}>
              <input
                value={expDrafts[i] ?? t}
                onChange={(e) => setExpDrafts((d) => ({ ...d, [i]: e.target.value }))}
                onBlur={() => commitExpectation(i)}
              />
              <button onClick={() => removeExpectation(i)}>删除</button>
            </div>
          ))}
          <div className="row">
            <input
              value={newExp}
              placeholder="一条 许诺→兑现(如:承诺正常校园恋爱 → 越成功越不知他爱的是谁)…(回车新增)"
              onChange={(e) => setNewExp(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") addExpectation(); }}
            />
            <button className="primary" onClick={addExpectation}>新增</button>
          </div>
        </div>
      </div>

      {/* 共鸣余韵目标 */}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="muted small" style={{ flex: 1 }}>共鸣 / 余韵目标　共 {aud.resonance_targets.length} 条</span>
        </div>
        <div className="grid">
          {aud.resonance_targets.length === 0 && <span className="muted small">还没有共鸣目标,在下方新增一条。</span>}
          {aud.resonance_targets.map((t, i) => (
            <div className="row" key={i}>
              <input
                value={resoDrafts[i] ?? t}
                onChange={(e) => setResoDrafts((d) => ({ ...d, [i]: e.target.value }))}
                onBlur={() => commitResonance(i)}
              />
              <button onClick={() => removeResonance(i)}>删除</button>
            </div>
          ))}
          <div className="row">
            <input
              value={newReso}
              placeholder="想让受众通关后留下的感受 / 想法…(回车新增)"
              onChange={(e) => setNewReso(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") addResonance(); }}
            />
            <button className="primary" onClick={addResonance}>新增</button>
          </div>
        </div>
      </div>

      {aud.provenance?.source && (
        <div className="card small">
          <span className="muted">出处:</span>{" "}
          <a onClick={() => s.setActiveView("provenance")}>{aud.provenance.source}</a>
          {aud.provenance.note && <span className="muted">　{aud.provenance.note}</span>}
        </div>
      )}
    </div>
  );
}
