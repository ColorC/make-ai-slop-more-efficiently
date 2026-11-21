import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import WebSessionPanel from './WebSessionPanel'
import { ccApi } from '../../api/ccClient'
import type { CcSessionMeta } from '../../api/ccClient'

vi.mock('../../api/ccClient', () => ({
  ccApi: {
    list: vi.fn(),
    tabStates: vi.fn(),
    kill: vi.fn(),
  },
}))

function webMeta(overrides: Partial<CcSessionMeta> = {}): CcSessionMeta {
  return {
    id: 'web-1',
    cmd: ['python', 'dsh-web.py', '--sid', 'web-1'],
    cwd: 'E:/work',
    cols: 120,
    rows: 30,
    started_at: Date.now() / 1000,
    alive: true,
    status: 'alive',
    display_kind: 'web',
    provider: 'dsh_web',
    web_phase: 'ready',
    web_url: '/api/cc/web/web-1/',
    ...overrides,
  }
}

function tabStatesFor(meta: CcSessionMeta | null) {
  return { pty: [], web: meta ? [meta] : [], chat: [] }
}

describe('WebSessionPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the iframe once the supervisor announces the URL', async () => {
    vi.mocked(ccApi.tabStates).mockResolvedValue(tabStatesFor(webMeta()))
    render(<WebSessionPanel entity={{
      type: 'cc_session', kind: 'web', id: 'web-1', title: 'dsh web',
      cwd: 'E:/work', alive: true, status: 'alive', cmd: [], startedAt: 0,
      provider: 'dsh_web', tags: [],
    }} />)
    expect(await screen.findByTestId('web-session-ready')).toBeTruthy()
    const frame = screen.getByTestId('web-session-frame') as HTMLIFrameElement
    expect(frame.src).toContain('/api/cc/web/web-1/')
    expect(screen.getByTestId('web-session-open-browser')).toBeTruthy()
    expect(screen.getByTestId('web-session-end')).toBeTruthy()
  })

  it('shows the starting state while no URL is announced yet', async () => {
    vi.mocked(ccApi.tabStates).mockResolvedValue(tabStatesFor(webMeta({ web_url: null, web_phase: 'starting' })))
    render(<WebSessionPanel entity={{
      type: 'cc_session', kind: 'web', id: 'web-1', title: 'dsh web',
      cwd: 'E:/work', alive: true, status: 'alive', cmd: [], startedAt: 0,
      provider: 'dsh_web', tags: [],
    }} />)
    expect(await screen.findByTestId('web-session-starting')).toBeTruthy()
  })

  it('shows the ended state when the session is gone', async () => {
    vi.mocked(ccApi.tabStates).mockResolvedValue(tabStatesFor(webMeta({ alive: false, status: 'ended', web_phase: 'ended', web_url: null })))
    render(<WebSessionPanel entity={{
      type: 'cc_session', kind: 'web', id: 'web-1', title: 'dsh web',
      cwd: 'E:/work', alive: false, status: 'ended', cmd: [], startedAt: 0,
      provider: 'dsh_web', tags: [],
    }} />)
    expect(await screen.findByTestId('web-session-ended')).toBeTruthy()
  })
})
