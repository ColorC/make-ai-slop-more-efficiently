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
import { ccApi, ASSISTANT_TOPIC, rememberAssistantSession } from '../../api/ccClient'
import { ArrowRightLeft, Copy, Keyboard, RefreshCw, X } from 'lucide-react'
import EmptyState from '../../shell/EmptyState'
import { usePanels } from '../../stores/panelsStore'
import { useWsAutoReconnect } from '../../lib/wsAutoReconnect'
import { openWebTab } from '../../lib/webTabHost'
import {
  buildTerminalInputFrame,
  createSettledResizeCommit,
  parseServerGeometry,
  type TerminalDimensions,
} from './terminalResize'
import { whenTerminalFontsReady } from './terminalFonts'
import { copyText } from '../../lib/copyText'
import { installTerminalTouchScroller, isTerminalViewportAtBottom } from './terminalTouchScroller'
import { installTerminalWheelRecovery } from './terminalWheelRecovery'
import { installMobileTerminalInputGuard } from './mobileTerminalInput'
import { TabSidecarToggleButton } from '../../shell/TabSidecar'
import { usePanelVisible } from '../../shell/panelRuntime'
import { reportFrontendEvent } from '../../lib/frontendTelemetry'
import { getCcTabSessionMeta, useCcTabRunState } from './tabStatus'
import { useSessionCompletionAttention } from './sessionAttention'
import { useSessionControls, type SessionControls } from './sessionControlsStore'
import { commandForSiblingSession } from './sessionCommand'
import { registerTerminalInspect, serializeTerminalInspect } from './terminalInspect'
import './cc_session.css'
import { useUiSettings, type TerminalRenderer } from '../../stores/uiSettingsStore'
import { useBreakpoint, useTouchMode } from '../../shell/useBreakpoint'
import TerminalSoftKeys, { shouldShowTerminalSoftKeys } from './TerminalSoftKeys'
import { applyStickyCtrl, SOFT_KEY_SEQUENCES } from './softKeySequences'

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
  queuedChunks?: string[]
  writing?: boolean
}

export interface TerminalSnapshotBuffer {
  data: string
  truncated: boolean
}

export interface TerminalFirstFrameWatchdog {
  seen: boolean
  timer: ReturnType<typeof setTimeout> | null
}

export const TERMINAL_SNAPSHOT_MAX_CHARS = 256 * 1024
export const TERMINAL_FIRST_FRAME_WATCHDOG_MS = 10_000
export const TERMINAL_RESTORE_WATCHDOG_MS = 15_000
// PERF-OBSERVATORY P4.1: debounce for reporting visibility flips to the PTY
// host (avoids pausing/resyncing on every fast tab-switch flicker).
export const TERMINAL_VISIBILITY_DEBOUNCE_MS = 500

const TERMINAL_INSPECT_KEY_SEQUENCES: Record<string, string> = {
  ArrowUp: SOFT_KEY_SEQUENCES.up,
  ArrowDown: SOFT_KEY_SEQUENCES.down,
  ArrowLeft: SOFT_KEY_SEQUENCES.left,
  ArrowRight: SOFT_KEY_SEQUENCES.right,
  Enter: SOFT_KEY_SEQUENCES.enter,
  Escape: SOFT_KEY_SEQUENCES.esc,
  Esc: SOFT_KEY_SEQUENCES.esc,
  Tab: SOFT_KEY_SEQUENCES.tab,
  PageUp: SOFT_KEY_SEQUENCES.pgup,
  PageDown: SOFT_KEY_SEQUENCES.pgdn,
  Backspace: '\x7f',
  Delete: '\x1b[3~',
  Home: '\x1b[H',
  End: '\x1b[F',
}

export function armTerminalFirstFrameWatchdog(
  watchdog: TerminalFirstFrameWatchdog,
  onTimeout: () => void,
  timeoutMs = TERMINAL_FIRST_FRAME_WATCHDOG_MS,
): void {
  if (watchdog.timer !== null) clearTimeout(watchdog.timer)
  watchdog.seen = false
  watchdog.timer = setTimeout(() => {
    watchdog.timer = null
    if (!watchdog.seen) onTimeout()
  }, timeoutMs)
}

export function markTerminalFirstFrameReceived(watchdog: TerminalFirstFrameWatchdog): void {
  watchdog.seen = true
  if (watchdog.timer !== null) clearTimeout(watchdog.timer)
  watchdog.timer = null
}

export function closeTerminalFirstFrameAttempt(watchdog: TerminalFirstFrameWatchdog): boolean {
  const closedBeforeFirstFrame = !watchdog.seen
  if (watchdog.timer !== null) clearTimeout(watchdog.timer)
  watchdog.timer = null
  watchdog.seen = false
  return closedBeforeFirstFrame
}

export function appendTerminalSnapshotBuffer(
  buffer: TerminalSnapshotBuffer,
  chunks: string[],
  maxChars = TERMINAL_SNAPSHOT_MAX_CHARS,
): void {
  if (chunks.length === 0) return
  const incoming = chunks.join('')
  if (!incoming) return
  const limit = Math.max(1, Math.floor(maxChars))
  const combined = buffer.data + incoming
  if (combined.length > limit) {
    buffer.data = combined.slice(-limit)
    buffer.truncated = true
  } else {
    buffer.data = combined
  }
}

export function consumeTerminalSnapshotBuffer(
  buffer: TerminalSnapshotBuffer,
): { chunks: string[]; truncated: boolean } {
  const result = {
    chunks: buffer.data ? [buffer.data] : [],
    truncated: buffer.truncated,
  }
  buffer.data = ''
  buffer.truncated = false
  return result
}

/** Keep browser-side terminal resources only for the visible panel.
 *
 * PTY ownership, replay and the current screen live in the detached host. A
 * hidden tab therefore needs only its session metadata; retaining xterm,
 * observers and a WebSocket for every tab makes one WebView renderer scale
 * linearly with the number of terminals the user has visited. Reattachment is
 * a bounded screen-frame restore, not an unbounded transcript replay.
 */
export function nextTerminalMounted(_previousMounted: boolean, panelVisible: boolean): boolean {
  return panelVisible
}

export function shouldKeepTerminalSocket(
  sessionId: string | null,
  xtermReadyFor: string | null,
  entityId: string,
  panelVisible: boolean,
  geometryReady = true,
): boolean {
  // Wait for one real fit before the first socket: a 0-size attach paints the
  // TUI at the host default, and later resize cannot always rebuild alt-screen.
  return panelVisible && sessionId !== null && xtermReadyFor === entityId && geometryReady
}

interface TerminalReplayTarget {
  clear: () => void
  reset?: () => void
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
  gate.queuedChunks = []
  gate.writing = false
  try {
    if (typeof term.reset === 'function') term.reset()
    else term.clear()
  } catch {
    try { term.clear() } catch { /* keep replaying even when reset is unavailable */ }
  }
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

export function forceFinishTerminalSnapshot(gate: TerminalReplayGate): void {
  if (!gate.active) return
  gate.ended = true
  gate.pending = 0
  gate.queuedChunks = []
  gate.writing = false
  finishTerminalSnapshot(gate, gate.generation)
}

function pumpTerminalSnapshotQueue(
  term: TerminalReplayTarget,
  gate: TerminalReplayGate,
  generation: number,
): void {
  if (gate.generation !== generation || !gate.active || gate.writing) return
  const chunk = gate.queuedChunks?.shift()
  if (!chunk) {
    finishTerminalSnapshot(gate, generation)
    return
  }
  gate.writing = true
  let settled = false
  const parsed = () => {
    if (settled) return
    settled = true
    if (gate.generation !== generation || !gate.active) return
    gate.writing = false
    gate.pending = Math.max(0, (gate.pending || 0) - 1)
    finishTerminalSnapshot(gate, generation)
    if ((gate.pending || 0) > 0) {
      // Yield between parser batches so rendering, input and the recovery
      // watchdog can run even for ANSI-heavy histories in WebView2.
      setTimeout(() => pumpTerminalSnapshotQueue(term, gate, generation), 0)
    }
  }
  try {
    term.write(stripTerminalUnderline(chunk), parsed)
  } catch {
    parsed()
  }
}

export function appendTerminalSnapshot(
  term: TerminalReplayTarget,
  chunks: string[],
  gate: TerminalReplayGate,
): void {
  const generation = gate.generation
  if (!gate.active || chunks.length === 0) return
  const batches = coalesceTerminalChunks(chunks)
  if (batches.length === 0) return
  gate.queuedChunks = [...(gate.queuedChunks || []), ...batches]
  gate.pending = (gate.pending || 0) + batches.length
  pumpTerminalSnapshotQueue(term, gate, generation)
}

export function endTerminalSnapshot(
  gate: TerminalReplayGate,
  onComplete?: () => void,
): void {
  gate.ended = true
  gate.onComplete = onComplete
  finishTerminalSnapshot(gate, gate.generation)
}

/** Full-screen agent TUIs that must be asked to repaint after a stale snapshot
 *  or after the viewer comes back from a hidden tab. Ordinary shells just
 *  keep scrolling; these apps own the alternate screen and tear if we only
 *  replay a truncated ANSI tail. */
const AGENT_TUI_PROVIDERS = [
  'claude',
  'claude_code',
  'codex',
  'codex_the_company',
  'codebuddy',
  'kimi',
  'opencode',
  'kiro',
  'qoder',
  'dsh',
  'zcode',
  'grok',
] as const

export function shouldRequestTerminalRedraw(meta: unknown): boolean {
  if (!meta || typeof meta !== 'object') return false
  const replay = meta as { provider?: unknown; screen_frame?: unknown; client_replay_truncated?: unknown }
  // A host-rendered screen is already the result of a redraw. Asking for
  // another one here resets the terminal and covers live output again.
  if (replay.screen_frame === true && replay.client_replay_truncated !== true) return false
  const provider = String(replay.provider || '').trim().toLowerCase()
  return (AGENT_TUI_PROVIDERS as readonly string[]).includes(provider)
}

/** Older detached hosts omitted DEC 1004 from screen frames. Claude then
 * remains in its last focus-out state even when the restored page is visible.
 * Reannounce focus through the normal PTY input protocol without restarting
 * the CLI or depending on the restored xterm's missing sendFocusMode flag. */
export function sendTerminalFocusRecovery(
  provider: unknown,
  socket: Pick<WebSocket, 'readyState' | 'send'> | null,
  state: { visible: boolean; replaying: boolean; documentVisible: boolean },
): boolean {
  if (!/^(claude|claude_code)$/.test(String(provider || '').toLowerCase())) return false
  if (!state.visible || !state.documentVisible || state.replaying || socket?.readyState !== WebSocket.OPEN) return false
  try {
    socket.send(JSON.stringify({ type: 'input', data: '\x1b[I' }))
    return true
  } catch { return false }
}

/** Whether this visibility sample should go to the PTY host at all.
 *
 * The DOM probe starts at 0×0 on first paint. Reporting *that* as hidden
 * pauses the live stream before the terminal has painted for real, so the
 * very first sample is never reported hidden — OpenCode and Grok would boot
 * into a torn screen. Past that first real sample, both directions are
 * reported: PERF-OBSERVATORY P4.1 makes a hidden PTY pause fanout to this
 * subscriber (ring keeps accumulating) and catch it up with one inline
 * resync on reveal, instead of the queue silently overflowing into a forced
 * reconnect + full replay. */
export function shouldReportTerminalVisibility(
  lastReported: boolean | null,
  nextVisible: boolean,
): boolean {
  if (!nextVisible && lastReported === null) return false
  return lastReported !== nextVisible
}

/** Re-sync visibility on a new socket without treating first paint as a hide. */
export function shouldSyncVisibilityOnOpen(
  lastReported: boolean | null,
  nextVisible: boolean,
): boolean {
  return lastReported !== null || nextVisible
}

/** Genuine hidden→visible for a full-screen agent TUI. Never the first show. */
export function shouldRequestRevealRedraw(
  lastReported: boolean | null,
  nextVisible: boolean,
  provider: unknown,
): boolean {
  return lastReported === false
    && nextVisible
    && shouldRequestTerminalRedraw({ provider })
}

/** After the first real fit, reconnect if the snapshot was taken at another grid.
 * That is the automatic version of "close the page and open it again".
 * Screen-model sessions never reconnect: the server re-renders one atomic
 * frame at the new geometry and pushes it after the client's own resize. */
export function shouldResyncAfterSnapshot(
  provider: unknown,
  snapshotCols: unknown,
  snapshotRows: unknown,
  termCols: number,
  termRows: number,
  screenModel?: unknown,
): boolean {
  if (screenModel === true) return false
  if (!shouldRequestTerminalRedraw({ provider })) return false
  if (termCols < 2 || termRows < 1) return false
  const cols = Number(snapshotCols)
  const rows = Number(snapshotRows)
  if (!Number.isFinite(cols) || !Number.isFinite(rows) || cols < 2 || rows < 1) return false
  return cols !== termCols || rows !== termRows
}

/** WS 重连的归类原因(供 terminal_ws_reconnect 埋点用)。
 *  · geometry_resync — 客户端主动发起(reconnectRef, 快照网格与真实网格不符);
 *  · first_frame_watchdog — 本端 10s 内没收到任何帧, 自己 close(4000, ...);
 *  · server_restart — 服务端主动踢(WS 标准 1012 = service restart);
 *  · other — 网络抖动 / 浏览器休眠等其余 close code。 */
export type TerminalReconnectReason = 'geometry_resync' | 'first_frame_watchdog' | 'server_restart' | 'other'

export function classifyTerminalCloseReason(code: number, reason: string): TerminalReconnectReason {
  if (reason === 'geometry-resync') return 'geometry_resync'
  if (code === 4000) return 'first_frame_watchdog'
  if (code === 1012) return 'server_restart'
  return 'other'
}

/** PERF-OBSERVATORY P4.1 incremental resume: track the highest `seq` seen on
 * an `output` frame (or a snapshot/resync's `last_seq`). A malformed or
 * missing value leaves the tracker unchanged rather than resetting it —
 * losing track of the anchor just means the next reconnect falls back to a
 * full snapshot, never a gap. */
export function nextLastSeqFromFrame(current: number | null, seq: unknown): number | null {
  if (typeof seq !== 'number' || !Number.isFinite(seq)) return current
  return current === null ? seq : Math.max(current, seq)
}

/** The optional preamble a client sends as its very first WS message so the
 * server can decide between an incremental resume and a full snapshot.
 * `null` means "nothing resumable yet" (first-ever attach for this tab). */
export function buildAttachPreamble(lastSeq: number | null): { type: 'attach'; last_seq: number | null } {
  return { type: 'attach', last_seq: lastSeq }
}

/** Whether a just-arrived frame settles "did this reconnect resume?" for the
 * `terminal_ws_reconnect` `resumed` telemetry field. `null` = not a
 * determination frame (keep waiting — plain `output`/exit etc). */
export function resumeOutcomeFor(msgType: unknown): boolean | null {
  if (msgType === 'resume_ok') return true
  if (msgType === 'snapshot_begin' || msgType === 'snapshot') return false
  return null
}

/** 该不该给这个终端装 WebGL 渲染器。
 *
 * 两条独立的否决:
 *  · codebuddy 历史上与 WebGL 有渲染问题, 恒定走 DOM 渲染器(既有行为, 不动);
 *  · 用户把「终端底」设成 glass —— 实测 @xterm/addon-webgl 0.19 的画布不透明,
 *    theme.background 进不去, 格纸透不到文字底下; 想要真玻璃只能退回 DOM 渲染器。
 *    这是性能换观感的取舍, 所以交给用户选, 默认仍是 gpu。
 */
export function shouldUseTerminalWebgl(
  provider: unknown,
  renderer: TerminalRenderer = 'glass',
): boolean {
  if (renderer === 'glass') return false
  return String(provider || '').trim().toLowerCase() !== 'codebuddy'
}

/** 隐藏页签的终端不占 GPU 上下文。
 *
 * 页签常驻(2026-08-07)之后, 所有开着的 CLI 终端都活着。WebGL 上下文却是**浏览器级**的
 * 稀缺资源(Chromium/WebView2 同时只保留十几个, 超了就把最老的判定为 context loss)。
 * 若每个常驻终端都攥着一个上下文, 结局是**正在看的那个**被挤掉、掉回 DOM 渲染器变慢 ——
 * 后台页签把前台的性能吃了, 正好是这次改造要避免的那种"显著变差"。
 *
 * 所以上下文只跟着**可见性**走: 谁在屏幕上谁用 GPU, 切走后延迟释放(见
 * TERMINAL_WEBGL_IDLE_DISPOSE_MS, 免得 A↔B 快速来回反复重建)。隐藏的终端仍在收字节、
 * 仍在滚历史, 只是没有渲染器要伺候 —— 反正它一个像素都不画。
 */
export function shouldAttachTerminalWebgl(
  panelVisible: boolean,
  provider: unknown,
  renderer: TerminalRenderer = 'glass',
): boolean {
  return panelVisible && shouldUseTerminalWebgl(provider, renderer)
}

/** 切走后过多久释放 GPU 上下文。够短不积压, 够长扛住来回切页签。 */
export const TERMINAL_WEBGL_IDLE_DISPOSE_MS = 2_000

export function coalesceTerminalChunks(
  chunks: string[],
  maxChars = 16 * 1024,
): string[] {
  const limit = Math.max(1, Math.floor(maxChars))
  const batches: string[] = []
  let batch = ''
  for (const source of chunks) {
    let chunk = source
    while (chunk) {
      const remaining = limit - batch.length
      batch += chunk.slice(0, remaining)
      chunk = chunk.slice(remaining)
      if (batch.length >= limit) {
        batches.push(batch)
        batch = ''
      }
    }
  }
  if (batch) batches.push(batch)
  return batches
}

const TERMINAL_RESPONSE_RE = /(?:\x1b\](?:10|11|12);rgb:[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)|\x1b\[\?(?:[0-9;]+)c|\x1b\[>(?:[0-9;]*)c|\x1b\[(?:0n|[0-9]+;[0-9]+R)|\x1b\[\?[0-9]+;[0-9]+\$y)+/g

export function stripTerminalGeneratedResponses(data: string): string {
  return data.replace(TERMINAL_RESPONSE_RE, '')
}

const TERMINAL_UNDERLINE_SGR_RE = /\x1b\[(?:4|21|24)m/g
export function stripTerminalUnderline(data: string): string {
  return data.replace(TERMINAL_UNDERLINE_SGR_RE, '\x1b[24m')
}

/** 从 :root 读一个 CSS 变量; 拿不到就让调用方走兜底。
 *  终端这类 canvas/DOM 混合渲染件没法直接吃 CSS, 只能把 token 值取出来喂给它。 */
function readCssVar(name: string): string {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  } catch {
    return ''
  }
}

/** 终端字体栈: 第一字体 Sarasa Term SC 是本地 webfont, 到货前先按回退字体量
 *  单元格 —— 到货后必须补一次 fit 重算 cols, 否则 TUI 右边框对不齐(锯齿)。 */
const TERMINAL_FONT_STACK = readCssVar('--fp-font-term')
  || 'Cascadia Mono, Consolas, "Microsoft YaHei", monospace'

export function readTerminalTheme() {
  const css = getComputedStyle(document.documentElement)
  const token = (name: string) => css.getPropertyValue(name).trim()
  return {
    // 2026-08-06(用户:「cli 使用黑色玻璃感, 更深更黑更特别」): 终端底从蓝图纸面 --fp-bp-solid
    // 改专用的近黑半透 --fp-term-bg —— 终端要读作"嵌在图板上的一块深色观察窗", 是另一种材质,
    // 而不是又一张同色蓝图纸。半透需要下面的 allowTransparency 才生效。
    background: token('--fp-term-bg'),
    foreground: token('--fp-text'),
    cursor: token('--fp-bp-brass-hi'),
    cursorAccent: token('--fp-term-bg-solid'),
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

export function shouldCaptureFocusedTerminalEscape(
  event: Pick<KeyboardEvent, 'key'> & Partial<Pick<KeyboardEvent, 'code' | 'keyCode' | 'which' | 'isComposing'>>,
  terminalFocused: boolean,
): boolean {
  const isEscape = event.key === 'Escape' || event.key === 'Esc' || event.code === 'Escape' || event.keyCode === 27 || event.which === 27
  return isEscape && terminalFocused && !event.isComposing
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

export default function CcSessionEditor({ entity, panelVisible: panelVisibleProp }: {
  entity: CcSessionEntity
  panelVisible?: boolean
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<any>(null)
  const fitRef = useRef<any>(null)
  // True while this viewer renders another viewer's PTY grid (server `geometry`)
  // instead of its own fit; its next keystroke takes over sizing.
  const followingServerGridRef = useRef(false)
  const xtermLoadedRef = useRef(false)
  const snapshotReplayRef = useRef<TerminalReplayGate>({ active: false, generation: 0 })
  const snapshotBufferRef = useRef<TerminalSnapshotBuffer>({ data: '', truncated: false })
  const snapshotMetaRef = useRef<unknown>(null)
  const snapshotTimedOutRef = useRef(false)
  // terminal_snapshot_replay 埋点: 从 snapshot_begin 到真正回放完成(xterm 写完)的耗时/字节/chunk数。
  const snapshotReplayStartRef = useRef<number | null>(null)
  const snapshotChunkCountRef = useRef<number>(0)
  const snapshotByteCountRef = useRef<number>(0)
  // terminal_ws_reconnect 埋点: 本会话(本次挂载)累计重连次数, 跨 backoff 重连与显式 reconnect() 共用。
  const reconnectCountRef = useRef<number>(0)
  // PERF-OBSERVATORY P4.1: highest output/snapshot seq seen so far, sent back
  // as the attach preamble's last_seq on the next connect so the server can
  // try an incremental resume instead of a full snapshot replay.
  const lastSeqRef = useRef<number | null>(null)
  // Set when handleClose reports a reconnect; cleared once the next
  // resume_ok/snapshot_begin settles whether that reconnect resumed, so the
  // (reused) terminal_ws_reconnect event can carry a `resumed` field.
  const awaitingResumeOutcomeRef = useRef(false)
  const visibilityDebounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const firstFrameWatchdogRef = useRef<TerminalFirstFrameWatchdog>({ seen: false, timer: null })
  const activeWsRef = useRef<WebSocket | null>(null)
  const resizeSettlerRef = useRef<ReturnType<typeof createSettledResizeCommit> | null>(null)
  const restoreRevealTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const restoreWatchdogTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const redrawSettlingRef = useRef(false)
  const redrawWritesPendingRef = useRef(0)
  const [error, setError] = useState<string | null>(null)
  const [alive, setAlive] = useState<boolean>(entity.alive)
  const [terminalRestoring, setTerminalRestoring] = useState(true)
  // A recoverable entity is a provider conversation, not a connectable PTY.
  // Native resume must return the replacement id before the first socket opens.
  const [sessionId, setSessionId] = useState<string | null>(entity.alive ? entity.id : null)
  const [xtermReadyFor, setXtermReadyFor] = useState<string | null>(null)
  const contextPanelVisible = usePanelVisible()
  const panelVisible = panelVisibleProp ?? contextPanelVisible
  const runState = useCcTabRunState(entity.id, 'pty')
  const markCompletionSeen = useSessionCompletionAttention((state) => state.markSeen)
  const panelVisibleRef = useRef(panelVisible)
  panelVisibleRef.current = panelVisible
  const lastReportedVisibilityRef = useRef<boolean | null>(null)
  const snapshotSizeRef = useRef<{ cols: number; rows: number } | null>(null)
  const snapshotScreenModelRef = useRef(false)
  const didGeometryResyncRef = useRef(false)
  const recoverTerminalFocus = useCallback(() => {
    sendTerminalFocusRecovery(entity.provider, activeWsRef.current, {
      visible: panelVisibleRef.current,
      replaying: snapshotReplayRef.current.active,
      documentVisible: document.visibilityState === 'visible',
    })
  }, [entity.provider])
  useEffect(() => {
    window.addEventListener('focus', recoverTerminalFocus)
    document.addEventListener('visibilitychange', recoverTerminalFocus)
    return () => {
      window.removeEventListener('focus', recoverTerminalFocus)
      document.removeEventListener('visibilitychange', recoverTerminalFocus)
    }
  }, [recoverTerminalFocus])
  const reconnectRef = useRef<((reason?: string) => void) | null>(null)
  // 客户端主动发起的重连(目前只有 geometry-resync)绕开 wsAutoReconnect 的 backoff 路径,
  // 旧 socket 的 onclose 因 generation 提前变化而被吞掉(见 wsAutoReconnect.reconnect()),
  // 所以埋点必须在这里显式补, handleClose 那条路径(见下方)才不会漏计。
  const triggerTerminalReconnect = useCallback((reason: string) => {
    reconnectCountRef.current += 1
    reportFrontendEvent('terminal_ws_reconnect', `terminal client-initiated reconnect: ${reason}`, {
      session_id: entity.id,
      close_code: 4001,
      close_reason: reason,
      category: classifyTerminalCloseReason(4001, reason),
      reconnect_count: reconnectCountRef.current,
    })
    reconnectRef.current?.(reason)
  }, [entity.id])
  const [geometryReady, setGeometryReady] = useState(false)
  // Detached PTY hosts retain state; hidden tabs release their browser renderer.
  const [terminalMounted, setTerminalMounted] = useState(() => nextTerminalMounted(false, panelVisible))
  useEffect(() => {
    setTerminalMounted((mounted) => nextTerminalMounted(mounted, panelVisible))
  }, [panelVisible])

  useEffect(() => {
    if (!panelVisible) return undefined
    const markIfVisible = () => {
      if (document.visibilityState !== 'visible') return
      if (runState !== 'done') return
      markCompletionSeen(entity.id, getCcTabSessionMeta(entity.id))
    }
    markIfVisible()
    document.addEventListener('visibilitychange', markIfVisible)
    return () => document.removeEventListener('visibilitychange', markIfVisible)
  }, [entity.id, markCompletionSeen, panelVisible, runState])
  const terminalRenderer = useUiSettings((state) => state.terminalRenderer)
  const webglRef = useRef<{ dispose: () => void } | null>(null)
  const webglDisposeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const xtermLibRef = useRef<XtermLib | null>(null)
  const resumeAttemptedRef = useRef(false)
  const [creating, setCreating] = useState(false)
  const [shortcutHelpOpen, setShortcutHelpOpen] = useState(false)
  // assistant 费用问题: 后端在输出流里识别欠费/配额/余额类报错后给会话打 billing_issue
  // 标记; 这里轮询 meta, 见标记即弹「切换到其他provider」小按钮。
  const [billingIssue, setBillingIssue] = useState(false)
  const [switchingProvider, setSwitchingProvider] = useState(false)
  const [terminalAtBottom, setTerminalAtBottom] = useState(true)
  const [windowsKeys, setWindowsKeys] = useState(() => {
    try { return window.localStorage.getItem('omni.cli.keyMode') !== 'terminal' } catch { return true }
  })
  const windowsKeysRef = useRef(windowsKeys)
  // 移动端软按键条(触屏专属, 见 useTouchMode): Ctrl 是粘滞开关, onData 里按
  // applyStickyCtrl 与下一个真实字符键合成, ref 供该 effect 内的闭包读最新值。
  const breakpoint = useBreakpoint()
  const touchMode = useTouchMode(breakpoint)
  const [terminalInputFocused, setTerminalInputFocused] = useState(false)
  const terminalInputFocusedRef = useRef(false)
  const [ctrlArmed, setCtrlArmed] = useState(false)
  const ctrlArmedRef = useRef(false)

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

  // assistant 费用问题轮询: 只对 assistant 会话开; 见标记后闩住不再轮。
  useEffect(() => {
    if (entity.topic !== ASSISTANT_TOPIC || !alive || billingIssue) return
    let disposed = false
    const tick = async () => {
      try {
        const list = await ccApi.list({ includeRecoverable: false })
        const meta = list.find((item) => item.id === (sessionId || entity.id))
        if (meta?.billing_issue && !disposed) setBillingIssue(true)
      } catch { /* 轮询失败, 下轮再试 */ }
    }
    void tick()
    const timer = window.setInterval(() => { void tick() }, 5_000)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [alive, billingIssue, entity.id, entity.topic, sessionId])

  // 切换到其他 provider: 后端杀旧会话、起回退 CLI 并自动交接; 这里开新页签。
  const switchProvider = async () => {
    if (!sessionId || switchingProvider) return
    setSwitchingProvider(true)
    setError(null)
    try {
      const next = await ccApi.switchProvider(sessionId)
      rememberAssistantSession(next.id)
      setBillingIssue(false)
      setAlive(false)
      usePanels.getState().openTab(
        { type: 'cc_session', id: next.id },
        `assistant · ${next.id.slice(0, 8)}`,
      )
    } catch (cause) {
      setError(`切换到其他 provider 失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setSwitchingProvider(false)
    }
  }

  // 1) 加载 xterm 库 (一次, entity.id 变也不重建 — 重建会闪烁)
  useEffect(() => {
    // 页签常驻(2026-08-07): 终端**第一次可见**时才建 —— xterm 要在真实布局里量字形,
    // dockview 把非当前页签整块 display:none, 在那里量出来是 0。建好之后就不再卸载,
    // 所以这个 effect 在整个页签生命周期里只跑一次, 切走切回都不会重放历史。
    if (!terminalMounted) {
      setXtermReadyFor(null)
      setTerminalRestoring(false)
      return
    }

    let cancelled = false
    let resizeObs: ResizeObserver | null = null
    let resizeSettler: ReturnType<typeof createSettledResizeCommit> | null = null
    let pendingDispose: (() => void) | null = null
    let fontCleanup: (() => void) | null = null
    setXtermReadyFor(null)
    setTerminalAtBottom(true)
    setTerminalRestoring(true)

    ;(async () => {
      try {
        const lib = await loadXterm()
        const { Terminal, FitAddon, WebLinksAddon, UnicodeGraphemesAddon } = lib
        xtermLibRef.current = lib
        if (cancelled) return
        if (!containerRef.current) return
        if (xtermLoadedRef.current) return

        const term = new Terminal({
          // 走 token, 不在这里硬编码 —— 原值没有任何 CJK 字体, 中文只能掉到系统宋体。
          fontFamily: TERMINAL_FONT_STACK,
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
          // 黑玻璃需要它: 不开的话 xterm 会把 theme.background 的 alpha 直接丢掉, 渲染成不透明。
          // 代价是 WebGL 渲染器要多合成一层; α 定在 .93(几乎不透), 每帧多出的采样量很小,
          // 换来的是格纸从终端底下隐约透上来 —— 这正是"玻璃"而非"黑块"的来源。
          allowTransparency: true,
          // 宽度不整齐的重叠字形(CJK/连字)按单元格重缩放, 减少半格错位。
          rescaleOverlappingGlyphs: true,
        })
        const fit = new FitAddon()
        term.loadAddon(fit)
        term.loadAddon(new WebLinksAddon((_event: MouseEvent, url: string) => {
          if (!openWebTab(url, '', window.location.href, { allowExternal: true })) {
            window.open(url, '_blank', 'noopener,noreferrer')
          }
        }))
        // Unicode 15 字素簇: emoji/组合字符按整簇算宽度。addon 或版本不可用则不设, 退回默认版本。
        try {
          term.loadAddon(new UnicodeGraphemesAddon())
          term.unicode.activeVersion = '15-graphemes'
        } catch { /* 降级: 保留 xterm 默认 unicode 版本 */ }
        const terminalRoot = containerRef.current
        term.open(terminalRoot)
        const terminalInput = term.textarea instanceof HTMLTextAreaElement ? term.textarea : null
        const handleTerminalInputFocus = () => {
          if (!cancelled) {
            terminalInputFocusedRef.current = true
            setTerminalInputFocused(true)
            recoverTerminalFocus()
          }
        }
        const handleTerminalInputBlur = () => {
          terminalInputFocusedRef.current = false
          if (!cancelled) setTerminalInputFocused(false)
        }
        terminalInput?.addEventListener('focus', handleTerminalInputFocus)
        terminalInput?.addEventListener('blur', handleTerminalInputBlur)
        // Capture Escape before page shortcuts, including tablet keyboards
        // targeting the document while xterm owns focus. Since capture prevents
        // xterm's own key handler, explicitly feed keydown into its input channel.
        // keyup/keypress must stay local without sending a second input.
        const terminalOwnsKeyboardFocus = (event: KeyboardEvent) => {
          if (!panelVisibleRef.current) return false
          if (terminalInput && document.activeElement === terminalInput) return true
          if (document.activeElement && terminalRoot.contains(document.activeElement)) return true
          return event.target instanceof Node && terminalRoot.contains(event.target)
        }
        const captureFocusedTerminalEscape = (event: KeyboardEvent) => {
          if (event.defaultPrevented || !shouldCaptureFocusedTerminalEscape(event, terminalOwnsKeyboardFocus(event))) return
          event.preventDefault()
          event.stopImmediatePropagation()
          // Match xterm's Escape/Alt+Escape encoding and reuse onData so the
          // existing input queue, replay gate and sticky Ctrl handling still apply.
          if (event.type === 'keydown') term.input(event.altKey ? '\x1b\x1b' : '\x1b', true)
        }
        const escapeEventTypes: Array<'keydown' | 'keyup' | 'keypress'> = ['keydown', 'keyup', 'keypress']
        for (const type of escapeEventTypes) {
          window.addEventListener(type, captureFocusedTerminalEscape, true)
          document.addEventListener(type, captureFocusedTerminalEscape, true)
          terminalRoot.addEventListener(type, captureFocusedTerminalEscape, true)
        }
        // WebGL 渲染器(GPU 加速)不在这里装: 它跟着**面板可见性**挂/摘(见下面那个 effect),
        // 因为常驻页签有很多个, 而 WebGL 上下文是浏览器级稀缺资源。
        try { fit.fit() } catch { /* size 0 race */ }
        termRef.current = term
        fitRef.current = fit
        xtermLoadedRef.current = true
        setXtermReadyFor(entity.id)
        if (term.cols >= 2 && term.rows >= 1) setGeometryReady(true)
        const inspectInput = {
          focus: () => {
            if (!panelVisibleRef.current) throw new Error('terminal is not visible')
            if (snapshotReplayRef.current.active) throw new Error('terminal is restoring')
            const socket = activeWsRef.current
            if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error('terminal socket is not open')
            if (!terminalRoot || !terminalRoot.isConnected || !terminalInput || !terminalRoot.contains(terminalInput)) {
              throw new Error('terminal input is unavailable')
            }
            terminalInput.focus({ preventScroll: true })
          },
          ownsActiveElement: (element: Element | null) => Boolean(
            element && terminalRoot && (element === terminalInput || terminalRoot.contains(element)),
          ),
          paste: (text: string) => {
            inspectInput.focus()
            if (typeof term.paste === 'function') term.paste(text)
            else term.input(text, true)
          },
          key: (key: string) => {
            const data = TERMINAL_INSPECT_KEY_SEQUENCES[key] ?? (key.length === 1 ? key : null)
            if (data === null) throw new Error(`unsupported terminal inspect key: ${key}`)
            inspectInput.focus()
            term.input(data, true)
          },
        }
        const unregisterInspect = registerTerminalInspect(entity.id, {
          sessionId: entity.id,
          provider: String(entity.provider || ''),
          visible: () => panelVisibleRef.current,
          snapshot: () => serializeTerminalInspect(entity.id, entity.provider, term, containerRef.current, panelVisibleRef.current),
          input: inspectInput,
        })

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
            // Returning false bypasses xterm's Ctrl+V control-character path,
            // but deliberately leaves the browser's native paste event intact.
            // Calling term.paste here as well would inject the same clipboard
            // text twice: once manually and once from xterm's paste listener.
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
            // Optimistic: a non-controlling viewer gets a `geometry` reply
            // that puts it back on the PTY grid.
            followingServerGridRef.current = false
            setGeometryReady(true)
            // WS 尚未就绪时 resize 帧不能丢: 挂载期的第一次尺寸校准正是
            // 后端拿到正确 cols 的时刻, 丢了要等下一次容器 resize 才补上
            // (对齐问题的隐藏来源之一)。先落 pendingRef, wsConn.send 挂上时补发。
            const payload = JSON.stringify({
              type: 'resize', cols: term.cols, rows: term.rows,
            })
            const send = wsConnSendRef.current
            if (send) send(payload)
            else pendingResizeRef.current = payload
            const snap = snapshotSizeRef.current
            if (
              !didGeometryResyncRef.current
              && shouldResyncAfterSnapshot(
                entity.provider,
                snap?.cols,
                snap?.rows,
                term.cols,
                term.rows,
                snapshotScreenModelRef.current,
              )
            ) {
              didGeometryResyncRef.current = true
              triggerTerminalReconnect('geometry-resync')
            }
          },
          100,
        )
        resizeSettlerRef.current = resizeSettler
        resizeObs = new ResizeObserver(() => {
          // 隐藏页签(display:none)量出来是 0 → 提交下去等于把 ConPTY 拉成 1×1,
          // 切回来再拉回去, TUI 会整屏重画 —— 那正是用户说的"切换就加载"。
          // 隐藏期间干脆不提交; 重新可见时由可见性 effect 统一 fit 一次。
          if (!panelVisibleRef.current) return
          resizeSettler?.schedule()
        })
        resizeObs.observe(containerRef.current)

        // 字体到货补量: Sarasa Term SC(本地 webfont)加载完之前, 第一次 fit 是
        // 用回退字体量的单元格宽度; 字体 swap 后度量变化但容器尺寸没变,
        // ResizeObserver 不会响 —— opencode 这类按 cols 画右边框的 TUI 就会
        // 右侧锯齿/错列。xterm 的字符度量缓存也不会自己刷新(同尺寸 resize 只在
        // 从未量过时才 measure, 见 xterm resize()/hasValidSize), 这正是用户
        // "手动拖几次尺寸才对齐"的原因: 每次真 resize 都走 _afterResize→measure()。
        // 到货后先故意 +1 触发一次真 resize 让 measure() 用新字体重校,
        // 再由 settled commit propose 出修正后的 cols 下发。
        const fontArrived = () => {
          if (cancelled) return
          const term = termRef.current
          if (!term) return
          try { term.resize(term.cols + 1, term.rows) } catch { /* 忽略 */ }
          resizeSettler?.schedule()
        }
        void whenTerminalFontsReady(TERMINAL_FONT_STACK).then(fontArrived)
        // 一次等待不够: 慢网/晚到的字体子集会在超时之后才到货。loadingdone 在
        // 每个字体加载完成时都触发, 持续补校(settled commit 会去重, +1 resize
        // 不下发任何消息, 成本只是局部重排一帧)。
        const fontSet = (document as Document & { fonts?: FontFaceSet }).fonts
        fontSet?.addEventListener?.('loadingdone', fontArrived)
        fontCleanup = () => fontSet?.removeEventListener?.('loadingdone', fontArrived)

        // 终端输入 → ws (经 wsAutoReconnect 队列, 重连期间排队 open 后补发)
        const disp = term.onData((data: string) => {
          // xterm uses onData for both real keyboard input and terminal-generated
          // replies. Snapshot replay must never inject the latter into the PTY.
          if (snapshotReplayRef.current.active) return
          const userData = stripTerminalGeneratedResponses(data)
          if (!userData) return
          // 软按键 Ctrl 粘滞: armed 时下一个真实字符键与 Ctrl 合成后一次性熄灭。
          const sticky = applyStickyCtrl(ctrlArmedRef.current, userData)
          if (sticky.armed !== ctrlArmedRef.current) {
            ctrlArmedRef.current = sticky.armed
            setCtrlArmed(sticky.armed)
          }
          // Typing on a viewer that follows another viewer's grid takes over
          // sizing: resize locally first, then let the server apply our grid.
          let takeover: TerminalDimensions | null = null
          if (followingServerGridRef.current) {
            const proposed = typeof fit.proposeDimensions === 'function' ? fit.proposeDimensions() : null
            if (proposed && proposed.cols >= 2 && proposed.rows >= 1) {
              takeover = { cols: proposed.cols, rows: proposed.rows }
              try { term.resize(proposed.cols, proposed.rows) } catch { /* disposing */ }
              followingServerGridRef.current = false
            }
          }
          wsConnSendRef.current?.(buildTerminalInputFrame(sticky.output ?? userData, takeover))
        })
        const disposeWheelRecovery = installTerminalWheelRecovery(terminalRoot, term, {
          enabled: () => snapshotScreenModelRef.current
            && /^codex(?:_|$)/.test(String(entity.provider || ''))
            && !snapshotReplayRef.current.active,
          send: (data) => wsConnSendRef.current?.(JSON.stringify({ type: 'input', data })),
        })
        // 全屏 TUI(Claude Code 2.1 fullscreen renderer / opencode)自己接管鼠标,
        // 历史根本不进 xterm scrollback —— 触摸拖动只能翻译成滚轮上报, 否则平板无解。
        const disposeTouchScroll = installTerminalTouchScroller(terminalRoot, term, {
          send: (data: string) => wsConnSendRef.current?.(JSON.stringify({
            type: 'input', data,
          })),
        })
        const syncTerminalBottom = () => {
          if (!cancelled) setTerminalAtBottom(isTerminalViewportAtBottom(term))
        }
        const scrollSubscription = term.onScroll(syncTerminalBottom)
        const disposeMobileInput = term.textarea instanceof HTMLTextAreaElement
          ? installMobileTerminalInputGuard(term.textarea, term, { eventRoot: terminalRoot })
          : () => undefined
        pendingDispose = () => {
          terminalInput?.removeEventListener('focus', handleTerminalInputFocus)
          terminalInput?.removeEventListener('blur', handleTerminalInputBlur)
          terminalInputFocusedRef.current = false
          for (const type of escapeEventTypes) {
            window.removeEventListener(type, captureFocusedTerminalEscape, true)
            document.removeEventListener(type, captureFocusedTerminalEscape, true)
            terminalRoot.removeEventListener(type, captureFocusedTerminalEscape, true)
          }
          unregisterInspect()
          disp.dispose()
          disposeWheelRecovery()
          scrollSubscription.dispose()
          disposeTouchScroll()
          disposeMobileInput()
        }
      } catch (e) {
        if (!cancelled) {
          setTerminalRestoring(false)
          setError(String(e))
        }
      }
    })()

    return () => {
      cancelled = true
      fontCleanup?.()
      resizeSettler?.dispose()
      if (resizeSettlerRef.current === resizeSettler) resizeSettlerRef.current = null
      if (restoreRevealTimerRef.current !== null) {
        clearTimeout(restoreRevealTimerRef.current)
        restoreRevealTimerRef.current = null
      }
      if (restoreWatchdogTimerRef.current !== null) {
        clearTimeout(restoreWatchdogTimerRef.current)
        restoreWatchdogTimerRef.current = null
      }
      closeTerminalFirstFrameAttempt(firstFrameWatchdogRef.current)
      try { resizeObs?.disconnect() } catch { /* */ }
      try { pendingDispose?.() } catch { /* */ }
      if (webglDisposeTimerRef.current !== null) {
        clearTimeout(webglDisposeTimerRef.current)
        webglDisposeTimerRef.current = null
      }
      try { webglRef.current?.dispose() } catch { /* 已 dispose */ }
      webglRef.current = null
      try { termRef.current?.dispose() } catch { /* */ }
      termRef.current = null
      fitRef.current = null
      xtermLoadedRef.current = false
      setGeometryReady(false)
      snapshotSizeRef.current = null
      didGeometryResyncRef.current = false
      // The next xterm is empty and must receive a self-contained screen frame;
      // an incremental resume would otherwise append deltas to a blank terminal.
      lastSeqRef.current = null
    }
  }, [entity.id, terminalMounted, recoverTerminalFocus])

  /** GPU 上下文跟着可见性走。见 shouldAttachTerminalWebgl 的说明。 */
  useEffect(() => {
    if (xtermReadyFor !== entity.id) return undefined
    const term = termRef.current
    const WebglAddon = xtermLibRef.current?.WebglAddon
    if (!term) return undefined

    const cancelPendingDispose = () => {
      if (webglDisposeTimerRef.current === null) return
      clearTimeout(webglDisposeTimerRef.current)
      webglDisposeTimerRef.current = null
    }

    if (!shouldAttachTerminalWebgl(panelVisible, entity.provider, terminalRenderer)) {
      if (!webglRef.current || webglDisposeTimerRef.current !== null) return undefined
      webglDisposeTimerRef.current = setTimeout(() => {
        webglDisposeTimerRef.current = null
        const webgl = webglRef.current
        webglRef.current = null
        // dispose 后 xterm 自动退回内建 DOM 渲染器(与 context loss 同一条路), 内容不丢。
        try { webgl?.dispose() } catch { /* 已 dispose */ }
      }, TERMINAL_WEBGL_IDLE_DISPOSE_MS)
      return cancelPendingDispose
    }

    cancelPendingDispose()
    if (!webglRef.current && WebglAddon) {
      try {
        const webgl = new WebglAddon()
        webgl.onContextLoss(() => {
          try { webgl.dispose() } catch { /* 已 dispose */ }
          if (webglRef.current === webgl) webglRef.current = null
        })
        term.loadAddon(webgl)
        webglRef.current = webgl
      } catch { /* 降级: 默认 DOM 渲染器 */ }
    }
    // 切回可见: 隐藏期间不提交 resize, 这里补一次 fit(尺寸可能在隐藏时被布局改过)。
    const fit = fitRef.current
    if (fit) {
      try {
        fit.fit()
        resizeSettlerRef.current?.resetCommitted()
        resizeSettlerRef.current?.schedule()
      } catch { /* size 0 race */ }
    }
    // WebGL 渲染器隐藏期间会被 dispose, 重新 attach 后画布是空白的一帧;
    // opencode 这类静态 TUI 没有新输出, 不主动 refresh 就一直"坏"在页面上
    // (用户切来切去看到的就是这个)。fit 同尺寸时不会触发重绘, 显式刷一遍视口。
    try { term.refresh(0, Math.max(0, term.rows - 1)) } catch { /* renderer may be disposing */ }
    return undefined
  }, [entity.id, entity.provider, panelVisible, terminalRenderer, xtermReadyFor])

  // 2) WebSocket 自愈 — 重连后 clear term 重写 snapshot, 避免 ring-buffer 重写视觉重复
  const wsConnSendRef = useRef<((data: string) => void) | null>(null)
  const pendingResizeRef = useRef<string | null>(null)

  const finishTerminalRestore = useCallback((delayMs = 0) => {
    if (restoreRevealTimerRef.current !== null) clearTimeout(restoreRevealTimerRef.current)
    restoreRevealTimerRef.current = setTimeout(() => {
      restoreRevealTimerRef.current = null
      redrawSettlingRef.current = false
      redrawWritesPendingRef.current = 0
      setTerminalRestoring(false)
    }, delayMs)
  }, [])

  const completeSnapshotReplay = useCallback((meta: unknown) => {
    const requestRedraw = shouldRequestTerminalRedraw(meta)
    endTerminalSnapshot(snapshotReplayRef.current, () => {
      if (restoreWatchdogTimerRef.current !== null) {
        clearTimeout(restoreWatchdogTimerRef.current)
        restoreWatchdogTimerRef.current = null
      }
      const liveTerm = termRef.current
      let willResync = false
      if (liveTerm) {
        try { liveTerm.refresh(0, Math.max(0, liveTerm.rows - 1)) } catch { /* renderer may be disposing */ }
        // 先判 resync 再决定要不要 nudge: 马上就要重连的话, 这里的 redraw 只会
        // 让 codex 类 TUI 白白多做一次"清屏+整份转录逐行重打"(实测 ~200 帧/98KB),
        // 且这份重打会进 ring 又被下一次快照原样重放 —— 正是"打开页面慢速逐行
        // 重载"的主要放大器。重连后的快照若仍截断, 会再走一次这里并正常 nudge。
        const snap = snapshotSizeRef.current
        willResync = !didGeometryResyncRef.current
          && shouldResyncAfterSnapshot(
            (meta as { provider?: unknown } | null)?.provider ?? entity.provider,
            snap?.cols,
            snap?.rows,
            liveTerm.cols,
            liveTerm.rows,
            snapshotScreenModelRef.current,
          )
        if (requestRedraw && !willResync) {
          redrawSettlingRef.current = true
          redrawWritesPendingRef.current = 0
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
        if (willResync) {
          didGeometryResyncRef.current = true
          triggerTerminalReconnect('geometry-resync')
        }
      }
      const startedAt = snapshotReplayStartRef.current
      const metaObj = (meta && typeof meta === 'object') ? meta as Record<string, unknown> : {}
      reportFrontendEvent('terminal_snapshot_replay', 'terminal snapshot replay complete', {
        session_id: entity.id,
        duration_ms: startedAt !== null ? Math.round(performance.now() - startedAt) : null,
        bytes: snapshotByteCountRef.current,
        chunk_count: snapshotChunkCountRef.current,
        truncated: Boolean(metaObj.replay_truncated || metaObj.client_replay_truncated),
        screen_frame: Boolean(metaObj.screen_frame),
        watchdog_abandoned: snapshotTimedOutRef.current,
      })
      snapshotReplayStartRef.current = null
      if (!willResync) recoverTerminalFocus()
      // A truncated TUI snapshot is followed by a provider redraw. Keep the
      // terminal covered until xterm has parsed that burst, with a fallback for
      // providers that ignore redraw. This avoids visibly painting line by line.
      // A pending geometry-resync keeps the cover too: the reconnect's
      // snapshot_begin re-arms it, so the gap never flashes stale content.
      finishTerminalRestore(requestRedraw || willResync ? 1_500 : 0)
    })
  }, [entity.id, entity.provider, finishTerminalRestore, recoverTerminalFocus])

  const armTerminalRestoreWatchdog = useCallback(() => {
    if (restoreWatchdogTimerRef.current !== null) {
      clearTimeout(restoreWatchdogTimerRef.current)
    }
    restoreWatchdogTimerRef.current = setTimeout(() => {
      restoreWatchdogTimerRef.current = null
      snapshotTimedOutRef.current = true
      const discarded = consumeTerminalSnapshotBuffer(snapshotBufferRef.current)
      snapshotByteCountRef.current = discarded.chunks.reduce((total, chunk) => total + chunk.length, 0)
      reportFrontendEvent('terminal_watchdog', 'restore watchdog fired', {
        session_id: entity.id,
        watchdog: 'restore',
        waited_ms: TERMINAL_RESTORE_WATCHDOG_MS,
      })
      if (!snapshotReplayRef.current.ended) {
        completeSnapshotReplay({
          ...(snapshotMetaRef.current && typeof snapshotMetaRef.current === 'object'
            ? snapshotMetaRef.current
            : { provider: entity.provider }),
          replay_truncated: true,
          client_replay_truncated: true,
        })
      }
      forceFinishTerminalSnapshot(snapshotReplayRef.current)
      setTerminalRestoring(false)
      setError('\u7ec8\u7aef\u5386\u53f2\u6062\u590d\u8d85\u65f6\uff0c\u5df2\u8df3\u8fc7\u5269\u4f59\u5386\u53f2\u5e76\u8bf7\u6c42\u91cd\u7ed8\u3002')
    }, TERMINAL_RESTORE_WATCHDOG_MS)
  }, [completeSnapshotReplay, entity.id, entity.provider])

  const handleMessage = useCallback((ev: MessageEvent) => {
    let msg: any
    try { msg = JSON.parse(ev.data as string) } catch { return }
    const term = termRef.current
    if (!term) return
    markTerminalFirstFrameReceived(firstFrameWatchdogRef.current)
    if (awaitingResumeOutcomeRef.current) {
      const resumed = resumeOutcomeFor(msg.type)
      if (resumed !== null) {
        awaitingResumeOutcomeRef.current = false
        reportFrontendEvent('terminal_ws_reconnect', `reconnect settled: resumed=${resumed}`, {
          session_id: entity.id,
          resumed,
          reconnect_count: reconnectCountRef.current,
        })
      }
    }
    if (msg.type === 'snapshot_begin') {
      if (restoreRevealTimerRef.current !== null) {
        clearTimeout(restoreRevealTimerRef.current)
        restoreRevealTimerRef.current = null
      }
      redrawSettlingRef.current = false
      redrawWritesPendingRef.current = 0
      setTerminalRestoring(true)
      snapshotTimedOutRef.current = false
      snapshotBufferRef.current = { data: '', truncated: false }
      snapshotMetaRef.current = msg.meta && typeof msg.meta === 'object' ? msg.meta : null
      const meta = snapshotMetaRef.current as { cols?: unknown; rows?: unknown } | null
      const cols = Number(meta?.cols)
      const rows = Number(meta?.rows)
      snapshotSizeRef.current = Number.isFinite(cols) && Number.isFinite(rows)
        ? { cols, rows }
        : null
      snapshotScreenModelRef.current = Boolean(
        (snapshotMetaRef.current as { screen_model?: unknown } | null)?.screen_model,
      )
      beginTerminalSnapshot(term, snapshotReplayRef.current)
      armTerminalRestoreWatchdog()
      snapshotReplayStartRef.current = performance.now()
      snapshotChunkCountRef.current = 0
      snapshotByteCountRef.current = 0
    } else if (msg.type === 'snapshot_chunk' && Array.isArray(msg.chunks)) {
      snapshotChunkCountRef.current += msg.chunks.length
      if (!snapshotTimedOutRef.current) {
        appendTerminalSnapshotBuffer(snapshotBufferRef.current, msg.chunks)
      }
    } else if (msg.type === 'snapshot_end') {
      if (snapshotTimedOutRef.current) {
        snapshotMetaRef.current = null
        return
      }
      const buffered = consumeTerminalSnapshotBuffer(snapshotBufferRef.current)
      snapshotByteCountRef.current = buffered.chunks.reduce((total, chunk) => total + chunk.length, 0)
      appendTerminalSnapshot(term, buffered.chunks, snapshotReplayRef.current)
      const baseMeta = snapshotMetaRef.current
      snapshotMetaRef.current = null
      if (baseMeta && typeof baseMeta === 'object') {
        lastSeqRef.current = nextLastSeqFromFrame(lastSeqRef.current, (baseMeta as { last_seq?: unknown }).last_seq)
      }
      completeSnapshotReplay(buffered.truncated
        ? {
            ...(baseMeta && typeof baseMeta === 'object' ? baseMeta : { provider: entity.provider }),
            replay_truncated: true,
            client_replay_truncated: true,
          }
        : baseMeta)
    } else if (msg.type === 'resume_ok') {
      // PERF-OBSERVATORY P4.1: short disconnect, the ring still covers what
      // we already have. No snapshot follows — skip term.reset() entirely
      // and just keep writing the incremental `output` frames that follow.
      if (restoreRevealTimerRef.current !== null) {
        clearTimeout(restoreRevealTimerRef.current)
        restoreRevealTimerRef.current = null
      }
      if (restoreWatchdogTimerRef.current !== null) {
        clearTimeout(restoreWatchdogTimerRef.current)
        restoreWatchdogTimerRef.current = null
      }
      setTerminalRestoring(false)
      recoverTerminalFocus()
    } else if (msg.type === 'snapshot' && Array.isArray(msg.chunks)) {
      // Reconnect snapshots are buffered to a fixed tail before xterm sees them.
      setTerminalRestoring(true)
      snapshotTimedOutRef.current = false
      snapshotBufferRef.current = { data: '', truncated: false }
      beginTerminalSnapshot(term, snapshotReplayRef.current)
      armTerminalRestoreWatchdog()
      snapshotReplayStartRef.current = performance.now()
      snapshotChunkCountRef.current = msg.chunks.length
      appendTerminalSnapshotBuffer(snapshotBufferRef.current, msg.chunks)
      const buffered = consumeTerminalSnapshotBuffer(snapshotBufferRef.current)
      snapshotByteCountRef.current = buffered.chunks.reduce((total, chunk) => total + chunk.length, 0)
      appendTerminalSnapshot(term, buffered.chunks, snapshotReplayRef.current)
      completeSnapshotReplay({
        provider: entity.provider,
        replay_truncated: buffered.truncated,
        client_replay_truncated: buffered.truncated,
      })
    } else if (msg.type === 'output' && typeof msg.data === 'string') {
      lastSeqRef.current = nextLastSeqFromFrame(lastSeqRef.current, msg.seq)
      if (!redrawSettlingRef.current) {
        term.write(stripTerminalUnderline(msg.data))
      } else {
        if (restoreRevealTimerRef.current !== null) {
          clearTimeout(restoreRevealTimerRef.current)
          restoreRevealTimerRef.current = null
        }
        redrawWritesPendingRef.current += 1
        term.write(stripTerminalUnderline(msg.data), () => {
          redrawWritesPendingRef.current = Math.max(0, redrawWritesPendingRef.current - 1)
          if (redrawWritesPendingRef.current === 0) finishTerminalRestore(80)
        })
      }
    } else if (msg.type === 'geometry') {
      // Another viewer sizes the PTY. Rendering its output at our own fit
      // width wraps every full-width rule and misplaces relative cursor moves,
      // so adopt the PTY grid (a fresh frame at it follows for screen-model TUIs).
      const grid = parseServerGeometry(msg)
      if (!grid) return
      if (term.cols !== grid.cols || term.rows !== grid.rows) {
        try { term.resize(grid.cols, grid.rows) } catch { /* disposing */ }
      }
      const fit = fitRef.current
      const proposed = fit && typeof fit.proposeDimensions === 'function' ? fit.proposeDimensions() : null
      followingServerGridRef.current = Boolean(
        proposed && (proposed.cols !== grid.cols || proposed.rows !== grid.rows),
      )
    } else if (msg.type === 'exit') {
      if (restoreWatchdogTimerRef.current !== null) {
        clearTimeout(restoreWatchdogTimerRef.current)
        restoreWatchdogTimerRef.current = null
      }
      setTerminalRestoring(false)
      setAlive(false)
      term.write(`\r\n\x1b[33m[session ended: ${msg.reason || 'unknown'}]\x1b[0m\r\n`)
    }
  }, [armTerminalRestoreWatchdog, completeSnapshotReplay, entity.id, entity.provider, finishTerminalRestore, recoverTerminalFocus])

  const handleOpen = useCallback((ws: WebSocket, _isReconnect: boolean) => {
    setError(null)
    redrawSettlingRef.current = false
    redrawWritesPendingRef.current = 0
    setTerminalRestoring(true)
    activeWsRef.current = ws
    // PERF-OBSERVATORY P4.1: must be the very first message on this socket —
    // the server reads it as an optional preamble before deciding between an
    // incremental resume and a full snapshot. null last_seq (nothing received
    // yet) is a normal first-ever attach; the server treats it like today.
    try {
      ws.send(JSON.stringify(buildAttachPreamble(lastSeqRef.current)))
    } catch { /* reconnect will retry; server falls back to a full snapshot */ }
    const visible = panelVisibleRef.current
    if (shouldSyncVisibilityOnOpen(lastReportedVisibilityRef.current, visible)) {
      lastReportedVisibilityRef.current = visible
      try {
        ws.send(JSON.stringify({ type: 'visibility', visible }))
        reportFrontendEvent('terminal_visibility', `visibility sync on open: ${visible}`, {
          session_id: entity.id,
          visible,
          path: 'open',
        })
      } catch { /* reconnect will establish the desired state */ }
    }
    armTerminalFirstFrameWatchdog(firstFrameWatchdogRef.current, () => {
      if (activeWsRef.current !== ws) return
      reportFrontendEvent('terminal_watchdog', 'first frame watchdog fired', {
        session_id: entity.id,
        watchdog: 'first_frame',
        waited_ms: TERMINAL_FIRST_FRAME_WATCHDOG_MS,
      })
      setTerminalRestoring(false)
      setError('\u4f1a\u8bdd\u8fde\u63a5\u540e\u672a\u6536\u5230\u7ec8\u7aef\u6570\u636e\uff0c\u6b63\u5728\u81ea\u52a8\u91cd\u8fde\u3002')
      try { ws.close(4000, 'initial-snapshot-timeout') } catch { /* reconnect hook will retry */ }
    })
    const fit = fitRef.current
    if (fit) {
      try {
        fit.fit()
        resizeSettlerRef.current?.resetCommitted()
        resizeSettlerRef.current?.schedule()
      } catch { /* */ }
    }
  }, [entity.id])

  const handleConnectionError = useCallback(() => {
    if (firstFrameWatchdogRef.current.seen) return
    if (firstFrameWatchdogRef.current.timer !== null) {
      clearTimeout(firstFrameWatchdogRef.current.timer)
      firstFrameWatchdogRef.current.timer = null
    }
    setTerminalRestoring(false)
    setError('\u4f1a\u8bdd\u8fde\u63a5\u5931\u8d25\uff0c\u6b63\u5728\u81ea\u52a8\u91cd\u8fde\u3002')
  }, [])

  const handleClose = useCallback((ev: CloseEvent) => {
    activeWsRef.current = null
    // code===1000(intentionalCloseCode \u9ed8\u8ba4\u503c)\u662f\u7ec4\u4ef6\u5378\u8f7d\u7b49\u4e3b\u52a8\u5173\u95ed, \u4e0d\u4f1a\u89e6\u53d1 backoff \u91cd\u8fde,
    // \u4e0d\u7b97\u4e00\u6b21"\u91cd\u8fde"\u3002\u5176\u4f59 code(\u542b 4000 \u9996\u5e27\u770b\u95e8\u72d7\u81ea\u5173\u30011012 \u670d\u52a1\u7aef\u8e22\u3001\u7f51\u7edc\u6296\u52a8\u7b49)
    // wsAutoReconnect \u90fd\u4f1a\u5b89\u6392\u4e0b\u4e00\u6b21 connect(), \u5728\u8fd9\u91cc\u62a5\u4e00\u6b21\u5373\u53ef \u2014\u2014 geometry-resync \u90a3\u6761
    // \u663e\u5f0f\u8def\u5f84\u4e0d\u4f1a\u8d70\u5230\u8fd9\u91cc(\u89c1 triggerTerminalReconnect \u7684\u6ce8\u91ca), \u4e0d\u4f1a\u91cd\u590d\u8ba1\u6570\u3002
    if (ev.code !== 1000) {
      reconnectCountRef.current += 1
      // Whether *this* reconnect resumed isn't knowable yet — that settles
      // once resume_ok or snapshot_begin arrives on the new socket (see the
      // awaitingResumeOutcomeRef check at the top of handleMessage).
      awaitingResumeOutcomeRef.current = true
      reportFrontendEvent('terminal_ws_reconnect', `ws closed code=${ev.code}`, {
        session_id: entity.id,
        close_code: ev.code,
        close_reason: ev.reason || null,
        category: classifyTerminalCloseReason(ev.code, ev.reason || ''),
        reconnect_count: reconnectCountRef.current,
      })
    }
    if (closeTerminalFirstFrameAttempt(firstFrameWatchdogRef.current)) {
      setTerminalRestoring(false)
      setError('\u4f1a\u8bdd\u8fde\u63a5\u5df2\u65ad\u5f00\uff0c\u6b63\u5728\u81ea\u52a8\u91cd\u8fde\u3002')
    }
  }, [entity.id])

  const wsConn = useWsAutoReconnect({
    url: sessionId ? ccApi.wsUrl(sessionId) : '',
    // Only the visible terminal owns a browser socket. The detached host keeps
    // running while hidden and supplies a bounded current-screen frame on show.
    enabled: shouldKeepTerminalSocket(sessionId, xtermReadyFor, entity.id, panelVisible, geometryReady),
    queueWhenDisconnected: false,
    onMessage: handleMessage,
    onOpen: handleOpen,
    onError: handleConnectionError,
    onClose: handleClose,
  })

  // expose wsConn.send to ref (xterm 加载 effect 运行时尚没有 wsConn)
  useEffect(() => {
    wsConnSendRef.current = wsConn.send
    reconnectRef.current = wsConn.reconnect
    const pending = pendingResizeRef.current
    pendingResizeRef.current = null
    if (pending) wsConn.send(pending)
  }, [wsConn.reconnect, wsConn.send])

  useEffect(() => {
    if (!shouldReportTerminalVisibility(lastReportedVisibilityRef.current, panelVisible)) return undefined
    // PERF-OBSERVATORY P4.1: debounce 500ms so rapid tab switching (alt-tab,
    // fast panel flips) doesn't pause/resync the PTY host on every flicker —
    // only the value still current after a quiet 500ms window gets sent.
    if (visibilityDebounceTimerRef.current !== null) {
      clearTimeout(visibilityDebounceTimerRef.current)
      visibilityDebounceTimerRef.current = null
    }
    visibilityDebounceTimerRef.current = setTimeout(() => {
      visibilityDebounceTimerRef.current = null
      const ws = activeWsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return
      if (!shouldReportTerminalVisibility(lastReportedVisibilityRef.current, panelVisible)) return
      const revealing = shouldRequestRevealRedraw(
        lastReportedVisibilityRef.current,
        panelVisible,
        entity.provider,
      )
      lastReportedVisibilityRef.current = panelVisible
      try {
        ws.send(JSON.stringify({ type: 'visibility', visible: panelVisible }))
        reportFrontendEvent('terminal_visibility', `visibility change: ${panelVisible}`, {
          session_id: entity.id,
          visible: panelVisible,
          path: 'effect',
        })
      } catch { /* reconnect sends current visibility from handleOpen */ }
      if (!revealing) return
      const term = termRef.current
      if (!term || term.cols < 2 || term.rows < 1) return
      try {
        ws.send(JSON.stringify({ type: 'redraw', cols: term.cols, rows: term.rows }))
      } catch { /* snapshot reconnect will request a repaint */ }
    }, TERMINAL_VISIBILITY_DEBOUNCE_MS)
    return () => {
      if (visibilityDebounceTimerRef.current !== null) {
        clearTimeout(visibilityDebounceTimerRef.current)
        visibilityDebounceTimerRef.current = null
      }
    }
  }, [entity.id, entity.provider, panelVisible, wsConn.state])

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

  // 软按键条: 与键盘 onData 相同的发送路径(wsConnSendRef), 点击后主动把焦点
  // 收回 xterm 隐藏 textarea, 防止移动端输入法因失焦而收起。
  const sendSoftKey = useCallback((sequence: string) => {
    termRef.current?.textarea?.focus?.({ preventScroll: true })
    wsConnSendRef.current?.(JSON.stringify({ type: 'input', data: sequence }))
  }, [])

  const toggleCtrlSoftKey = useCallback(() => {
    setCtrlArmed((value) => {
      const next = !value
      ctrlArmedRef.current = next
      return next
    })
    termRef.current?.textarea?.focus?.({ preventScroll: true })
  }, [])

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
          {shouldShowTerminalSoftKeys(
            touchMode,
            terminalInputFocused,
            window.__OMNICOMPANY_HOST__?.runtime === 'desktop',
          ) && (
            <TerminalSoftKeys
              ctrlArmed={ctrlArmed}
              onToggleCtrl={toggleCtrlSoftKey}
              onSend={sendSoftKey}
            />
          )}
          {billingIssue && alive && (
            <div
              className="cs-billing"
              data-testid="cc-billing-switch"
              role="status"
              aria-live="polite"
            >
              <span className="cs-billing-note">检测到费用问题（欠费/配额/余额）</span>
              <button
                type="button"
                className="cs-billing-btn"
                disabled={switchingProvider}
                onClick={() => { void switchProvider() }}
              >
                {switchingProvider
                  ? <RefreshCw size={13} className="cccp-spinning" />
                  : <ArrowRightLeft size={13} />}
                {switchingProvider ? '切换中…' : '切换到其他provider'}
              </button>
            </div>
          )}
          {terminalRestoring && (
            <div
              className="cs-term-restoring"
              data-testid="cc-terminal-restoring"
              role="status"
              aria-live="polite"
            >
              <span>正在恢复终端…</span>
            </div>
          )}
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
