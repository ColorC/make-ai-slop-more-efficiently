// 背景 / 思考:显示并编辑创作的设计理念与世界背景。
// thinking(设计理念/取舍)、world_notes(世界背景)用 textarea 失焦整体回写 background 单对象;
// open_questions(待定问题)可增删条目;下方吸收旧便签(notes)的增删改。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import type { Note, Provenance } from "../types";

// v2 单对象载体 Background(契约:thinking/world_notes/open_questions[];可带 provenance)
interface Background {
  thinking?: string | null;
  world_notes?: string | null;
  open_questions: string[];
  provenance?: Provenance | null;
}

const EMPTY_BG: Background = { thinking: "", world_notes: "", open_questions: [] };

export function BackgroundView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const bg: Background = { ...EMPTY_BG, ...((p as unknown as { background?: Background }).background ?? {}) };
  const notes: Note[] = p.notes ?? [];

  // 本地编辑缓冲,失焦才落库(让输入流畅)
  const [thinking, setThinking] = useState<string>(bg.thinking ?? "");
  const [worldNotes, setWorldNotes] = useState<string>(bg.world_notes ?? "");
  const [newQ, setNewQ] = useState<string>("");
  const [qDrafts, setQDrafts] = useState<Record<number, string>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});

  // project 变化时同步缓冲(未在编辑中的项)
  useEffect(() => {
    setThinking(bg.thinking ?? "");
    setWorldNotes(bg.world_notes ?? "");
    setQDrafts({});
    setNoteDrafts((prev) => {
      const next: Record<string, string> = {};
      for (const n of notes) next[n.id] = n.id in prev ? prev[n.id] : (n.text ?? "");
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p]);

  const saveBg = async (patch: Partial<Background>) => {
    const next: Background = { ...bg, ...patch };
    await s.updateEntity("background", "_", next);
    onChanged?.();
  };

  const commitThinking = async () => {
    if (thinking === (bg.thinking ?? "")) return;
    await saveBg({ thinking });
  };
  const commitWorldNotes = async () => {
    if (worldNotes === (bg.world_notes ?? "")) return;
    await saveBg({ world_notes: worldNotes });
  };

  const addQuestion = async () => {
    const q = newQ.trim();
    if (!q) return;
    setNewQ("");
    await saveBg({ open_questions: [...bg.open_questions, q] });
  };
  const commitQuestion = async (i: number) => {
    const v = (qDrafts[i] ?? "").trim();
    if (v === (bg.open_questions[i] ?? "")) return;
    if (!v) { await removeQuestion(i); return; }
    const arr = bg.open_questions.slice();
    arr[i] = v;
    await saveBg({ open_questions: arr });
  };
  const removeQuestion = async (i: number) => {
    const arr = bg.open_questions.slice();
    arr.splice(i, 1);
    await saveBg({ open_questions: arr });
  };

  const addNote = async () => {
    const note: Note = { id: "note-" + Date.now(), text: "", at: new Date().toISOString() };
    await s.createEntity("notes", note);
    onChanged?.();
  };
  const commitNote = async (n: Note) => {
    const text = noteDrafts[n.id] ?? "";
    if (text === (n.text ?? "")) return;
    await s.updateEntity("notes", n.id, { ...n, text });
    onChanged?.();
  };
  const removeNote = async (n: Note) => {
    if (!confirm("删除这条便签?")) return;
    await s.deleteEntity("notes", n.id);
    onChanged?.();
  };

  return (
    <div>
      <h2 className="view-h">背景 / 思考</h2>
      <p className="view-sub">创作之前与之外的思考 —— 设计理念与取舍、世界背景、还没定下来的问题。</p>

      <div className="field">
        <label>设计理念 / 取舍</label>
        <textarea
          value={thinking}
          placeholder="为什么这么设计?做了哪些取舍?想达到什么、放弃了什么…"
          style={{ minHeight: 110 }}
          onChange={(e) => setThinking(e.target.value)}
          onBlur={commitThinking}
        />
      </div>

      <div className="field">
        <label>世界背景</label>
        <textarea
          value={worldNotes}
          placeholder="这个故事发生的世界:时代、地理、规则、氛围…(细化的设定条目去「设定 · 世界」)"
          style={{ minHeight: 110 }}
          onChange={(e) => setWorldNotes(e.target.value)}
          onBlur={commitWorldNotes}
        />
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="muted small" style={{ flex: 1 }}>待定问题　共 {bg.open_questions.length} 条</span>
        </div>
        <div className="grid">
          {bg.open_questions.length === 0 && <span className="muted small">还没有待定问题,在下方新增一条。</span>}
          {bg.open_questions.map((q, i) => (
            <div className="row" key={i}>
              <input
                value={qDrafts[i] ?? q}
                onChange={(e) => setQDrafts((d) => ({ ...d, [i]: e.target.value }))}
                onBlur={() => commitQuestion(i)}
              />
              <button onClick={() => removeQuestion(i)}>删除</button>
            </div>
          ))}
          <div className="row">
            <input
              value={newQ}
              placeholder="还没想清楚的问题…(回车新增)"
              onChange={(e) => setNewQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") addQuestion(); }}
            />
            <button className="primary" onClick={addQuestion}>新增</button>
          </div>
        </div>
      </div>

      {bg.provenance?.source && (
        <div className="card small">
          <span className="muted">出处:</span>{" "}
          <a onClick={() => s.setActiveView("provenance")}>{bg.provenance.source}</a>
          {bg.provenance.note && <span className="muted">　{bg.provenance.note}</span>}
        </div>
      )}

      <div className="row" style={{ margin: "18px 0 8px" }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0, flex: 1 }}>便签</h3>
        <span className="muted small">共 {notes.length} 条</span>
        <button className="primary" onClick={addNote}>新增便签</button>
      </div>
      <p className="muted small" style={{ margin: "0 0 8px" }}>随手记下的想法,不进结构,只是草稿纸。</p>

      {notes.length === 0 && <p className="muted small">还没有便签,点上面新增一条。</p>}
      {notes.map((n) => (
        <div className="card" key={n.id}>
          <textarea
            value={noteDrafts[n.id] ?? ""}
            placeholder="写点什么…"
            onChange={(e) => setNoteDrafts((d) => ({ ...d, [n.id]: e.target.value }))}
            onBlur={() => commitNote(n)}
          />
          <div className="row" style={{ marginTop: 6 }}>
            {n.at && <span className="muted small">{n.at}</span>}
            <span style={{ flex: 1 }} />
            <button onClick={() => removeNote(n)}>删除</button>
          </div>
        </div>
      ))}
    </div>
  );
}
