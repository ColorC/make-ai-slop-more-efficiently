import React, { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  BookOpen,
  Copy,
  ExternalLink,
  Keyboard,
  LayoutPanelLeft,
  Maximize2,
  Minimize2,
  NotebookPen,
  LayoutGrid,
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

const WorkshopSurface = React.lazy(async () => {
  const module = await import('../studio_reader/WorkshopSurface')
  return { default: module.WorkshopSurface }
})

// 未绑定项目的概览才回退到审阅队列。绑定后只挂唯一的项目 Wiki 首页。
const TaskPanel = React.lazy(() => import('../task/TaskPanel'))

const ReviewBoard = React.lazy(async () => {
  const module = await import('../review_queue/ReviewBoard')
  return { default: module.ReviewBoard }
})

const ProjectDetail = React.lazy(async () => {
  const module = await import('../project/ProjectDetail')
  return { default: module.default }
})

export type CompanionMode = 'embedded' | 'tab' | 'surface'
// 已提交页(2026-08-14 用户裁定)移除: 提交是会话行为, 侧栏只置顶展示本会话提交的材料。
// 2026-09-04: 专注页并回 Todo(studio) —— 用户「todo 和专注差距太小了, 要么就
// 合并到 todo 吧」。消息队列现在是 Todo 页顶部那一段, 不再单独占一个页签。
export type CompanionPage = 'multiagent' | 'overview' | 'notes' | 'studio'
export const DEFAULT_COMPANION_PAGE: CompanionPage = 'overview'

const MultiagentView = React.lazy(async () => {
  const module = await import('../multiagent/MultiagentView')
  return { default: module.default }
})

export interface SessionCompanionProps {
  sessionId: string
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
}: {
  active: boolean
  icon: React.ReactNode
  children: React.ReactNode
  onClick: () => void
  testid: string
}) {
  return (
    <button
      type="button"
      className="cccp-nav-btn"
      data-active={active ? '1' : '0'}
      data-testid={testid}
      onClick={onClick}
    >
      {icon}
      <span>{children}</span>
    </button>
  )
}

export default function SessionCompanion({
  sessionId,
  alive,
  mode = 'embedded',
  onHeaderDoubleClick,
  headerActions,
}: SessionCompanionProps) {
  const openTab = usePanels((state) => state.openTab)
  const [page, setPage] = useState<CompanionPage>(DEFAULT_COMPANION_PAGE)
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
  const controls = useSessionControls((state) => state.bySession[sessionId])
  const navRequest = useCompanionNav((state) => state)

  const refresh = useCallback(async (quiet = false) => {
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

  useEffect(() => {
    let disposed = false
    const run = async (quiet = false) => {
      if (!disposed) await refresh(quiet)
    }
    void run()
    const interval = window.setInterval(
      () => {
        if (document.visibilityState === 'visible') void run(true)
      },
      alive === false ? 30_000 : 15_000,
    )
    return () => {
      disposed = true
      window.clearInterval(interval)
    }
  }, [alive, refresh])

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
  const project = context?.context.project?.trim() || ''

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
    setPage(next)
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
          <PageButton active={page === 'overview'} icon={<BookOpen size={14} />} onClick={() => selectPage('overview')} testid="companion-nav-overview">
            概览
          </PageButton>
          <PageButton active={page === 'notes'} icon={<NotebookPen size={14} />} onClick={() => selectPage('notes')} testid="companion-nav-notes">
            画布
          </PageButton>
          <PageButton active={page === 'studio'} icon={<LayoutGrid size={14} />} onClick={() => selectPage('studio')} testid="companion-nav-studio">
            Todo
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
            disabled={creatingSibling || controls?.creating}
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
          <KebabMenu testid="cc-session-actions" items={actionItems} />
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

        {page === 'studio' && (
          <div className="cccp-page cccp-page-studio" data-testid="companion-studio">
            <React.Suspense fallback={<div className="cccp-empty">正在载入 Todo…</div>}>
              <TaskPanel sessionId={sessionId} />
            </React.Suspense>
          </div>
        )}

        {page === 'overview' && (
          <div className="cccp-page cccp-page-overview" data-testid="companion-overview">
            {project ? (
              <div className="cccp-wiki-host" data-testid="companion-project-wiki" data-project={project}>
                <React.Suspense fallback={<div className="cccp-empty">正在载入项目 Wiki…</div>}>
                  <ProjectDetail key={project} entity={{ id: project }} wikiOnly />
                </React.Suspense>
              </div>
            ) : (
              <React.Suspense fallback={<div className="cccp-empty">正在载入审阅队列…</div>}>
                <ReviewBoard />
              </React.Suspense>
            )}
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
      </div>
    </section>
  )
}
