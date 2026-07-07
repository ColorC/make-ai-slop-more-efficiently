// 落地层 · 游戏内文本:p.game_texts 中 is_draft 为否的条目,按 text_type 分组成区。
// 编辑即写回 vilo wiki(单一内容库)——这是最重要的落地面。
import React, { useEffect, useMemo, useState } from "react";
import { useStudio } from "../store";

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

const TYPE_ORDER: GameText["text_type"][] = ["card", "event", "tag", "wiki"];
const TYPE_LABEL: Record<GameText["text_type"], string> = {
  card: "卡牌", event: "事件", tag: "标签", wiki: "百科",
};

// art_status → 徽标色(done/ok 绿、todo 灰、其余警告)
function artBadgeClass(s?: string | null): string {
  const v = (s ?? "").toLowerCase();
  if (v === "done" || v === "ok" || v === "ready") return "badge ok";
  if (v === "" || v === "todo" || v === "none") return "badge";
  return "badge warn";
}

export function GameTextView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const all: GameText[] = ((p as any).game_texts ?? []) as GameText[];
  const live = useMemo(() => all.filter((g) => !g.is_draft), [all]);

  // 本地编辑缓冲:body / annotations 各自一份,失焦才落库(输入流畅)。
  const [bodyDraft, setBodyDraft] = useState<Record<string, string>>({});
  const [annDraft, setAnnDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    setBodyDraft((prev) => {
      const next: Record<string, string> = {};
      for (const g of live) next[g.id] = g.id in prev ? prev[g.id] : (g.body ?? "");
      return next;
    });
    setAnnDraft((prev) => {
      const next: Record<string, string> = {};
      for (const g of live) next[g.id] = g.id in prev ? prev[g.id] : (g.annotations ?? "");
      return next;
    });
  }, [p]);

  const groups = useMemo(() => {
    const m: Record<string, GameText[]> = {};
    for (const g of live) (m[g.text_type] ??= []).push(g);
    return m;
  }, [live]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const g of live) c[g.text_type] = (c[g.text_type] ?? 0) + 1;
    return c;
  }, [live]);

  const commitBody = async (g: GameText) => {
    const body = bodyDraft[g.id] ?? "";
    if (body === (g.body ?? "")) return;
    await s.updateEntity("game_texts", g.id, { ...g, body });
    onChanged?.();
  };
  const commitAnn = async (g: GameText) => {
    const annotations = annDraft[g.id] ?? "";
    if (annotations === (g.annotations ?? "")) return;
    await s.updateEntity("game_texts", g.id, { ...g, annotations });
    onChanged?.();
  };

  const add = async (text_type: "card" | "event") => {
    await s.createEntity("game_texts", {
      id: "gt-" + Date.now(),
      text_type,
      title: "新条目",
      body: "",
      is_draft: false,
    });
    onChanged?.();
  };

  return (
    <div>
      <h2 className="view-h">游戏内文本</h2>
      <p className="view-sub">游戏会真正读到的文案 —— 编辑即写回游戏内 wiki(单一内容库),立即生效。</p>

      <div className="row" style={{ marginBottom: 12 }}>
        <button className="primary" onClick={() => add("card")}>新建卡</button>
        <button className="primary" onClick={() => add("event")}>新建事件</button>
        <span className="grow" style={{ flex: 1 }} />
        <span className="muted small">
          共 {live.length} 条 ·
          {TYPE_ORDER.map((t) => ` ${TYPE_LABEL[t]} ${counts[t] ?? 0}`).join(" /")}
        </span>
      </div>

      {live.length === 0 && (
        <p className="muted small">还没有正式条目。点上方新建卡 / 新建事件,或在草稿看板里把草稿转正。</p>
      )}

      {TYPE_ORDER.map((t) => {
        const items = groups[t];
        if (!items || items.length === 0) return null;
        return (
          <div key={t} style={{ marginBottom: 18 }}>
            <div className="group" style={{ color: "var(--muted)", fontSize: 11, margin: "10px 2px 6px", textTransform: "uppercase", letterSpacing: ".5px" }}>
              {TYPE_LABEL[t]} · {items.length}
            </div>
            {items.map((g) => (
              <div className="card" key={g.id}>
                <div className="row" style={{ marginBottom: 6 }}>
                  <b
                    className="clickable"
                    style={{ flex: 1, cursor: "pointer" }}
                    onClick={() => s.select("game_texts", g.id)}
                    title="点开检查器编辑全字段"
                  >
                    {g.title || g.id}
                  </b>
                  {g.category && <span className="chip small">{g.category}</span>}
                  <span className={artBadgeClass(g.art_status)}>
                    美术 {g.art_status || "未定"}
                  </span>
                </div>

                <div className="field">
                  <label>文案 / 正文(失焦写回 wiki)</label>
                  <textarea
                    value={bodyDraft[g.id] ?? ""}
                    placeholder="写文案 / 正文…"
                    onChange={(e) => setBodyDraft((d) => ({ ...d, [g.id]: e.target.value }))}
                    onBlur={() => commitBody(g)}
                  />
                </div>

                <div className="field" style={{ marginBottom: 0 }}>
                  <label>创作者批注(失焦写回 wiki)</label>
                  <textarea
                    value={annDraft[g.id] ?? ""}
                    placeholder="给同伴 / 美术的批注…"
                    onChange={(e) => setAnnDraft((d) => ({ ...d, [g.id]: e.target.value }))}
                    onBlur={() => commitAnn(g)}
                  />
                </div>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
