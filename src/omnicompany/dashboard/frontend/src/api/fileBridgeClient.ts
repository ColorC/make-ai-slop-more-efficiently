const BASE = '/lofa/file-bridge'
const BRIDGE_HEADERS = { 'X-Omni-File-Bridge': '1' }

export interface FileBridgeRoot {
  id: string
  label: string
  path: string
  writable: boolean
  available: boolean
}

export interface FileBridgeEntry {
  name: string
  path: string
  relative_path: string
  kind: 'folder' | 'file'
  size: number | null
  modified_at: string
  mime: string
  preview: 'directory' | 'text' | 'image' | 'pdf' | 'audio' | 'video' | 'none'
  accessible: boolean
  content_token?: string
  content?: string
  encoding?: string
  truncated?: boolean
}

export interface FileBridgeListing {
  root_id: string
  root_path: string
  path: string
  relative_path: string
  items: FileBridgeEntry[]
  truncated: boolean
}

export interface FileBridgeUploadResult {
  batch_id: string
  uploaded_at: string
  batch_path: string
  root_id: 'staging'
  total_bytes: number
  items: FileBridgeEntry[]
}

export interface FileBridgeHistory {
  items: FileBridgeUploadResult[]
  history_path: string
  staging_path: string
  query_command: string
}

async function responseJson<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.detail || body?.error || `${fallback} (${response.status})`)
  }
  return body as T
}

export const fileBridgeApi = {
  async roots(signal?: AbortSignal): Promise<FileBridgeRoot[]> {
    const response = await fetch(`${BASE}/roots`, { headers: BRIDGE_HEADERS, signal })
    const body = await responseJson<{ items: FileBridgeRoot[] }>(response, '读取文件桥根目录失败')
    return body.items || []
  },

  async browse(rootId: string, path = '', signal?: AbortSignal): Promise<FileBridgeListing> {
    const response = await fetch(`${BASE}/browse`, {
      method: 'POST',
      headers: { ...BRIDGE_HEADERS, 'Content-Type': 'application/json' },
      body: JSON.stringify({ root_id: rootId, path }),
      signal,
    })
    return responseJson<FileBridgeListing>(response, '读取目录失败')
  },

  async inspect(rootId: string, path: string, signal?: AbortSignal): Promise<FileBridgeEntry> {
    const response = await fetch(`${BASE}/inspect`, {
      method: 'POST',
      headers: { ...BRIDGE_HEADERS, 'Content-Type': 'application/json' },
      body: JSON.stringify({ root_id: rootId, path }),
      signal,
    })
    return responseJson<FileBridgeEntry>(response, '读取文件失败')
  },

  async upload(files: File[], signal?: AbortSignal): Promise<FileBridgeUploadResult> {
    const form = new FormData()
    for (const file of files) form.append('files', file, file.name)
    const response = await fetch(`${BASE}/upload`, {
      method: 'POST',
      headers: BRIDGE_HEADERS,
      body: form,
      signal,
    })
    return responseJson<FileBridgeUploadResult>(response, '上传失败')
  },

  async history(limit = 30, signal?: AbortSignal): Promise<FileBridgeHistory> {
    const query = new URLSearchParams({ limit: String(limit) })
    const response = await fetch(`${BASE}/history?${query.toString()}`, {
      headers: BRIDGE_HEADERS,
      signal,
    })
    return responseJson<FileBridgeHistory>(response, '读取上传记录失败')
  },
}

export function fileBridgeContentUrl(token: string, inline = false): string {
  return `${BASE}/content/${encodeURIComponent(token)}${inline ? '?inline=true' : ''}`
}
