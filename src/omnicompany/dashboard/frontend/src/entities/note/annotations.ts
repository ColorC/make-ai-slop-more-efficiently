export interface Annotation {
  id: string
  anchor: { hash: string; snippet: string }
  comment: string
  author: string
  created_at: number
  resolved: boolean
}

/** FNV-1a 32-bit hash of normalized paragraph text. Stable across reloads + machines. */
export function paragraphHash(text: string): string {
  const norm = (text || '').toLowerCase().replace(/\s+/g, ' ').trim().slice(0, 200)
  let h = 0x811c9dc5
  for (let i = 0; i < norm.length; i++) {
    h ^= norm.charCodeAt(i)
    h = (h * 0x01000193) >>> 0
  }
  return h.toString(16)
}

export function snippetOf(text: string): string {
  return (text || '').replace(/\s+/g, ' ').trim().slice(0, 60)
}

/** Walk a mdast subtree, return concatenated text. */
export function extractText(node: any): string {
  if (!node) return ''
  if (typeof node.value === 'string') return node.value
  if (!node.children) return ''
  return node.children.map((c: any) => extractText(c)).join('')
}

export async function listAnnotations(noteId: string): Promise<Annotation[]> {
  const r = await fetch(`/api/notes/${noteId}/annotations`)
  if (!r.ok) throw new Error(`list: ${r.status}`)
  const d = await r.json() as { items: Annotation[] }
  return d.items || []
}

export async function createAnnotation(noteId: string, anchor: { hash: string; snippet: string }, comment: string): Promise<Annotation> {
  const r = await fetch(`/api/notes/${noteId}/annotations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ anchor, comment }),
  })
  if (!r.ok) throw new Error(`create: ${r.status}`)
  return r.json()
}

export async function deleteAnnotation(noteId: string, annId: string): Promise<void> {
  const r = await fetch(`/api/notes/${noteId}/annotations/${annId}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`delete: ${r.status}`)
}

export async function patchAnnotation(noteId: string, annId: string, body: Partial<Pick<Annotation, 'comment' | 'resolved'>>): Promise<Annotation> {
  const r = await fetch(`/api/notes/${noteId}/annotations/${annId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`patch: ${r.status}`)
  return r.json()
}
