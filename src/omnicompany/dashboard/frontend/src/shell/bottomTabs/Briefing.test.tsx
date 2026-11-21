import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import BriefingTab from './Briefing'

const briefing = {
  generated_at: '2026-05-31T00:00:00Z',
  severity: 'calm',
  headline: '系统平稳',
  all_green: true,
  summary: {
    plans_total: 0,
    plans_active: 0,
    plans_done: 0,
    review_total: 0,
    review_pending: 0,
    mandatory_unaccepted: 0,
    pushed_unread: 0,
    subagents_total: 0,
    subagents_running: 0,
    subagents_blocked: 0,
  },
  review: {
    available: true,
    total: 0,
    by_status: {},
    by_tier: {},
    mandatory_unaccepted: 0,
    pushed_unread: 0,
    recent: [],
  },
  plans: { total: 0, active: [] },
  subagents: { total: 0, running: [], blocked: [] },
  next_actions: [{ kind: 'calm', label: '当前没有必须立即处理的事项', priority: 'calm', target: null }],
  secretary: { title: '系统平稳', body: '没有阻断和待审推送, 可以继续推进下一阶段。' },
}

describe('BriefingTab', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the all-green secretary briefing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(briefing), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    render(<BriefingTab />)

    await waitFor(() => {
      expect(screen.getByTestId('briefing-headline').textContent).toContain('系统平稳')
    })
    expect(screen.getByTestId('briefing-all-green').textContent).toContain('当前没有待审材料')
    expect(screen.getByText('当前没有必须立即处理的事项')).toBeTruthy()
  })
})
