import { describe, expect, it, vi } from 'vitest'
import {
  computeTerminalContextEdit,
  installMobileTerminalInputGuard,
  looksLikeRepeatedInputContext,
} from './mobileTerminalInput'

function inputEvent(data: string | null, inputType = 'insertText', isComposing = false): InputEvent {
  return new InputEvent('input', {
    bubbles: true,
    cancelable: true,
    data,
    inputType,
    isComposing,
  })
}

function fixture() {
  const root = document.createElement('div')
  const textarea = document.createElement('textarea')
  root.appendChild(textarea)
  document.body.appendChild(root)
  const term = { input: vi.fn() }
  const dispose = installMobileTerminalInputGuard(textarea, term, { enabled: true, eventRoot: root })
  return { root, textarea, term, dispose }
}

describe('mobile terminal context edits', () => {
  it('sends only the appended suffix for accumulated keyboard context', () => {
    expect(computeTerminalContextEdit('abc', 'abc"')).toBe('"')
  })

  it('replaces a smart-quote rewrite without resending the old text', () => {
    expect(computeTerminalContextEdit('abc"', '“abc”')).toBe('\x7f\x7f\x7f\x7f“abc”')
    expect(looksLikeRepeatedInputContext('abc"', '“abc”')).toBe(true)
    expect(looksLikeRepeatedInputContext('abc"', 'unrelated voice sentence')).toBe(false)
  })
})

describe('mobile terminal input guard', () => {
  it('disables keyboard helpers and clears xterm textarea after its own timer', () => {
    vi.useFakeTimers()
    const { root, textarea, dispose } = fixture()
    const observed: string[] = []

    // CompositionHelper's keyCode=229 fallback schedules from keydown, before input.
    window.setTimeout(() => observed.push(textarea.value), 0)
    textarea.value = 'hello'
    textarea.dispatchEvent(inputEvent('hello'))
    expect(textarea.value).toBe('hello')
    vi.runAllTimers()

    expect(observed).toEqual(['hello'])
    expect(textarea.value).toBe('')
    expect(textarea.autocomplete).toBe('off')
    expect(textarea.autocapitalize).toBe('none')
    expect(textarea.spellcheck).toBe(false)
    dispose()
    root.remove()
    vi.useRealTimers()
  })

  it('intercepts restored full context and sends only the actual edit once', () => {
    vi.useFakeTimers()
    const { root, textarea, term, dispose } = fixture()
    const xtermInput = vi.fn()
    textarea.addEventListener('input', (event) => xtermInput((event as InputEvent).data))

    textarea.value = 'abc'
    textarea.dispatchEvent(inputEvent('abc'))
    vi.runAllTimers()
    expect(xtermInput).toHaveBeenCalledWith('abc')

    // OEM keyboard restores its prior context even though the hidden textarea was cleared.
    textarea.value = 'abc"'
    textarea.dispatchEvent(inputEvent('"'))

    expect(term.input).toHaveBeenCalledWith('"', true)
    expect(xtermInput).toHaveBeenCalledTimes(1)
    expect(textarea.value).toBe('')
    dispose()
    root.remove()
    vi.useRealTimers()
  })

  it('turns a whole-context smart quote rewrite into backspaces plus replacement', () => {
    vi.useFakeTimers()
    const { root, textarea, term, dispose } = fixture()

    textarea.value = 'abc"'
    textarea.dispatchEvent(inputEvent('abc"'))
    vi.runAllTimers()

    textarea.value = '“abc”'
    textarea.dispatchEvent(inputEvent('“abc”'))

    expect(term.input).toHaveBeenCalledWith('\x7f\x7f\x7f\x7f“abc”', true)
    dispose()
    root.remove()
    vi.useRealTimers()
  })

  it('does not clear or intercept an active composition', () => {
    vi.useFakeTimers()
    const { root, textarea, term, dispose } = fixture()
    const xtermInput = vi.fn()
    textarea.addEventListener('input', xtermInput)

    textarea.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '' }))
    textarea.value = '你好'
    textarea.dispatchEvent(inputEvent('你好', 'insertCompositionText', true))
    vi.runAllTimers()

    expect(textarea.value).toBe('你好')
    expect(term.input).not.toHaveBeenCalled()
    expect(xtermInput).toHaveBeenCalledTimes(1)

    textarea.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '你好' }))
    vi.runAllTimers()
    expect(textarea.value).toBe('')
    dispose()
    root.remove()
    vi.useRealTimers()
  })
})
