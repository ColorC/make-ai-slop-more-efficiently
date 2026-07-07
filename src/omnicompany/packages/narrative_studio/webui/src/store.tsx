// 全局状态中枢:单一真源(project)+ 选中实体(驱动检查器)+ 编辑动作。
import React, { createContext, useContext, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Project } from "./types";

export interface Selection { carrier: string; id: string; }

interface StudioState {
  project: Project | null;
  loading: boolean;
  error: string | null;
  selection: Selection | null;
  reload: () => Promise<void>;
  select: (carrier: string, id: string) => void;
  clearSelection: () => void;
  updateEntity: (carrier: string, id: string, patch: any) => Promise<void>;
  createEntity: (carrier: string, entity: any) => Promise<any>;
  deleteEntity: (carrier: string, id: string) => Promise<void>;
  // 视图切换 / 跨视图跳转
  activeView: string;
  setActiveView: (v: string) => void;
  // 跳到某实体:切到它的载体视图并选中(万物可点即跳转)
  jumpTo: (carrier: string, id: string, view?: string) => void;
}

const Ctx = createContext<StudioState | null>(null);

// 载体 → 默认视图(用于 jumpTo)
const CARRIER_VIEW: Record<string, string> = {
  premise: "premise", reveal_layers: "reveal", world: "world",
  characters: "characters", relationships: "relationships",
  variables: "variables", stat_blocks: "variables", pressures: "variables",
  failure_levels: "variables", meta_progress: "variables",
  beats: "structure", storylines: "structure", pacing: "structure",
  nodes: "engine", connections: "engine", endings: "engine",
  scenes: "plot", prose_lines: "plot",
  tags: "tags", notes: "background", voices: "style", registers: "style", style_matrix: "style",
  // v2 四层新载体
  audience: "audience", background: "background",
  game_texts: "gametext", comments: "comments", rejected_archive: "archive",
};

// 网址直达:#view=structure 或 ?view=structure(审阅台 iframe / 推送链接用;hash 优先,不经服务器)
function viewFromUrl(): string | null {
  try {
    const h = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const q = new URLSearchParams(window.location.search);
    return h.get("view") || q.get("view");
  } catch {
    return null;
  }
}

export function StudioProvider({ children }: { children: React.ReactNode }) {
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [activeView, setActiveViewState] = useState<string>(() => viewFromUrl() ?? "premise");

  // 切视图时把 #view= 写回网址,当前页面随时可复制成直达链接
  const setActiveView = useCallback((v: string) => {
    setActiveViewState(v);
    try { window.history.replaceState(null, "", `#view=${v}`); } catch { /* iframe 沙箱等场景忽略 */ }
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const p = await api.getProject();
      setProject(p);
      setError(null);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const select = useCallback((carrier: string, id: string) => setSelection({ carrier, id }), []);
  const clearSelection = useCallback(() => setSelection(null), []);

  const updateEntity = useCallback(async (carrier: string, id: string, patch: any) => {
    await api.updateEntity(carrier, id, patch);
    await reload();
  }, [reload]);

  const createEntity = useCallback(async (carrier: string, entity: any) => {
    const r = await api.createEntity(carrier, entity);
    await reload();
    return r;
  }, [reload]);

  const deleteEntity = useCallback(async (carrier: string, id: string) => {
    await api.deleteEntity(carrier, id);
    setSelection((s) => (s && s.carrier === carrier && s.id === id ? null : s));
    await reload();
  }, [reload]);

  const jumpTo = useCallback((carrier: string, id: string, view?: string) => {
    setActiveView(view ?? CARRIER_VIEW[carrier] ?? activeView);
    setSelection({ carrier, id });
  }, [activeView]);

  const value = useMemo<StudioState>(() => ({
    project, loading, error, selection,
    reload, select, clearSelection, updateEntity, createEntity, deleteEntity,
    activeView, setActiveView, jumpTo,
  }), [project, loading, error, selection, reload, select, clearSelection, updateEntity, createEntity, deleteEntity, activeView, jumpTo]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStudio(): StudioState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useStudio must be used within StudioProvider");
  return v;
}
