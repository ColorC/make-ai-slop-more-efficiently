import { describe, expect, it } from 'vitest'
import { stripTerminalUnderline } from './Editor'

describe('terminal underline control sequences', () => {
  it('keeps real ESC bytes when disabling underline around prompt punctuation', () => {
    expect(stripTerminalUnderline('\x1b[4m(\x1b[24m')).toBe('\x1b[24m(\x1b[24m')
    expect(stripTerminalUnderline('\x1b[21mtext\x1b[24m')).toBe('\x1b[24mtext\x1b[24m')
  })

  it('preserves colors, cursor movement and text that discusses escape sequences', () => {
    const data = '\x1b[31mred\x1b[0m\x1b[2K literal \\x1b[24m'
    expect(stripTerminalUnderline(data)).toBe(data)
  })
})
