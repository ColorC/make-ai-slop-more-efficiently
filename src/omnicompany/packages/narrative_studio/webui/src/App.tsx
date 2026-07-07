import React, { useEffect, useState } from "react";
import { useStudio } from "./store";
import { api } from "./api";
import { VIEWS, VIEW_GROUPS } from "./views";
import { Inspector } from "./components/Inspector";
import { CommandPalette } from "./components/CommandPalette";
import { SearchPanel } from "./components/SearchPanel";
import { ReplacePanel } from "./components/ReplacePanel";
import type { HealthIssue } from "./types";

export function App() {
  const s = useStudio();
  const [palette, setPalette] = useState(false);
  const [search, setSearch] = useState(false);
  const [replace, setReplace] = useState(false);
  const [health, setHealth] = useState<HealthIssue[]>([]);
  const [versions, setVersions] = useState<string[]>([]);
  // 工具组默认收起,不和四层内容争注意力
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({ tools: true });

  const refreshMeters = React.useCallback(() => {
    api.health().then(setHealth).catch(() => {});
    api.versions().then(setVersions).catch(() => {});
  }, []);
  useEffect(() => { if (s.project) refreshMeters(); }, [s.project, refreshMeters]);

  const undoLast = React.useCallback(async () => {
    const h = await api.history().catch(() => [] as string[]);
    if (h.length) { await api.restoreHistory(h[0]); await s.reload(); refreshMeters(); }
  }, [s, refreshMeters]);

  const onVersion = React.useCallback(async (v: string) => {
    if (!v) return;
    if (v === "__save") {
      const name = prompt("存为版本名(如 旧版-00-12 / 新版-alters):");
      if (name) { await api.saveVersion(name); api.versions().then(setVersions).catch(() => {}); }
    } else if (v === "__diff") {
      s.setActiveView("diff");
    } else if (confirm(`切换到版本「${v}」?当前态会自动留底,可经修订历史还原。`)) {
      await api.activateVersion(v); await s.reload(); refreshMeters();
    }
  }, [s, refreshMeters]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "p") { e.preventDefault(); setPalette(true); }
      if ((e.ctrlKey || e.metaKey) && k === "f") { e.preventDefault(); setSearch(true); }
      if ((e.ctrlKey || e.metaKey) && k === "h") { e.preventDefault(); setReplace(true); }
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && k === "z") { e.preventDefault(); void undoLast(); }
      if (e.key === "Escape") { setPalette(false); setSearch(false); setReplace(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undoLast]);

  if (s.loading) return <div className="center">加载中…</div>;
  if (s.error) return <div className="center"><p className="sev-high">后端连接失败:{s.error}</p><p className="muted">确认后端在跑:<code>python -m omnicompany.packages.narrative_studio</code></p></div>;
  if (!s.project) return <div className="center">无项目</div>;

  const highCount = health.filter((h) => h.severity === "high").length;
  // 网址带了不存在的 view 参数时回落到立意页,不给用户看"视图缺失"
  const ActiveView = (VIEWS.find((v) => v.id === s.activeView) ?? VIEWS.find((v) => v.id === "premise"))?.Component;
  const counts = countByCarrier(s.project);

  return (
    <div className="app">
      {/* 退役标记(统一设计工作室 v2 四期 D6, DEC-2026-07-05-025/030): 本网页壳已退役,
          生产入口=驾驶舱阅读视图(叙事展示区); dashboard 已不再托管本页(仅 dev 模式可见)。 */}
      <div style={{ background: "#5a4a12", color: "#ffd97a", padding: "6px 12px", fontSize: 12, textAlign: "center" }}>
        本页已退役:浏览/审阅走驾驶舱「项目 vilo → 阅读视图」;结构化编辑走内容引擎 API。此壳仅源码留档(渲染器画法出处)。
      </div>
      {/* 顶栏只留真正需要常驻的:全局命令(=全局右键,Ctrl-P 搜任意实体/视图,也是搜索/替换入口)+ 刷新;
          仅在有"严重"结构问题时亮一个红点。完成度/健康的详情都进左侧"工具"组,不在顶栏堆数字。 */}
      <div className="topbar">
        <span className="title">叙事工作室</span>
        <span className="pill">{s.project.meta.name} · {s.project.meta.version}</span>
        <span className="grow" />
        <button onClick={() => setPalette(true)} title="Ctrl-P:搜任意实体/视图并跳转(搜索 Ctrl-F / 替换 Ctrl-H / 撤销 Ctrl-Z)">⌘ 命令 / 跳转</button>
        {highCount > 0 && (
          <span className="badge err" style={{ cursor: "pointer" }} onClick={() => s.setActiveView("health")}
            title="有严重结构问题,点开看健康检查">⚠ {highCount}</span>
        )}
        <button onClick={() => { s.reload(); refreshMeters(); }} title="刷新">↻</button>
      </div>

      <div className="body">
        <nav className="nav">
          {VIEW_GROUPS.map((g) => {
            const items = VIEWS.filter((v) => v.group === g.id && v.nav !== false);
            if (!items.length) return null;
            const isCollapsed = !!g.collapsible && collapsed[g.id];
            return (
              <div key={g.label}>
                <div
                  className="group"
                  style={g.collapsible ? { cursor: "pointer", userSelect: "none" } : undefined}
                  onClick={g.collapsible ? () => setCollapsed((c) => ({ ...c, [g.id]: !c[g.id] })) : undefined}
                >
                  {g.collapsible ? (isCollapsed ? "▸ " : "▾ ") : ""}{g.label}
                </div>
                {!isCollapsed && items.map((v) => (
                  <div
                    key={v.id}
                    className={"item" + (s.activeView === v.id ? " active" : "")}
                    onClick={() => s.setActiveView(v.id)}
                  >
                    <span>{v.label}</span>
                    {counts[v.countKey ?? ""] != null && <span className="count">{counts[v.countKey!]}</span>}
                  </div>
                ))}
              </div>
            );
          })}
          {/* 版本管理:低频写操作,放导航底部,不再常驻顶栏 */}
          <div style={{ marginTop: "auto", paddingTop: 10 }}>
            <div className="group">版本</div>
            <select value="" onChange={(e) => onVersion(e.target.value)} title="切换/存为版本·版本对照"
              style={{ width: "100%" }}>
              <option value="">版本 / 变体…</option>
              {versions.map((v) => <option key={v} value={v}>切到 {v}</option>)}
              <option value="__save">+ 存为版本</option>
              <option value="__diff">⇄ 版本对照</option>
            </select>
          </div>
        </nav>

        <main className="center">
          {ActiveView ? <ActiveView onChanged={refreshMeters} /> : <div className="muted">视图缺失:{s.activeView}</div>}
        </main>

        {s.selection && (
          <aside className="inspector">
            <Inspector onChanged={refreshMeters} />
          </aside>
        )}
      </div>

      {palette && <CommandPalette onClose={() => setPalette(false)} />}
      {search && <SearchPanel onClose={() => setSearch(false)} />}
      {replace && <ReplacePanel onClose={() => setReplace(false)} onChanged={() => { s.reload(); refreshMeters(); }} />}
    </div>
  );
}

function countByCarrier(p: any): Record<string, number> {
  const c: Record<string, number> = {};
  for (const k of Object.keys(p)) if (Array.isArray(p[k])) c[k] = p[k].length;
  return c;
}
