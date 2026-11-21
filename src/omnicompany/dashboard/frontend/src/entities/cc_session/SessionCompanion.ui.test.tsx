import React, { useEffect } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanels } from '../../stores/panelsStore'

vi.mock('../../api/ccClient', () => ({
  ccApi: {
    context: vi.fn(async (sessionId: string) => ({
      session_id: sessionId,
      context: {
        project: 'omnidashboard',
        active_plan: 'plans/[2026-08-11]SIDEBAR',
      },
      modified_files: [], bash_writes: [], added_workers: [], added_materials: [], event_count: 0,
    })),
    list: vi.fn(async () => [{
      id: 'pty-1', alive: true, status: 'alive', provider: 'codex', cwd: 'E:\\work',
      cmd: ['codex'], cols: 120, rows: 32, started_at: 1,
    }]),
    create: vi.fn(),
    kill: vi.fn(),
  },
}))

vi.mock('../studio_reader/WorkshopSurface', () => ({
  WorkshopSurface: ({
    kind, project, sessionId, card, onReady,
  }: {
    kind: string
    project?: string
    sessionId?: string
    card?: string
    onReady?: () => void
  }) => {
    useEffect(() => { onReady?.() }, [onReady])
    return (
      <div
        data-testid={`workshop-surface-${kind}`}
        data-project={project ?? ''}
        data-session={sessionId ?? ''}
        data-card={card ?? ''}
      />
    )
  },
}))

const runState = vi.hoisted(() => ({ value: 'done' as string }))
vi.mock('./tabStatus', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./tabStatus')>()),
  useCcTabRunState: () => runState.value,
}))
vi.mock('../workshop', () => ({
  WorkshopPageFrame: ({ src }: { src: string }) => <div data-testid="results-canvas-frame" data-src={src} />,
}))

vi.mock('../multiagent/MultiagentView', () => ({ default: () => <div>会话列表</div> }))
vi.mock('../review_queue/ReviewBoard', () => ({
  ReviewBoard: () => <div data-testid="review-board-stub" />,
}))
vi.mock('../project/ProjectDetail', () => ({
  default: ({ entity, wikiOnly }: { entity: { id: string }; wikiOnly?: boolean }) => (
    <div data-testid="project-detail-stub" data-project={entity.id} data-wiki-only={wikiOnly ? '1' : '0'} />
  ),
}))

import SessionCompanion from './SessionCompanion'

describe('SessionCompanion information hierarchy', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', undefined)
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      unobserve() {}
      disconnect() {}
    })
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ ok: true, result: null }),
    })))
    usePanels.setState({
      tabs: [{ id: 'cc_session:pty-1', ref: { type: 'cc_session', id: 'pty-1' }, title: '会话' }],
      activeId: 'cc_session:pty-1',
    } as never)
  })

  it('has the session, canvas, results and Studio pages, opening on the session page', async () => {
    render(<SessionCompanion sessionId="pty-1" />)
    const nav = screen.getByRole('navigation', { name: '会话信息页面' })
    const pages = nav.querySelectorAll('.cccp-nav-pages [data-testid^="companion-nav-"]')
    expect(Array.from(pages).map((item) => item.textContent)).toEqual(['会话', '画布', '结果', 'Studio'])
    expect(Array.from(pages).map((item) => item.getAttribute('data-testid'))).toEqual([
      'companion-nav-multiagent', 'companion-nav-notes', 'companion-nav-results', 'companion-nav-studio',
    ])
    expect(screen.queryByTestId('companion-nav-overview')).toBeNull()
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('multiagent')
    // Studio 首次进入前不挂 iframe
    expect(screen.getByTestId('companion-studio').hasAttribute('hidden')).toBe(true)
    expect(screen.queryByTestId('studio-frame')).toBeNull()
    await screen.findByText('会话列表')
    expect(screen.queryByTestId('project-detail-stub')).toBeNull()
    expect(screen.queryByTestId('review-board-stub')).toBeNull()
  })

  it('shows the session products as tiles and opens a card fullscreen without any summary text', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const target = String(url)
      const body = target.includes('/turn-results') ? {
        session_id: 'pty-1', provider: 'claude_code', owner_device: true, turn_key: 'u-2', prompt: '做一下', scope: 'session',
        canvases: [{ project: 'demogame', canvas: 'c1', card: 'k1', url: 'https://workshop.example.com/bside/canvas?project=demogame&canvas=c1#card=k1' }],
        files: [],
        media: [{ url: 'https://cdn.example/shot.png', kind: 'image' }],
      } : target.startsWith('/bside/api/canvases/c1') ? {
        document: { title: '活动画布', cards: [{ id: 'k1', kind: 'text', adapter: 'text', title: '方案卡', source: { editableId: 'tx1' } }] },
      } : target.startsWith('/bside/lofa/overlay/invoke') ? {
        ok: true, result: { items: { tx1: { editableId: 'tx1', content: ['# 标题', '', '正文**直接**可见'].join(String.fromCharCode(10)) } } },
      } : { ok: true, result: null }
      return { ok: true, status: 200, json: async () => body }
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<SessionCompanion sessionId="pty-1" />)
    fireEvent.click(screen.getByTestId('companion-nav-results'))
    const tiles = await screen.findAllByTestId('result-tile')
    expect(tiles.map((tile) => tile.getAttribute('data-kind'))).toEqual(['image', 'card'])
    await waitFor(() => expect(tiles[1]!.textContent).toContain('方案卡'))
    expect(screen.queryByTestId('results-judgments')).toBeNull()
    fireEvent.click(tiles[1]!)
    const viewer = await screen.findByTestId('result-viewer')
    await waitFor(() => expect(viewer.textContent).toContain('正文直接可见'))
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('result-viewer')).toBeNull())
  })

  it('reads the session binding once instead of polling it', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    try {
      const { ccApi } = await import('../../api/ccClient')
      const context = ccApi.context as ReturnType<typeof vi.fn>
      context.mockClear()
      render(<SessionCompanion sessionId="pty-1" />)
      await waitFor(() => expect(context).toHaveBeenCalledTimes(1))
      vi.advanceTimersByTime(60_000)
      await Promise.resolve()
      expect(context).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the multiagent session page', async () => {
    render(<SessionCompanion sessionId="pty-1" />)
    fireEvent.click(screen.getByTestId('companion-nav-multiagent'))
    await screen.findByText('会话列表')
  })

  it('shows one session-bound note surface from the notes page', async () => {
    render(<SessionCompanion sessionId="pty-1" />)
    const preloaded = screen.getByTestId('companion-notes')
    expect(preloaded.hasAttribute('hidden')).toBe(true)
    expect(screen.queryByTestId('companion-notes-host')).toBeNull()
    fireEvent.click(screen.getByTestId('companion-nav-notes'))

    const host = await screen.findByTestId('companion-notes-host')
    expect(preloaded.hasAttribute('hidden')).toBe(false)
    expect(host.getAttribute('data-session-id')).toBe('pty-1')
    expect(screen.getAllByTestId('companion-notes-host')).toHaveLength(1)

    await waitFor(() => expect(screen.queryByTestId('companion-notes-loading')).toBeNull())
    expect(host.querySelector('[data-testid="workshop-surface-canvas"]')).toBeTruthy()
  })

  it('mounts the Studio iframe on first entry and only hides it when switching pages', async () => {
    render(<SessionCompanion sessionId="pty-1" />)
    fireEvent.click(screen.getByTestId('companion-nav-studio'))
    const page = screen.getByTestId('companion-studio')
    expect(page.hasAttribute('hidden')).toBe(false)
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('studio')
    const frame = await screen.findByTestId('studio-frame')
    // 侧栏只放聊天视图; 3D 场景在主区域的 Studio 页签
    expect(frame.getAttribute('src')).toBe('/api/embed/studio/index.html?view=scene')
    expect(page.contains(frame)).toBe(true)

    fireEvent.click(screen.getByTestId('companion-nav-notes'))
    expect(page.hasAttribute('hidden')).toBe(true)
    expect(screen.getByTestId('studio-frame')).toBe(frame)

    fireEvent.click(screen.getByTestId('companion-nav-multiagent'))
    fireEvent.click(screen.getByTestId('companion-nav-studio'))
    expect(page.hasAttribute('hidden')).toBe(false)
    expect(screen.getByTestId('studio-frame')).toBe(frame)
    expect(screen.getAllByTestId('studio-frame')).toHaveLength(1)
  })

  it('opens or focuses the single Studio scene tab when the Studio page is clicked', async () => {
    render(<SessionCompanion sessionId="pty-1" />)
    fireEvent.click(screen.getByTestId('companion-nav-studio'))
    expect(usePanels.getState().activeId).toBe('studio:main')
    const studioTabs = usePanels.getState().tabs.filter((tab) => tab.ref.type === 'studio')
    expect(studioTabs).toHaveLength(1)
    expect(studioTabs[0]).toMatchObject({ id: 'studio:main', title: 'Studio' })

    // 切回会话页签再点一次: 还是同一个场景页签
    act(() => { usePanels.getState().activate('cc_session:pty-1') })
    fireEvent.click(screen.getByTestId('companion-nav-studio'))
    expect(usePanels.getState().activeId).toBe('studio:main')
    expect(usePanels.getState().tabs.filter((tab) => tab.ref.type === 'studio')).toHaveLength(1)
  })

  it('stays on Studio with the same iframe when the shared sidebar switches session', async () => {
    const { rerender } = render(<SessionCompanion sessionId="pty-1" />)
    fireEvent.click(screen.getByTestId('companion-nav-studio'))
    const frame = await screen.findByTestId('studio-frame')
    act(() => { usePanels.getState().activate('cc_session:pty-1') })

    // 共享侧栏: 从 Studio 开了新会话页签后, 同一实例换 sessionId, 页与 iframe 都不动,
    // 也不把主区域抢回 Studio 场景页签。
    rerender(<SessionCompanion sessionId="pty-2" />)
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('studio')
    expect(screen.getByTestId('studio-frame')).toBe(frame)
    expect(usePanels.getState().activeId).toBe('cc_session:pty-1')
  })

  it('parks on the Studio chat page while the Studio scene tab is in front, without reopening it', async () => {
    const { rerender } = render(<SessionCompanion sessionId="pty-1" />)
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('multiagent')

    rerender(<SessionCompanion sessionId="pty-1" studioTabActive />)
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('studio')
    const frame = await screen.findByTestId('studio-frame')
    expect(frame.getAttribute('src')).toBe('/api/embed/studio/index.html?view=scene')
    // 只改侧栏页, 不去开场景页签(主区域焦点由用户的点击决定)
    expect(usePanels.getState().activeId).toBe('cc_session:pty-1')
    expect(usePanels.getState().tabs.some((tab) => tab.ref.type === 'studio')).toBe(false)

    rerender(<SessionCompanion sessionId="pty-1" />)
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('studio')
    expect(screen.getByTestId('studio-frame')).toBe(frame)
  })

  it('works without a session: session list and Studio only', async () => {
    const { ccApi } = await import('../../api/ccClient')
    const context = ccApi.context as ReturnType<typeof vi.fn>
    context.mockClear()
    render(<SessionCompanion sessionId="" studioTabActive />)
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('studio')
    expect(await screen.findByTestId('studio-frame')).toBeTruthy()
    expect((screen.getByTestId('companion-nav-notes') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId('cc-session-new') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByTestId('cc-session-actions')).toBeNull()
    expect(context).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('companion-nav-multiagent'))
    await screen.findByText('会话列表')
  })

  it('unmounts the Studio iframe while its panel is hidden', async () => {
    const { PanelRuntimeProvider } = await import('../../shell/panelRuntime')
    const view = (visible: boolean) => (
      <PanelRuntimeProvider value={{ tabId: 'cc_companion:pty-1', visible }}>
        <SessionCompanion sessionId="pty-1" mode="tab" />
      </PanelRuntimeProvider>
    )
    const { rerender } = render(view(true))
    fireEvent.click(screen.getByTestId('companion-nav-studio'))
    await screen.findByTestId('studio-frame')

    rerender(view(false))
    expect(screen.queryByTestId('studio-frame')).toBeNull()

    rerender(view(true))
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('studio')
    expect(await screen.findByTestId('studio-frame')).toBeTruthy()
  })

  it('opens the Studio page from a navigation request', async () => {
    const { useCompanionNav } = await import('./companionNavStore')
    render(<SessionCompanion sessionId="pty-1" />)
    act(() => { useCompanionNav.getState().open('pty-1', 'studio') })
    expect(screen.getByTestId('session-companion').getAttribute('data-page')).toBe('studio')
    expect(await screen.findByTestId('studio-frame')).toBeTruthy()
    // 程序化导航只切侧栏页, 不开场景页签
    expect(usePanels.getState().activeId).toBe('cc_session:pty-1')
  })
})
