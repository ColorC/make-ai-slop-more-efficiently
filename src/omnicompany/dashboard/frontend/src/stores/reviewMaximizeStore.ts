import { create } from 'zustand'
import type { DockviewApi } from 'dockview'

// 全屏审阅(maximize)状态。触发点在 dockview 的页签(EditorArea), 需要隐藏的外壳
// (左栏/底栏/页签条/顶栏退出键)在 CockpitShell —— 两端都订阅这个 store 保持同步。
export type TabMenuState = { x: number; y: number; tabId: string; title: string }

type ReviewMaximizeState = {
  /** dockview onReady 时登记, 让 store 的动作能直接操作面板。 */
  api: DockviewApi | null
  /** 当前被最大化的页签 id; null 表示未进入全屏审阅。 */
  maximizedTabId: string | null
  /** 右键页签弹出的上下文菜单; null 表示未打开。 */
  tabMenu: TabMenuState | null
  registerApi: (api: DockviewApi | null) => void
  openTabMenu: (menu: TabMenuState) => void
  closeTabMenu: () => void
  maximize: (tabId: string) => void
  maximizeActive: () => void
  exit: () => void
  /** dockview 自身的最大化状态变化(关页/拖拽等)后回收, 避免外壳卡在"已隐藏但无最大化面板"。 */
  syncFromDockview: () => void
}

export const useReviewMaximize = create<ReviewMaximizeState>((set, get) => ({
  api: null,
  maximizedTabId: null,
  tabMenu: null,
  registerApi: (api) => set({ api }),
  openTabMenu: (tabMenu) => set({ tabMenu }),
  closeTabMenu: () => set({ tabMenu: null }),
  maximize: (tabId) => {
    const panel = get().api?.getPanel(tabId)
    if (!panel) return
    panel.api.setActive()
    panel.api.maximize()
    set({ maximizedTabId: tabId, tabMenu: null })
  },
  // 给面板内部的"全屏"按钮用: 直接最大化当前活动页签(点按钮时该页签即活动), 不必知道自己的 id。
  maximizeActive: () => {
    const panel = get().api?.activePanel
    if (!panel) return
    panel.api.maximize()
    set({ maximizedTabId: panel.id, tabMenu: null })
  },
  exit: () => {
    const { api } = get()
    if (api?.hasMaximizedGroup()) api.exitMaximizedGroup()
    set({ maximizedTabId: null, tabMenu: null })
  },
  syncFromDockview: () => {
    const { api, maximizedTabId } = get()
    if (!api) return
    if (!api.hasMaximizedGroup() && maximizedTabId !== null) set({ maximizedTabId: null })
  },
}))
