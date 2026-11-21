import { ccApi, type CcSessionMeta } from '../../api/ccClient'
import { ccChatApi, type CcChatSessionMeta } from '../../api/ccChatClient'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { lazy } from 'react'
import { Terminal } from 'lucide-react'
import { relTimeEn as relTime } from '../../lib/time'
import { sessionProjectionTitle } from './sessionProjection'

export type CcSessionStatus = 'alive' | 'recoverable' | 'ended'
/** New work is always PTY-backed. ``chat`` remains only for archived deep links.
 *  ``web`` = web-display session (iframe surface, e.g. DeepSeek Harness Web UI). */
export type CcSessionKind = 'pty' | 'chat' | 'web'

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
  /** Web-display sessions: iframe 地址(ready 后才有, starting 阶段为 null)。 */
  webUrl?: string | null
  webPhase?: string
  /** Agent Worker 主题(如 'assistant'); shell/chat 会话缺省。 */
  topic?: string | null
  project?: string | null
  /** #2 接管式采纳: adopted=resume 别处会话采纳来的(当 subagent); takenOver=用户已接管(总控不自动 hook)。 */
  adopted?: boolean
  takenOver?: boolean
}

/** plan_id `_infra/dashboard/[2026-05-03]CC-PLAN-SESSION-CONTEXT` → `CC-PLAN-SESSION-CONTEXT` */
function planShortName(planId: string): string {
  const last = planId.split('/').pop() || planId
  return last.replace(/^\[\d{4}-\d{2}-\d{2}\]/, '')
}

export function providerForPty(m: CcSessionMeta): string {
  const explicit = String(m.provider || '').trim().toLowerCase()
  const command = String(m.cmd?.[0] || '').toLowerCase()
  const fullCommand = String(m.cmd?.join(' ') || '').toLowerCase()
  // Older persisted PTYs were recorded as shell before provider detection was
  // moved into the daemon. Re-derive those rows from the immutable command so
  // the sidebar can still identify the actual CLI after a restart.
  const inferred = fullCommand.includes('dsh-web.py')
    ? 'dsh_web'
    : fullCommand.includes('omni-dsh-pty') || fullCommand.includes('dsh-pty.py')
      ? 'dsh'
      : command.includes('zcode')
        ? 'zcode'
        : command.includes('grok')
          ? 'grok'
          : command.includes('kiro')
            ? 'kiro'
            : command.includes('codebuddy') || command.includes('cbc')
              ? 'codebuddy'
              : command.includes('codex')
                ? 'codex'
                : command.includes('kimi')
                  ? 'kimi'
                  : command.includes('opencode')
                    ? 'opencode'
                    : command.includes('qodercli')
                      ? 'qoder'
                      : command.includes('powershell') || command.includes('pwsh')
                        ? 'shell'
                        : ''
  if (inferred && (!explicit || explicit === 'shell' || explicit === 'claude_code')) return inferred
  return explicit || inferred || 'claude_code'
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
  const resolvedTitle = sessionProjectionTitle({
    provider: providerForPty(m),
    sessionId: m.id,
    topic: m.topic,
    summaryTitle: m.display_title,
    nativeTitle: m.provider_title,
  })
  const title = resolvedTitle
  const tags: string[] = [status]
  if (planShort && cwdLast !== planShort) tags.push(cwdLast)
  const rel = relTime(m.started_at)
  if (rel) tags.push(rel)
  return {
    type: 'cc_session',
    kind: m.display_kind === 'web' ? 'web' : 'pty',
    id: m.id,
    title,
    cwd: m.cwd,
    alive: m.alive,
    status,
    cmd: m.cmd,
    startedAt: m.started_at,
    claudeSessionId: m.claude_session_id || null,
    providerSessionId: m.provider_session_id || null,
    providerTitle: m.provider_title || null,
    activePlan: m.active_plan || null,
    provider: providerForPty(m),
    webUrl: m.web_url || null,
    webPhase: m.web_phase,
    topic: m.topic || null,
    project: m.project || null,
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
    // Archived chat ids remain resolvable, but no product list creates or advertises them.
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
    const pty = await ccApi.list().catch(() => [] as CcSessionMeta[])
    return pty.map(toPtyEntity)
  },
}

// 重查看器懒加载: PTY 路线(xterm)实现在 ./EditorRouter, 打开 cc_session tab 才下载对应 chunk。
const EditorRouter = lazy(() => import('./EditorRouter'))

export const ccSessionRegistration: EntityRegistration<CcSessionEntity> = {
  resolver,
  renderer: { type: 'cc_session', Editor: EditorRouter },
  label: 'Agent Worker · CLI',
  icon: <Terminal size={14} />,
}

export { ccApi } from '../../api/ccClient'
