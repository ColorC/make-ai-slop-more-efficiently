/** Live terminal handles for the Dashboard inspect channel. */

export interface TerminalInspectSnapshot {
  sessionId: string
  provider: string
  visible: boolean
  cols: number
  rows: number
  lines: string[]
  canvasDataUrl: string | null
}

export interface TerminalInspectInputAdapter {
  /** Focus the visible xterm input, or throw when the terminal is unavailable. */
  focus: () => void
  /** Whether the active DOM element belongs to this terminal. */
  ownsActiveElement?: (element: Element | null) => boolean
  /** Send text through the terminal's normal input path. */
  paste: (text: string) => void
  /** Map and send a named key through the terminal's normal input path. */
  key: (key: string) => void
}

export interface TerminalInspectHandle {
  sessionId: string
  provider: string
  visible: () => boolean
  snapshot: () => TerminalInspectSnapshot
  input?: TerminalInspectInputAdapter
}

const handles = new Map<string, TerminalInspectHandle>()

export function registerTerminalInspect(
  id: string,
  handle: TerminalInspectHandle,
): () => void {
  handles.set(id, handle)
  return () => {
    if (handles.get(id) === handle) handles.delete(id)
  }
}

export function listTerminalInspect(): TerminalInspectSnapshot[] {
  return [...handles.values()].map((handle) => handle.snapshot())
}

/** Return the visible terminal input adapter that owns the current focus. */
export function getTerminalInspectInput(activeElement: Element | null = document.activeElement): (TerminalInspectInputAdapter & { sessionId: string }) | null {
  const mounted = [...handles.values()].reverse().find((handle) => {
    try {
      if (!handle.input || !handle.visible()) return false
      if (activeElement == null) return false
      return Boolean(handle.input.ownsActiveElement?.(activeElement))
    } catch {
      return false
    }
  })
  return mounted?.input ? { ...mounted.input, sessionId: mounted.sessionId } : null
}

export function serializeTerminalInspect(
  sessionId: string,
  provider: unknown,
  term: {
    cols?: number
    rows?: number
    buffer?: { active?: { length: number; getLine: (index: number) => { translateToString: (trim?: boolean) => string } | undefined } }
  } | null,
  container: HTMLElement | null,
  visible: boolean,
): TerminalInspectSnapshot {
  const buffer = term?.buffer?.active
  const lines: string[] = []
  if (buffer && typeof buffer.length === 'number') {
    const count = Math.min(buffer.length, 400)
    for (let index = 0; index < count; index += 1) {
      lines.push(buffer.getLine(index)?.translateToString(true) ?? '')
    }
  }
  let canvasDataUrl: string | null = null
  const canvas = container?.querySelector('canvas')
  if (canvas instanceof HTMLCanvasElement) {
    try {
      canvasDataUrl = canvas.toDataURL('image/png')
    } catch {
      canvasDataUrl = null
    }
  }
  return {
    sessionId,
    provider: String(provider || ''),
    visible,
    cols: Number(term?.cols) || 0,
    rows: Number(term?.rows) || 0,
    lines,
    canvasDataUrl,
  }
}
