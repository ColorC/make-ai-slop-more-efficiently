// 否决案归档(只读):被取代 / 被否决的旧内容,仅供查证,绝不作真源或基础。
// 按 verdict 分两区;每条 area + title + reason + source(+ excerpt 如有),点 source 展开。
import React, { useMemo, useState } from "react";
import { useStudio } from "../store";

// 契约镜像(对应后端 RejectedItem;types.ts 落地前在本视图内声明)
interface RejectedItem {
  id: string;
  area: string;
  title: string;
  verdict: "superseded" | "rejected";
  reason?: string | null;
  source?: string | null;
  excerpt?: string | null;
}

function Section({ title, sub, sev, items }: {
  title: string;
  sub: string;
  sev: "sev-medium" | "sev-high";
  items: RejectedItem[];
}) {
  const [openSrc, setOpenSrc] = useState<Record<string, boolean>>({});
  const toggle = (id: string) => setOpenSrc((o) => ({ ...o, [id]: !o[id] }));

  return (
    <div style={{ marginBottom: 20 }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <b className={sev}>{title}</b>
        <span className="muted small">{sub}</span>
        <span className="grow" style={{ flex: 1 }} />
        <span className="muted small">{items.length} 条</span>
      </div>

      {items.length === 0 ? (
        <div className="card muted small">这一区没有内容。</div>
      ) : (
        items.map((it) => (
          <div className="card" key={it.id}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <span className="chip small">{it.area}</span>
              <span style={{ flex: 1 }}>{it.title}</span>
            </div>

            {it.reason && (
              <div className={"small " + sev} style={{ marginTop: 6 }}>
                {it.reason}
              </div>
            )}

            {it.excerpt && (
              <div
                className="muted small"
                style={{
                  marginTop: 6,
                  paddingLeft: 8,
                  borderLeft: "3px solid var(--border)",
                  whiteSpace: "pre-wrap",
                }}>
                {it.excerpt}
              </div>
            )}

            {it.source && (
              <div className="small" style={{ marginTop: 6 }}>
                <a onClick={() => toggle(it.id)}>{openSrc[it.id] ? "收起出处" : "出处"}</a>
                {openSrc[it.id] && (
                  <div className="muted" style={{ marginTop: 4, wordBreak: "break-all" }}>
                    {it.source}
                  </div>
                )}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}

export function RejectedArchiveView({ onChanged }: { onChanged?: () => void }) {
  void onChanged; // 只读视图,无写入
  const s = useStudio();
  const p = s.project!;
  const archive: RejectedItem[] =
    ((p as unknown as { rejected_archive?: RejectedItem[] }).rejected_archive) ?? [];

  const superseded = useMemo(() => archive.filter((it) => it.verdict === "superseded"), [archive]);
  const rejected = useMemo(() => archive.filter((it) => it.verdict === "rejected"), [archive]);

  return (
    <div>
      <h2 className="view-h">否决案归档</h2>
      <p className="view-sub">这些是被取代或被否决的旧内容,仅供查证,绝不作真源 / 基础。</p>

      {archive.length === 0 ? (
        <div className="card muted">归档是空的。没有被取代或被否决的内容。</div>
      ) : (
        <>
          <Section
            title="被取代"
            sub="有更新的内容接替了它"
            sev="sev-medium"
            items={superseded}
          />
          <Section
            title="被否决"
            sub="经评估不予采用"
            sev="sev-high"
            items={rejected}
          />
        </>
      )}
    </div>
  );
}
