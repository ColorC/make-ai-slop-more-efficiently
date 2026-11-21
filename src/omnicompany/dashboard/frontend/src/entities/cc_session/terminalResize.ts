export interface TerminalDimensions {
  cols: number
  rows: number
}

/**
 * PTY grid from a server `geometry` frame. Only one viewer sizes the PTY; the
 * others must render at this grid, or full-width TUI rules wrap and smear.
 */
export function parseServerGeometry(msg: unknown): TerminalDimensions | null {
  if (!msg || typeof msg !== 'object') return null
  const cols = Number((msg as { cols?: unknown }).cols)
  const rows = Number((msg as { rows?: unknown }).rows)
  if (!Number.isInteger(cols) || !Number.isInteger(rows) || cols < 2 || rows < 1) return null
  return { cols, rows }
}

/**
 * Keystroke frame. `takeover` carries this viewer's own fit grid while it is
 * rendering another viewer's grid: typing here claims PTY sizing.
 */
export function buildTerminalInputFrame(data: string, takeover: TerminalDimensions | null = null): string {
  return JSON.stringify(takeover
    ? { type: 'input', data, cols: takeover.cols, rows: takeover.rows }
    : { type: 'input', data })
}

/**
 * Commit only the final terminal geometry after a burst of layout changes.
 * The first valid observation is always committed; later identical grids are skipped.
 */
export function createSettledResizeCommit(
  read: () => TerminalDimensions | null,
  commit: (dimensions: TerminalDimensions) => void,
  delayMs = 100,
) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let lastCommitted = ''

  const flush = () => {
    timer = null
    try {
      const dimensions = read()
      if (!dimensions || dimensions.cols < 2 || dimensions.rows < 1) return
      const key = `${dimensions.cols}x${dimensions.rows}`
      if (key === lastCommitted) return
      lastCommitted = key
      commit(dimensions)
    } catch { /* terminal may be disposing while a resize callback is queued */ }
  }

  return {
    schedule() {
      if (timer !== null) clearTimeout(timer)
      timer = setTimeout(flush, delayMs)
    },
    resetCommitted() {
      lastCommitted = ''
    },
    dispose() {
      if (timer !== null) clearTimeout(timer)
      timer = null
    },
  }
}
