import React, { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  Building2,
  Copy,
  Inbox,
  ExternalLink,
  Keyboard,
  LayoutPanelLeft,
  Maximize2,
  Minimize2,
  NotebookPen,
  Plus,
  RefreshCw,
  Rows3,
  Square,
} from 'lucide-react'
import { ccApi, type CcSessionMeta, type SessionContext } from '../../api/ccClient'
import { copyText } from '../../lib/copyText'
import { openInOmnidashboard } from '../../lib/surface'
import { usePanels } from '../../stores/panelsStore'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { useSessionControls, type SessionControls } from './sessionControlsStore'
import { commandForSiblingSession } from './sessionCommand'
import './sessionCompanion.css'
import { workshopSurfaceUrl } from '../../lib/workshopSurface'
import { ensureDesktopSession } from '../../lib/desktopSession'
import { useCompanionNav } from './companionNavStore'
import { usePanelVisible } from '../../shell/panelRuntime'
import { TurnResultsPage } from './TurnResults'
import { useTranslation } from 'react-i18next'

import { STUDIO_TAB_TITLE } from '../studio'

const StudioFrame = React.lazy(() => import('../studio/StudioFrame'))

const WorkshopSurface = React.lazy(async () => {
  const module = await import('../studio_reader/WorkshopSurface')
  return { default: module.WorkshopSurface }
})

export type CompanionMode = 'embedded' | 'tab' | 'surface'
// 已提交页(2026-08-14 用户裁定)移除: 提交是会话行为, 侧栏只置顶展示本会话提交的材料。
// 2026-11-16: 概览、Todo 两页移出侧栏, 只留会话、画布; 默认页改为会话。
// 项目 Wiki / 审阅队列 / Todo 组件仍在项目页、审阅页使用。
// 2026-11-16: Studio 从主区域页签改为侧栏第三页(会话、画布、Studio)。
// 2026-11-16 晚: 拆成两处 —— 侧栏(在右)Studio 页只放 3D 办公楼(view=scene), 点它时在主区域(在左)打开 /
// 切到 Studio 聊天页签(view=chat, 单例); 聊天页签在前台时侧栏照挂、停在 Studio 页。
// 2026-11-20: 加「结果」页 —— 一轮结束后自动切过来, 嵌出这一轮提到的画布和本机文件。
export type CompanionPage = 'multiagent' | 'notes' | 'results' | 'studio'
export const DEFAULT_COMPANION_PAGE: CompanionPage = 'multiagent'

const MultiagentView = React.lazy(async () => {
  const module = await import('../multiagent/MultiagentView')
  return { default: module.default }
})

export interface SessionCompanionProps {
  /** 空串 = 没有会话(主区域只开着 Studio 场景页签、之前没开过会话页签): 只有会话列表与 Studio 可用。 */
  sessionId: string
  /** 主区域当前是 Studio 场景页签: 侧栏停在 Studio 页(聊天)。 */
  studioTabActive?: boolean
  alive?: boolean
  mode?: CompanionMode
  onHeaderDoubleClick?: () => void
  headerActions?: React.ReactNode
}

export function companionSurfaceUrl(
  sessionId: string,
  origin: string = window.location.origin,
): string {
  const url = new URL('/', origin)
  url.searchParams.set('surface', 'session-companion')
  url.searchParams.set('id', sessionId)
  return url.toString()
}

/** Dashboard and overlay both host the same Workshop Canvas. */
export function sessionNotesUrl(
  sessionId: string,
  origin: string = window.location.origin,
): string {
  return new URL(workshopSurfaceUrl('canvas', { session: sessionId }), origin).toString()
}

export function companionHostTabId(sessionId: string, mode: CompanionMode): string | null {
  if (mode === 'surface') return null
  return mode === 'tab' ? `cc_companion:${sessionId}` : `cc_session:${sessionId}`
}

export async function requestSiblingSession(
  controls: Pick<SessionControls, 'newSession'> | undefined,
  createFallback: () => Promise<CcSessionMeta>,
  openFallback: (session: CcSessionMeta) => void,
): Promise<void> {
  if (controls) {
    await controls.newSession()
    return
  }
  const session = await createFallback()
  openFallback(session)
}

function shortId(value: string, size = 10): string {
  if (!value) return '—'
  return value.length <= size ? value : value.slice(0, size)
}

function PageButton({
  active,
  icon,
  children,
  onClick,
  testid,
  disabled,
}: {
  active: boolean
  icon: React.ReactNode
  children: React.ReactNode
  onClick: () => void
  testid: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      className="cccp-nav-btn"
      data-active={active ? '1' : '0'}
      data-testid={testid}
      disabled={disabled}
      onClick={onClick}
    >
      {icon}
      <span>{children}</span>
    </button>
  )
}

export default function SessionCompanion({
  sessionId,
  studioTabActive = false,
  alive,
  mode = 'embedded',
  onHeaderDoubleClick,
  headerActions,
}: SessionCompanionProps) {
  const openTab = usePanels((state) => state.openTab)
  const [page, setPage] = useState<CompanionPage>(studioTabActive ? 'studio' : DEFAULT_COMPANION_PAGE)
  const [meta, setMeta] = useState<CcSessionMeta | null>(null)
  const [context, setContext] = useState<SessionContext | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [creatingSibling, setCreatingSibling] = useState(false)
  // 侧栏全屏(2026-08-16 用户裁定): CSS overlay 盖满视口, 取消后原位原样恢复 —— DOM 不动。
  const [fullscreen, setFullscreen] = useState(false)
  const [notesMounted, setNotesMounted] = useState(false)
  const [notesReady, setNotesReady] = useState(false)
  const [notesSlow, setNotesSlow] = useState(false)
  const [notesError, setNotesError] = useState('')
  const [notesReload, setNotesReload] = useState(0)
  // Studio 页(WebGL 场景较重): 首次进入才挂载; 同一实例里切走只隐藏。Studio 不分会话,
  // 换会话不重挂。所在面板不可见(独立「会话信息」页签被切走)时卸载, 只留可见的那一个。
  const [studioMounted, setStudioMounted] = useState(false)
  const panelVisible = usePanelVisible()
  const controls = useSessionControls((state) => state.bySession[sessionId])
  const navRequest = useCompanionNav((state) => state)
  const { t: tResults } = useTranslation('results')


  const refresh = useCallback(async (quiet = false) => {
    if (!sessionId) {
      setContext(null)
      setMeta(null)
      setError('')
      setLoading(false)
      return
    }
    if (!quiet) setLoading(true)
    const [contextResult, sessionsResult] = await Promise.allSettled([
      ccApi.context(sessionId),
      ccApi.list(),
    ])
    const nextContext = contextResult.status === 'fulfilled' ? contextResult.value : null
    const nextMeta = sessionsResult.status === 'fulfilled'
      ? sessionsResult.value.find((item) => item.id === sessionId) || null
      : null
    setContext(nextContext)
    setMeta(nextMeta)

    const failures: string[] = []
    if (contextResult.status === 'rejected') failures.push('会话绑定')
    setError(failures.length ? `${failures.join('、')}暂时不可用` : '')
    setLoading(false)
  }, [sessionId])

  // 2026-11-16 轮询减负: 会话绑定(context)只剩「新建同类会话」兜底工作目录在用,
  // 挂载 / 换会话时取一次即可。会话元信息(list)里只有终止按钮的可用态跟着存活变化:
  // 编辑器登记了 controls 时以 controls.alive 为准, 不再定时取; 没有编辑器(独立页签 /
  // 外链面)时才按原节奏只刷新 list。
  useEffect(() => {
    void refresh()
  }, [refresh])

  const hasLiveControls = Boolean(controls)
  useEffect(() => {
    if (hasLiveControls || !sessionId) return
    let disposed = false
    const interval = window.setInterval(
      () => {
        if (document.visibilityState !== 'visible') return
        void ccApi.list().then(
          (sessions) => { if (!disposed) setMeta(sessions.find((item) => item.id === sessionId) || null) },
          () => { if (!disposed) setMeta(null) },
        )
      },
      alive === false ? 30_000 : 15_000,
    )
    return () => {
      disposed = true
      window.clearInterval(interval)
    }
  }, [alive, hasLiveControls, sessionId])

  useEffect(() => {
    setNotesMounted(false)
    setNotesReady(false)
    setNotesSlow(false)
    setNotesError('')
    setNotesReload(0)
  }, [sessionId])

  useEffect(() => {
    if (page === 'notes') setNotesMounted(true)
  }, [page, sessionId])

  useEffect(() => {
    if (!panelVisible) setStudioMounted(false)
    else if (page === 'studio') setStudioMounted(true)
  }, [page, panelVisible])

  // 主区域切到 Studio 场景页签: 侧栏停到 Studio 页(聊天)。只改页, 不反过来开场景页签。
  useEffect(() => {
    if (studioTabActive) setPage('studio')
  }, [studioTabActive])

  useEffect(() => {
    if (!navRequest.sessionId || navRequest.sessionId !== sessionId || !navRequest.page) return
    if (navRequest.page === 'notes') setNotesMounted(true)
    setPage(navRequest.page)
  }, [navRequest.nonce, navRequest.page, navRequest.sessionId, sessionId])

  const handleNotesReady = useCallback(() => {
    setNotesReady(true)
    setNotesSlow(false)
    setNotesError('')
  }, [])

  // 2026-08-18 统一应用鉴权: 画布 iframe 报 auth-required 时不再把登录表单留在画布里。
  // 桌面运行时先补 mint 一次会话; 拿不到(或浏览器环境)就顶层打开一次画布入口,
  // 让用户在顶层完成网关登录拿到 cookie, 回到侧栏重试即可。
  const reauthNotes = useCallback(async () => {
    setNotesError('')
    const ok = await ensureDesktopSession().catch(() => false)
    if (ok) {
      setNotesReady(false)
      setNotesSlow(false)
      setNotesReload((value) => value + 1)
      return
    }
    window.open(sessionNotesUrl(sessionId), '_blank', 'noopener')
  }, [sessionId])

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data
      if (!data || typeof data !== 'object') return
      if (data.type === 'omni-notes-ready') {
        handleNotesReady()
        return
      }
      if (data.type === 'omni-notes-error') {
        setNotesError(String(data.error || '画布打开失败'))
        setNotesSlow(true)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [handleNotesReady])

  useEffect(() => {
    if (page !== 'notes' || notesReady) return
    const timer = window.setTimeout(() => setNotesSlow(true), 8_000)
    return () => window.clearTimeout(timer)
  }, [notesReady, notesReload, page])

  const displayTitle = meta?.display_title || meta?.provider_title || shortId(sessionId, 12)
  const ptyMissing = !loading && (!meta || !meta.alive)

  const openCompanionTab = useCallback((split: boolean) => {
    openTab(
      { type: 'cc_companion', id: sessionId },
      `会话信息 · ${displayTitle}`,
      undefined,
      split
        ? { direction: 'right', referenceTabId: `cc_session:${sessionId}` }
        : undefined,
    )
  }, [displayTitle, openTab, sessionId])

  const copyCompanionLink = useCallback(() => {
    void copyText(companionSurfaceUrl(sessionId)).then((ok) => {
      if (!ok) setError('复制失败：浏览器剪贴板权限受限')
    })
  }, [sessionId])

  const createSibling = useCallback(async () => {
    if (creatingSibling || controls?.creating) return
    setCreatingSibling(true)
    setActionError('')
    try {
      await requestSiblingSession(
        controls,
        () => ccApi.create({
          cmd: commandForSiblingSession({
            provider: meta?.provider,
            cmd: meta?.cmd,
          }),
          cwd: meta?.cwd || context?.context.cwd || undefined,
        }),
        (next) => {
          const label = next.provider === 'shell' ? '纯 CLI' : (next.provider || 'CLI')
          const title = `${label} · ${shortId(next.id, 8)}`
          if (mode === 'surface') openInOmnidashboard('cc_session', next.id, undefined, title)
          else openTab({ type: 'cc_session', id: next.id }, title)
        },
      )
    } catch (cause) {
      setActionError(`新建会话失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setCreatingSibling(false)
    }
  }, [context?.context.cwd, controls, creatingSibling, meta?.cmd, meta?.cwd, mode, openTab])

  const killSession = useCallback(async () => {
    if (!window.confirm(`终止会话 ${shortId(sessionId, 8)}？`)) return
    if (controls) {
      controls.kill()
      return
    }
    try {
      await ccApi.kill(sessionId)
      await refresh()
    } catch (cause) {
      setError(`终止失败：${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }, [controls, refresh, sessionId])

  const selectPage = (next: CompanionPage) => {
    if (next === 'notes') setNotesMounted(true)
    if (next === 'studio') setStudioMounted(true)
    setPage(next)
    // 只有「用户点了 Studio 页签」这个动作才在主区域打开 / 切到场景页签(单例);
    // 侧栏停在 Studio 页时切换会话页签不会被抢回场景。独立外链面没有主区域, 不开。
    if (next === 'studio' && mode !== 'surface') openTab({ type: 'studio', id: 'main' }, STUDIO_TAB_TITLE)
    // 2026-08-12 用户裁决: 切页签不再自动加宽侧栏(宽度尽量不变), 宽窄只由用户拖/双击决定
  }

  const actionItems: KebabItem[] = [
    ...(mode === 'embedded' ? [
      {
        label: '拆分到右侧',
        icon: <LayoutPanelLeft size={15} />,
        testid: 'companion-split',
        onClick: () => openCompanionTab(true),
      },
      {
        label: '作为页签打开',
        icon: <Rows3 size={15} />,
        testid: 'companion-open-tab',
        onClick: () => openCompanionTab(false),
      },
    ] : []),
    {
      label: '复制会话 ID',
      icon: <Copy size={15} />,
      onClick: () => { void copyText(sessionId) },
    },
    {
      label: '复制独立链接',
      icon: <Copy size={15} />,
      testid: 'companion-copy-link',
      onClick: copyCompanionLink,
    },
    ...(controls ? [
      {
        label: `快捷键模式：${controls.windowsKeys ? 'Windows' : '终端 / Bash'}`,
        icon: <Keyboard size={15} />,
        testid: 'cc-session-toggle-key-mode',
        onClick: controls.toggleKeyMode,
      },
      {
        label: '终端快捷键速查',
        icon: <Keyboard size={15} />,
        testid: 'cc-session-shortcuts',
        onClick: controls.showShortcuts,
      },
    ] : []),
    {
      label: '刷新',
      icon: <RefreshCw size={15} />,
      onClick: () => { void refresh() },
    },
  ]

  return (
    <section
      className="cccp-root"
      data-testid="session-companion"
      data-mode={mode}
      data-page={page}
      data-session-id={sessionId}
      data-fullscreen={fullscreen ? '1' : undefined}
    >
      <nav
        className="cccp-nav"
        aria-label="会话信息页面"
        onDoubleClick={onHeaderDoubleClick}
        title={mode === 'embedded' ? '双击切换宽/窄' : undefined}
        data-testid={mode === 'embedded' ? 'companion-width-toggle' : undefined}
      >
        <div className="cccp-nav-pages">
          <PageButton active={page === 'multiagent'} icon={<Activity size={14} />} onClick={() => selectPage('multiagent')} testid="companion-nav-multiagent">
            会话
          </PageButton>
          <PageButton active={page === 'notes'} icon={<NotebookPen size={14} />} onClick={() => selectPage('notes')} testid="companion-nav-notes" disabled={!sessionId}>
            画布
          </PageButton>
          <PageButton active={page === 'results'} icon={<Inbox size={14} />} onClick={() => selectPage('results')} testid="companion-nav-results" disabled={!sessionId}>
            {tResults('nav')}
          </PageButton>
          <PageButton active={page === 'studio'} icon={<Building2 size={14} />} onClick={() => selectPage('studio')} testid="companion-nav-studio">
            Studio
          </PageButton>
        </div>
        <div className="cccp-nav-actions">
          {headerActions}
          <button
            type="button"
            className="cccp-icon-btn"
            title="在同一目录新建同类会话"
            aria-label="新会话"
            data-testid="cc-session-new"
            disabled={!sessionId || creatingSibling || controls?.creating}
            aria-busy={creatingSibling || controls?.creating ? 'true' : undefined}
            onClick={() => { void createSibling() }}
          >
            {creatingSibling || controls?.creating
              ? <RefreshCw size={14} className="cccp-spinning" />
              : <Plus size={14} />}
          </button>
          <button
            type="button"
            className="cccp-icon-btn"
            title={fullscreen ? '取消全屏（回到原位置）' : '侧栏全屏'}
            aria-label={fullscreen ? '取消全屏' : '侧栏全屏'}
            data-testid="cc-session-fullscreen"
            onClick={() => setFullscreen((value) => !value)}
          >
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          {sessionId && <KebabMenu testid="cc-session-actions" items={actionItems} />}
          <button
            type="button"
            className="cccp-icon-btn cccp-kill"
            title="终止会话"
            aria-label="终止会话"
            disabled={ptyMissing || controls?.alive === false}
            data-cc-kill
            onClick={() => { void killSession() }}
          >
            <Square size={13} />
          </button>
        </div>
      </nav>

      {(actionError || error) && (
        <div className="cccp-notice" data-error="1" role="status">
          {actionError || error}
        </div>
      )}

      <div className="cccp-body">
        {page === 'multiagent' && (
          <div className="cccp-page cccp-page-multiagent" data-testid="companion-multiagent">
            <React.Suspense fallback={<div className="cccp-empty">正在载入活跃会话…</div>}>
              <MultiagentView />
            </React.Suspense>
          </div>
        )}

        <div
          className="cccp-page cccp-page-notes"
          data-testid="companion-notes"
          hidden={page !== 'notes'}
        >
          {notesMounted && (
            <div
              className="cccp-notes-host"
              data-testid="companion-notes-host"
              data-session-id={sessionId}
            >
              <React.Suspense fallback={<div className="cccp-empty">正在打开画布…</div>}>
                <WorkshopSurface
                  key={`${sessionId}:${notesReload}`}
                  kind="canvas"
                  sessionId={sessionId}
                  title="札记"
                  onReady={handleNotesReady}
                  onError={(error) => {
                    setNotesError(error)
                    if (error === 'auth-required') setNotesSlow(true)
                  }}
                />
              </React.Suspense>
            </div>
          )}
          {notesMounted && !notesReady && (
            <div className="cccp-notes-gate" data-testid="companion-notes-loading">
              <RefreshCw className="cccp-notes-spin" size={18} />
              <strong>{notesError === 'auth-required' ? '画布需要重新认证' : (notesError || (notesSlow ? '画布加载时间过长' : '正在打开画布…'))}</strong>
              <span>{notesSlow ? '可在这里重载；也可以先用独立页面打开，不必一直等空白画布。' : '画布就绪后会自动出现。'}</span>
              {notesSlow && (
                <div className="cccp-notes-recovery">
                  <button
                    type="button"
                    onClick={() => {
                      setNotesReady(false)
                      setNotesSlow(false)
                      setNotesError('')
                      setNotesReload((value) => value + 1)
                    }}
                  >
                    <RefreshCw size={14} /> 重新加载
                  </button>
                  <a href={sessionNotesUrl(sessionId)} target="_blank" rel="noreferrer">
                    <ExternalLink size={14} /> 独立打开
                  </a>
                  {notesError === 'auth-required' && (
                    <button type="button" data-testid="companion-notes-reauth" onClick={() => { void reauthNotes() }}>
                      <RefreshCw size={14} /> 重新认证
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {page === 'results' && (
          <div className="cccp-page cccp-page-results">
            <TurnResultsPage sessionId={sessionId} />
          </div>
        )}

        <div
          className="cccp-page cccp-page-studio"
          data-testid="companion-studio"
          hidden={page !== 'studio'}
        >
          {studioMounted && panelVisible && (
            <React.Suspense fallback={<div className="cccp-empty">正在打开 Studio…</div>}>
              <StudioFrame testid="companion-studio-host" view="scene" />
            </React.Suspense>
          )}
        </div>
      </div>
    </section>
  )
}
