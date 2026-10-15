import { useSyncExternalStore } from 'react'
import { ccApi } from '../../api/ccClient'
import type { CcSessionMeta } from '../../api/ccClient'
import type { CcChatSessionMeta } from '../../api/ccChatClient'
import { sessionProjectionTitle } from './sessionProjection'

export type CcTabRunState = 'working' | 'done' | 'ended' | 'unknown'
export type CcTabSessionKind = 'pty' | 'chat'

export const CC_TAB_WORKING_TITLE = 'Agent 工作中'

/** 列表行展示所需的轻量会话 meta, 与运行态同源(同一份轮询载荷)。 */
export interface CcTabSessionMetaLite {
  kind: CcTabSessionKind
  provider?: string
  origin?: 'human' | 'agent'
  visibility?: 'visible' | 'hidden' | 'background'
  runtimeHost?: 'omnicompany' | 'litework' | string
  cwd?: string
  /** pty 取原生会话标题, chat 取用户可编辑名; 都可能为空。 */
  title?: string
  /** Provider-native first prompt/title. Multiagent never substitutes a digest for it. */
  providerTitle?: string
  /** Cheap-model display enrichment retained for legacy consumers only. */
  displayTitle?: string
  topic?: string
  project?: string
  workstream?: string
  activePlan?: string
  bindingSource?: string
  providerSessionId?: string
  /** Canonical live PTY id when this record only projects state for an old tab id. */
  resumeAliasFor?: string
  startedAt?: number
  lastActivityAt?: number
  /** 最新一次真实提交；完成提醒不能用空闲 TUI 也会刷新的输出时间。 */
  lastSubmitAt?: number
  /** PTY 是否真正提交过用户回合；空终端不能算“工作已结束”。 */
  hasUserTurn?: boolean
  /** dsh web 会话: host 内会话总数(session.list), >1 时列表行标注便于区分。 */
  webSessionCount?: number
  lastMessage?: string
  messageCount?: number
  /** 仅 provider 明确回传的会话累计值；PTY 与未知 adapter 留空。 */
  tokenUsage?: {
    total: number
    input: number
    output: number
    cacheCreationInput: number
    cacheReadInput: number
    source: 'provider_reported'
  }
}

export function ptyTabRunState(meta: Pick<CcSessionMeta, 'alive' | 'working' | 'has_user_turn' | 'status' | 'provider_runtime_known'>): CcTabRunState {
  if (!meta.alive || meta.status === 'recoverable') return 'ended'
  if (meta.has_user_turn && meta.provider_runtime_known === false) return 'unknown'
  return meta.has_user_turn && meta.working ? 'working' : 'done'
}

export function webTabRunState(meta: Pick<CcSessionMeta, 'alive' | 'status' | 'web_phase' | 'working'>): CcTabRunState {
  if (!meta.alive || meta.status === 'ended') return 'ended'
  // 端口未宣布(启动中)算工作中; 就绪后以 supervisor 轮询上报的 working 为准
  // (dsh 会话经 session.list 的 running 位判定), 旧 supervisor 不上报则恒 false=待命。
  if (meta.web_phase === 'starting') return 'working'
  return meta.working ? 'working' : 'done'
}

export function chatTabRunState(meta: Pick<CcChatSessionMeta, 'alive' | 'running' | 'status'>): CcTabRunState {
  if (!meta.alive || meta.status === 'ended') return 'ended'
  return meta.running ? 'working' : 'done'
}

export function buildCcTabStates(
  ptySessions: ReadonlyArray<Pick<CcSessionMeta, 'id' | 'alive' | 'working' | 'has_user_turn' | 'status' | 'provider_runtime_known'>>,
  webSessions: ReadonlyArray<Pick<CcSessionMeta, 'id' | 'alive' | 'status' | 'web_phase' | 'working'>>,
  chatSessions: ReadonlyArray<Pick<CcChatSessionMeta, 'id' | 'alive' | 'running' | 'status'>>,
): Readonly<Record<string, CcTabRunState>> {
  const states: Record<string, CcTabRunState> = {}
  for (const session of ptySessions) states[session.id] = ptyTabRunState(session)
  for (const session of webSessions) states[session.id] = webTabRunState(session)
  for (const session of chatSessions) states[session.id] = chatTabRunState(session)
  return states
}

export function buildCcTabMetas(
  ptySessions: ReadonlyArray<
    Pick<CcSessionMeta, 'id' | 'cwd'>
    & Partial<Pick<CcSessionMeta, 'provider' | 'provider_title' | 'display_title' | 'topic' | 'project' | 'workstream' | 'active_plan' | 'binding_source' | 'provider_session_id' | 'started_at' | 'last_submit_at' | 'last_output_at' | 'has_user_turn' | 'origin' | 'visibility' | 'runtime_host'>>
    & { resume_alias_for?: string }
  >,
  webSessions: ReadonlyArray<
    Pick<CcSessionMeta, 'id' | 'cwd'>
    & Partial<Pick<CcSessionMeta, 'provider' | 'started_at' | 'origin' | 'display_title'>>
  >,
  chatSessions: ReadonlyArray<{
    id: string
    provider?: string | null
    cwd?: string
    name?: string | null
    provider_session_id?: string | null
    started_at?: number
    last_message?: string | null
    message_count?: number
    token_usage?: {
      total: number
      input: number
      output: number
      cache_creation_input: number
      cache_read_input: number
      source: 'provider_reported'
    } | null
  }>,
): Readonly<Record<string, CcTabSessionMetaLite>> {
  const metas: Record<string, CcTabSessionMetaLite> = {}
  for (const session of ptySessions) {
    const title = sessionProjectionTitle({
      provider: session.provider,
      sessionId: session.id,
      topic: session.topic,
      summaryTitle: session.display_title,
      nativeTitle: session.provider_title,
    })
    metas[session.id] = {
      kind: 'pty',
      provider: session.provider || undefined,
      origin: session.origin,
      visibility: session.visibility,
      runtimeHost: session.runtime_host,
      cwd: session.cwd || undefined,
      title,
      providerTitle: session.provider_title || undefined,
      displayTitle: session.display_title || undefined,
      topic: session.topic || undefined,
      project: session.project || undefined,
      workstream: session.workstream || undefined,
      activePlan: session.active_plan || undefined,
      bindingSource: session.binding_source || undefined,
      providerSessionId: session.provider_session_id || undefined,
      resumeAliasFor: session.resume_alias_for || undefined,
      startedAt: session.started_at || undefined,
      lastActivityAt: Math.max(session.last_submit_at || 0, session.last_output_at || 0) || undefined,
      lastSubmitAt: session.last_submit_at || undefined,
      hasUserTurn: session.has_user_turn,
    }
  }
  for (const session of webSessions) {
    const title = sessionProjectionTitle({
      provider: session.provider,
      sessionId: session.id,
      summaryTitle: session.display_title,
    })
    metas[session.id] = {
      // kind 保持 'pty': web 展示面与 PTY 同走 ccApi.kill 生命周期,
      // 且 runtimeTabs 只按 kind !== 'chat' 区分是否进活跃列表。
      kind: 'pty',
      provider: session.provider || undefined,
      origin: session.origin,
      cwd: session.cwd || undefined,
      startedAt: session.started_at || undefined,
      title,
      providerTitle: session.display_title || undefined,
      lastActivityAt: session.started_at || undefined,
    }
  }
  for (const session of chatSessions) {
    const title = sessionProjectionTitle({
      provider: session.provider,
      sessionId: session.id,
      nativeTitle: session.name,
      preview: session.last_message,
    })
    metas[session.id] = {
      kind: 'chat',
      provider: session.provider || undefined,
      cwd: session.cwd || undefined,
      title,
      providerSessionId: session.provider_session_id || undefined,
      startedAt: session.started_at || undefined,
      lastMessage: session.last_message || undefined,
      messageCount: session.message_count,
      tokenUsage: session.token_usage ? {
        total: session.token_usage.total,
        input: session.token_usage.input,
        output: session.token_usage.output,
        cacheCreationInput: session.token_usage.cache_creation_input,
        cacheReadInput: session.token_usage.cache_read_input,
        source: session.token_usage.source,
      } : undefined,
    }
  }
  return metas
}

export interface TabStatusSnapshot {
  states: Readonly<Record<string, CcTabRunState>>
  metas: Readonly<Record<string, CcTabSessionMetaLite>>
  ptyLoaded: boolean
  chatLoaded: boolean
}

const POLL_MS = 2_000
const HIDDEN_POLL_MS = 60_000
const UNFOCUSED_POLL_MS = 60_000
const IDLE_MODE_KEY = 'omni.dashboard.idle.v1'
let ptyStates: Readonly<Record<string, CcTabRunState>> = {}
let chatStates: Readonly<Record<string, CcTabRunState>> = {}
let ptyMetas: Readonly<Record<string, CcTabSessionMetaLite>> = {}
let chatMetas: Readonly<Record<string, CcTabSessionMetaLite>> = {}
let snapshot: TabStatusSnapshot = { states: {}, metas: {}, ptyLoaded: false, chatLoaded: false }
let pollTimer: ReturnType<typeof setTimeout> | null = null
let visibilityHandler: (() => void) | null = null
let refreshInFlight: Promise<void> | null = null
const listeners = new Set<() => void>()

function sameStates(
  left: Readonly<Record<string, CcTabRunState>>,
  right: Readonly<Record<string, CcTabRunState>>,
): boolean {
  const leftKeys = Object.keys(left)
  const rightKeys = Object.keys(right)
  if (leftKeys.length !== rightKeys.length) return false
  return leftKeys.every((key) => left[key] === right[key])
}

function sameMetas(
  left: Readonly<Record<string, CcTabSessionMetaLite>>,
  right: Readonly<Record<string, CcTabSessionMetaLite>>,
): boolean {
  const leftKeys = Object.keys(left)
  const rightKeys = Object.keys(right)
  if (leftKeys.length !== rightKeys.length) return false
  return leftKeys.every((key) => {
    const a = left[key]
    const b = right[key]
    return b !== undefined
      && a.kind === b.kind
      && a.provider === b.provider
      && a.runtimeHost === b.runtimeHost
      && a.cwd === b.cwd
      && a.title === b.title
      && a.providerSessionId === b.providerSessionId
      && a.resumeAliasFor === b.resumeAliasFor
      && a.topic === b.topic
      && a.project === b.project
      && a.workstream === b.workstream
      && a.activePlan === b.activePlan
      && a.startedAt === b.startedAt
      && a.lastActivityAt === b.lastActivityAt
      && a.lastSubmitAt === b.lastSubmitAt
      && a.hasUserTurn === b.hasUserTurn
      && a.lastMessage === b.lastMessage
      && a.messageCount === b.messageCount
      && a.tokenUsage?.total === b.tokenUsage?.total
  })
}

function publishIfChanged(ptyLoaded: boolean, chatLoaded: boolean): void {
  const states = { ...ptyStates, ...chatStates }
  const metas = { ...ptyMetas, ...chatMetas }
  if (
    snapshot.ptyLoaded === ptyLoaded
    && snapshot.chatLoaded === chatLoaded
    && sameStates(snapshot.states, states)
    && sameMetas(snapshot.metas, metas)
  ) return

  snapshot = { states, metas, ptyLoaded, chatLoaded }
  listeners.forEach((listener) => listener())
}

interface DshWebRuntime {
  working: boolean
  title?: string
  sessionCount: number
}

// dsh web 会话运行态(单独接线, 不依赖 ccdaemon 重启): 经统一 web 代理
// POST /api/cc/web/{sid}/api/session.list, 读 harness 的会话列表投影。
// supervisor/web_host 的 runtime 事件是同一数据的另一条通道(重启后生效), 这里兜底。
const webRuntime: Record<string, DshWebRuntime> = {}

async function fetchDshWebRuntime(
  sessions: ReadonlyArray<Pick<CcSessionMeta, 'id' | 'alive' | 'web_phase'>>,
): Promise<void> {
  const ready = sessions.filter((session) => session.alive && session.web_phase === 'ready')
  if (ready.length === 0) return
  await Promise.all(ready.map(async (session) => {
    try {
      const rpcId = `dsh-session-list-${session.id}-${Date.now()}-${Math.random().toString(36).slice(2)}`
      const response = await fetch(`/api/cc/web/${encodeURIComponent(session.id)}/api/session.list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'client-request', rpcId, method: 'session.list', payload: {} }),
        cache: 'no-store',
      })
      if (!response.ok) return
      const envelope = await response.json() as {
        result?: { value?: { items?: Array<{ running?: boolean; updatedAt?: number; projections?: { values?: { title?: unknown } } }> } }
      }
      const items = envelope.result?.value?.items ?? []
      const runningItems = items.filter((item) => item.running === true)
      const active = runningItems[0]
        ?? [...items].sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))[0]
      const rawTitle = active?.projections?.values?.title
      const title = typeof rawTitle === 'string' && rawTitle.trim() ? rawTitle.trim() : undefined
      webRuntime[session.id] = { working: runningItems.length > 0, title, sessionCount: items.length }
    } catch {
      // 瞬时失败保留上一份, 下个 tick 重试
    }
  }))
}

function refresh(): Promise<void> {
  if (refreshInFlight) return refreshInFlight

  // 全部会话的运行态来自 ccdaemon 的纯内存快照(/api/cc/tab-states, 后端
  // 按真实 PTY 输出流/chat 在途回合判定, 与浏览器是否挂载终端无关)。此前 PTY
  // 状态只由挂载中的终端页签经自家 WebSocket 发布: 切走即卸载(onlyWhenVisible),
  // 后台页签活跃时不亮、卸载前的 'working' 还会永久残留。改为统一轮询后,
  // 前台/后台/刷新后的页签都以后端为准, 最多滞后一个轮询周期。
  // 快照也带 resume 别名: 老页签仍以原 PTY id 为键, 后端把替代会话的运行态
  // 投影回旧 id, 重启续接过的页签同样准。
  // dsh web 会话再叠加经代理的 session.list 运行态(working/标题/会话数)。
  refreshInFlight = ccApi.tabStates().then(async (payload) => {
    await fetchDshWebRuntime(payload.web)
    const webMerged = payload.web.map((session) => ({
      ...session,
      working: session.working ?? webRuntime[session.id]?.working ?? false,
    }))
    ptyStates = buildCcTabStates(payload.pty, webMerged, [])
    chatStates = buildCcTabStates([], [], payload.chat)
    ptyMetas = buildCcTabMetas(payload.pty, webMerged, [])
    chatMetas = buildCcTabMetas([], [], payload.chat)
    for (const [sid, runtime] of Object.entries(webRuntime)) {
      const meta = ptyMetas[sid]
      if (!meta) continue
      if (runtime.title) {
        meta.title = sessionProjectionTitle({
          provider: meta.provider,
          sessionId: sid,
          topic: meta.topic,
          summaryTitle: meta.displayTitle,
          nativeTitle: runtime.title,
        })
        meta.providerTitle = runtime.title
      }
      meta.webSessionCount = runtime.sessionCount
    }
    publishIfChanged(true, true)
  }).catch(() => {
    // 瞬时失败(ccdaemon 重启/代理 503)保留上一份快照不闪, 下个 tick 自愈。
  }).finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

function documentHasFocus(): boolean {
  return typeof document === 'undefined'
    || typeof document.hasFocus !== 'function'
    || document.hasFocus()
}

function idleModeEnabled(): boolean {
  try {
    return window.localStorage.getItem(IDLE_MODE_KEY) === '1'
  } catch {
    return false
  }
}

function pollDelay(): number {
  if (typeof document === 'undefined') return POLL_MS
  if (document.visibilityState === 'hidden') return HIDDEN_POLL_MS
  return idleModeEnabled() && !documentHasFocus() ? UNFOCUSED_POLL_MS : POLL_MS
}

function clearPollTimer(): void {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll(): void {
  clearPollTimer()
  if (listeners.size === 0) return
  pollTimer = setTimeout(() => {
    pollTimer = null
    void refresh().finally(schedulePoll)
  }, pollDelay())
}

function handleWindowActivityChange(): void {
  clearPollTimer()
  if (listeners.size === 0) return
  if (document.visibilityState === 'hidden' || (idleModeEnabled() && !documentHasFocus())) {
    schedulePoll()
    return
  }
  // Resume with one immediate authoritative snapshot, then return to the
  // foreground cadence. Hidden dashboards otherwise poll only once a minute.
  void refresh().finally(schedulePoll)
}

/** 手动触发一轮刷新(活跃会话页的刷新按钮); 与定时轮询共用同一在途锁。 */
export function refreshCcTabStates(): Promise<void> {
  return refresh()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) {
    visibilityHandler = handleWindowActivityChange
    document.addEventListener('visibilitychange', visibilityHandler)
    window.addEventListener('focus', visibilityHandler)
    window.addEventListener('blur', visibilityHandler)
    void refresh().finally(schedulePoll)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      clearPollTimer()
      if (visibilityHandler !== null) {
        document.removeEventListener('visibilitychange', visibilityHandler)
        window.removeEventListener('focus', visibilityHandler)
        window.removeEventListener('blur', visibilityHandler)
        visibilityHandler = null
      }
    }
  }
}

function getSnapshot(): TabStatusSnapshot {
  return snapshot
}

function runStateSnapshot(sessionId: string, kind: CcTabSessionKind): CcTabRunState {
  const state = snapshot.states[sessionId]
  if (state) return state
  const sourceLoaded = kind === 'chat' ? snapshot.chatLoaded : snapshot.ptyLoaded
  return sourceLoaded ? 'ended' : 'unknown'
}

/** Non-reactive lookup for effects that already subscribe to one session's run state. */
export function getCcTabSessionMeta(sessionId: string): CcTabSessionMetaLite | undefined {
  return snapshot.metas[sessionId]
}

/**
 * One shared status stream for every visible Dockview tab header. Polling hits
 * a single in-memory backend snapshot and unchanged status snapshots do not
 * notify React.
 */
export function useCcTabRunState(sessionId: string, kind: CcTabSessionKind): CcTabRunState {
  // Subscribe to one primitive value. A status/meta change in any of the other
  // N sessions must not rerender every terminal editor and tab header.
  const read = () => runStateSnapshot(sessionId, kind)
  return useSyncExternalStore(subscribe, read, read)
}

/** 活跃会话列表页用: 整份快照(运行态 + 轻量 meta), 快照引用只在真有变化时更新。 */
export function useCcTabStatusSnapshot(): TabStatusSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}
