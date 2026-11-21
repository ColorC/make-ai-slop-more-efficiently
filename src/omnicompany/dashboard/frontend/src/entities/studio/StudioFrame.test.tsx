import React from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StudioFrame, { STUDIO_PAGE_URL } from './StudioFrame'
import StudioView from './StudioView'
import { resetStudioSessions, useStudioSessions } from './studioSessions'
import { ccApi } from '../../api/ccClient'
import type { CcSessionMeta } from '../../api/ccClient'
import { usePanels } from '../../stores/panelsStore'
import { usePanelVisible } from '../../shell/panelRuntime'

// 会话区挂的是会话页签同款组件(cc_session 的 EditorRouter); 单测里换成一个只报 id 和可见性的替身, 不拉 xterm。
vi.mock('../cc_session/EditorRouter', () => ({
  default: ({ entity }: { entity: { id: string } }) => {
    const visible = usePanelVisible()
    return <div data-testid="stub-editor" data-entity={entity.id} data-visible={visible ? '1' : '0'} />
  },
}))

const COMPANY = {
  project: 'hunao-games',
  bootstrap_task: '开工。先读 AGENTS.md。',
  staff: [
    {
      id: 'claude', name: 'Claude', provider: 'claude_2', model: 'fable', effort: 'high', dir: 'C:/workspace/hunao-games/staff/claude',
      sources: [
        { provider: 'claude_2', model: 'fable', effort: 'high' },
        { provider: 'claude_code', model: 'claude-opus-5-5', effort: null },
      ],
    },
    { id: 'deepseek', name: 'DeepSeek', provider: 'opencode', model: 'deepseek-v4.1-flash', effort: null, dir: 'C:/workspace/hunao-games/staff/deepseek' },
  ],
}

function meta(id: string, alive = true): CcSessionMeta {
  return { id, cmd: ['claude'], cwd: 'E:/x', cols: 120, rows: 30, started_at: 1794800000, alive, status: alive ? 'alive' : 'recoverable' } as CcSessionMeta
}

function stubFetch(companySessions: string[] = []) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/studio/company') return { ok: true, status: 200, json: async () => COMPANY }
    if (url === '/api/studio/sessions') return { ok: true, status: 200, json: async () => ({ sessions: companySessions.map((id) => ({ id })) }) }
    return { ok: true, status: 200 }
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function mountFrame(view: 'chat' | 'scene' = 'chat') {
  render(<StudioFrame view={view} />)
  return spyFrame()
}

async function mountView() {
  render(<StudioView />)
  return spyFrame()
}

async function spyFrame() {
  const frame = await screen.findByTestId('studio-frame') as HTMLIFrameElement
  const win = frame.contentWindow!
  const posted = vi.spyOn(win, 'postMessage').mockImplementation(() => {})
  return { frame, win, posted }
}

function send(data: unknown, source: Window | null, origin = window.location.origin) {
  window.dispatchEvent(new MessageEvent('message', { data, origin, source }))
}

function openIds() {
  return useStudioSessions.getState().open.map((item) => item.id)
}

function tab(id: string) {
  return screen.getAllByTestId('studio-session-tab').find((el) => el.getAttribute('data-session-id') === id)!
}

describe('StudioFrame', () => {
  let openTab: ReturnType<typeof vi.fn>
  const originalOpenTab = usePanels.getState().openTab

  beforeEach(() => {
    resetStudioSessions()
    openTab = vi.fn(() => 'tab-id')
    usePanels.setState({ openTab: openTab as never })
  })

  afterEach(() => {
    usePanels.setState({ openTab: originalOpenTab })
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    resetStudioSessions()
  })

  it('embeds the published Studio page in a full-size iframe', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200 })))
    render(<StudioFrame testid="host-x" view="chat" />)
    const frame = await screen.findByTestId('studio-frame')
    expect(frame.getAttribute('src')).toBe('/api/embed/studio/index.html?view=chat')
    expect(STUDIO_PAGE_URL).toBe('/api/embed/studio/index.html')
    expect(frame.parentElement?.getAttribute('data-testid')).toBe('host-x')
    expect(screen.queryByTestId('studio-missing')).toBeNull()
  })

  it('the Studio tab puts the chat view on the left and an empty session area on the right', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200 })))
    render(<StudioView />)
    const frame = await screen.findByTestId('studio-frame')
    expect(frame.parentElement?.getAttribute('data-testid')).toBe('studio-chat')
    expect(frame.getAttribute('src')).toBe('/api/embed/studio/index.html?view=chat')
    const root = screen.getByTestId('studio-view')
    expect(root.getAttribute('data-layout')).toBe('wide')
    expect(screen.getByTestId('studio-session-empty').textContent).toBe('从左边点一个会话，或给员工开一个新会话')
    expect(screen.queryAllByTestId('studio-session-tab')).toHaveLength(0)
  })

  it('shows one line instead of a blank page when the Studio page is not published', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 })))
    render(<StudioFrame view="scene" />)
    expect((await screen.findByTestId('studio-missing')).textContent).toBe('Studio 页面还没发布')
    expect(screen.queryByTestId('studio-frame')).toBeNull()
  })

  it('creates a visible staff session from staff.json, opens it in the session area and reports back', async () => {
    const fetchMock = stubFetch()
    const create = vi.spyOn(ccApi, 'createAgentWorker').mockResolvedValue(meta('pty-new-claude'))
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-new-claude')])
    const { win, posted } = await mountView()

    send({ type: 'omni-studio:new-session', staff: 'claude', task: '先把立项文档写了' }, win)

    await waitFor(() => expect(posted).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledWith('/api/studio/company', expect.anything())
    expect(create).toHaveBeenCalledTimes(1)
    expect(create).toHaveBeenCalledWith({
      provider: 'claude_2',
      model: 'fable',
      effort: 'high',
      cwd: 'C:/workspace/hunao-games/staff/claude',
      project: 'hunao-games',
      topic: 'studio:claude',
      task: '开工。先读 AGENTS.md。\n老板交代：先把立项文档写了',
      visibility: 'visible',
    })
    expect(posted).toHaveBeenCalledWith(
      { type: 'omni-studio:session-created', staff: 'claude', session: 'pty-new-claude' },
      window.location.origin,
    )
    // 不再另开主区域页签: 就在 Studio 页签的会话区里
    expect(openTab).not.toHaveBeenCalled()
    expect(openIds()).toEqual(['pty-new-claude'])
    expect(tab('pty-new-claude').textContent).toContain('Claude · pty-new-')
    expect(tab('pty-new-claude').getAttribute('aria-selected')).toBe('true')
    const editor = await screen.findByTestId('stub-editor')
    expect(editor.getAttribute('data-entity')).toBe('pty-new-claude')
    expect(editor.getAttribute('data-visible')).toBe('1')
    expect(screen.queryByTestId('studio-session-empty')).toBeNull()
  })

  it('starts the session on the source the page picked, only if staff.json lists it', async () => {
    stubFetch()
    const create = vi.spyOn(ccApi, 'createAgentWorker').mockResolvedValue(meta('pty-claude-1'))
    const { win, posted } = await mountFrame()

    send({ type: 'omni-studio:new-session', staff: 'claude', source: 'claude_code' }, win)
    await waitFor(() => expect(posted).toHaveBeenCalled())
    expect(create.mock.calls[0][0]).toMatchObject({ provider: 'claude_code', model: 'claude-opus-5-5', topic: 'studio:claude' })
    expect(create.mock.calls[0][0].effort).toBeUndefined()

    posted.mockClear()
    send({ type: 'omni-studio:new-session', staff: 'claude', source: 'codex' }, win)
    await waitFor(() => expect(posted).toHaveBeenCalled())
    expect(create).toHaveBeenCalledTimes(1)
    expect(posted.mock.calls[0][0]).toMatchObject({ type: 'omni-studio:error' })

    posted.mockClear()
    send({ type: 'omni-studio:new-session', staff: 'deepseek', source: 'opencode' }, win)
    await waitFor(() => expect(posted).toHaveBeenCalled())
    expect(create.mock.calls[1][0]).toMatchObject({ provider: 'opencode', model: 'deepseek-v4.1-flash' })
  })

  it('uses the bootstrap task alone and omits a null effort', async () => {
    stubFetch()
    const create = vi.spyOn(ccApi, 'createAgentWorker').mockResolvedValue(meta('pty-ds'))
    const { win, posted } = await mountFrame()

    send({ type: 'omni-studio:new-session', staff: 'deepseek' }, win)

    await waitFor(() => expect(posted).toHaveBeenCalled())
    const body = create.mock.calls[0][0]
    expect(body.task).toBe('开工。先读 AGENTS.md。')
    expect(body.effort).toBeUndefined()
    expect(body.topic).toBe('studio:deepseek')
    expect(body.project).toBe('hunao-games')
    expect(useStudioSessions.getState().open).toEqual([{ id: 'pty-ds', label: 'DeepSeek · pty-ds' }])
  })

  it('reports an error when the session cannot be created', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'createAgentWorker').mockRejectedValue(new Error('provider offline'))
    const { win, posted } = await mountFrame()

    send({ type: 'omni-studio:new-session', staff: 'claude' }, win)

    await waitFor(() => expect(posted).toHaveBeenCalled())
    const [message] = posted.mock.calls[0]
    expect(message).toMatchObject({ type: 'omni-studio:error' })
    expect((message as { message: string }).message).toContain('provider offline')
    expect(openTab).not.toHaveBeenCalled()
    expect(openIds()).toEqual([])
  })

  it('ignores messages that do not come from this iframe or from this origin', async () => {
    stubFetch(['pty-live'])
    const create = vi.spyOn(ccApi, 'createAgentWorker').mockResolvedValue(meta('pty-x'))
    const list = vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-live')])
    const kill = vi.spyOn(ccApi, 'kill').mockResolvedValue()
    const { win, posted } = await mountFrame()

    send({ type: 'omni-studio:new-session', staff: 'claude' }, window)
    send({ type: 'omni-studio:new-session', staff: 'claude' }, null)
    send({ type: 'omni-studio:open-session', session: 'pty-live' }, win, 'https://evil.example')
    send({ type: 'omni-studio:end-session', session: 'pty-live' }, window)
    send({ type: 'omni-studio:end-session', session: 'pty-live' }, win, 'https://evil.example')

    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(create).not.toHaveBeenCalled()
    expect(list).not.toHaveBeenCalled()
    expect(kill).not.toHaveBeenCalled()
    expect(openTab).not.toHaveBeenCalled()
    expect(posted).not.toHaveBeenCalled()
    expect(openIds()).toEqual([])
  })

  it('opens a live session in the session area with the label the page gave', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-live')])
    const { win, posted } = await mountView()

    send({ type: 'omni-studio:open-session', session: 'pty-live', label: 'DeepSeek · 代肝：日常' }, win)

    await waitFor(() => expect(openIds()).toEqual(['pty-live']))
    expect(tab('pty-live').textContent).toContain('DeepSeek · 代肝：日常')
    expect((await screen.findByTestId('stub-editor')).getAttribute('data-entity')).toBe('pty-live')
    expect(openTab).not.toHaveBeenCalled()
    expect(posted).not.toHaveBeenCalled()
  })

  it('reports an error instead of opening an ended session', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-dead', false)])
    const { win, posted } = await mountFrame()

    send({ type: 'omni-studio:open-session', session: 'pty-dead' }, win)
    send({ type: 'omni-studio:open-session', session: 'pty-missing' }, win)

    await waitFor(() => expect(posted).toHaveBeenCalledTimes(2))
    for (const [message] of posted.mock.calls) {
      expect(message).toMatchObject({ type: 'omni-studio:error' })
    }
    expect(openTab).not.toHaveBeenCalled()
    expect(openIds()).toEqual([])
  })

  it('a session opened from the sidebar scene brings the Studio tab to the front', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-live')])
    const { win } = await mountFrame('scene')

    send({ type: 'omni-studio:open-session', session: 'pty-live' }, win)

    await waitFor(() => expect(openTab).toHaveBeenCalledWith({ type: 'studio', id: 'main' }, 'Studio'))
    expect(useStudioSessions.getState().open).toEqual([{ id: 'pty-live', label: 'pty-live' }])
  })

  it('switches between open sessions; hidden ones stay mounted but invisible', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-a'), meta('pty-b')])
    act(() => {
      useStudioSessions.getState().openSession('pty-a', 'Claude · A')
      useStudioSessions.getState().openSession('pty-b', 'GPT · B')
    })
    await mountView()

    await waitFor(() => expect(screen.getAllByTestId('stub-editor')).toHaveLength(2))
    const visibility = () => Object.fromEntries(
      screen.getAllByTestId('stub-editor').map((el) => [el.getAttribute('data-entity'), el.getAttribute('data-visible')]),
    )
    expect(visibility()).toEqual({ 'pty-a': '0', 'pty-b': '1' })

    fireEvent.click(tab('pty-a'))
    expect(visibility()).toEqual({ 'pty-a': '1', 'pty-b': '0' })
    expect(tab('pty-a').getAttribute('aria-selected')).toBe('true')
  })

  it('the end button kills the session right away, drops its tab and tells the page to refresh', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-a'), meta('pty-b')])
    const kill = vi.spyOn(ccApi, 'kill').mockResolvedValue()
    const confirm = vi.spyOn(window, 'confirm')
    act(() => {
      useStudioSessions.getState().openSession('pty-a', 'Claude · A')
      useStudioSessions.getState().openSession('pty-b', 'GPT · B')
    })
    const { posted } = await mountView()

    fireEvent.click(within(tab('pty-b')).getByTestId('studio-session-end'))

    await waitFor(() => expect(openIds()).toEqual(['pty-a']))
    expect(kill).toHaveBeenCalledWith('pty-b')
    expect(confirm).not.toHaveBeenCalled()
    expect(screen.getAllByTestId('studio-session-tab').map((el) => el.getAttribute('data-session-id'))).toEqual(['pty-a'])
    expect(tab('pty-a').getAttribute('aria-selected')).toBe('true')
    await waitFor(() => expect(posted).toHaveBeenCalledWith({ type: 'omni-studio:sessions-changed', ended: 'pty-b' }, window.location.origin))
  })

  it('still drops the tab when the session had already exited, and shows an error when it is still alive', async () => {
    stubFetch()
    const list = vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-a'), meta('pty-b')])
    vi.spyOn(ccApi, 'kill').mockRejectedValue(new Error('cc/sessions delete: 500'))
    act(() => {
      useStudioSessions.getState().openSession('pty-a', 'A')
      useStudioSessions.getState().openSession('pty-b', 'B')
    })
    await mountView()
    await waitFor(() => expect(screen.getAllByTestId('stub-editor')).toHaveLength(2))

    fireEvent.click(within(tab('pty-b')).getByTestId('studio-session-end'))
    expect((await screen.findByTestId('studio-session-error')).textContent).toContain('cc/sessions delete: 500')
    expect(openIds()).toEqual(['pty-a', 'pty-b'])

    list.mockResolvedValue([meta('pty-a')])
    fireEvent.click(within(tab('pty-b')).getByTestId('studio-session-end'))
    await waitFor(() => expect(openIds()).toEqual(['pty-a']))
  })

  it('collapse (button or middle click) only hides the session; the list can bring it back', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-a'), meta('pty-b')])
    const kill = vi.spyOn(ccApi, 'kill').mockResolvedValue()
    act(() => {
      useStudioSessions.getState().openSession('pty-a', 'Claude · A')
      useStudioSessions.getState().openSession('pty-b', 'GPT · B')
    })
    const { win } = await mountView()

    fireEvent.click(within(tab('pty-b')).getByTestId('studio-session-collapse'))
    expect(openIds()).toEqual(['pty-a'])

    fireEvent(tab('pty-a'), new MouseEvent('auxclick', { bubbles: true, button: 1 }))
    expect(openIds()).toEqual([])
    expect(screen.getByTestId('studio-session-empty')).toBeTruthy()
    expect(kill).not.toHaveBeenCalled()

    send({ type: 'omni-studio:open-session', session: 'pty-b', label: 'GPT · B' }, win)
    await waitFor(() => expect(openIds()).toEqual(['pty-b']))
  })

  it('ends a company session when the page asks, and refuses one that is not the company\'s', async () => {
    stubFetch(['pty-live'])
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-live'), meta('pty-other')])
    const kill = vi.spyOn(ccApi, 'kill').mockResolvedValue()
    act(() => useStudioSessions.getState().openSession('pty-live', 'Claude · live'))
    const { win, posted } = await mountView()

    send({ type: 'omni-studio:end-session', session: 'pty-live' }, win)
    await waitFor(() => expect(kill).toHaveBeenCalledWith('pty-live'))
    await waitFor(() => expect(posted).toHaveBeenCalledWith({ type: 'omni-studio:sessions-changed', ended: 'pty-live' }, window.location.origin))
    expect(openIds()).toEqual([])

    posted.mockClear()
    send({ type: 'omni-studio:end-session', session: 'pty-other' }, win)
    await waitFor(() => expect(posted).toHaveBeenCalled())
    expect(posted.mock.calls[0][0]).toMatchObject({ type: 'omni-studio:error' })
    expect(kill).toHaveBeenCalledTimes(1)
  })

  it('a session bound to the company project is ended without the slow company list lookup', async () => {
    const fetchMock = stubFetch([])
    vi.spyOn(ccApi, 'list').mockResolvedValue([{ ...meta('pty-bound'), project: 'hunao-games' }, { ...meta('pty-staff'), topic: 'studio:gpt' }])
    const kill = vi.spyOn(ccApi, 'kill').mockResolvedValue()
    const { win } = await mountFrame()

    send({ type: 'omni-studio:end-session', session: 'pty-bound' }, win)
    send({ type: 'omni-studio:end-session', session: 'pty-staff' }, win)

    await waitFor(() => expect(kill).toHaveBeenCalledTimes(2))
    expect(kill).toHaveBeenCalledWith('pty-bound')
    expect(kill).toHaveBeenCalledWith('pty-staff')
    expect(fetchMock).not.toHaveBeenCalledWith('/api/studio/sessions', expect.anything())
  })

  it('drops a remembered session that has ended instead of resuming it', async () => {
    stubFetch()
    vi.spyOn(ccApi, 'list').mockResolvedValue([meta('pty-dead', false)])
    const resume = vi.spyOn(ccApi, 'resume')
    act(() => useStudioSessions.getState().openSession('pty-dead', 'Claude · old'))
    await mountView()

    await waitFor(() => expect(openIds()).toEqual([]))
    expect(resume).not.toHaveBeenCalled()
    expect(screen.queryByTestId('stub-editor')).toBeNull()
  })
})
