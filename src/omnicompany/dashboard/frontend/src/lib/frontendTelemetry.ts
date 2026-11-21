/**
 * frontendTelemetry.ts — 前端观测上报(2026-08-16「远端画布打不开只能靠猜」的对策)。
 *
 * 服务端日志只能看到"某请求没来", 远端浏览器里的真实故障(WS 握手失败、JS 异常、
 * fetch 4xx/5xx)必须由前端自己报回来。本模块捕获:
 *   - window error / unhandledrejection
 *   - console.error(全部) / console.warn(限流)
 *   - fetch 失败(网络错误或 status≥400), 排除本上报端点自身
 *   - 结构化性能指标(LCP/CLS/INP 近似/TTFB、longtask 按分钟聚合、Chrome heap 快照)
 * 并提供 reportFrontendEvent(kind, data) 供业务主动上报(画布卡住、页面就绪等)。
 *
 * 纪律: 观测绝不能反噬页面 —— 自身异常全部吞掉; 上报失败退避重试; 单页事件量限流;
 * 不新增额外定时器/网络通道 —— 性能采集全部挂在现有 PerformanceObserver 回调、
 * 现有 60s heartbeat tick、以及现有 buffer/flush 上报机制上。
 */

const ENDPOINT = '/api/diagnostics/frontend/events'
const MAX_BUFFER = 120
const FLUSH_INTERVAL_MS = 2500
const MAX_EVENTS_PER_FLUSH = 40
const HEARTBEAT_INTERVAL_MS = 60_000
export const RETRY_BASE_MS = FLUSH_INTERVAL_MS
export const RETRY_MAX_MS = 60_000

export const TELEMETRY_INSTANCE_KEY = 'omni.session.instanceId'
export const ATTRIBUTION_INSTANCE_HEADER = 'X-Omni-Instance'
export const ATTRIBUTION_SURFACE_HEADER = 'X-Omni-Surface'

function instanceId(): string {
  const fresh = (): string => {
    const raw = (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}${Math.random()}`)
      .replace(/[^a-f0-9]/g, '')
    return (raw + '0'.repeat(32)).slice(0, 32)
  }
  try {
    const existing = window.localStorage.getItem(TELEMETRY_INSTANCE_KEY)
    if (existing && /^[a-f0-9]{32}$/.test(existing)) return existing
    const id = fresh()
    window.localStorage.setItem(TELEMETRY_INSTANCE_KEY, id)
    return id
  } catch {
    return fresh()
  }
}

const pageLoadId = (globalThis.crypto?.randomUUID?.() ?? String(Date.now())).slice(0, 18)
const buffer: Array<{ t: number; kind: string; level: string; message: string; data?: Record<string, unknown> }> = []
let flushTimer: ReturnType<typeof setTimeout> | null = null
let dropping = false
let warnBudget = 20
let backoffMs = RETRY_BASE_MS
let droppedCount = 0

function surface(): string {
  try {
    const params = new URLSearchParams(window.location.search)
    return params.get('surface') || (window.frameElement ? 'iframe' : 'top')
  } catch {
    return 'top'
  }
}

/** 把超出容量的最旧条目丢弃, 返回丢弃条数(调用方负责计入 droppedCount)。 */
export function capBuffer<T>(list: T[], max: number): number {
  let dropped = 0
  while (list.length > max) {
    list.shift()
    dropped += 1
  }
  return dropped
}

function queue(kind: string, level: 'error' | 'warn' | 'info', message: string, data?: Record<string, unknown>): void {
  if (dropping) return
  buffer.push({
    t: Math.round(Date.now()) / 1000,
    kind: kind.slice(0, 64),
    level,
    message: String(message).slice(0, 4000),
    ...(data ? { data } : {}),
  })
  droppedCount += capBuffer(buffer, MAX_BUFFER)
  if (buffer.length >= MAX_EVENTS_PER_FLUSH) void flush('full')
  // 上面的立即 flush 只搬走队首一批(最多 MAX_EVENTS_PER_FLUSH 条); 若那之后 buffer
  // 仍有剩余(例如立即 flush 触发前又挤进来一条 telemetry_dropped 之类的新事件, 恰好
  // 让长度依然过线), 必须兜底再排一个普通定时器, 否则剩余条目会卡住等不到下一次
  // 外部事件才被动带出去。
  if (buffer.length > 0 && !flushTimer) {
    flushTimer = setTimeout(() => { flushTimer = null; void flush('interval') }, FLUSH_INTERVAL_MS)
  }
}

/** 退避序列: 上一次失败的等待时间翻倍, 封顶 RETRY_MAX_MS; 成功后调用方把它复位回 RETRY_BASE_MS。 */
export function nextBackoffMs(current: number): number {
  return Math.min(current * 2, RETRY_MAX_MS)
}

function scheduleRetry(): void {
  if (flushTimer) return
  const delay = backoffMs
  backoffMs = nextBackoffMs(backoffMs)
  flushTimer = setTimeout(() => { flushTimer = null; void flush('retry') }, delay)
}

function reportDroppedIfAny(): void {
  if (droppedCount <= 0) return
  const n = droppedCount
  droppedCount = 0
  queue('telemetry_dropped', 'warn', `缓冲区溢出丢弃 ${n} 条事件`, { count: n })
}

async function flush(_reason: string): Promise<void> {
  if (!buffer.length) return
  const events = buffer.splice(0, MAX_EVENTS_PER_FLUSH)
  const payload = {
    instance: {
      instance_id: instanceId(),
      page_load_id: pageLoadId,
      origin: window.location.origin,
      surface: surface(),
      href: window.location.pathname.slice(0, 160),
      ua: navigator.userAgent.slice(0, 200),
    },
    events,
  }
  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    })
    if (!res.ok && (res.status === 401 || res.status === 403 || res.status === 404)) {
      // 后端旧版或未登录环境: 停止上报, 别刷请求。
      dropping = true
      return
    }
    if (!res.ok) {
      // 5xx / 429 等瞬时失败: 事件退回 buffer 头部重试, 指数退避。
      buffer.unshift(...events)
      droppedCount += capBuffer(buffer, MAX_BUFFER)
      scheduleRetry()
      return
    }
    backoffMs = RETRY_BASE_MS
    reportDroppedIfAny()
  } catch {
    // 网络失败: 事件退回 buffer 头部重试, 指数退避。
    buffer.unshift(...events)
    droppedCount += capBuffer(buffer, MAX_BUFFER)
    scheduleRetry()
  }
}

export function reportFrontendEvent(
  kind: string,
  message: string,
  data?: Record<string, unknown>,
  level: 'error' | 'warn' | 'info' = 'info',
): void {
  try { queue(kind, level, message, data) } catch { /* 观测不反噬 */ }
}

function installGlobalCapture(): void {
  window.addEventListener('error', (ev) => {
    const target = ev.target as (EventTarget & { src?: string }) | null
    // 资源加载失败(target 非 window)单独归类, 和脚本错误分开看。
    if (target && target !== window && (target as HTMLElement).tagName) {
      queue('resource_error', 'error', `资源加载失败 ${(target as HTMLElement).tagName} ${String((target as HTMLImageElement).src || '').slice(0, 160)}`)
      return
    }
    queue('js_error', 'error', ev.message || 'script error', {
      source: `${ev.filename || ''}:${ev.lineno || 0}:${ev.colno || 0}`,
      stack: ev.error instanceof Error ? String(ev.error.stack || '').slice(0, 1200) : undefined,
    })
  })
  window.addEventListener('unhandledrejection', (ev) => {
    const reason = ev.reason
    queue('unhandled_rejection', 'error', reason instanceof Error ? (reason.stack || reason.message) : String(reason).slice(0, 800))
  })
  const origError = console.error.bind(console)
  console.error = (...parts: unknown[]) => {
    try {
      queue('console_error', 'error', parts.map(p => (p instanceof Error ? (p.stack || p.message) : String(p))).join(' ').slice(0, 2000))
    } catch { /* */ }
    origError(...parts)
  }
  const origWarn = console.warn.bind(console)
  console.warn = (...parts: unknown[]) => {
    try {
      if (warnBudget > 0) { warnBudget -= 1; queue('console_warn', 'warn', parts.map(String).join(' ').slice(0, 800)) }
    } catch { /* */ }
    origWarn(...parts)
  }
}

/** 判断一个请求 URL 是否与当前页面同源。跨域请求绝不附加自定义头 ——
 *  那会触发浏览器 CORS 预检, 可能打断第三方 API 调用。 */
export function isSameOriginUrl(url: string, pageOrigin: string = window.location.origin): boolean {
  try {
    return new URL(url, pageOrigin).origin === pageOrigin
  } catch {
    return false
  }
}

export interface AttributionTags {
  instance: string
  surface: string
}

/** 给同源 fetch 输入附加归因头(实例 id + surface); 跨域或任何解析异常一律
 *  原样放行(fail-open) —— 观测绝不能打断业务请求。 */
export function withAttributionHeaders(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  tags: AttributionTags,
): { input: RequestInfo | URL; init?: RequestInit } {
  try {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!isSameOriginUrl(url)) return { input, init }
    if (typeof Request !== 'undefined' && input instanceof Request) {
      const headers = new Headers(input.headers)
      headers.set(ATTRIBUTION_INSTANCE_HEADER, tags.instance)
      headers.set(ATTRIBUTION_SURFACE_HEADER, tags.surface)
      return { input: new Request(input, { headers }), init }
    }
    const headers = new Headers(init?.headers)
    headers.set(ATTRIBUTION_INSTANCE_HEADER, tags.instance)
    headers.set(ATTRIBUTION_SURFACE_HEADER, tags.surface)
    return { input, init: { ...init, headers } }
  } catch {
    return { input, init }
  }
}

export interface FetchOutcomeData {
  method: string
  path: string
  status: number
  duration_ms: number
}

/** 把 fetch 结果整理成结构化字段, 供后端归因库直接消费(而不必解析 message 字符串)。 */
export function buildFetchResultData(method: string, url: string, status: number, durationMs: number): FetchOutcomeData {
  return { method, path: url.slice(0, 160), status, duration_ms: Math.round(durationMs) }
}

function installFetchCapture(): void {
  const origFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const started = performance.now()
    let url = ''
    try {
      url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    } catch { url = '(unreadable)' }
    const tagged = withAttributionHeaders(input, init, { instance: instanceId(), surface: surface() })
    try {
      const res = await origFetch(tagged.input as RequestInfo, tagged.init)
      const duration = Math.round(performance.now() - started)
      if (!url.startsWith(ENDPOINT) && (res.status >= 400 || duration > 8000)) {
        const method = init?.method
          || (typeof input !== 'string' && !(input instanceof URL) && (input as Request).method)
          || 'GET'
        queue('fetch_result', res.status >= 400 ? 'error' : 'warn',
          `${method} ${url.slice(0, 160)} → ${res.status} ${duration}ms`,
          buildFetchResultData(method, url, res.status, duration) as unknown as Record<string, unknown>)
      }
      return res
    } catch (err) {
      // 排除上报端点自身: 否则 flush() 网络失败时, 这里排队的事件会抢先用固定间隔
      // 重新占用 flushTimer, 让 scheduleRetry() 的指数退避形同虚设。
      if (!url.startsWith(ENDPOINT)) {
        const message = err instanceof Error ? err.message : String(err)
        queue('fetch_network_error', 'error', `${url.slice(0, 160)} 网络失败: ${message}`, {
          path: url.slice(0, 160),
          error: message,
        })
      }
      throw err
    }
  }
}

// ─────────────────────────────────────────────────────────────────
// 性能观测: LCP / CLS / INP(近似) / TTFB / longtask 分钟聚合 / heap 快照。
// 全部挂在现有 PerformanceObserver 回调与现有 60s heartbeat tick 上,
// 不引入 web-vitals 依赖, 不新增定时器。
// ─────────────────────────────────────────────────────────────────

let lcpMs: number | undefined
let clsValue = 0
let inpMs = 0
let ttfbMs: number | undefined
let vitalsReported = false

export interface LongtaskBucket {
  minuteKey: number
  count: number
  totalMs: number
}

/** 把一条 longtask 条目并入当前分钟桶; 跨分钟则把非空旧桶作为完成事件退回, 并开新桶。 */
export function accumulateLongtask(
  bucket: LongtaskBucket | null,
  durationMs: number,
  minuteKey: number,
): { bucket: LongtaskBucket; flushed?: LongtaskBucket } {
  if (bucket && bucket.minuteKey !== minuteKey) {
    const flushed = bucket.count > 0 ? bucket : undefined
    return { bucket: { minuteKey, count: 1, totalMs: Math.round(durationMs) }, flushed }
  }
  const base = bucket ?? { minuteKey, count: 0, totalMs: 0 }
  return { bucket: { minuteKey, count: base.count + 1, totalMs: base.totalMs + Math.round(durationMs) } }
}

let longtaskBucket: LongtaskBucket | null = null

function emitLongtaskBucket(bucket: LongtaskBucket): void {
  queue('longtask_minute', 'warn', `长任务 ${bucket.count} 次, 共 ${bucket.totalMs}ms`, {
    minute: bucket.minuteKey,
    count: bucket.count,
    total_ms: bucket.totalMs,
  })
}

/** heartbeat tick 兜底: 若当前桶所属分钟已经过去(page 空闲, 再没有新 longtask 触发跨分钟落桶),
 *  在这里把它冲掉, 避免最后一分钟的数据永远卡在内存里不上报。 */
function flushStaleLongtaskBucket(nowMinuteKey: number): void {
  if (longtaskBucket && longtaskBucket.minuteKey !== nowMinuteKey && longtaskBucket.count > 0) {
    emitLongtaskBucket(longtaskBucket)
    longtaskBucket = null
  }
}

export interface VitalsInput {
  lcpMs?: number
  cls?: number
  inpMs?: number
  ttfbMs?: number
}

/** 把当前累计的性能指标整理成上报用的 data 字段; 全空则返回 undefined(不产生空事件)。 */
export function buildVitalsSnapshot(v: VitalsInput): Record<string, number> | undefined {
  const data: Record<string, number> = {}
  if (v.lcpMs !== undefined) data.lcp_ms = Math.round(v.lcpMs)
  if (v.cls !== undefined && v.cls > 0) data.cls = Math.round(v.cls * 1000) / 1000
  if (v.inpMs !== undefined && v.inpMs > 0) data.inp_ms = Math.round(v.inpMs)
  if (v.ttfbMs !== undefined) data.ttfb_ms = Math.round(v.ttfbMs)
  return Object.keys(data).length ? data : undefined
}

function reportVitalsOnce(): void {
  if (vitalsReported) return
  const snap = buildVitalsSnapshot({ lcpMs, cls: clsValue, inpMs, ttfbMs })
  if (!snap) return
  vitalsReported = true
  queue('perf_vitals', 'info', 'perf vitals', snap)
}

export interface ChromeMemoryInfo {
  usedJSHeapSize: number
  totalJSHeapSize: number
  jsHeapSizeLimit: number
}

/** Chrome-only `performance.memory`; 其它浏览器/环境判空返回 undefined。 */
export function readChromeMemory(perf: Performance = performance): ChromeMemoryInfo | undefined {
  try {
    const mem = (perf as unknown as { memory?: ChromeMemoryInfo }).memory
    if (mem && typeof mem.usedJSHeapSize === 'number') {
      return { usedJSHeapSize: mem.usedJSHeapSize, totalJSHeapSize: mem.totalJSHeapSize, jsHeapSizeLimit: mem.jsHeapSizeLimit }
    }
  } catch { /* */ }
  return undefined
}

type ObserveInit = PerformanceObserverInit & { durationThreshold?: number }

function installPerfObservers(): void {
  if (typeof PerformanceObserver === 'undefined') return
  try {
    new PerformanceObserver((list) => {
      const entries = list.getEntries()
      const last = entries[entries.length - 1]
      if (last) lcpMs = last.startTime
    }).observe({ type: 'largest-contentful-paint', buffered: true } as ObserveInit)
  } catch { /* 浏览器不支持该 entry type */ }

  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as unknown as { value: number; hadRecentInput: boolean }
        if (!shift.hadRecentInput) clsValue += shift.value
      }
    }).observe({ type: 'layout-shift', buffered: true } as ObserveInit)
  } catch { /* */ }

  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const timing = entry as unknown as { duration: number }
        if (timing.duration > inpMs) inpMs = timing.duration
      }
    }).observe({ type: 'event', buffered: true, durationThreshold: 40 } as ObserveInit)
  } catch { /* */ }

  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const nav = entry as unknown as { responseStart: number }
        ttfbMs = nav.responseStart
      }
    }).observe({ type: 'navigation', buffered: true } as ObserveInit)
  } catch { /* */ }

  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const minuteKey = Math.floor(Date.now() / 60_000)
        const { bucket, flushed } = accumulateLongtask(longtaskBucket, entry.duration, minuteKey)
        if (flushed) emitLongtaskBucket(flushed)
        longtaskBucket = bucket
      }
    }).observe({ type: 'longtask', buffered: true } as ObserveInit)
  } catch { /* 浏览器不支持 longtask(如 Safari/Firefox) */ }

  try {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') reportVitalsOnce()
    })
  } catch { /* */ }
}

let installed = false
export function installFrontendTelemetry(): void {
  if (installed) return
  installed = true
  try {
    installGlobalCapture()
    installFetchCapture()
    installPerfObservers()
    queue('page_load', 'info', `页面加载 ${window.location.pathname.slice(0, 120)}`)
    window.setInterval(() => {
      flushStaleLongtaskBucket(Math.floor(Date.now() / 60_000))
      const mem = readChromeMemory()
      queue('heartbeat', 'info', 'heartbeat', mem ? { memory: mem } : undefined)
    }, HEARTBEAT_INTERVAL_MS)
    window.addEventListener('pagehide', () => {
      try {
        reportVitalsOnce()
        if (navigator.sendBeacon) navigator.sendBeacon(ENDPOINT, JSON.stringify({
          instance: {
            instance_id: instanceId(), page_load_id: pageLoadId,
            origin: window.location.origin, surface: surface(),
            href: window.location.pathname.slice(0, 160), ua: navigator.userAgent.slice(0, 200),
          },
          events: buffer.splice(0, MAX_EVENTS_PER_FLUSH),
        }))
      } catch { /* */ }
    })
  } catch { /* 观测不反噬 */ }
}
