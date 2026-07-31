async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail || '' } catch { /* ignore */ }
    throw new Error(`${r.status} ${r.statusText} ${detail}`.trim())
  }
  return r.json() as Promise<T>
}

export interface BriefingAction {
  kind: string
  label: string
  priority: 'critical' | 'attention' | 'info' | 'calm' | string
  target: string | null
}

export interface BriefingSummary {
  plans_total: number
  plans_active: number
  plans_done: number
  review_total: number
  review_pending: number
  mandatory_unaccepted: number
  pushed_unread: number
  subagents_total: number
  subagents_running: number
  subagents_blocked: number
}

export interface BriefingMaterial {
  id: string
  title: string
  kind: string
  tier: string
  status: string
  source_plan_id: string | null
  source_subagent_id: string | null
  pushed_to_user: boolean
  updated_at: string
}

export interface BossSightBriefing {
  generated_at: string
  severity: 'critical' | 'attention' | 'calm' | string
  headline: string
  all_green: boolean
  summary: BriefingSummary
  review: {
    available: boolean
    total: number
    by_status: Record<string, number>
    by_tier: Record<string, number>
    mandatory_unaccepted: number
    pushed_unread: number
    recent: BriefingMaterial[]
  }
  plans: {
    total: number
    active: Array<Record<string, unknown>>
  }
  subagents: {
    total: number
    running: Array<Record<string, unknown>>
    blocked: Array<Record<string, unknown>>
  }
  controls?: BossSightControlResponse
  observability?: {
    settings: BossSightObservabilitySettings
    recent: BossSightObservationEvent[]
  }
  next_actions: BriefingAction[]
  secretary: {
    title: string
    body: string
  }
  workflow_summary?: BossSightWorkflowCtxSummary
}

export type BossSightActor = 'human' | 'controller'
export type BossSightControlKey =
  | 'controller.auto_wake'
  | 'reviewstage.push_to_user'
  | 'spawn.hard_block'
  | 'observability.enabled'
export type BossSightObservabilityDimension = 'click' | 'selection' | 'toggle_change' | 'view_dwell'

export interface BossSightControlHistoryEntry {
  id: string
  actor: BossSightActor | string
  updated_at: string
  reason: string
  previous: boolean
  next: boolean
}

export interface BossSightControlItem {
  key: BossSightControlKey | string
  label: string
  description: string
  value: boolean
  updated_by: BossSightActor | 'system' | string
  updated_at: string
  reason: string
  history: BossSightControlHistoryEntry[]
}

export interface BossSightControlResponse {
  items: BossSightControlItem[]
  by_key: Record<string, BossSightControlItem>
  count: number
}

export interface BossSightObservabilitySettings {
  dimensions: Record<BossSightObservabilityDimension | string, boolean>
  updated_by: BossSightActor | 'system' | string
  updated_at: string
  reason: string
  history: Array<Record<string, unknown>>
}

export interface BossSightObservationEvent {
  id: string
  dimension: BossSightObservabilityDimension | string
  surface: string
  target: string | null
  value: unknown
  meta: Record<string, unknown>
  actor: BossSightActor | string
  recorded_at: string
}

export interface MaterialRegistryRelation {
  kind: string
  id: string
  label: string
  uri?: string | null
}

export interface MaterialRegistryItem {
  uri: string
  id: string
  title: string
  kind: string
  role: string
  layer: 'context' | 'executor' | string
  status?: string | null
  display: string
  source: string
  path?: string | null
  snippet: string
  open_ref: {
    type?: string
    id?: string
    facet?: string
    url?: string
    command?: string
  }
  entity_uri?: string | null
  relations: MaterialRegistryRelation[]
  tags: string[]
  updated_at?: string | null
}

export interface MaterialRegistryResponse {
  generated_at: string
  items: MaterialRegistryItem[]
  total: number
  returned: number
  counts: {
    by_kind: Record<string, number>
    by_role: Record<string, number>
    by_layer: Record<string, number>
    by_status: Record<string, number>
  }
  filters: Record<string, unknown>
  summary: {
    total: number
    counts: Record<string, Record<string, number>>
    highlighted_items: MaterialRegistryItem[]
    execution_boundaries: MaterialRegistryItem[]
    executors: MaterialRegistryItem[]
  }
}

export interface BossSightWorkflowActionEvent {
  id: string
  kind: string
  actor: string
  target: Record<string, unknown>
  note: string
  status: 'succeeded' | 'failed' | string
  result: Record<string, unknown>
  error: string
  created_at: string
}

export interface BossSightWorkflowCtxSummary {
  status: string
  headline: string
  summary: {
    status?: string
    headline?: string
    unresolved_count: number
    critical_count: number
    comment_unresolved_count: number
    comment_todo_done_count: number
    blocked_agent_count: number
    action_failed_count: number
    action_succeeded_count: number
  }
  unresolved: Array<Record<string, any>>
  comment_feedback: {
    by_status: Record<string, number>
    unresolved_count: number
    todo_done_count: number
    unresolved: Array<Record<string, any>>
    recent_resolved: Array<Record<string, any>>
  }
  action_history: {
    recent: BossSightWorkflowActionEvent[]
    failed_count: number
    succeeded_count: number
    last_failed: BossSightWorkflowActionEvent | null
  }
  blocked_agents: Array<Record<string, any>>
}

export interface BossSightWorkflowSummaryResponse {
  generated_at: string
  status: string
  headline: string
  summary: BossSightWorkflowCtxSummary['summary']
  unresolved: {
    count: number
    critical_count: number
    attention_count: number
    by_reason: Record<string, number>
    by_kind: Record<string, number>
    items: Array<Record<string, any>>
  }
  comment_feedback: BossSightWorkflowCtxSummary['comment_feedback'] & {
    total: number
  }
  blocked_agents: Array<Record<string, any>>
  action_history: BossSightWorkflowCtxSummary['action_history'] & {
    count: number
    by_status: Record<string, number>
    by_kind: Record<string, number>
  }
  ctx_summary: BossSightWorkflowCtxSummary
}

// SESSION-SELF-BINDING(4.5): 活跃对话按 plan/project/task 聚合 — 给计划/项目/任务/对话
// 四页显示"有多少活跃对话在推进"。见 boss_sight/services/binding_aggregate.py。
export interface BossSightBindingSession {
  key: string
  name?: string
  provider?: string
  session_id?: string
  run_status?: string
  running: boolean
  title?: string
  active_plan?: string | null
  project?: string | null
  task_id?: string | null
  /** 有会话自己声明的绑定(vs digest 推测) — 后端 residents.py: rec["authoritative"]=True。
   *  当前 binding_aggregate 的精简会话卡未必透出此字段(阶段性), 前端按可选处理。 */
  authoritative?: boolean
}

export interface BossSightBindingBucket {
  key: string
  /** 在跑(running=True)的会话数 */
  active: number
  /** 绑到该 key 的全部会话数(含已停) */
  total: number
  sessions: BossSightBindingSession[]
}

export interface BossSightActiveBindings {
  total_active: number
  total: number
  source: string
  generated_at: number
  by_plan: Record<string, BossSightBindingBucket>
  by_project: Record<string, BossSightBindingBucket>
  by_task: Record<string, BossSightBindingBucket>
}

export interface BossSightUserPrefs {
  version: number
  permanent_allow: Array<{
    id: string
    scope: string
    tool: string
    pattern: string
    reason: string
    actor: BossSightActor | string
    created_at: string
  }>
}

export const bossSightApi = {
  briefing: async (): Promise<BossSightBriefing> => {
    const r = await fetch('/api/boss-sight/briefing')
    return jsonOrThrow(r)
  },

  workflowSummary: async (): Promise<BossSightWorkflowSummaryResponse> => {
    const r = await fetch('/api/boss-sight/workflow-summary')
    return jsonOrThrow(r)
  },

  getControl: async (): Promise<BossSightControlResponse> => {
    const r = await fetch('/api/boss-sight/control')
    return jsonOrThrow(r)
  },

  setControl: async (
    key: string,
    value: boolean,
    actor: BossSightActor = 'human',
    reason = '',
  ): Promise<BossSightControlItem> => {
    const r = await fetch(`/api/boss-sight/control/${encodeURIComponent(key)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, actor, reason }),
    })
    return jsonOrThrow(r)
  },

  getUserPrefs: async (): Promise<BossSightUserPrefs> => {
    const r = await fetch('/api/boss-sight/user-prefs')
    return jsonOrThrow(r)
  },

  addPermanentAllow: async (input: {
    scope?: string
    tool: string
    pattern?: string
    reason?: string
    actor?: BossSightActor
  }): Promise<BossSightUserPrefs['permanent_allow'][number]> => {
    const r = await fetch('/api/boss-sight/user-prefs/permanent_allow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: input.scope || 'user',
        tool: input.tool,
        pattern: input.pattern || '',
        reason: input.reason || '',
        actor: input.actor || 'human',
      }),
    })
    return jsonOrThrow(r)
  },

  getObservabilitySettings: async (): Promise<BossSightObservabilitySettings> => {
    const r = await fetch('/api/boss-sight/observability/settings')
    return jsonOrThrow(r)
  },

  setObservabilitySettings: async (
    dimensions: Record<string, boolean>,
    actor: BossSightActor = 'human',
    reason = '',
  ): Promise<BossSightObservabilitySettings> => {
    const r = await fetch('/api/boss-sight/observability/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dimensions, actor, reason }),
    })
    return jsonOrThrow(r)
  },

  recordObservation: async (input: {
    dimension: BossSightObservabilityDimension
    surface?: string
    target?: string | null
    value?: unknown
    meta?: Record<string, unknown>
    actor?: BossSightActor
  }): Promise<{ recorded: boolean; skipped: boolean; reason?: string; event?: BossSightObservationEvent }> => {
    const r = await fetch('/api/boss-sight/observability/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dimension: input.dimension,
        surface: input.surface || '',
        target: input.target ?? null,
        value: input.value ?? null,
        meta: input.meta || {},
        actor: input.actor || 'human',
      }),
    })
    return jsonOrThrow(r)
  },

  recentObservations: async (limit = 20): Promise<{ items: BossSightObservationEvent[]; count: number }> => {
    const q = new URLSearchParams({ limit: String(limit) })
    const r = await fetch(`/api/boss-sight/observability/recent?${q.toString()}`)
    return jsonOrThrow(r)
  },

  getMaterialRegistry: async (filters: {
    q?: string
    kind?: string
    role?: string
    layer?: string
    status?: string
    limit?: number
  } = {}): Promise<MaterialRegistryResponse> => {
    const q = new URLSearchParams()
    if (filters.q) q.set('q', filters.q)
    if (filters.kind) q.set('kind', filters.kind)
    if (filters.role) q.set('role', filters.role)
    if (filters.layer) q.set('layer', filters.layer)
    if (filters.status) q.set('status', filters.status)
    q.set('limit', String(filters.limit || 250))
    const r = await fetch(`/api/boss-sight/material-registry?${q.toString()}`)
    return jsonOrThrow(r)
  },

  /** 活跃对话按 plan/project/task 聚合(计数 + 清单), 给计划/项目/任务/对话四页复用。 */
  activeBindings: async (): Promise<BossSightActiveBindings> => {
    const r = await fetch('/api/boss-sight/active-bindings')
    return jsonOrThrow(r)
  },
}
