import { reviewstageApi } from './reviewstageClient'
import { afterEach, describe, expect, it, vi } from 'vitest'

describe('reviewstageApi v2-07 helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('posts comment feedback status updates', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      id: 'cmt_1',
      content: 'fix this',
      author: 'user',
      target: {},
      created_at: '2026-05-31T00:00:00Z',
      feedback_status: 'to_todo',
      feedback_history: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    await reviewstageApi.setCommentFeedback('mat_1', 'cmt_1', 'to_todo', 'created todo')

    expect(fetchMock).toHaveBeenCalledWith('/api/boss-sight/reviewstage/mat_1/comments/cmt_1/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'to_todo', by: 'user', note: 'created todo' }),
    })
  })

  it('posts batch verdict, tier, and delete requests', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
        ok: true,
        changed_count: 2,
        changed_ids: ['mat_1', 'mat_2'],
        not_found: [],
        skipped: [],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    await reviewstageApi.batchVerdict(['mat_1', 'mat_2'], 'accepted', 'batch')
    await reviewstageApi.batchTier(['mat_1'], 'mandatory')
    await reviewstageApi.batchDelete(['mat_2'], true)

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/boss-sight/reviewstage/batch_verdict', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ ids: ['mat_1', 'mat_2'], verdict: 'accepted', by: 'user', reason: 'batch' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/boss-sight/reviewstage/batch_tier', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ ids: ['mat_1'], new_tier: 'mandatory', by: 'user' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/boss-sight/reviewstage/batch_delete', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ ids: ['mat_2'], include_pending: true }),
    }))
  })

  it('fetches the decision-tree domain projection', async () => {
    const payload = {
      domains: [{
        domain: 'frontend_design',
        steps: [{
          name: '信息审计', order: 1, desc: '盘点信息架构', expected_kinds: ['markdown'],
          gate: { enforcer: 'frontend_design.ux_audit.enum_info' },
          samples: [], adopted_rulings: [{ id: 'DEC-2026-07-04-088', statement: '界面绝不堆说明文字', anchor: '不写 TBD/占位' }],
          next: '交互审计',
        }],
      }],
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))

    const res = await reviewstageApi.domainTree('walker')

    expect(fetchMock).toHaveBeenCalledWith('/api/boss-sight/reviewstage/domain-tree?project=walker')
    expect(res.domains[0].domain).toBe('frontend_design')
    expect(res.domains[0].steps[0].gate.enforcer).toBe('frontend_design.ux_audit.enum_info')
    expect(res.domains[0].steps[0].adopted_rulings[0].id).toBe('DEC-2026-07-04-088')
    expect(res.domains[0].steps[0].next).toBe('交互审计')
  })

  it('posts UI capture payloads', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      id: 'mat_capture',
      kind: 'markdown',
      tier: 'important',
      title: 'Codex debug handoff',
      status: 'pending',
      source_subagent_id: null,
      source_plan_id: 'cockpit/user-capture',
      file_relpath: null,
      inline_content: '# capture',
      annotations: [],
      comments: [],
      annotations_allowed: true,
      created_at: '2026-06-03T00:00:00Z',
      updated_at: '2026-06-03T00:00:00Z',
      history: [],
      pushed_to_user: false,
      pushed_reason: null,
      pushed_at: null,
      extra: {},
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    await reviewstageApi.capture({
      capture_kind: 'debug_start',
      title: 'Codex debug handoff',
      comment: 'point at the missing button',
      target: { selector: '[data-testid="x"]' },
      debug_allowed: true,
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/boss-sight/reviewstage/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        capture_kind: 'debug_start',
        title: 'Codex debug handoff',
        comment: 'point at the missing button',
        target: { selector: '[data-testid="x"]' },
        debug_allowed: true,
      }),
    })
  })
})
