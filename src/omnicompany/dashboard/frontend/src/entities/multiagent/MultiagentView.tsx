/**
 * Multiagent 工作面：只列 ccdaemon 当前仍持有的真实 runtime。
 *
 * 高频运行态来自 /api/cc/tab-states 的 2s 纯内存投影；adapter 语义摘要来自
 * residents/tail 的可见页 10s scanner 投影；材料走 Review WebSocket，轮询只做断线兜底。
 * 页面没有重复标题栏，控件只占一行；行展开态只由窗口联动(linked)触发，不再提供手动 Full 切换。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Folder,
  LayoutPanelLeft,
  Maximize2,
  Plus,
  RefreshCw,
  Square,
  TerminalSquare,
  Unlink,
} from 'lucide-react'
import {
  ccApi,
  ASSISTANT_TOPIC,
  rememberAssistantSession,
  rememberedAssistantSession,
  type AgentWorkerCatalog,
} from '../../api/ccClient'
import { ccChatApi } from '../../api/ccChatClient'
import { usePanels, type OpenedTab } from '../../stores/panelsStore'
import { useBackgroundPoll } from '../../shell/panelRuntime'
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
  isSessionCompletionUnseen,
  useSessionCompletionAttention,
} from '../cc_session/sessionAttention'
import {
  fetchResidents,
  fetchTail,
  type Resident,
  type SessionLineage,
  type TailLine,
} from './residentsClient'
import {
  isMultiagentLinkedSurface,
  multiagentLinkedUrl,
  openMultiagentLinkedWindow,
  useMultiagentLink,
} from './multiagentLink'
import { copyText } from '../../lib/copyText'
import { PROJECT_ICON_IDS, ProjectIcon } from '../../lib/projectIcon'
import { projectsApi, type ProjectItem } from '../../api/projectsClient'
import './multiagent.css'
import { BlueprintSelect } from '../../components/BlueprintSelect'
import { ProviderIcon } from './ProviderIcon'
import { sessionProjectionTitle } from '../cc_session/sessionProjection'

export function providerDisplayName(provider?: string | null): string {
  const names: Record<string, string> = {
    claude: 'Claude',
    claude_code: 'Claude',
    // 同 codex/codex_2: 主号不带后缀(它喂会话标题), 第二个账号才标出来。
    claude_2: 'Claude official 2',
    // 主号保持不带后缀: 它是默认账号, 且这个名字还喂会话标题 —— 加后缀会把
    // 所有现存 Codex 会话的标题一起改掉, 纯噪音。第二个账号才需要标出来。
    codex: 'Codex',
    codex_2: 'Codex official 2',
    codex_the_company: 'the_company API',
    codex_glm: 'GLM Coding Plan',
    codebuddy: 'CodeBuddy',
    gemini: 'Gemini',
    antigravity: 'Antigravity',
    kimi: 'Kimi',
    opencode: 'OpenCode',
    kiro: 'Kiro',
    qoder: 'Qoder',
    dsh: 'DeepSeek Harness',
    dsh_web: 'DeepSeek Harness',
    // zcode 已从可启动列表移除 (2026-08-17, GLM 改走官方 codex_glm 路线)。
    // 名称与图标保留, 只为让既有会话与历史仍能正确渲染。
    zcode: 'ZCode',
    grok: 'Grok Build',
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

function formatTokens(total?: number): string {
  if (!total) return ''
  if (total >= 1_000_000) return `${(total / 1_000_000).toFixed(total >= 10_000_000 ? 0 : 1)}m`
  if (total >= 1_000) return `${(total / 1_000).toFixed(total >= 100_000 ? 0 : 1)}k`
  return String(total)
}

const INSUFFICIENT_SUMMARY = /^(?:信息不足|资料不足|证据不足|无可用信息|暂无可用信息|暂无信息|无|[（(]无[）)]|进行中…?|Agent 工作中)(?:$|[，。；:：\s].*)/

/** Drop model uncertainty placeholders while retaining any useful segment. */
export function summaryDisplayText(...values: Array<string | null | undefined>): string {
  for (const value of values) {
    const segments = String(value || '')
      .split(/\s*·\s*/)
      .map((segment) => segment.trim())
      .filter((segment) => segment && !INSUFFICIENT_SUMMARY.test(segment))
    if (segments.length > 0) return segments.join(' · ')
  }
  return ''
}

export function sessionDisplayTitle({
  provider,
  sessionId,
  topic,
  summaryTitle,
  nativeTitle,
  firstMessage,
  preview,
}: {
  provider?: string | null
  sessionId: string
  topic?: string | null
  summaryTitle?: string | null
  nativeTitle?: string | null
  firstMessage?: string | null
  preview?: string | null
}): string {
  // 标题只接受三个来源：显式真实主题、provider 原生标题/首条消息、稳定默认值。
  // digest title、当前进度、随机 Agent 名和页签旧标题都不参与，避免把推测当身份。
  return sessionProjectionTitle({ provider, sessionId, topic, summaryTitle, nativeTitle, firstMessage, preview })
}

export function sessionLineageBadge(
  lineage?: SessionLineage,
  runtimeOrigin?: 'human' | 'agent',
): string {
  if (lineage?.kind === 'derived') {
    if (lineage.origin === 'ai') return 'AI 派生'
    if (lineage.origin === 'human') return '人工派生'
    return '派生'
  }
  if (lineage?.origin === 'ai' || runtimeOrigin === 'agent') return 'AI 创建'
  return ''
}

/**
 * 折叠组判据：LiTeWork 宿主的会话，以及 AI 自己拉起/派生的会话。
 * 机主在这台机器上亲手开的对话留在主列表；其余整组折起来，让主列表只剩「我在跟谁说话」。
 */
export function isFoldedSession(
  meta?: Pick<CcTabSessionMetaLite, 'runtimeHost' | 'origin'> | null,
  resident?: Pick<Resident, 'lineage' | 'origin'>,
): boolean {
  if (meta?.runtimeHost === 'litework') return true
  // Residents projects the canonical current owner. A user may take over an
  // AI-created worker without rewriting its creation lineage or restarting it.
  if (meta?.origin === 'human' || resident?.origin === 'human') return false
  if (resident?.lineage?.origin === 'ai') return true
  return (meta?.origin || resident?.origin) === 'agent'
}

function lineageAgentLabel(lineage?: SessionLineage): string {
  if (!lineage) return ''
  return summaryDisplayText(lineage.agent_name)
    || summaryDisplayText(lineage.agent_path?.split('/').filter(Boolean).pop()?.replace(/[_-]+/g, ' '))
}

export function lineageParentLabel(lineage?: SessionLineage): string {
  if (!lineage?.parent_session_id) return ''
  return summaryDisplayText(lineage.parent_title) || lineage.parent_session_id.slice(0, 12)
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

const VOID_PROJECTS = new Set([
  '', '无', '信息不足', '未归属', '未绑定', '未知项目',
  'WindowsWorkspace', 'Windows工作区', 'AIWorkSpace',
])

function projectKey(value?: string | null): string {
  return String(value || '').trim().toLocaleLowerCase().replace(/[\s_-]+/g, '')
}

/**
 * Resolve a resident projection back to the registered project catalog.
 * Scanner/digest labels are evidence only: workspace folder names and other
 * free text must never become project identities in the UI. Cwd is
 * deliberately ignored here; conversation content or an explicit binding is
 * the project evidence for this surface.
 */
export function canonicalResidentProject(resident?: Resident, projects: ProjectItem[] = []): string {
  const label = String(resident?.project || '').trim()
  if (!label || VOID_PROJECTS.has(label) || /^(windows\s*workspace|windows工作区|ai\s*workspace)(?:\b|\s|工具|项目)/i.test(label)) return ''
  const key = projectKey(label)
  const catalog: ProjectItem[] = projects.length > 0
    ? projects
    : PROJECT_ICON_IDS.map((id) => ({ id, name: id, group: '' }))
  const exact = catalog.find((project) => [project.id, project.name, project.short]
    .some((alias) => projectKey(alias) === key))
  if (exact) return exact.id

  const contextual = catalog
    .flatMap((project) => [project.id, project.name, project.short]
      .filter(Boolean)
      .map((alias) => ({ project, alias: projectKey(alias) })))
    .filter(({ alias }) => alias.length >= 2 && key.includes(alias))
    .sort((left, right) => right.alias.length - left.alias.length)
  if (contextual.length > 0 && contextual.every(({ project }) => project.id === contextual[0].project.id)) {
    return contextual[0].project.id
  }
  return ''
}

export interface SessionProjectGroup<T> {
  key: string
  projectId: string
  rows: T[]
}

/** Group live session rows by their canonical project, keeping list order. */
export function groupSessionRows<T extends { projectId?: string | null }>(
  rows: ReadonlyArray<T>,
): SessionProjectGroup<T>[] {
  const groups = new Map<string, SessionProjectGroup<T>>()
  for (const row of rows) {
    const projectId = String(row.projectId || '').trim()
    const key = projectId || '__unbound__'
    const group = groups.get(key)
    if (group) {
      group.rows.push(row)
    } else {
      groups.set(key, { key, projectId, rows: [row] })
    }
  }
  // Keep unresolved sessions visible but out of the way after known projects,
  // matching Todo's dedicated ungrouped bucket.
  return [...groups.values()].sort((left, right) => (
    Number(!left.projectId) - Number(!right.projectId)
  ))
}

export function sessionProjectGroupLabel(projectId: string, projects: ProjectItem[]): string {
  if (!projectId) return '未绑定项目'
  const project = projects.find((item) => item.id === projectId)
  return project?.name || project?.short || projectId
}

/** Resolve a row's project from the authoritative id or content-derived label. */
export function canonicalSessionProject(
  meta?: Pick<CcTabSessionMetaLite, 'project' | 'cwd'> | null,
  resident?: Resident,
  projects: ProjectItem[] = [],
): string {
  const explicit = String(meta?.project || '').trim()
  return canonicalResidentProject({
    ...resident,
    project: explicit || resident?.project,
    cwd: meta?.cwd || resident?.cwd,
  } as Resident, projects)
}

export function planDisplayName(planId?: string | null, title?: string | null): string {
  const explicit = summaryDisplayText(title)
  if (explicit) return explicit
  const leaf = String(planId || '').split(/[\\/]/).filter(Boolean).pop() || ''
  return leaf
    .replace(/^\[\d{4}-\d{2}-\d{2}\]\s*/, '')
    .replace(/[-_]+/g, ' ')
    .trim()
}

export type SessionVisibility = 'visible' | 'hidden' | 'background'

export function resolveSessionVisibility(
  sessionId: string,
  meta?: Pick<CcTabSessionMetaLite, 'visibility'> | null,
  placements?: Readonly<Record<string, string>>,
): SessionVisibility {
  const overlay = placements?.[sessionId]
  if (overlay === 'hidden' || overlay === 'background' || overlay === 'visible') return overlay
  if (meta?.visibility === 'hidden' || meta?.visibility === 'background' || meta?.visibility === 'visible') {
    return meta.visibility
  }
  return 'visible'
}

export function isListedInMultiagent(visibility: SessionVisibility): boolean {
  return visibility !== 'hidden'
}

export function multiagentSortRank(visibility: SessionVisibility, state?: CcTabRunState): number {
  if (visibility === 'background') return 10
  if (state === 'working') return 0
  // A transient unknown/done observation must not shuffle the non-running
  // list. Only a real working transition is allowed to cross its boundary.
  return 1
}

/**
 * One deterministic order for the live-session surface.
 *
 * Backend snapshot order is incidental: reconnects and polling can change it
 * even when no session changed.  Anchor each state group by immutable
 * ``startedAt`` (oldest first, so a new session appends rather than pushing a
 * stable list down) and then by id.  Consequently ordinary refreshes, title
 * changes, summaries, project hydration, and done↔unknown uncertainty cannot
 * move a row; only entering/leaving ``working`` can cross the group boundary.
 */
export function stableMultiagentSessionIds(
  sessionIds: ReadonlyArray<string>,
  snapshot: TabStatusSnapshot,
  placements: Readonly<Record<string, string>> = {},
): string[] {
  const startedAt = (sessionId: string): number => {
    const value = snapshot.metas[sessionId]?.startedAt
    return typeof value === 'number' && Number.isFinite(value)
      ? value
      : Number.POSITIVE_INFINITY
  }
  return [...sessionIds].sort((left, right) => (
    multiagentSortRank(
      resolveSessionVisibility(left, snapshot.metas[left], placements),
      snapshot.states[left],
    ) - multiagentSortRank(
      resolveSessionVisibility(right, snapshot.metas[right], placements),
      snapshot.states[right],
    )
    || startedAt(left) - startedAt(right)
    || left.localeCompare(right)
  ))
}

// 新会话选择记忆(用户 2026-09-19: "默认模型要改成上一次创建时的")。
// 只在真正创建成功时落盘, 所以记住的是"上一次建的", 不是随手点开又改回去的那一次。
// 模型按执行者分别记、推理强度按执行者+模型分别记: 换执行者不该把别家的模型名带过去。
const NEW_SESSION_PREF_KEY = 'omni.multiagent.newSession'

interface NewSessionPref {
  kind?: string
  model?: Record<string, string>
  effort?: Record<string, string>
}

export function readNewSessionPref(): NewSessionPref {
  try {
    const raw = window.localStorage.getItem(NEW_SESSION_PREF_KEY)
    const value = raw ? JSON.parse(raw) : null
    return value && typeof value === 'object' ? value as NewSessionPref : {}
  } catch {
    return {}
  }
}

export function writeNewSessionPref(kind: string, model: string, effort: string): void {
  if (!kind) return
  try {
    const prev = readNewSessionPref()
    window.localStorage.setItem(NEW_SESSION_PREF_KEY, JSON.stringify({
      kind,
      model: { ...(prev.model || {}), [kind]: model },
      effort: { ...(prev.effort || {}), [`${kind}:${model}`]: effort },
    } satisfies NewSessionPref))
  } catch {
    // privacy mode / 无 localStorage: 记不住不影响新建
  }
}

/** 刚建出来的会话最多认领这么久; 之后一律以 /api/cc/sessions 的存活集合为准。 */
export const PENDING_RUNTIME_CLAIM_MS = 60_000

/**
 * 刚建好的会话先认下, 别等下一轮 hydrate。
 *
 * runtimeTabs 拿 activePtyRuntimeIds 当"还活着"的闸门, 而那份集合来自 30s 一轮的
 * hydrateRuntimeMetas。新建时直接用拉回来的集合整份覆盖, 会把乐观加进去的新会话再抹掉
 * (真机上 /api/cc/sessions 偶尔还没把刚起的 PTY 算进去), 表现就是新行闪一下又没了。
 * 认领期内取并集, 一旦接口自己报出这个 id, 或者认领超时, 就交还给接口。
 */
export function mergePendingRuntimeIds(
  fetched: Set<string>,
  pending: Map<string, number>,
  now: number = Date.now(),
): Set<string> {
  for (const [id, since] of pending) {
    if (fetched.has(id) || now - since > PENDING_RUNTIME_CLAIM_MS) pending.delete(id)
    else fetched.add(id)
  }
  return fetched
}

/** 记住的值只有仍在目录里时才算数 —— 空串是"CLI 默认"这个合法选项, 不是"没记过"。 */
export function rememberedModelFor(
  pref: NewSessionPref, kind: string, models: readonly string[],
): string | null {
  if (!pref.model || !Object.prototype.hasOwnProperty.call(pref.model, kind)) return null
  const value = String(pref.model[kind] || '')
  if (value && !models.includes(value)) return null
  return value
}

export function runtimeTabs(
  tabs: OpenedTab[],
  snapshot: TabStatusSnapshot,
  activePtyRuntimeIds: ReadonlySet<string> | null,
  activeChatRuntimeIds: ReadonlySet<string> | null,
  placements: Readonly<Record<string, string>> = {},
): OpenedTab[] {
  const bySessionId = new Map(
    tabs.filter((tab) => tab.ref.type === 'cc_session').map((tab) => [tab.ref.id, tab]),
  )
  const seenPtyProviderSessions = new Set<string>()
  const activeIds = Object.keys(snapshot.metas).filter((sessionId) => {
    if (snapshot.states[sessionId] === 'ended') return false
    const meta = snapshot.metas[sessionId]
    const visibility = resolveSessionVisibility(sessionId, meta, placements)
    if (!isListedInMultiagent(visibility)) return false
    // Older ccdaemon builds projected every recoverable structured-chat record
    // as alive. The full metadata endpoint already carries runtime_alive, so
    // use that authoritative bit until the daemon can be restarted safely.
    if (meta?.kind === 'chat') return activeChatRuntimeIds === null || activeChatRuntimeIds.has(sessionId)
    if (meta?.resumeAliasFor) return false
    if (activePtyRuntimeIds !== null) return activePtyRuntimeIds.has(sessionId)
    // Compatibility with a running daemon that predates resume_alias_for. Its
    // snapshot emits canonical sessions before old-id aliases, all sharing the
    // same provider-native id, so retain only the first projection.
    if (meta?.providerSessionId) {
      if (seenPtyProviderSessions.has(meta.providerSessionId)) return false
      seenPtyProviderSessions.add(meta.providerSessionId)
    }
    return true
  })
  const activeSet = new Set(activeIds)
  const orderedIds = [
    ...tabs.filter((tab) => tab.ref.type === 'cc_session' && activeSet.has(tab.ref.id)).map((tab) => tab.ref.id),
    ...activeIds.filter((sessionId) => !bySessionId.has(sessionId)),
  ]
  return stableMultiagentSessionIds(orderedIds, snapshot, placements)
    .map((sessionId) => bySessionId.get(sessionId) || ({
      id: `cc_session:${sessionId}`,
      ref: { type: 'cc_session', id: sessionId },
      title: snapshot.metas[sessionId]?.title || `${providerDisplayName(snapshot.metas[sessionId]?.provider)} · ${sessionId.slice(0, 8)}`,
    } as OpenedTab))
}

export function residentFor(sessionId: string, meta: CcTabSessionMetaLite | undefined, residents: Resident[]): Resident | undefined {
  const providerSessionIds = new Set([meta?.providerSessionId, sessionId].filter(Boolean))
  // A PTY may host several nested/native provider sessions over its lifetime.
  // Exact provider identity must beat the shared PTY alias or row ordering can
  // attach another child's title/project to the visible terminal.
  return residents.find((resident) => providerSessionIds.has(resident.session_id))
    || residents.find((resident) => resident.pty_id === sessionId)
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
    lastSubmitAt: meta.lastSubmitAt ?? hydrated.lastSubmitAt,
    providerSessionId: meta.providerSessionId ?? hydrated.providerSessionId,
    tokenUsage: meta.tokenUsage ?? hydrated.tokenUsage,
    origin: meta.origin ?? hydrated.origin,
    visibility: meta.visibility ?? hydrated.visibility,
  }
}

function lastLine(lines: TailLine[], roles: string[]): TailLine | undefined {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (roles.includes(String(lines[index].role || '').toLowerCase()) && lines[index].text?.trim()) return lines[index]
  }
  return undefined
}

function SessionRow({
  tab,
  meta,
  state,
  resident,
  projects,
  tail,
  full,
  selected,
  visibility = 'visible',
  needsAttention,
  pendingMaterials,
  onSelect,
  onOpenLocal,
  onKill,
}: {
  tab: OpenedTab
  meta: CcTabSessionMetaLite | undefined
  state: CcTabRunState
  resident?: Resident
  projects: ProjectItem[]
  tail: TailLine[]
  full: boolean
  selected: boolean
  visibility?: SessionVisibility
  needsAttention: boolean
  pendingMaterials: Array<{ id: string; title: string }>
  onSelect: (tab: OpenedTab, state: CcTabRunState, meta: CcTabSessionMetaLite | undefined) => void
  onOpenLocal: (tab: OpenedTab) => void
  onKill: (tab: OpenedTab, kind: CcTabSessionKind) => Promise<void>
}) {
  const sessionId = tab.ref.id
  const kind: CcTabSessionKind = meta?.kind ?? (sessionId.startsWith('chat-') ? 'chat' : 'pty')
  const cwd = meta?.cwd || resident?.cwd || ''
  const provider = meta?.provider || resident?.provider
  const providerName = providerDisplayName(provider)
  const lineage = resident?.lineage
  const lineageTask = lineage?.kind === 'derived' ? summaryDisplayText(lineage.task) : ''
  const lineageBadge = sessionLineageBadge(lineage, meta?.origin || resident?.origin)
  const parentLabel = lineageParentLabel(lineage)
  const agentLabel = lineageAgentLabel(lineage)
  const lineageContext = parentLabel
    ? `来自 ${parentLabel}${agentLabel ? ` · ${agentLabel}` : ''}`
    : agentLabel
  // The tab-state projection is the shared identity contract. Residents can
  // supply operational context but never replace a session's confirmed title.
  const title = meta?.title || sessionDisplayTitle({
    provider,
    sessionId,
    topic: resident?.topic,
    firstMessage: resident?.initial_task,
    preview: resident?.preview,
  })
  const project = canonicalSessionProject(meta, resident, projects)
  const planId = meta?.activePlan || resident?.active_plan || ''
  const planName = planDisplayName(planId, resident?.active_plan_title)
  const workstream = meta?.workstream || resident?.workstream || ''
  const workstreamName = resident?.workstream_name || workstream.split('.').slice(1).join('.')
  const currentTask = summaryDisplayText(
    resident?.current_task,
    resident?.initial_task,
    resident?.preview,
  )
  const latestStep = summaryDisplayText(
    resident?.last_step,
    meta?.lastMessage,
    lastLine(tail, ['assistant', 'agent'])?.text,
  )
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
    ...(lineage?.parent_session_id ? [{
      label: '复制父会话 ID',
      icon: <Copy size={15} />,
      onClick: () => { void copyText(lineage.parent_session_id || '') },
    }] : []),
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
      data-visibility={visibility}
      data-attention={needsAttention ? '1' : '0'}
      data-lineage-kind={lineage?.kind || 'root'}
      data-lineage-origin={lineage?.origin || (meta?.origin === 'agent' ? 'ai' : 'unknown')}
    >
      <button
        type="button"
        className="ma-row-hit"
        onClick={() => onSelect(tab, state, meta)}
        aria-label={`选择 ${title}${lineageBadge ? `，${lineageBadge}` : ''}${lineageContext ? `，${lineageContext}` : ''}，${providerName}，${runStateLabel(state)}${needsAttention ? '，有未查看结果' : ''}`}
      >
        <span className="ma-identities" title={`${project || '未绑定项目'} · ${providerName}`}>
          <span className="ma-project-mark" data-bound={project ? '1' : '0'}>
            {project ? <ProjectIcon id={project} size={34} /> : <Folder size={22} />}
          </span>
          <ProviderIcon provider={provider} label={providerName} size={16} />
        </span>
        <span className="ma-row-copy">
          <span className="ma-row-title-line">
            {lineageBadge ? <span className="ma-lineage-badge">{lineageBadge}</span> : null}
            {meta?.runtimeHost === 'litework' ? <span className="ma-lineage-badge" data-testid="session-litework-badge">LiTeWork</span> : null}
            {visibility === 'background' ? <span className="ma-lineage-badge" data-testid="session-background-badge">后置</span> : null}
            <span className="ma-row-title" title={title}>{title}</span>
          </span>
          <span className="ma-row-context">
            <span className="ma-project-name" data-authoritative={resident?.authoritative ? '1' : '0'} title={project || '该会话尚未绑定项目'}>
              {project || '未绑定项目'}
            </span>
            {workstream ? <span className="ma-context-sep" aria-hidden>·</span> : null}
            {workstream ? <span className="ma-workstream-name" title={workstream}>{workstreamName}</span> : null}
            {planId ? <span className="ma-context-sep" aria-hidden>·</span> : null}
            {planId ? <span className="ma-plan-name" title={planId}>{planName || planId}</span> : null}
            {meta?.webSessionCount && meta.webSessionCount > 1 ? <span className="ma-context-sep" aria-hidden>·</span> : null}
            {meta?.webSessionCount && meta.webSessionCount > 1 ? (
              <span className="ma-workstream-name" title={`该 dsh host 内有 ${meta.webSessionCount} 个会话`}>{meta.webSessionCount} 个会话</span>
            ) : null}
          </span>
          {lineageContext ? (
            <span className="ma-lineage-context" title={lineage?.parent_session_id || lineageContext}>
              {lineageContext}
            </span>
          ) : null}
        </span>
        {meta?.tokenUsage?.total ? (
          <span className="ma-token bp-dim" title="Provider 回传的会话累计 Token">
            <i aria-hidden />{formatTokens(meta.tokenUsage.total)} tok<i aria-hidden />
          </span>
        ) : null}
        {pendingMaterials.length > 0 ? (
          <span className="ma-material-count v2-count" title="未审阅材料">{pendingMaterials.length}</span>
        ) : null}
      </button>
      <KebabMenu testid="active-process-more" items={menu} />
      {full && (
        <div className="ma-detail">
          <div className="ma-detail-main">
            {lineageContext && <p><span>来源</span>{lineageBadge} · {lineageContext}</p>}
            {lineageTask && <p><span>职责</span>{lineageTask}</p>}
            {currentTask && currentTask !== lineageTask && <p><span>当前</span>{currentTask}</p>}
            {latestStep && <p><span>最新</span>{latestStep}</p>}
            {latestUser && <p><span>输入</span>{latestUser}</p>}
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
  const activeTabId = usePanels((state) => state.activeId)
  const snapshot = useCcTabStatusSnapshot()
  const reviewVersion = useReviewStream((state) => state.version)
  const reviewConnected = useReviewStream((state) => state.connected)
  const reviewMaterials = useReviewStream((state) => state.materials)
  const seenCompletions = useSessionCompletionAttention((state) => state.seen)
  const markCompletionSeen = useSessionCompletionAttention((state) => state.markSeen)
  const link = useMultiagentLink()
  const linkedSurface = isMultiagentLinkedSurface()
  const [full, setFull] = useState(false)
  // LiTeWork / AI 创建的会话整组折起来, 默认收拢: 主列表只留机主亲手开的对话。
  const [foldedOpen, setFoldedOpen] = useState(false)
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({})
  const [sessionCatalog, setSessionCatalog] = useState<AgentWorkerCatalog | null>(null)
  const [newSessionKind, setNewSessionKind] = useState('')
  const [newSessionModel, setNewSessionModel] = useState('')
  const [newSessionEffort, setNewSessionEffort] = useState('')
  const [creating, setCreating] = useState(false)
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [residents, setResidents] = useState<Resident[]>([])
  const [projects, setProjects] = useState<ProjectItem[]>([])
  const [tails, setTails] = useState<Record<string, TailLine[]>>({})
  const [hydratedMetas, setHydratedMetas] = useState<Record<string, Partial<CcTabSessionMetaLite>>>({})
  const [activePtyRuntimeIds, setActivePtyRuntimeIds] = useState<ReadonlySet<string> | null>(null)
  const pendingRuntimeIdsRef = useRef<Map<string, number>>(new Map())
  const [activeChatRuntimeIds, setActiveChatRuntimeIds] = useState<ReadonlySet<string> | null>(null)
  const [error, setError] = useState('')
  const [placements, setPlacements] = useState<Record<string, string>>({})
  const sessionOptions = sessionCatalog?.options || []
  const selectedSessionOption = sessionOptions.find((option) => option.id === newSessionKind)
  const sessionModels = selectedSessionOption?.models || []
  const sessionEfforts = selectedSessionOption && newSessionModel
    ? (selectedSessionOption.model_efforts?.[newSessionModel] || [])
    : []

  const sessionTabs = useMemo(
    () => runtimeTabs(tabs, snapshot, activePtyRuntimeIds, activeChatRuntimeIds, placements),
    [activeChatRuntimeIds, activePtyRuntimeIds, placements, snapshot, tabs],
  )

  useEffect(() => useReviewStream.getState().acquire(), [])

  useEffect(() => {
    let active = true
    void ccApi.agentWorkerCatalog().then((catalog) => {
      if (!active) return
      setSessionCatalog(catalog)
      setNewSessionKind((current) => {
        if (current && catalog.options.some((option) => option.id === current)) return current
        const remembered = readNewSessionPref().kind
        if (remembered && catalog.options.some((option) => option.id === remembered)) return remembered
        return catalog.options[0]?.id || ''
      })
    }).catch((cause) => {
      if (active) setError(`执行者目录不可用：${cause instanceof Error ? cause.message : String(cause)}`)
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!selectedSessionOption) {
      setNewSessionModel('')
      setNewSessionEffort('')
      return
    }
    const models = selectedSessionOption.models || []
    const pref = readNewSessionPref()
    const remembered = rememberedModelFor(pref, newSessionKind, models)
    const model = remembered !== null
      ? remembered
      : (selectedSessionOption.default_model && models.includes(selectedSessionOption.default_model)
        ? selectedSessionOption.default_model
        : '')
    const efforts = model ? (selectedSessionOption.model_efforts?.[model] || []) : []
    const rememberedEffort = pref.effort?.[`${newSessionKind}:${model}`] || ''
    const preferredEffort = (rememberedEffort && efforts.includes(rememberedEffort))
      ? rememberedEffort
      : (selectedSessionOption.model_default_efforts?.[model]
        || selectedSessionOption.default_effort
        || '')
    setNewSessionModel(model)
    setNewSessionEffort(efforts.includes(preferredEffort) ? preferredEffort : (efforts[0] || ''))
  }, [newSessionKind, selectedSessionOption])

  const hydrateRuntimeMetas = useCallback(async () => {
    const [ptyResult, chatResult] = await Promise.allSettled([
      ccApi.list({ includeRecoverable: false }),
      ccChatApi.list({ limit: 500, includeArchived: false }),
    ])
    const next: Record<string, Partial<CcTabSessionMetaLite>> = {}
    if (ptyResult.status === 'fulfilled') {
      setActivePtyRuntimeIds(mergePendingRuntimeIds(
        new Set(ptyResult.value.map((meta) => meta.id)),
        pendingRuntimeIdsRef.current,
      ))
      for (const meta of ptyResult.value) {
        next[meta.id] = {
          startedAt: meta.started_at,
          lastActivityAt: Math.max(meta.last_submit_at || 0, meta.last_output_at || 0) || undefined,
          lastSubmitAt: meta.last_submit_at || undefined,
          providerSessionId: meta.provider_session_id || undefined,
          origin: meta.origin,
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

  // 后台节流(2026-08-07 页签常驻): 页签切走 / 窗口不在前台就挂起, 回来立刻补一次。
  // Activity animation is fed by the lightweight tab-state stream. Session
  // metadata is enrichment only, so keep it out of the interactive budget.
  useBackgroundPoll(hydrateRuntimeMetas, 30_000)

  const refreshDetails = useCallback(async () => {
    try {
      const response = await fetchResidents()
      setResidents(response.residents || [])
      setError('')
    } catch (cause) {
      setError(`动态索引暂时不可用：${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }, [])

  // Residents can trigger registry reconciliation on older ccdaemon builds.
  // Refresh immediately on visibility restore, then only once a minute.
  useBackgroundPoll(refreshDetails, 60_000)

  const refreshProjects = useCallback(async () => {
    try {
      const board = await projectsApi.list()
      setProjects(board.projects || [])
    } catch {
      // Keep the deterministic registered-id fallback; project discovery must
      // not make the runtime list unavailable.
    }
  }, [])

  useBackgroundPoll(refreshProjects, 300_000)

  const refreshPlacements = useCallback(async () => {
    try {
      const response = await fetch('/api/boss-sight/session-placements')
      if (!response.ok) return
      const payload = await response.json() as { placements?: Record<string, string> }
      setPlacements(payload.placements || {})
    } catch {
      // Overlay is optional; tab-state visibility still works after ccdaemon picks it up.
    }
  }, [])

  useBackgroundPoll(refreshPlacements, 15_000)

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

  const loadTails = useCallback(async () => {
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
    setTails(Object.fromEntries(entries))
  }, [])

  useBackgroundPoll(loadTails, 30_000, { enabled: full })

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

  const selectSession = useCallback((
    tab: OpenedTab,
    state: CcTabRunState,
    meta: CcTabSessionMetaLite | undefined,
  ) => {
    if (state === 'done') markCompletionSeen(tab.ref.id, meta)
    if (link.connected || linkedSurface) {
      link.publishSelection(tab.ref.id)
      setFull(true)
      return
    }
    openLocal(tab)
  }, [link, linkedSurface, markCompletionSeen, openLocal])

  const killSession = useCallback(async (tab: OpenedTab, kind: CcTabSessionKind) => {
    const sessionId = tab.ref.id
    if (typeof window !== 'undefined' && !window.confirm(`终止会话 ${sessionId.slice(0, 8)}？`)) return
    if (kind === 'pty') await ccApi.kill(sessionId)
    else await ccChatApi.kill(sessionId)
    usePanels.getState().closeTab(tab.id)
    await refreshCcTabStates()
  }, [])

  const selectNewSessionModel = useCallback((model: string) => {
    setNewSessionModel(model)
    const efforts = selectedSessionOption?.model_efforts?.[model]
      || []
    const preferred = selectedSessionOption?.model_default_efforts?.[model] || ''
    setNewSessionEffort(efforts.includes(preferred) ? preferred : (efforts[0] || ''))
  }, [selectedSessionOption])

  const createSession = useCallback(async () => {
    if (creating) return
    setCreating(true)
    setError('')
    try {
      const option = sessionOptions.find((item) => item.id === newSessionKind)
      if (!option) throw new Error('执行者目录尚未就绪')
      const meta = option.provider
        ? await ccApi.createAgentWorker({
          provider: option.provider,
          model: newSessionModel || option.default_model || undefined,
          effort: newSessionEffort || undefined,
          visibility: 'visible',
        })
        : await ccApi.create({ cmd: option.cmd })
      writeNewSessionPref(newSessionKind, newSessionModel, newSessionEffort)
      // 不先认下刚建的这个 id, 新会话要等下一轮 30s hydrate 才进列表
      // (用户 2026-09-19: 新建的对话要过好久才出现)。先乐观落地, 再让 hydrate 去对账。
      pendingRuntimeIdsRef.current.set(meta.id, Date.now())
      setActivePtyRuntimeIds((prev) => (prev === null ? prev : new Set([...prev, meta.id])))
      usePanels.getState().openTab({ type: 'cc_session', id: meta.id }, `${option.label} · ${meta.id.slice(0, 8)}`)
      await refreshCcTabStates()
      void hydrateRuntimeMetas()
    } catch (cause) {
      setError(`新建失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setCreating(false)
    }
  }, [creating, hydrateRuntimeMetas, newSessionEffort, newSessionKind, newSessionModel, sessionOptions])

  // assistant = 机主的助理(盯全机进度、派活、记事, 自己不干活); 规矩在它的落脚目录
  // C:/workspace/assistant 的 AGENTS.md 里, 后端按模型阶梯挑 provider/model。
  // 单一持久会话: 永远复用同一个活着的 assistant 会话, 只有不存在/已死才新建。
  // 不进会话列表(visibility=hidden), 入口只有这个按钮。
  const openAssistant = useCallback(async () => {
    if (assistantBusy) return
    setAssistantBusy(true)
    setError('')
    try {
      const remembered = rememberedAssistantSession()
      const sessions = await ccApi.list({ includeRecoverable: false })
      const live = sessions.find((item) => (
        item.alive && remembered !== null && item.id === remembered
      )) ?? sessions.find((item) => item.alive && item.topic === ASSISTANT_TOPIC)
      const assistant = sessionCatalog?.assistant
      if (!live && !assistant?.provider) throw new Error('assistant 执行者目录尚未就绪')
      const meta = live ?? await ccApi.createAgentWorker({
        provider: assistant!.provider,
        model: assistant!.model || undefined,
        effort: assistant!.effort || undefined,
        topic: ASSISTANT_TOPIC,
        visibility: 'hidden',
      })
      rememberAssistantSession(meta.id)
      usePanels.getState().openTab(
        { type: 'cc_session', id: meta.id },
        `assistant · ${meta.id.slice(0, 8)}`,
      )
      await refreshCcTabStates()
    } catch (cause) {
      setError(`assistant 启动失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setAssistantBusy(false)
    }
  }, [assistantBusy, sessionCatalog])

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

  const rows = useMemo(() => sessionTabs.map((tab) => {
    const meta = withHydratedMeta(snapshot.metas[tab.ref.id], hydratedMetas[tab.ref.id])
    const kind: CcTabSessionKind = meta?.kind ?? (tab.ref.id.startsWith('chat-') ? 'chat' : 'pty')
    const resident = residentFor(tab.ref.id, meta, residents)
    const ids = [tab.ref.id, meta?.providerSessionId, resident?.session_id].filter(Boolean) as string[]
    return {
      tab,
      meta,
      kind,
      resident,
      projectId: canonicalSessionProject(meta, resident, projects),
      visibility: resolveSessionVisibility(tab.ref.id, meta, placements),
      pendingMaterials: ids.flatMap((id) => pendingBySession.get(id) || []),
      folded: isFoldedSession(meta, resident),
    }
  }), [hydratedMetas, pendingBySession, placements, projects, residents, sessionTabs, snapshot.metas])
  const primaryRows = rows.filter((row) => !row.folded)
  const foldedRows = rows.filter((row) => row.folded)
  const primaryGroups = groupSessionRows(primaryRows)
  const foldedGroups = groupSessionRows(foldedRows)

  const renderRow = (row: (typeof rows)[number]) => {
    const state = runStateFor(snapshot, row.tab.ref.id, row.kind)
    return (
      <SessionRow
        key={row.tab.id}
        tab={row.tab}
        meta={row.meta}
        state={state}
        resident={row.resident}
        projects={projects}
        tail={tails[row.tab.ref.id] || []}
        full={full}
        visibility={row.visibility}
        selected={link.selectedSessionId === row.tab.ref.id || activeTabId === row.tab.id}
        needsAttention={isSessionCompletionUnseen(state, row.tab.ref.id, row.meta, seenCompletions)}
        pendingMaterials={row.pendingMaterials}
        onSelect={selectSession}
        onOpenLocal={openLocal}
        onKill={killSession}
      />
    )
  }

  const toggleProjectGroup = useCallback((scope: 'primary' | 'folded', key: string) => {
    const stateKey = `${scope}:${key}`
    setCollapsedProjects((current) => ({ ...current, [stateKey]: !current[stateKey] }))
  }, [])

  const renderProjectGroups = (
    groups: SessionProjectGroup<(typeof rows)[number]>[],
    scope: 'primary' | 'folded',
  ) => groups.map((group) => {
    const stateKey = `${scope}:${group.key}`
    const collapsed = Boolean(collapsedProjects[stateKey])
    const testKey = group.projectId || 'unbound'
    return (
      <section
        key={stateKey}
        className="ma-project-group"
        data-testid={`multiagent-project-group-${testKey}`}
        data-project={group.projectId || undefined}
        data-collapsed={collapsed ? '1' : '0'}
      >
        <header className="ma-project-head">
          <button
            type="button"
            className="ma-project-head-hit"
            aria-expanded={!collapsed}
            onClick={() => toggleProjectGroup(scope, group.key)}
          >
            <ChevronDown size={13} className="ma-project-caret" aria-hidden />
            <span className="ma-project-group-label">
              {sessionProjectGroupLabel(group.projectId, projects)}
            </span>
            <span className="ma-project-group-count">{group.rows.length}</span>
          </button>
        </header>
        {!collapsed && <div className="ma-project-group-body">{group.rows.map(renderRow)}</div>}
      </section>
    )
  })

  const globalPending = Object.values(reviewMaterials).filter((material) => material.status === 'pending').length

  return (
    <div className="ma-root fp-scroll" data-testid="multiagent-view" data-linked-role={link.role}>
      <div className="ma-tools" aria-label="会话工具">
        <BlueprintSelect
          className="tm-select"
          value={newSessionKind}
          onChange={setNewSessionKind}
          label="执行者"
          aria-label="新会话执行者"
          data-testid="multiagent-new-provider"
          menuMinWidth={280}
          options={sessionOptions.map((option) => ({
            value: option.id,
            label: option.label,
            icon: option.provider
              ? <ProviderIcon provider={option.provider} label={option.label} size={16} />
              : <TerminalSquare size={14} aria-hidden />,
          }))}
        />
        {sessionModels.length > 0 ? (
          <BlueprintSelect
            className="tm-select"
            value={newSessionModel}
            onChange={selectNewSessionModel}
            label="模型"
            aria-label="新会话模型"
            data-testid="multiagent-new-model"
            menuMinWidth={260}
            options={[
              { value: '', label: 'CLI 默认' },
              ...sessionModels.map((model) => ({
                value: model,
                label: selectedSessionOption?.model_labels?.[model] || model,
              })),
            ]}
          />
        ) : null}
        {sessionEfforts.length > 0 ? (
          <BlueprintSelect
            className="tm-select"
            value={newSessionEffort}
            onChange={setNewSessionEffort}
            label="推理"
            aria-label="新会话推理强度"
            data-testid="multiagent-new-effort"
            menuMinWidth={160}
            options={sessionEfforts.map((effort) => ({ value: effort, label: effort }))}
          />
        ) : null}
        <button type="button" className="v2-btn primary sm" onClick={() => { void createSession() }} disabled={creating || !newSessionKind} data-testid="multiagent-new-session">
          <Plus size={13} />{creating ? '新建中…' : '新会话'}
        </button>
        <button
          type="button"
          className={`v2-btn sm${assistantBusy ? ' ma-assistant-busy' : ''}`}
          onClick={() => { void openAssistant() }}
          disabled={assistantBusy}
          data-testid="multiagent-assistant"
          title={`assistant：机主的助理，盯全机进度、派活、记事（${sessionCatalog?.assistant.label || sessionCatalog?.assistant.provider || '执行者'}${sessionCatalog?.assistant.model ? ` · ${sessionCatalog.assistant.model}` : ''}）`}
        >
          {assistantBusy ? <RefreshCw size={13} className="cccp-spinning" /> : <Bot size={13} />}assistant
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
      <div className="ma-list mo-stagger" data-testid="multiagent-list">
        {renderProjectGroups(primaryGroups, 'primary')}
        {foldedRows.length > 0 && (
          <>
            <button
              type="button"
              className="ma-fold-head"
              data-testid="multiagent-fold-toggle"
              data-open={foldedOpen ? '1' : '0'}
              aria-expanded={foldedOpen}
              onClick={() => setFoldedOpen((open) => !open)}
            >
              <ChevronRight size={14} className="ma-fold-caret" aria-hidden />
              <span className="ma-fold-label">LiTeWork 与 AI 创建</span>
              <span className="ma-fold-count">{foldedRows.length}</span>
            </button>
            {foldedOpen && renderProjectGroups(foldedGroups, 'folded')}
          </>
        )}
      </div>
    </div>
  )
}
