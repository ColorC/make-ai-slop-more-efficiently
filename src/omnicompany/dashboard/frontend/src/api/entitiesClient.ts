const BASE = '/api/boss-sight'

export interface EntityOpenRef {
  type?: string
  id?: string
  facet?: string
  url?: string
  command?: string
}

export interface EntitySearchResult {
  uri: string
  kind: string
  id: string
  display: string
  short_name: string
  title: string
  snippet: string
  source: string
  path?: string | null
  open_ref: EntityOpenRef
  score?: number
}

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail || '' } catch { /* noop */ }
    throw new Error(`${r.status} ${r.statusText} ${detail}`)
  }
  return r.json() as Promise<T>
}

export const entitiesApi = {
  search: async (q: string, limit = 50, kind?: string): Promise<EntitySearchResult[]> => {
    const params = new URLSearchParams()
    params.set('q', q)
    params.set('limit', String(limit))
    if (kind) params.set('kind', kind)
    const r = await fetch(`${BASE}/search?${params.toString()}`)
    const d = await jsonOrThrow<{ items: EntitySearchResult[] }>(r)
    return d.items || []
  },

  suggest: async (q: string, limit = 12): Promise<EntitySearchResult[]> => {
    const params = new URLSearchParams()
    params.set('q', q)
    params.set('limit', String(limit))
    const r = await fetch(`${BASE}/entities?${params.toString()}`)
    const d = await jsonOrThrow<{ items: EntitySearchResult[] }>(r)
    return d.items || []
  },

  resolve: async (uri: string): Promise<EntitySearchResult> => {
    const params = new URLSearchParams({ uri })
    const r = await fetch(`${BASE}/entities/resolve?${params.toString()}`)
    return jsonOrThrow<EntitySearchResult>(r)
  },
}
