// 终端链接旁预览(2026-11-20 业主:「cli 相关界面展开那只能在链接旁边展开了」; 11-21:「那改成悬停弹预览呗」)。
// 鼠标停在画布卡链接 / 图片视频网址 / 本机路径上, 预览就贴着这条链接弹出; 鼠标移进预览可以停留操作,
// 移开就收起。点击链接直接全屏, Esc 返回; 按住 Ctrl 点击照旧(网页开页签、文件开文件页签)。
// 成串的产物放结果页画廊, 终端里不排序列。
import '../../i18n/config.js'
import React, { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { create } from 'zustand'
import { Maximize, PanelTop, X } from 'lucide-react'
import { openLocalPathToken, parseWorkshopCanvasUrl } from '../../lib/turnResults'
import { openOverlayFileInDashboard } from '../../lib/overlayFileNavigation'
import { usePanels } from '../../stores/panelsStore'
import { canvasItem, fileItem, useResultViewer, type ResultItem } from './resultItems'
import { ResultBody, ResultViewerHost } from './ResultView'

const MEDIA_URL = /\.(png|jpe?g|gif|webp|svg|avif|mp4|webm|mov|m4v)(\?|$)/i
const VIDEO_URL = /\.(mp4|webm|mov|m4v)(\?|$)/i

/** 终端里的网址 → 可直接看的条目; 普通网页返回 null(照旧开页签)。 */
export function itemForUrl(url: string): ResultItem | null {
  const canvas = parseWorkshopCanvasUrl(url)
  if (canvas) return canvasItem(canvas)
  let pathname = ''
  try { pathname = new URL(url).pathname } catch { return null }
  if (!MEDIA_URL.test(pathname)) return null
  return { kind: VIDEO_URL.test(pathname) ? 'video' : 'image', key: `url:${url}`, src: url, name: decodeURIComponent(pathname.split('/').pop() || url) }
}

const pathCache = new Map<string, Promise<ResultItem | null>>()

/** 本机路径 → 条目(机主设备换只读令牌); 不存在或无权限返回 null。 */
export function itemForPath(path: string): Promise<ResultItem | null> {
  const key = path.toLowerCase()
  let hit = pathCache.get(key)
  if (!hit) {
    hit = openLocalPathToken(path).then(
      (result) => fileItem({
        name: result.name,
        path: result.path,
        kind: result.kind === 'folder' ? 'folder' : 'file',
        preview: result.preview || (result.kind === 'folder' ? 'folder' : 'none'),
        mime: result.mime,
        size: 0,
        open_token: result.open_token,
      }),
      () => null,
    )
    pathCache.set(key, hit)
    window.setTimeout(() => pathCache.delete(key), 60_000)
  }
  return hit
}

interface ExpandState {
  item: ResultItem | null
  /** 被点的那一行的屏幕位置: 面板贴着这行下沿展开。 */
  x: number
  rowTop: number
  rowBottom: number
  show: (item: ResultItem, x: number, rowTop: number, rowBottom: number) => void
  close: () => void
}

export const useLinkExpand = create<ExpandState>((set, get) => ({
  item: null,
  x: 0,
  rowTop: 0,
  rowBottom: 0,
  show: (item, x, rowTop, rowBottom) => {
    // 同一条链接在两行之间移动时不跳位置
    if (get().item?.key === item.key) return
    set({ item, x, rowTop, rowBottom })
  },
  close: () => { pointerInPanel = false; set({ item: null }) },
}))

// 悬停弹出 / 收起的节奏: 停一下才弹(扫过不闪), 移开后留一点时间让鼠标移进面板。
const SHOW_DELAY_MS = 220
const HIDE_DELAY_MS = 260
let showTimer = 0
let hideTimer = 0
let hoverToken = 0
/** 鼠标在预览里: xterm 晚到的悬停 / 离开(路上经过的别的链接)一律不理。 */
let pointerInPanel = false

export function hoverLinkEnter(event: MouseEvent, resolve: () => ResultItem | null | Promise<ResultItem | null>, screen: Element | null, rows: number) {
  if (pointerInPanel) return
  window.clearTimeout(hideTimer)
  window.clearTimeout(showTimer)
  const token = ++hoverToken
  const { top, bottom } = rowBounds(event, screen, rows)
  const x = event.clientX
  showTimer = window.setTimeout(() => {
    void Promise.resolve(resolve()).then((item) => {
      if (item && token === hoverToken) useLinkExpand.getState().show(item, x, top, bottom)
    })
  }, SHOW_DELAY_MS)
}

export function hoverLinkLeave() {
  if (pointerInPanel) return
  hoverToken += 1
  window.clearTimeout(showTimer)
  window.clearTimeout(hideTimer)
  hideTimer = window.setTimeout(() => useLinkExpand.getState().close(), HIDE_DELAY_MS)
}

// 鼠标进了预览: 不收起, 也不再被路上经过的别的链接换掉。
function holdPanel() {
  pointerInPanel = true
  hoverToken += 1
  window.clearTimeout(hideTimer)
  window.clearTimeout(showTimer)
}

const PANEL_W = 560
const PANEL_H = 360

/** 点中的那一行在屏幕上的上下沿(按终端行高取整)。 */
export function rowBounds(event: MouseEvent, screen: Element | null, rows: number): { top: number; bottom: number } {
  const rect = screen?.getBoundingClientRect()
  if (!rect || rows <= 0) return { top: event.clientY - 9, bottom: event.clientY + 9 }
  const cell = rect.height / rows
  const index = Math.floor((event.clientY - rect.top) / cell)
  return { top: rect.top + index * cell, bottom: rect.top + (index + 1) * cell }
}

export function TerminalLinkExpand() {
  const { t } = useTranslation('results')
  const { item, x, rowTop, rowBottom, close } = useLinkExpand()
  const openViewer = useResultViewer((state) => state.open)
  const openTab = usePanels((state) => state.openTab)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!item) return undefined
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !useResultViewer.getState().items.length) close()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [close, item])

  if (!item) return <ResultViewerHost />

  const width = Math.min(PANEL_W, window.innerWidth - 16)
  const height = Math.min(PANEL_H, window.innerHeight - 16)
  const left = Math.max(8, Math.min(x - 24, window.innerWidth - width - 8))
  const below = rowBottom + 4 + height <= window.innerHeight - 8
  const top = below ? rowBottom + 4 : Math.max(8, rowTop - 4 - height)
  // 卡片的标题在面板正文头上(卡自己的标题), 这里不重复。
  const title = item.kind === 'file' ? item.file.name
    : item.kind === 'image' || item.kind === 'video' ? item.name
      : item.kind === 'canvas' ? item.canvas : ''

  const openElsewhere = () => {
    const file = item.kind === 'file' || item.kind === 'image' || item.kind === 'video' ? item.file : undefined
    if (item.kind === 'card' || item.kind === 'canvas') {
      openTab({ type: 'workshop', id: 'canvas' }, t('canvasTab', { canvas: item.canvas }), item.kind === 'card' ? `${item.canvas}#${item.card}` : item.canvas)
    } else if (file?.open_token) {
      openOverlayFileInDashboard({ name: file.name, path: file.path, kind: file.kind, open_token: file.open_token })
    } else if (item.kind === 'image' || item.kind === 'video') {
      window.open(item.src, '_blank', 'noopener,noreferrer')
    }
    close()
  }

  return (
    <>
      <ResultViewerHost />
      <div
        ref={panelRef}
        className="rv-expand xterm-hover"
        onMouseEnter={holdPanel}
        onMouseLeave={() => { pointerInPanel = false; hoverLinkLeave() }}
        data-kind={item.kind}
        data-testid="terminal-link-expand"
        style={{ left, top, width, height }}
      >
        <div className="rv-expand-bar">
          <span className="rv-expand-title" title={title}>{title}</span>
          <button type="button" className="rv-expand-btn" title={t('openTab')} onClick={openElsewhere}><PanelTop size={14} /></button>
          <button type="button" className="rv-expand-btn" title={t('fullscreen')} data-testid="terminal-link-expand-fullscreen" onClick={() => openViewer([item])}><Maximize size={14} /></button>
          <button type="button" className="rv-expand-btn" title={t('close')} onClick={close}><X size={14} /></button>
        </div>
        <div className="rv-expand-body"><ResultBody item={item} autoPlay={false} /></div>
      </div>
    </>
  )
}
