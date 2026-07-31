// 线程监控面板 — 也被 SurfaceShell「执行者(控制台)」复用(entities/controller 持有)。
// 2026-07-19 蓝图 G 重置(阶段四第四波): 玻璃卡+裸 hex(#f0b429/#241d0b/#3a5cff 等)清零 →
//   会话卡=厚框纸件(2px 白框);运行状态=`.v2-status` 六态徽章(与全站同一套状态语言);
//   provider tag=黄铜/白线描边身份标记;fresh 高亮改 data-fresh 属性驱动(CSS,不再内联色);
//   accent 实心按钮海(打开/采纳/载入)清零 → 幽灵小按钮,每卡一个白线描边主操作;
//   弹层=实底纸件(无玻璃);执行者 select=虚线测量件。数据接线 / testid / 交互全保留。
import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ccApi, type CcSessionMeta } from '../../api/ccClient'
import { ccChatApi, type CcChatSessionMeta, type CcChatProvider, type ImportableSession } from '../../api/ccChatClient'
import { usePanels } from '../../stores/panelsStore'
import { useControllerView } from './viewStore'
import { relTimeEn as relTime } from '../../lib/time'
import { openChatInVscode } from '../../lib/surface'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { Activity, Circle, Folder } from 'lucide-react'
import { Code2, Download, MessageSquare, RefreshCw, Plus, RotateCcw, Search, Trash2 } from 'lucide-react'

type NewSessionKind = CcChatProvider | 'plain_cli' | 'claude_cli' | 'codex_cli' | 'codebuddy_cli' | 'kimi_cli' | 'opencode_cli' | 'powershell'
const SESSION_RENDER_BATCH = 36

// 与 Lofa 同一心智模型：真实对话和远程 CLI 共用一个新建入口。
const SESSION_OPTIONS: Array<{ value: NewSessionKind; label: string }> = [
  { value: 'plain_cli', label: '纯 CLI（手动唤起）' },
  { value: 'claude_cli', label: 'Claude CLI' },
  { value: 'codex_cli', label: 'Codex CLI' },
  { value: 'codebuddy_cli', label: 'CodeBuddy CLI' },
  { value: 'kimi_cli', label: 'Kimi CLI' },
  { value: 'opencode_cli', label: 'OpenCode CLI' },
  { value: 'powershell', label: 'PowerShell' },
]

const SESSION_LABELS: Record<NewSessionKind, string> = {
  controller: '总控对话',
  claude_code: 'Claude 对话',
  codex: 'Codex 对话',
  codebuddy: 'CodeBuddy 对话',
  kimi: 'Kimi 对话',
  opencode: 'OpenCode 对话',
  omni_agent: 'OmniAgent 对话',
  plain_cli: '纯 CLI',
  claude_cli: 'Claude CLI',
  codex_cli: 'Codex CLI',
  codebuddy_cli: 'CodeBuddy CLI',
  kimi_cli: 'Kimi CLI',
  opencode_cli: 'OpenCode CLI',
  powershell: 'PowerShell',
}

type ThreadRow = {
  kind: 'chat' | 'pty'
  id: string
  title: string
  provider: string
  status: string
  activePlan: string | null
  startedAt: number
  lastMessage?: string
  alive: boolean
  working: boolean
  subscribers: number
  cwd: string
  providerSessionId?: string | null
}

type ManagedSession =
  | { kind: 'chat'; meta: CcChatSessionMeta }
  | { kind: 'pty'; meta: CcSessionMeta }

interface QuickPreviewData {
  title: string
  source: string
  provider: string
  cwd: string
  sessionId: string
  messages: Array<{ role: 'user' | 'assistant'; text: string }>
  top: number
  left: number
}

function planShortName(planId: string | null | undefined): string {
  if (!planId) return 'no-plan'
  const last = planId.split('/').pop() || planId
  return last.replace(/^\[\d{4}-\d{2}-\d{2}\]/, '')
}

export function normalizeSessionTitle(value: string | null | undefined): string {
  if (!value) return ''
  return value
    .split(/\r?\n/)
    .map((part) => part.trim())
    .find(Boolean)
    ?.replace(/\s+/g, ' ') || ''
}

function sessionFallbackTitle(provider: string, cwd: string): string {
  const cwdName = cwd.split(/[\\/]/).filter(Boolean).pop() || cwd || '未知目录'
  return `${providerLabel(provider)} · ${cwdName}`
}

function activeSessionTitle(item: ImportableSession, managedRow: ThreadRow | null): string {
  return normalizeSessionTitle(item.digest?.title)
    || normalizeSessionTitle(item.preview)
    || normalizeSessionTitle(item.last_user)
    || normalizeSessionTitle(managedRow?.title)
    || sessionFallbackTitle(item.provider, item.cwd)
}

function SessionIdentity({
  title,
  sessionId,
  status,
  fresh = false,
  actions,
}: {
  title: string
  sessionId: string
  status: React.ReactNode
  fresh?: boolean
  actions?: React.ReactNode
}) {
  return (
    <div className="tm-session-head">
      <div className="tm-session-badges">
        {fresh && <span className="tm-fresh"><Circle size={8} fill="currentColor" stroke="none" aria-hidden />24h</span>}
        {status}
      </div>
      <div className="tm-session-identity">
        <div className="tm-title tm-session-title" data-testid="session-title" title={title}>{title}</div>
        <div className="tm-session-id" data-testid="session-id" title={sessionId}>
          <span className="tm-session-id-label">ID</span>
          <span>{sessionId}</span>
        </div>
      </div>
      {actions && <div className="tm-session-actions">{actions}</div>}
    </div>
  )
}

function providerForPty(m: CcSessionMeta): string {
  if (m.provider) return m.provider
  const command = String(m.cmd?.[0] || 'claude').toLowerCase()
  return command.includes('codex') ? 'codex'
    : command.includes('codebuddy') || command.includes('cbc') ? 'codebuddy'
      : command.includes('kimi') ? 'kimi'
      : command.includes('opencode') ? 'opencode'
        : command.includes('powershell') || command.includes('pwsh') ? 'shell'
          : 'claude_code'
}

function isPlainLiveCli(m: CcSessionMeta): boolean {
  return m.alive && m.status !== 'recoverable' && providerForPty(m) === 'shell'
}

function isRestorableAgentCli(m: CcSessionMeta, openTabIds: Set<string>): boolean {
  return m.alive
    && m.status !== 'recoverable'
    && providerForPty(m) !== 'shell'
    && !openTabIds.has(`cc_session:${m.id}`)
}

function isContentfulRemoteOpenCode(m: CcSessionMeta): boolean {
  return m.alive
    && m.status !== 'recoverable'
    && providerForPty(m) === 'opencode'
    && Boolean(m.has_user_turn)
}

function providerLabel(provider: string): string {
  const labels: Record<string, string> = {
    claude_code: 'Claude', codex: 'Codex', codebuddy: 'CodeBuddy', kimi: 'Kimi', opencode: 'OpenCode',
    shell: '纯 CLI', powershell: 'PowerShell', controller: '总控', omni_agent: 'OmniAgent',
  }
  return labels[provider] || provider || 'Agent'
}

function matchesSessionQuery(query: string, ...values: unknown[]): boolean {
  if (!query) return true
  return values.some((value) => String(value || '').toLocaleLowerCase().includes(query))
}

function threadMatchesQuery(thread: ThreadRow, query: string): boolean {
  return matchesSessionQuery(
    query,
    thread.id,
    thread.providerSessionId,
    thread.title,
    thread.provider,
    thread.status,
    thread.activePlan,
    thread.cwd,
    thread.lastMessage,
  )
}

function importableMatchesQuery(item: ImportableSession, query: string): boolean {
  return matchesSessionQuery(
    query,
    item.session_id,
    item.provider,
    item.cwd,
    item.preview,
    item.last_user,
    item.last_did,
    item.digest?.project,
    item.digest?.plan,
    item.digest?.title,
    item.digest?.last_step,
  )
}

const managedKey = (provider: string, sessionId: string) => `${provider}:${sessionId}`

// 多 agent 完成感知(后端 /active 的 status): 一眼看出每个 agent 在跑还是干完了。
// 状态语言统一 = blueprint .v2-status 六态(working=warn / done=ok / waiting=hollow / idle=idle)。
const RUN_STATUS: Record<string, { label: string; cls: string }> = {
  working: { label: '运行中', cls: 'st-warn' },
  done: { label: '已完成', cls: 'st-ok' },
  waiting: { label: '等待输入', cls: 'st-hollow' },
  idle: { label: '空闲', cls: 'st-idle' },
}

function StatusBadge({ status }: { status?: string }) {
  const m = RUN_STATUS[status || 'idle'] || RUN_STATUS.idle
  return (
    <span className={`v2-status ${m.cls}`} style={{ flex: 'none' }} data-testid="run-status" data-status={status || 'idle'}>
      <i className="led" aria-hidden />{m.label}
    </span>
  )
}

function chatToRow(m: CcChatSessionMeta): ThreadRow {
  const title = normalizeSessionTitle(m.name)
    || normalizeSessionTitle(m.first_message)
    || normalizeSessionTitle(m.last_message)
    || (m.active_plan ? planShortName(m.active_plan) : '')
    || sessionFallbackTitle(m.provider || 'claude_code', m.cwd)
  return {
    kind: 'chat',
    id: m.id,
    title,
    provider: m.provider || 'claude_code',
    status: m.running ? 'working' : m.alive ? 'waiting' : 'done',
    activePlan: m.active_plan,
    startedAt: m.started_at,
    lastMessage: m.last_message || m.first_message,
    alive: m.alive,
    working: Boolean(m.running),
    subscribers: Number(m.subscribers || 0),
    cwd: m.cwd,
    providerSessionId: m.claude_session_id,
  }
}

function sessionTabTitle(title: string, sessionId: string): string {
  const shortId = sessionId.slice(0, 8)
  return shortId ? `${title} \u00b7 ${shortId}` : title
}

function ptyToRow(m: CcSessionMeta): ThreadRow {
  const status = m.working ? 'working' : m.alive ? 'waiting' : 'done'
  const cwdName = m.cwd.split(/[\\/]/).filter(Boolean).pop() || m.cwd
  const planName = m.active_plan ? planShortName(m.active_plan) : ''
  const provider = providerForPty(m)
  const title = normalizeSessionTitle(m.display_title)
    || normalizeSessionTitle(m.provider_title)
    || planName
    || sessionFallbackTitle(provider, cwdName)
  return {
    kind: 'pty',
    id: m.id,
    title,
    provider,
    status,
    activePlan: m.active_plan || null,
    startedAt: m.started_at,
    alive: m.alive,
    working: Boolean(m.working),
    subscribers: Number(m.subscribers || 0),
    cwd: m.cwd,
    providerSessionId: m.provider_session_id || m.claude_session_id,
  }
}

export default function ThreadMonitorPanel() {
  const [threads, setThreads] = useState<ThreadRow[]>([])
  const [activeSessions, setActiveSessions] = useState<ImportableSession[]>([])
  const [importableSessions, setImportableSessions] = useState<ImportableSession[]>([])
  const [sessionQuery, setSessionQuery] = useState('')
  const [visibleSessionLimit, setVisibleSessionLimit] = useState(SESSION_RENDER_BATCH)
  // 驾驶舱自管会话按 claude_session_id 并进同一对话列表 —— 让它们也带摘要/项目/状态,
  // 而不是被去重踢到下面那个裸 hash 列表(用户 2026-06-13: 新建的对话在主列表里看不到)。
  const [managedByTranscript, setManagedByTranscript] = useState<Map<string, ManagedSession>>(new Map())
  const [ptySessions, setPtySessions] = useState<CcSessionMeta[]>([])
  const [quickPreview, setQuickPreview] = useState<QuickPreviewData | null>(null)
  const quickPreviewCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newSessionKind, setNewSessionKind] = useState<NewSessionKind>('codex_cli')
  // #2 载入已有会话(Claude Code / Codex)。
  const [importOpen, setImportOpen] = useState(false)
  const [importItems, setImportItems] = useState<ImportableSession[]>([])
  const [importLoading, setImportLoading] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [importingId, setImportingId] = useState<string | null>(null)
  const [adoptingId, setAdoptingId] = useState<string | null>(null)
  const [openingExistingId, setOpeningExistingId] = useState<string | null>(null)
  const [restoringAll, setRestoringAll] = useState(false)
  const [cleanupOpen, setCleanupOpen] = useState(false)
  const [cleaningPlainCli, setCleaningPlainCli] = useState(false)
  const [endingPtyId, setEndingPtyId] = useState<string | null>(null)
  const [loadNote, setLoadNote] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)
  const openTabBackground = usePanels((s) => s.openTabBackground)
  const openTabIds = usePanels((s) => s.tabs.map((tab) => tab.id))
  // 窄窗口(vscode 侧边栏)适配: 容器宽度 < 360 视为窄, 用 compact 驱动样式(按钮换行/行单列/隐藏长说明)。
  const rootRef = useRef<HTMLDivElement>(null)
  const [compact, setCompact] = useState(false)
  useEffect(() => {
    const el = rootRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((es) => setCompact(es[0].contentRect.width < 360))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  useEffect(() => () => {
    if (quickPreviewCloseTimer.current) clearTimeout(quickPreviewCloseTimer.current)
  }, [])
  useEffect(() => {
    setVisibleSessionLimit(SESSION_RENDER_BATCH)
  }, [sessionQuery])

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const [chat, pty, active, importable] = await Promise.all([
        ccChatApi.list({ limit: 80, includeArchived: false }).catch(() => [] as CcChatSessionMeta[]),
        ccApi.list().catch(() => [] as CcSessionMeta[]),
        ccChatApi.activeSessions(7 * 86400, 80).catch(() => [] as ImportableSession[]),
        ccChatApi.importable(120).catch(() => [] as ImportableSession[]),
      ])
      // claude_session_id → 自管 chat 会话(渲染时拿权威 plan + 自管 id 供"打开")
      const managed = new Map<string, ManagedSession>()
      for (const c of chat) {
        if (c.claude_session_id) {
          const entry: ManagedSession = { kind: 'chat', meta: c }
          managed.set(managedKey(c.provider || 'claude_code', c.claude_session_id), entry)
          managed.set(`sid:${c.claude_session_id}`, entry)
        }
      }
      for (const p of pty) {
        const nativeId = p.provider_session_id || p.claude_session_id
        if (nativeId) {
          const entry: ManagedSession = { kind: 'pty', meta: p }
          managed.set(managedKey(providerForPty(p), nativeId), entry)
          managed.set(`sid:${nativeId}`, entry)
        }
      }
      setManagedByTranscript(managed)
      setPtySessions(pty)
      setImportableSessions(importable)
      // 统一对话列表 = 所有有 transcript 的对话(自管 + 外部), /active 已按最近活动排好序。
      // 自管会话的 transcript 也在 ~/.claude/projects, 一样被 /active 摘要, 所以不再过滤掉它们。
      setActiveSessions(active)
      // 线程列表只留"还没 transcript"的(全新没说话的 chat / pty), 避免和上面重复。
      const activeSids = new Set(active.map((a) => a.session_id))
      const hasTranscript = (sid: string | null | undefined) => Boolean(sid && activeSids.has(sid))
      const rows = [
        ...chat.filter((c) => !hasTranscript(c.claude_session_id)).map(chatToRow),
        ...pty.filter((p) => !hasTranscript(p.provider_session_id || p.claude_session_id)).map(ptyToRow),
      ].sort((a, b) => b.startedAt - a.startedAt)
      setThreads(rows)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function openThread(thread: ThreadRow) {
    try {
      if (thread.kind === 'pty' && !thread.alive) {
        const fresh = await ccApi.resume(thread.id)
        usePanels.getState().closeTab(`cc_session:${thread.id}`)
        openTab({ type: 'cc_session', id: fresh.id }, `${fresh.cmd?.[0] || 'CLI'} · ${fresh.id.slice(0, 8)}`)
      } else if (thread.kind === 'chat') {
        if (!thread.providerSessionId) {
          throw new Error('这条旧网页会话没有可供 CLI resume 的原生会话 ID')
        }
        const fresh = await ccApi.resumeProvider({
          provider: thread.provider,
          provider_session_id: thread.providerSessionId,
          cwd: thread.cwd,
        })
        openTab({ type: 'cc_session', id: fresh.id }, sessionTabTitle(thread.title, fresh.id))
      } else {
        openTab({ type: 'cc_session', id: thread.id }, sessionTabTitle(thread.title, thread.id))
      }
    } catch (cause) {
      setError(`打开失败: ${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }

  function showQuickPreview(
    element: HTMLElement,
    data: Omit<QuickPreviewData, 'top' | 'left'>,
  ) {
    if (quickPreviewCloseTimer.current) clearTimeout(quickPreviewCloseTimer.current)
    const rect = element.getBoundingClientRect()
    const width = Math.min(500, Math.max(320, window.innerWidth - 24))
    const left = rect.right + width + 12 <= window.innerWidth
      ? rect.right + 8
      : Math.max(12, rect.left - width - 8)
    const top = Math.max(12, Math.min(rect.top, window.innerHeight - 440))
    setQuickPreview({ ...data, top, left })
  }

  function hideQuickPreviewSoon() {
    if (quickPreviewCloseTimer.current) clearTimeout(quickPreviewCloseTimer.current)
    quickPreviewCloseTimer.current = setTimeout(() => setQuickPreview(null), 120)
  }

  async function onCreate(kind: NewSessionKind = newSessionKind) {
    setError(null)
    try {
      const cliCommands: Partial<Record<NewSessionKind, string[] | undefined>> = {
        plain_cli: ['powershell', '-NoLogo'],
        claude_cli: undefined,
        codex_cli: ['codex'],
        codebuddy_cli: ['codebuddy'],
        kimi_cli: ['kimi'],
        opencode_cli: ['opencode'],
        powershell: ['powershell'],
      }
      if (kind in cliCommands) {
        const meta = await ccApi.create({ cmd: cliCommands[kind] })
        openTab({ type: 'cc_session', id: meta.id }, `${SESSION_LABELS[kind]} · ${meta.id.slice(0, 8)}`)
      } else {
        const meta = await ccChatApi.create({ provider: kind as CcChatProvider })
        if (kind === 'controller') {
          useControllerView.getState().setView('chat')
          openTab({ type: 'controller', id: 'main' }, '总控')
        } else {
          openTab({ type: 'cc_session', id: meta.id }, `${meta.name || meta.provider || '对话'} · ${meta.id.slice(-6)}`)
        }
      }
      void load()
    } catch (cause) {
      setError(`新建失败: ${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }

  async function restoreAllCli() {
    if (restoringAll) return
    setRestoringAll(true)
    setError(null)
    try {
      const openIds = new Set(usePanels.getState().tabs.map((tab) => tab.id))
      for (const meta of ptySessions) {
        if (isRestorableAgentCli(meta, openIds)) {
          openTabBackground({ type: 'cc_session', id: meta.id }, sessionTabTitle(ptyToRow(meta).title, meta.id))
        }
      }
      void load()
    } finally {
      setRestoringAll(false)
    }
  }

  const plainLiveCliSessions = ptySessions.filter(isPlainLiveCli)
  const currentOpenTabIds = new Set(openTabIds)
  const remoteOpenCodeSessions = ptySessions
    .filter(isContentfulRemoteOpenCode)
    .sort((a, b) => b.started_at - a.started_at)
  const remoteOpenCodeIds = new Set(remoteOpenCodeSessions.map((meta) => meta.id))
  const remainingThreads = threads.filter((thread) => !remoteOpenCodeIds.has(thread.id))
  const normalizedSessionQuery = sessionQuery.trim().toLocaleLowerCase()
  const remoteProviderSessionIds = new Set(
    remoteOpenCodeSessions
      .map((meta) => meta.provider_session_id || meta.claude_session_id)
      .filter((id): id is string => Boolean(id)),
  )
  const knownProviderSessionIds = new Set([
    ...activeSessions.map((item) => item.session_id),
    ...threads.map((thread) => thread.providerSessionId).filter((id): id is string => Boolean(id)),
    ...remoteProviderSessionIds,
  ])
  const filteredRemoteOpenCodeSessions = remoteOpenCodeSessions.filter((meta) => threadMatchesQuery(ptyToRow(meta), normalizedSessionQuery))
  const filteredActiveSessions = activeSessions.filter((item) => (
    !remoteProviderSessionIds.has(item.session_id)
    && importableMatchesQuery(item, normalizedSessionQuery)
  ))
  const filteredRemainingThreads = remainingThreads.filter((thread) => threadMatchesQuery(thread, normalizedSessionQuery))
  const filteredHistoricalSessions = normalizedSessionQuery
    ? importableSessions.filter((item) => (
        !knownProviderSessionIds.has(item.session_id)
        && importableMatchesQuery(item, normalizedSessionQuery)
      ))
    : []
  const sessionMatchCount = (
    filteredRemoteOpenCodeSessions.length
    + filteredActiveSessions.length
    + filteredRemainingThreads.length
    + filteredHistoricalSessions.length
  )
  let renderBudget = visibleSessionLimit
  const visibleRemoteOpenCodeSessions = filteredRemoteOpenCodeSessions.slice(0, renderBudget)
  renderBudget -= visibleRemoteOpenCodeSessions.length
  const visibleActiveSessions = filteredActiveSessions.slice(0, Math.max(0, renderBudget))
  renderBudget -= visibleActiveSessions.length
  const visibleRemainingThreads = filteredRemainingThreads.slice(0, Math.max(0, renderBudget))
  renderBudget -= visibleRemainingThreads.length
  const visibleHistoricalSessions = filteredHistoricalSessions.slice(0, Math.max(0, renderBudget))
  const visibleSessionCount = (
    visibleRemoteOpenCodeSessions.length
    + visibleActiveSessions.length
    + visibleRemainingThreads.length
    + visibleHistoricalSessions.length
  )
  const restorableCliCount = ptySessions.filter((meta) => (
    isRestorableAgentCli(meta, currentOpenTabIds)
  )).length

  function openRemoteOpenCodeSessions() {
    for (const meta of remoteOpenCodeSessions) {
      openTabBackground({ type: 'cc_session', id: meta.id }, sessionTabTitle(ptyToRow(meta).title, meta.id))
    }
    const focus = remoteOpenCodeSessions[0]
    if (focus) openTab({ type: 'cc_session', id: focus.id }, sessionTabTitle(ptyToRow(focus).title, focus.id))
  }

  async function endPtySession(id: string) {
    if (endingPtyId) return
    setEndingPtyId(id)
    setError(null)
    try {
      await ccApi.kill(id)
      usePanels.getState().closeTab(`cc_session:${id}`)
      await load()
    } catch (cause) {
      setError(`结束 CLI 失败: ${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setEndingPtyId(null)
    }
  }

  async function cleanupPlainCliSessions() {
    if (cleaningPlainCli) return
    setCleaningPlainCli(true)
    setError(null)
    const failed: string[] = []
    for (const meta of plainLiveCliSessions) {
      try {
        await ccApi.kill(meta.id)
        usePanels.getState().closeTab(`cc_session:${meta.id}`)
      } catch {
        failed.push(meta.id)
      }
    }
    await load()
    setCleaningPlainCli(false)
    if (failed.length > 0) {
      setError(`有 ${failed.length} 个纯 CLI 未能结束: ${failed.map((id) => id.slice(0, 8)).join('、')}`)
    } else {
      setCleanupOpen(false)
    }
  }

  async function openImport() {
    setImportOpen(true)
    setImportLoading(true)
    setImportError(null)
    try {
      setImportItems(await ccChatApi.importable(40))
    } catch (e) {
      setImportError(e instanceof Error ? e.message : String(e))
    } finally {
      setImportLoading(false)
    }
  }

  // A1(用户明示 2026-06-06): "载入"= 把这段已有对话的真实内容作为【总控对话的前文】注入,
  // 不是另起一个会话(那样既看不到真实历史, 外部会话还常 resume 失败)。
  async function onImport(item: ImportableSession) {
    setImportingId(item.session_id)
    setImportError(null)
    setLoadNote(null)
    try {
      const res = await ccChatApi.loadContext(item)
      if (!res.ok) {
        setImportError(
          res.reason === 'no_active_controller'
            ? '总控已迁到 chatui ——「载入为前文」待重新接入 chatui 侧注入接口, 暂不可用。'
            : `载入失败: ${res.reason || '未知原因'}`,
        )
        return
      }
      setImportOpen(false)
      // 切到总控对话, 让用户看到载入的前文 + 总控的简短确认。
      useControllerView.getState().setView('chat')
      openTab({ type: 'controller', id: 'main' }, '总控')
      setLoadNote(
        `已把这段对话(${res.message_count ?? 0} 条)载入为总控前文${res.truncated ? '(内容较长, 已截断尾部)' : ''} —— 在总控对话里可见`,
      )
    } catch (e) {
      setImportError(e instanceof Error ? e.message : String(e))
    } finally {
      setImportingId(null)
    }
  }

  // #2 接管式采纳: resume 这段别处会话当 subagent(总控可驱动/你可接管), 打开成 chat 页签。
  // 主“打开”应始终在 Dashboard 内恢复会话，不能依赖 VSCode WebView host bridge。
  // fork/resume 保留原 transcript；采纳为 subagent 等管理动作继续放在 More。
  async function openExistingSession(item: ImportableSession) {
    setOpeningExistingId(item.session_id)
    setError(null)
    try {
      const m = await ccApi.resumeProvider({
        provider_session_id: item.session_id,
        provider: item.provider,
        cwd: item.cwd,
      })
      openTab(
        { type: 'cc_session', id: m.id },
        `${providerLabel(item.provider)} CLI · ${item.session_id.slice(0, 8)}`,
      )
    } catch (e) {
      setError(`打开失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setOpeningExistingId(null)
    }
  }

  async function onAdopt(item: ImportableSession) {
    setAdoptingId(item.session_id)
    setError(null)
    try {
      const m = await ccChatApi.create({ adopt_session_id: item.session_id, provider: item.provider, cwd: item.cwd })
      openTab({ type: 'cc_session', id: m.id }, `采纳 · ${item.provider === 'codex' ? 'Codex' : 'Claude'} · ${item.session_id.slice(0, 6)}`)
    } catch (e) {
      setError(`采纳失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setAdoptingId(null)
    }
  }

  return (
    <div ref={rootRef} className="tm-root" data-testid="thread-monitor-panel">
      {/* 无标题头(Linear 风): 不再放"对话/执行者"标题, 页签已标识。仅留右对齐控件工具条。 */}
      <div className="tm-bar">
        <div className={`tm-controls${compact ? ' wrap' : ''}`}>
          <label className="tm-session-search">
            <Search size={13} aria-hidden />
            <input
              type="search"
              value={sessionQuery}
              onChange={(event) => setSessionQuery(event.target.value)}
              placeholder="搜索标题 / ID / 项目 / 目录…"
              aria-label="搜索会话"
              data-testid="thread-session-search"
            />
          </label>
          <span className="tm-search-count" data-testid="thread-session-search-count">
            {sessionMatchCount} 个会话
          </span>
          <select
            className="tm-select"
            value={newSessionKind}
            onChange={(e) => setNewSessionKind(e.target.value as NewSessionKind)}
            data-testid="thread-new-provider"
            aria-label="选择执行者"
          >
            {SESSION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button type="button" className="ct-newbtn" onClick={() => { void onCreate() }} data-testid="thread-new-session">
            <Plus size={13} aria-hidden />新建
          </button>
          {remoteOpenCodeSessions.length > 0 && (
            <button
              type="button"
              className="ct-newbtn"
              onClick={openRemoteOpenCodeSessions}
              data-testid="thread-open-remote-opencode"
            >
              <Code2 size={13} aria-hidden />打开远程 OpenCode {remoteOpenCodeSessions.length}
            </button>
          )}
          {restorableCliCount > 0 && (
            <button type="button" className="ct-newbtn" onClick={() => { void restoreAllCli() }} disabled={restoringAll} data-testid="thread-restore-all">
              <RotateCcw size={13} aria-hidden />{restoringAll ? '打开中…' : `打开后台 CLI ${restorableCliCount}`}
            </button>
          )}
          <KebabMenu testid="thread-actions" items={[
            { label: '打开活跃会话 / Multiagent', icon: <Activity size={15} />, testid: 'thread-open-multiagent', onClick: () => openTab({ type: 'multiagent', id: 'main' }, '活跃会话') },
            ...(plainLiveCliSessions.length > 0 ? [{
              label: `清理后台纯 CLI（${plainLiveCliSessions.length}）`,
              icon: <Trash2 size={15} />,
              testid: 'thread-cleanup-plain-cli',
              danger: true,
              onClick: () => setCleanupOpen(true),
            }] : []),
            { label: '新建总控对话', icon: <MessageSquare size={15} />, testid: 'thread-new-controller', onClick: () => { void onCreate('controller') } },
            { label: '新建 Claude 对话', icon: <MessageSquare size={15} />, onClick: () => { void onCreate('claude_code') } },
            { label: '新建 Codex 对话', icon: <MessageSquare size={15} />, onClick: () => { void onCreate('codex') } },
            { label: '新建 CodeBuddy 对话', icon: <MessageSquare size={15} />, onClick: () => { void onCreate('codebuddy') } },
            { label: '新建 Kimi 对话', icon: <MessageSquare size={15} />, onClick: () => { void onCreate('kimi') } },
            { label: '新建 OpenCode 对话', icon: <MessageSquare size={15} />, onClick: () => { void onCreate('opencode') } },
            { label: '新建 OmniAgent 对话', icon: <MessageSquare size={15} />, onClick: () => { void onCreate('omni_agent') } },
            { label: '载入对话为前文', icon: <Download size={15} />, testid: 'thread-import-session', onClick: () => { void openImport() } },
            { label: '刷新', icon: <RefreshCw size={15} />, testid: 'thread-refresh', onClick: () => { void load() } },
          ] as KebabItem[]} />
        </div>
      </div>
      {error && <div className="tm-error">{error}</div>}
      {/* #1 修复: 载入错误以前只在弹窗里显示, 这里(其他目录的对话)点"载入"出错被吞掉了 → 看着像没反应。现在两处都显示。 */}
      {importError && <div className="tm-error" data-testid="import-error">{importError}</div>}
      {loadNote && <div className="tm-note" style={{ color: 'var(--fp-ok)' }} data-testid="load-note">{loadNote}</div>}
      {visibleRemoteOpenCodeSessions.length > 0 && (
        <div style={{ marginBottom: 12 }} data-testid="remote-opencode-sessions">
          <div className="tm-note" style={{ marginBottom: 8 }}>
            远程 OpenCode · 内容保存在服务器，可从任意电脑打开
          </div>
          <div className="tm-grid">
            {visibleRemoteOpenCodeSessions.map((meta) => {
              const runtime = Number(meta.subscribers || 0) > 0 ? 'attached' : 'detached'
              const cwdName = meta.cwd.split(/[\\/]/).filter(Boolean).pop() || meta.cwd
              return (
                <div
                  key={`remote-opencode-${meta.id}`}
                  className="tm-card"
                  data-testid="remote-opencode-row"
                  data-runtime={runtime}
                >
                  <div>
                    <SessionIdentity
                      title={normalizeSessionTitle(meta.display_title) || normalizeSessionTitle(meta.provider_title) || `OpenCode \u00b7 ${cwdName}`}
                      sessionId={meta.id}
                      status={<StatusBadge status={meta.working ? 'working' : 'waiting'} />}
                    />
                    <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                      <span className="tm-ptag">OpenCode</span>
                      <span className={`tm-source source-${runtime}`}>
                        {runtime === 'attached' ? '网页已连接' : '服务器保活'}
                      </span>
                    </div>
                    <div className="tm-doing">已有用户交互内容，可在当前网页继续查看和操作</div>
                    <div className="tm-meta">
                      {meta.cwd} · {relTime(meta.last_output_at || meta.started_at)} · {meta.id}
                    </div>
                  </div>
                  <div className="tm-foot">
                    <button
                      type="button"
                      className="ct-btn ct-btn-go"
                      data-testid={`remote-opencode-open-${meta.id}`}
                      onClick={() => { void openThread(ptyToRow(meta)) }}
                    >
                      打开
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
      {visibleActiveSessions.length > 0 && (
        <div style={{ marginBottom: 12 }} data-testid="active-sessions">
          <div className="tm-grid">
            {visibleActiveSessions.map((it) => {
              const fresh = (Date.now() / 1000 - (it.mtime || 0)) < 86400
              const managed = managedByTranscript.get(managedKey(it.provider, it.session_id))
                || managedByTranscript.get(`sid:${it.session_id}`)
              const managedRow = managed?.kind === 'chat' ? chatToRow(managed.meta)
                : managed?.kind === 'pty' ? ptyToRow(managed.meta)
                  : null
              const runtime = managed?.kind === 'pty'
                ? (!managed.meta.alive ? 'ended' : Number(managed.meta.subscribers || 0) > 0 ? 'attached' : 'detached')
                : managed ? 'dashboard' : 'external'
              const sourceLabel = runtime === 'attached' ? '网页已连接'
                : runtime === 'detached' ? '后台保活'
                  : runtime === 'dashboard' ? '可用 CLI 续接'
                    : runtime === 'ended' ? '可续接'
                      : it.status === 'working' ? '其他软件运行' : '其他软件会话'
              const planLabel = managedRow?.activePlan
                ? planShortName(managedRow.activePlan)
                : (it.digest?.plan && it.digest.plan !== '无' ? it.digest.plan : '')
              const previewMessages = it.recent_messages?.length
                ? it.recent_messages
                : [
                    ...(it.last_user ? [{ role: 'user' as const, text: it.last_user }] : []),
                    ...(it.last_did ? [{ role: 'assistant' as const, text: it.last_did }] : []),
                  ]
              const displayTitle = activeSessionTitle(it, managedRow)
              const previewData = {
                title: displayTitle,
                source: sourceLabel,
                provider: providerLabel(it.provider),
                cwd: it.cwd || '(未知目录)',
                sessionId: it.session_id,
                messages: previewMessages,
              }
              return (
                <div
                  key={`act-${it.provider}-${it.session_id}-${it.file}`}
                  className="tm-card"
                  data-testid="active-session-row"
                  data-fresh={fresh ? '1' : '0'}
                  data-owned={managed ? '1' : '0'}
                  data-runtime={runtime}
                  tabIndex={0}
                  onMouseEnter={(e) => showQuickPreview(e.currentTarget, previewData)}
                  onMouseLeave={hideQuickPreviewSoon}
                  onFocus={(e) => showQuickPreview(e.currentTarget, previewData)}
                  onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setQuickPreview(null) }}
                >
                  <SessionIdentity
                    title={displayTitle}
                    sessionId={it.session_id}
                    fresh={fresh}
                    status={<StatusBadge status={managedRow?.status || it.status} />}
                    actions={<KebabMenu testid="active-session-more" items={(managed
                      ? [{ label: '\u5728 VSCode \u6253\u5f00', icon: <Code2 size={15} />, testid: 'active-session-open-vscode', onClick: () => openChatInVscode(it.provider, it.cwd, it.session_id) }]
                      : [
                          { label: adoptingId === it.session_id ? '\u91c7\u7eb3\u4e2d\u2026' : '\u91c7\u7eb3\u4e3a subagent', icon: <MessageSquare size={15} />, testid: 'active-session-adopt', disabled: adoptingId === it.session_id, onClick: () => { void onAdopt(it) } },
                          { label: importingId === it.session_id ? '\u8f7d\u5165\u4e2d\u2026' : '\u8f7d\u5165\u4e3a\u524d\u6587', icon: <Download size={15} />, testid: 'active-session-load', disabled: importingId === it.session_id, onClick: () => { void onImport(it) } },
                        ]) as KebabItem[]} />}
                  />
                  <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span className={`tm-ptag${it.provider === 'codex' ? ' codex' : ''}`}>{providerLabel(it.provider)}</span>
                    <span className={`tm-source source-${runtime}`}>{sourceLabel}</span>
                  </div>
                  {(it.digest?.project || planLabel) && (
                    <div className="tm-meta" style={{ color: 'var(--fp-link)', marginTop: 8 }} data-testid="active-session-project">
                      <Folder size={12} aria-hidden style={{ verticalAlign: -2, marginRight: 4 }} />{it.digest?.project || '—'}{planLabel ? ` · ${planLabel}` : ''}
                    </div>
                  )}
                  <div className="tm-doing" data-testid="active-session-did" title={it.digest?.last_step || it.last_did || ''}>
                    {it.status === 'working' ? '正在做: ' : '最近一步: '}{it.digest?.last_step || it.last_did || '—'}
                  </div>
                  <div className="tm-meta">{it.cwd || '(未知目录)'} · {relTime(it.mtime)} · {it.session_id.slice(0, 12)}</div>
                  <div className="tm-foot">
                    {managedRow
                      ? <button type="button" className="ct-btn ct-btn-go" data-testid="active-session-open" onClick={() => { void openThread({ ...managedRow, title: displayTitle }) }} title="在 CLI 中 resume 此会话">打开 CLI</button>
                      : <button
                          type="button"
                          className="ct-btn ct-btn-go"
                          data-testid="active-session-open"
                          disabled={openingExistingId === it.session_id}
                          onClick={() => { void openExistingSession(it) }}
                          title="在 CLI 中 resume 此会话"
                        >{openingExistingId === it.session_id ? '唤起中…' : '打开 CLI'}</button>}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
      {loading && <div className="tm-empty">加载中...</div>}
      {!loading && !error && sessionMatchCount === 0 && <div className="tm-empty">{normalizedSessionQuery ? '没有匹配的会话' : '暂无会话'}</div>}
      {!loading && !error && visibleRemainingThreads.length > 0 && (
        <div className="tm-grid">
          <div className="tm-note" style={{ gridColumn: '1 / -1' }}>还没产生对话内容的会话(全新 / pty)</div>
          {visibleRemainingThreads.map((thread) => {
            const fresh = (Date.now() / 1000 - thread.startedAt) < 86400
            const runtime = thread.kind === 'pty'
              ? (!thread.alive ? 'ended' : thread.subscribers > 0 ? 'attached' : 'detached')
              : 'dashboard'
            const sourceLabel = runtime === 'attached' ? '网页已连接'
              : runtime === 'detached' ? '后台保活'
                : runtime === 'ended' ? '可续接' : '可用 CLI 续接'
            const previewData = {
              title: thread.title,
              source: sourceLabel,
              provider: providerLabel(thread.provider),
              cwd: thread.cwd || '(未知目录)',
              sessionId: thread.id,
              messages: thread.lastMessage
                ? [{ role: 'assistant' as const, text: thread.lastMessage }]
                : [],
            }
            return (
            <div
              key={`${thread.kind}-${thread.id}`}
              className="tm-card"
              data-testid="thread-monitor-row"
              data-fresh={fresh ? '1' : '0'}
              data-runtime={runtime}
              tabIndex={0}
              onMouseEnter={(e) => showQuickPreview(e.currentTarget, previewData)}
              onMouseLeave={hideQuickPreviewSoon}
              onFocus={(e) => showQuickPreview(e.currentTarget, previewData)}
              onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setQuickPreview(null) }}
            >
              <div style={{ minWidth: 0 }}>
                <SessionIdentity
                  title={thread.title}
                  sessionId={thread.id}
                  fresh={fresh}
                  status={<StatusBadge status={thread.status} />}
                />
                <div style={{ marginTop: 8 }}><span className={`tm-source source-${runtime}`}>{sourceLabel}</span></div>
                <div className="tm-meta">
                  {providerLabel(thread.provider)} {'\u00b7'} {thread.kind} {'\u00b7'} {planShortName(thread.activePlan)} {'\u00b7'} {relTime(thread.startedAt)}
                  {thread.lastMessage ? ` \u00b7 ${thread.lastMessage}` : ''}
                </div>
              </div>
              <div className="tm-foot">
                <button type="button" className="ct-btn" onClick={() => { void openThread(thread) }}>{thread.kind === 'pty' && !thread.alive ? '续接 CLI' : '打开 CLI'}</button>
                {thread.kind === 'pty' && thread.alive && (
                  <KebabMenu
                    testid={`thread-session-actions-${thread.id}`}
                    items={[{
                      label: endingPtyId === thread.id ? '结束中…' : '结束这个后台 CLI',
                      icon: <Trash2 size={15} />,
                      testid: `thread-kill-session-${thread.id}`,
                      danger: true,
                      disabled: Boolean(endingPtyId),
                      onClick: () => { void endPtySession(thread.id) },
                    }]}
                  />
                )}
              </div>
            </div>
            )
          })}
        </div>
      )}
      {!loading && !error && visibleHistoricalSessions.length > 0 && (
        <div className="tm-grid" data-testid="historical-session-results">
          <div className="tm-note" style={{ gridColumn: '1 / -1' }}>历史会话 · 打开即启动 CLI 并 resume</div>
          {visibleHistoricalSessions.map((item) => (
            <div
              key={`historical-${item.provider}-${item.session_id}-${item.file}`}
              className="tm-card"
              data-testid="historical-session-row"
            >
              <SessionIdentity
                title={activeSessionTitle(item, null)}
                sessionId={item.session_id}
                status={<StatusBadge status={item.status || 'idle'} />}
              />
              <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                <span className={`tm-ptag${item.provider === 'codex' ? ' codex' : ''}`}>{providerLabel(item.provider)}</span>
                <span className="tm-source source-ended">可用 CLI 续接</span>
              </div>
              <div className="tm-doing">{item.preview || item.last_user || '已有原生 CLI 会话'}</div>
              <div className="tm-meta">{item.cwd || '(未知目录)'} · {relTime(item.mtime)}</div>
              <div className="tm-foot">
                <button
                  type="button"
                  className="ct-btn ct-btn-go"
                  data-testid="historical-session-open"
                  disabled={openingExistingId === item.session_id}
                  onClick={() => { void openExistingSession(item) }}
                >
                  {openingExistingId === item.session_id ? '唤起中…' : '打开 CLI'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {!loading && !error && visibleSessionCount < sessionMatchCount && (
        <div className="tm-load-more">
          <button
            type="button"
            className="ct-btn"
            data-testid="thread-session-load-more"
            onClick={() => setVisibleSessionLimit((current) => current + SESSION_RENDER_BATCH)}
          >
            再显示 {Math.min(SESSION_RENDER_BATCH, sessionMatchCount - visibleSessionCount)} 个
          </button>
        </div>
      )}
      {cleanupOpen && (
        <div className="tm-modal-bg" data-testid="cleanup-plain-cli-modal" onMouseDown={(e) => { if (e.target === e.currentTarget && !cleaningPlainCli) setCleanupOpen(false) }}>
          <div className="tm-modal">
            <div className="tm-modal-head">
              <div className="tm-modal-title">结束后台纯 CLI</div>
              <button type="button" className="ct-btn" disabled={cleaningPlainCli} onClick={() => setCleanupOpen(false)}>取消</button>
            </div>
            <div className="tm-note" style={{ whiteSpace: 'normal' }}>
              只结束下面这些尚未唤起 agent 的纯终端。Codex、Claude、Kimi、OpenCode 会话不会被处理。
            </div>
            <div className="tm-modal-list">
              {plainLiveCliSessions.map((meta) => (
                <div key={meta.id} className="tm-import-row">
                  <div style={{ minWidth: 0 }}>
                    <div className="tm-title">纯 CLI · {meta.id.slice(0, 8)}</div>
                    <div className="tm-meta">{meta.cwd}</div>
                  </div>
                </div>
              ))}
            </div>
            <div className="tm-foot">
              <button
                type="button"
                className="ct-btn"
                data-testid="cleanup-plain-cli-confirm"
                disabled={cleaningPlainCli || plainLiveCliSessions.length === 0}
                onClick={() => { void cleanupPlainCliSessions() }}
              >
                {cleaningPlainCli ? '结束中…' : `结束 ${plainLiveCliSessions.length} 个纯 CLI`}
              </button>
            </div>
          </div>
        </div>
      )}
      {importOpen && (
        <div className="tm-modal-bg" data-testid="import-session-modal" onMouseDown={(e) => { if (e.target === e.currentTarget) setImportOpen(false) }}>
          <div className="tm-modal">
            <div className="tm-modal-head">
              <div className="tm-modal-title">载入已有对话为总控前文 · Claude Code / Codex</div>
              <button type="button" className="ct-btn" onClick={() => setImportOpen(false)}>关闭</button>
            </div>
            <div className="tm-note" style={{ whiteSpace: 'normal' }}>选一段本机已有对话, 把它的真实内容作为【前文/背景】插入当前总控对话(总控会看到并简短确认)。不另起会话。</div>
            {importError && <div className="tm-error">{importError}</div>}
            {importLoading && <div className="tm-empty">扫描中…</div>}
            {!importLoading && !importError && importItems.length === 0 && (
              <div className="tm-empty">没扫到可载入的历史会话(~/.claude/projects、~/.codex/sessions 近 90 天内)。</div>
            )}
            {!importLoading && importItems.length > 0 && (
              <div className="tm-modal-list">
                {importItems.map((item) => (
                  <div key={`${item.provider}-${item.session_id}-${item.file}`} className="tm-import-row" data-testid="import-session-row">
                    <div style={{ minWidth: 0 }}>
                      <div className="tm-title">
                        <span className={`tm-ptag${item.provider === 'codex' ? ' codex' : ''}`}>{item.provider === 'codex' ? 'Codex' : 'Claude'}</span>
                        {item.preview || item.session_id}
                      </div>
                      <div className="tm-meta">{item.cwd || '(未知目录)'} · {relTime(item.mtime)} · {item.session_id.slice(0, 12)}</div>
                    </div>
                    <button
                      type="button"
                      className="ct-btn ct-btn-go"
                      disabled={importingId === item.session_id}
                      data-testid="import-session-go"
                      onClick={() => { void onImport(item) }}
                    >
                      {importingId === item.session_id ? '载入中…' : '载入为前文'}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
      {quickPreview && typeof document !== 'undefined' && createPortal(
        <aside
          className="tm-quick-preview"
          data-testid="session-quick-preview"
          style={{ top: quickPreview.top, left: quickPreview.left }}
          aria-label="会话快捷预览"
          onMouseEnter={() => { if (quickPreviewCloseTimer.current) clearTimeout(quickPreviewCloseTimer.current) }}
          onMouseLeave={hideQuickPreviewSoon}
        >
          <div className="tm-preview-head">
            <span className="tm-preview-title">{quickPreview.title}</span>
            <span className="tm-source source-preview">{quickPreview.source}</span>
          </div>
          <div className="tm-preview-meta">{quickPreview.provider} · {quickPreview.cwd} · {quickPreview.sessionId}</div>
          <div className="tm-preview-messages">
            {quickPreview.messages.length === 0 && <div className="tm-preview-empty">还没有可预览的对话内容</div>}
            {quickPreview.messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`tm-preview-message role-${message.role}`}>
                <div className="tm-preview-role">
                  {message.role === 'assistant' && !quickPreview.messages.slice(index + 1).some((item) => item.role === 'assistant') ? '最后回复' : message.role === 'assistant' ? '回复' : '你'}
                </div>
                <div className="tm-preview-text">{message.text}</div>
              </div>
            ))}
          </div>
        </aside>,
        document.body,
      )}
    </div>
  )
}
