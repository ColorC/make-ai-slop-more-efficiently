/**
 * panelHydration —— 常驻页签的**分批水合**闸门。
 *
 * 所有页签都 `always` 常驻之后, 一个新问题立刻冒出来: 刷新驾驶舱时十几个页签会
 * **同时**解析实体、下载懒 chunk、渲染整屏卡片。那不是"像浏览器一样开十几个标签页",
 * 那是"十几个标签页在同一帧里一起加载" —— 首屏会被后台页签的活儿挤掉。
 *
 * 浏览器恢复会话时的做法是有优先级、有限并发地把背景标签页慢慢喂进来。这里照做:
 *
 *   · **可见**的面板立刻水合(用户正在看它, 不排队, 也不受并发上限约束);
 *   · **隐藏**的面板进队列, 在浏览器空闲时按 PANEL_HYDRATION_CONCURRENCY 个一批放行;
 *   · 面板自己在实体解析完/失败后调用 settle() 让出名额;
 *   · 万一某个面板永远不 settle(接口挂住), watchdog 到时强行让出, 队列不会卡死。
 *
 * 水合是**单向**的: 一旦放行就永久 hydrated, 之后切走切回都不会再回到骨架 ——
 * 这正是"切换页签不再加载"的落点。
 */

import React from 'react'
import { reportFrontendEvent } from '../lib/frontendTelemetry'

/** tab.id 由 panelsStore 拼成 `${ref.type}:${ref.id}`(见 panelsStore.ts tabIdFor)。
 *  只对会话终端上报排队时长, 携带真实 session_id —— 其余面板类型没有"会话"语义。 */
const CC_SESSION_TAB_PREFIX = 'cc_session:'

function reportPanelHydrationWait(tabId: string, requestedAt: number | null, wasQueued: boolean): void {
  if (!tabId.startsWith(CC_SESSION_TAB_PREFIX)) return
  reportFrontendEvent('panel_hydration_wait', 'terminal panel hydration wait', {
    session_id: tabId.slice(CC_SESSION_TAB_PREFIX.length),
    wait_ms: requestedAt !== null ? Math.round(performance.now() - requestedAt) : null,
    queued: wasQueued,
  })
}

/** 后台页签同时水合的上限。组件内部还会继续发请求, 因此这里只放一扇门。 */
export const PANEL_HYDRATION_CONCURRENCY = 1
/** 面板迟迟不 settle 时强行让出名额的时限。 */
export const PANEL_HYDRATION_SETTLE_TIMEOUT_MS = 5_000
/** requestIdleCallback 的兜底时限: 主线程一直忙也要在这之后放行下一批。 */
export const PANEL_HYDRATION_IDLE_TIMEOUT_MS = 700
/** 冷启动先完整让路给当前页；切到排队页仍由 promote() 立即放行。 */
export const PANEL_HYDRATION_MIN_DELAY_MS = 2_500

/** 取消函数; 调用后这次调度不再触发。 */
export type CancelScheduled = () => void
export type IdleScheduler = (task: () => void) => CancelScheduled

const defaultScheduler: IdleScheduler = (task) => {
  let idleHandle: number | null = null
  let cancelled = false
  const timer = setTimeout(() => {
    if (cancelled) return
    const idle = (globalThis as { requestIdleCallback?: (cb: () => void, options?: { timeout: number }) => number })
      .requestIdleCallback
    if (typeof idle === 'function') {
      idleHandle = idle(task, { timeout: PANEL_HYDRATION_IDLE_TIMEOUT_MS })
    } else {
      task()
    }
  }, PANEL_HYDRATION_MIN_DELAY_MS)
  return () => {
    cancelled = true
    clearTimeout(timer)
    if (idleHandle !== null) {
      const cancel = (globalThis as { cancelIdleCallback?: (handle: number) => void }).cancelIdleCallback
      cancel?.(idleHandle)
    }
  }
}

interface WaitingEntry {
  hydrate: () => void
}

export interface PanelHydrationQueueOptions {
  scheduler?: IdleScheduler
  concurrency?: number
  settleTimeoutMs?: number
  /** 测试注入: 默认用 setTimeout。 */
  setTimer?: (task: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
}

export class PanelHydrationQueue {
  private readonly scheduler: IdleScheduler
  private readonly concurrency: number
  private readonly settleTimeoutMs: number
  private readonly setTimer: (task: () => void, ms: number) => unknown
  private readonly clearTimer: (handle: unknown) => void

  /** 隐藏面板的等待队列(插入序 = 放行序)。 */
  private readonly waiting = new Map<string, WaitingEntry>()
  /** 已放行但还没 settle 的面板 → 它的 watchdog 句柄。 */
  private readonly running = new Map<string, unknown>()
  private pumpCancel: CancelScheduled | null = null

  constructor(options: PanelHydrationQueueOptions = {}) {
    this.scheduler = options.scheduler ?? defaultScheduler
    this.concurrency = Math.max(1, options.concurrency ?? PANEL_HYDRATION_CONCURRENCY)
    this.settleTimeoutMs = options.settleTimeoutMs ?? PANEL_HYDRATION_SETTLE_TIMEOUT_MS
    this.setTimer = options.setTimer ?? ((task, ms) => setTimeout(task, ms))
    this.clearTimer = options.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>))
  }

  /** 队列长度(诊断/测试用)。 */
  get pending(): number {
    return this.waiting.size
  }

  /** 已放行未 settle 的数量(诊断/测试用)。 */
  get inFlight(): number {
    return this.running.size
  }

  /**
   * 登记一个待水合面板。可见的当场放行; 隐藏的排队。
   * 返回 release 函数: 面板卸载时调用, 同时清掉排队与名额。
   */
  request(id: string, visible: boolean, hydrate: () => void): CancelScheduled {
    if (visible) {
      this.start(id, hydrate)
      return () => this.release(id)
    }
    this.waiting.set(id, { hydrate })
    this.schedulePump()
    return () => this.release(id)
  }

  /** 面板变可见: 插队立刻放行(用户正在看它, 不该再等空闲)。 */
  promote(id: string): void {
    const entry = this.waiting.get(id)
    if (!entry) return
    this.waiting.delete(id)
    this.start(id, entry.hydrate)
  }

  /** 面板已完成首轮加载, 让出名额。 */
  settle(id: string): void {
    if (!this.running.has(id)) return
    this.clearTimer(this.running.get(id))
    this.running.delete(id)
    this.schedulePump()
  }

  /** 面板卸载(页签关闭): 排队与名额一并清掉。 */
  release(id: string): void {
    this.waiting.delete(id)
    this.settle(id)
    if (this.waiting.size === 0 && this.running.size === 0) this.cancelPump()
  }

  private start(id: string, hydrate: () => void): void {
    if (this.running.has(id)) return
    const watchdog = this.setTimer(() => {
      // 迟迟不 settle: 也许接口挂住了, 也许面板压根不上报。放行下一批, 别拖累别人。
      this.running.delete(id)
      this.schedulePump()
    }, this.settleTimeoutMs)
    this.running.set(id, watchdog)
    hydrate()
  }

  private schedulePump(): void {
    if (this.pumpCancel || this.waiting.size === 0) return
    this.pumpCancel = this.scheduler(() => {
      this.pumpCancel = null
      this.pump()
    })
  }

  private cancelPump(): void {
    this.pumpCancel?.()
    this.pumpCancel = null
  }

  private pump(): void {
    while (this.running.size < this.concurrency && this.waiting.size > 0) {
      const [id, entry] = this.waiting.entries().next().value as [string, WaitingEntry]
      this.waiting.delete(id)
      this.start(id, entry.hydrate)
    }
    if (this.waiting.size > 0) this.schedulePump()
  }
}

export const panelHydrationQueue = new PanelHydrationQueue()

export interface PanelHydration {
  /** true 之后永不回退 —— 面板内容从此常驻。 */
  hydrated: boolean
  /** 首轮加载结束(成功或失败)时调用, 让出并发名额。多次调用只算一次。 */
  settle: () => void
}

/**
 * 面板水合闸门。可见面板立刻返回 hydrated=true; 隐藏面板等队列放行。
 * hydrated 一旦为 true 就不再变回 false, 所以调用方可以放心把它当"内容已挂载"用。
 */
export function usePanelHydration(
  tabId: string,
  visible: boolean,
  queue: PanelHydrationQueue = panelHydrationQueue,
): PanelHydration {
  const [hydrated, setHydrated] = React.useState(false)
  const visibleRef = React.useRef(visible)
  visibleRef.current = visible
  const settledRef = React.useRef(false)

  // ⚠ deps 里**不能有 hydrated**: 可见面板会在 request 内同步 hydrate,
  // 若 hydrated 在 deps 里, 这个 effect 立刻 cleanup → release → 名额刚占上就还回去,
  // 后台队列会误以为没人在忙, 把所有隐藏页签一次放行 —— 分批水合就白做了。
  React.useEffect(() => {
    let cancelled = false
    // panel_hydration_wait 埋点: 面板挂载(request 发起)到真正放行(hydrate 回调触发)之间
    // 的等待 —— 可见面板走 start() 同步放行, 这里量出的就是 ~0ms; 隐藏面板排队等 idle
    // 放行, 这里量出的才是真实排队时长(串行 concurrency=1 的可观测代价)。
    const requestedAt = performance.now()
    const wasQueued = !visibleRef.current
    const release = queue.request(tabId, visibleRef.current, () => {
      if (!cancelled) {
        setHydrated(true)
        reportPanelHydrationWait(tabId, requestedAt, wasQueued)
      }
    })
    return () => {
      cancelled = true
      release()
    }
  }, [queue, tabId])

  React.useEffect(() => {
    if (visible) queue.promote(tabId)
  }, [queue, tabId, visible])

  const settle = React.useCallback(() => {
    if (settledRef.current) return
    settledRef.current = true
    queue.settle(tabId)
  }, [queue, tabId])

  return { hydrated, settle }
}
