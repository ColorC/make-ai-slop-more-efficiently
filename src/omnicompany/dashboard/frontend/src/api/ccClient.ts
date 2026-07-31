/**
 * Native Claude Code / Codex lifecycle integration REST + WS client.
 * Backend: src/omnicompany/dashboard/ccdaemon/pty_routes.py
 */

const BASE = '/api/cc'
const PTY_CLIENT_PROTOCOL = 'focused-visible-v1'

export type AgentIntegrationProvider = 'claude_code' | 'codex'

export interface CcSessionMeta {
  id: string
  cmd: string[]
  cwd: string
  cols: number
  rows: number
  started_at: number
  alive: boolean
  /** True only after a real prompt submission while agent output is still active. */
  working?: boolean
  /** Whether this PTY has ever received a non-empty submitted user turn. */
  has_user_turn?: boolean
  last_submit_at?: number | null
  last_output_at?: number | null
  /** Runtime provider detected from the initial command or a CLI launched in a plain shell. */
  provider?: 'shell' | 'claude_code' | 'codex' | 'codebuddy' | 'kimi' | 'opencode' | string
  /** Provider-native durable conversation id used only after the PTY is gone. */
  provider_session_id?: string | null
  /** Native conversation title, when the provider exposes one. */
  provider_title?: string | null
  /** Best current UI title: cheap-model summary first, then native/first-turn fallback. */
  display_title?: string | null
  child_pid?: number | null
  host_pid?: number | null
  host_port?: number | null
  hosted?: boolean
  subscribers?: number
  buffered_chunks?: number
  buffered_bytes?: number
  replay_truncated?: boolean
  status?: 'alive' | 'recoverable'
  claude_session_id?: string | null
  active_plan?: string | null
  ended_at?: number | null
  exit_reason?: string | null
}

export interface SessionsList {
  items: CcSessionMeta[]      // alive
  alive_count: number
  recoverable: CcSessionMeta[]
  recoverable_count: number
}

/**
 * 页签活跃徽章的高频运行态投影 — 对应后端 GET /cc/tab-states。
 * 纯内存快照, 供前端 2s 轮询: 覆盖所有活会话(含别处驱动/后台页签),
 * 不依赖某个终端页签是否挂载, 刷新后首轮轮询即准。
 */
export interface CcTabStatesPayload {
  pty: Array<Pick<CcSessionMeta, 'id' | 'alive' | 'working' | 'has_user_turn' | 'status' | 'provider' | 'cwd' | 'provider_title' | 'display_title' | 'provider_session_id' | 'started_at' | 'last_submit_at' | 'last_output_at'>>
  chat: Array<{
    id: string
    alive: boolean
    runtime_alive?: boolean
    running: boolean
    status: 'alive' | 'ended'
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
  }>
}

export interface CreateSessionBody {
  cmd?: string[]
  cwd?: string
  cols?: number
  rows?: number
  safe_mode?: boolean
}

export interface ResumeProviderSessionBody {
  provider: string
  provider_session_id: string
  cwd?: string
  cols?: number
  rows?: number
}

export interface ModifiedFile {
  path: string
  count: number
  last_ts: string
  last_tool: string
}
export interface BashWrite {
  path: string
  snippet: string
  ts: string
}
export interface ResolvedContextItem {
  path: string
  abs_path?: string
  category?: string
  source?: string
  reason?: string
  exists?: boolean
  dashboard_target?: { type: 'note' | 'plan'; id: string } | null
  vscode_uri?: string
}
export interface ResolvedContextBundle {
  plan_id?: string | null
  project?: string | null
  paths?: string[]
  explicit_kinds?: string[]
  inferred_kinds?: Record<string, string | null>
  topic?: string
  contexts: ResolvedContextItem[]
  total: number
  missing: ResolvedContextItem[]
  missing_total: number
  resolved_at?: number
  summary?: string
  error?: string
}
export interface SessionContext {
  session_id: string
  kind?: 'cc' | 'native'
  context: {
    active_plan?: string | null
    project?: string | null
    plan_meta?: Record<string, any>
    project_meta?: Record<string, any>  // project.md frontmatter (立于 plan 之上)
    cwd?: string | null
    provider?: string | null
    provider_session_id?: string | null
    trace_id?: string | null
    claude_session_id?: string | null
    started_at?: number | null
    ended_at?: number | null
    agent_state?: string
    user_context?: { work_type?: string; standards?: string[]; notes?: string }
    resolved_context?: ResolvedContextBundle
  }
  modified_files: ModifiedFile[]
  bash_writes: BashWrite[]
  added_workers: string[]
  added_materials: string[]
  event_count: number
}

export const ccApi = {
  async health(): Promise<{ status: string; claude_cli_found: boolean; session_count: number; default_cwd: string }> {
    const r = await fetch(`${BASE}/health`)
    if (!r.ok) throw new Error(`cc/health: ${r.status}`)
    return r.json()
  },

  async list(options: { includeRecoverable?: boolean } = {}): Promise<CcSessionMeta[]> {
    // Returns BOTH alive + recoverable, fused into one list with status fields.
    const includeRecoverable = options.includeRecoverable !== false
    const r = await fetch(`${BASE}/sessions?include_recoverable=${includeRecoverable}`)
    if (!r.ok) throw new Error(`cc/sessions list: ${r.status}`)
    const d = await r.json() as SessionsList
    const alive = (d.items || []).map(m => ({ ...m, status: 'alive' as const }))
    const rec = (d.recoverable || []).map(m => ({ ...m, status: 'recoverable' as const }))
    return [...alive, ...rec]
  },

  /** 页签活跃徽章专用的高频轻量快照(纯内存, 无磁盘 IO)。 */
  async tabStates(): Promise<CcTabStatesPayload> {
    const r = await fetch(`${BASE}/tab-states`)
    if (!r.ok) throw new Error(`cc/tab-states: ${r.status}`)
    const d = await r.json() as Partial<CcTabStatesPayload>
    return { pty: d.pty || [], chat: d.chat || [] }
  },

  async create(body?: CreateSessionBody): Promise<CcSessionMeta> {
    const r = await fetch(`${BASE}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/sessions create: ${r.status}`)
    }
    return r.json()
  },

  async resume(recoverableId: string): Promise<CcSessionMeta> {
    const r = await fetch(`${BASE}/sessions/${recoverableId}/resume`, { method: 'POST' })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/sessions resume: ${r.status}`)
    }
    return r.json()
  },

  async resumeProvider(body: ResumeProviderSessionBody): Promise<CcSessionMeta> {
    const r = await fetch(`${BASE}/sessions/resume-provider`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/sessions resume-provider: ${r.status}`)
    }
    return r.json()
  },

  async kill(id: string): Promise<void> {
    const r = await fetch(`${BASE}/sessions/${id}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`cc/sessions delete: ${r.status}`)
  },

  async context(id: string): Promise<SessionContext> {
    const r = await fetch(`${BASE}/sessions/${id}/context`)
    if (!r.ok) throw new Error(`cc/sessions context: ${r.status}`)
    return r.json()
  },

  // (REMOVED 2026-05-02 round 4) patchContext — work_type / standards 改走 plan.md frontmatter

  /**
   * Switch (or unbind) the active_plan for a cc_session.
   * plan_id=null 解绑. effective='next_user_turn' (alive) | 'immediate' (recoverable).
   */
  async patchActivePlan(sid: string, planId: string | null): Promise<{
    session_id: string
    active_plan: string | null
    alive: boolean
    effective: 'next_user_turn' | 'immediate'
    note: string
  }> {
    const r = await fetch(`${BASE}/sessions/${sid}/active_plan`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: planId }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `patchActivePlan: ${r.status}`)
    }
    return r.json()
  },

  /** Build the WS URL for a session. Caller wraps in `new WebSocket(...)`. */
  wsUrl(id: string): string {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${window.location.host}${BASE}/sessions/${id}/ws?client_protocol=${PTY_CLIENT_PROTOCOL}`
  },

  // ── settings install / status (mirrors `omni cc` CLI exactly) ──
  async installStatus(
    scope: 'project' | 'user' = 'project',
    provider: AgentIntegrationProvider = 'claude_code',
  ): Promise<{
    provider: AgentIntegrationProvider
    settings_path: string
    installed: boolean
    mcp_command?: string | null
    hook_events?: string[]
    requires_trust?: boolean
    trust_command?: string
  }> {
    const r = await fetch(`${BASE}/install/status?scope=${scope}&provider=${provider}`)
    if (!r.ok) throw new Error(`status: ${r.status}`)
    return r.json()
  },
  async install(
    scope: 'project' | 'user' = 'project',
    provider: AgentIntegrationProvider = 'claude_code',
  ): Promise<any> {
    const r = await fetch(`${BASE}/install?scope=${scope}&provider=${provider}`, { method: 'POST' })
    if (!r.ok) throw new Error(`install: ${r.status}`)
    return r.json()
  },
  async uninstall(
    scope: 'project' | 'user' = 'project',
    provider: AgentIntegrationProvider = 'claude_code',
  ): Promise<any> {
    const r = await fetch(`${BASE}/install?scope=${scope}&provider=${provider}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`uninstall: ${r.status}`)
    return r.json()
  },
}
