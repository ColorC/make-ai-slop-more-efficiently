import { create } from 'zustand'
import type { TurnCanvasRef } from '../../lib/turnResults'

type CompanionPage = 'multiagent' | 'notes' | 'results' | 'studio'

interface CompanionNavState {
  sessionId: string | null
  page: CompanionPage | null
  /** 结果页要直接选中的画布(终端里点了画布链接时带上)。 */
  canvas: TurnCanvasRef | null
  nonce: number
  open: (sessionId: string, page: CompanionPage, canvas?: TurnCanvasRef | null) => void
}

export const useCompanionNav = create<CompanionNavState>((set) => ({
  sessionId: null,
  page: null,
  canvas: null,
  nonce: 0,
  open: (sessionId, page, canvas = null) => set((state) => ({ sessionId, page, canvas, nonce: state.nonce + 1 })),
}))
