import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Copy,
  ExternalLink,
  FolderKanban,
  GitBranch,
  Keyboard,
  LayoutPanelLeft,
  ListTree,
  Maximize2,
  Plus,
  RefreshCw,
  Rows3,
  Square,
  Unlink,
} from 'lucide-react'
import { ccApi, type CcSessionMeta, type SessionContext } from '../../api/ccClient'
import {
  reviewstageApi,
  type ReviewReadback,
  type ReviewReadbackItem,
} from '../../api/reviewstageClient'
import { api, type TraceDetail, type TraceEvent } from '../../api/client'
import { copyText } from '../../lib/copyText'
import { openInOmnidashboard } from '../../lib/surface'
import { usePanels } from '../../stores/panelsStore'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { useReviewStream } from '../review/streamStore'
import { useSessionControls, type SessionControls } from './sessionControlsStore'
import { commandForSiblingSession } from './sessionCommand'
import './sessionCompanion.css'

const ReviewMaterialPanel = React.lazy(async () => {
  const module = await import('../review_material')
  return { default: module.ReviewMaterialPanel }
})

export type CompanionMode = 'embedded' | 'tab' | 'surface'
export type CompanionPage = 'multiagent' | 'overview' | 'materials' | 'trace'
export const DEFAULT_COMPANION_PAGE: CompanionPage = 'multiagent'

const MultiagentView = React.lazy(async () => {
  const module = await import('../multiagent/MultiagentView')
  return { default: module.default }
})

const MATERIAL_PAGE_SIZE = 8
const TRACE_PAGE_SIZE = 24

export interface SessionCompanionProps {
  sessionId: string
  alive?: boolean
  mode?: CompanionMode
  onHeaderDoubleClick?: () => void
  onRequestWide?: () => void
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

export function dedupeReviewReadback(items: ReviewReadbackItem[]): ReviewReadbackItem[] {
  const byId = new Map<string, ReviewReadbackItem>()
  for (const item of items) {
    if (item?.id && !byId.has(item.id)) byId.set(item.id, item)
  }
  return Array.from(byId.values())
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

function titleTail(value: string | null | undefined): string {
  if (!value) return '未绑定'
  const tail = value.split('/').filter(Boolean).pop() || value
  return tail.replace(/^\[\d{4}-\d{2}-\d{2}\]/, '') || value
}

function providerLabel(provider: string | null | undefined): string {
  if (!provider) return 'CLI'
  if (provider === 'claude_code' || provider === 'claude-code') return 'Claude Code'
  if (provider === 'codex') return 'Codex'
  if (provider === 'codebuddy') return 'CodeBuddy'
  if (provider === 'opencode') return 'OpenCode'
  if (provider === 'kimi') return 'Kimi'
  if (provider === 'shell') return 'Shell'
  return provider
}

function formatTime(value: string | number | null | undefined): string {
  if (value == null || value === '') return '—'
  const date = typeof value === 'number'
    ? new Date(value < 1e12 ? value * 1000 : value)
    : new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(undefined, {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
}

function traceSummary(event: TraceEvent): string {
  const payload = event.payload || {}
  const value = (
    payload.summary
    || payload.title
    || payload.task_desc
    || payload.tool_name
    || payload.command
    || payload.message
    || payload.path
  )
  if (value != null && String(value).trim()) return String(value).trim().slice(0, 180)
  const keys = Object.keys(payload)
  return keys.length ? keys.slice(0, 4).join(' · ') : event.event_type
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

function Pager({
  page,
  total,
  pageSize,
  onChange,
}: {
  page: number
  total: number
  pageSize: number
  onChange: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  if (pages <= 1) return null
  return (
    <div className="cccp-pager" aria-label="分页">
      <button type="button" disabled={page <= 0} onClick={() => onChange(page - 1)} aria-label="上一页">
        <ChevronLeft size={14} />
      </button>
      <span>{page + 1} / {pages}</span>
      <button type="button" disabled={page >= pages - 1} onClick={() => onChange(page + 1)} aria-label="下一页">
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

export default function SessionCompanion({
  sessionId,
  alive,
  mode = 'embedded',
  onHeaderDoubleClick,
  onRequestWide,
  headerActions,
}: SessionCompanionProps) {
  const openTab = usePanels((state) => state.openTab)
  const [page, setPage] = useState<CompanionPage>(DEFAULT_COMPANION_PAGE)
  const [selectedMaterialId, setSelectedMaterialId] = useState<string | null>(null)
  const [meta, setMeta] = useState<CcSessionMeta | null>(null)
  const [context, setContext] = useState<SessionContext | null>(null)
  const [readback, setReadback] = useState<ReviewReadback | null>(null)
  const [trace, setTrace] = useState<TraceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [creatingSibling, setCreatingSibling] = useState(false)
  const [materialPage, setMaterialPage] = useState(0)
  const [tracePage, setTracePage] = useState(0)
  const reviewVersion = useReviewStream((state) => state.version)
  const reviewConnected = useReviewStream((state) => state.connected)
  const controls = useSessionControls((state) => state.bySession[sessionId])

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

    const provider = (
      nextContext?.context.provider
      || nextMeta?.provider
      || 'claude_code'
    )
    const providerSessionId = (
      nextContext?.context.provider_session_id
      || nextMeta?.provider_session_id
      || nextContext?.context.claude_session_id
      || nextMeta?.claude_session_id
      || sessionId
    )
    const traceId = nextContext?.context.trace_id || sessionId
    const [readbackResult, traceResult] = await Promise.allSettled([
      reviewstageApi.readback({
        provider,
        session_id: providerSessionId,
        limit: 200,
      }),
      api.trace(traceId),
    ])
    if (readbackResult.status === 'fulfilled') setReadback(readbackResult.value)
    if (traceResult.status === 'fulfilled') setTrace(traceResult.value)

    const failures: string[] = []
    if (contextResult.status === 'rejected') failures.push('会话绑定')
    if (readbackResult.status === 'rejected') failures.push('已提交材料')
    if (traceResult.status === 'rejected') failures.push('轨迹')
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

  useEffect(() => useReviewStream.getState().acquire(), [])

  // 材料一提交，Review WebSocket 的 version 当场变化；readback 立即重拉。
  // 15 秒可见页轮询只在事件流断线/关联晚到时兜底。
  useEffect(() => {
    if (reviewVersion > 0) void refresh(true)
  }, [refresh, reviewVersion])

  const materials = useMemo(
    () => dedupeReviewReadback(readback?.items || []),
    [readback],
  )
  const activePlan = context?.context.active_plan || meta?.active_plan || null
  const project = (
    context?.context.project
    || context?.context.plan_meta?.project
    || null
  ) as string | null
  const traceId = context?.context.trace_id || sessionId
  const events = trace?.events || []
  const displayTitle = meta?.display_title || meta?.provider_title || shortId(sessionId, 12)
  const ptyMissing = !loading && (!meta || !meta.alive)

  useEffect(() => {
    const maxPage = Math.max(0, Math.ceil(materials.length / MATERIAL_PAGE_SIZE) - 1)
    if (materialPage > maxPage) setMaterialPage(maxPage)
  }, [materialPage, materials.length])

  useEffect(() => {
    const maxPage = Math.max(0, Math.ceil(events.length / TRACE_PAGE_SIZE) - 1)
    if (tracePage > maxPage) setTracePage(maxPage)
  }, [events.length, tracePage])

  const openEntity = useCallback((
    type: 'plan' | 'project' | 'review_material' | 'trace',
    id: string,
    title: string,
  ) => {
    if (mode === 'surface') {
      openInOmnidashboard(type, id, undefined, title)
      return
    }
    openTab({ type, id }, title)
  }, [mode, openTab])

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
    setSelectedMaterialId(null)
    setPage(next)
    if (next === 'materials') onRequestWide?.()
  }

  const selectMaterial = (item: ReviewReadbackItem) => {
    setSelectedMaterialId(item.id)
    setPage('materials')
    onRequestWide?.()
  }

  const visibleMaterials = materials.slice(
    materialPage * MATERIAL_PAGE_SIZE,
    (materialPage + 1) * MATERIAL_PAGE_SIZE,
  )
  const visibleEvents = events.slice(
    tracePage * TRACE_PAGE_SIZE,
    (tracePage + 1) * TRACE_PAGE_SIZE,
  )

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
      label: '复制独立链接',
      icon: <Copy size={15} />,
      testid: 'companion-copy-link',
      onClick: copyCompanionLink,
    },
    {
      label: '在新窗口打开',
      icon: <Maximize2 size={15} />,
      testid: 'companion-open-window',
      onClick: () => window.open(companionSurfaceUrl(sessionId), '_blank', 'noopener,noreferrer'),
    },
    {
      label: 'Multiagent',
      icon: <Activity size={15} />,
      onClick: () => {
        if (mode === 'surface') openInOmnidashboard('multiagent', 'main', undefined, 'Multiagent')
        else openTab({ type: 'multiagent', id: 'main' }, 'Multiagent')
      },
    },
    {
      label: '审阅',
      icon: <ClipboardCheck size={15} />,
      onClick: () => {
        if (mode === 'surface') openInOmnidashboard('review_queue', 'main', undefined, '审阅')
        else openTab({ type: 'review_queue', id: 'main' }, '审阅')
      },
    },
    ...(controls ? [
      {
        label: `快捷键模式：${controls.windowsKeys ? 'Windows' : '终端 / Bash'}`,
        icon: <Keyboard size={15} />,
        testid: 'cc-session-toggle-key-mode',
        onClick: controls.toggleKeyMode,
      },
      {
        label: '全选终端内容',
        icon: <Copy size={15} />,
        testid: 'cc-session-select-all',
        onClick: controls.selectAll,
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

  if (selectedMaterialId) {
    const selected = materials.find((item) => item.id === selectedMaterialId)
    return (
      <section className="cccp-root cccp-reading" data-testid="session-companion" data-mode={mode}>
        <header className="cccp-reading-head">
          <button
            type="button"
            className="cccp-text-btn"
            onClick={() => setSelectedMaterialId(null)}
            data-testid="companion-material-back"
          >
            <ArrowLeft size={14} /> 已提交
          </button>
          <div className="cccp-reading-title" title={selected?.title || selectedMaterialId}>
            {selected?.title || selectedMaterialId}
          </div>
          <button
            type="button"
            className="cccp-icon-btn"
            title="在完整页签中打开"
            aria-label="在完整页签中打开"
            onClick={() => openEntity('review_material', selectedMaterialId, selected?.title || selectedMaterialId)}
          >
            <ExternalLink size={14} />
          </button>
        </header>
        <div className="cccp-reader">
          <React.Suspense fallback={<div className="cccp-empty">正在加载材料阅读视图…</div>}>
            <ReviewMaterialPanel id={selectedMaterialId} embedded />
          </React.Suspense>
        </div>
      </section>
    )
  }

  return (
    <section
      className="cccp-root"
      data-testid="session-companion"
      data-mode={mode}
      data-page={page}
      data-session-id={sessionId}
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
            Multiagent
          </PageButton>
          <PageButton active={page === 'overview'} icon={<FolderKanban size={14} />} onClick={() => selectPage('overview')} testid="companion-nav-overview">
            概览
          </PageButton>
          <PageButton active={page === 'materials'} icon={<BookOpen size={14} />} onClick={() => selectPage('materials')} testid="companion-nav-materials">
            已提交 <span className="cccp-count">{materials.length}</span>
          </PageButton>
          <PageButton active={page === 'trace'} icon={<ListTree size={14} />} onClick={() => selectPage('trace')} testid="companion-nav-trace">
            轨迹 <span className="cccp-count">{events.length}</span>
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

      {(actionError || error || ptyMissing || (!reviewConnected && reviewVersion > 0)) && (
        <div className="cccp-notice" data-error="1" role="status">
          {actionError || error || (ptyMissing ? 'PTY 已不在运行；材料与轨迹仍可查看。' : '材料实时流已断开，正在短轮询补偿。')}
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

        {page === 'overview' && (
          <div className="cccp-page" data-testid="companion-overview">
            <section className="cccp-section">
              <div className="cccp-section-head">
                <span>绑定</span>
                <span>Plan / Project</span>
              </div>
              <button
                type="button"
                className="cccp-binding"
                data-bound={activePlan ? '1' : '0'}
                disabled={!activePlan}
                onClick={() => activePlan && openEntity('plan', activePlan, titleTail(activePlan))}
              >
                {activePlan ? <CheckCircle2 size={15} /> : <Unlink size={15} />}
                <span className="cccp-binding-label">PLAN</span>
                <strong title={activePlan || undefined}>{titleTail(activePlan)}</strong>
                {activePlan && <ExternalLink size={12} />}
              </button>
              <button
                type="button"
                className="cccp-binding"
                data-bound={project ? '1' : '0'}
                disabled={!project}
                onClick={() => project && openEntity('project', project, project)}
              >
                {project ? <CheckCircle2 size={15} /> : <Unlink size={15} />}
                <span className="cccp-binding-label">PROJECT</span>
                <strong title={project || undefined}>{project || '未绑定'}</strong>
                {project && <ExternalLink size={12} />}
              </button>
            </section>

            <div className="cccp-stat-grid">
              <button type="button" onClick={() => selectPage('materials')}>
                <BookOpen size={16} />
                <strong>{materials.length}</strong>
                <span>已提交材料</span>
              </button>
              <button type="button" onClick={() => selectPage('trace')}>
                <GitBranch size={16} />
                <strong>{events.length}</strong>
                <span>轨迹事件</span>
              </button>
            </div>

            <section className="cccp-section">
              <div className="cccp-section-head">
                <span>会话</span>
                <span>{shortId(sessionId, 12)}</span>
              </div>
              <dl className="cccp-meta">
                <div><dt>Provider</dt><dd>{providerLabel(context?.context.provider || meta?.provider)}</dd></div>
                <div><dt>Native ID</dt><dd title={context?.context.provider_session_id || meta?.provider_session_id || undefined}>{shortId(context?.context.provider_session_id || meta?.provider_session_id || '', 16)}</dd></div>
                <div><dt>Trace</dt><dd title={traceId}>{shortId(traceId, 16)}</dd></div>
                <div><dt>Started</dt><dd>{formatTime(context?.context.started_at || meta?.started_at)}</dd></div>
                <div className="cccp-meta-wide"><dt>CWD</dt><dd title={context?.context.cwd || meta?.cwd || undefined}>{context?.context.cwd || meta?.cwd || '—'}</dd></div>
              </dl>
            </section>
          </div>
        )}

        {page === 'materials' && (
          <div className="cccp-page" data-testid="companion-materials">
            <div className="cccp-page-head">
              <div>
                <strong>已提交材料</strong>
                <span>正式关联 + 对话中精确发送的 ID / 链接 / 名称</span>
              </div>
              <Pager page={materialPage} total={materials.length} pageSize={MATERIAL_PAGE_SIZE} onChange={setMaterialPage} />
            </div>
            {visibleMaterials.length === 0 ? (
              <div className="cccp-empty">
                <BookOpen size={20} />
                <strong>还没有检测到已提交材料</strong>
                <span>发送材料 ID、材料链接或完整名称后会自动出现。</span>
              </div>
            ) : (
              <div className="cccp-material-list">
                {visibleMaterials.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    className="cccp-material-row"
                    onClick={() => selectMaterial(item)}
                    data-testid={`companion-material-${item.id}`}
                  >
                    <span className="cccp-material-icon"><BookOpen size={15} /></span>
                    <span className="cccp-material-main">
                      <strong>{item.title}</strong>
                      <span>
                        {item.kind} · {item.status} · {formatTime(item.created_at)}
                      </span>
                      <code title={item.id}>{item.id}</code>
                    </span>
                    <span className="cccp-association">
                      {item.association === 'session_binding' ? '已关联' : '对话提及'}
                    </span>
                    <ChevronRight size={14} />
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {page === 'trace' && (
          <div className="cccp-page" data-testid="companion-trace">
            <div className="cccp-page-head">
              <div>
                <strong>会话轨迹</strong>
              </div>
              <div className="cccp-page-actions">
                <Pager page={tracePage} total={events.length} pageSize={TRACE_PAGE_SIZE} onChange={setTracePage} />
                <button
                  type="button"
                  className="cccp-text-btn"
                  onClick={() => openEntity('trace', traceId, `Trace · ${shortId(traceId, 12)}`)}
                >
                  完整轨迹 <ExternalLink size={12} />
                </button>
              </div>
            </div>
            {visibleEvents.length === 0 ? (
              <div className="cccp-empty">
                <ListTree size={20} />
                <strong>暂时没有轨迹事件</strong>
                <span>会话事件进入统一 trace 后会在这里聚合。</span>
              </div>
            ) : (
              <ol className="cccp-trace-list">
                {visibleEvents.map((event) => (
                  <li key={event.id}>
                    <span className="cccp-trace-node" />
                    <div>
                      <div className="cccp-trace-line">
                        <strong>{event.event_type}</strong>
                        <span>{event.source}</span>
                        <time>{formatTime(event.timestamp)}</time>
                      </div>
                      <p>{traceSummary(event)}</p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
