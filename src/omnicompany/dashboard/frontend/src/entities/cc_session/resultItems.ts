// 会话产物: 画布卡、整张画布、图片、视频、其它文件, 统一成一种「可直接看」的条目。
// 产物条、结果画廊、终端链接悬停预览、全屏查看器都用它。
import { create } from 'zustand'
import { overlayFileContentUrl } from '../../api/overlayClient'
import type { TurnCanvasRef, TurnFileRef, TurnResults } from '../../lib/turnResults'

export type ResultItem =
  | { kind: 'card'; key: string; project: string; canvas: string; card: string; url: string }
  | { kind: 'canvas'; key: string; project: string; canvas: string; url: string }
  | { kind: 'image' | 'video'; key: string; src: string; name: string; path?: string; file?: TurnFileRef }
  | { kind: 'file'; key: string; file: TurnFileRef }

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|avif|bmp)$/i
const VIDEO_EXT = /\.(mp4|webm|mov|m4v)$/i

export function canvasItem(ref: TurnCanvasRef): ResultItem {
  return ref.card
    ? { kind: 'card', key: `card:${ref.project}/${ref.canvas}#${ref.card}`, project: ref.project, canvas: ref.canvas, card: ref.card, url: ref.url }
    : { kind: 'canvas', key: `canvas:${ref.project}/${ref.canvas}`, project: ref.project, canvas: ref.canvas, url: ref.url }
}

export function fileItem(file: TurnFileRef): ResultItem {
  const key = `file:${file.path.toLowerCase()}`
  if (file.open_token && (file.preview === 'image' || IMAGE_EXT.test(file.path))) {
    return { kind: 'image', key, src: overlayFileContentUrl(file.open_token), name: file.name, path: file.path, file }
  }
  if (file.open_token && (file.preview === 'video' || VIDEO_EXT.test(file.path))) {
    return { kind: 'video', key, src: overlayFileContentUrl(file.open_token), name: file.name, path: file.path, file }
  }
  return { kind: 'file', key, file }
}

function nameFromUrl(url: string): string {
  try {
    return decodeURIComponent(new URL(url).pathname.split('/').pop() || url)
  } catch {
    return url
  }
}

/** 顺序: 图和视频在前(一眼能判断), 再画布卡, 再其它文件; 各自保持「最近提到的在前」。 */
export function buildResultItems(results: TurnResults | null | undefined): ResultItem[] {
  if (!results) return []
  const media: ResultItem[] = []
  const cards: ResultItem[] = []
  const files: ResultItem[] = []
  for (const ref of results.media || []) {
    media.push({ kind: ref.kind, key: `url:${ref.url}`, src: ref.url, name: nameFromUrl(ref.url) })
  }
  for (const ref of results.canvases || []) cards.push(canvasItem(ref))
  for (const file of results.files || []) {
    const item = fileItem(file)
    ;(item.kind === 'file' ? files : media).push(item)
  }
  const seen = new Set<string>()
  return [...media, ...cards, ...files].filter((item) => (seen.has(item.key) ? false : (seen.add(item.key), true)))
}

/** 全屏查看器: 打开一组条目中的某一个, 左右键切换, Esc 返回。 */
interface ViewerState {
  items: ResultItem[]
  index: number
  open: (items: ResultItem[], index?: number) => void
  step: (delta: number) => void
  close: () => void
}

export const useResultViewer = create<ViewerState>((set) => ({
  items: [],
  index: 0,
  open: (items, index = 0) => set({ items, index: Math.max(0, Math.min(index, items.length - 1)) }),
  step: (delta) => set((state) => (state.items.length
    ? { index: (state.index + delta + state.items.length) % state.items.length }
    : state)),
  close: () => set({ items: [], index: 0 }),
}))
