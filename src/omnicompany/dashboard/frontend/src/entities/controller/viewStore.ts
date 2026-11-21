// Agent 页(原「中枢」)的视图切换 store。从 index.tsx 抽出是为了首屏拆包:
// CockpitShell / ThreadMonitorPanel 都要引它, 留在 index.tsx 会把全部子视图钉进主包。
import { create } from 'zustand'

/** 默认是人↔AI 的真实对话; sessions 是驾驶舱内原生 chat/PTY 会话入口; cron 是定时任务。
 *  内容类视图(项目板 / 任务窗口 / 审阅总览 / 最近访问)已随内容治理交给 Workshop 而退役。 */
export type ControllerView = 'chat' | 'sessions' | 'cron'
export const useControllerView = create<{ view: ControllerView; setView: (v: ControllerView) => void }>((set) => ({
  view: 'chat',
  setView: (view) => set({ view }),
}))
