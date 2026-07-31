import { create } from 'zustand'
import type { EntityRef } from '../entities/types'
import { useReviewQueueFocus } from './reviewQueueFocusStore'

export type DockDirection = 'left' | 'right' | 'above' | 'below'

export interface DockPlacement {
  direction: DockDirection
  referenceTabId?: string
}

export interface OpenedTab {
  id: string
  ref: EntityRef
  facet?: string
  title: string
  pinned?: boolean
  placement?: DockPlacement
}

interface PanelsState {
  tabs: OpenedTab[]
  activeId: string | null
  openTab: (ref: EntityRef, title: string, facet?: string, placement?: DockPlacement) => string
  /** 后台打开(鼠标中键): 加 tab 但不切焦点 —— 当前视图不动, 像浏览器中键开后台页。 */
  openTabBackground: (ref: EntityRef, title: string, facet?: string, placement?: DockPlacement) => string
  requestDockPlacement: (id: string, placement: DockPlacement) => void
  clearDockPlacement: (id: string) => void
  closeTab: (id: string) => void
  activate: (id: string) => void
  renameTab: (id: string, title: string) => void
  setTabs: (tabs: OpenedTab[], activeId?: string | null) => void
}

const tabId = (ref: EntityRef, facet?: string) =>
  facet ? `${ref.type}:${ref.id}#${facet}` : `${ref.type}:${ref.id}`

export const CONTROLLER_TAB_ID = 'controller:main'

export const CONTROLLER_TAB: OpenedTab = {
  id: CONTROLLER_TAB_ID,
  ref: { type: 'controller', id: 'main' },
  title: '总控',
}

// 项目工作板仍是固定页签；2026-07-20 用户再改定：全新窗口(无快照)默认落项目工作板,
// 有快照则恢复上次退出时的页签+焦点(不再强制落总控)。
export const PROJECT_BOARD_TAB_ID = 'project_board:main'

export const PROJECT_BOARD_TAB: OpenedTab = {
  id: PROJECT_BOARD_TAB_ID,
  ref: { type: 'project_board', id: 'main' },
  title: '项目',
}

// 任务窗口 = 主区第 2 个固定页签(用户 2026-06-22: 像游戏任务窗口, 纳入所有长期任务, 配 AIGC 图)。
// 固定顺序: 项目工作板(第1, 默认首页) → 任务窗口(第2) → 总控(第3)。
export const QUEST_BOARD_TAB_ID = 'quest_board:main'

export const QUEST_BOARD_TAB: OpenedTab = {
  id: QUEST_BOARD_TAB_ID,
  ref: { type: 'quest_board', id: 'main' },
  title: '任务窗口',
}

/** Fresh windows start with these tabs; restored windows keep exactly what the user left open. */
export function withDefaultTabs(tabs: OpenedTab[]): OpenedTab[] {
  const byId = new Map<string, OpenedTab>()
  // Default order: project → quests → controller. All three remain closable.
  byId.set(PROJECT_BOARD_TAB_ID, PROJECT_BOARD_TAB)
  byId.set(QUEST_BOARD_TAB_ID, QUEST_BOARD_TAB)
  byId.set(CONTROLLER_TAB_ID, CONTROLLER_TAB)
  for (const tab of tabs) {
    byId.set(tab.id, { ...tab, pinned: false })
  }
  return Array.from(byId.values())
}

function uniqueTabs(tabs: OpenedTab[]): OpenedTab[] {
  const byId = new Map<string, OpenedTab>()
  for (const tab of tabs) byId.set(tab.id, { ...tab, pinned: false })
  return Array.from(byId.values())
}

export const usePanels = create<PanelsState>((set, get) => ({
  tabs: withDefaultTabs([]),
  // 全新窗口(无快照)默认焦点 = 项目工作板(2026-07-20 用户: 别再默认落总控)。
  activeId: PROJECT_BOARD_TAB_ID,
  openTab: (ref, title, facet, placement) => {
    // 审阅队列是单例 tab: facet(材料 id)不进 tab id, 改走聚焦 store, 避免每个材料开一个新 tab。
    if (ref.type === 'review_queue') {
      if (facet) useReviewQueueFocus.getState().setFocused(facet)
      const id = tabId(ref)
      const existing = get().tabs.find((t) => t.id === id)
      if (existing) {
        set((s) => ({
          tabs: placement ? s.tabs.map((t) => (t.id === id ? { ...t, placement } : t)) : s.tabs,
          activeId: id,
        }))
        return id
      }
      set((s) => ({ tabs: [...s.tabs, { id, ref, title, placement }], activeId: id }))
      return id
    }
    const id = tabId(ref, facet)
    const existing = get().tabs.find((t) => t.id === id)
    if (existing) {
      set((s) => ({
        tabs: placement ? s.tabs.map((t) => (t.id === id ? { ...t, placement } : t)) : s.tabs,
        activeId: id,
      }))
      return id
    }
    set((s) => ({ tabs: [...s.tabs, { id, ref, facet, title, placement }], activeId: id }))
    return id
  },
  openTabBackground: (ref, title, facet, placement) => {
    // 后台打开: 不改 activeId(当前焦点不动)。review_queue 单例同样规则。
    if (ref.type === 'review_queue' && facet) useReviewQueueFocus.getState().setFocused(facet)
    const id = ref.type === 'review_queue' ? tabId(ref) : tabId(ref, facet)
    if (get().tabs.some((t) => t.id === id)) return id // 已开则不动焦点
    const tab: OpenedTab = ref.type === 'review_queue'
      ? { id, ref, title, placement }
      : { id, ref, facet, title, placement }
    set((s) => ({ tabs: [...s.tabs, tab] })) // 注意: 不设 activeId
    return id
  },
  requestDockPlacement: (id, placement) => set((s) => ({
    tabs: s.tabs.map((t) => (t.id === id ? { ...t, placement } : t)),
    activeId: id,
  })),
  clearDockPlacement: (id) => set((s) => ({
    tabs: s.tabs.map((t) => (t.id === id && t.placement ? { ...t, placement: undefined } : t)),
  })),
  closeTab: (id) => set((s) => {
    const idx = s.tabs.findIndex((t) => t.id === id)
    if (idx < 0) return s
    const next = s.tabs.filter((t) => t.id !== id)
    let active = s.activeId
    if (s.activeId === id) {
      active = next[idx]?.id ?? next[idx - 1]?.id ?? null
    }
    return { tabs: next, activeId: active }
  }),
  activate: (id) => set({ activeId: id }),
  renameTab: (id, title) => set((s) => ({
    tabs: s.tabs.map((tab) => (tab.id === id && tab.title !== title ? { ...tab, title } : tab)),
  })),
  setTabs: (tabs, activeId) => {
    const next = uniqueTabs(tabs)
    const validActive = activeId && next.some((t) => t.id === activeId) ? activeId : next[next.length - 1]?.id
    set({ tabs: next, activeId: validActive || null })
  },
}))

// ── 窗口级页签快照 ─────────────────────────────────────────────────────────
// localStorage 在同源的多个浏览器窗口间共享，窗口 A 的最后焦点会覆盖窗口 B，
// B 刷新后因此恢复到 A 的 cc_session，看起来像「串台」。sessionStorage 在当前
// top-level browsing context 内独立且刷新后仍保留，正好对应「每个窗口恢复自己」。
// v3 不自动读取全局 v2，避免升级后的第一次刷新仍把另一个窗口的页签带进来。
export const TAB_SNAPSHOT_KEY = 'omni.cockpit.tabSnapshot.v4'

export interface TabSnapshot {
  /** Distinguishes "no snapshot yet" from an intentionally empty workspace. */
  exists: boolean
  /** 退出时的焦点页签; 老快照无此字段 = null。 */
  activeId: string | null
  tabs: OpenedTab[]
}

/** 记下当前打开的「非固定」页签(固定页签天然在, 不记) + 当前焦点页签。只存可序列化最小字段。 */
export function saveTabSnapshot(
  tabs: OpenedTab[],
  activeId?: string | null,
  storage: Storage = sessionStorage,
): void {
  try {
    const slim = tabs
      .map((t) => ({ id: t.id, ref: t.ref, facet: t.facet, title: t.title }))
    const snap: Omit<TabSnapshot, 'exists'> = { activeId: activeId || null, tabs: slim }
    storage.setItem(TAB_SNAPSHOT_KEY, JSON.stringify(snap))
  } catch { /* storage 不可用 */ }
}

/** 读当前浏览器窗口上次的页签快照(tabs + 焦点)。 */
export function loadTabSnapshot(storage: Storage = sessionStorage): TabSnapshot {
  const empty: TabSnapshot = { exists: false, activeId: null, tabs: [] }
  try {
    const raw = storage.getItem(TAB_SNAPSHOT_KEY)
    if (!raw) return empty
    const parsed = JSON.parse(raw)
    // 老格式 = 裸数组(无 activeId); 新格式 = { activeId, tabs }。
    const arr: unknown = Array.isArray(parsed) ? parsed : parsed?.tabs
    const activeId: string | null = !Array.isArray(parsed) && typeof parsed?.activeId === 'string' ? parsed.activeId : null
    if (!Array.isArray(arr)) return { exists: true, activeId, tabs: [] }
    const tabs = arr
      .filter((t: any) => t && t.id && t.ref && t.title)
      .map((t: any) => ({ id: t.id, ref: t.ref, facet: t.facet, title: t.title }))
    return { exists: true, activeId, tabs }
  } catch { return empty }
}
