import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Terminal } from '@xterm/xterm'
import { ccApi } from '../../api/ccClient'
import { usePanels } from '../../stores/panelsStore'
import CcSessionEditor from './Editor'
import type { CcSessionEntity } from './index'

// ── 依赖 mock: 不真开 xterm / WS ─────────────────────────────────────────────

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    static instances: any[] = []
    cols = 120
    rows = 30
    buffer = { active: {} }
    textarea = null
    unicode = { activeVersion: '' }
    open = vi.fn()
    write = vi.fn()
    reset = vi.fn()
    clear = vi.fn()
    loadAddon = vi.fn()
    onData = vi.fn(() => ({ dispose: vi.fn() }))
    onScroll = vi.fn(() => ({ dispose: vi.fn() }))
    resize = vi.fn()
    hasSelection = vi.fn(() => false)
    getSelection = vi.fn(() => '')
    clearSelection = vi.fn()
    selectAll = vi.fn()
    paste = vi.fn()
    dispose = vi.fn()
    refresh = vi.fn()
    scrollToBottom = vi.fn()
    customKeyHandler: ((event: KeyboardEvent) => boolean) | null = null

    constructor() {
      ;(this.constructor as typeof Terminal & { instances: unknown[] }).instances.push(this)
    }

    attachCustomKeyEventHandler = vi.fn((handler: (event: KeyboardEvent) => boolean) => {
      this.customKeyHandler = handler
    })
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit = vi.fn() } }))
vi.mock('@xterm/addon-web-links', () => ({ WebLinksAddon: class { constructor(..._args: unknown[]) {} } }))
vi.mock('@xterm/addon-webgl', () => ({ WebglAddon: class {} }))
vi.mock('@xterm/addon-unicode-graphemes', () => ({ UnicodeGraphemesAddon: class {} }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))

vi.mock('../../lib/wsAutoReconnect', () => ({
  useWsAutoReconnect: () => ({
    state: 'connected',
    reconnectAttempts: 0,
    disconnectedAt: null,
    ws: null,
    send: vi.fn(),
    close: vi.fn(),
  }),
}))

vi.mock('./tabStatus', () => ({
  getCcTabSessionMeta: () => undefined,
  useCcTabRunState: () => 'idle',
}))

vi.mock('./sessionAttention', () => ({
  useSessionCompletionAttention: vi.fn((selector: (state: unknown) => unknown) => (
    selector({ seen: {}, markSeen: vi.fn() })
  )),
}))

vi.mock('./sessionControlsStore', () => {
  const useSessionControls = Object.assign(
    vi.fn((selector: (state: unknown) => unknown) => selector({ bySession: {} })),
    { getState: vi.fn(() => ({ register: vi.fn(), unregister: vi.fn() })) },
  )
  return { useSessionControls }
})

vi.mock('../../stores/uiSettingsStore', () => ({
  useUiSettings: vi.fn((selector: (state: unknown) => unknown) => (
    selector({ terminalRenderer: 'canvas' })
  )),
}))

vi.mock('./terminalTouchScroller', () => ({
  installTerminalTouchScroller: () => () => undefined,
  isTerminalViewportAtBottom: () => true,
}))

vi.mock('./mobileTerminalInput', () => ({
  installMobileTerminalInputGuard: () => () => undefined,
}))

vi.mock('./terminalInspect', () => ({
  registerTerminalInspect: () => () => undefined,
  serializeTerminalInspect: () => ({}),
}))

// ── fixtures ────────────────────────────────────────────────────────────────

function assistantEntity(overrides: Partial<CcSessionEntity> = {}): CcSessionEntity {
  return {
    type: 'cc_session',
    kind: 'pty',
    id: 'assist-1',
    title: 'assistant · assist-1',
    cwd: 'C:/workspace/',
    alive: true,
    status: 'alive',
    cmd: ['codex'],
    startedAt: 1786400000,
    provider: 'codex',
    topic: 'assistant',
    tags: [],
    ...overrides,
  }
}

function metaFor(id: string, billingIssue: boolean) {
  return {
    id,
    cmd: ['codex'],
    cwd: 'C:/workspace/',
    cols: 120,
    rows: 30,
    started_at: 1786400000,
    alive: true,
    provider: 'codex',
    topic: 'assistant',
    billing_issue: billingIssue,
  }
}

describe('assistant billing switch chip', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    ;(Terminal as unknown as { instances: unknown[] }).instances = []
    usePanels.setState({ tabs: [], activeId: null } as never)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows the switch chip when the session meta carries billing_issue', async () => {
    vi.spyOn(ccApi, 'list').mockResolvedValue([metaFor('assist-1', true)])

    render(<CcSessionEditor entity={assistantEntity()} />)

    await waitFor(() => expect(screen.getByTestId('cc-billing-switch')).toBeTruthy())
    expect(screen.getByText('切换到其他provider')).toBeTruthy()
  })

  it('does not poll or show the chip for non-assistant sessions', async () => {
    const list = vi.spyOn(ccApi, 'list').mockResolvedValue([metaFor('assist-1', true)])

    render(<CcSessionEditor entity={assistantEntity({ topic: undefined, id: 'plain-1' })} />)
    await waitFor(() => expect(document.querySelector('[data-cc-session-id="plain-1"]')).toBeTruthy())

    expect(screen.queryByTestId('cc-billing-switch')).toBeNull()
    expect(list).not.toHaveBeenCalled()
  })

  it('uses the native paste event exactly once instead of manually reinjecting Ctrl+V text', async () => {
    render(<CcSessionEditor entity={assistantEntity()} panelVisible />)

    type MockTerminal = {
      paste: ReturnType<typeof vi.fn>
      customKeyHandler: ((event: KeyboardEvent) => boolean) | null
    }
    const terminals = (Terminal as unknown as { instances: MockTerminal[] }).instances
    await waitFor(() => expect(terminals).toHaveLength(1))

    const terminal = terminals[0]
    expect(terminal.customKeyHandler).toBeTypeOf('function')
    const handled = terminal.customKeyHandler!(new KeyboardEvent('keydown', {
      key: 'v', ctrlKey: true, cancelable: true,
    }))

    // xterm receives the browser's subsequent native paste event. The shortcut
    // handler must not call term.paste first or the payload is duplicated.
    expect(handled).toBe(false)
    expect(terminal.paste).not.toHaveBeenCalled()
  })

  it('switches provider, kills the old tab and opens the fallback session tab', async () => {
    vi.spyOn(ccApi, 'list').mockResolvedValue([metaFor('assist-1', true)])
    const switchSpy = vi.spyOn(ccApi, 'switchProvider').mockResolvedValue({
      ...metaFor('assist-2', false),
      switched_from: 'assist-1',
      reason: 'billing_issue',
    })

    render(<CcSessionEditor entity={assistantEntity()} />)
    await waitFor(() => expect(screen.getByTestId('cc-billing-switch')).toBeTruthy())

    fireEvent.click(screen.getByTestId('cc-billing-switch').querySelector('button') as HTMLElement)

    await waitFor(() => {
      expect(switchSpy).toHaveBeenCalledWith('assist-1')
      expect(usePanels.getState().tabs.some((tab) => tab.ref.type === 'cc_session' && tab.ref.id === 'assist-2')).toBe(true)
    })
  })

  it('surfaces a failed provider switch as an error bar', async () => {
    vi.spyOn(ccApi, 'list').mockResolvedValue([metaFor('assist-1', true)])
    vi.spyOn(ccApi, 'switchProvider').mockRejectedValue(new Error('opencode CLI not found'))

    render(<CcSessionEditor entity={assistantEntity()} />)
    await waitFor(() => expect(screen.getByTestId('cc-billing-switch')).toBeTruthy())

    fireEvent.click(screen.getByTestId('cc-billing-switch').querySelector('button') as HTMLElement)

    await waitFor(() => expect(screen.getByText(/切换到其他 provider 失败/)).toBeTruthy())
  })
})
