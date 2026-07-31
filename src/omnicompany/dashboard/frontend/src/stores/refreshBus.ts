// 全局刷新总线 — 顶栏"刷新"按钮按下时广播, 各数据面板(项目工作板/侧栏项目面板等)订阅强刷。
// 2026-06-12 用户实测: 首页换成项目工作板后, 顶栏刷新只刷简报(不可见), 看起来"点了没用"。
import { create } from 'zustand'

interface RefreshBus {
  nonce: number
  bump: () => void
}

export const useRefreshBus = create<RefreshBus>((set) => ({
  nonce: 0,
  bump: () => set((s) => ({ nonce: s.nonce + 1 })),
}))
