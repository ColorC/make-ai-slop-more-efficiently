// 落地层 · 草稿看板:p.game_texts 中 is_draft 为真的条目,按 status 分三列看板。
// "转正"调 api.draftPromote → reload + onChanged,条目 is_draft 转否进游戏内文本。
import React, { useMemo, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

// 本地契约镜像(types.ts 由他者维护;此处用文档约定的形状,store 接 any 落库)。
interface GameText {
  id: string;
  text_type: "card" | "event" | "tag" | "wiki";
  title: string;
  category?: string | null;
  host?: string | null;
  body: string;
  choices?: unknown[];
  art?: string | null;
  art_status?: string | null;
  annotations?: string | null;
  is_draft: boolean;
  status?: string | null;
}

type Col = "todo" | "tocomplete" | "done";
const COLS: Col[] = ["todo", "tocomplete", "done"];
const COL_LABEL: Record<Col, string> = { todo: "待写", tocomplete: "待完善", done: "已就绪" };

function normStatus(s?: string | null): Col {
  const v = (s ?? "").toLowerCase();
  if (v === "done") return "done";
  if (v === "tocomplete") return "tocomplete";
  return "todo";
}

function summarize(body: string): string {
  const t = (body ?? "").trim().replace(/\s+/g, " ");
  return t.length > 90 ? t.slice(0, 90) + "…" : t;
}

export function DraftsView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const all: GameText[] = ((p as any).game_texts ?? []) as GameText[];
  const drafts = useMemo(() => all.filter((g) => g.is_draft), [all]);

  const [promoting, setPromoting] = useState<string | null>(null);

  const byCol = useMemo(() => {
    const m: Record<Col, GameText[]> = { todo: [], tocomplete: [], done: [] };
    for (const g of drafts) m[normStatus(g.status)].push(g);
    return m;
  }, [drafts]);

  const add = async () => {
    await s.createEntity("game_texts", {
      id: "draft-" + Date.now(),
      text_type: "card",
      title: "草稿-新",
      body: "",
      is_draft: true,
    });
    onChanged?.();
  };

  const promote = async (g: GameText) => {
    setPromoting(g.id);
    try {
      await api.draftPromote(g.id);
      await s.reload();
      onChanged?.();
    } finally {
      setPromoting(null);
    }
  };

  return (
    <div>
      <h2 className="view-h">草稿看板</h2>
      <p className="view-sub">还在打磨的条目 —— 转正后即 is_draft=false,进入游戏内文本(写回 wiki)。</p>

      <div className="row" style={{ marginBottom: 12 }}>
        <button className="primary" onClick={add}>新建草稿</button>
        <span className="grow" style={{ flex: 1 }} />
        <span className="muted small">共 {drafts.length} 条草稿</span>
      </div>

      {drafts.length === 0 && <p className="muted small">还没有草稿。点上方新建草稿开始。</p>}

      <div className="grid" style={{ gridTemplateColumns: "repeat(3, 1fr)", alignItems: "start" }}>
        {COLS.map((col) => (
          <div key={col}>
            <div
              className="group"
              style={{ color: "var(--muted)", fontSize: 11, margin: "0 2px 6px", textTransform: "uppercase", letterSpacing: ".5px" }}
            >
              <span className={`status-dot status-${col}`} style={{ marginRight: 6 }} />
              {COL_LABEL[col]} · {byCol[col].length}
            </div>

            {byCol[col].length === 0 && <p className="muted small" style={{ margin: "0 2px 8px" }}>—</p>}

            {byCol[col].map((g) => (
              <div className="card" key={g.id}>
                <div className="row" style={{ marginBottom: 4 }}>
                  <b
                    className="clickable"
                    style={{ flex: 1, cursor: "pointer" }}
                    onClick={() => s.select("game_texts", g.id)}
                    title="点开检查器编辑全字段"
                  >
                    {g.title || g.id}
                  </b>
                  <span className="chip small">{g.text_type}</span>
                </div>

                {summarize(g.body) && (
                  <div className="small muted" style={{ marginBottom: 8 }}>{summarize(g.body)}</div>
                )}

                <div className="row">
                  <span className="grow" style={{ flex: 1 }} />
                  <button
                    className="primary"
                    disabled={promoting === g.id}
                    onClick={() => promote(g)}
                    title="转为正式条目,进入游戏内文本"
                  >
                    {promoting === g.id ? "转正中…" : "转正"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
