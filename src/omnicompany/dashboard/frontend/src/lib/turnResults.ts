// 会话「本轮结果」与本机文件打开(2026-11-20 人在回路提速)。
// 后端: GET /api/cc/sessions/{sid}/turn-results, POST /api/local-files/open。
import { openOverlayFileInDashboard } from './overlayFileNavigation'

export interface TurnCanvasRef {
  project: string
  canvas: string
  card: string
  url: string
}

export interface TurnFileRef {
  name: string
  path: string
  kind: 'file' | 'folder'
  preview: string
  mime: string
  size: number
  /** 只有机主设备拿得到; 为 null 时只能看路径。 */
  open_token: string | null
}

export interface TurnMediaRef {
  url: string
  kind: 'image' | 'video'
}

export interface TurnResults {
  session_id: string
  provider: string
  owner_device: boolean
  turn_key: string
  prompt: string
  canvases: TurnCanvasRef[]
  files: TurnFileRef[]
  /** 回复里直接给出的图片 / 视频网址。 */
  media?: TurnMediaRef[]
  scope?: 'turn' | 'session'
  unavailable?: string
}

export function turnResultCount(results: TurnResults | null | undefined): number {
  return results ? results.canvases.length + results.files.length : 0
}

export async function fetchTurnResults(
  sessionId: string,
  signal?: AbortSignal,
  scope: 'turn' | 'session' = 'turn',
): Promise<TurnResults> {
  const query = scope === 'session' ? '?scope=session' : ''
  const response = await fetch(`/api/cc/sessions/${encodeURIComponent(sessionId)}/turn-results${query}`, { signal })
  if (!response.ok) throw new Error(`本轮结果: ${response.status}`)
  return response.json()
}

export interface LocalFileOpenResult {
  name: string
  path: string
  kind: string
  mime: string
  open_token: string
  preview?: string
}

export async function openLocalPathToken(path: string): Promise<LocalFileOpenResult> {
  const response = await fetch('/api/local-files/open', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || `打开失败(${response.status})`)
  return payload.result as LocalFileOpenResult
}

/** 终端里点到的本机路径 → 驾驶舱里的文件页签(远程设备上也能看)。 */
export async function openLocalPathInDashboard(path: string): Promise<void> {
  const result = await openLocalPathToken(path)
  openOverlayFileInDashboard({
    name: result.name,
    path: result.path,
    kind: result.kind,
    open_token: result.open_token,
  })
}

/** Workshop 画布链接(…/bside/canvas?… 或 …/canvas?…) → 画布引用; 不是画布链接返回 null。 */
export function parseWorkshopCanvasUrl(raw: string): TurnCanvasRef | null {
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    return null
  }
  if (!/\/canvas(?:\/notes-web\.html)?$/.test(url.pathname)) return null
  const canvas = url.searchParams.get('canvas') || ''
  if (!canvas) return null
  const hash = decodeURIComponent(url.hash.replace(/^#/, ''))
  const card = url.searchParams.get('card') || url.searchParams.get('focus') || (hash.startsWith('card=') ? hash.slice(5) : hash)
  return { project: url.searchParams.get('project') || '', canvas, card, url: raw }
}

export function canvasRefKey(ref: Pick<TurnCanvasRef, 'project' | 'canvas' | 'card'>): string {
  return `canvas:${ref.project}/${ref.canvas}#${ref.card}`
}

export function fileRefKey(ref: Pick<TurnFileRef, 'path'>): string {
  return `file:${ref.path.toLowerCase()}`
}

// Windows 绝对路径(盘符开头), 遇到空白/引号/反引号/竖线/全角标点即止。
const LOCAL_PATH = /(?<![A-Za-z0-9_/\\])(?:file:\/\/\/)?[A-Za-z]:[\\/][^\s"'<>`|*?　-〿＀-￯│┃]*/g
const TRAILING = /[.,;:!?)\]}>*_~]+$/

export interface LocalPathMatch {
  path: string
  start: number
  end: number
}

/** 一行文字里的本机绝对路径(只做形状判断; 存不存在由后端打开时判)。 */
export function findLocalPaths(text: string): LocalPathMatch[] {
  const out: LocalPathMatch[] = []
  for (const match of text.matchAll(LOCAL_PATH)) {
    let value = match[0]
    const trimmed = value.replace(TRAILING, '')
    value = trimmed
    if (value.length < 4) continue
    const start = match.index ?? 0
    out.push({ path: value.replace(/^file:\/\/\//i, ''), start, end: start + value.length })
  }
  return out
}
