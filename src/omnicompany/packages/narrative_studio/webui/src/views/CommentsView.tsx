// 圈选评论收件箱:把页面上圈选的片段连同评论收进来,逐条处理。
// 未处理排前;每条可勾"已处理"或删除;顶部表单新增一条。
import React, { useMemo, useState } from "react";
import { useStudio } from "../store";

// 契约镜像(对应后端 Comment;types.ts 落地前在本视图内声明)
interface Comment {
  id: string;
  target?: string | null;
  anchor?: string | null;
  body: string;
  author?: string | null;
  resolved: boolean;
}

export function CommentsView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const comments: Comment[] = ((p as unknown as { comments?: Comment[] }).comments) ?? [];

  // 新增表单
  const [target, setTarget] = useState("");
  const [anchor, setAnchor] = useState("");
  const [body, setBody] = useState("");

  // 未处理在前,各自保持原顺序
  const ordered = useMemo(() => {
    const open = comments.filter((c) => !c.resolved);
    const done = comments.filter((c) => c.resolved);
    return [...open, ...done];
  }, [comments]);
  const openCount = comments.filter((c) => !c.resolved).length;

  const add = async () => {
    const text = body.trim();
    if (!text) return;
    const c: Comment = {
      id: "cmt-" + Date.now(),
      target: target.trim() || null,
      anchor: anchor.trim() || null,
      body: text,
      resolved: false,
    };
    await s.createEntity("comments", c);
    setTarget("");
    setAnchor("");
    setBody("");
    onChanged?.();
  };

  const toggle = async (c: Comment) => {
    await s.updateEntity("comments", c.id, { ...c, resolved: !c.resolved });
    onChanged?.();
  };

  const remove = async (c: Comment) => {
    if (!confirm("删除这条评论?")) return;
    await s.deleteEntity("comments", c.id);
    onChanged?.();
  };

  return (
    <div>
      <h2 className="view-h">圈选评论</h2>
      <p className="view-sub">页面上圈选的片段连同评论都收进这个收件箱;未处理的排在前面,逐条勾掉或删除。</p>

      {/* 新增表单 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="field">
          <label>对象 target(可填 载体:id,或留空)</label>
          <input
            value={target}
            placeholder="例如 scenes:sc-01 / characters:ch-02,留空表示全局"
            onChange={(e) => setTarget(e.target.value)}
          />
        </div>
        <div className="field">
          <label>圈选片段 anchor</label>
          <input
            value={anchor}
            placeholder="被圈选的文字 / 元素描述"
            onChange={(e) => setAnchor(e.target.value)}
          />
        </div>
        <div className="field">
          <label>评论内容</label>
          <textarea
            value={body}
            placeholder="写下你的疑问或意见…"
            onChange={(e) => setBody(e.target.value)}
          />
        </div>
        <div className="row">
          <button className="primary" onClick={add} disabled={!body.trim()}>新增评论</button>
          <span className="grow" style={{ flex: 1 }} />
          <span className="muted small">共 {comments.length} 条 · 未处理 {openCount}</span>
        </div>
      </div>

      {/* 列表 */}
      {comments.length === 0 ? (
        <div className="card muted">
          收件箱是空的。在网页上圈选一个元素或一段文字,把它连同你的评论贴进来——它们会汇总到这里逐条处理。
        </div>
      ) : (
        ordered.map((c) => (
          <div
            className="card"
            key={c.id}
            style={c.resolved ? { opacity: 0.6 } : undefined}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label className="row small" style={{ width: "auto", gap: 4, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={c.resolved}
                  onChange={() => toggle(c)}
                  style={{ width: "auto" }}
                />
                <span className="muted">已处理</span>
              </label>
              <span className="grow" style={{ flex: 1 }} />
              {c.target ? (
                <span className="chip small">{c.target}</span>
              ) : (
                <span className="chip small muted">全局</span>
              )}
              <button onClick={() => remove(c)}>删除</button>
            </div>

            {c.anchor && (
              <div
                className="small"
                style={{
                  marginTop: 8,
                  paddingLeft: 8,
                  borderLeft: "3px solid var(--accent2)",
                  color: "var(--muted)",
                }}>
                “{c.anchor}”
              </div>
            )}

            <div style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>{c.body}</div>

            {c.author && <div className="muted small" style={{ marginTop: 6 }}>—— {c.author}</div>}
          </div>
        ))
      )}
    </div>
  );
}
