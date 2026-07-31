import type { Entity } from '../types'
import type { EntityResolver } from '../registry'

export interface NoteEntity extends Entity {
  type: 'note'
  path: string
  size?: number
}

export interface NoteDetail extends NoteEntity {
  content: string
}

export interface NoteLinks {
  outgoing: string[]
  outgoing_unresolved: string[]
  backlinks: string[]
}

let _listCache: NoteEntity[] | null = null

export async function fetchList(): Promise<NoteEntity[]> {
  if (_listCache) return _listCache
  const r = await fetch('/api/notes')
  if (!r.ok) throw new Error(`list notes: ${r.status}`)
  const data = await r.json() as { items: any[] }
  _listCache = (data.items || []).map((n: any) => ({
    type: 'note' as const,
    id: n.id,
    title: n.title,
    path: n.path,
    size: n.size,
    tags: [n.id.split('/')[0] || 'root'],
  }))
  return _listCache!
}

export async function fetchDetail(id: string): Promise<NoteDetail> {
  const r = await fetch(`/api/notes/${id}`)
  if (!r.ok) throw new Error(`get note: ${r.status}`)
  const n = await r.json()
  return {
    type: 'note',
    id: n.id,
    title: n.title,
    path: n.path,
    content: n.content,
  }
}

export async function saveNote(id: string, content: string): Promise<{ ok: boolean; mtime: number; size: number }> {
  const r = await fetch(`/api/notes/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!r.ok) {
    const detail = await r.text()
    throw new Error(`save note: ${r.status} ${detail.slice(0, 200)}`)
  }
  return r.json()
}

export async function fetchLinks(id: string): Promise<NoteLinks> {
  const r = await fetch(`/api/notes/${id}/links`)
  if (!r.ok) throw new Error(`get links: ${r.status}`)
  const d = await r.json()
  return {
    outgoing: d.outgoing || [],
    outgoing_unresolved: d.outgoing_unresolved || [],
    backlinks: d.backlinks || [],
  }
}

export interface FullLinkGraph {
  out_links: Record<string, string[]>
  out_unresolved: Record<string, string[]>
  backlinks: Record<string, string[]>
  edges: [string, string][]
  node_count: number
  edge_count: number
}

export async function fetchFullGraph(): Promise<FullLinkGraph> {
  const r = await fetch('/api/notes/_links')
  if (!r.ok) throw new Error(`graph: ${r.status}`)
  return r.json()
}

export async function searchNotes(q: string): Promise<{ id: string; title: string; snippet: string }[]> {
  if (!q.trim()) return []
  const r = await fetch(`/api/notes/_search?q=${encodeURIComponent(q)}&limit=30`)
  if (!r.ok) throw new Error(`search: ${r.status}`)
  const d = await r.json()
  return d.items || []
}

export const noteResolver: EntityResolver<NoteEntity> = {
  type: 'note',
  async fetch(id) {
    const list = await fetchList()
    const found = list.find((n) => n.id === id)
    if (found) return found
    const d = await fetchDetail(id)
    return { type: 'note', id: d.id, title: d.title, path: d.path }
  },
  async list() { return fetchList() },
  async search(q) {
    const all = await fetchList()
    const ql = q.toLowerCase()
    return all.filter((n) => n.id.toLowerCase().includes(ql) || n.title.toLowerCase().includes(ql))
  },
}

export function invalidateNoteCache(): void {
  _listCache = null
}
