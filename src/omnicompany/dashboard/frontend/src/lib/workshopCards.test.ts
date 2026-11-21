import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchCardPreviews, workshopMarkdownUrl } from './workshopCards'

describe('workshopMarkdownUrl', () => {
  it('routes canvas assets through the /bside proxy with the project', () => {
    expect(workshopMarkdownUrl('../lofa/overlay/asset/abc.png', 'hunao-games'))
      .toBe('/bside/lofa/overlay/asset/abc.png?project=hunao-games')
  })

  it('keeps absolute links untouched', () => {
    expect(workshopMarkdownUrl('https://workshop.example.com/bside/canvas?canvas=x', 'p'))
      .toBe('https://workshop.example.com/bside/canvas?canvas=x')
  })
})

describe('fetchCardPreviews', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('returns markdown for text cards and null for embedded kinds', async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => (String(url).includes('/api/canvases/')
        ? { document: { title: 'T', cards: [
          { id: 'a', kind: 'text', adapter: 'text', title: 'A', source: { editableId: 'ta' } },
          { id: 'v', kind: 'material', adapter: 'video', title: 'V' },
        ] } }
        : { ok: true, result: { items: { ta: { editableId: 'ta', content: 'body' } } } }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const cards = await fetchCardPreviews('p', 'canvas-x', ['a', 'v', 'gone'])
    expect(cards.map((c) => [c.id, c.markdown])).toEqual([['a', 'body'], ['v', null]])
    const canvasCall = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect((canvasCall[1].headers as Record<string, string>)['x-workshop-project']).toBe('p')
  })

  it('resolves a card id truncated by terminal wrapping when the prefix is unique', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => (String(url).includes('/api/canvases/')
        ? { document: { title: 'T', cards: [{ id: 'wsh-ac236ab8-157f', kind: 'text', adapter: 'text', title: 'Full', source: { editableId: 'tf' } }] } }
        : { ok: true, result: { items: { tf: { editableId: 'tf', content: 'x' } } } }),
    })))
    const cards = await fetchCardPreviews('p', 'canvas-y', ['wsh-ac236'])
    expect(cards.map((c) => c.id)).toEqual(['wsh-ac236ab8-157f'])
  })
})
