export interface MobileTerminalInputTarget {
  input: (data: string, wasUserInput?: boolean) => void
}

interface MobileTerminalInputOptions {
  enabled?: boolean
  eventRoot?: HTMLElement
}

const DEL = '\x7f'

function codePoints(value: string): string[] {
  return Array.from(value)
}

/** Build the terminal edit needed to transform already-sent mobile text. */
export function computeTerminalContextEdit(previous: string, next: string): string {
  const before = codePoints(previous)
  const after = codePoints(next)
  let prefix = 0
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) {
    prefix++
  }
  return DEL.repeat(before.length - prefix) + after.slice(prefix).join('')
}

function longestCommonSubstringLength(left: string, right: string): number {
  const a = codePoints(left).slice(-160)
  const b = codePoints(right).slice(-160)
  let best = 0
  let previous = new Array<number>(b.length + 1).fill(0)
  for (let i = 1; i <= a.length; i++) {
    const current = new Array<number>(b.length + 1).fill(0)
    for (let j = 1; j <= b.length; j++) {
      if (a[i - 1] === b[j - 1]) {
        current[j] = previous[j - 1] + 1
        best = Math.max(best, current[j])
      }
    }
    previous = current
  }
  return best
}

/** Detect OEM keyboards that put the entire editable context in InputEvent.data. */
export function looksLikeRepeatedInputContext(previous: string, data: string): boolean {
  const beforeLength = codePoints(previous).length
  const dataLength = codePoints(data).length
  if (beforeLength < 3 || dataLength < beforeLength) return false
  const common = longestCommonSubstringLength(previous, data)
  return common >= Math.max(3, Math.ceil(beforeLength * 0.6))
}

function defaultEnabled(): boolean {
  if (typeof navigator !== 'undefined' && navigator.maxTouchPoints > 0) return true
  try { return typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches } catch { return false }
}

function removeLastCodePoint(value: string): string {
  const points = codePoints(value)
  points.pop()
  return points.join('')
}

/**
 * Keeps xterm's hidden textarea from accumulating the complete mobile editing
 * context. Some Android/OEM keyboards rewrite that context when auto-pairing
 * quotes; xterm 6 then treats the whole textarea as fresh input.
 */
export function installMobileTerminalInputGuard(
  textarea: HTMLTextAreaElement,
  term: MobileTerminalInputTarget,
  options: MobileTerminalInputOptions = {},
): () => void {
  textarea.autocomplete = 'off'
  textarea.autocapitalize = 'none'
  textarea.spellcheck = false
  textarea.setAttribute('autocorrect', 'off')
  textarea.setAttribute('data-gramm', 'false')
  textarea.setAttribute('data-gramm_editor', 'false')

  if (!(options.enabled ?? defaultEnabled())) return () => undefined

  const inputEventRoot = options.eventRoot || textarea
  let composing = false
  let committed = ''
  let clearTimer: number | null = null

  const cancelClear = () => {
    if (clearTimer === null) return
    window.clearTimeout(clearTimer)
    clearTimer = null
  }

  const scheduleClear = () => {
    cancelClear()
    clearTimer = window.setTimeout(() => {
      clearTimer = null
      if (!composing) textarea.value = ''
    }, 0)
  }

  // Start with no stale editing context from an earlier focus/session.
  textarea.value = ''

  const onCompositionStart = () => {
    composing = true
    cancelClear()
  }

  const onCompositionEnd = () => {
    composing = false
    // xterm's CompositionHelper also uses a zero-delay timer. Its listener was
    // installed first by term.open(), so this reset runs after xterm consumes it.
    const value = textarea.value
    if (value) committed = value
    scheduleClear()
  }

  const onInput = (event: Event) => {
    const input = event as InputEvent
    if (composing || input.isComposing) return

    const data = input.data || ''
    const nextValue = textarea.value
    const isReplacement = input.inputType === 'insertReplacementText'
    const restoredContext = Boolean(
      committed
      && data
      && nextValue !== data
      && nextValue.length >= data.length,
    )
    const repeatedData = Boolean(committed && data && looksLikeRepeatedInputContext(committed, data))

    if (committed && data && (isReplacement || restoredContext || repeatedData)) {
      const nextContext = restoredContext ? nextValue : data
      const edit = computeTerminalContextEdit(committed, nextContext)

      // Capture phase prevents xterm's input handler from also sending ev.data.
      // Clearing immediately also neutralizes CompositionHelper's keyCode=229 timer.
      event.preventDefault()
      event.stopImmediatePropagation()
      textarea.value = ''
      committed = nextContext
      if (edit) term.input(edit, true)
      scheduleClear()
      return
    }

    if (input.inputType.startsWith('delete')) {
      committed = nextValue || removeLastCodePoint(committed)
    } else if (input.inputType === 'insertLineBreak' || input.inputType === 'insertParagraph') {
      committed = ''
    } else if (data) {
      committed = nextValue && nextValue !== data ? nextValue : committed + data
    }
    scheduleClear()
  }

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === 'Escape' || event.ctrlKey || event.metaKey) {
      committed = ''
    }
  }

  const onBlur = () => {
    composing = false
    committed = ''
    cancelClear()
    textarea.value = ''
  }

  textarea.addEventListener('compositionstart', onCompositionStart, true)
  textarea.addEventListener('compositionend', onCompositionEnd)
  inputEventRoot.addEventListener('input', onInput, true)
  textarea.addEventListener('keydown', onKeyDown, true)
  textarea.addEventListener('blur', onBlur, true)

  return () => {
    cancelClear()
    textarea.removeEventListener('compositionstart', onCompositionStart, true)
    textarea.removeEventListener('compositionend', onCompositionEnd)
    inputEventRoot.removeEventListener('input', onInput, true)
    textarea.removeEventListener('keydown', onKeyDown, true)
    textarea.removeEventListener('blur', onBlur, true)
  }
}
