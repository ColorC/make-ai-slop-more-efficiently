import { useSyncExternalStore } from 'react'
import { ccApi } from '../../api/ccClient'
import type { CcSessionMeta } from '../../api/ccClient'
import type { CcChatSessionMeta } from '../../api/ccChatClient'

export type CcTabRunState = 'working' | 'done' | 'ended' | 'unknown'
export type CcTabSessionKind = 'pty' | 'chat'

export const CC_TAB_WORKING_TITLE = 'Agent 工作中'

/** 列表行展示所需的轻量会话 meta, 与运行态同源(同一份轮询载荷)。 */
export interface CcTabSessionMetaLite {
  kind: CcTabSessionKind
  provider?: string
  cwd?: string
  /** pty 取原生会话标题, chat 取用户可编辑名; 都可能为空。 */
  title?: string
  providerSessionId?: string
  startedAt?: number
  lastActivityAt?: number
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

export function ptyTabRunState(meta: Pick<CcSessionMeta, 'alive' | 'working' | 'has_user_turn' | 'status'>): CcTabRunState {
  if (!meta.alive || meta.status === 'recoverable') return 'ended'
  return meta.has_user_turn && meta.working ? 'working' : 'done'
}

export function chatTabRunState(meta: Pick<CcChatSessionMeta, 'alive' | 'running' | 'status'>): CcTabRunState {
  if (!meta.alive || meta.status === 'ended') return 'ended'
  return meta.running ? 'working' : 'done'
}

export function buildCcTabStates(
  ptySessions: ReadonlyArray<Pick<CcSessionMeta, 'id' | 'alive' | 'working' | 'has_user_turn' | 'status'>>,
  chatSessions: ReadonlyArray<Pick<CcChatSessionMeta, 'id' | 'alive' | 'running' | 'status'>>,
): Readonly<Record<string, CcTabRunState>> {
  const states: Record<string, CcTabRunState> = {}
  for (const session of ptySessions) states[session.id] = ptyTabRunState(session)
  for (const session of chatSessions) states[session.id] = chatTabRunState(session)
  return states
}

export function buildCcTabMetas(
  ptySessions: ReadonlyArray<
    Pick<CcSessionMeta, 'id' | 'cwd'>
    & Partial<Pick<CcSessionMeta, 'provider' | 'provider_title' | 'display_title' | 'provider_session_id' | 'started_at' | 'last_submit_at' | 'last_output_at'>>
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
    metas[session.id] = {
      kind: 'pty',
      provider: session.provider || undefined,
      cwd: session.cwd || undefined,
      title: session.display_title || session.provider_title || undefined,
      providerSessionId: session.provider_session_id || undefined,
      startedAt: session.started_at || undefined,
      lastActivityAt: Math.max(session.last_submit_at || 0, session.last_output_at || 0) || undefined,
    }
  }
  for (const session of chatSessions) {
    metas[session.id] = {
      kind: 'chat',
      provider: session.provider || undefined,
      cwd: session.cwd || undefined,
      title: session.name || undefined,
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
let ptyStates: Readonly<Record<string, CcTabRunState>> = {}
let chatStates: Readonly<Record<string, CcTabRunState>> = {}
let ptyMetas: Readonly<Record<string, CcTabSessionMetaLite>> = {}
let chatMetas: Readonly<Record<string, CcTabSessionMetaLite>> = {}
let snapshot: TabStatusSnapshot = { states: {}, metas: {}, ptyLoaded: false, chatLoaded: false }
let pollTimer: ReturnType<typeof setInterval> | null = null
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
      && a.cwd === b.cwd
      && a.title === b.title
      && a.providerSessionId === b.providerSessionId
      && a.startedAt === b.startedAt
      && a.lastActivityAt === b.lastActivityAt
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

function refresh(): Promise<void> {
  if (refreshInFlight) return refreshInFlight

  // 全部会话的运行态来自 ccdaemon 的纯内存快照(/api/cc/tab-states, 后端
  // 按真实 PTY 输出流/chat 在途回合判定, 与浏览器是否挂载终端无关)。此前 PTY
  // 状态只由挂载中的终端页签经自家 WebSocket 发布: 切走即卸载(onlyWhenVisible),
  // 后台页签活跃时不亮、卸载前的 'working' 还会永久残留。改为统一轮询后,
  // 前台/后台/刷新后的页签都以后端为准, 最多滞后一个轮询周期。
  // 快照也带 resume 别名: 老页签仍以原 PTY id 为键, 后端把替代会话的运行态
  // 投影回旧 id, 重启续接过的页签同样准。
  refreshInFlight = ccApi.tabStates().then((payload) => {
    ptyStates = buildCcTabStates(payload.pty, [])
    chatStates = buildCcTabStates([], payload.chat)
    ptyMetas = buildCcTabMetas(payload.pty, [])
    chatMetas = buildCcTabMetas([], payload.chat)
    publishIfChanged(true, true)
  }).catch(() => {
    // 瞬时失败(ccdaemon 重启/代理 503)保留上一份快照不闪, 下个 tick 自愈。
  }).finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

/** 手动触发一轮刷新(活跃会话页的刷新按钮); 与定时轮询共用同一在途锁。 */
export function refreshCcTabStates(): Promise<void> {
  return refresh()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) {
    void refresh()
    pollTimer = setInterval(() => { void refresh() }, POLL_MS)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }
}

function getSnapshot(): TabStatusSnapshot {
  return snapshot
}

/**
 * One shared status stream for every visible Dockview tab header. Polling hits
 * a single in-memory backend snapshot and unchanged status snapshots do not
 * notify React.
 */
export function useCcTabRunState(sessionId: string, kind: CcTabSessionKind): CcTabRunState {
  const current = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const state = current.states[sessionId]
  if (state) return state
  // 快照里没有这个 id: 会话已不在活名单(结束/被 kill)→ ended; 首轮快照未到 → unknown。
  const sourceLoaded = kind === 'chat' ? current.chatLoaded : current.ptyLoaded
  return sourceLoaded ? 'ended' : 'unknown'
}

/** 活跃会话列表页用: 整份快照(运行态 + 轻量 meta), 快照引用只在真有变化时更新。 */
export function useCcTabStatusSnapshot(): TabStatusSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}
