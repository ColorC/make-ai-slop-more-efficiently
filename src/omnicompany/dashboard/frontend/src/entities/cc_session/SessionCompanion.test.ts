import { describe, expect, it } from 'vitest'
import type { ReviewReadbackItem } from '../../api/reviewstageClient'
import {
  companionSurfaceUrl,
  dedupeReviewReadback,
  DEFAULT_COMPANION_PAGE,
  requestSiblingSession,
} from './SessionCompanion'

function item(id: string, title: string): ReviewReadbackItem {
  return {
    id,
    title,
    status: 'pending',
    tier: 'important',
    kind: 'markdown',
    plan_id: null,
    reason: null,
    has_concrete_content: true,
    presentation: 'background',
    mentioned_in_conversation: true,
    mention_evidence: id,
    association: 'conversation_mention',
    created_at: '2026-07-30T00:00:00Z',
  }
}

describe('SessionCompanion helpers', () => {
  it('opens Multiagent as the first/default companion page', () => {
    expect(DEFAULT_COMPANION_PAGE).toBe('multiagent')
  })

  it('builds a copyable standalone surface URL for the CLI session', () => {
    expect(companionSurfaceUrl('862bb496844d41ee', 'https://dashboard.test:12443')).toBe(
      'https://dashboard.test:12443/?surface=session-companion&id=862bb496844d41ee',
    )
  })

  it('keeps the first authoritative material row for each material id', () => {
    const first = { ...item('material:a', '正式关联'), association: 'session_binding' as const }
    const duplicate = item('material:a', '对话重复提及')
    const second = item('material:b', '另一个材料')

    expect(dedupeReviewReadback([first, duplicate, second])).toEqual([first, second])
  })

  it('waits for the mounted editor to finish creating the sibling session', async () => {
    let release!: () => void
    const pending = new Promise<void>((resolve) => { release = resolve })
    let completed = false
    const request = requestSiblingSession(
      { newSession: () => pending },
      async () => { throw new Error('fallback must not run') },
      () => { throw new Error('fallback must not open') },
    ).then(() => { completed = true })

    await Promise.resolve()
    expect(completed).toBe(false)
    release()
    await request
    expect(completed).toBe(true)
  })

  it('creates and opens a fallback session when no editor controller is mounted', async () => {
    const created = {
      id: 'new-session',
      cmd: ['codex'],
      cwd: 'E:\\work',
      cols: 120,
      rows: 32,
      started_at: 1,
      alive: true,
    }
    let opened = ''
    await requestSiblingSession(undefined, async () => created, (session) => {
      opened = session.id
    })
    expect(opened).toBe('new-session')
  })

  it('propagates editor creation failures so the button can show them', async () => {
    await expect(requestSiblingSession(
      { newSession: async () => { throw new Error('spawn unavailable') } },
      async () => { throw new Error('fallback must not run') },
      () => undefined,
    )).rejects.toThrow('spawn unavailable')
  })
})
