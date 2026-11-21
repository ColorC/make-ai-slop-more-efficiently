import { create } from 'zustand'

export interface SessionControls {
  alive: boolean
  connected: boolean
  creating: boolean
  windowsKeys: boolean
  cwd: string
  cmd: string[]
  /**
   * Resolves only after the sibling session has been created and its tab opened.
   * Callers can therefore keep visible progress/error state instead of fire-and-forget.
   */
  newSession: () => Promise<void>
  kill: () => void
  toggleKeyMode: () => void
  selectAll: () => void
  showShortcuts: () => void
}

interface SessionControlsState {
  bySession: Record<string, SessionControls>
  register: (sessionId: string, controls: SessionControls) => void
  unregister: (sessionId: string, controls: SessionControls) => void
}

export const useSessionControls = create<SessionControlsState>((set) => ({
  bySession: {},
  register: (sessionId, controls) => set((state) => ({
    bySession: { ...state.bySession, [sessionId]: controls },
  })),
  unregister: (sessionId, controls) => set((state) => {
    if (state.bySession[sessionId] !== controls) return state
    const next = { ...state.bySession }
    delete next[sessionId]
    return { bySession: next }
  }),
}))
