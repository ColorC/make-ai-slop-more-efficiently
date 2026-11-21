// 画布卡片直接预览(2026-11-20 业主:「做好了东西应该展示的尽量直接, 最好免点击……是否可以直接预览画布卡片」)。
// 走同源 /bside 代理(机主设备由驾驶舱后端带 Workshop 个人身份):
//   GET  /bside/api/canvases/<画布>              画布文档(卡片标题、类型、正文编号)
//   POST /bside/lofa/overlay/invoke notes_text_get_many   文字卡正文(Markdown)
// 图片在正文里写成 ../lofa/overlay/asset/<id>, 要带 ?project= 才取得到。

export interface WorkshopCardPreview {
  id: string
  title: string
  kind: string
  adapter: string
  canvasTitle: string
  /** 文字卡的 Markdown 正文; 非文字卡为 null。 */
  markdown: string | null
  /** 视频 / 图片 / 网页卡的源地址(已改成驾驶舱同源可取); 其它卡为 null, 由调用方嵌画布页。 */
  mediaUrl: string | null
  mediaKind: 'video' | 'image' | 'web' | null
}

interface CanvasCard {
  id?: string
  title?: string
  kind?: string
  adapter?: string
  source?: { editableId?: string; kind?: string; url?: string }
}

interface CanvasDocument {
  title?: string
  revision?: number
  cards?: CanvasCard[]
}

const CANVAS_TTL_MS = 30_000
const canvasCache = new Map<string, { at: number; doc: Promise<CanvasDocument> }>()

function headers(project: string): Record<string, string> {
  return project ? { 'x-workshop-project': project } : {}
}

export async function fetchCanvasDocument(project: string, canvas: string): Promise<CanvasDocument> {
  const key = `${project}/${canvas}`
  const cached = canvasCache.get(key)
  if (cached && Date.now() - cached.at < CANVAS_TTL_MS) return cached.doc
  const doc = fetch(`/bside/api/canvases/${encodeURIComponent(canvas)}`, { headers: headers(project) })
    .then(async (response) => {
      if (response.status === 401 || response.status === 403) throw new Error('auth-required')
      if (!response.ok) throw new Error(`canvas ${response.status}`)
      const payload = await response.json()
      return (payload?.document ?? payload) as CanvasDocument
    })
  canvasCache.set(key, { at: Date.now(), doc })
  doc.catch(() => canvasCache.delete(key))
  return doc
}

export async function fetchCardTexts(project: string, ids: string[]): Promise<Map<string, string>> {
  const out = new Map<string, string>()
  if (!ids.length) return out
  const response = await fetch('/bside/lofa/overlay/invoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers(project) },
    body: JSON.stringify({ cmd: 'notes_text_get_many', args: { ids } }),
  })
  if (!response.ok) throw new Error(`texts ${response.status}`)
  const payload = await response.json()
  const items = payload?.result?.items ?? {}
  const values: Array<Record<string, unknown>> = Array.isArray(items) ? items : Object.values(items)
  for (const item of values) {
    const id = String(item.editableId ?? item.id ?? '')
    if (id && typeof item.content === 'string') out.set(id, item.content)
  }
  return out
}

/** 一张画布里这一轮提到的若干张卡 → 预览数据; 卡已删时跳过。 */
export async function fetchCardPreviews(
  project: string,
  canvas: string,
  cardIds: string[],
): Promise<WorkshopCardPreview[]> {
  const doc = await fetchCanvasDocument(project, canvas)
  const byId = new Map((doc.cards || []).map((card) => [String(card.id || ''), card]))
  // 终端里的长链接常被 TUI 折行, 识别器只拿到前半截卡号: 按前缀找, 唯一命中才认。
  const resolve = (id: string): CanvasCard | undefined => {
    const exact = byId.get(id)
    if (exact || id.length < 6) return exact
    const hits = (doc.cards || []).filter((card) => String(card.id || '').startsWith(id))
    return hits.length === 1 ? hits[0] : undefined
  }
  const wanted = cardIds.map(resolve).filter((card): card is CanvasCard => Boolean(card))
  const textIds = wanted
    .filter((card) => card.kind === 'text' && (card.adapter || 'text') === 'text')
    .map((card) => card.source?.editableId || String(card.id))
  const texts = await fetchCardTexts(project, textIds)
  return wanted.map((card) => {
    const textId = card.source?.editableId || String(card.id)
    const isText = card.kind === 'text' && (card.adapter || 'text') === 'text'
    const adapter = String(card.adapter || '')
    const mediaKind = card.source?.kind === 'link' && card.source.url && (adapter === 'video' || adapter === 'image' || adapter === 'web')
      ? adapter
      : null
    return {
      id: String(card.id),
      title: String(card.title || card.id),
      kind: String(card.kind || ''),
      adapter,
      canvasTitle: String(doc.title || canvas),
      markdown: isText ? texts.get(textId) ?? '' : null,
      mediaUrl: mediaKind ? workshopSourceUrl(String(card.source!.url)) : null,
      mediaKind,
    }
  })
}

/** Workshop 自己域名下的地址改走驾驶舱同源 /bside 代理(机主设备带个人身份, 远程设备也能看)。 */
export function workshopSourceUrl(url: string): string {
  const match = /^https?:\/\/workshop\.colorc\.cc\/bside\/(.*)$/i.exec(url)
  if (match) return `/bside/${match[1]}`
  if (/^\.\.\/api\//.test(url)) return `/bside/${url.slice(3)}`
  return url
}

/** 正文里的相对地址 → 驾驶舱同源可取的地址。 */
export function workshopMarkdownUrl(url: string, project: string): string {
  if (/^\.\.\/lofa\/overlay\/asset\//.test(url) || /^lofa\/overlay\/asset\//.test(url)) {
    const path = url.replace(/^\.\.\//, '')
    const sep = path.includes('?') ? '&' : '?'
    return `/bside/${path}${project ? `${sep}project=${encodeURIComponent(project)}` : ''}`
  }
  if (/^\.\.\/canvas\?/.test(url)) return `/bside/${url.replace(/^\.\.\//, '')}`
  return url
}
