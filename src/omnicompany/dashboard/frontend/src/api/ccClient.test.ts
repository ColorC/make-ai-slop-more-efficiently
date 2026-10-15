import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ccApi } from './ccClient'

describe('ccApi session list startup coalescing', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('shares concurrent catalogue reads but does not cache later polls', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ id: 'pty-1', status: 'alive' }], recoverable: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const results = await Promise.all([
      ccApi.list(),
      ccApi.list(),
      ccApi.list(),
      ccApi.list(),
    ])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(results.every((items) => items[0]?.id === 'pty-1')).toBe(true)

    await ccApi.list()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps explicitly remembered human tombstones visible but non-resumable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [],
        recoverable: [],
        remembered: [{ id: 'human-ended', origin: 'human', alive: false }],
      }),
    }))

    const sessions = await ccApi.list()

    expect(sessions).toEqual([
      expect.objectContaining({
        id: 'human-ended', status: 'ended', resumable: false,
      }),
    ])
  })
})
