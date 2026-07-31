import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CC_TAB_WORKING_TITLE, buildCcTabMetas, buildCcTabStates, chatTabRunState, ptyTabRunState } from './tabStatus'

describe('cc session tab status', () => {
  it('requires a submitted user turn before PTY output can be working', () => {
    expect(ptyTabRunState({ alive: true, working: true, has_user_turn: false, status: 'alive' })).toBe('done')
    expect(ptyTabRunState({ alive: true, working: true, has_user_turn: true, status: 'alive' })).toBe('working')
    expect(ptyTabRunState({ alive: true, working: false, has_user_turn: true, status: 'alive' })).toBe('done')
    expect(ptyTabRunState({ alive: false, working: false, has_user_turn: true, status: 'recoverable' })).toBe('ended')
  })

  it('uses the exact in-flight flag for structured chats', () => {
    expect(chatTabRunState({ alive: true, running: true, status: 'alive' })).toBe('working')
    expect(chatTabRunState({ alive: true, running: false, status: 'alive' })).toBe('done')
    expect(chatTabRunState({ alive: false, running: false, status: 'ended' })).toBe('ended')
  })

  it('builds one snapshot for both transports', () => {
    expect(buildCcTabStates(
      [{ id: 'pty-1', alive: true, working: true, has_user_turn: true, status: 'alive' }],
      [{ id: 'chat-1', alive: true, running: false, status: 'alive' }],
    )).toEqual({ 'pty-1': 'working', 'chat-1': 'done' })
  })

  it('prefers the cheap-model display title over the raw provider title', () => {
    expect(buildCcTabMetas(
      [{
        id: 'pty-1',
        provider: 'codex',
        cwd: 'E:\\work',
        provider_title: 'raw first prompt',
        display_title: '修复 CLI 会话自动标题',
      }],
      [],
    )['pty-1']?.title).toBe('修复 CLI 会话自动标题')
  })

  it('uses a concise accessible hint rather than a visible verbose label', () => {
    expect(CC_TAB_WORKING_TITLE).toBe('Agent 工作中')
    expect(CC_TAB_WORKING_TITLE).not.toContain('进行中')
  })
})

describe('cc session tab status polling store', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  function stubTabStates(payload: unknown) {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => payload,
    }))
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  it('reports working for background sessions from the unified in-memory snapshot', async () => {
    const fetchMock = stubTabStates({
      pty: [{ id: 'pty-bg', alive: true, working: true, has_user_turn: true, status: 'alive' }],
      chat: [{ id: 'chat-bg', alive: true, running: true, status: 'alive' }],
    })
    const { useCcTabRunState } = await import('./tabStatus')

    // 后台页签(终端未挂载)同样拿到运行态; 首轮快照到达前是 unknown 而非误报 done。
    const { result } = renderHook(() => useCcTabRunState('pty-bg', 'pty'))
    await waitFor(() => expect(result.current).toBe('working'))
    expect(fetchMock).toHaveBeenCalledWith('/api/cc/tab-states')

    const chat = renderHook(() => useCcTabRunState('chat-bg', 'chat'))
    expect(chat.result.current).toBe('working')
  })

  it('falls back to ended for ids absent from a loaded snapshot', async () => {
    stubTabStates({ pty: [], chat: [] })
    const { useCcTabRunState } = await import('./tabStatus')

    const { result } = renderHook(() => useCcTabRunState('pty-gone', 'pty'))
    await waitFor(() => expect(result.current).toBe('ended'))
  })

  it('keeps the previous snapshot across a transient poll failure', async () => {
    vi.useFakeTimers()
    try {
      let fail = false
      const fetchMock = vi.fn(async () => {
        if (fail) throw new Error('ccdaemon restarting')
        return {
          ok: true,
          json: async () => ({
            pty: [{ id: 'pty-1', alive: true, working: true, has_user_turn: true, status: 'alive' }],
            chat: [],
          }),
        }
      })
      vi.stubGlobal('fetch', fetchMock)
      const { useCcTabRunState } = await import('./tabStatus')

      const { result } = renderHook(() => useCcTabRunState('pty-1', 'pty'))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(result.current).toBe('working')

      // 一个轮询 tick 失败: 不清空状态(页签不闪), 恢复后照常更新。
      fail = true
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
      expect(result.current).toBe('working')
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)

      fail = false
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
      expect(result.current).toBe('working')
    } finally {
      vi.useRealTimers()
    }
  })
})
