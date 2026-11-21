import { describe, expect, it } from 'vitest'
import { shouldShowTerminalSoftKeys } from './TerminalSoftKeys'

describe('terminal soft-key visibility', () => {
  it('shows only while a touch terminal owns input focus', () => {
    expect(shouldShowTerminalSoftKeys(true, true)).toBe(true)
    expect(shouldShowTerminalSoftKeys(true, false)).toBe(false)
    expect(shouldShowTerminalSoftKeys(false, true)).toBe(false)
    expect(shouldShowTerminalSoftKeys(false, false)).toBe(false)
  })

  it('stays hidden in the native Desktop host even on a touch display', () => {
    expect(shouldShowTerminalSoftKeys(true, true, true)).toBe(false)
  })
})
