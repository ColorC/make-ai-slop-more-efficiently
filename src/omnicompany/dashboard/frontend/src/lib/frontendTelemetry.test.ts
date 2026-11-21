/**
 * frontendTelemetry 单测 — 覆盖 2026-08-16 PERF-OBSERVATORY P1.1/P1.3/P1.4a 新增逻辑:
 *   - 归因头: 同源注入 / 跨域不注入(纯函数 + 端到端 fetch 包装)
 *   - 上报退避: 失败指数退避、成功复位(端到端, fake timer 驱动 flush)
 *   - longtask 分钟聚合: 跨分钟落桶为一条 longtask_minute
 *   - fetch 包装结构化字段: fetch_result/data 里的 method/path/status/duration_ms
 *   - buffer 溢出丢最旧计数 + telemetry_dropped 补报
 *
 * 纯逻辑(归因头判定/退避序列/longtask 归约/结构化字段/vitals 快照/内存读取/容量裁剪)
 * 用静态 import 直接测; 需要真跑 installFrontendTelemetry() 的端到端行为(它是模块级单例,
 * 一次 install 只生效一次)则用 vi.resetModules() + 动态 import 换取一个全新的模块实例。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ATTRIBUTION_INSTANCE_HEADER,
  ATTRIBUTION_SURFACE_HEADER,
  RETRY_BASE_MS,
  accumulateLongtask,
  buildFetchResultData,
  buildVitalsSnapshot,
  capBuffer,
  isSameOriginUrl,
  nextBackoffMs,
  readChromeMemory,
  withAttributionHeaders,
} from './frontendTelemetry'

// ─────────────────────────────────────────────────────────────────
// 纯函数: 归因头判定
// ─────────────────────────────────────────────────────────────────

describe('isSameOriginUrl', () => {
  it('accepts relative paths and same-origin absolute urls', () => {
    expect(isSameOriginUrl('/api/foo')).toBe(true)
    expect(isSameOriginUrl(`${window.location.origin}/api/foo`)).toBe(true)
  })

  it('rejects a different origin', () => {
    expect(isSameOriginUrl('https://cross-origin.example.com/x')).toBe(false)
  })

  it('fails closed (false) when the page origin itself cannot form a valid base', () => {
    expect(isSameOriginUrl('/api/foo', 'not-a-valid-origin')).toBe(false)
  })
})

describe('withAttributionHeaders', () => {
  const tags = { instance: 'inst-123', surface: 'top' }

  it('injects both headers for a same-origin string url', () => {
    const { init } = withAttributionHeaders('/api/foo', { method: 'POST' }, tags)
    const headers = new Headers(init?.headers)
    expect(headers.get(ATTRIBUTION_INSTANCE_HEADER)).toBe('inst-123')
    expect(headers.get(ATTRIBUTION_SURFACE_HEADER)).toBe('top')
    expect(init?.method).toBe('POST')
  })

  it('injects both headers for a same-origin Request object without losing method', () => {
    const req = new Request(`${window.location.origin}/api/foo`, { method: 'DELETE' })
    const { input } = withAttributionHeaders(req, undefined, tags)
    expect(input).toBeInstanceOf(Request)
    const tagged = input as Request
    expect(tagged.method).toBe('DELETE')
    expect(tagged.headers.get(ATTRIBUTION_INSTANCE_HEADER)).toBe('inst-123')
    expect(tagged.headers.get(ATTRIBUTION_SURFACE_HEADER)).toBe('top')
  })

  it('does not inject headers for a cross-origin request', () => {
    const { input, init } = withAttributionHeaders('https://cross-origin.example.com/x', { method: 'GET' }, tags)
    expect(input).toBe('https://cross-origin.example.com/x')
    const headers = new Headers(init?.headers)
    expect(headers.get(ATTRIBUTION_INSTANCE_HEADER)).toBeNull()
    expect(headers.get(ATTRIBUTION_SURFACE_HEADER)).toBeNull()
  })

  it('fails open (returns original input/init) if url extraction throws', () => {
    const weird = { toString: () => { throw new Error('boom') } } as unknown as string
    const result = withAttributionHeaders(weird, { method: 'GET' }, tags)
    expect(result.input).toBe(weird)
    expect(result.init?.method).toBe('GET')
  })
})

// ─────────────────────────────────────────────────────────────────
// 纯函数: 退避序列 / longtask 归约 / 结构化字段 / vitals 快照 / 内存读取 / 容量裁剪
// ─────────────────────────────────────────────────────────────────

describe('nextBackoffMs', () => {
  it('doubles the current delay and caps at 60s', () => {
    let delay = RETRY_BASE_MS
    const seq = [delay]
    for (let i = 0; i < 6; i++) {
      delay = nextBackoffMs(delay)
      seq.push(delay)
    }
    expect(seq).toEqual([2500, 5000, 10000, 20000, 40000, 60000, 60000])
  })
})

describe('accumulateLongtask', () => {
  it('aggregates multiple entries within the same minute without flushing', () => {
    let bucket = null as ReturnType<typeof accumulateLongtask>['bucket'] | null
    let r = accumulateLongtask(bucket, 60, 100)
    bucket = r.bucket
    expect(r.flushed).toBeUndefined()
    r = accumulateLongtask(bucket, 90, 100)
    bucket = r.bucket
    expect(r.flushed).toBeUndefined()
    expect(bucket).toEqual({ minuteKey: 100, count: 2, totalMs: 150 })
  })

  it('flushes the previous non-empty bucket when the minute rolls over', () => {
    let r = accumulateLongtask(null, 60, 100)
    r = accumulateLongtask(r.bucket, 90, 100)
    const priorBucket = r.bucket
    r = accumulateLongtask(r.bucket, 45, 101)
    expect(r.flushed).toEqual(priorBucket)
    expect(r.bucket).toEqual({ minuteKey: 101, count: 1, totalMs: 45 })
  })

  it('does not flush anything when starting from an empty bucket', () => {
    const r = accumulateLongtask(null, 55, 200)
    expect(r.flushed).toBeUndefined()
    expect(r.bucket).toEqual({ minuteKey: 200, count: 1, totalMs: 55 })
  })
})

describe('buildFetchResultData', () => {
  it('produces structured fields with a truncated path', () => {
    const longPath = '/api/' + 'x'.repeat(300)
    const data = buildFetchResultData('GET', longPath, 503, 812.6)
    expect(data.method).toBe('GET')
    expect(data.status).toBe(503)
    expect(data.duration_ms).toBe(813)
    expect(data.path.length).toBe(160)
  })
})

describe('buildVitalsSnapshot', () => {
  it('omits fields that are not yet available', () => {
    expect(buildVitalsSnapshot({})).toBeUndefined()
    expect(buildVitalsSnapshot({ lcpMs: 1234.6 })).toEqual({ lcp_ms: 1235 })
  })

  it('rounds cls to 3 decimals and drops a zero cls', () => {
    expect(buildVitalsSnapshot({ cls: 0 })).toBeUndefined()
    expect(buildVitalsSnapshot({ cls: 0.123456 })).toEqual({ cls: 0.123 })
  })

  it('assembles all four metrics together', () => {
    expect(buildVitalsSnapshot({ lcpMs: 2000, cls: 0.05, inpMs: 180, ttfbMs: 300 })).toEqual({
      lcp_ms: 2000, cls: 0.05, inp_ms: 180, ttfb_ms: 300,
    })
  })
})

describe('readChromeMemory', () => {
  it('returns undefined when performance.memory is absent', () => {
    expect(readChromeMemory({} as Performance)).toBeUndefined()
  })

  it('reads the Chrome-only memory fields when present', () => {
    const fakePerf = { memory: { usedJSHeapSize: 10, totalJSHeapSize: 20, jsHeapSizeLimit: 30 } } as unknown as Performance
    expect(readChromeMemory(fakePerf)).toEqual({ usedJSHeapSize: 10, totalJSHeapSize: 20, jsHeapSizeLimit: 30 })
  })
})

describe('capBuffer', () => {
  it('drops the oldest entries and reports the drop count', () => {
    const list = [1, 2, 3, 4, 5]
    const dropped = capBuffer(list, 3)
    expect(dropped).toBe(2)
    expect(list).toEqual([3, 4, 5])
  })

  it('is a no-op when already within capacity', () => {
    const list = [1, 2]
    expect(capBuffer(list, 3)).toBe(0)
    expect(list).toEqual([1, 2])
  })
})

// ─────────────────────────────────────────────────────────────────
// 端到端: installFrontendTelemetry() 单例, 每个用例用 vi.resetModules() +
// 动态 import 换取全新模块实例, 避免跨用例污染。
// ─────────────────────────────────────────────────────────────────

type TelemetryModule = typeof import('./frontendTelemetry')

class MockPerformanceObserver {
  static instances: MockPerformanceObserver[] = []
  type: string | undefined
  constructor(private readonly cb: (list: { getEntries: () => unknown[] }) => void) {
    MockPerformanceObserver.instances.push(this)
  }
  observe(opts: { type: string }): void { this.type = opts.type }
  disconnect(): void { /* no-op */ }
  trigger(entries: unknown[]): void { this.cb({ getEntries: () => entries }) }
}

function jsonBody(call: unknown[]): { instance: unknown; events: Array<{ kind: string; data?: Record<string, unknown> }> } {
  const init = call[1] as RequestInit
  return JSON.parse(String(init.body))
}

describe('installFrontendTelemetry — end to end', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    MockPerformanceObserver.instances = []
    ;(globalThis as any).PerformanceObserver = MockPerformanceObserver
    fetchMock = vi.fn()
    ;(globalThis as any).fetch = fetchMock
  })

  afterEach(() => {
    vi.useRealTimers()
    delete (globalThis as any).PerformanceObserver
  })

  it('retries a failed flush with doubling backoff and resets it after success, preserving events', async () => {
    vi.resetModules()
    const mod: TelemetryModule = await import('./frontendTelemetry')
    fetchMock
      .mockRejectedValueOnce(new Error('network down'))
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))

    mod.installFrontendTelemetry() // queues 'page_load', schedules first flush at 2500ms

    expect(fetchMock).toHaveBeenCalledTimes(0)
    await vi.advanceTimersByTimeAsync(2500) // first attempt fails → retry scheduled at 2500ms
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2500) // second attempt (retry #1) fails → retry scheduled at 5000ms
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(4999)
    expect(fetchMock).toHaveBeenCalledTimes(2) // not yet — backoff must have doubled to 5000ms, not stayed at 2500ms
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchMock).toHaveBeenCalledTimes(3) // third attempt (retry #2) succeeds

    // the page_load event survived two failed attempts instead of being lost on requeue
    const lastBody = jsonBody(fetchMock.mock.calls[2])
    expect(lastBody.events.some((e) => e.kind === 'page_load')).toBe(true)
  })

  it('injects attribution headers on a same-origin application fetch and reports structured fetch_result data', async () => {
    vi.resetModules()
    const mod: TelemetryModule = await import('./frontendTelemetry')
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/api/diagnostics/frontend/events')) return new Response('{}', { status: 200 })
      return new Response('nope', { status: 500 })
    })

    mod.installFrontendTelemetry()
    await (window.fetch as typeof fetch)('/api/widgets')

    // the app-level call carried attribution headers to origFetch
    const appCall = fetchMock.mock.calls.find((c) => c[0] === '/api/widgets')
    expect(appCall).toBeDefined()
    const appHeaders = new Headers((appCall![1] as RequestInit)?.headers)
    expect(appHeaders.get(ATTRIBUTION_INSTANCE_HEADER)).toMatch(/^[a-f0-9]{32}$/)
    expect(appHeaders.get(ATTRIBUTION_SURFACE_HEADER)).toBeTruthy()

    await vi.advanceTimersByTimeAsync(2500) // let the buffered fetch_result flush out
    const diagCall = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/diagnostics/frontend/events'))
    const body = jsonBody(diagCall!)
    const fetchResult = body.events.find((e) => e.kind === 'fetch_result')
    expect(fetchResult?.data).toMatchObject({ method: 'GET', path: '/api/widgets', status: 500 })
    expect(typeof fetchResult?.data?.duration_ms).toBe('number')
  })

  it('does not inject attribution headers on a cross-origin application fetch', async () => {
    vi.resetModules()
    const mod: TelemetryModule = await import('./frontendTelemetry')
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))
    mod.installFrontendTelemetry()

    await (window.fetch as typeof fetch)('https://cross-origin.example.com/x')
    const crossCall = fetchMock.mock.calls.find((c) => c[0] === 'https://cross-origin.example.com/x')
    expect(crossCall).toBeDefined()
    const headers = new Headers((crossCall![1] as RequestInit | undefined)?.headers)
    expect(headers.get(ATTRIBUTION_INSTANCE_HEADER)).toBeNull()
    expect(headers.get(ATTRIBUTION_SURFACE_HEADER)).toBeNull()
  })

  it('aggregates longtasks per minute and emits exactly one longtask_minute event when the minute rolls over', async () => {
    vi.setSystemTime(new Date('2026-08-16T00:00:00.000Z'))
    vi.resetModules()
    const mod: TelemetryModule = await import('./frontendTelemetry')
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))
    mod.installFrontendTelemetry()

    const ltObserver = MockPerformanceObserver.instances.find((o) => o.type === 'longtask')
    expect(ltObserver).toBeDefined()

    ltObserver!.trigger([{ duration: 60 }, { duration: 90 }]) // same minute → no emission yet
    vi.setSystemTime(new Date('2026-08-16T00:01:05.000Z')) // next minute
    ltObserver!.trigger([{ duration: 45 }]) // rollover → flushes the prior minute's bucket

    await vi.advanceTimersByTimeAsync(2500) // flush buffer to the mock endpoint
    const diagCall = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/diagnostics/frontend/events'))
    const body = jsonBody(diagCall!)
    const longtaskEvents = body.events.filter((e) => e.kind === 'longtask_minute')
    expect(longtaskEvents).toHaveLength(1)
    expect(longtaskEvents[0].data).toMatchObject({ count: 2, total_ms: 150 })
  })

  it('reports a perf_vitals event once the page becomes hidden', async () => {
    vi.resetModules()
    const mod: TelemetryModule = await import('./frontendTelemetry')
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))
    mod.installFrontendTelemetry()

    const lcpObserver = MockPerformanceObserver.instances.find((o) => o.type === 'largest-contentful-paint')
    const navObserver = MockPerformanceObserver.instances.find((o) => o.type === 'navigation')
    lcpObserver!.trigger([{ startTime: 1800 }])
    navObserver!.trigger([{ responseStart: 220 }])

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))

    await vi.advanceTimersByTimeAsync(2500)
    const diagCall = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/diagnostics/frontend/events'))
    const body = jsonBody(diagCall!)
    const vitals = body.events.find((e) => e.kind === 'perf_vitals')
    expect(vitals?.data).toMatchObject({ lcp_ms: 1800, ttfb_ms: 220 })
  })

  it('emits a telemetry_dropped event with a positive count after buffer overflow recovers', async () => {
    vi.resetModules()
    const mod: TelemetryModule = await import('./frontendTelemetry')
    fetchMock.mockRejectedValue(new Error('down'))
    mod.installFrontendTelemetry()

    // 一次性同步写入远超缓冲上限(120)的事件、中途不 await —— buffer 每撞到 40 就会
    // 同步触发一次 flush('full'), 多个 flush 并发挂起(各自已从 buffer 里 splice
    // 出至多 40 条, 尚未 reject)。等下面统一放行微任务, 它们才会陆续失败, 把
    // 事件 unshift 回 buffer, 累计真正超过 120 触发丢弃计数。
    for (let i = 0; i < 200; i++) mod.reportFrontendEvent('synthetic', `evt ${i}`)

    // 放行微任务与到期的重试定时器, 让所有并发中的失败 flush 全部 settle。
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(60_000)
    await vi.advanceTimersByTimeAsync(60_000)

    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))
    await vi.advanceTimersByTimeAsync(60_000)
    // reportDroppedIfAny() 在成功后另外 queue 了一条 telemetry_dropped, 需要再放行
    // 一轮 FLUSH_INTERVAL_MS 才会被真正 flush 出去。
    await vi.advanceTimersByTimeAsync(2_500)
    const allEvents = fetchMock.mock.calls.map((c) => jsonBody(c)).flatMap((b) => b.events)

    const droppedEvent = allEvents.find((e) => e.kind === 'telemetry_dropped')
    expect(droppedEvent).toBeDefined()
    expect((droppedEvent!.data!.count as number)).toBeGreaterThan(0)
  })
})
