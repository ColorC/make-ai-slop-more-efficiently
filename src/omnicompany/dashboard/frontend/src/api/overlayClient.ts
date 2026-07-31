export interface OverlayFileHit {
  kind: 'app' | 'folder' | 'exe' | 'file' | string
  name: string
  path: string
  score: number
  tags: string[]
  pinned: boolean
  open_token: string
}

export interface OverlayFileEntry {
  kind: 'folder' | 'file' | string
  name: string
  path: string
  size: number
  modified_at: string
  mime: string
  open_token: string
}

export interface OverlayFileDetail extends OverlayFileEntry {
  preview: 'directory' | 'text' | 'image' | 'pdf' | 'audio' | 'video' | 'none' | string
  truncated?: boolean
  content?: string
  encoding?: string
  items?: OverlayFileEntry[]
}

interface OverlayBridgeResponse {
  ok?: boolean
  result?: unknown
  error?: string
}

/** Search the Overlay Shell index on the Dashboard host through its same-origin bridge. */
export async function searchOverlayFiles(
  query: string,
  limit = 40,
  signal?: AbortSignal,
): Promise<OverlayFileHit[]> {
  const q = query.trim()
  if (!q) return []
  const response = await fetch('/lofa/overlay/invoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      cmd: 'search',
      args: { query: q, limit: Math.max(1, Math.min(Math.trunc(limit), 100)) },
    }),
    signal,
  })
  const body = await response.json().catch(() => null) as OverlayBridgeResponse | null
  if (!response.ok || !body || body.ok === false) {
    throw new Error(body?.error || `Overlay Shell 文件搜索失败 (${response.status})`)
  }
  if (!Array.isArray(body.result)) {
    throw new Error('Overlay Shell 文件搜索返回格式无效')
  }
  return body.result.filter((hit): hit is OverlayFileHit =>
    !!hit && typeof hit === 'object'
    && typeof (hit as OverlayFileHit).name === 'string'
    && typeof (hit as OverlayFileHit).path === 'string'
    && typeof (hit as OverlayFileHit).open_token === 'string')
}

/** Resolve a signed search result into read-only metadata/content for the Dashboard viewer. */
export async function inspectOverlayFile(
  token: string,
  signal?: AbortSignal,
): Promise<OverlayFileDetail> {
  const response = await fetch('/lofa/overlay/invoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: 'file_inspect', args: { token } }),
    signal,
  })
  const body = await response.json().catch(() => null) as OverlayBridgeResponse | null
  if (!response.ok || !body || body.ok === false || !body.result || typeof body.result !== 'object') {
    throw new Error(body?.error || `网页文件打开失败 (${response.status})`)
  }
  const detail = body.result as OverlayFileDetail
  if (!detail.name || !detail.path || !detail.open_token || !detail.preview) {
    throw new Error('网页文件打开返回格式无效')
  }
  return detail
}

/** Same-origin streaming URL for image/PDF/audio/video previews. */
export function overlayFileContentUrl(token: string): string {
  return `/lofa/overlay/file/${encodeURIComponent(token)}`
}
