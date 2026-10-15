import React, { useEffect } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

  it('shows the bound project Wiki workspace with its shared navigation in overview', async () => {
    render(<SessionCompanion sessionId="pty-1" />)
    expect(screen.getByRole('button', { name: '会话' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /已提交/ })).toBeNull()

    const wiki = await screen.findByTestId('project-detail-stub')
    expect(wiki.getAttribute('data-project')).toBe('omnidashboard')
    expect(wiki.getAttribute('data-wiki-only')).toBe('1')
    expect(screen.queryByTestId('review-board-stub')).toBeNull()
    expect(screen.queryByTestId('companion-pinned')).toBeNull()
  })

  it('overview still is the review board when the session has no bound project', async () => {
    const { ccApi } = await import('../../api/ccClient')
    ;(ccApi.context as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      session_id: 'pty-2',
      context: { project: null },
      modified_files: [], bash_writes: [], added_workers: [], added_materials: [], event_count: 0,
    })
    render(<SessionCompanion sessionId="pty-2" />)
    await screen.findByTestId('review-board-stub')
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
})
