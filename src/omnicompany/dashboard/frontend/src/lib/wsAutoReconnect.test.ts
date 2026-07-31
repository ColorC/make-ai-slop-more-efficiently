/**
 * wsAutoReconnect 单元测试 (阶段 10 plan §14 exit_criteria 2).
 *
 * 验证 hook 状态机器:
 *   - 首次 connect → onOpen 调一次, isReconnect=false
 *   - server close (非 1000) → 触发重连
 *   - 退避: 1s → 2s → 4s → 8s → 16s → 30s 上限
 *   - 主动 close(1000) 不再重连, state 切 disconnected
 *   - reconnecting 期间 send 进队列, open 后 flush
 *   - 累计 longDisconnectMs (默认 60s) 后 state 切 disconnected
 *
 * 用 vitest fake timer + mock WebSocket. mock WS 暴露 instance 让测试可以
 * 主动触发 onopen / onclose / onmessage / onerror.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useWsAutoReconnect } from './wsAutoReconnect'


// ─────────────────────────────────────────────────────────────────
// Mock WebSocket — 简化的 fake 实现, 暴露 trigger* 给测试主动触发事件
// ─────────────────────────────────────────────────────────────────


class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: MockWebSocket[] = []

  url: string
  readyState: number = MockWebSocket.CONNECTING
  sent: (string | ArrayBuffer | Blob)[] = []
  onopen: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string | ArrayBuffer | Blob) {
    if (this.readyState !== MockWebSocket.OPEN) {
      throw new Error('WebSocket not open')
    }
    this.sent.push(data)
  }

  close(code = 1000, reason = '') {
    this.readyState = MockWebSocket.CLOSING
    this.triggerClose(code, reason)
  }

  // testing helpers
  triggerOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  triggerClose(code = 1006, reason = '') {
    this.readyState = MockWebSocket.CLOSED
    const ev = new Event('close') as CloseEvent
    ;(ev as any).code = code
    ;(ev as any).reason = reason
    ;(ev as any).wasClean = code === 1000
    this.onclose?.(ev)
  }

  triggerMessage(data: string) {
    if (this.readyState !== MockWebSocket.OPEN) return
    const ev = new MessageEvent('message', { data })
    this.onmessage?.(ev)
  }

  triggerError() {
    this.onerror?.(new Event('error'))
  }
}


describe('useWsAutoReconnect', () => {
  let originalWebSocket: typeof WebSocket

  beforeEach(() => {
    vi.useFakeTimers()
    originalWebSocket = (globalThis as any).WebSocket
    ;(globalThis as any).WebSocket = MockWebSocket as any
    // 也把 readyState 常量挂到 globalThis.WebSocket (业务代码用 WebSocket.OPEN)
    ;(globalThis as any).WebSocket.OPEN = MockWebSocket.OPEN
    ;(globalThis as any).WebSocket.CONNECTING = MockWebSocket.CONNECTING
    ;(globalThis as any).WebSocket.CLOSING = MockWebSocket.CLOSING
    ;(globalThis as any).WebSocket.CLOSED = MockWebSocket.CLOSED
    MockWebSocket.instances = []
  })

  afterEach(() => {
    ;(globalThis as any).WebSocket = originalWebSocket
    vi.useRealTimers()
  })

  it('CASE1 首次 connect → onOpen 调一次 isReconnect=false', () => {
    const onOpen = vi.fn()
    const { result } = renderHook(() => useWsAutoReconnect({
      url: 'ws://test/echo',
      onOpen,
    }))

    expect(result.current.state).toBe('connecting')
    expect(MockWebSocket.instances).toHaveLength(1)

    act(() => { MockWebSocket.instances[0].triggerOpen() })

    expect(result.current.state).toBe('connected')
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith(expect.anything(), false)
  })

  it('CASE2 server close (非 1000) → 1s 后重连尝试', () => {
    const onOpen = vi.fn()
    const { result } = renderHook(() => useWsAutoReconnect({
      url: 'ws://test/echo',
      onOpen,
    }))
    act(() => { MockWebSocket.instances[0].triggerOpen() })
    expect(result.current.state).toBe('connected')

    // server abruptly close (code 1006 abnormal)
    act(() => { MockWebSocket.instances[0].triggerClose(1006) })
    expect(result.current.state).toBe('reconnecting')
    expect(MockWebSocket.instances).toHaveLength(1)  // 还没重连

    // 1s 后退避到期, 创建新 ws
    act(() => { vi.advanceTimersByTime(1000) })
    expect(MockWebSocket.instances).toHaveLength(2)

    // 触发新 ws open
    act(() => { MockWebSocket.instances[1].triggerOpen() })
    expect(result.current.state).toBe('connected')
    expect(onOpen).toHaveBeenCalledTimes(2)
    // 第二次 onOpen 是 reconnect
    expect(onOpen.mock.calls[1][1]).toBe(true)
  })

  it('CASE3 退避指数增长到 30s 上限', () => {
    const { result } = renderHook(() => useWsAutoReconnect({
      url: 'ws://test/echo',
      baseBackoffMs: 1000,
      maxBackoffMs: 30_000,
    }))
    act(() => { MockWebSocket.instances[0].triggerOpen() })

    // 连续失败: close → reconnect 创建新 ws → open 之前再 close
    const closeAndAdvance = (advanceMs: number) => {
      const last = MockWebSocket.instances[MockWebSocket.instances.length - 1]
      act(() => { last.triggerClose(1006) })
      act(() => { vi.advanceTimersByTime(advanceMs) })
    }

    closeAndAdvance(1000)   // attempt 1, 1s
    expect(MockWebSocket.instances).toHaveLength(2)
    closeAndAdvance(2000)   // attempt 2, 2s
    expect(MockWebSocket.instances).toHaveLength(3)
    closeAndAdvance(4000)   // attempt 3, 4s
    expect(MockWebSocket.instances).toHaveLength(4)
    closeAndAdvance(8000)   // attempt 4, 8s
    expect(MockWebSocket.instances).toHaveLength(5)
    closeAndAdvance(16000)  // attempt 5, 16s
    expect(MockWebSocket.instances).toHaveLength(6)
    // attempt 6: 32000ms → clamped to 30000ms
    closeAndAdvance(30000)
    expect(MockWebSocket.instances).toHaveLength(7)
    // 多 1s 不应触发再多 (说明上限是 30000)
    closeAndAdvance(30000)
    expect(MockWebSocket.instances).toHaveLength(8)
    expect(result.current.reconnectAttempts).toBeGreaterThan(5)
  })

  it('CASE4 reconnecting 期间 send 入队, open 后 flush', () => {
    const { result } = renderHook(() => useWsAutoReconnect({
      url: 'ws://test/echo',
    }))
    act(() => { MockWebSocket.instances[0].triggerOpen() })
    act(() => { MockWebSocket.instances[0].triggerClose(1006) })

    // reconnecting 状态. send 应入队不抛.
    act(() => {
      result.current.send('queued-A')
      result.current.send('queued-B')
    })

    // 新 ws 创建 + open 后, queued 消息应顺序 flush
    act(() => { vi.advanceTimersByTime(1000) })
    expect(MockWebSocket.instances).toHaveLength(2)
    act(() => { MockWebSocket.instances[1].triggerOpen() })

    expect(MockWebSocket.instances[1].sent).toEqual(['queued-A', 'queued-B'])
  })

  it('CASE5 主动 close(1000) → state 切 disconnected, 不再重连', () => {
    const { result } = renderHook(() => useWsAutoReconnect({
      url: 'ws://test/echo',
    }))
    act(() => { MockWebSocket.instances[0].triggerOpen() })
    expect(result.current.state).toBe('connected')

    act(() => { result.current.close(1000, 'app-quit') })
    expect(result.current.state).toBe('disconnected')

    // 等 1s + 100ms — 不该有新 ws 创建
    act(() => { vi.advanceTimersByTime(2000) })
    expect(MockWebSocket.instances).toHaveLength(1)
  })

  it('CASE6 累计 longDisconnectMs 仍未连上 → state 切 disconnected', () => {
    const { result } = renderHook(() => useWsAutoReconnect({
      url: 'ws://test/echo',
      longDisconnectMs: 60_000,
    }))
    act(() => { MockWebSocket.instances[0].triggerOpen() })

    // 长时间断 — 持续 close + 重连失败
    act(() => { MockWebSocket.instances[0].triggerClose(1006) })
    expect(result.current.state).toBe('reconnecting')

    // 推进 60s, longDisconnect timer fires → state 切 disconnected
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(result.current.state).toBe('disconnected')
  })
})
