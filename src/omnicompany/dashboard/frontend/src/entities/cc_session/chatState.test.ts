import { describe, expect, it } from 'vitest'
import { applyFrame, createChatState, markUserSent } from './chatState'

describe('native dashboard chat state', () => {
  it('rebuilds a reconnect snapshot from normalized messages', () => {
    const state = createChatState('chat-1')
    markUserSent(state, 'stale')

    applyFrame(state, {
      kind: 'snapshot',
      messages: [
        { kind: 'text', id: 'u1', role: 'user', content: 'hello' },
        { kind: 'tool_use', id: 't1', toolId: 'tool-1', toolName: 'Read', input: { path: 'README.md' } },
        { kind: 'tool_result', id: 'r1', toolId: 'tool-1', content: 'ok' },
        { kind: 'text', id: 'a1', role: 'assistant', content: 'done' },
      ],
    })

    expect(state.items.map((item) => item.type)).toEqual(['user', 'tool', 'assistant'])
    const tool = state.items[1]
    expect(tool.type === 'tool' && tool.done).toBe(true)
    expect(tool.type === 'tool' && tool.result).toBe('ok')
    expect(state.running).toBe(false)
  })

  it('merges stream deltas and closes the turn on complete', () => {
    const state = createChatState('chat-2')
    markUserSent(state, 'go')
    applyFrame(state, { kind: 'stream_delta', content: 'hel' })
    applyFrame(state, { kind: 'stream_delta', content: 'lo' })
    applyFrame(state, { kind: 'complete', sessionId: 'chat-2' })

    const answer = state.items.find((item) => item.type === 'assistant')
    expect(answer?.type === 'assistant' && answer.text).toBe('hello')
    expect(answer?.type === 'assistant' && answer.streaming).toBe(false)
    expect(state.running).toBe(false)
  })

  it('treats a Claude interrupt as a normal stopped turn', () => {
    const state = createChatState('chat-3')
    markUserSent(state, 'long task')
    applyFrame(state, { kind: 'error', code: 'interrupted', message: 'user interrupted' })

    expect(state.aborted).toBe(true)
    expect(state.running).toBe(false)
    expect(state.items[state.items.length - 1]).toMatchObject({ type: 'system', level: 'info', text: '（已中断）' })
  })
})
