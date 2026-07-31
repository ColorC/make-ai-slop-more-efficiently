/**
 * ClaudeCodeSession Editor — xterm.js terminal bridged to backend PTY via WebSocket.
 *
 * Protocol (matches src/omnicompany/dashboard/cc_wrapper/api.py):
 *   client → server: {type:"input",  data:string} | {type:"resize", cols, rows}
 *   server → client: {type:"snapshot", chunks:string[]} | {type:"output", data} | {type:"exit", reason}
 *
 * Architecture is a clean-room reimpl of the public PTY-over-WS pattern; xterm.js
 * itself is MIT (no AGPL contagion).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CcSessionEntity } from './index'
import { ccApi } from '../../api/ccClient'
import { Copy, Keyboard, X } from 'lucide-react'
import EmptyState from '../../shell/EmptyState'
import { usePanels } from '../../stores/panelsStore'
import { useWsAutoReconnect } from '../../lib/wsAutoReconnect'
import { createSettledResizeCommit } from './terminalResize'
import { copyText } from '../../lib/copyText'
import { installTerminalTouchScroller, isTerminalViewportAtBottom } from './terminalTouchScroller'
import { installMobileTerminalInputGuard } from './mobileTerminalInput'
import { TabSidecarToggleButton } from '../../shell/TabSidecar'
import { useSessionControls, type SessionControls } from './sessionControlsStore'
import { commandForSiblingSession } from './sessionCommand'
import './cc_session.css'

export { commandForSiblingSession } from './sessionCommand'

interface XtermLib {
  Terminal: any
  FitAddon: any
  WebLinksAddon: any
  WebglAddon: any
  UnicodeGraphemesAddon: any
}

export interface TerminalReplayGate {
  active: boolean
  generation: number
  pending?: number
  ended?: boolean
  onComplete?: () => void
}

/** Keep at most the focused CLI tab attached to the PTY.
 *
 * Dockview can temporarily leave several panels mounted while groups are being
 * restored or merged. Connecting every mounted panel replays every terminal's
 * history at once and can overwhelm the browser with many multi-megabyte
 * snapshots. A split view still works: focusing a CLI panel transfers the live
 * connection to that panel.
 */
export function isTerminalTabActive(activeTabId: string | null, sessionId: string): boolean {
  return activeTabId === `cc_session:${sessionId}`
}

export function shouldConnectTerminal(
  activeTabId: string | null,
  sessionId: string,
  documentVisible: boolean,
): boolean {
  return documentVisible && isTerminalTabActive(activeTabId, sessionId)
}

function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const sync = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', sync)
    return () => document.removeEventListener('visibilitychange', sync)
  }, [])
  return visible
}

interface TerminalReplayTarget {
  clear: () => void
  write: (data: string, callback?: () => void) => void
}

/**
 * Replaying a PTY snapshot can contain historical OSC queries (for example
 * OSC 10/11 foreground/background queries). xterm answers those through
 * onData, which must not be forwarded into the live prompt as keyboard input.
 */
export function replayTerminalSnapshot(
  term: TerminalReplayTarget,
  chunks: string[],
  gate: TerminalReplayGate,
): void {
  beginTerminalSnapshot(term, gate)
  appendTerminalSnapshot(term, chunks, gate)
  endTerminalSnapshot(gate)
}

export function beginTerminalSnapshot(
  term: TerminalReplayTarget,
  gate: TerminalReplayGate,
): void {
  const generation = gate.generation + 1
  gate.generation = generation
  gate.active = true
  gate.pending = 0
  gate.ended = false
  gate.onComplete = undefined
  try { term.clear() } catch { /* keep replaying even when clear is unavailable */ }
}

function finishTerminalSnapshot(gate: TerminalReplayGate, generation: number): void {
  if (
    gate.generation !== generation
    || !gate.active
    || !gate.ended
    || (gate.pending || 0) !== 0
  ) return
  gate.active = false
  const onComplete = gate.onComplete
  gate.onComplete = undefined
  try { onComplete?.() } catch { /* replay completion must not break terminal input */ }
}

export function appendTerminalSnapshot(
  term: TerminalReplayTarget,
  chunks: string[],
  gate: TerminalReplayGate,
): void {
  const generation = gate.generation
  if (!gate.active || chunks.length === 0) return
  const batches = coalesceTerminalChunks(chunks)
  gate.pending = (gate.pending || 0) + batches.length
  const parsed = () => {
    if (gate.generation !== generation) return
    gate.pending = Math.max(0, (gate.pending || 0) - 1)
    finishTerminalSnapshot(gate, generation)
  }
  for (const chunk of batches) {
    try {
      term.write(chunk, parsed)
    } catch {
      parsed()
    }
  }
}

export function endTerminalSnapshot(
  gate: TerminalReplayGate,
  onComplete?: () => void,
): void {
  gate.ended = true
  gate.onComplete = onComplete
  finishTerminalSnapshot(gate, gate.generation)
}

export function shouldRequestTerminalRedraw(meta: unknown): boolean {
  if (!meta || typeof meta !== 'object') return false
  const replay = meta as { provider?: unknown; replay_truncated?: unknown }
  if (replay.replay_truncated !== true) return false
  const provider = String(replay.provider || '').trim().toLowerCase()
  return ['claude', 'claude_code', 'codex', 'codebuddy', 'kimi', 'opencode'].includes(provider)
}

export function coalesceTerminalChunks(
  chunks: string[],
  maxChars = 256 * 1024,
): string[] {
  const batches: string[] = []
  let batch = ''
  for (const chunk of chunks) {
    if (batch && batch.length + chunk.length > maxChars) {
      batches.push(batch)
      batch = ''
    }
    batch += chunk
  }
  if (batch) batches.push(batch)
  return batches
}

const TERMINAL_RESPONSE_RE = /(?:\x1b\](?:10|11|12);rgb:[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)|\x1b\[\?(?:[0-9;]+)c|\x1b\[>(?:[0-9;]*)c|\x1b\[(?:0n|[0-9]+;[0-9]+R)|\x1b\[\?[0-9]+;[0-9]+\$y)+/g

export function stripTerminalGeneratedResponses(data: string): string {
  return data.replace(TERMINAL_RESPONSE_RE, '')
}

export function readTerminalTheme() {
  const css = getComputedStyle(document.documentElement)
  const token = (name: string) => css.getPropertyValue(name).trim()
  return {
    background: token('--fp-bp-solid'),
    foreground: token('--fp-text'),
    cursor: token('--fp-bp-brass-hi'),
    cursorAccent: token('--fp-bp-solid'),
    selectionBackground: token('--fp-term-selection'),
    selectionInactiveBackground: token('--fp-term-selection-inactive'),
    selectionForeground: token('--fp-text'),
    // xterm otherwise falls back to its generic ANSI palette. Against the
    // blueprint navy background those defaults lose hue after the 4.5:1
    // contrast correction, making Codex/Claude/Kimi/OpenCode look almost
    // monochrome compared with VSCode's terminal.
    black: token('--fp-bp-scene'),
    red: token('--fp-err'),
    green: token('--fp-ok'),
    yellow: token('--fp-warn'),
    blue: token('--fp-link'),
    magenta: token('--fp-violet'),
    cyan: token('--fp-accent-2'),
    white: token('--fp-text-2'),
    brightBlack: token('--fp-text-3'),
    brightRed: '#ff8d85',
    brightGreen: '#a8e6c4',
    brightYellow: '#f3dc8b',
    brightBlue: '#9bc2ff',
    brightMagenta: '#c4b5fd',
    brightCyan: '#8ee8f0',
    brightWhite: token('--fp-text'),
  }
}

export type TerminalShortcutAction = 'terminal' | 'copy' | 'cut-copy' | 'paste' | 'select-all'

export function resolveTerminalShortcut(
  event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey'>,
  hasSelection: boolean,
  windowsMode: boolean,
): TerminalShortcutAction {
  const key = event.key.toLowerCase()
  const command = event.ctrlKey || event.metaKey
  if (event.ctrlKey && event.key === 'Insert' && hasSelection) return 'copy'
  if (!command) return 'terminal'
  if (key === 'c' && (hasSelection || event.shiftKey)) return 'copy'
  if (key === 'x' && hasSelection) return 'cut-copy'
  if (event.shiftKey && key === 'v') return 'paste'
  if (windowsMode && key === 'v') return 'paste'
  if (windowsMode && key === 'a') return 'select-all'
  return 'terminal'
}

let _xtermPromise: Promise<XtermLib> | null = null
async function loadXterm(): Promise<XtermLib> {
  if (_xtermPromise) return _xtermPromise
  _xtermPromise = (async () => {
    const [{ Terminal }, fitMod, webLinksMod, webglMod, unicodeMod] = await Promise.all([
      import('@xterm/xterm'),
      import('@xterm/addon-fit'),
      import('@xterm/addon-web-links'),
      import('@xterm/addon-webgl'),
      import('@xterm/addon-unicode-graphemes'),
    ])
    // @ts-ignore — CSS side-effect import; vite handles it.
    await import('@xterm/xterm/css/xterm.css')
    return {
      Terminal,
      FitAddon: fitMod.FitAddon,
      WebLinksAddon: webLinksMod.WebLinksAddon,
      WebglAddon: webglMod.WebglAddon,
      UnicodeGraphemesAddon: unicodeMod.UnicodeGraphemesAddon,
    }
  })()
  return _xtermPromise
}

const S: Record<string, any> = {
  // root 透明: 吃 body 全局冷渐变, 不再铺 colors.bg 实底把渐变顶掉。
  // UI chrome 走 sans(终端字节流自身的字体由 xterm theme 管, 与页面 UI 字体无关)。
  root: { position: 'relative', display: 'flex', flexDirection: 'column' as const, height: '100%', background: 'transparent', color: 'var(--fp-text)', fontFamily: 'var(--fp-font-sans)', fontSize: 'var(--fp-fs-3)' },
  body: { flex: 1, display: 'flex', minHeight: 0 },
  termCol: { position: 'relative', flex: 1, display: 'flex', flexDirection: 'column' as const, minWidth: 0 },
}

export default function CcSessionEditor({ entity }: { entity: CcSessionEntity }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<any>(null)
  const fitRef = useRef<any>(null)
  const xtermLoadedRef = useRef(false)
  const snapshotReplayRef = useRef<TerminalReplayGate>({ active: false, generation: 0 })
  const snapshotMetaRef = useRef<unknown>(null)
  const activeWsRef = useRef<WebSocket | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [alive, setAlive] = useState<boolean>(entity.alive)
  // A recoverable entity is a provider conversation, not a connectable PTY.
  // Native resume must return the replacement id before the first socket opens.
  const [sessionId, setSessionId] = useState<string | null>(entity.alive ? entity.id : null)
  const [xtermReadyFor, setXtermReadyFor] = useState<string | null>(null)
  const activeTabId = usePanels((state) => state.activeId)
  const documentVisible = useDocumentVisible()
  const ownsTerminalConnection = shouldConnectTerminal(activeTabId, entity.id, documentVisible)
  const resumeAttemptedRef = useRef(false)
  const [creating, setCreating] = useState(false)
  const [shortcutHelpOpen, setShortcutHelpOpen] = useState(false)
  const [terminalAtBottom, setTerminalAtBottom] = useState(true)
  const [windowsKeys, setWindowsKeys] = useState(() => {
    try { return window.localStorage.getItem('omni.cli.keyMode') !== 'terminal' } catch { return true }
  })
  const windowsKeysRef = useRef(windowsKeys)

  useEffect(() => {
    windowsKeysRef.current = windowsKeys
    try { window.localStorage.setItem('omni.cli.keyMode', windowsKeys ? 'windows' : 'terminal') } catch { /* */ }
  }, [windowsKeys])

  // A persisted tab points at the old PTY id, while provider-native resume
  // creates a fresh PTY. Resume first, then keep the same visible tab connected
  // to the new id; opening a recoverable tab must never connect the dead id.
  useEffect(() => {
    if (entity.alive || entity.status !== 'recoverable' || resumeAttemptedRef.current) return
    resumeAttemptedRef.current = true
    let cancelled = false
    setError(null)
    void ccApi.resume(entity.id)
      .then((meta) => {
        if (cancelled) return
        setSessionId(meta.id)
        setAlive(true)
      })
      .catch((e) => {
        if (!cancelled) setError(`resume failed: ${e}`)
      })
    return () => { cancelled = true }
  }, [entity.alive, entity.id, entity.status])

  // 1) 加载 xterm 库 (一次, entity.id 变也不重建 — 重建会闪烁)
  useEffect(() => {
    let cancelled = false
    let resizeObs: ResizeObserver | null = null
    let resizeSettler: ReturnType<typeof createSettledResizeCommit> | null = null
    let pendingDispose: (() => void) | null = null
    setXtermReadyFor(null)
    setTerminalAtBottom(true)

    ;(async () => {
      try {
        const { Terminal, FitAddon, WebLinksAddon, WebglAddon, UnicodeGraphemesAddon } = await loadXterm()
        if (cancelled) return
        if (!containerRef.current) return
        if (xtermLoadedRef.current) return

        const term = new Terminal({
          fontFamily: 'Consolas, "Cascadia Code", Menlo, monospace',
          fontSize: 15,
          cursorBlink: true,
          cursorStyle: 'bar',
          theme: readTerminalTheme(),
          scrollback: 5000,
          convertEol: false,
          rightClickSelectsWord: true,
          // 后端 PTY 是 pywinpty/ConPTY: 明确按 Windows 语义做行 reflow, 否则 xterm 按 Unix
          // 换行猜测, 终端变宽/变窄时历史行排版错乱。buildNumber 取本机 Windows 内部版本。
          windowsPty: { backend: 'conpty', buildNumber: 19045 },
          // 暗底下低对比文本(灰注释/dim)强制拉到 4.5:1 起, 保证可读。
          minimumContrastRatio: 4.5,
          // 开放实验 API: unicode 版本切换与字形重缩放需要它。
          allowProposedApi: true,
          // 宽度不整齐的重叠字形(CJK/连字)按单元格重缩放, 减少半格错位。
          rescaleOverlappingGlyphs: true,
        })
        const fit = new FitAddon()
        term.loadAddon(fit)
        term.loadAddon(new WebLinksAddon())
        // Unicode 15 字素簇: emoji/组合字符按整簇算宽度。addon 或版本不可用则不设, 退回默认版本。
        try {
          term.loadAddon(new UnicodeGraphemesAddon())
          term.unicode.activeVersion = '15-graphemes'
        } catch { /* 降级: 保留 xterm 默认 unicode 版本 */ }
        const terminalRoot = containerRef.current
        term.open(terminalRoot)
        // WebGL 渲染器(GPU 加速): context 丢失(驱动重置/切显卡)时 dispose, 退回内建 DOM 渲染器,
        // 不让终端整块变黑。WebGL 不可用(无 GPU/被禁)时直接走默认 DOM 渲染器。
        try {
          const webgl = new WebglAddon()
          webgl.onContextLoss(() => { try { webgl.dispose() } catch { /* 已 dispose */ } })
          term.loadAddon(webgl)
        } catch { /* 降级: 默认 DOM 渲染器 */ }
        try { fit.fit() } catch { /* size 0 race */ }
        termRef.current = term
        fitRef.current = fit
        xtermLoadedRef.current = true
        setXtermReadyFor(entity.id)

        // Windows 友好键位与终端控制键共存：有选区的 Ctrl+C 一定复制；无选区仍把
        // ^C 送进 PTY。Windows 模式额外接管 Ctrl+A/V，终端模式则保留 Bash/Readline 语义。
        term.attachCustomKeyEventHandler((event: KeyboardEvent) => {
          if (event.type !== 'keydown') return true
          const action = resolveTerminalShortcut(event, term.hasSelection(), windowsKeysRef.current)
          if (action === 'terminal') return true
          if (action === 'copy' || action === 'cut-copy') {
            const selected = term.getSelection()
            if (selected) void copyText(selected)
            if (action === 'cut-copy') term.clearSelection()
            return false
          }
          if (action === 'select-all') {
            term.selectAll()
            return false
          }
          if (action === 'paste') {
            if (navigator.clipboard?.readText) {
              void navigator.clipboard.readText()
                .then((text) => { if (text) term.paste(text) })
                .catch(() => { /* 受限浏览器仍可用未接管的 Shift+Insert / 右键原生 paste */ })
            }
            return false
          }
          return true
        })

        // ShellRail 的 hover 宽度动画持续 220ms。ResizeObserver 会在动画的每一帧
        // 触发；逐帧 fit + ConPTY resize 会让 Codex TUI 和 WebGL canvas 反复清屏。
        // 其它 React 页面仍然实时 reflow，只有终端等容器尺寸稳定后再提交一次 resize。
        resizeSettler = createSettledResizeCommit(
          () => {
            const proposed = typeof fit.proposeDimensions === 'function'
              ? fit.proposeDimensions()
              : null
            return proposed ? { cols: proposed.cols, rows: proposed.rows } : null
          },
          (proposed) => {
            term.resize(proposed.cols, proposed.rows)
            wsConnSendRef.current?.(JSON.stringify({
              type: 'resize', cols: term.cols, rows: term.rows,
            }))
          },
          100,
        )
        resizeObs = new ResizeObserver(() => {
          resizeSettler?.schedule()
        })
        resizeObs.observe(containerRef.current)

        // 终端输入 → ws (经 wsAutoReconnect 队列, 重连期间排队 open 后补发)
        const disp = term.onData((data: string) => {
          // xterm uses onData for both real keyboard input and terminal-generated
          // replies. Snapshot replay must never inject the latter into the PTY.
          if (snapshotReplayRef.current.active) return
          const userData = stripTerminalGeneratedResponses(data)
          if (!userData) return
          wsConnSendRef.current?.(JSON.stringify({
            type: 'input',
            data: userData,
          }))
        })
        const disposeTouchScroll = installTerminalTouchScroller(terminalRoot, term)
        const syncTerminalBottom = () => {
          if (!cancelled) setTerminalAtBottom(isTerminalViewportAtBottom(term))
        }
        const scrollSubscription = term.onScroll(syncTerminalBottom)
        const disposeMobileInput = term.textarea instanceof HTMLTextAreaElement
          ? installMobileTerminalInputGuard(term.textarea, term, { eventRoot: terminalRoot })
          : () => undefined
        pendingDispose = () => {
          disp.dispose()
          scrollSubscription.dispose()
          disposeTouchScroll()
          disposeMobileInput()
        }
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    })()

    return () => {
      cancelled = true
      resizeSettler?.dispose()
      try { resizeObs?.disconnect() } catch { /* */ }
      try { pendingDispose?.() } catch { /* */ }
      try { termRef.current?.dispose() } catch { /* */ }
      termRef.current = null
      fitRef.current = null
      xtermLoadedRef.current = false
    }
  }, [entity.id])

  // 2) WebSocket 自愈 — 重连后 clear term 重写 snapshot, 避免 ring-buffer 重写视觉重复
  const wsConnSendRef = useRef<((data: string) => void) | null>(null)

  const handleMessage = useCallback((ev: MessageEvent) => {
    let msg: any
    try { msg = JSON.parse(ev.data as string) } catch { return }
    const term = termRef.current
    if (!term) return
    if (msg.type === 'snapshot_begin') {
      snapshotMetaRef.current = msg.meta && typeof msg.meta === 'object' ? msg.meta : null
      beginTerminalSnapshot(term, snapshotReplayRef.current)
    } else if (msg.type === 'snapshot_chunk' && Array.isArray(msg.chunks)) {
      appendTerminalSnapshot(term, msg.chunks, snapshotReplayRef.current)
    } else if (msg.type === 'snapshot_end') {
      const snapshotMeta = snapshotMetaRef.current
      snapshotMetaRef.current = null
      endTerminalSnapshot(
        snapshotReplayRef.current,
        shouldRequestTerminalRedraw(snapshotMeta)
          ? () => {
              const liveTerm = termRef.current
              if (!liveTerm) return
              const payload = JSON.stringify({
                type: 'redraw',
                cols: liveTerm.cols,
                rows: liveTerm.rows,
              })
              const ws = activeWsRef.current
              try {
                if (ws?.readyState === WebSocket.OPEN) ws.send(payload)
                else wsConnSendRef.current?.(payload)
              } catch { /* reconnect will replay and retry the repaint */ }
            }
          : undefined,
      )
    } else if (msg.type === 'snapshot' && Array.isArray(msg.chunks)) {
      // 重连时也会重发 snapshot；解析完成前隔离 xterm 自动生成的 OSC/DSR 回复。
      replayTerminalSnapshot(term, msg.chunks, snapshotReplayRef.current)
    } else if (msg.type === 'output' && typeof msg.data === 'string') {
      term.write(msg.data)
    } else if (msg.type === 'exit') {
      setAlive(false)
      term.write(`\r\n\x1b[33m[session ended: ${msg.reason || 'unknown'}]\x1b[0m\r\n`)
    }
  }, [])

  const handleOpen = useCallback((ws: WebSocket, _isReconnect: boolean) => {
    setError(null)
    activeWsRef.current = ws
    // open 时立即同步 term size 给后端
    const term = termRef.current
    const fit = fitRef.current
    if (term && fit) {
      try {
        fit.fit()
        ws.send(JSON.stringify({
          type: 'resize', cols: term.cols, rows: term.rows,
        }))
      } catch { /* */ }
    }
  }, [])

  const wsConn = useWsAutoReconnect({
    url: sessionId ? ccApi.wsUrl(sessionId) : '',
    enabled: sessionId !== null && xtermReadyFor === entity.id && ownsTerminalConnection,
    queueWhenDisconnected: false,
    onMessage: handleMessage,
    onOpen: handleOpen,
  })

  // expose wsConn.send to ref (xterm 加载 effect 运行时尚没有 wsConn)
  useEffect(() => { wsConnSendRef.current = wsConn.send }, [wsConn.send])

  const onKill = async () => {
    if (!sessionId) return
    try {
      await ccApi.kill(sessionId)
      setAlive(false)
    } catch (e) {
      setError(`kill failed: ${e}`)
    }
  }

  const onNewSession = async () => {
    if (creating) return
    setCreating(true)
    setError(null)
    try {
      const meta = await ccApi.create({
        cmd: commandForSiblingSession(entity),
        cwd: entity.cwd,
      })
      const label = meta.provider === 'shell' ? '纯 CLI' : (meta.provider || entity.provider || 'CLI')
      usePanels.getState().openTab(
        { type: 'cc_session', id: meta.id },
        `${label} · ${meta.id.slice(0, 8)}`,
      )
    } catch (e) {
      setError(`new session failed: ${e}`)
      throw e
    } finally {
      setCreating(false)
    }
  }

  const companionControls = useMemo<SessionControls>(() => ({
    alive,
    connected: wsConn.state === 'connected',
    creating,
    windowsKeys,
    cwd: entity.cwd,
    cmd: entity.cmd,
    newSession: onNewSession,
    kill: () => { void onKill() },
    toggleKeyMode: () => setWindowsKeys((value) => !value),
    selectAll: () => termRef.current?.selectAll(),
    showShortcuts: () => setShortcutHelpOpen(true),
  // The callbacks intentionally project the current editor state into the sibling companion.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [alive, creating, entity.cmd, entity.cwd, entity.provider, windowsKeys, wsConn.state])

  useEffect(() => {
    const store = useSessionControls.getState()
    store.register(entity.id, companionControls)
    return () => useSessionControls.getState().unregister(entity.id, companionControls)
  }, [companionControls, entity.id])

  return (
    <div
      className="cs-shell"
      style={S.root}
      data-cc-session-id={sessionId}
      data-cc-session-alive={alive ? 'true' : 'false'}
      data-cc-session-connected={wsConn.state === 'connected' ? 'true' : 'false'}
    >
      {shortcutHelpOpen && (
        <div className="cs-shortcuts" data-testid="cc-session-shortcut-help">
          <div className="cs-shortcuts-head">
            <div>
              <strong>终端快捷键</strong>
              <span>当前：{windowsKeys ? 'Windows 模式' : '终端 / Bash 模式'}</span>
            </div>
            <button type="button" onClick={() => setShortcutHelpOpen(false)} aria-label="关闭快捷键速查"><X size={14} /></button>
          </div>
          <div className="cs-shortcuts-note">
            有文字选区时 <kbd>Ctrl+C</kbd> 始终复制；没有选区时仍是中断当前 CLI。
          </div>
          <div className="cs-shortcuts-grid">
            <kbd>鼠标拖选</kbd><span>选择终端文字</span>
            <kbd>Ctrl+C</kbd><span>复制选区 / 无选区时中断</span>
            <kbd>Ctrl+X</kbd><span>复制选区并取消高亮（输出不可删除）</span>
            <kbd>Ctrl+V</kbd><span>Windows 模式粘贴</span>
            <kbd>Ctrl+A</kbd><span>Windows 模式全选终端内容</span>
            <kbd>Ctrl+Shift+C / V</kbd><span>终端通用复制 / 粘贴</span>
            <kbd>Ctrl+A / Ctrl+E</kbd><span>终端模式移到命令行首 / 行尾</span>
            <kbd>Ctrl+U / Ctrl+K</kbd><span>删除到行首 / 行尾</span>
            <kbd>Ctrl+W</kbd><span>删除前一个词</span>
            <kbd>Ctrl+R</kbd><span>搜索命令历史</span>
            <kbd>Ctrl+L</kbd><span>清屏</span>
            <kbd>Shift+Insert</kbd><span>浏览器原生粘贴（不经过快捷键接管）</span>
          </div>
        </div>
      )}
      <div className="cs-body" style={S.body}>
        <div className="cs-term-col" style={S.termCol}>
          <TabSidecarToggleButton
            label="会话信息"
            showWhen="collapsed"
            testId="session-ctx-toggle"
            className="cs-sidecar-handle"
          />
          <div ref={containerRef} className="cs-term" data-cc-term />
          {!terminalAtBottom && (
            <button
              type="button"
              className="cs-latest"
              data-testid="cc-terminal-latest"
              onClick={() => {
                termRef.current?.scrollToBottom()
                setTerminalAtBottom(true)
              }}
            >
              ↓ 回到最新
            </button>
          )}
          {error && <div className="cs-errbar">{error}</div>}
          {error && !alive && <EmptyState text="重新打开 tab 可重连" />}
        </div>
      </div>
    </div>
  )
}
