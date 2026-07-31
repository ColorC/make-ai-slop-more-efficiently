import { afterEach, describe, expect, it, vi } from 'vitest'
import { bossSightApi } from './bossSightClient'

describe('bossSightApi v2-08 helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('posts dual-control updates with actor and reason', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      key: 'controller.auto_wake',
      value: false,
      updated_by: 'controller',
      updated_at: '2026-05-31T00:00:00Z',
      reason: 'pause',
      history: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    await bossSightApi.setControl('controller.auto_wake', false, 'controller', 'pause')

    expect(fetchMock).toHaveBeenCalledWith('/api/boss-sight/control/controller.auto_wake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: false, actor: 'controller', reason: 'pause' }),
    })
  })

  it('posts observability settings and events', async () => {
    const responseBody = {
      dimensions: { click: true, selection: false, toggle_change: true, view_dwell: true },
      updated_by: 'human',
      updated_at: '2026-05-31T00:00:00Z',
      reason: 'privacy',
      history: [],
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })))

    await bossSightApi.setObservabilitySettings({ selection: false }, 'human', 'privacy')
    await bossSightApi.recordObservation({ dimension: 'click', surface: 'settings', target: 'save', value: true })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/boss-sight/observability/settings', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ dimensions: { selection: false }, actor: 'human', reason: 'privacy' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/boss-sight/observability/event', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        dimension: 'click',
        surface: 'settings',
        target: 'save',
        value: true,
        meta: {},
        actor: 'human',
      }),
    }))
  })

  it('writes permanent allow through user prefs only', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      id: 'allow_1',
      scope: 'user',
      tool: 'Bash',
      pattern: 'npm run build',
      reason: 'local build',
      actor: 'human',
      created_at: '2026-05-31T00:00:00Z',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    await bossSightApi.addPermanentAllow({ tool: 'Bash', pattern: 'npm run build', reason: 'local build' })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/boss-sight/user-prefs/permanent_allow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: 'user',
        tool: 'Bash',
        pattern: 'npm run build',
        reason: 'local build',
        actor: 'human',
      }),
    })
  })
})
