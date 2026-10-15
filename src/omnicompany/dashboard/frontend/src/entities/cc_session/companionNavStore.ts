import { create } from 'zustand'

type CompanionPage = 'multiagent' | 'overview' | 'notes' | 'studio'

interface CompanionNavState {
  sessionId: string | null
  page: CompanionPage | null
  nonce: number
  open: (sessionId: string, page: CompanionPage) => void
}

export const useCompanionNav = create<CompanionNavState>((set) => ({
  sessionId: null,
  page: null,
  nonce: 0,
  open: (sessionId, page) => set((state) => ({ sessionId, page, nonce: state.nonce + 1 })),
}))
