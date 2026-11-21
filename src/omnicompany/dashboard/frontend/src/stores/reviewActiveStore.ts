/**
 * stores/reviewActiveStore — 审阅"激活材料 + 待写锚点"的跨区共享真源(三区化)。
 *
 * 三个语义区(队列 A / 材料 B / 评论 C)都读这里的 activeMaterialId 对齐看同一条材料。
 * 内部默认形态(三区同一个 webview)直接共享本 store 即时联动;
 * 切到 VSCode 原生形态后三区是不同 webview, 见 setActiveMaterial 的后端广播(工作流 E):
 * setActiveMaterial 既写本地、又 POST /api/boss-sight/reviewstage/active, 后端在审阅 WS 流上
 * 回广播 active_material 事件, 别的 webview 经 streamStore 把 id 写回这里(setActiveMaterialLocal,
 * 不再回 POST, 避免回环)。
 *
 * pendingAnchor: B 区(正文)圈选一段文字 → 写这里 → C 区(评论)追加框接锚点。评论独立成区后
 * 锚点不能再藏在 MaterialDetail 局部 state, 必须放共享 store 才能跨区(乃至跨 webview)传。
 */
import { create } from 'zustand'
import { reviewstageApi } from '../api/reviewstageClient'

export const REVIEW_COMMENTS_OPEN_EVENT = 'omni:open-review-comments'

export function requestReviewCommentsOpen(materialId: string) {
  window.dispatchEvent(new CustomEvent(REVIEW_COMMENTS_OPEN_EVENT, { detail: { materialId } }))
}

interface ReviewActiveState {
  activeMaterialId: string | null
  pendingAnchor: string | null
  pendingAnchorMaterialId: string | null
  /** 选中材料: 写本地 + 广播给别的表面(经后端)。origin='remote' 时只写本地, 不回广播。 */
  setActiveMaterial: (id: string | null, origin?: 'local' | 'remote') => void
  setPendingAnchor: (text: string | null, materialId?: string | null) => void
  clearPendingAnchor: () => void
}

export const useReviewActive = create<ReviewActiveState>((set, get) => ({
  activeMaterialId: null,
  pendingAnchor: null,
  pendingAnchorMaterialId: null,
  setActiveMaterial: (id, origin = 'local') => {
    if (get().activeMaterialId === id) return
    set({
      activeMaterialId: id,
      pendingAnchor: null,
      pendingAnchorMaterialId: null,
    })
    if (origin === 'local' && id) {
      // 跨 webview 联动: 通知后端, 后端在 WS 流上回广播 active_material(本 webview 自己忽略回环)。
      void reviewstageApi.setActiveMaterial?.(id).catch(() => { /* 单表面/后端老版本: 静默, 本地仍生效 */ })
    }
  },
  setPendingAnchor: (text, materialId) => set({
    pendingAnchor: text ? text.slice(0, 200) : null,
    pendingAnchorMaterialId: text ? materialId || get().activeMaterialId : null,
  }),
  clearPendingAnchor: () => set({
    pendingAnchor: null,
    pendingAnchorMaterialId: null,
  }),
}))
