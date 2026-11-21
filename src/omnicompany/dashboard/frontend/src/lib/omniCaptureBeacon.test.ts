import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

// public/omni-capture-beacon.js is a plain browser IIFE (loaded via <script>, not an
// ES module), so it is exercised here by evaluating its real source against the jsdom
// globals rather than importing it. This is the file P1.4b fixes: it POSTs to
// /api/boss-sight/captures/surface on a 5s interval plus mousemove/scroll/resize/DOM
// mutation triggers. Confirmed root cause (2026-08-16 `omni dashboard observe`: 273
// events, 262 fetch_network_error, all within the same second, no backoff): report()
// only single-flighted concurrent triggers (reportInFlight/reportQueued) but had no
// failure backoff and no document.hidden gate, so a broken link let every trigger fire
// its own fetch immediately — and failed fetches resolve near-instantly, so bursts of
// triggers (mousemove throttled to ~140-200ms) turned into bursts of failed requests.
const here = dirname(fileURLToPath(import.meta.url))
const beaconSrc = readFileSync(resolve(here, '../../public/omni-capture-beacon.js'), 'utf-8')

type Cleanup = () => void

/**
 * Evaluates the beacon's IIFE against the current jsdom window/document, capturing
 * every listener/observer it registers so the test can tear them down afterward.
 * Without this, each test's fresh closures would keep listening on the shared jsdom
 * window/document and leak into later tests.
 */
function loadBeacon(): Cleanup {
  ;(window as unknown as { __omniCaptureBeacon?: boolean }).__omniCaptureBeacon = false
  try { window.sessionStorage.clear() } catch { /* ignore */ }

  type ListenerEntry = [string, EventListenerOrEventListenerObject, boolean | AddEventListenerOptions | undefined]
  const winListeners: ListenerEntry[] = []
  const docListeners: ListenerEntry[] = []
  const observers: MutationObserver[] = []

  const origWinAdd = window.addEventListener.bind(window)
  const origDocAdd = document.addEventListener.bind(document)
  const OrigMutationObserver = window.MutationObserver

  window.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject, opts?: boolean | AddEventListenerOptions) => {
    winListeners.push([type, listener, opts])
    return origWinAdd(type, listener, opts)
  }) as typeof window.addEventListener
  document.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject, opts?: boolean | AddEventListenerOptions) => {
    docListeners.push([type, listener, opts])
    return origDocAdd(type, listener, opts)
  }) as typeof document.addEventListener
  ;(window as unknown as { MutationObserver: typeof MutationObserver }).MutationObserver = class extends OrigMutationObserver {
    constructor(cb: MutationCallback) {
      super(cb)
      observers.push(this)
    }
  }

  // eslint-disable-next-line no-new-func -- evaluating the real production script source
  new Function(beaconSrc)()

  window.addEventListener = origWinAdd as typeof window.addEventListener
  document.addEventListener = origDocAdd as typeof document.addEventListener
  ;(window as unknown as { MutationObserver: typeof MutationObserver }).MutationObserver = OrigMutationObserver

  return () => {
    for (const [type, listener, opts] of winListeners) window.removeEventListener(type, listener, opts)
    for (const [type, listener, opts] of docListeners) document.removeEventListener(type, listener, opts)
    for (const o of observers) o.disconnect()
  }
}

describe('omni-capture-beacon surface reporting (boss-sight retry storm fix)', () => {
  let cleanup: Cleanup | undefined

  beforeEach(() => {
    vi.useFakeTimers()
    document.body.innerHTML = ''
    Object.defineProperty(document, 'hidden', { value: false, configurable: true })
  })

  afterEach(() => {
    cleanup?.()
    cleanup = undefined
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.restoreAllMocks()
    ;(window as unknown as { __omniCaptureBeacon?: boolean }).__omniCaptureBeacon = false
  })

  it('single-flights concurrent triggers instead of firing parallel requests', async () => {
    let resolveFetch!: (v: Response) => void
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((resolve) => { resolveFetch = resolve }),
    )

    cleanup = loadBeacon() // start() calls recompute()+report() synchronously: request #1 fires, stays pending
    expect(fetchMock).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new Event('resize'))
    await vi.advanceTimersByTimeAsync(400)
    window.dispatchEvent(new Event('resize'))
    await vi.advanceTimersByTimeAsync(400)
    expect(fetchMock).toHaveBeenCalledTimes(1) // triggers while in-flight only queue, never fire in parallel

    resolveFetch(new Response('{}', { status: 200 }))
    await vi.advanceTimersByTimeAsync(0)
    await Promise.resolve()
    await Promise.resolve()
    expect(fetchMock).toHaveBeenCalledTimes(1) // queued interaction triggers wait for the global cadence
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('gates same-second retries after a failure instead of retrying immediately', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('fetch_network_error'))

    cleanup = loadBeacon() // t=0: initial attempt fails -> fail streak 1, backoff window opens
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Reproduces the incident: a burst of triggers within the same second (mousemove/
    // resize storm) used to each fire their own failed fetch. Now they must be dropped.
    window.dispatchEvent(new Event('resize'))
    await vi.advanceTimersByTimeAsync(300)
    window.dispatchEvent(new Event('resize'))
    await vi.advanceTimersByTimeAsync(300)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('treats HTTP error responses as failures instead of resetting backoff', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 502 }))
    cleanup = loadBeacon()
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(3)
  })

  it('caps backoff growth so retries stay far sparser than the naive 5s cadence', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('fetch_network_error'))

    cleanup = loadBeacon()
    await vi.advanceTimersByTimeAsync(0)

    // Naive (no backoff) cadence over 60s would be one attempt every 5s -> 13 attempts.
    // Exponential backoff capped at 60s should keep this well below that.
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
    expect(fetchMock.mock.calls.length).toBeLessThan(8)
  })

  it('resumes normal cadence once a request succeeds again', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('fetch_network_error'))
    cleanup = loadBeacon()
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(20000) // accumulate some backoff first
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1)

    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))
    await vi.advanceTimersByTimeAsync(60000) // let a success land and clear the backoff
    const countAfterRecovery = fetchMock.mock.calls.length

    await vi.advanceTimersByTimeAsync(10000) // healthy cadence: one attempt every 5s
    expect(fetchMock.mock.calls.length - countAfterRecovery).toBeGreaterThanOrEqual(2)
  })

  it('pauses reporting while document.hidden and sends a catch-up report on visibility restore', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
    cleanup = loadBeacon()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, 'hidden', { value: true, configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(5000) // the 5s interval would normally fire again here
    expect(fetchMock).toHaveBeenCalledTimes(1) // but reporting is paused while hidden

    Object.defineProperty(document, 'hidden', { value: false, configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2) // becoming visible sends one catch-up report
  })

  it('pauses reporting for an unfocused dashboard page in explicit idle mode', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    cleanup = loadBeacon()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    window.localStorage.setItem('omni.dashboard.idle.v1', '1')
    hasFocus.mockReturnValue(false)
    window.dispatchEvent(new Event('blur'))
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    hasFocus.mockReturnValue(true)
    window.dispatchEvent(new Event('focus'))
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
