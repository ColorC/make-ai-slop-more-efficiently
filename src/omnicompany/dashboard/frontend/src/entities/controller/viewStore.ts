// controller 视图切换 store(2026-07 首屏拆包, 从 index.tsx 抽出):
// CockpitShell / ProjectsPanel / ThreadMonitorPanel 都要引它 —— 留在 index.tsx 会把
// 总控的全部子视图(项目板/任务窗口/审阅总览…)经静态 import 钉进首屏主包, lazy 失效。
import { create } from 'zustand'

/** 总控默认是人↔AI 的真实对话；sessions 是 Dashboard 内原生 chat/PTY 会话入口。 */
export type ControllerView = 'project' | 'home' | 'chat' | 'sessions' | 'cron' | 'quest' | 'multiagent' | 'review'
export const useControllerView = create<{ view: ControllerView; setView: (v: ControllerView) => void }>((set) => ({
  view: 'chat',
  setView: (view) => set({ view }),
}))
