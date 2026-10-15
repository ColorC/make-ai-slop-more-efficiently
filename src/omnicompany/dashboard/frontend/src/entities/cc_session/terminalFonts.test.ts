import { describe, expect, it, vi } from 'vitest'
import { whenTerminalFontsReady, type TerminalFontSources } from './terminalFonts'

function deferred() {
  let resolve!: (v: unknown) => void
  const promise = new Promise<unknown>((r) => { resolve = r })
  return { promise, resolve }
}

describe('whenTerminalFontsReady', () => {
  it('resolves once the primary family loads', async () => {
    const loaded = deferred()
    const ready = deferred()
    const sources: TerminalFontSources = {
      load: vi.fn(() => loaded.promise as Promise<unknown[]>),
      ready: ready.promise,
    }
    let settled = false
    const wait = whenTerminalFontsReady("'Sarasa Term SC', Consolas, monospace", 1000, sources).then(() => { settled = true })
    expect(sources.load).toHaveBeenCalledWith('15px "Sarasa Term SC"')
    loaded.resolve([])
    ready.resolve(null)
    await wait
    expect(settled).toBe(true)
  })

  it('gives up after the timeout instead of waiting forever', async () => {
    const loaded = deferred()
    const ready = deferred()
    const sources: TerminalFontSources = {
      load: vi.fn(() => loaded.promise as Promise<unknown[]>),
      ready: ready.promise,
    }
    await whenTerminalFontsReady('Consolas, monospace', 60, sources)
    expect(sources.load).toHaveBeenCalledTimes(1)
  })

  it('resolves immediately when document.fonts is unavailable', async () => {
    await whenTerminalFontsReady('Consolas, monospace', 1000, null)
  })

  it('tolerates load() rejecting (fallback font path)', async () => {
    const ready = deferred()
    const sources: TerminalFontSources = {
      load: vi.fn(() => Promise.reject(new Error('unsupported'))),
      ready: ready.promise,
    }
    ready.resolve(null)
    await whenTerminalFontsReady('Fancy Font, monospace', 1000, sources)
  })
})
