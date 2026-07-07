// 角色视图:按 importance 分组成卡片网格。点卡打开检查器编辑;卡上"出场场景"展开该角色出场场景序列(点跳场景)。
// 就地动作:status-dot 点快切状态、"采访"展开按 dossier 维度编辑、参考图 URL+缩略图、多选批量改状态。
import React, { useState } from "react";
import { useStudio } from "../store";
import { api } from "../api";
import type { Character, DossierField, Status } from "../types";

const GROUPS: { key: string; label: string }[] = [
  { key: "main", label: "主角" },
  { key: "secondary", label: "配角" },
  { key: "group", label: "群像 / 群体" },
];

// status 循环:todo → tocomplete → done → todo
const STATUS_CYCLE: Status[] = ["todo", "tocomplete", "done"];
const nextStatus = (s: Status): Status =>
  STATUS_CYCLE[(STATUS_CYCLE.indexOf(s) + 1) % STATUS_CYCLE.length];

// 把 arc 四元拆成有值的项
function arcItems(c: Character): { k: string; label: string; v: string }[] {
  const a = c.arc ?? {};
  const defs: { k: keyof typeof a; label: string }[] = [
    { k: "want", label: "想要" },
    { k: "need", label: "需要" },
    { k: "wound", label: "创伤" },
    { k: "lie", label: "谎言" },
  ];
  return defs
    .map((d) => ({ k: d.k as string, label: d.label, v: (a[d.k] ?? "") as string }))
    .filter((x) => x.v && String(x.v).trim());
}

export function CharactersView({ onChanged }: { onChanged?: () => void }) {
  const s = useStudio();
  const p = s.project!;
  // 已展开"出场场景"的角色 → 其场景投影
  const [scenesOf, setScenesOf] = useState<Record<string, any>>({});
  // 已展开"采访"的角色集合
  const [interviewing, setInterviewing] = useState<Record<string, true>>({});
  // 多选模式 + 选中集合
  const [multi, setMulti] = useState(false);
  const [picked, setPicked] = useState<Record<string, true>>({});
  const [busy, setBusy] = useState(false);

  const chars = p.characters ?? [];
  const known = new Set(GROUPS.map((g) => g.key));
  const groupOf = (c: Character) => (known.has(c.importance) ? c.importance : "other");

  const toggleScenes = (id: string) => {
    setScenesOf((prev) => {
      if (id in prev) {
        const next = { ...prev };
        delete next[id];
        return next;
      }
      return prev;
    });
    if (!(id in scenesOf)) {
      api.characterScenes(id).then((d) => setScenesOf((prev) => ({ ...prev, [id]: d }))).catch(() => {});
    }
  };

  const toggleInterview = (id: string) =>
    setInterviewing((prev) => {
      const next = { ...prev };
      if (id in next) delete next[id];
      else next[id] = true;
      return next;
    });

  const togglePick = (id: string) =>
    setPicked((prev) => {
      const next = { ...prev };
      if (id in next) delete next[id];
      else next[id] = true;
      return next;
    });

  const addCharacter = async () => {
    const id = "char_" + Math.random().toString(36).slice(2, 8);
    await s.createEntity("characters", {
      id, name: "新角色", importance: "secondary",
      arc: {}, summary: {}, dossier: [], facts: [],
      custom_fields: {}, status: "todo",
    });
    onChanged?.();
    s.select("characters", id);
  };

  // 整体回写一个 character(后端做全量替换),完成后 onChanged
  const writeChar = async (c: Character, patch: Partial<Character>) => {
    await s.updateEntity("characters", c.id, { ...c, ...patch });
    onChanged?.();
  };

  // ① status-dot 快切
  const cycleStatus = async (c: Character, e: React.MouseEvent) => {
    e.stopPropagation();
    await writeChar(c, { status: nextStatus(c.status) });
  };

  // ② 采访:更新某维 freetext
  const setDossierFreetext = async (c: Character, idx: number, freetext: string) => {
    const cur = c.dossier ?? [];
    if (idx < 0 || idx >= cur.length) return;
    if ((cur[idx].freetext ?? "") === freetext) return; // 无变化不回写
    const dossier = cur.map((d, i) => (i === idx ? { ...d, freetext } : d));
    await writeChar(c, { dossier });
  };

  const addDimension = async (c: Character) => {
    const field: DossierField = { dimension: "性格", mode: "freetext", questions: [], freetext: "", status: "todo" };
    await writeChar(c, { dossier: [...(c.dossier ?? []), field] });
  };

  // ③ 参考图 URL
  const setImage = async (c: Character, image: string) => {
    const v = image.trim();
    if ((c.image ?? "") === v) return; // 无变化不回写
    await writeChar(c, { image: v || null });
  };

  // ④ 批量改状态
  const pickedIds = Object.keys(picked);
  const batchStatus = async (status: Status) => {
    if (pickedIds.length === 0) return;
    setBusy(true);
    try {
      await api.batchUpdate("characters", pickedIds, { status });
      await s.reload();
      onChanged?.();
      setPicked({});
    } finally {
      setBusy(false);
    }
  };

  const exitMulti = () => { setMulti(false); setPicked({}); };

  // 角色 id → 显示名(供出场场景中的 pov 等回显)
  const nameOf = (cid?: string | null) =>
    cid ? (chars.find((c) => c.id === cid)?.name ?? cid) : "";

  const renderCard = (c: Character) => {
    const items = arcItems(c);
    const sentence = c.summary?.sentence ?? "";
    const opened = c.id in scenesOf;
    const proj = scenesOf[c.id];
    const showInterview = c.id in interviewing;
    const isPicked = c.id in picked;
    const dossier = c.dossier ?? [];
    // 投影宽容解析:可能是 {scenes:[...]} / [...] / {sequence:[...]}
    const scenes: any[] = Array.isArray(proj)
      ? proj
      : (proj?.scenes ?? proj?.sequence ?? []);
    // 多选模式下点卡 = 勾选;否则 = 打开检查器
    const onCardClick = () => (multi ? togglePick(c.id) : s.select("characters", c.id));
    return (
      <div
        className="card clickable"
        key={c.id}
        onClick={onCardClick}
        style={multi && isPicked ? { borderColor: "var(--accent)", boxShadow: "0 0 0 1px var(--accent)" } : undefined}
      >
        <div className="row">
          {multi && (
            <input
              type="checkbox"
              checked={isPicked}
              style={{ width: "auto" }}
              onClick={(e) => e.stopPropagation()}
              onChange={() => togglePick(c.id)}
            />
          )}
          {/* ① 点 status-dot 循环切状态 */}
          <span
            className={`status-dot status-${c.status}`}
            title={`状态:${c.status}(点击切换)`}
            style={{ cursor: "pointer" }}
            onClick={(e) => cycleStatus(c, e)}
          />
          <b>{c.name}</b>
          <span className="chip small">{c.importance}</span>
          <span className="grow" style={{ flex: 1 }} />
          {/* ③ 参考图缩略图 */}
          {c.image && (
            <img
              src={c.image}
              alt={c.name}
              style={{ width: 28, height: 28, borderRadius: 4, objectFit: "cover", border: "1px solid var(--border)" }}
              onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
            />
          )}
        </div>
        {sentence && <p className="small" style={{ margin: "6px 0" }}>{sentence}</p>}
        {items.length > 0 && (
          <div className="grid small" style={{ gridTemplateColumns: "auto 1fr", marginTop: 4 }}>
            {items.map((it) => (
              <React.Fragment key={it.k}>
                <span className="muted">{it.label}</span>
                <span>{it.v}</span>
              </React.Fragment>
            ))}
          </div>
        )}

        <div className="row wrap" style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
          <button onClick={() => toggleScenes(c.id)}>
            {opened ? "收起出场场景" : "出场场景"}
          </button>
          {/* ② 采访 */}
          <button onClick={() => toggleInterview(c.id)}>
            {showInterview ? "收起采访" : "采访"}
          </button>
        </div>

        {/* ③ 参考图 URL 输入 */}
        <div className="field" style={{ marginTop: 8, marginBottom: 0 }} onClick={(e) => e.stopPropagation()}>
          <label>参考图 URL</label>
          <input
            className="small"
            defaultValue={c.image ?? ""}
            placeholder="粘贴图片链接,失焦保存"
            onBlur={(e) => setImage(c, e.target.value)}
          />
        </div>

        {/* ② 采访展开:逐维度 freetext 编辑 */}
        {showInterview && (
          <div style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
            {dossier.length === 0 && (
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted small">还没有采访维度。</span>
                <button className="small" onClick={() => addDimension(c)}>+维度</button>
              </div>
            )}
            {dossier.map((d, idx) => (
              <div className="field" key={idx} style={{ marginBottom: 8 }}>
                <label>
                  {d.dimension}
                  <span className="muted"> · {d.mode}</span>
                </label>
                <textarea
                  className="small"
                  defaultValue={d.freetext ?? ""}
                  placeholder={`关于「${d.dimension}」的自由记述,失焦保存`}
                  onBlur={(e) => setDossierFreetext(c, idx, e.target.value)}
                />
              </div>
            ))}
            {dossier.length > 0 && (
              <button className="small" onClick={() => addDimension(c)}>+维度</button>
            )}
          </div>
        )}

        {opened && (
          <div style={{ marginTop: 6 }} onClick={(e) => e.stopPropagation()}>
            {scenes.length === 0 && <p className="muted small">无出场场景</p>}
            <div className="wrap">
              {scenes.map((sc: any, i: number) => {
                const sid = sc?.id ?? sc?.scene_id ?? sc?.scene_ref ?? String(sc);
                const title = sc?.title ?? sc?.intent_summary ?? sid;
                const pov = sc?.links?.pov ?? sc?.pov;
                return (
                  <span
                    key={sid + "_" + i}
                    className="chip click small"
                    title={pov ? "POV: " + nameOf(pov) : undefined}
                    onClick={() => s.jumpTo("scenes", sid)}
                  >
                    {i + 1}. {title}
                  </span>
                );
              })}
            </div>
          </div>
        )}
      </div>
    );
  };

  // other 分组只在有内容时显示
  const groups = [...GROUPS, { key: "other", label: "其他" }];

  return (
    <div>
      <div className="row">
        <h2 className="view-h">角色</h2>
        <span className="grow" style={{ flex: 1 }} />
        {/* ④ 多选模式开关 */}
        {multi ? (
          <button onClick={exitMulti}>退出多选</button>
        ) : (
          <button onClick={() => setMulti(true)}>多选</button>
        )}
        <button className="primary" onClick={addCharacter}>新增角色</button>
      </div>
      <p className="view-sub">按重要度分组的人物档案 —— 点卡片编辑,点 status 圆点切状态,"采访"按维度记述,可填参考图。</p>

      {chars.length === 0 && <p className="muted">还没有角色,点右上角新增。</p>}

      {groups.map((g) => {
        const members = chars.filter((c) => groupOf(c) === g.key);
        if (members.length === 0) return null;
        return (
          <div key={g.key} style={{ marginBottom: 16 }}>
            <div className="nav-like muted small" style={{ textTransform: "uppercase", letterSpacing: ".5px", margin: "6px 0" }}>
              {g.label}（{members.length}）
            </div>
            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
              {members.map(renderCard)}
            </div>
          </div>
        );
      })}

      {/* ④ 底部批量条 */}
      {multi && pickedIds.length > 0 && (
        <div
          className="card row wrap"
          style={{
            position: "sticky", bottom: 0, marginTop: 12,
            background: "var(--panel)", borderColor: "var(--accent)", zIndex: 10,
          }}
        >
          <b>已选 {pickedIds.length} 个角色</b>
          <span className="grow" style={{ flex: 1 }} />
          <span className="muted small">批量改状态:</span>
          <button disabled={busy} onClick={() => batchStatus("todo")}>todo</button>
          <button disabled={busy} onClick={() => batchStatus("tocomplete")}>tocomplete</button>
          <button disabled={busy} onClick={() => batchStatus("done")}>done</button>
          <button disabled={busy} onClick={() => setPicked({})}>清空</button>
        </div>
      )}
    </div>
  );
}
