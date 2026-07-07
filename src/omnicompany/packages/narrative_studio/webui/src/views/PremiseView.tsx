// 立意视图:展示中心命题 / 控制理念 / 立场 / 锁定。
// 每条控制理念可下钻"看服务它的场景"。
import React, { useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";

interface IdeaScene { scene_id: string; title?: string | null; status?: string }

export function PremiseView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  const pr = p.premise;

  // idea -> 服务它的场景列表(点开才拉)
  const [open, setOpen] = useState<string | null>(null);
  const [scenes, setScenes] = useState<IdeaScene[]>([]);
  const [loading, setLoading] = useState(false);

  const toggleIdea = (idea: string) => {
    if (open === idea) { setOpen(null); setScenes([]); return; }
    setOpen(idea); setScenes([]); setLoading(true);
    api.ideaAlignment(idea)
      .then((r: any) => setScenes(Array.isArray(r) ? r : []))
      .catch(() => setScenes([]))
      .finally(() => setLoading(false));
  };

  return (
    <div>
      <h2 className="view-h">立意</h2>
      <p className="view-sub">这部作品想说什么 —— 中心命题与它的控制理念。</p>

      <div className="row" style={{ marginBottom: 12 }}>
        <button className="primary" onClick={() => s.select("premise", "premise")}>编辑立意</button>
        {pr.locked && <span className="badge ok">已锁定</span>}
      </div>

      <div className="card clickable" onClick={() => s.select("premise", "premise")}>
        <div className="muted small">中心命题</div>
        <div style={{ fontSize: 15, lineHeight: 1.65, marginTop: 4, whiteSpace: "pre-wrap" }}>
          {pr.proposition || <span className="muted">(未填写)</span>}
        </div>
      </div>

      <div className="card">
        <div className="muted small" style={{ marginBottom: 6 }}>控制理念</div>
        {(pr.controlling_ideas || []).length === 0 && <span className="muted">(暂无)</span>}
        <div className="grid">
          {(pr.controlling_ideas || []).map((idea, i) => (
            <div key={i} className="card" style={{ margin: 0 }}>
              <div className="row">
                <span style={{ flex: 1 }}>{idea}</span>
                <button className="small" onClick={() => toggleIdea(idea)}>
                  {open === idea ? "收起" : "看服务它的场景"}
                </button>
              </div>
              {open === idea && (
                <div style={{ marginTop: 8 }}>
                  {loading && <span className="muted small">加载中…</span>}
                  {!loading && scenes.length === 0 && <span className="muted small">没有场景声明服务此理念</span>}
                  <div className="wrap">
                    {scenes.map((sc) => (
                      <span
                        key={sc.scene_id}
                        className="chip click"
                        onClick={() => s.jumpTo("scenes", sc.scene_id)}
                      >
                        <span className={`status-dot status-${sc.status ?? "todo"}`} style={{ marginRight: 5 }} />
                        {sc.title || sc.scene_id}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="muted small">立场</div>
        <div style={{ marginTop: 4 }}>{pr.stance || <span className="muted">(未定)</span>}</div>
      </div>

      {pr.provenance?.source && (
        <div className="card small">
          <span className="muted">出处:</span>{" "}
          <a onClick={() => s.setActiveView("provenance")}>{pr.provenance.source}</a>
          {pr.provenance.note && <span className="muted">　{pr.provenance.note}</span>}
        </div>
      )}
    </div>
  );
}
