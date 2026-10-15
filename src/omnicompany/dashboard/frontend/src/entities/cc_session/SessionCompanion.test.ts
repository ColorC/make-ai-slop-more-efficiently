import { describe, expect, it } from 'vitest'
import {
  companionSurfaceUrl,
  companionHostTabId,
  DEFAULT_COMPANION_PAGE,
  requestSiblingSession,
  sessionNotesUrl,
} from './SessionCompanion'

describe('SessionCompanion helpers', () => {
  it('opens the project wiki overview as the first/default companion page', () => {
    expect(DEFAULT_COMPANION_PAGE).toBe('overview')
  })

  it('builds a copyable standalone surface URL for the CLI session', () => {
    expect(companionSurfaceUrl('862bb496844d41ee', 'https://dashboard.test:12443')).toBe(
      'https://dashboard.test:12443/?surface=session-companion&id=862bb496844d41ee',
    )
  })

  it('binds the note surface to exactly one CLI session id', () => {
    const url = new URL(sessionNotesUrl('pty-1', 'https://dashboard.test:12443'))
    expect(url.pathname).toBe('/api/embed/notes-web/notes-web.html')
    expect(url.searchParams.get('bridge_prefix')).toBe('/api/embed')
    expect(url.searchParams.get('session')).toBe('pty-1')
  })

  it('places a material to the left of the current companion host', () => {
    expect(companionHostTabId('pty-1', 'embedded')).toBe('cc_session:pty-1')
    expect(companionHostTabId('pty-1', 'tab')).toBe('cc_companion:pty-1')
    expect(companionHostTabId('pty-1', 'surface')).toBeNull()
  })

  it('waits for the mounted editor to finish creating the sibling session', async () => {
    let release!: () => void
    const pending = new Promise<void>((resolve) => { release = resolve })
    let completed = false
    const request = requestSiblingSession(
      { newSession: () => pending },
      async () => { throw new Error('fallback must not run') },
      () => { throw new Error('fallback must not open') },
    ).then(() => { completed = true })

    await Promise.resolve()
    expect(completed).toBe(false)
    release()
    await request
    expect(completed).toBe(true)
  })

  it('creates and opens a fallback session when no editor controller is mounted', async () => {
    const created = {
      id: 'new-session',
      cmd: ['codex'],
      cwd: 'E:\\work',
      cols: 120,
      rows: 32,
      started_at: 1,
      alive: true,
    }
    let opened = ''
    await requestSiblingSession(undefined, async () => created, (session) => {
      opened = session.id
    })
    expect(opened).toBe('new-session')
  })

  it('propagates editor creation failures so the button can show them', async () => {
    await expect(requestSiblingSession(
      { newSession: async () => { throw new Error('spawn unavailable') } },
      async () => { throw new Error('fallback must not run') },
      () => undefined,
    )).rejects.toThrow('spawn unavailable')
  })
})
