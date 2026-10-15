import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  appendTerminalSnapshot,
  appendTerminalSnapshotBuffer,
  armTerminalFirstFrameWatchdog,
  beginTerminalSnapshot,
  buildAttachPreamble,
  classifyTerminalCloseReason,
  closeTerminalFirstFrameAttempt,
  coalesceTerminalChunks,
  consumeTerminalSnapshotBuffer,
  commandForSiblingSession,
  endTerminalSnapshot,
  forceFinishTerminalSnapshot,
  markTerminalFirstFrameReceived,
  nextLastSeqFromFrame,
  nextTerminalMounted,
  readTerminalTheme,
  replayTerminalSnapshot,
  resolveTerminalShortcut,
  resumeOutcomeFor,
  sendTerminalFocusRecovery,
  shouldKeepTerminalSocket,
  shouldReportTerminalVisibility,
  shouldRequestRevealRedraw,
  shouldRequestTerminalRedraw,
  shouldResyncAfterSnapshot,
  shouldSyncVisibilityOnOpen,
  shouldCaptureFocusedTerminalEscape,
  shouldAttachTerminalWebgl,
  shouldUseTerminalWebgl,
  stripTerminalGeneratedResponses,
  type TerminalFirstFrameWatchdog,
  type TerminalReplayGate,
  type TerminalSnapshotBuffer,
} from './Editor'

const TOKENS: Record<string, string> = {
  '--fp-bp-solid': '#0d2450',
  '--fp-bp-scene': '#091a3e',
  // 2026-08-06 CLI 黑玻璃: 终端底不再复用蓝图纸面 --fp-bp-solid, 改专用近黑半透
  '--fp-term-bg': 'rgba(4,8,18,.93)',
  '--fp-term-bg-solid': '#040812',
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
      // 终端要读作"嵌在图板上的深色观察窗", 与蓝图纸面是两种材质, 故不再是 --fp-bp-solid
      background: 'rgba(4,8,18,.93)',
      cursorAccent: '#040812',
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

  it('cuts a visible selection as copy plus selection clear without stealing Ctrl+X otherwise', () => {
    expect(resolveTerminalShortcut(key('x', { ctrlKey: true }), true, true)).toBe('cut-copy')
    expect(resolveTerminalShortcut(key('x', { ctrlKey: true }), false, true)).toBe('terminal')
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

  it('captures Escape for the focused terminal without intercepting IME cancellation', () => {
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Escape' }, true)).toBe(true)
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Esc' }, true)).toBe(true)
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Unidentified', code: 'Escape' }, true)).toBe(true)
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Unidentified', keyCode: 27 }, true)).toBe(true)
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Unidentified', which: 27 }, true)).toBe(true)
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Escape', isComposing: true }, true)).toBe(false)
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Escape' }, false)).toBe(false)
    expect(shouldCaptureFocusedTerminalEscape({ key: 'Enter' }, true)).toBe(false)
  })
})

describe('same-place CLI creation', () => {
  it('creates the same agent type in the same directory flow', () => {
    expect(commandForSiblingSession({ provider: 'codex', cmd: ['C:/bin/codex.cmd'] })).toEqual(['codex'])
    expect(commandForSiblingSession({ provider: 'codebuddy', cmd: ['codebuddy'] })).toEqual(['codebuddy'])
    expect(commandForSiblingSession({ provider: 'kimi', cmd: ['kimi'] })).toEqual(['kimi'])
    expect(commandForSiblingSession({ provider: 'opencode', cmd: ['opencode'] })).toEqual(['opencode'])
    expect(commandForSiblingSession({ provider: 'kiro', cmd: ['kiro-cli', 'chat'] })).toEqual(['kiro-cli', 'chat'])
    expect(commandForSiblingSession({ provider: 'claude_code', cmd: ['claude'] })).toBeUndefined()
  })

  it('keeps a plain terminal as a plain terminal', () => {
    expect(commandForSiblingSession({ provider: 'shell', cmd: ['pwsh', '-NoLogo'] }))
      .toEqual(['pwsh', '-NoLogo'])
  })
})

describe('terminal residency across tab switches', () => {
  it('keeps browser terminal resources scoped to the visible panel', () => {
    expect(nextTerminalMounted(false, false)).toBe(false)
    expect(nextTerminalMounted(false, true)).toBe(true)
    expect(nextTerminalMounted(true, false)).toBe(false)
    expect(nextTerminalMounted(true, true)).toBe(true)
  })

  it('disconnects hidden browser streams while the detached PTY stays alive', () => {
    expect(shouldKeepTerminalSocket('pty-1', 'pty-1', 'pty-1', true)).toBe(true)
    expect(shouldKeepTerminalSocket('pty-1', 'pty-1', 'pty-1', false)).toBe(false)
    expect(shouldKeepTerminalSocket(null, 'pty-1', 'pty-1', true)).toBe(false)
    expect(shouldKeepTerminalSocket('pty-1', 'pty-2', 'pty-1', true)).toBe(false)
    expect(shouldKeepTerminalSocket('pty-1', 'pty-1', 'pty-1', true, false)).toBe(false)
  })

  // WebGL 上下文是浏览器级稀缺资源: 常驻的隐藏终端不该攥着它, 否则把前台那个挤成 DOM 渲染器。
  it('scopes the GPU renderer to visible panels only', () => {
    expect(shouldAttachTerminalWebgl(true, 'codex', 'gpu')).toBe(true)
    expect(shouldAttachTerminalWebgl(false, 'codex', 'gpu')).toBe(false)
    // 既有的两条否决继续生效(codebuddy / 玻璃底走 DOM 渲染器)。
    expect(shouldAttachTerminalWebgl(true, 'codebuddy', 'gpu')).toBe(false)
    expect(shouldAttachTerminalWebgl(true, 'codex', 'glass')).toBe(false)
  })
})

describe('terminal snapshot replay isolation', () => {
  it('fully resets terminal state before replay and falls back to clear', () => {
    const gate: TerminalReplayGate = { active: false, generation: 0 }
    const calls: string[] = []
    beginTerminalSnapshot({
      reset: () => calls.push('reset'),
      clear: () => calls.push('clear'),
      write: () => undefined,
    }, gate)
    expect(calls).toEqual(['reset'])

    beginTerminalSnapshot({
      clear: () => calls.push('fallback-clear'),
      write: () => undefined,
    }, gate)
    expect(calls).toEqual(['reset', 'fallback-clear'])
  })

  it('keeps generated terminal replies gated until every yielded chunk is parsed', () => {
    vi.useFakeTimers()
    try {
      const callbacks: Array<() => void> = []
      const writes: string[] = []
      const gate: TerminalReplayGate = { active: false, generation: 0 }
      const term = {
        clear: () => undefined,
        write: (chunk: string, callback?: () => void) => {
          writes.push(chunk)
          if (callback) callbacks.push(callback)
        },
      }

      beginTerminalSnapshot(term, gate)
      appendTerminalSnapshot(term, ['\u001b]10;?\u001b\\'], gate)
      appendTerminalSnapshot(term, ['\u001b]11;?\u001b\\'], gate)
      endTerminalSnapshot(gate)

      expect(gate.active).toBe(true)
      expect(writes).toHaveLength(1)
      callbacks[0]()
      expect(gate.active).toBe(true)
      expect(writes).toHaveLength(1)
      vi.runOnlyPendingTimers()
      expect(writes).toHaveLength(2)
      callbacks[1]()
      expect(gate.active).toBe(false)
    } finally {
      vi.useRealTimers()
    }
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

  it('coalesces small replay chunks and splits oversized source chunks', () => {
    expect(coalesceTerminalChunks(['ab', 'cd', 'ef'], 4)).toEqual(['abcd', 'ef'])
    expect(coalesceTerminalChunks(['abcdefghij'], 4)).toEqual(['abcd', 'efgh', 'ij'])
  })

  it('keeps only a bounded snapshot tail before xterm parsing', () => {
    const buffer: TerminalSnapshotBuffer = { data: '', truncated: false }
    appendTerminalSnapshotBuffer(buffer, ['ab', 'cdef'], 4)
    expect(buffer).toEqual({ data: 'cdef', truncated: true })
    expect(consumeTerminalSnapshotBuffer(buffer)).toEqual({
      chunks: ['cdef'],
      truncated: true,
    })
    expect(buffer).toEqual({ data: '', truncated: false })
  })

  it('runs replay completion once, after every yielded xterm write callback', () => {
    vi.useFakeTimers()
    try {
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
      vi.runOnlyPendingTimers()
      callbacks[1]()
      callbacks[1]()
      expect(completed).toEqual(['done'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('lets the watchdog finish a stuck replay without double completion', () => {
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
    appendTerminalSnapshot(term, ['stuck'], gate)
    endTerminalSnapshot(gate, () => completed.push('done'))
    forceFinishTerminalSnapshot(gate)

    expect(gate.active).toBe(false)
    expect(gate.queuedChunks).toEqual([])
    expect(completed).toEqual(['done'])
    callbacks[0]()
    expect(completed).toEqual(['done'])
  })
})

describe('terminal first-frame watchdog', () => {
  it('reports a no-frame close and cancels the pending timeout', () => {
    vi.useFakeTimers()
    try {
      const timedOut: string[] = []
      const watchdog: TerminalFirstFrameWatchdog = { seen: false, timer: null }
      armTerminalFirstFrameWatchdog(watchdog, () => timedOut.push('timeout'), 100)

      expect(closeTerminalFirstFrameAttempt(watchdog)).toBe(true)
      vi.advanceTimersByTime(100)
      expect(timedOut).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })

  it('times out an open connection that never receives its first frame', () => {
    vi.useFakeTimers()
    try {
      const timedOut: string[] = []
      const watchdog: TerminalFirstFrameWatchdog = { seen: false, timer: null }
      armTerminalFirstFrameWatchdog(watchdog, () => timedOut.push('timeout'), 100)

      vi.advanceTimersByTime(99)
      expect(timedOut).toEqual([])
      vi.advanceTimersByTime(1)
      expect(timedOut).toEqual(['timeout'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('cancels the timeout after the first snapshot frame arrives', () => {
    vi.useFakeTimers()
    try {
      const timedOut: string[] = []
      const watchdog: TerminalFirstFrameWatchdog = { seen: false, timer: null }
      armTerminalFirstFrameWatchdog(watchdog, () => timedOut.push('timeout'), 100)
      markTerminalFirstFrameReceived(watchdog)

      vi.advanceTimersByTime(100)
      expect(timedOut).toEqual([])
      expect(closeTerminalFirstFrameAttempt(watchdog)).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('terminal redraw recovery', () => {
  it('restores Claude focus only after the snapshot parser has released the replay gate', () => {
    const writes: Array<() => void> = []
    const gate: TerminalReplayGate = { active: false, generation: 0 }
    const term = { reset: vi.fn(), write: (_data: string, callback: () => void) => writes.push(callback) }
    const send = vi.fn()
    const socket = { readyState: WebSocket.OPEN, send }
    const recover = () => sendTerminalFocusRecovery('claude_code', socket, {
      visible: true, documentVisible: true, replaying: gate.active,
    })
    beginTerminalSnapshot(term, gate)
    appendTerminalSnapshot(term, ['restored host screen without DEC 1004'], gate)
    expect(recover()).toBe(false)
    endTerminalSnapshot(gate, recover)
    expect(send).not.toHaveBeenCalled()
    writes[0]()
    expect(send).toHaveBeenCalledExactlyOnceWith(JSON.stringify({ type: 'input', data: '\x1b[I' }))
  })

  it('does not send recovery input to hidden, disconnected, or ordinary shell terminals', () => {
    const send = vi.fn()
    const visible = { visible: true, replaying: false, documentVisible: true }
    const socket = { readyState: WebSocket.OPEN, send }
    expect(sendTerminalFocusRecovery('shell', socket, visible)).toBe(false)
    expect(sendTerminalFocusRecovery('claude_code', null, visible)).toBe(false)
    expect(sendTerminalFocusRecovery('claude_code', { ...socket, readyState: WebSocket.CLOSED }, visible)).toBe(false)
    expect(sendTerminalFocusRecovery('claude_code', socket, { ...visible, visible: false })).toBe(false)
    expect(sendTerminalFocusRecovery('claude_code', socket, { ...visible, documentVisible: false })).toBe(false)
    expect(send).not.toHaveBeenCalled()
  })

  it('reveals a complete host screen without requesting another snapshot', () => {
    for (const provider of ['claude_code', 'codex', 'opencode']) {
      expect(shouldRequestTerminalRedraw({ provider, screen_frame: true, replay_truncated: true })).toBe(false)
      expect(shouldRequestTerminalRedraw({ provider, screen_frame: true, client_replay_truncated: true })).toBe(true)
      expect(shouldRequestTerminalRedraw({ provider, screen_frame: false, replay_truncated: true })).toBe(true)
    }
  })

  it('redraws every agent TUI snapshot but never ordinary shells', () => {
    expect(shouldRequestTerminalRedraw({
      provider: 'opencode',
      replay_truncated: true,
    })).toBe(true)
    expect(shouldRequestTerminalRedraw({
      provider: 'shell',
      replay_truncated: true,
    })).toBe(false)
    expect(shouldRequestTerminalRedraw({
      provider: 'codebuddy',
      replay_truncated: false,
    })).toBe(true)
    expect(shouldRequestTerminalRedraw({
      provider: 'kiro',
      replay_truncated: false,
    })).toBe(true)
    expect(shouldRequestTerminalRedraw({
      provider: 'grok',
      replay_truncated: false,
    })).toBe(true)
    expect(shouldRequestTerminalRedraw({
      provider: 'zcode',
      replay_truncated: true,
    })).toBe(true)
  })

  it('does not treat first 0-size paint as a hide, but redraws a real reveal', () => {
    expect(shouldReportTerminalVisibility(null, false)).toBe(false)
    expect(shouldReportTerminalVisibility(null, true)).toBe(true)
    // PERF-OBSERVATORY P4.1: past the first real sample, hides ARE reported —
    // the PTY host pauses fanout to this subscriber instead of the old
    // "never pause, let the queue overflow into a forced reconnect" bug.
    expect(shouldReportTerminalVisibility(true, false)).toBe(true)
    expect(shouldReportTerminalVisibility(false, true)).toBe(true)
    expect(shouldReportTerminalVisibility(true, true)).toBe(false)
    expect(shouldReportTerminalVisibility(false, false)).toBe(false)
    expect(shouldResyncAfterSnapshot('opencode', 80, 24, 110, 46)).toBe(true)
    expect(shouldResyncAfterSnapshot('opencode', 110, 46, 110, 46)).toBe(false)
    expect(shouldResyncAfterSnapshot('shell', 80, 24, 110, 46)).toBe(false)
    // PTY-SCREEN-CONVERGENCE: screen-model sessions never geometry-resync —
    // the server re-renders one atomic frame at the new geometry and pushes
    // it after the client's own resize, so a grid mismatch is self-healing.
    expect(shouldResyncAfterSnapshot('opencode', 80, 24, 110, 46, true)).toBe(false)
    expect(shouldResyncAfterSnapshot('opencode', 80, 24, 110, 46, false)).toBe(true)
    expect(shouldResyncAfterSnapshot('shell', 80, 24, 110, 46, true)).toBe(false)
    expect(shouldSyncVisibilityOnOpen(null, false)).toBe(false)
    expect(shouldSyncVisibilityOnOpen(null, true)).toBe(true)
    expect(shouldSyncVisibilityOnOpen(false, false)).toBe(true)
    expect(shouldRequestRevealRedraw(null, true, 'opencode')).toBe(false)
    expect(shouldRequestRevealRedraw(false, true, 'opencode')).toBe(true)
    expect(shouldRequestRevealRedraw(false, true, 'grok')).toBe(true)
    expect(shouldRequestRevealRedraw(false, true, 'shell')).toBe(false)
    expect(shouldRequestRevealRedraw(true, true, 'grok')).toBe(false)
  })

  it('keeps CodeBuddy on the stable DOM renderer', () => {
    expect(shouldUseTerminalWebgl('codebuddy', 'gpu')).toBe(false)
    expect(shouldUseTerminalWebgl(' CodeBuddy ', 'gpu')).toBe(false)
    expect(shouldUseTerminalWebgl('codex', 'gpu')).toBe(true)
  })

  // 2026-08-06: 「终端底」档位钉死这个取舍。实测 @xterm/addon-webgl 0.19 的画布不透明,
  // theme.background 进不去(WebGL 开着时 allowTransparency 开/关都是纯黑, 关掉 WebGL
  // 探针红色才出现), 所以真玻璃必须退回 DOM 渲染器 —— 这是性能换观感, 不能默认替用户选。
  it('drops WebGL only when the user asks for the glass terminal', () => {
    expect(shouldUseTerminalWebgl('codex', 'gpu')).toBe(true)
    expect(shouldUseTerminalWebgl('codex', 'glass')).toBe(false)
    // 默认档 = glass(用户 2026-08-06 裁定), 所以不传档位时不装 WebGL
    expect(shouldUseTerminalWebgl('codex')).toBe(false)
    // glass 档不该把 codebuddy 的既有否决翻过来
    expect(shouldUseTerminalWebgl('codebuddy', 'glass')).toBe(false)
  })
})

describe('terminal_ws_reconnect close reason classification', () => {
  it('gives geometry-resync priority over the close code (client explicit reconnect)', () => {
    expect(classifyTerminalCloseReason(4001, 'geometry-resync')).toBe('geometry_resync')
    expect(classifyTerminalCloseReason(1000, 'geometry-resync')).toBe('geometry_resync')
  })

  it('recognizes the first-frame watchdog self-close', () => {
    expect(classifyTerminalCloseReason(4000, 'initial-snapshot-timeout')).toBe('first_frame_watchdog')
  })

  it('recognizes a server-initiated restart close (RFC 6455 service restart)', () => {
    expect(classifyTerminalCloseReason(1012, '')).toBe('server_restart')
  })

  it('falls back to other for network blips and unrecognized codes', () => {
    expect(classifyTerminalCloseReason(1006, '')).toBe('other')
    expect(classifyTerminalCloseReason(1011, 'internal error')).toBe('other')
  })
})

describe('PERF-OBSERVATORY P4.1 seq tracking / resume handshake', () => {
  it('tracks the highest seq seen and ignores malformed/missing values', () => {
    expect(nextLastSeqFromFrame(null, 5)).toBe(5)
    expect(nextLastSeqFromFrame(5, 9)).toBe(9)
    expect(nextLastSeqFromFrame(9, 3)).toBe(9) // out-of-order frame never regresses
    expect(nextLastSeqFromFrame(9, undefined)).toBe(9)
    expect(nextLastSeqFromFrame(9, null)).toBe(9)
    expect(nextLastSeqFromFrame(9, 'not-a-number')).toBe(9)
    expect(nextLastSeqFromFrame(9, Number.NaN)).toBe(9)
    expect(nextLastSeqFromFrame(null, undefined)).toBe(null)
  })

  it('builds the attach preamble from the tracked last_seq', () => {
    expect(buildAttachPreamble(null)).toEqual({ type: 'attach', last_seq: null })
    expect(buildAttachPreamble(42)).toEqual({ type: 'attach', last_seq: 42 })
  })

  it('classifies resume_ok vs snapshot frames as the resume-outcome determination point', () => {
    expect(resumeOutcomeFor('resume_ok')).toBe(true)
    expect(resumeOutcomeFor('snapshot_begin')).toBe(false)
    expect(resumeOutcomeFor('snapshot')).toBe(false)
    expect(resumeOutcomeFor('output')).toBe(null)
    expect(resumeOutcomeFor('exit')).toBe(null)
    expect(resumeOutcomeFor(undefined)).toBe(null)
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
