import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanels } from '../stores/panelsStore'
import { nativeWebviewId, openWorkspaceTarget } from './workspaceNavigation'

describe('workspaceNavigation', () => {
  beforeEach(() => {
    delete window.__OMNICOMPANY_HOST__
    usePanels.setState({
      tabs: [{ id: 'cc_session:chat-1', ref: { type: 'cc_session', id: 'chat-1' }, title: '会话一' }],
      activeId: 'cc_session:chat-1',
    })
  })

  it('opens a native entity with a return target', () => {
    const opened = openWorkspaceTarget({
      kind: 'entity',
      ref: { type: 'studio_reader', id: 'omnicompany' },
      title: 'omnicompany 阅读',
    })

    expect(opened).toBe('studio_reader:omnicompany')
    expect(usePanels.getState().tabs.find((tab) => tab.id === opened)?.returnTo?.id)
      .toBe('cc_session:chat-1')
  })

  it('opens an unsupported public page in a real browser window', () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    try {
      const opened = openWorkspaceTarget({
        kind: 'web',
        url: 'https://example.com/docs',
        title: 'External docs',
      })

      expect(opened).toBeNull()
      expect(click).toHaveBeenCalledTimes(1)
      expect(usePanels.getState().tabs).toHaveLength(1)
    } finally {
      click.mockRestore()
    }
  })

  it('creates deterministic Rust-safe child WebView ids without exposing the full URL', () => {
    const first = nativeWebviewId('web_review:web-page:https://example.com/a?token=secret')
    const second = nativeWebviewId('web_review:web-page:https://example.com/b?token=secret')

    expect(first).toMatch(/^web_[0-9a-f]{8}_[0-9a-f]{8}_[0-9]+$/)
    expect(first.length).toBeLessThanOrEqual(80)
    expect(first).not.toContain('https')
    expect(first).not.toBe(second)
    expect(nativeWebviewId('same')).toBe(nativeWebviewId('same'))
  })
})
