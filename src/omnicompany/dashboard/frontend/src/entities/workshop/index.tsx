// Workshop 面 —— 内容(项目 / 画布 / 审阅)由 Workshop 管, 驾驶舱只负责把对应页面嵌进页签。
// 驾驶舱原生的项目板、札记画布、审阅队列已退役; 旧地址在 panelsStore 的
// canonicalizePanelTarget 里统一改指到这里。
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ClipboardList, FolderKanban, NotebookPen } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { WORKSHOP_PAGES, isWorkshopPage, workshopPageUrl, type WorkshopPage } from '../../lib/workshopSurface'
import { checkWorkshopAccess, workshopLoginUrl, type WorkshopAccess } from '../../lib/workshopAccess'
import { resolveSkin } from '../../skins/skinRegistry'
import { useUiSettings } from '../../stores/uiSettingsStore'

export interface WorkshopEntity extends Entity {
  type: 'workshop'
  id: WorkshopPage
}

const resolver: EntityResolver<WorkshopEntity> = {
  type: 'workshop',
  async fetch(id) {
    if (!isWorkshopPage(id)) throw new Error(`workshop: unknown page ${id}`)
    return { type: 'workshop', id, title: WORKSHOP_PAGES[id].label }
  },
  async list() {
    return (Object.keys(WORKSHOP_PAGES) as WorkshopPage[])
      .map((id) => ({ type: 'workshop' as const, id, title: WORKSHOP_PAGES[id].label }))
  },
}

export const WORKSHOP_PAGE_ICON: Record<WorkshopPage, React.ReactNode> = {
  projects: <FolderKanban size={14} />,
  canvas: <NotebookPen size={14} />,
  review: <ClipboardList size={14} />,
}

// 嵌入框不给 allow-top-navigation: Workshop 没登录时会把「顶层页面」换成它的登录页,
// 同源嵌入下顶层就是驾驶舱 —— 整个驾驶舱被换走, 页签恢复后还会反复被换走。
// 登录由下面的门自己处理(在新窗口登录), 框里的页面永远动不了驾驶舱的地址。
export const WORKSHOP_FRAME_SANDBOX = [
  'allow-scripts', 'allow-same-origin', 'allow-forms', 'allow-modals', 'allow-downloads',
  'allow-popups', 'allow-popups-to-escape-sandbox',
].join(' ')

const PROBE_MS = 400
/** 等内容的探测最多跑这么久, 到点不管有没有都收手(不让定时器一直挂着)。 */
const PROBE_LIMIT_MS = 20_000
/** 超过这么久还没内容, 就在等待提示里给一个「在新窗口打开」的出路。 */
const SLOW_MS = 12_000
/** 等登录时多久问一次「登上了吗」。 */
const LOGIN_POLL_MS = 3_000

/** 页面是同源的(走 /bside 代理), 可以直接看它有没有渲染出内容。
 *  用 textContent 而不是 innerText: 后者每次都要框里的页面重新排版。 */
function frameHasContent(frame: HTMLIFrameElement | null): boolean {
  try {
    const text = frame?.contentDocument?.body?.textContent ?? ''
    return text.trim().length > 20
  } catch {
    return true
  }
}

const gateStyle: React.CSSProperties = {
  position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center',
  justifyContent: 'center', gap: 10, color: 'var(--fp-text-2)', fontSize: 'var(--fp-fs-3)',
}

/** facet = 页面内定位: 画布页是 `画布id` 或 `画布id#卡片id`, 项目页是项目 id。 */
const Editor: React.FC<{ entity: WorkshopEntity; facet?: string }> = ({ entity, facet }) => {
  // Workshop 自己的明暗主题跟驾驶舱当前皮肤的明暗走。
  const base = useUiSettings((state) => resolveSkin(state.skin).base)
  const src = workshopPageUrl(entity.id, { base, target: facet })
  return <WorkshopPageFrame page={entity.id} src={src} />
}

/** 嵌一页 Workshop: 先问认不认得我(没登录给登录门), 再放进沙箱框; 会话侧栏「结果」页也用它。 */
export const WorkshopPageFrame: React.FC<{ page: WorkshopPage; src: string; label?: string }> = ({ page, src, label: labelOverride }) => {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [access, setAccess] = useState<WorkshopAccess | 'checking'>('checking')
  const [ready, setReady] = useState(false)
  const [slow, setSlow] = useState(false)
  const label = labelOverride || WORKSHOP_PAGES[page].label

  const recheck = useCallback(() => {
    let alive = true
    void checkWorkshopAccess().then((result) => { if (alive) setAccess(result) })
    return () => { alive = false }
  }, [])

  // 先问 Workshop 认不认得我, 认得才把页面嵌进来。
  useEffect(() => recheck(), [recheck])

  // 等登录: 用户在新窗口登完, 这里自己发现并接着打开, 不用回来点。
  useEffect(() => {
    if (access !== 'login-required') return undefined
    const timer = window.setInterval(recheck, LOGIN_POLL_MS)
    window.addEventListener('focus', recheck)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', recheck)
    }
  }, [access, recheck])

  // Workshop 首屏要几秒到十几秒; 框是透明的, 不提示的话看上去就是一张空页。
  useEffect(() => {
    if (access !== 'ok') return undefined
    setReady(false)
    setSlow(false)
    const stop = () => window.clearInterval(probe)
    const probe = window.setInterval(() => {
      if (!frameHasContent(frameRef.current)) return
      setReady(true)
      stop()
    }, PROBE_MS)
    const slowTimer = window.setTimeout(() => setSlow(true), SLOW_MS)
    const limitTimer = window.setTimeout(() => { stop(); setReady(true) }, PROBE_LIMIT_MS)
    return () => {
      stop()
      window.clearTimeout(slowTimer)
      window.clearTimeout(limitTimer)
    }
  }, [access, src])

  if (access === 'checking') {
    return <div style={{ position: 'relative', width: '100%', height: '100%' }}><div style={gateStyle}>正在连接 Workshop…</div></div>
  }

  if (access === 'login-required') {
    return (
      <div style={{ position: 'relative', width: '100%', height: '100%' }}>
        <div style={gateStyle} data-testid="workshop-page-login">
          <span style={{ color: 'var(--fp-text)' }}>{label}在 Workshop 里，需要先登录 Workshop</span>
          <span>登录在新窗口完成，登完这里会自己接着打开。</span>
          <span style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <a className="v2-btn primary" href={workshopLoginUrl(src)} target="_blank" rel="noopener">在新窗口登录</a>
            <button type="button" className="v2-btn" onClick={recheck}>我已登录，重试</button>
          </span>
        </div>
      </div>
    )
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <iframe
        key={src}
        ref={frameRef}
        title={`Workshop ${label}`}
        src={src}
        sandbox={WORKSHOP_FRAME_SANDBOX}
        data-testid={`workshop-page-${page}`}
        data-workshop-src={src}
        allow="clipboard-read; clipboard-write; fullscreen"
        style={{ width: '100%', height: '100%', border: 0, background: 'transparent', display: 'block' }}
      />
      {!ready && (
        <div data-testid="workshop-page-loading" style={{ ...gateStyle, pointerEvents: slow ? 'auto' : 'none' }}>
          <span>正在打开 Workshop 的{label}…</span>
          {slow && (
            <a href={src.replace('embed=1&', '')} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--fp-link)' }}>
              比平时慢，在新窗口打开
            </a>
          )}
        </div>
      )}
    </div>
  )
}

export const workshopRegistration: EntityRegistration<WorkshopEntity> = {
  resolver,
  renderer: { type: 'workshop', Editor },
  label: 'Workshop',
  icon: WORKSHOP_PAGE_ICON.projects,
}
