/**
 * Pure key→sequence mapping and sticky-Ctrl composition for the mobile
 * terminal soft-key bar (TerminalSoftKeys.tsx). Kept side-effect free so the
 * mapping table and the Ctrl arm/compose state machine can be unit tested
 * without xterm or a DOM.
 */

export type SoftKeyId =
  | 'esc'
  | 'tab'
  | 'ctrl'
  | 'up'
  | 'down'
  | 'left'
  | 'right'
  | 'pgup'
  | 'pgdn'
  | 'enter'

/** Direct key → control sequence map. 'ctrl' is excluded: it is a sticky
 *  toggle composed with the next character key rather than a fixed sequence. */
export const SOFT_KEY_SEQUENCES: Record<Exclude<SoftKeyId, 'ctrl'>, string> = {
  esc: '\x1b',
  tab: '\t',
  up: '\x1b[A',
  down: '\x1b[B',
  right: '\x1b[C',
  left: '\x1b[D',
  pgup: '\x1b[5~',
  pgdn: '\x1b[6~',
  enter: '\r',
}

const CTRL_SPECIAL: Record<string, string> = {
  '@': '\x00',
  '[': '\x1b',
  '\\': '\x1c',
  ']': '\x1d',
  '^': '\x1e',
  _: '\x1f',
  '?': '\x7f',
}

/** C0 control code for Ctrl+ch, or null when ch has no Ctrl composition
 *  (digits, most punctuation, multi-char escape sequences). */
export function encodeCtrlChar(ch: string): string | null {
  if (ch.length !== 1) return null
  const upper = ch.toUpperCase()
  if (upper >= 'A' && upper <= 'Z') return String.fromCharCode(upper.charCodeAt(0) - 64)
  return CTRL_SPECIAL[ch] ?? null
}

export interface StickyCtrlResult {
  /** Ctrl arm state after processing this chunk. */
  armed: boolean
  /** Replacement bytes to send instead of the input chunk, or null to send it unchanged. */
  output: string | null
}

/**
 * Applies the sticky Ctrl toggle to one outgoing input chunk.
 *
 * When armed, the next character key composes with Ctrl (e.g. tapping the
 * soft Ctrl key then typing "c" sends Ctrl+C) and the toggle disarms — one
 * shot, matching the mobile "modifier key" convention (Termux et al). A
 * non-composable chunk (arrow/function-key escape sequences, digits, an
 * empty chunk) disarms without transforming the data: holding Ctrl armed
 * across an unrelated keypress would silently eat later input instead.
 */
export function applyStickyCtrl(armed: boolean, data: string): StickyCtrlResult {
  if (!armed || data.length === 0) return { armed, output: null }
  const encoded = encodeCtrlChar(data[0])
  if (encoded === null) return { armed: false, output: null }
  return { armed: false, output: encoded + data.slice(1) }
}
