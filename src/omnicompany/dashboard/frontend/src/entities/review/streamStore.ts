/**
 * entities/review/streamStore — 审阅台 WS 实时流的模块级 store (R3).
 *
 * 为什么在 store 不在面板组件: dockview 面板默认 renderer='onlyWhenVisible'
 * (EditorArea.tsx), 切走即卸载 —— 长连接挂在面板组件上会反复断连。
 * 连接生命周期归这里管: 引用计数, 第一个 acquire 建连接, 最后一个 release(带短暂
 * linger, 防页签快速切换抖动)关闭; 连接挂掉且仍有订阅者时自动重连(重连拿到新
 * snapshot, 自动补齐断线期间错过的事件)。
 *
 * 消费方:
 * - review_queue 列表: 订阅 version, 每个事件触发一次重拉(手动 Refresh 按钮保留)。
 * - review_material 面板: 订阅 materials[id], 事件携带完整材料直接热更新。
 * - CockpitShell: 订阅 version(拉 stats 更新浏览器标签 urgent 角标) + pushed/pushedNonce
 *   (推送 toast)。R4 起 standalone 审阅台已退役, 这里是唯一一条审阅 WS。
 */

import { create } from 'zustand'
import { reviewstageApi, type Material, type StreamEvent } from '../../api/reviewstageClient'
import { useReviewActive } from '../../stores/reviewActiveStore'

interface ReviewStreamState {
  /** 非 ping 事件自增 — 列表型订阅者(review_queue)的"该重拉了"信号。 */
  version: number
  /** 按 id 的最新材料 (snapshot 整体重建 + 增量事件合并; deleted 移除)。 */
  materials: Record<string, Material>
  connected: boolean
  /** 最近一次 pushed 事件的材料 (驾驶舱推送 toast 用)。 */
  pushed: Material | null
  /** pushed 事件自增令牌: 同一材料重复推送也能触发一次新 toast。 */
  pushedNonce: number
  /** 引用计数订阅: 返回 release(幂等)。直接当 useEffect 的 cleanup 用。 */
  acquire: () => () => void
}

const RECONNECT_DELAY_MS = 3000
/** refs 归零后延迟关闭: dockview 切页签是"旧面板卸载→新面板挂载", 不留窗口会反复断连。 */
const DISCONNECT_LINGER_MS = 1500

let refs = 0
let closeStream: (() => void) | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let lingerTimer: ReturnType<typeof setTimeout> | null = null

export const useReviewStream = create<ReviewStreamState>((set) => {
  const applyEvent = (ev: StreamEvent) => {
    if (ev.event_type === 'ping') return
    if (ev.event_type === 'active_material') {
      // 别的表面切了激活材料 → 写共享 store(origin=remote, 不回广播, 避免回环)。本表面自己发的那条
      // 在这之前已本地生效, setActiveMaterial 同 id 早退, 是幂等的。
      useReviewActive.getState().setActiveMaterial(ev.material_id, 'remote')
      return
    }
    if (ev.event_type === 'snapshot') {
      const next: Record<string, Material> = {}
      for (const m of ev.items) next[m.id] = m
      set((s) => ({ materials: next, version: s.version + 1 }))
      return
    }
    if (ev.event_type === 'deleted') {
      set((s) => {
        const next = { ...s.materials }
        delete next[ev.material.id]
        return { materials: next, version: s.version + 1 }
      })
      return
    }
    // created / updated / verdict_changed / comment_added / annotation_added / pushed
    set((s) => ({
      materials: { ...s.materials, [ev.material.id]: ev.material },
      version: s.version + 1,
      ...(ev.event_type === 'pushed'
        ? { pushed: ev.material, pushedNonce: s.pushedNonce + 1 }
        : null),
    }))
  }

  const disconnect = () => {
    const close = closeStream
    closeStream = null
    if (close) {
      try { close() } catch { /* */ }
    }
  }

  const connect = () => {
    if (closeStream || refs <= 0) return
    closeStream = reviewstageApi.openStream(applyEvent, () => {
      // 连接挂了: 收掉当前连接, 仍有订阅者就计划重连。
      disconnect()
      set({ connected: false })
      if (refs > 0 && !reconnectTimer) {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null
          connect()
        }, RECONNECT_DELAY_MS)
      }
    })
    set({ connected: true })
  }

  return {
    version: 0,
    materials: {},
    connected: false,
    pushed: null,
    pushedNonce: 0,
    acquire: () => {
      refs += 1
      if (lingerTimer) { clearTimeout(lingerTimer); lingerTimer = null }
      connect()
      let released = false
      return () => {
        if (released) return
        released = true
        refs -= 1
        if (refs > 0) return
        refs = 0
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
        if (lingerTimer) clearTimeout(lingerTimer)
        lingerTimer = setTimeout(() => {
          lingerTimer = null
          if (refs <= 0) {
            disconnect()
            set({ connected: false })
          }
        }, DISCONNECT_LINGER_MS)
      }
    },
  }
})
