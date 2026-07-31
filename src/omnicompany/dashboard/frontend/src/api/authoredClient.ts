// 统一札记(authored notes) API 客户端 —— 对接 ccdaemon 的 /api/boss-sight/notes(经 dashboard 代理透传)。
const BASE = '/api/boss-sight/notes'

export interface NoteTarget {
  kind: string
  id?: string
  uri?: string
  material_id?: string
  plan_id?: string
  session_id?: string
  sub_kind?: string
  sub_id?: string
  title?: string
  url?: string
  selected_text?: string
  route?: string
  selector?: string
  new_object?: { kind?: string; title?: string; dest_dir?: string }
  [k: string]: unknown
}

export interface AuthoredNote {
  id: string
  content: string
  title?: string          // 可选显示名(重命名); 空则回退正文首行
  author: string
  target: NoteTarget
  uses: string[]
  feedback_status: string
  feedback_history?: Array<Record<string, unknown>>
  captures?: string[]
  project_id: string
  created_at: string
  updated_at?: string
  archived?: boolean
  json_path?: string
  extra?: Record<string, unknown>
}

export interface NoteFilter {
  target?: string       // target.kind
  target_id?: string
  project?: string
  uses?: string
  q?: string
  include_archived?: boolean
}

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail || '' } catch { /* */ }
    throw new Error(`${r.status} ${r.statusText} ${detail}`)
  }
  return r.json() as Promise<T>
}

export const authoredApi = {
  list: async (f: NoteFilter = {}): Promise<{ count: number; items: AuthoredNote[] }> => {
    const q = new URLSearchParams()
    if (f.target) q.set('target', f.target)
    if (f.target_id) q.set('target_id', f.target_id)
    if (f.project) q.set('project', f.project)
    if (f.uses) q.set('uses', f.uses)
    if (f.q) q.set('q', f.q)
    if (f.include_archived) q.set('include_archived', 'true')
    return jsonOrThrow(await fetch(`${BASE}?${q.toString()}`))
  },

  get: async (id: string): Promise<AuthoredNote> =>
    jsonOrThrow(await fetch(`${BASE}/${encodeURIComponent(id)}`)),

  byTarget: async (kind: string, id: string): Promise<{ count: number; items: AuthoredNote[] }> =>
    jsonOrThrow(await fetch(`${BASE}/by-target/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`)),

  create: async (body: {
    content: string; target?: NoteTarget; uses?: string[]; feedback_status?: string;
    project_id?: string; author?: string; captures?: string[]; extra?: Record<string, unknown>;
    image_data_url?: string  // 同文档自截图/拖入图: data:image/...;base64,... 后端解码落盘进 captures
  }): Promise<AuthoredNote> =>
    jsonOrThrow(await fetch(BASE, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })),

  update: async (id: string, body: {
    content?: string; uses?: string[]; feedback_status?: string; title?: string; by?: string
  }): Promise<AuthoredNote> =>
    jsonOrThrow(await fetch(`${BASE}/${encodeURIComponent(id)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })),

  remove: async (id: string): Promise<void> => {
    const r = await fetch(`${BASE}/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`delete: ${r.status}`)
  },

  // 草稿成品导出到项目目录(撰写态真源留中心 store, 成品落项目仓库)
  exportDraft: async (id: string, body: { dest_dir?: string; filename?: string; overwrite?: boolean } = {}):
    Promise<{ ok: boolean; exported_path: string; note_id: string }> =>
    jsonOrThrow(await fetch(`${BASE}/${encodeURIComponent(id)}/export-draft`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })),
}
