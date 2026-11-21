import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  isSessionCompletionUnseen,
  sessionCompletionFingerprint,
  useSessionCompletionAttention,
} from './sessionAttention'

describe('session completion attention', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useSessionCompletionAttention.setState({ seen: {} })
  })

  it('only flags a completed session that contains real user work', () => {
    const emptyPty = { kind: 'pty' as const, hasUserTurn: false, lastActivityAt: 10 }
    const completedPty = { kind: 'pty' as const, hasUserTurn: true, lastSubmitAt: 20, lastActivityAt: 25 }
    expect(isSessionCompletionUnseen('done', 'pty-1', emptyPty, {})).toBe(false)
    expect(isSessionCompletionUnseen('working', 'pty-1', completedPty, {})).toBe(false)
    expect(isSessionCompletionUnseen('done', 'pty-1', completedPty, {})).toBe(true)
  })

  it('clears after viewing and flags a later completed turn again', () => {
    const first = { kind: 'pty' as const, hasUserTurn: true, startedAt: 1, lastSubmitAt: 20, lastActivityAt: 25 }
    useSessionCompletionAttention.getState().markSeen('pty-1', first)
    expect(isSessionCompletionUnseen(
      'done',
      'pty-1',
      first,
      useSessionCompletionAttention.getState().seen,
    )).toBe(false)

    const later = { ...first, lastSubmitAt: 30, lastActivityAt: 35 }
    expect(sessionCompletionFingerprint('pty-1', later)).not.toBe(sessionCompletionFingerprint('pty-1', first))
    expect(isSessionCompletionUnseen(
      'done',
      'pty-1',
      later,
      useSessionCompletionAttention.getState().seen,
    )).toBe(true)
  })

  it('does not resurrect unread when an idle TUI only repaints output', () => {
    const viewed = {
      kind: 'pty' as const,
      hasUserTurn: true,
      startedAt: 1,
      lastSubmitAt: 20,
      lastActivityAt: 25,
    }
    useSessionCompletionAttention.getState().markSeen('pty-1', viewed)

    const idleRepaint = { ...viewed, lastActivityAt: 200 }
    expect(sessionCompletionFingerprint('pty-1', idleRepaint))
      .toBe(sessionCompletionFingerprint('pty-1', viewed))
    expect(isSessionCompletionUnseen(
      'done',
      'pty-1',
      idleRepaint,
      useSessionCompletionAttention.getState().seen,
    )).toBe(false)
  })

  it('keeps a completion acknowledged by the old output-based fingerprint', () => {
    const meta = {
      kind: 'pty' as const,
      hasUserTurn: true,
      startedAt: 1,
      lastSubmitAt: 20,
      lastActivityAt: 200,
    }
    const current = sessionCompletionFingerprint('pty-1', meta) || ''
    const legacySeen = `pty-1:pty:1:200${current.slice(current.lastIndexOf(':'))}`

    expect(isSessionCompletionUnseen('done', 'pty-1', meta, { 'pty-1': legacySeen })).toBe(false)
  })
})

/* 已看记录的真源在服务端（计划 FOCUS-INBOX 阶段 2.2b）——
 * 本地那份只是缓存 + 乐观更新, 但「记下来」这件事必须真的到后端, 否则换台设备就丢。 */
describe('session completion attention · 服务端同步', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useSessionCompletionAttention.setState({ seen: {} })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('看过一轮要报到服务端, 只报 sessionId 由后端定是哪一轮', async () => {
    const calls: Array<{ path: string; body: Record<string, unknown> }> = []
    vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit) => {
      calls.push({ path, body: JSON.parse(String(init.body)) })
      return { ok: true, json: async () => ({ ok: true }) }
    }))

    const meta = { kind: 'pty' as const, hasUserTurn: true, startedAt: 1, lastSubmitAt: 20 }
    useSessionCompletionAttention.getState().markSeen('pty-1', meta)

    // 本地先生效: 不等网络往返, 点进去就不再标未读。
    expect(useSessionCompletionAttention.getState().seen['pty-1']).toBeTruthy()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].path).toBe('/api/inbox/sessions/seen')
    expect(calls[0].body.sessionId).toBe('pty-1')
  })

  it('服务端不可达时退化成纯本地, 不抛也不清掉本地记录', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    const meta = { kind: 'pty' as const, hasUserTurn: true, startedAt: 1, lastSubmitAt: 20 }
    expect(() => useSessionCompletionAttention.getState().markSeen('pty-1', meta)).not.toThrow()
    expect(useSessionCompletionAttention.getState().seen['pty-1']).toBeTruthy()
  })
})
