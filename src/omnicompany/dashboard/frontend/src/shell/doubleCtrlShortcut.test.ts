import { afterEach, describe, expect, it, vi } from 'vitest'
import { DOUBLE_CTRL_QUIET_MS, installDoubleCtrlShortcut } from './doubleCtrlShortcut'

function ctrlTap() {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Control', ctrlKey: true }))
  window.dispatchEvent(new KeyboardEvent('keyup', { key: 'Control' }))
}

afterEach(() => {
  vi.useRealTimers()
})

describe('installDoubleCtrlShortcut', () => {
  it('两个干净 Ctrl 点按在静默窗口后触发一次', () => {
    vi.useFakeTimers()
    const onDouble = vi.fn()
    const dispose = installDoubleCtrlShortcut(window, onDouble)
    ctrlTap()
    ctrlTap()
    expect(onDouble).not.toHaveBeenCalled()
    vi.advanceTimersByTime(DOUBLE_CTRL_QUIET_MS)
    expect(onDouble).toHaveBeenCalledTimes(1)
    dispose()
  })

  it('不会把 Ctrl+C 当作 Ctrl 点按', () => {
    vi.useFakeTimers()
    const onDouble = vi.fn()
    const dispose = installDoubleCtrlShortcut(window, onDouble)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Control', ctrlKey: true }))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }))
    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'c', ctrlKey: true }))
    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'Control' }))
    ctrlTap()
    vi.advanceTimersByTime(DOUBLE_CTRL_QUIET_MS)
    expect(onDouble).not.toHaveBeenCalled()
    dispose()
  })
})
