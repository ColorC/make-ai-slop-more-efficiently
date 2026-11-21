export const DOUBLE_CTRL_QUIET_MS = 340

/**
 * Match Overlay Shell's clean Ctrl-tap gesture: Ctrl must go down/up without
 * another key in between, so Ctrl+C/V/X never summons the palette.
 */
export function installDoubleCtrlShortcut(
  target: Window,
  onDoubleCtrl: () => void,
  quietMs = DOUBLE_CTRL_QUIET_MS,
): () => void {
  let ctrlHeld = false
  let clean = false
  let taps = 0
  let timer: number | undefined

  const reset = () => {
    ctrlHeld = false
    clean = false
    taps = 0
    if (timer !== undefined) target.clearTimeout(timer)
    timer = undefined
  }
  const settle = () => {
    timer = undefined
    const shouldOpen = taps >= 2
    taps = 0
    if (shouldOpen) onDoubleCtrl()
  }
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Control') {
      if (!event.repeat && !ctrlHeld) {
        ctrlHeld = true
        clean = !event.altKey && !event.metaKey && !event.shiftKey
      }
      return
    }
    if (ctrlHeld) clean = false
  }
  const onKeyUp = (event: KeyboardEvent) => {
    if (event.key !== 'Control') {
      if (ctrlHeld) clean = false
      return
    }
    const cleanTap = ctrlHeld && clean && !event.altKey && !event.metaKey && !event.shiftKey
    ctrlHeld = false
    clean = false
    if (!cleanTap) return
    taps += 1
    if (timer !== undefined) target.clearTimeout(timer)
    timer = target.setTimeout(settle, quietMs)
  }

  target.addEventListener('keydown', onKeyDown, true)
  target.addEventListener('keyup', onKeyUp, true)
  target.addEventListener('blur', reset)
  return () => {
    reset()
    target.removeEventListener('keydown', onKeyDown, true)
    target.removeEventListener('keyup', onKeyUp, true)
    target.removeEventListener('blur', reset)
  }
}
