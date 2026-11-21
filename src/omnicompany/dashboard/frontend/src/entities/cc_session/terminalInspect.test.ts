import { describe, expect, it } from 'vitest'
import { getTerminalInspectInput, registerTerminalInspect, listTerminalInspect, serializeTerminalInspect } from './terminalInspect'

describe('terminal inspect registry', () => {
  it('registers, snapshots, and unregisters a handle', () => {
    const unregister = registerTerminalInspect('sid-1', {
      sessionId: 'sid-1',
      provider: 'grok',
      visible: () => true,
      snapshot: () => serializeTerminalInspect('sid-1', 'grok', {
        cols: 80,
        rows: 24,
        buffer: {
          active: {
            length: 2,
            getLine: (index) => ({
              translateToString: () => (index === 0 ? 'hello' : 'world'),
            }),
          },
        },
      }, null, true),
    })
    const listed = listTerminalInspect()
    expect(listed).toHaveLength(1)
    expect(listed[0]).toMatchObject({
      sessionId: 'sid-1',
      provider: 'grok',
      visible: true,
      cols: 80,
      rows: 24,
      lines: ['hello', 'world'],
      canvasDataUrl: null,
    })
    unregister()
    expect(listTerminalInspect()).toEqual([])
  })

  it('returns only a visible input adapter and drops it on unregister', () => {
    const key = () => undefined
    const unregister = registerTerminalInspect('sid-input', {
      sessionId: 'sid-input', provider: 'claude_code', visible: () => true,
      snapshot: () => serializeTerminalInspect('sid-input', 'claude_code', null, null, true),
      input: { focus: () => undefined, ownsActiveElement: () => true, paste: () => undefined, key },
    })
    expect(getTerminalInspectInput()?.sessionId).toBe('sid-input')
    unregister()
    expect(getTerminalInspectInput()).toBeNull()
  })

  it('reports the visible rows when history is scrolled instead of only the buffer prefix', () => {
    const snapshot = serializeTerminalInspect('history', 'shell', {
      cols: 80, rows: 2,
      buffer: { active: {
        length: 500, viewportY: 450, baseY: 498,
        getLine: index => ({ translateToString: () => `line-${index}` }),
      } },
    }, null, true)
    expect(snapshot.lines).toHaveLength(400)
    expect(snapshot.viewportY).toBe(450)
    expect(snapshot.baseY).toBe(498)
    expect(snapshot.viewportLines).toEqual(['line-450', 'line-451'])
  })
})
