import { describe, expect, it } from 'vitest'
import { applyStickyCtrl, encodeCtrlChar, SOFT_KEY_SEQUENCES } from './softKeySequences'

describe('SOFT_KEY_SEQUENCES', () => {
  it('maps each direct key to its standard control sequence', () => {
    expect(SOFT_KEY_SEQUENCES.esc).toBe('\x1b')
    expect(SOFT_KEY_SEQUENCES.tab).toBe('\t')
    expect(SOFT_KEY_SEQUENCES.up).toBe('\x1b[A')
    expect(SOFT_KEY_SEQUENCES.down).toBe('\x1b[B')
    expect(SOFT_KEY_SEQUENCES.right).toBe('\x1b[C')
    expect(SOFT_KEY_SEQUENCES.left).toBe('\x1b[D')
    expect(SOFT_KEY_SEQUENCES.pgup).toBe('\x1b[5~')
    expect(SOFT_KEY_SEQUENCES.pgdn).toBe('\x1b[6~')
    expect(SOFT_KEY_SEQUENCES.enter).toBe('\r')
  })
})

describe('encodeCtrlChar', () => {
  it('encodes upper and lower case letters to the same C0 code', () => {
    expect(encodeCtrlChar('c')).toBe('\x03')
    expect(encodeCtrlChar('C')).toBe('\x03')
    expect(encodeCtrlChar('a')).toBe('\x01')
    expect(encodeCtrlChar('z')).toBe('\x1a')
  })

  it('encodes the special bracket punctuation Ctrl composes with', () => {
    expect(encodeCtrlChar('[')).toBe('\x1b')
    expect(encodeCtrlChar('\\')).toBe('\x1c')
    expect(encodeCtrlChar(']')).toBe('\x1d')
    expect(encodeCtrlChar('^')).toBe('\x1e')
    expect(encodeCtrlChar('_')).toBe('\x1f')
    expect(encodeCtrlChar('?')).toBe('\x7f')
    expect(encodeCtrlChar('@')).toBe('\x00')
  })

  it('returns null for characters with no Ctrl composition', () => {
    expect(encodeCtrlChar('5')).toBeNull()
    expect(encodeCtrlChar('!')).toBeNull()
    expect(encodeCtrlChar('')).toBeNull()
    expect(encodeCtrlChar('ab')).toBeNull()
  })
})

describe('applyStickyCtrl', () => {
  it('passes data through unchanged when not armed', () => {
    expect(applyStickyCtrl(false, 'c')).toEqual({ armed: false, output: null })
  })

  it('composes the next character key with Ctrl and disarms', () => {
    expect(applyStickyCtrl(true, 'c')).toEqual({ armed: false, output: '\x03' })
    expect(applyStickyCtrl(true, 'X')).toEqual({ armed: false, output: '\x18' })
  })

  it('preserves any trailing bytes after the composed first character', () => {
    // Defensive: onData chunks are normally single keystrokes, but a paste-like
    // burst should still only transform the first character.
    expect(applyStickyCtrl(true, 'cd')).toEqual({ armed: false, output: '\x03d' })
  })

  it('disarms without transforming when the chunk is not composable', () => {
    // Arrow-key escape sequence: first char is ESC itself, not a letter.
    expect(applyStickyCtrl(true, '\x1b[A')).toEqual({ armed: false, output: null })
    // Digit keys have no Ctrl+digit convention.
    expect(applyStickyCtrl(true, '5')).toEqual({ armed: false, output: null })
  })

  it('stays armed and no-ops on an empty chunk', () => {
    expect(applyStickyCtrl(true, '')).toEqual({ armed: true, output: null })
  })
})
