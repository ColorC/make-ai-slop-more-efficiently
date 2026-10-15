interface RecoveredTerminal {
  buffer: { active: { baseY: number } }
  modes: { mouseTrackingMode: string }
}

/** Old, still-running PTY hosts repaint cells without the guest's mouse modes.
 * Page keys keep those Codex sessions scrollable until their next native start.
 * New snapshots and ordinary scrollback keep xterm's native wheel handling.
 */
export function installTerminalWheelRecovery(
  root: HTMLElement,
  term: RecoveredTerminal,
  options: { enabled: () => boolean; send: (data: string) => void },
): () => void {
  let remainder = 0
  let lastEventAt = 0
  const onWheel = (event: WheelEvent) => {
    if (!options.enabled() || term.modes.mouseTrackingMode !== 'none'
      || term.buffer.active.baseY > 0 || event.ctrlKey || event.altKey
      || event.metaKey || event.shiftKey || !event.deltaY
      || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      remainder = 0
      return
    }
    event.preventDefault()
    event.stopPropagation()
    const delta = event.deltaY * (event.deltaMode === 1 ? 18 : event.deltaMode === 2 ? 120 : 1)
    const now = Date.now()
    if (now - lastEventAt > 250 || Math.sign(remainder) !== Math.sign(delta)) remainder = 0
    lastEventAt = now
    remainder += delta
    if (Math.abs(remainder) < 60) return
    options.send(remainder < 0 ? '\x1b[5~' : '\x1b[6~')
    remainder %= 60
  }
  root.addEventListener('wheel', onWheel, { capture: true, passive: false })
  return () => root.removeEventListener('wheel', onWheel, true)
}
