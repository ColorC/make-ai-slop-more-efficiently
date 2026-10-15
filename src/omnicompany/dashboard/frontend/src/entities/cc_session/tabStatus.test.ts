import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CC_TAB_WORKING_TITLE, buildCcTabMetas, buildCcTabStates, chatTabRunState, ptyTabRunState, webTabRunState } from './tabStatus'

describe('cc session tab status', () => {
  it('requires a submitted user turn before PTY output can be working', () => {
    expect(ptyTabRunState({ alive: true, working: true, has_user_turn: false, status: 'alive' })).toBe('done')
    expect(ptyTabRunState({ alive: true, working: true, has_user_turn: true, status: 'alive' })).toBe('working')
    expect(ptyTabRunState({ alive: true, working: false, has_user_turn: true, status: 'alive' })).toBe('done')
    expect(ptyTabRunState({ alive: true, working: false, has_user_turn: true, provider_runtime_known: false, status: 'alive' })).toBe('unknown')
    expect(ptyTabRunState({ alive: false, working: false, has_user_turn: true, status: 'recoverable' })).toBe('ended')
  })

  it('uses the exact in-flight flag for structured chats', () => {
    expect(chatTabRunState({ alive: true, running: true, status: 'alive' })).toBe('working')
    expect(chatTabRunState({ alive: true, running: false, status: 'alive' })).toBe('done')
    expect(chatTabRunState({ alive: false, running: false, status: 'ended' })).toBe('ended')
  })

  it('maps web display sessions from their supervisor phase', () => {
    expect(webTabRunState({ alive: true, status: 'alive', web_phase: 'starting' })).toBe('working')
    expect(webTabRunState({ alive: true, status: 'alive', web_phase: 'ready' })).toBe('done')
    expect(webTabRunState({ alive: false, status: 'ended', web_phase: 'ended' })).toBe('ended')
  })

  it('builds one snapshot for both transports', () => {
    expect(buildCcTabStates(
      [{ id: 'pty-1', alive: true, working: true, has_user_turn: true, status: 'alive' }],
      [{ id: 'web-1', alive: true, status: 'alive', web_phase: 'starting' }],
      [{ id: 'chat-1', alive: true, running: false, status: 'alive' }],
    )).toEqual({ 'pty-1': 'working', 'web-1': 'working', 'chat-1': 'done' })
  })

  it('projects the complete authoritative session identity with its display title', () => {
    const meta = buildCcTabMetas(
      [{
        id: 'pty-1',
        provider: 'codex',
        cwd: 'E:\\work',
        provider_title: 'raw first prompt',
        display_title: '修复 CLI 会话自动标题',
        topic: '修复 CLI 会话自动标题',
        project: 'omnidashboard',
        workstream: 'omnidashboard.session-identity',
        active_plan: 'dashboard/[2026-08-15]SESSION-PROJECTION',
        binding_source: 'cli_bind',
        runtime_host: 'litework',
      }],
      [],
      [],
    )['pty-1']
    expect(meta?.providerTitle).toBe('raw first prompt')
    expect(meta?.displayTitle).toBe('修复 CLI 会话自动标题')
    expect(meta?.topic).toBe('修复 CLI 会话自动标题')
    expect(meta?.project).toBe('omnidashboard')
    expect(meta?.workstream).toBe('omnidashboard.session-identity')
    expect(meta?.activePlan).toBe('dashboard/[2026-08-15]SESSION-PROJECTION')
    expect(meta?.bindingSource).toBe('cli_bind')
    expect(meta?.runtimeHost).toBe('litework')
    expect(meta?.title).toBe('修复 CLI 会话自动标题')
  })

  it('keeps topic precedence over companion and provider titles', () => {
    const meta = buildCcTabMetas(
      [{
        id: 'f4a9aa541df34881', provider: 'grok', cwd: 'C:/workspace/',
        provider_title: 'raw first provider prompt',
        display_title: 'companion generated summary',
        topic: 'confirmed Walker material workflow',
      }],
      [],
      [],
    )['f4a9aa541df34881']
    expect(meta?.title).toBe('confirmed Walker material workflow')
  })

  it('projects whether a PTY has submitted real work for completion attention', () => {
    const meta = buildCcTabMetas(
      [{ id: 'pty-1', cwd: 'E:\\work', origin: 'agent', has_user_turn: true, last_submit_at: 40, last_output_at: 42 }],
      [],
      [],
    )['pty-1']
    expect(meta?.hasUserTurn).toBe(true)
    expect(meta?.lastActivityAt).toBe(42)
    expect(meta?.lastSubmitAt).toBe(40)
    expect(meta?.origin).toBe('agent')
  })

  it('projects web display sessions into the active-session list', () => {
    const meta = buildCcTabMetas(
      [],
      [{ id: 'web-1', provider: 'dsh_web', cwd: 'C:/workspace/', started_at: 1786681848, origin: 'human' }],
      [],
    )['web-1']
    expect(meta?.kind).toBe('pty')
    expect(meta?.provider).toBe('dsh_web')
    expect(meta?.startedAt).toBe(1786681848)
    expect(meta?.cwd).toBe('C:/workspace/')
  })

  it('uses a concise accessible hint rather than a visible verbose label', () => {
    expect(CC_TAB_WORKING_TITLE).toBe('Agent 工作中')
    expect(CC_TAB_WORKING_TITLE).not.toContain('进行中')
  })
})

describe('cc session tab status polling store', () => {
  let hasFocus: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    // jsdom reports false for document.hasFocus() even though these tests
    // represent the foreground dashboard unless a case explicitly overrides it.
    hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(true)
  })

  afterEach(() => {
    hasFocus?.mockRestore()
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
    expect(fetchMock).toHaveBeenCalledWith('/api/cc/tab-states', { cache: 'no-store' })

    const chat = renderHook(() => useCcTabRunState('chat-bg', 'chat'))
    expect(chat.result.current).toBe('working')
  })

  it('falls back to ended for ids absent from a loaded snapshot', async () => {
    stubTabStates({ pty: [], chat: [] })
    const { useCcTabRunState } = await import('./tabStatus')

    const { result } = renderHook(() => useCcTabRunState('pty-gone', 'pty'))
    await waitFor(() => expect(result.current).toBe('ended'))
  })

  it('does not rerender one session when only another session changes', async () => {
    let otherWorking = true
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        pty: [
          { id: 'target', alive: true, working: false, has_user_turn: true, status: 'alive' },
          { id: 'other', alive: true, working: otherWorking, has_user_turn: true, status: 'alive' },
        ],
        chat: [],
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { refreshCcTabStates, useCcTabRunState } = await import('./tabStatus')
    let renders = 0
    const view = renderHook(() => {
      renders += 1
      return useCcTabRunState('target', 'pty')
    })
    await waitFor(() => expect(view.result.current).toBe('done'))
    const settledRenders = renders

    otherWorking = false
    await act(async () => { await refreshCcTabStates() })

    expect(view.result.current).toBe('done')
    expect(renders).toBe(settledRenders)
    view.unmount()
  })

  it('includes web display sessions from the unified snapshot', async () => {
    stubTabStates({
      pty: [],
      web: [{
        id: 'web-1', alive: true, status: 'alive', provider: 'dsh_web',
        cwd: 'C:/workspace/', started_at: 1786681848, web_phase: 'ready', origin: 'human',
      }],
      chat: [],
    })
    const { useCcTabRunState, useCcTabStatusSnapshot } = await import('./tabStatus')

    const state = renderHook(() => useCcTabRunState('web-1', 'pty'))
    await waitFor(() => expect(state.result.current).toBe('done'))

    const { result: snap } = renderHook(() => useCcTabStatusSnapshot())
    expect(snap.current.metas['web-1']?.provider).toBe('dsh_web')
    expect(snap.current.metas['web-1']?.kind).toBe('pty')
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

  it('lazily polls hidden dashboards and refreshes immediately on return', async () => {
    vi.useFakeTimers()
    const visibilityState = vi.spyOn(document, 'visibilityState', 'get')
    visibilityState.mockReturnValue('visible')
    try {
      const fetchMock = stubTabStates({ pty: [], chat: [] })
      const { useCcTabStatusSnapshot } = await import('./tabStatus')
      const view = renderHook(() => useCcTabStatusSnapshot())

      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(fetchMock).toHaveBeenCalledTimes(1)

      visibilityState.mockReturnValue('hidden')
      document.dispatchEvent(new Event('visibilitychange'))
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
      expect(fetchMock).toHaveBeenCalledTimes(1)

      await act(async () => { await vi.advanceTimersByTimeAsync(50_000) })
      expect(fetchMock).toHaveBeenCalledTimes(2)

      visibilityState.mockReturnValue('visible')
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchMock).toHaveBeenCalledTimes(3)
      view.unmount()
    } finally {
      visibilityState.mockRestore()
      vi.useRealTimers()
    }
  })

  it('backs off when the dashboard window is visible but unfocused', async () => {
    vi.useFakeTimers()
    try {
      const fetchMock = stubTabStates({ pty: [], chat: [] })
      const { useCcTabStatusSnapshot } = await import('./tabStatus')
      const view = renderHook(() => useCcTabStatusSnapshot())

      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(fetchMock).toHaveBeenCalledTimes(1)

      window.localStorage.setItem('omni.dashboard.idle.v1', '1')
      hasFocus.mockReturnValue(false)
      window.dispatchEvent(new Event('blur'))
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
      expect(fetchMock).toHaveBeenCalledTimes(1)

      hasFocus.mockReturnValue(true)
      window.dispatchEvent(new Event('focus'))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(fetchMock).toHaveBeenCalledTimes(2)
      window.localStorage.removeItem('omni.dashboard.idle.v1')
      view.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})
