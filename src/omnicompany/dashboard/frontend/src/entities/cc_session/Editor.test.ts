import { beforeEach, describe, expect, it } from 'vitest'
import {
  appendTerminalSnapshot,
  beginTerminalSnapshot,
  coalesceTerminalChunks,
  commandForSiblingSession,
  endTerminalSnapshot,
  isTerminalTabActive,
  readTerminalTheme,
  replayTerminalSnapshot,
  resolveTerminalShortcut,
  shouldRequestTerminalRedraw,
  shouldConnectTerminal,
  stripTerminalGeneratedResponses,
  type TerminalReplayGate,
} from './Editor'

const TOKENS: Record<string, string> = {
  '--fp-bp-solid': '#0d2450',
  '--fp-bp-scene': '#091a3e',
  '--fp-text': '#eef4ff',
  '--fp-text-2': '#a3bce0',
  '--fp-text-3': '#6e8cb8',
  '--fp-accent-weak': 'rgba(238,244,255,.1)',
  '--fp-border-subtle': 'rgba(235,245,255,.2)',
  '--fp-term-selection': 'rgba(111,168,255,.46)',
  '--fp-term-selection-inactive': 'rgba(176,141,62,.30)',
  '--fp-err': '#e0685f',
  '--fp-ok': '#7fd4a8',
  '--fp-warn': '#e2c05a',
  '--fp-link': '#6fa8ff',
  '--fp-violet': '#a78bfa',
  '--fp-accent-2': '#5ad0e0',
  '--fp-bp-brass-hi': '#d3ac62',
}

describe('remote CLI terminal theme', () => {
  beforeEach(() => {
    for (const [name, value] of Object.entries(TOKENS)) {
      document.documentElement.style.setProperty(name, value)
    }
  })

  it('maps the blueprint theme onto all 16 ANSI colors', () => {
    const theme = readTerminalTheme()
    expect(theme).toMatchObject({
      background: '#0d2450',
      foreground: '#eef4ff',
      red: '#e0685f',
      green: '#7fd4a8',
      yellow: '#e2c05a',
      blue: '#6fa8ff',
      magenta: '#a78bfa',
      cyan: '#5ad0e0',
      brightRed: '#ff8d85',
      brightGreen: '#a8e6c4',
      brightYellow: '#f3dc8b',
      brightBlue: '#9bc2ff',
      brightMagenta: '#c4b5fd',
      brightCyan: '#8ee8f0',
      selectionBackground: 'rgba(111,168,255,.46)',
      selectionInactiveBackground: 'rgba(176,141,62,.30)',
      selectionForeground: '#eef4ff',
    })
  })
})

describe('remote CLI keyboard shortcuts', () => {
  const key = (value: string, options: Partial<KeyboardEvent> = {}) => ({
    key: value,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    ...options,
  }) as Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey'>

  it('copies a visible selection but preserves Ctrl+C interrupt without a selection', () => {
    expect(resolveTerminalShortcut(key('c', { ctrlKey: true }), true, true)).toBe('copy')
    expect(resolveTerminalShortcut(key('c', { ctrlKey: true }), false, true)).toBe('terminal')
  })

  it('provides Windows select-all/paste while terminal mode preserves readline keys', () => {
    expect(resolveTerminalShortcut(key('a', { ctrlKey: true }), false, true)).toBe('select-all')
    expect(resolveTerminalShortcut(key('v', { ctrlKey: true }), false, true)).toBe('paste')
    expect(resolveTerminalShortcut(key('a', { ctrlKey: true }), false, false)).toBe('terminal')
    expect(resolveTerminalShortcut(key('v', { ctrlKey: true }), false, false)).toBe('terminal')
  })

  it('keeps conventional terminal copy/paste and Windows insert aliases in both modes', () => {
    expect(resolveTerminalShortcut(key('C', { ctrlKey: true, shiftKey: true }), false, false)).toBe('copy')
    expect(resolveTerminalShortcut(key('V', { ctrlKey: true, shiftKey: true }), false, false)).toBe('paste')
    expect(resolveTerminalShortcut(key('Insert', { shiftKey: true }), false, false)).toBe('terminal')
    expect(resolveTerminalShortcut(key('Insert', { ctrlKey: true }), true, false)).toBe('copy')
  })
})

describe('same-place CLI creation', () => {
  it('creates the same agent type in the same directory flow', () => {
    expect(commandForSiblingSession({ provider: 'codex', cmd: ['C:/bin/codex.cmd'] })).toEqual(['codex'])
    expect(commandForSiblingSession({ provider: 'codebuddy', cmd: ['codebuddy'] })).toEqual(['codebuddy'])
    expect(commandForSiblingSession({ provider: 'kimi', cmd: ['kimi'] })).toEqual(['kimi'])
    expect(commandForSiblingSession({ provider: 'opencode', cmd: ['opencode'] })).toEqual(['opencode'])
    expect(commandForSiblingSession({ provider: 'claude_code', cmd: ['claude'] })).toBeUndefined()
  })

  it('keeps a plain terminal as a plain terminal', () => {
    expect(commandForSiblingSession({ provider: 'shell', cmd: ['powershell', '-NoLogo'] }))
      .toEqual(['powershell', '-NoLogo'])
  })
})

describe('remote CLI connection ownership', () => {
  it('connects only the focused CLI tab when several Dockview panels are mounted', () => {
    expect(isTerminalTabActive('cc_session:862bb496844d41ee', '862bb496844d41ee')).toBe(true)
    expect(isTerminalTabActive('cc_session:b62a494ddef6410f', '862bb496844d41ee')).toBe(false)
    expect(isTerminalTabActive('review_queue:main', '862bb496844d41ee')).toBe(false)
    expect(isTerminalTabActive(null, '862bb496844d41ee')).toBe(false)
  })

  it('releases the PTY when its browser tab is hidden', () => {
    expect(shouldConnectTerminal('cc_session:862bb496844d41ee', '862bb496844d41ee', true)).toBe(true)
    expect(shouldConnectTerminal('cc_session:862bb496844d41ee', '862bb496844d41ee', false)).toBe(false)
  })
})

describe('terminal snapshot replay isolation', () => {
  it('keeps generated terminal replies gated until every chunk is parsed', () => {
    const callbacks: Array<() => void> = []
    const gate: TerminalReplayGate = { active: false, generation: 0 }
    const term = {
      clear: () => undefined,
      write: (_chunk: string, callback?: () => void) => {
        if (callback) callbacks.push(callback)
      },
    }

    beginTerminalSnapshot(term, gate)
    appendTerminalSnapshot(term, ['\u001b]10;?\u001b\\'], gate)
    appendTerminalSnapshot(term, ['\u001b]11;?\u001b\\'], gate)
    endTerminalSnapshot(gate)

    expect(gate.active).toBe(true)
    callbacks[0]()
    expect(gate.active).toBe(true)
    callbacks[1]()
    expect(gate.active).toBe(false)
  })

  it('does not let a stale replay completion unlock a newer snapshot', () => {
    const callbacks: Array<() => void> = []
    const gate: TerminalReplayGate = { active: false, generation: 0 }
    const term = {
      clear: () => undefined,
      write: (_chunk: string, callback?: () => void) => {
        if (callback) callbacks.push(callback)
      },
    }

    replayTerminalSnapshot(term, ['old'], gate)
    replayTerminalSnapshot(term, ['new'], gate)
    callbacks[0]()
    expect(gate.active).toBe(true)
    callbacks[1]()
    expect(gate.active).toBe(false)
  })

  it('coalesces small replay chunks but keeps bounded batches', () => {
    expect(coalesceTerminalChunks(['ab', 'cd', 'ef'], 4)).toEqual(['abcd', 'ef'])
  })

  it('runs replay completion once, after every xterm write callback', () => {
    const callbacks: Array<() => void> = []
    const completed: string[] = []
    const gate: TerminalReplayGate = { active: false, generation: 0 }
    const term = {
      clear: () => undefined,
      write: (_chunk: string, callback?: () => void) => {
        if (callback) callbacks.push(callback)
      },
    }

    beginTerminalSnapshot(term, gate)
    appendTerminalSnapshot(term, ['first'], gate)
    appendTerminalSnapshot(term, ['second'], gate)
    endTerminalSnapshot(gate, () => completed.push('done'))

    callbacks[0]()
    expect(completed).toEqual([])
    callbacks[1]()
    callbacks[1]()
    expect(completed).toEqual(['done'])
  })
})

describe('terminal redraw recovery', () => {
  it('redraws truncated agent TUIs but never ordinary shells or complete snapshots', () => {
    expect(shouldRequestTerminalRedraw({
      provider: 'opencode',
      replay_truncated: true,
    })).toBe(true)
    expect(shouldRequestTerminalRedraw({
      provider: 'shell',
      replay_truncated: true,
    })).toBe(false)
    expect(shouldRequestTerminalRedraw({
      provider: 'opencode',
      replay_truncated: false,
    })).toBe(false)
  })
})

describe('terminal protocol reply isolation', () => {
  it('drops delayed OSC color and mode replies before they reach Codex input', () => {
    const generated = '\u001b]10;rgb:eeee/f4f4/ffff\u001b\\'
      + '\u001b]11;rgb:0d0d/2424/5050\u001b\\'
      + '\u001b[?2004;1$y'
    expect(stripTerminalGeneratedResponses(generated)).toBe('')
  })

  it('preserves real keyboard, arrow and bracketed-paste input', () => {
    const input = '\u001b[A\u001b[200~hello\u001b[201~\r'
    expect(stripTerminalGeneratedResponses(input)).toBe(input)
  })
})
