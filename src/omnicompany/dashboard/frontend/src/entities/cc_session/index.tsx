import { ccApi, type CcSessionMeta } from '../../api/ccClient'
import { ccChatApi, type CcChatSessionMeta } from '../../api/ccChatClient'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { lazy } from 'react'
import { Terminal } from 'lucide-react'
import { relTimeEn as relTime } from '../../lib/time'

export type CcSessionStatus = 'alive' | 'recoverable' | 'ended'
/** PTY = xterm 字节流路线 (现有), chat = claude-agent-sdk 结构化消息路线 (新).
 *  两条路线 spawn 同一个本地 claude binary, 只是 transport 不同. */
export type CcSessionKind = 'pty' | 'chat'

export interface CcSessionEntity extends Entity {
  type: 'cc_session'
  kind: CcSessionKind
  cwd: string
  alive: boolean
  status: CcSessionStatus
  cmd: string[]
  startedAt: number
  claudeSessionId?: string | null
  providerSessionId?: string | null
  providerTitle?: string | null
  activePlan?: string | null
  /** LLM provider 字符串 (来自 backend cc_sessions.json) — 决定头像 / model 默认等.
   *  claude_code (默认) / omni_agent / codex. pty session 通常没有, 空字符串占位. */
  provider?: string | null
  /** #2 接管式采纳: adopted=resume 别处会话采纳来的(当 subagent); takenOver=用户已接管(总控不自动 hook)。 */
  adopted?: boolean
  takenOver?: boolean
}

/** plan_id `_infra/dashboard/[2026-05-03]CC-PLAN-SESSION-CONTEXT` → `CC-PLAN-SESSION-CONTEXT` */
function planShortName(planId: string): string {
  const last = planId.split('/').pop() || planId
  return last.replace(/^\[\d{4}-\d{2}-\d{2}\]/, '')
}

function providerForPty(m: CcSessionMeta): string {
  if (m.provider) return m.provider
  const command = String(m.cmd?.[0] || '').toLowerCase()
  if (command.includes('codex')) return 'codex'
  if (command.includes('kimi')) return 'kimi'
  if (command.includes('opencode')) return 'opencode'
  if (command.includes('powershell') || command.includes('pwsh')) return 'shell'
  return 'claude_code'
}

/**
 * Dashboard 会把 PTY id 以 8 位短码展示、写进最近项和可复制深链。
 * 连接层必须把唯一短码还原成 daemon 的完整 id，否则会错误连接
 * `/sessions/862bb496/ws`，而真实会话是 `862bb496844d41ee`。
 */
export function resolvePtyMetaById(list: CcSessionMeta[], id: string): CcSessionMeta | undefined {
  const exact = list.find((item) => item.id === id)
  if (exact) return exact
  if (id.length < 8) return undefined
  const prefixMatches = list.filter((item) => item.id.startsWith(id))
  return prefixMatches.length === 1 ? prefixMatches[0] : undefined
}

async function fetchPtyMetaById(id: string): Promise<CcSessionMeta | undefined> {
  // A freshly-created hosted PTY is returned by POST before its immutable host
  // discovery generation is necessarily visible to the first list request.
  // Deep links open immediately after creation, so retry the read briefly
  // instead of misclassifying that live shell as a provider-resumable session.
  const delays = [0, 80, 160, 320]
  for (const delay of delays) {
    if (delay) await new Promise((resolve) => window.setTimeout(resolve, delay))
    const match = resolvePtyMetaById(await ccApi.list(), id)
    if (match) return match
  }
  return undefined
}

function toPtyEntity(m: CcSessionMeta): CcSessionEntity {
  const cwdLast = m.cwd.split(/[\\/]/).filter(Boolean).slice(-1)[0] || m.cwd
  const status: CcSessionStatus = m.status || (m.alive ? 'alive' : 'recoverable')
  const planShort = m.active_plan ? planShortName(m.active_plan) : ''
  const idTail = m.id.slice(-6)
  const resolvedTitle = m.display_title || m.provider_title
  const titleMain = resolvedTitle || planShort || cwdLast
  const title = resolvedTitle ? titleMain : `${titleMain} · ${idTail}`
  const tags: string[] = [status]
  if (planShort && cwdLast !== planShort) tags.push(cwdLast)
  const rel = relTime(m.started_at)
  if (rel) tags.push(rel)
  return {
    type: 'cc_session',
    kind: 'pty',
    id: m.id,
    title,
    cwd: m.cwd,
    alive: m.alive,
    status,
    cmd: m.cmd,
    startedAt: m.started_at,
    claudeSessionId: m.claude_session_id || null,
    providerSessionId: m.provider_session_id || null,
    providerTitle: resolvedTitle || null,
    activePlan: m.active_plan || null,
    provider: providerForPty(m),
    tags,
  }
}

export function toChatEntity(m: CcChatSessionMeta): CcSessionEntity {
  const cwdLast = m.cwd.split(/[\\/]/).filter(Boolean).slice(-1)[0] || m.cwd
  const status: CcSessionStatus = m.alive ? 'alive' : 'ended'
  const planShort = m.active_plan ? planShortName(m.active_plan) : ''
  const idTail = m.id.slice(-6)
  const titleMain = planShort || cwdLast
  const title = `${titleMain} · chat · ${idTail}`
  const tags: string[] = ['chat', status]
  if (planShort && cwdLast !== planShort) tags.push(cwdLast)
  const rel = relTime(m.started_at)
  if (rel) tags.push(rel)
  return {
    type: 'cc_session',
    kind: 'chat',
    id: m.id,
    title,
    cwd: m.cwd,
    alive: m.alive,
    status,
    cmd: m.cmd,
    startedAt: m.started_at,
    claudeSessionId: m.claude_session_id,
    activePlan: m.active_plan || null,
    provider: (m as any).provider || 'claude_code',
    adopted: !!(m as any).adopted,
    takenOver: !!(m as any).taken_over,
    tags,
  }
}

const resolver: EntityResolver<CcSessionEntity> = {
  type: 'cc_session',
  async fetch(id) {
    // chat session id 形如 "chat-XXXX", PTY 是 uuid hex.
    if (id.startsWith('chat-')) {
      const list = await ccChatApi.list().catch(() => [])
      const m = list.find((x) => x.id === id)
      if (m) return toChatEntity(m)
    } else {
      const m = await fetchPtyMetaById(id)
      if (m) return toPtyEntity(m)
    }
    return {
      type: 'cc_session', kind: 'pty', id, title: id.slice(0, 12),
      cwd: '?', alive: false, status: 'recoverable', cmd: [], startedAt: 0, tags: ['dead'],
    }
  },
  async list() {
    // 同时拉两路, 一个挂另一个不影响
    const [pty, chat] = await Promise.all([
      ccApi.list().catch(() => [] as CcSessionMeta[]),
      ccChatApi.list().catch(() => [] as CcChatSessionMeta[]),
    ])
    return [...pty.map(toPtyEntity), ...chat.map(toChatEntity)]
  },
}

// 重查看器懒加载: PTY 路线(xterm)实现在 ./EditorRouter, 打开 cc_session tab 才下载对应 chunk。
const EditorRouter = lazy(() => import('./EditorRouter'))

export const ccSessionRegistration: EntityRegistration<CcSessionEntity> = {
  resolver,
  renderer: { type: 'cc_session', Editor: EditorRouter },
  label: 'CLI / 对话',
  icon: <Terminal size={14} />,
}

export { ccApi } from '../../api/ccClient'
