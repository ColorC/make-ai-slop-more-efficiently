import { afterEach, describe, expect, it, vi } from 'vitest'
import { materialWikiApi, type MaterialWikiListResponse } from './materialWikiClient'

const canonicalListing: MaterialWikiListResponse = {
  project_id: 'walker',
  count: 1,
  total: 1,
  offset: 0,
  limit: 100,
  has_more: false,
  materials: [{
    material_id: 'walker.character.empty-echo',
    project_id: 'walker',
    profile_id: 'walker.materials',
    title: 'empty-echo',
    placement_path: ['characters', 'enemy'],
    head_version: 'v1',
    links: { canonical_ref: 'project:walker/materials/empty-echo.md', api: '', studio: '' },
  }],
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('materialWikiApi.list authority boundary', () => {
  it('returns the canonical API listing without requesting the static projection', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(canonicalListing), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(materialWikiApi.list('walker', { limit: 100 })).resolves.toEqual(canonicalListing)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/boss-sight/material-wiki/projects/walker/materials?limit=100')
  })

  it('loads the static projection only after the canonical API fails', async () => {
    const fallbackEntry = { ...canonicalListing.materials[0], title: 'fallback entry', terminal: false }
    const fetchMock = vi.fn(async (url: string | URL | Request) => {
      if (String(url).includes('/api/boss-sight/material-wiki/')) {
        return new Response(JSON.stringify({ detail: 'daemon unavailable' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ entries: [fallbackEntry] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await materialWikiApi.list('walker')

    expect(result.materials).toEqual([fallbackEntry])
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/walker-game/assets/art/manifests/registry/wiki-index.json')
  })
})
