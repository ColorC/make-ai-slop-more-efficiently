import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  buildTerminalInputFrame,
  createSettledResizeCommit,
  parseServerGeometry,
  type TerminalDimensions,
} from './terminalResize'

describe('createSettledResizeCommit', () => {
  afterEach(() => vi.useRealTimers())

  it('commits once after a resize burst and skips an identical grid', () => {
    vi.useFakeTimers()
    let dimensions: TerminalDimensions = { cols: 132, rows: 107 }
    const commits: TerminalDimensions[] = []
    const settled = createSettledResizeCommit(() => dimensions, (next) => commits.push(next), 100)

    settled.schedule()
    dimensions = { cols: 126, rows: 107 }
    settled.schedule()
    dimensions = { cols: 119, rows: 107 }
    settled.schedule()
    vi.advanceTimersByTime(99)
    expect(commits).toEqual([])
    vi.advanceTimersByTime(1)
    expect(commits).toEqual([{ cols: 119, rows: 107 }])

    settled.schedule()
    vi.advanceTimersByTime(100)
    expect(commits).toHaveLength(1)
  })

  it('cancels a pending commit when the terminal unmounts', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const settled = createSettledResizeCommit(() => ({ cols: 80, rows: 24 }), commit, 100)
    settled.schedule()
    settled.dispose()
    vi.runAllTimers()
    expect(commit).not.toHaveBeenCalled()
  })

  it('recommits identical geometry after a websocket reconnect', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const settled = createSettledResizeCommit(() => ({ cols: 119, rows: 40 }), commit, 100)

    settled.schedule()
    vi.advanceTimersByTime(100)
    settled.schedule()
    vi.advanceTimersByTime(100)
    expect(commit).toHaveBeenCalledTimes(1)

    settled.resetCommitted()
    settled.schedule()
    vi.advanceTimersByTime(100)
    expect(commit).toHaveBeenCalledTimes(2)
  })
})

describe('server-authoritative grid', () => {
  it('parses a geometry frame and rejects malformed grids', () => {
    expect(parseServerGeometry({ type: 'geometry', cols: 110, rows: 39 })).toEqual({ cols: 110, rows: 39 })
    expect(parseServerGeometry({ type: 'geometry', cols: 1, rows: 39 })).toBeNull()
    expect(parseServerGeometry({ type: 'geometry', cols: 110.5, rows: 39 })).toBeNull()
    expect(parseServerGeometry({ type: 'geometry' })).toBeNull()
    expect(parseServerGeometry(null)).toBeNull()
  })

  it('carries the viewer grid on a keystroke only when taking over sizing', () => {
    expect(JSON.parse(buildTerminalInputFrame('x'))).toEqual({ type: 'input', data: 'x' })
    expect(JSON.parse(buildTerminalInputFrame('x', { cols: 45, rows: 30 }))).toEqual({
      type: 'input', data: 'x', cols: 45, rows: 30,
    })
  })
})
