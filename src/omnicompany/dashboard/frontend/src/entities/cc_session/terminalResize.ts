export interface TerminalDimensions {
  cols: number
  rows: number
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
    dispose() {
      if (timer !== null) clearTimeout(timer)
      timer = null
    },
  }
}
