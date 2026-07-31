import { create } from 'zustand'

/**
 * 审阅队列"当前聚焦的材料 id" —— 跟 tab 身份解耦。
 *
 * 为什么: 审阅队列是单例 tab(review_queue:main)。以前点不同材料链接会把 facet 拼进 tab id,
 * 每个材料开一个新 tab(用户反馈: 每次点链接都新建一个审阅队列 tab)。现在 tab 永远是同一个,
 * "聚焦哪条材料"走这个 store, 点链接只更新聚焦 id, 不再新开 tab。
 */
export const useReviewQueueFocus = create<{
  focusedId: string | null
  /** 自增令牌: 即便聚焦同一个 id 再次点击也能触发一次"重新聚焦"。 */
  nonce: number
  setFocused: (id: string | null) => void
}>((set) => ({
  focusedId: null,
  nonce: 0,
  setFocused: (id) => set((s) => ({ focusedId: id, nonce: s.nonce + 1 })),
}))
