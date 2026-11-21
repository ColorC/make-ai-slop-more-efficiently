/**
 * 用户 UI 捕获(圈选/快照/调试交接)客户端 — 配套
 * src/omnicompany/dashboard/boss_sight/captures/routes.py
 *
 * 用户明示 2026-06-03: 复制 = 纯剪贴板(不经此 API); 提交 = 保存到文件(save)。不进审阅队列。
 * 2026-06-12 用户明示: "捕获塞给总控"的功能与提示整体关停 — dispatch 已删,
 * 捕获只落盘(enqueue 一律 false), 由用户自己决定把文件路径粘给谁。
 */

const BASE = '/api/boss-sight/captures'

export interface CaptureSaveBody {
  capture_kind: 'element_comment' | 'page_snapshot' | 'debug_start'
  title?: string
  comment?: string
  url?: string
  route?: string
  target?: Record<string, unknown> | null
  text_snapshot?: string | null
  dom_snapshot?: string | null
  /** 尽力截图(同源 canvas toDataURL); 后端落盘成 shot-*.png 并写进 md 的「截图」节。空/WebGL 未保留缓冲时省略。 */
  image_data_url?: string | null
  /** P1.2 快照带日志: 浏览器日志环形缓冲当前内容(最近 50 条 {time, level, text}), 后端写进 md 的「浏览器日志」节。 */
  logs?: Array<{ time: string; level: string; text: string }> | null
  /** true(提交)=存 pending 进 dispatch 批次; false(复制)=存 clips 只为拿文件链接, 不计入待处理。默认 true。 */
  enqueue?: boolean
}

export interface CaptureSaveResult {
  saved_path: string
  pending_count: number
  /** P1.2 快照 URL 化: 该捕获文件的 http 可访问 URL(经 /api/boss-sight/captures/file 静态读取), 后端组装。 */
  url?: string
  share_id?: string
  canonical_url?: string | null
  share_status?: 'reserved' | 'capturing' | 'ready' | 'failed' | 'unavailable'
  share_error?: string | null
}

export interface CaptureListResult {
  pending_count: number
  items: { name: string; path: string }[]
}

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text().catch(() => '')).slice(0, 200)}`)
  return r.json() as Promise<T>
}

export const capturesApi = {
  // 提交 = 保存到文件(pending 目录)
  save: async (body: CaptureSaveBody): Promise<CaptureSaveResult> =>
    jsonOrThrow(await fetch(BASE, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })),

  list: async (): Promise<CaptureListResult> =>
    jsonOrThrow(await fetch(BASE)),
}
