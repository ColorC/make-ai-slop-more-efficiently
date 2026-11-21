import { create } from 'zustand'

/**
 * Studio 页签会话区里开着的会话(2026-11-16 业主: 会话就在 Studio 窗口里操作, 能直接结束)。
 *
 * 放在页签组件外: Studio 页签切走会整块卸载(见 ./index.tsx), 切回来要还是那几个小页签。
 * 终端本身的内容由 PTY 宿主保管, 这里只记 id 和小页签上的字。也记进本机浏览器, 刷新后还在;
 * 刷新后已经结束的会话在解析时丢掉, 不会去恢复它。
 */
export interface StudioOpenSession {
  id: string
  /** 小页签上的字: 员工名 · 会话标题或 id 前 8 位。 */
  label: string
}

interface StudioSessionsState {
  open: StudioOpenSession[]
  activeId: string | null
  /** 每次「打开 / 切到某个会话」加一。窄布局据此把会话区盖上来。 */
  focusSeq: number
  /** 公司会话变了(结束了一个)就加一。嵌着的 Studio 页面据此刷新会话列表。 */
  sessionsVersion: number
  /** 最近结束的那个会话 id: 守护进程要过十几秒才把它记成已结束, 页面先按这个把它标成已结束。 */
  lastEnded: string | null
  openSession: (id: string, label: string) => void
  setActive: (id: string) => void
  /** 收起: 只从会话区拿掉, 会话照旧活着。 */
  collapse: (id: string) => void
  sessionsChanged: (endedId?: string) => void
}

const STORAGE_KEY = 'omni.studio.openSessions.v1'
export const STUDIO_LABEL_MAX = 48

function clampLabel(label: string, id: string): string {
  const text = label.trim() || id.slice(0, 8)
  return text.length > STUDIO_LABEL_MAX ? `${text.slice(0, STUDIO_LABEL_MAX - 1)}…` : text
}

function readSaved(): Pick<StudioSessionsState, 'open' | 'activeId'> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return { open: [], activeId: null }
    const data = JSON.parse(raw) as { open?: unknown; activeId?: unknown }
    const open = Array.isArray(data.open)
      ? data.open.filter((item): item is StudioOpenSession => (
        !!item && typeof item.id === 'string' && !!item.id && typeof item.label === 'string'
      ))
      : []
    const activeId = typeof data.activeId === 'string' && open.some((item) => item.id === data.activeId)
      ? data.activeId
      : open[0]?.id ?? null
    return { open, activeId }
  } catch {
    return { open: [], activeId: null }
  }
}

function save(state: Pick<StudioSessionsState, 'open' | 'activeId'>) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ open: state.open, activeId: state.activeId }))
  } catch { /* 存不了就只在这次页面里记着 */ }
}

export const useStudioSessions = create<StudioSessionsState>((set, get) => ({
  ...readSaved(),
  focusSeq: 0,
  sessionsVersion: 0,
  lastEnded: null,
  openSession: (id, label) => {
    const { open, focusSeq } = get()
    const existing = open.find((item) => item.id === id)
    const nextOpen = existing
      ? open.map((item) => (item.id === id && label.trim() ? { id, label: clampLabel(label, id) } : item))
      : [...open, { id, label: clampLabel(label, id) }]
    set({ open: nextOpen, activeId: id, focusSeq: focusSeq + 1 })
    save(get())
  },
  setActive: (id) => {
    if (!get().open.some((item) => item.id === id)) return
    set((state) => ({ activeId: id, focusSeq: state.focusSeq + 1 }))
    save(get())
  },
  collapse: (id) => {
    const { open, activeId } = get()
    const index = open.findIndex((item) => item.id === id)
    if (index < 0) return
    const nextOpen = open.filter((item) => item.id !== id)
    // 收起当前那个: 切到它右边的(没有就左边的), 和主区域页签的习惯一样。
    const nextActive = activeId === id ? (nextOpen[index] ?? nextOpen[index - 1])?.id ?? null : activeId
    set({ open: nextOpen, activeId: nextActive })
    save(get())
  },
  sessionsChanged: (endedId) => set((state) => ({ sessionsVersion: state.sessionsVersion + 1, lastEnded: endedId ?? null })),
}))

/** 单测用: 回到空白。 */
export function resetStudioSessions() {
  useStudioSessions.setState({ open: [], activeId: null, focusSeq: 0, sessionsVersion: 0, lastEnded: null })
  try { window.localStorage.removeItem(STORAGE_KEY) } catch { /* */ }
}
