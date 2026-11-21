// 产物的两种看法: 缩略块(产物条 / 画廊 / 终端链接悬停)和全屏查看器。
// 2026-11-20 业主:「提到的画布卡直接显示, 各种内容都要直接看, 而且图啊视频啊什么的放大可以全屏并且再返回,
// 确实不需要文字」。做法参考 Orca 的图片 lightbox(居中浮层, Esc 返回)与产物内嵌预览(embed=1)。
import '../../i18n/config.js'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChevronLeft, ChevronRight, ExternalLink, File, Folder, Globe, LayoutGrid, Maximize, Minimize, NotebookPen, PanelTop, Play, X } from 'lucide-react'
import { WorkshopPageFrame } from '../workshop'
import { overlayFileRegistration, type OverlayFileEntity } from '../overlay_file'
import { workshopPageUrl } from '../../lib/workshopSurface'
import { fetchCardPreviews, workshopMarkdownUrl, type WorkshopCardPreview } from '../../lib/workshopCards'
import { resolveSkin } from '../../skins/skinRegistry'
import { useUiSettings } from '../../stores/uiSettingsStore'
import { usePanels } from '../../stores/panelsStore'
import { openOverlayFileInDashboard } from '../../lib/overlayFileNavigation'
import { useResultViewer, type ResultItem } from './resultItems'
import './resultView.css'

// ── 画布卡数据 ──

const cardCache = new Map<string, Promise<WorkshopCardPreview | null>>()

function loadCard(project: string, canvas: string, card: string): Promise<WorkshopCardPreview | null> {
  const key = `${project}/${canvas}#${card}`
  let hit = cardCache.get(key)
  if (!hit) {
    hit = fetchCardPreviews(project, canvas, [card]).then((cards) => cards[0] ?? null)
    cardCache.set(key, hit)
    hit.catch(() => cardCache.delete(key))
    window.setTimeout(() => cardCache.delete(key), 30_000)
  }
  return hit
}

export function useCard(item: Extract<ResultItem, { kind: 'card' }> | null) {
  const [state, setState] = useState<{ card: WorkshopCardPreview | null; error: string; loading: boolean }>({ card: null, error: '', loading: true })
  useEffect(() => {
    if (!item) return undefined
    let alive = true
    setState({ card: null, error: '', loading: true })
    loadCard(item.project, item.canvas, item.card).then(
      (card) => { if (alive) setState({ card, error: '', loading: false }) },
      (cause) => { if (alive) setState({ card: null, error: cause instanceof Error ? cause.message : String(cause), loading: false }) },
    )
    return () => { alive = false }
  }, [item?.project, item?.canvas, item?.card])
  return state
}

const IMG_IN_MARKDOWN = /!\[[^\]]*\]\(([^)\s]+)/

function firstImage(markdown: string | null | undefined, project: string): string | null {
  const match = markdown ? IMG_IN_MARKDOWN.exec(markdown) : null
  return match ? workshopMarkdownUrl(match[1]!, project) : null
}

function CanvasFrame({ item }: { item: Extract<ResultItem, { kind: 'card' | 'canvas' }> }) {
  const base = useUiSettings((state) => resolveSkin(state.skin).base)
  const target = item.kind === 'card' ? `${item.canvas}#${item.card}` : item.canvas
  return <WorkshopPageFrame page="canvas" src={workshopPageUrl('canvas', { base, target, project: item.project })} />
}

/** 卡片正文(卡自己的内容), 正文里的图点一下全屏。 */
export function CardBody({ item, markdown }: { item: Extract<ResultItem, { kind: 'card' }>; markdown: string }) {
  const openViewer = useResultViewer((state) => state.open)
  return (
    <div className="rv-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => workshopMarkdownUrl(url, item.project)}
        components={{
          img: ({ src, alt }) => (
            <img
              src={String(src || '')}
              alt={alt || ''}
              onClick={() => openViewer([{ kind: 'image', key: `img:${src}`, src: String(src || ''), name: alt || '' }])}
            />
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}

// ── 缩略块 ──

function TileCard({ item }: { item: Extract<ResultItem, { kind: 'card' }> }) {
  const { t } = useTranslation('results')
  const { card, error, loading } = useCard(item)
  const cover = firstImage(card?.markdown, item.project)
  return (
    <>
      <div className="rv-tile-title"><NotebookPen size={11} /><span>{card?.title || item.card}</span></div>
      {card?.mediaKind === 'video' && card.mediaUrl
        ? <><video className="rv-tile-cover" src={card.mediaUrl} muted preload="metadata" /><span className="rv-tile-play"><Play size={16} /></span></>
        : card?.mediaKind === 'image' && card.mediaUrl
          ? <img className="rv-tile-cover" src={card.mediaUrl} alt="" loading="lazy" />
          : card?.mediaKind === 'web'
            ? <div className="rv-tile-icon"><Globe size={22} /></div>
      : cover
        ? <img className="rv-tile-cover" src={cover} alt="" loading="lazy" />
        : card?.markdown
          ? <div className="rv-tile-text"><ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={(url) => workshopMarkdownUrl(url, item.project)}>{card.markdown.slice(0, 1200)}</ReactMarkdown></div>
          : <div className="rv-tile-empty">{loading ? '' : error === 'auth-required' ? t('cardLogin') : card ? item.canvas : t('cardMissing')}</div>}
    </>
  )
}

export function ResultTile({ item, onOpen, size = 'shelf' }: { item: ResultItem; onOpen?: () => void; size?: 'shelf' | 'grid' | 'hover' }) {
  const body = (() => {
    switch (item.kind) {
      case 'image':
        return <img className="rv-tile-cover" src={item.src} alt={item.name} loading="lazy" />
      case 'video':
        return (
          <>
            <video className="rv-tile-cover" src={item.src} muted preload="metadata" />
            <span className="rv-tile-play"><Play size={16} /></span>
          </>
        )
      case 'card':
        return <TileCard item={item} />
      case 'canvas':
        return <div className="rv-tile-icon"><LayoutGrid size={22} /><span>{item.canvas}</span></div>
      case 'file':
        return <div className="rv-tile-icon">{item.file.kind === 'folder' ? <Folder size={22} /> : <File size={22} />}<span>{item.file.name}</span></div>
    }
  })()
  const title = item.kind === 'file'
    ? item.file.path
    : item.kind === 'card' || item.kind === 'canvas' ? item.url : (item.path || item.src)
  if (size === 'hover') return <div className="rv-tile" data-size="hover" data-kind={item.kind}>{body}</div>
  return (
    <button type="button" className="rv-tile" data-size={size} data-kind={item.kind} title={title} onClick={onOpen} data-testid="result-tile">
      {body}
    </button>
  )
}

// ── 全屏查看器 ──

function ViewerFile({ item }: { item: Extract<ResultItem, { kind: 'file' }> }) {
  const { t } = useTranslation('results')
  const [entity, setEntity] = useState<OverlayFileEntity | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    setEntity(null)
    setError('')
    if (!item.file.open_token) return undefined
    let alive = true
    overlayFileRegistration.resolver.fetch(item.file.open_token).then(
      (next) => { if (alive) setEntity(next as OverlayFileEntity) },
      (cause) => { if (alive) setError(cause instanceof Error ? cause.message : String(cause)) },
    )
    return () => { alive = false }
  }, [item.file.open_token])
  if (!item.file.open_token) return <div className="rv-viewer-note">{t('fileOwnerOnly')}</div>
  if (error) return <div className="rv-viewer-note">{error}</div>
  if (!entity) return null
  const Viewer = overlayFileRegistration.renderer.Editor
  return <div className="rv-viewer-file"><Viewer entity={entity} /></div>
}

function ViewerCard({ item }: { item: Extract<ResultItem, { kind: 'card' }> }) {
  const { t } = useTranslation('results')
  const { card, error, loading } = useCard(item)
  const [asCanvas, setAsCanvas] = useState(false)
  useEffect(() => { setAsCanvas(false) }, [item.key])
  const media = card?.mediaKind && card.mediaUrl ? { kind: card.mediaKind, url: card.mediaUrl } : null
  const showCanvas = asCanvas || (card !== null && card.markdown === null && !media)
  return (
    <div className="rv-viewer-card">
      <div className="rv-viewer-card-head">
        <strong>{card?.title || item.card}</strong>
        {card && (card.markdown !== null || media) && (
          <button type="button" className="rv-btn" onClick={() => setAsCanvas((v) => !v)}>{asCanvas ? t('viewText') : t('viewInCanvas')}</button>
        )}
      </div>
      {showCanvas
        ? <div className="rv-viewer-frame"><CanvasFrame item={item} /></div>
        : media?.kind === 'video'
          ? <div className="rv-viewer-media-wrap"><video className="rv-viewer-media" src={media.url} controls autoPlay /></div>
        : media?.kind === 'image'
          ? <div className="rv-viewer-media-wrap"><img className="rv-viewer-media" src={media.url} alt={card?.title || ''} /></div>
        : media?.kind === 'web'
          ? <div className="rv-viewer-frame"><iframe className="rv-viewer-web" src={media.url} title={card?.title || ''} sandbox="allow-scripts allow-same-origin allow-forms allow-popups" /></div>
        : card?.markdown
          ? <div className="rv-viewer-card-body"><CardBody item={item} markdown={card.markdown} /></div>
          : <div className="rv-viewer-note">{loading ? '' : error === 'auth-required' ? t('cardLogin') : t('cardMissing')}</div>}
    </div>
  )
}

/** 一个产物的完整内容: 全屏查看器和终端链接旁的展开面板共用。 */
export function ResultBody({ item, autoPlay = true }: { item: ResultItem; autoPlay?: boolean }) {
  switch (item.kind) {
    case 'image':
      return <img className="rv-viewer-media" src={item.src} alt={item.name} />
    case 'video':
      return <video className="rv-viewer-media" src={item.src} controls autoPlay={autoPlay} />
    case 'card':
      return <ViewerCard item={item} />
    case 'canvas':
      return <div className="rv-viewer-card"><div className="rv-viewer-frame"><CanvasFrame item={item} /></div></div>
    case 'file':
      return <ViewerFile item={item} />
  }
}

// 查看器在终端和结果画廊里各挂一份, 只让最先挂上的那份渲染, 免得叠两层。
const hostOrder: string[] = []

export function ResultViewerHost() {
  const id = React.useId()
  const [primary, setPrimary] = useState(false)
  useEffect(() => {
    hostOrder.push(id)
    const sync = () => setPrimary(hostOrder[0] === id)
    sync()
    window.addEventListener('rv-host-change', sync)
    window.dispatchEvent(new Event('rv-host-change'))
    return () => {
      hostOrder.splice(hostOrder.indexOf(id), 1)
      window.removeEventListener('rv-host-change', sync)
      window.dispatchEvent(new Event('rv-host-change'))
    }
  }, [id])
  return primary ? <ResultViewerOverlay /> : null
}

function ResultViewerOverlay() {
  const { t } = useTranslation('results')
  const { items, index, step, close } = useResultViewer()
  const openTab = usePanels((state) => state.openTab)
  const rootRef = useRef<HTMLDivElement>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const item = items[index] ?? null

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  useEffect(() => {
    if (!item) return undefined
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.fullscreenElement) { event.preventDefault(); close() }
      if (event.key === 'ArrowLeft' && items.length > 1) step(-1)
      if (event.key === 'ArrowRight' && items.length > 1) step(1)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [close, item, items.length, step])

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    else void rootRef.current?.requestFullscreen?.().catch(() => {})
  }, [])

  const closeAll = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    close()
  }, [close])

  if (!item) return null

  const openElsewhere = () => {
    const file = item.kind === 'file' || item.kind === 'image' || item.kind === 'video' ? item.file : undefined
    if (item.kind === 'card' || item.kind === 'canvas') {
      openTab({ type: 'workshop', id: 'canvas' }, t('canvasTab', { canvas: item.canvas }), item.kind === 'card' ? `${item.canvas}#${item.card}` : item.canvas)
    } else if (file?.open_token) {
      openOverlayFileInDashboard({ name: file.name, path: file.path, kind: file.kind, open_token: file.open_token })
    } else if (item.kind === 'image' || item.kind === 'video') {
      window.open(item.src, '_blank', 'noopener,noreferrer')
    }
    closeAll()
  }
  const externalUrl = item.kind === 'card' || item.kind === 'canvas' ? item.url : null

  return createPortal(
    <div ref={rootRef} className="rv-viewer" data-testid="result-viewer" data-kind={item.kind} onClick={(event) => { if (event.target === event.currentTarget) closeAll() }}>
      <div className="rv-viewer-bar">
        {items.length > 1 && <span className="rv-viewer-count">{index + 1} / {items.length}</span>}
        <button type="button" className="rv-btn-icon" title={t('openTab')} onClick={openElsewhere}><PanelTop size={16} /></button>
        {externalUrl && <a className="rv-btn-icon" href={externalUrl} target="_blank" rel="noopener noreferrer" title={t('openWindow')}><ExternalLink size={16} /></a>}
        <button type="button" className="rv-btn-icon" title={fullscreen ? t('exitFullscreen') : t('fullscreen')} onClick={toggleFullscreen} data-testid="result-viewer-fullscreen">
          {fullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
        </button>
        <button type="button" className="rv-btn-icon" title={t('close')} onClick={closeAll} data-testid="result-viewer-close"><X size={18} /></button>
      </div>
      {items.length > 1 && (
        <>
          <button type="button" className="rv-nav" data-side="left" title={t('prev')} onClick={() => step(-1)}><ChevronLeft size={28} /></button>
          <button type="button" className="rv-nav" data-side="right" title={t('next')} onClick={() => step(1)}><ChevronRight size={28} /></button>
        </>
      )}
      <div className="rv-viewer-stage" onClick={(event) => { if (event.target === event.currentTarget) closeAll() }}>
        <ResultBody item={item} />
      </div>
    </div>,
    document.body,
  )
}
