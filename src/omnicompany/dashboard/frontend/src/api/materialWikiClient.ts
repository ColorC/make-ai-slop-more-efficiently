export interface MaterialWikiSource {
  kind: string
  ref: string
  note?: string | null
}

export interface MaterialWikiRelationRef {
  material_id: string
  version: string
}

export interface MaterialWikiRelations {
  parent_version?: MaterialWikiRelationRef | null
  supersedes: MaterialWikiRelationRef[]
  derived_from: MaterialWikiRelationRef[]
  related: MaterialWikiRelationRef[]
}

export interface MaterialWikiVersion {
  version: string
  status: string
  body?: string
  created_at: string
  sources: MaterialWikiSource[]
  relations: MaterialWikiRelations
  author_session?: string | null
  prior_status?: string | null
}

export interface MaterialWikiEntry {
  material_id: string
  project_id: string
  profile_id: string
  title: string
  placement_path: string[]
  head_version: string
  current_version?: string | null
  versions?: MaterialWikiVersion[]
  links: { canonical_ref: string; api: string; studio: string }
  status?: string
  approved?: boolean
  terminal?: boolean
  // 画布提审卡的裁决状态(submission.state): unreviewed | seen | fully-passed |
  // not-fully-passed; 无对应画布卡时缺省/null。裁决状态存在画布卡上而非 wiki
  // 目录文件, 属列表投影的派生字段。
  review_state?: string | null
  preview?: string
  canonical_path?: string
  version_count?: number
  requested_material_id?: string
  resolved_alias?: { target_material_id: string; anchor?: string | null; reason?: string | null }
}

export interface MaterialWikiTaxonomyNode {
  id: string
  label: string
  allow_dynamic_children: boolean
  children: MaterialWikiTaxonomyNode[]
}

export interface MaterialWikiProfile {
  profile_id: string
  project_id: string
  source: { kind: string; ref: string }
  taxonomy: MaterialWikiTaxonomyNode[]
  statuses: Array<{ id: string; label: string; current: boolean; approved: boolean; terminal: boolean }>
  default_status: string
  locked_depth: number
  dynamic_from_depth?: number | null
  max_depth?: number | null
}

interface ListOptions {
  unapprovedOnly?: boolean
  includeTerminal?: boolean
  query?: string
  status?: string
  offset?: number
  limit?: number
}

export interface MaterialWikiListResponse {
  project_id: string
  count: number
  total: number
  offset: number
  limit: number
  has_more: boolean
  next_offset?: number | null
  materials: MaterialWikiEntry[]
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(payload?.detail || `Material Wiki ${response.status}`)
  }
  return response.json() as Promise<T>
}

function projectBase(project: string): string {
  return `/api/boss-sight/material-wiki/projects/${encodeURIComponent(project)}`
}

// 静态投影兜底：daemon 的 material-wiki 列表/正文在环境错位等情况下可能不完整或不可用，
// 此时从项目自带的静态索引补全树与正文。索引由 canonical 本体生成（同源投影，非第二事实源），
// 让标准审阅面板在 daemon 不稳时仍能显示完整目录与正文。仅对登记了静态索引的项目生效。
const STATIC_INDEX_SLUG: Record<string, string> = { walker: 'walker-game' }
const staticIndexCache = new Map<string, Promise<Map<string, MaterialWikiEntry>> | null>()
function loadStaticIndex(project: string): Promise<Map<string, MaterialWikiEntry>> | null {
  const slug = STATIC_INDEX_SLUG[project]
  if (!slug) return null
  if (!staticIndexCache.has(project)) {
    staticIndexCache.set(
      project,
      fetch(`/${slug}/assets/art/manifests/registry/wiki-index.json`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
        .then((j) => {
          const map = new Map<string, MaterialWikiEntry>()
          for (const e of ((j as { entries?: MaterialWikiEntry[] } | null)?.entries ?? [])) map.set(e.material_id, e)
          return map
        }),
    )
  }
  return staticIndexCache.get(project) ?? null
}

export const materialWikiApi = {
  subscribe(project: string, onChange: () => void) {
    if (typeof EventSource === 'undefined') return () => undefined
    const events = new EventSource(`${projectBase(project)}/events`)
    events.addEventListener('changed', onChange)
    return () => events.close()
  },
  profile(project: string) {
    return request<MaterialWikiProfile>(`${projectBase(project)}/profile`)
  },
  async list(project: string, options: ListOptions = {}) {
    const params = new URLSearchParams()
    if (options.unapprovedOnly) params.set('unapproved_only', 'true')
    if (options.includeTerminal) params.set('include_terminal', 'true')
    if (options.query) params.set('query', options.query)
    if (options.status) params.set('status', options.status)
    if (options.offset) params.set('offset', String(options.offset))
    if (options.limit) params.set('limit', String(options.limit))
    const suffix = params.size ? `?${params}` : ''
    try {
      // The HTTP API owns the canonical listing. A project snapshot is an optional,
      // rebuildable fallback and must never delay or override a successful response.
      return await request<MaterialWikiListResponse>(`${projectBase(project)}/materials${suffix}`)
    } catch (canonicalError) {
      const staticMap = await loadStaticIndex(project)
      if (!staticMap) throw canonicalError

      // Static fallback keeps the reader usable while the canonical service is
      // unavailable, but it does not become a second directory authority.
      const filterStatic = (arr: MaterialWikiEntry[]) => {
        let out = arr
        if (!options.includeTerminal) out = out.filter((e) => !e.terminal)
        if (options.query) {
          const q = options.query.toLowerCase()
          out = out.filter((e) => `${e.title} ${e.preview ?? ''}`.toLowerCase().includes(q))
        }
        return out
      }
      const materials = filterStatic([...staticMap.values()])
      return { project_id: project, count: materials.length, total: materials.length, offset: 0, limit: materials.length, has_more: false, materials }
    }
  },
  async get(project: string, materialId: string) {
    try {
      return await request<MaterialWikiEntry>(`${projectBase(project)}/materials/${encodeURIComponent(materialId)}`)
    } catch {
      const staticMap = await (loadStaticIndex(project) ?? Promise.resolve(null))
      const entry = staticMap?.get(materialId)
      if (entry) return entry
      throw new Error(`Material Wiki 未找到 ${materialId}`)
    }
  },
  create(project: string, body: Record<string, unknown>) {
    return request<Record<string, unknown>>(`${projectBase(project)}/materials`, {
      method: 'POST', body: JSON.stringify(body),
    })
  },
  revise(project: string, materialId: string, body: Record<string, unknown>) {
    return request<Record<string, unknown>>(
      `${projectBase(project)}/materials/${encodeURIComponent(materialId)}/revisions`,
      { method: 'POST', body: JSON.stringify(body) },
    )
  },
  link(project: string, materialId: string, body: Record<string, unknown>) {
    return request<Record<string, unknown>>(
      `${projectBase(project)}/materials/${encodeURIComponent(materialId)}/relations`,
      { method: 'POST', body: JSON.stringify(body) },
    )
  },
  archive(project: string, materialId: string, body: Record<string, unknown>) {
    return request<Record<string, unknown>>(
      `${projectBase(project)}/materials/${encodeURIComponent(materialId)}/archive`,
      { method: 'POST', body: JSON.stringify(body) },
    )
  },
  restore(project: string, materialId: string, body: Record<string, unknown>) {
    return request<Record<string, unknown>>(
      `${projectBase(project)}/materials/${encodeURIComponent(materialId)}/restore`,
      { method: 'POST', body: JSON.stringify(body) },
    )
  },
  batchArchive(project: string, items: Array<Record<string, unknown>>) {
    return request<{ ok: boolean; results: unknown[]; errors: Array<{ material_id: string; error: string }> }>(
      `${projectBase(project)}/batch-archive`,
      { method: 'POST', body: JSON.stringify({ items }) },
    )
  },
}
