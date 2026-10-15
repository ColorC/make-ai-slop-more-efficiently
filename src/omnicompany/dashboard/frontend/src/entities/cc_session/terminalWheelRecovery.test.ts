import { describe, expect, it, vi } from 'vitest'
import { installTerminalWheelRecovery } from './terminalWheelRecovery'

function fixture() {
  const root = document.createElement('div')
  const term = { modes: { mouseTrackingMode: 'none' }, buffer: { active: { baseY: 0 } } }
  const send = vi.fn()
  const options = { enabled: () => true, send }
  const dispose = installTerminalWheelRecovery(root, term, options)
  const wheel = (deltaY: number, extra: WheelEventInit = {}) => {
    const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY, ...extra })
    root.dispatchEvent(event)
    return event
  }
  return { root, term, send, options, dispose, wheel }
}

describe('legacy Codex snapshot wheel recovery', () => {
  it('sends page keys in both directions and removes its listener on dispose', () => {
    const f = fixture()
    expect(f.wheel(-100).defaultPrevented).toBe(true)
    f.wheel(100)
    expect(f.send.mock.calls.flat()).toEqual(['\x1b[5~', '\x1b[6~'])
    f.dispose()
    expect(f.wheel(-100).defaultPrevented).toBe(false)
    expect(f.send).toHaveBeenCalledTimes(2)
  })

  it('accumulates trackpad deltas and caps a large burst at one page', () => {
    const f = fixture()
    f.wheel(-20)
    f.wheel(-20)
    expect(f.send).not.toHaveBeenCalled()
    f.wheel(-20)
    f.wheel(-6000)
    expect(f.send).toHaveBeenCalledTimes(2)
    f.dispose()
  })

  it('leaves native mouse reports, normal scrollback and modified gestures to xterm', () => {
    const f = fixture()
    f.term.modes.mouseTrackingMode = 'any'
    expect(f.wheel(-100).defaultPrevented).toBe(false)
    f.term.modes.mouseTrackingMode = 'none'
    f.term.buffer.active.baseY = 10
    expect(f.wheel(-100).defaultPrevented).toBe(false)
    f.term.buffer.active.baseY = 0
    for (const extra of [{ctrlKey:true}, {shiftKey:true}, {deltaX:200}]) {
      expect(f.wheel(-100, extra).defaultPrevented).toBe(false)
    }
    f.options.enabled = () => false
    expect(f.wheel(-100).defaultPrevented).toBe(false)
    expect(f.send).not.toHaveBeenCalled()
    f.dispose()
  })
})
