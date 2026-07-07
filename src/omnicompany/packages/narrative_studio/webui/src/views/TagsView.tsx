// 标签 / 伏笔视图:左列标签(foreshadow 类高亮),选中后右侧按叙述顺序画出现点链,
// 埋→收一目了然;只埋未收(链尾只 1 处)标红。
import React, { useEffect, useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

export function TagsView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;

  const [selTag, setSelTag] = useState<string | null>(p.tags[0]?.id ?? null);
  const [occ, setOcc] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selTag) { setOcc([]); return; }
    setLoading(true);
    api.tagOccurrences(selTag)
      .then((d) => setOcc(Array.isArray(d) ? d : []))
      .catch(() => setOcc([]))
      .finally(() => setLoading(false));
  }, [selTag, s.project]);

  const tag = p.tags.find((t) => t.id === selTag) ?? null;
  const isForeshadow = (kind: string) => kind === "foreshadow";

  return (
    <div>
      <h2 className="view-h">标签 / 伏笔</h2>
      <p className="view-sub">伏笔类标签高亮。选一个标签，右侧按叙述顺序串出它的出现点——埋下与回收一目了然。</p>

      <div className="grid" style={{ gridTemplateColumns: "240px 1fr" }}>
        {/* 左:标签列表 */}
        <div>
          {p.tags.length === 0 && <p className="muted">还没有标签。</p>}
          {p.tags.map((t) => (
            <div
              className={"card clickable"}
              key={t.id}
              style={{
                ...(selTag === t.id ? { borderColor: "var(--accent)" } : {}),
                ...(isForeshadow(t.kind) ? { borderLeft: "3px solid var(--accent2)" } : {}),
              }}
              onClick={() => setSelTag(t.id)}>
              <div className="row">
                <b>#{t.name}</b>
                <span className="grow" style={{ flex: 1 }} />
                <span className={"chip small" + (isForeshadow(t.kind) ? "" : "")}>{t.kind}</span>
              </div>
            </div>
          ))}
        </div>

        {/* 右:出现点链 */}
        <div>
          {!tag ? (
            <div className="card muted">选一个标签查看它的出现点链。</div>
          ) : (
            <div className="card">
              <div className="row" style={{ marginBottom: 10 }}>
                <b>#{tag.name}</b>
                <span className="muted small">{tag.kind}</span>
                <span className="grow" style={{ flex: 1 }} />
                <a onClick={() => s.select("tags", tag.id)}>编辑</a>
              </div>

              {loading ? (
                <div className="muted small">加载中…</div>
              ) : occ.length === 0 ? (
                <div className="muted small">这个标签还没在任何场景或成文行出现。</div>
              ) : (
                <div>
                  {occ.map((o, i) => {
                    const last = i === occ.length - 1;
                    const carrier = o.kind === "scene" ? "scenes" : "prose_lines";
                    return (
                      <div key={o.kind + ":" + o.id} className="row" style={{ alignItems: "flex-start", marginBottom: 8 }}>
                        <div style={{ width: 18, textAlign: "center", color: "var(--muted)" }}>
                          {i + 1}
                        </div>
                        <div style={{ flex: 1 }}>
                          <div className="card clickable" style={{ margin: 0 }}
                            onClick={() => s.jumpTo(carrier, o.id)}>
                            <div className="row">
                              <span className="chip small">{o.kind === "scene" ? "场景" : "成文"}</span>
                              <span>{o.title || <span className="muted">（无标题）</span>}</span>
                            </div>
                          </div>
                          {!last && <div style={{ marginLeft: 10, color: "var(--muted)" }}>↓</div>}
                        </div>
                      </div>
                    );
                  })}
                  {occ.length === 1 && isForeshadow(tag.kind) && (
                    <div className="sev-high small" style={{ marginTop: 4 }}>只埋未收：这条伏笔只出现一处，还没有回收点。</div>
                  )}
                  {occ.length === 1 && !isForeshadow(tag.kind) && (
                    <div className="muted small" style={{ marginTop: 4 }}>仅一处出现。</div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
