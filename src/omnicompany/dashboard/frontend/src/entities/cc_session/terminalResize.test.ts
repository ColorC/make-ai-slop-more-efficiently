import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSettledResizeCommit, type TerminalDimensions } from './terminalResize'

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
})
