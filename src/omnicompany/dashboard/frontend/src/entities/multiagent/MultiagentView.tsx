/**
 * Multiagent 工作面：只列 ccdaemon 当前仍持有的真实 runtime。
 *
 * 高频运行态来自 /api/cc/tab-states 的 2s 纯内存投影；adapter 语义摘要来自
 * residents/tail 的可见页 10s scanner 投影；材料走 Review WebSocket，轮询只做断线兜底。
 * 页面没有重复标题栏，控件只占一行；Full Mode 才展开上下文与最近动态。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Copy,
  ExternalLink,
  Folder,
  LayoutPanelLeft,
  Maximize2,
  Plus,
  RefreshCw,
  Square,
  Unlink,
} from 'lucide-react'
import { ccApi } from '../../api/ccClient'
import { ccChatApi } from '../../api/ccChatClient'
import { usePanels, type OpenedTab } from '../../stores/panelsStore'
import { useReviewStream } from '../review/streamStore'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import {
  refreshCcTabStates,
  useCcTabStatusSnapshot,
  type CcTabRunState,
  type CcTabSessionKind,
  type CcTabSessionMetaLite,
  type TabStatusSnapshot,
} from '../cc_session/tabStatus'
import {
  fetchResidents,
  fetchTail,
  type Resident,
  type TailLine,
} from './residentsClient'
import {
  isMultiagentLinkedSurface,
  multiagentLinkedUrl,
  openMultiagentLinkedWindow,
  useMultiagentLink,
} from './multiagentLink'
import { copyText } from '../../lib/copyText'
import './multiagent.css'

type NewSessionKind = 'plain_cli' | 'claude_cli' | 'codex_cli' | 'codebuddy_cli' | 'kimi_cli' | 'opencode_cli' | 'powershell'

const SESSION_OPTIONS: Array<{ value: NewSessionKind; label: string; cmd?: string[] }> = [
  { value: 'codex_cli', label: 'Codex CLI', cmd: ['codex'] },
  { value: 'claude_cli', label: 'Claude CLI' },
  { value: 'codebuddy_cli', label: 'CodeBuddy CLI', cmd: ['codebuddy'] },
  { value: 'kimi_cli', label: 'Kimi CLI', cmd: ['kimi'] },
  { value: 'opencode_cli', label: 'OpenCode CLI', cmd: ['opencode'] },
  { value: 'plain_cli', label: '纯 CLI', cmd: ['powershell', '-NoLogo'] },
  { value: 'powershell', label: 'PowerShell', cmd: ['powershell'] },
]

export function providerDisplayName(provider?: string | null): string {
  const names: Record<string, string> = {
    claude: 'Claude',
    claude_code: 'Claude',
    codex: 'Codex',
    codebuddy: 'CodeBuddy',
    kimi: 'Kimi',
    opencode: 'OpenCode',
    powershell: 'PowerShell',
    shell: '终端',
    omni_agent: 'OmniAgent',
    controller: '总控',
  }
  return names[String(provider || '')] || String(provider || 'Agent')
}

function shortCwd(cwd?: string): string {
  if (!cwd) return ''
  return cwd.split(/[\\/]/).filter(Boolean).pop() || cwd
}

function formatDuration(startedAt?: number, now = Date.now()): string {
  if (!startedAt) return '—'
  const startMs = startedAt < 1e12 ? startedAt * 1000 : startedAt
  const seconds = Math.max(0, Math.floor((now - startMs) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

function formatTokens(total?: number): string {
  if (!total) return ''
  if (total >= 1_000_000) return `${(total / 1_000_000).toFixed(total >= 10_000_000 ? 0 : 1)}m`
  if (total >= 1_000) return `${(total / 1_000).toFixed(total >= 100_000 ? 0 : 1)}k`
  return String(total)
}

function runStateFor(snapshot: TabStatusSnapshot, sessionId: string, kind: CcTabSessionKind): CcTabRunState {
  const state = snapshot.states[sessionId]
  if (state) return state
  const sourceLoaded = kind === 'chat' ? snapshot.chatLoaded : snapshot.ptyLoaded
  return sourceLoaded ? 'ended' : 'unknown'
}

function runStateLabel(state: CcTabRunState): string {
  if (state === 'working') return '进行中'
  if (state === 'done') return '待命'
  if (state === 'ended') return '已结束'
  return '同步中'
}

function runtimeTabs(
  tabs: OpenedTab[],
  snapshot: TabStatusSnapshot,
  activeChatRuntimeIds: ReadonlySet<string> | null,
): OpenedTab[] {
  const bySessionId = new Map(
    tabs.filter((tab) => tab.ref.type === 'cc_session').map((tab) => [tab.ref.id, tab]),
  )
  const activeIds = Object.keys(snapshot.metas).filter((sessionId) => {
    if (snapshot.states[sessionId] === 'ended') return false
    const meta = snapshot.metas[sessionId]
    // Older ccdaemon builds projected every recoverable structured-chat record
    // as alive. The full metadata endpoint already carries runtime_alive, so
    // use that authoritative bit until the daemon can be restarted safely.
    return meta?.kind !== 'chat' || activeChatRuntimeIds === null || activeChatRuntimeIds.has(sessionId)
  })
  const activeSet = new Set(activeIds)
  const orderedIds = [
    ...tabs.filter((tab) => tab.ref.type === 'cc_session' && activeSet.has(tab.ref.id)).map((tab) => tab.ref.id),
    ...activeIds.filter((sessionId) => !bySessionId.has(sessionId)),
  ]
  return orderedIds
    .map((sessionId) => bySessionId.get(sessionId) || ({
      id: `cc_session:${sessionId}`,
      ref: { type: 'cc_session', id: sessionId },
      title: snapshot.metas[sessionId]?.title || `${providerDisplayName(snapshot.metas[sessionId]?.provider)} · ${sessionId.slice(0, 8)}`,
    } as OpenedTab))
}

function residentFor(sessionId: string, meta: CcTabSessionMetaLite | undefined, residents: Resident[]): Resident | undefined {
  const ids = new Set([sessionId, meta?.providerSessionId].filter(Boolean))
  return residents.find((resident) => ids.has(resident.pty_id || '') || ids.has(resident.session_id))
}

function withHydratedMeta(
  meta: CcTabSessionMetaLite,
  hydrated: Partial<CcTabSessionMetaLite> | undefined,
): CcTabSessionMetaLite {
  if (!hydrated) return meta
  return {
    ...meta,
    startedAt: meta.startedAt ?? hydrated.startedAt,
    lastActivityAt: meta.lastActivityAt ?? hydrated.lastActivityAt,
    providerSessionId: meta.providerSessionId ?? hydrated.providerSessionId,
    tokenUsage: meta.tokenUsage ?? hydrated.tokenUsage,
  }
}

function lastLine(lines: TailLine[], roles: string[]): TailLine | undefined {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (roles.includes(String(lines[index].role || '').toLowerCase()) && lines[index].text?.trim()) return lines[index]
  }
  return undefined
}

function startVisiblePoll(callback: () => Promise<void> | void, intervalMs: number): () => void {
  let stopped = false
  let inFlight = false
  const run = () => {
    if (stopped || inFlight || document.visibilityState !== 'visible') return
    inFlight = true
    void Promise.resolve(callback()).finally(() => {
      inFlight = false
    })
  }
  const onVisibilityChange = () => {
    if (document.visibilityState === 'visible') run()
  }
  run()
  const timer = window.setInterval(run, intervalMs)
  document.addEventListener('visibilitychange', onVisibilityChange)
  return () => {
    stopped = true
    window.clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }
}

function SessionRow({
  tab,
  meta,
  state,
  resident,
  tail,
  full,
  selected,
  pendingMaterials,
  now,
  onSelect,
  onOpenLocal,
  onKill,
}: {
  tab: OpenedTab
  meta: CcTabSessionMetaLite | undefined
  state: CcTabRunState
  resident?: Resident
  tail: TailLine[]
  full: boolean
  selected: boolean
  pendingMaterials: Array<{ id: string; title: string }>
  now: number
  onSelect: (tab: OpenedTab) => void
  onOpenLocal: (tab: OpenedTab) => void
  onKill: (tab: OpenedTab, kind: CcTabSessionKind) => Promise<void>
}) {
  const sessionId = tab.ref.id
  const kind: CcTabSessionKind = meta?.kind ?? (sessionId.startsWith('chat-') ? 'chat' : 'pty')
  const cwd = meta?.cwd || resident?.cwd || ''
  const title = meta?.title || resident?.title || resident?.name || tab.title || sessionId.slice(0, 12)
  const currentTask = resident?.current_task || resident?.initial_task || resident?.preview || ''
  const latestStep = resident?.last_step || meta?.lastMessage || lastLine(tail, ['assistant', 'agent'])?.text || ''
  const latestUser = lastLine(tail, ['user', 'human'])?.text || ''
  const menu: KebabItem[] = [
    {
      label: '在当前窗口打开',
      icon: <ExternalLink size={15} />,
      onClick: () => onOpenLocal(tab),
    },
    {
      label: '复制会话 ID',
      icon: <Copy size={15} />,
      onClick: () => { void copyText(sessionId) },
    },
    ...(cwd ? [{
      label: '复制工作目录',
      icon: <Folder size={15} />,
      onClick: () => { void copyText(cwd) },
    }] : []),
    {
      label: '终止会话',
      icon: <Square size={15} />,
      danger: true,
      testid: 'active-process-kill',
      onClick: () => { void onKill(tab, kind) },
    },
  ]

  return (
    <article
      className="ma-row"
      data-testid="multiagent-row"
      data-state={state}
      data-kind={kind}
      data-session-id={sessionId}
      data-full={full ? '1' : '0'}
      data-selected={selected ? '1' : '0'}
    >
      <button
        type="button"
        className="ma-row-hit"
        onClick={() => onSelect(tab)}
        aria-label={`选择 ${title}`}
      >
        <span className="ma-state" data-state={state} title={runStateLabel(state)}>
          <span className="ma-state-dot" aria-hidden />
          <span>{runStateLabel(state)}</span>
        </span>
        <span className="ma-row-title" title={title}>{title}</span>
        <span className="ma-provider">{providerDisplayName(meta?.provider || resident?.provider)}</span>
        <span className="ma-runtime" title="本次 runtime 工作时间">{formatDuration(meta?.startedAt, now)}</span>
        {meta?.tokenUsage?.total ? (
          <span className="ma-token" title="Provider 回传的会话累计 Token">
            {formatTokens(meta.tokenUsage.total)} tok
          </span>
        ) : null}
        {pendingMaterials.length > 0 ? (
          <span className="ma-material-count" title="未审阅材料">{pendingMaterials.length}</span>
        ) : null}
      </button>
      <KebabMenu testid="active-process-more" items={menu} />
      {full && (
        <div className="ma-detail">
          <div className="ma-detail-main">
            {currentTask && <p><span>当前</span>{currentTask}</p>}
            {latestStep && <p><span>最新</span>{latestStep}</p>}
            {latestUser && <p><span>输入</span>{latestUser}</p>}
            {!currentTask && !latestStep && !latestUser && <p className="ma-muted">adapter 暂无可解析动态</p>}
          </div>
          <div className="ma-detail-side">
            {cwd && <code title={cwd}>{shortCwd(cwd)}</code>}
            <code title={sessionId}>{sessionId}</code>
            {pendingMaterials.slice(0, 2).map((material) => (
              <span key={material.id} title={material.title}>待审 · {material.title}</span>
            ))}
          </div>
        </div>
      )}
    </article>
  )
}

export interface MultiagentViewProps {
  /** SurfaceShell 等宿主可覆写导航；联动 viewer 会优先保持在固定工作面内。 */
  onOpen?: (tab: OpenedTab) => void
}

export default function MultiagentView({ onOpen }: MultiagentViewProps = {}) {
  const tabs = usePanels((state) => state.tabs)
  const snapshot = useCcTabStatusSnapshot()
  const reviewVersion = useReviewStream((state) => state.version)
  const reviewConnected = useReviewStream((state) => state.connected)
  const reviewMaterials = useReviewStream((state) => state.materials)
  const link = useMultiagentLink()
  const linkedSurface = isMultiagentLinkedSurface()
  const [full, setFull] = useState(false)
  const [newSessionKind, setNewSessionKind] = useState<NewSessionKind>('codex_cli')
  const [creating, setCreating] = useState(false)
  const [residents, setResidents] = useState<Resident[]>([])
  const [tails, setTails] = useState<Record<string, TailLine[]>>({})
  const [hydratedMetas, setHydratedMetas] = useState<Record<string, Partial<CcTabSessionMetaLite>>>({})
  const [activeChatRuntimeIds, setActiveChatRuntimeIds] = useState<ReadonlySet<string> | null>(null)
  const [error, setError] = useState('')
  const [now, setNow] = useState(Date.now())

  const sessionTabs = useMemo(
    () => runtimeTabs(tabs, snapshot, activeChatRuntimeIds),
    [activeChatRuntimeIds, snapshot, tabs],
  )

  useEffect(() => useReviewStream.getState().acquire(), [])

  const hydrateRuntimeMetas = useCallback(async () => {
    const [ptyResult, chatResult] = await Promise.allSettled([
      ccApi.list({ includeRecoverable: false }),
      ccChatApi.list({ limit: 500, includeArchived: false }),
    ])
    const next: Record<string, Partial<CcTabSessionMetaLite>> = {}
    if (ptyResult.status === 'fulfilled') {
      for (const meta of ptyResult.value) {
        next[meta.id] = {
          startedAt: meta.started_at,
          lastActivityAt: Math.max(meta.last_submit_at || 0, meta.last_output_at || 0) || undefined,
          providerSessionId: meta.provider_session_id || undefined,
        }
      }
    }
    if (chatResult.status === 'fulfilled') {
      setActiveChatRuntimeIds(new Set(
        chatResult.value.filter((meta) => meta.runtime_alive === true).map((meta) => meta.id),
      ))
      for (const meta of chatResult.value) {
        next[meta.id] = {
          startedAt: meta.started_at,
          providerSessionId: meta.claude_session_id || undefined,
          tokenUsage: meta.token_usage ? {
            total: meta.token_usage.total,
            input: meta.token_usage.input,
            output: meta.token_usage.output,
            cacheCreationInput: meta.token_usage.cache_creation_input,
            cacheReadInput: meta.token_usage.cache_read_input,
            source: meta.token_usage.source,
          } : undefined,
        }
      }
    }
    setHydratedMetas(next)
  }, [])

  useEffect(() => {
    return startVisiblePoll(hydrateRuntimeMetas, 15_000)
  }, [hydrateRuntimeMetas])

  const refreshDetails = useCallback(async () => {
    try {
      const response = await fetchResidents()
      setResidents(response.residents || [])
      setError('')
    } catch (cause) {
      setError(`动态索引暂时不可用：${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }, [])

  useEffect(() => {
    return startVisiblePoll(refreshDetails, 10_000)
  }, [refreshDetails])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const runtimePollRef = useRef({
    hydratedMetas,
    residents,
    sessionTabs,
    snapshotMetas: snapshot.metas,
  })
  runtimePollRef.current = {
    hydratedMetas,
    residents,
    sessionTabs,
    snapshotMetas: snapshot.metas,
  }

  useEffect(() => {
    if (!full) return
    let disposed = false
    const load = async () => {
      const current = runtimePollRef.current
      if (current.sessionTabs.length === 0) return
      const entries = await Promise.all(current.sessionTabs.map(async (tab) => {
        const meta = withHydratedMeta(
          current.snapshotMetas[tab.ref.id],
          current.hydratedMetas[tab.ref.id],
        )
        const resident = residentFor(tab.ref.id, meta, current.residents)
        const tailId = resident?.session_id || meta?.providerSessionId || tab.ref.id
        return [tab.ref.id, await fetchTail(tailId, 10)] as const
      }))
      if (!disposed) setTails(Object.fromEntries(entries))
    }
    const stopPolling = startVisiblePoll(load, 10_000)
    return () => {
      disposed = true
      stopPolling()
    }
  }, [full])

  useEffect(() => {
    if (!link.selectedSessionId) return
    const node = document.querySelector<HTMLElement>(`[data-testid="multiagent-row"][data-session-id="${CSS.escape(link.selectedSessionId)}"]`)
    node?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [link.selectedSessionId])

  // WebSocket 事件直接更新 store；version 保留为显式依赖，确保提交后本工作面立即重算关联材料。
  const pendingBySession = useMemo(() => {
    void reviewVersion
    const pending = Object.values(reviewMaterials).filter((material) => material.status === 'pending')
    const map = new Map<string, Array<{ id: string; title: string }>>()
    for (const material of pending) {
      if (!material.source_subagent_id) continue
      const current = map.get(material.source_subagent_id) || []
      current.push({ id: material.id, title: material.title })
      map.set(material.source_subagent_id, current)
    }
    return map
  }, [reviewMaterials, reviewVersion])

  const defaultOpen = useCallback((tab: OpenedTab) => {
    usePanels.getState().openTab(tab.ref, tab.title, tab.facet)
  }, [])
  const openLocal = onOpen || defaultOpen

  const selectSession = useCallback((tab: OpenedTab) => {
    if (link.connected || linkedSurface) {
      link.publishSelection(tab.ref.id)
      setFull(true)
      return
    }
    openLocal(tab)
  }, [link, linkedSurface, openLocal])

  const killSession = useCallback(async (tab: OpenedTab, kind: CcTabSessionKind) => {
    const sessionId = tab.ref.id
    if (typeof window !== 'undefined' && !window.confirm(`终止会话 ${sessionId.slice(0, 8)}？`)) return
    if (kind === 'pty') await ccApi.kill(sessionId)
    else await ccChatApi.kill(sessionId)
    await refreshCcTabStates()
  }, [])

  const createSession = useCallback(async () => {
    if (creating) return
    setCreating(true)
    setError('')
    try {
      const option = SESSION_OPTIONS.find((item) => item.value === newSessionKind) || SESSION_OPTIONS[0]
      const meta = await ccApi.create({ cmd: option.cmd })
      usePanels.getState().openTab({ type: 'cc_session', id: meta.id }, `${option.label} · ${meta.id.slice(0, 8)}`)
      await refreshCcTabStates()
    } catch (cause) {
      setError(`新建失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setCreating(false)
    }
  }, [creating, newSessionKind])

  const copyLinked = useCallback(async () => {
    const linkId = useMultiagentLink.getState().ensureOwnerLink()
    const ok = await copyText(multiagentLinkedUrl(linkId))
    if (!ok) setError('复制联动链接失败：浏览器剪贴板权限受限')
  }, [])

  const openLinked = useCallback(async () => {
    const result = openMultiagentLinkedWindow()
    if (result.opened) {
      setError('')
      return
    }
    const copied = await copyText(result.url)
    setError(copied
      ? '浏览器拦截了新页签；联动链接已复制，可直接粘贴到另一个页签。'
      : '浏览器拦截了新页签；请允许本站弹出窗口，或使用旁边的复制链接按钮。')
  }, [])

  const globalPending = Object.values(reviewMaterials).filter((material) => material.status === 'pending').length

  return (
    <div className="ma-root fp-scroll" data-testid="multiagent-view" data-linked-role={link.role}>
      <div className="ma-tools" aria-label="Multiagent 工具">
        <select
          className="tm-select"
          value={newSessionKind}
          onChange={(event) => setNewSessionKind(event.target.value as NewSessionKind)}
          aria-label="新会话执行者"
          data-testid="multiagent-new-provider"
        >
          {SESSION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <button type="button" className="ct-newbtn" onClick={() => { void createSession() }} disabled={creating} data-testid="multiagent-new-session">
          <Plus size={13} />{creating ? '新建中…' : '新会话'}
        </button>
        <button type="button" className={`ma-mode${full ? ' on' : ''}`} onClick={() => setFull((value) => !value)} aria-pressed={full} data-testid="multiagent-full-mode">
          {full ? '精简' : 'Full'}
        </button>
        {link.linkId ? (
          <span
            className="ma-link-state"
            data-connected={link.connected ? '1' : '0'}
            data-testid="multiagent-link-state"
            title={link.peerWindowId || undefined}
          >
            <i />{link.connected
              ? `已绑定${link.role === 'viewer' ? '主窗' : '联动窗'} · ${link.peerWindowId?.slice(-6)}`
              : linkedSurface ? '等待主窗口' : '等待联动窗口'}
          </span>
        ) : null}
        <span className="ma-tool-spacer" />
        {globalPending > 0 ? <span className="ma-global-pending">{globalPending} 待审</span> : null}
        {!linkedSurface && (
          <>
            {!link.connected && (
              <button type="button" className="v2-iconbtn" onClick={() => { void openLinked() }} title="在联动窗口打开" aria-label="在联动窗口打开" data-testid="multiagent-open-linked">
                <LayoutPanelLeft size={14} />
              </button>
            )}
            <button type="button" className="v2-iconbtn" onClick={() => { void copyLinked() }} title="复制联动链接" aria-label="复制联动链接" data-testid="multiagent-copy-linked">
              <Copy size={14} />
            </button>
          </>
        )}
        {link.linkId && (
          <button type="button" className="v2-iconbtn" onClick={link.release} title="解除窗口联动" aria-label="解除窗口联动">
            <Unlink size={14} />
          </button>
        )}
        <button
          type="button"
          className="v2-iconbtn"
          onClick={() => { void Promise.all([refreshCcTabStates(), refreshDetails(), hydrateRuntimeMetas()]) }}
          title="刷新"
          aria-label="刷新"
          data-testid="multiagent-refresh"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      {(error || (!reviewConnected && reviewVersion > 0)) && (
        <div className="ma-anomaly" role="status" data-testid="multiagent-anomaly">
          {error || '材料实时流已断开，正在用短轮询补偿。'}
        </div>
      )}

      {sessionTabs.length === 0 && snapshot.ptyLoaded && snapshot.chatLoaded && (
        <div className="tm-empty ma-empty" data-testid="multiagent-empty">当前没有由 Dashboard 保活的会话。</div>
      )}
      <div className="ma-list">
        {sessionTabs.map((tab) => {
          const meta = withHydratedMeta(snapshot.metas[tab.ref.id], hydratedMetas[tab.ref.id])
          const kind: CcTabSessionKind = meta?.kind ?? (tab.ref.id.startsWith('chat-') ? 'chat' : 'pty')
          const resident = residentFor(tab.ref.id, meta, residents)
          const ids = [tab.ref.id, meta?.providerSessionId, resident?.session_id].filter(Boolean) as string[]
          const pendingMaterials = ids.flatMap((id) => pendingBySession.get(id) || [])
          return (
            <SessionRow
              key={tab.id}
              tab={tab}
              meta={meta}
              state={runStateFor(snapshot, tab.ref.id, kind)}
              resident={resident}
              tail={tails[tab.ref.id] || []}
              full={full}
              selected={link.selectedSessionId === tab.ref.id}
              pendingMaterials={pendingMaterials}
              now={now}
              onSelect={selectSession}
              onOpenLocal={openLocal}
              onKill={killSession}
            />
          )
        })}
      </div>
    </div>
  )
}
