const BASE = '/api/v2'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export interface Round {
  round_num: number
  started_at: number | null
  completed_at: number | null
  task_desc: string | null
  agent_success: number | null
  evo_triggered: number
  open_loop_count: number
  pain_peak: number | null
  final_output_text: string | null
  // fallback fields from routing_events aggregation
  route_total?: number
  route_hit?: number
  success_count?: number
}

export interface RoutingEvent {
  id: number
  trace_id: string
  task_desc: string | null
  input_types: string | null
  target_types: string | null
  route_found: number
  path_nodes: string | null
  confidence: number | null
  agent_success: number | null
  round_num: number | null
  created_at: number
}

export interface SignalSpan {
  id: number
  trace_id: string
  round_num: number | null
  span_index: number
  node_id: string
  node_desc: string | null
  impl_kind: string | null
  input_text: string | null
  input_format: string | null
  output_text: string | null
  output_format: string | null
  success: number
  error_text: string | null
  tool_name: string | null
  latency_ms: number | null
}

export interface TraceDetail {
  trace_id: string
  events: TraceEvent[]
  routing_events: RoutingEvent[]
  signal_spans: SignalSpan[]
  intent_steps?: IntentStep[]
}

export interface TraceEvent {
  id: string
  trace_id: string
  parent_id: string | null
  event_type: string
  source: string
  payload: Record<string, any>
  timestamp: string
  _domain?: string
}

export interface IntentStep {
  id: number
  trace_id: string
  step_num: number
  tool_name: string | null
  input_types: string | null
  output_types: string | null
  desc: string | null
  rationale: string | null
  tool_args_summary: string | null
  tool_result: string | null
  tool_exit_ok: number | null
  timestamp: number | null
}

export interface TraceListItem {
  trace_id: string
  task_desc: string | null
  source: string
  domain: string
  started_at: string
  ended_at: string
  event_count: number
  tool_calls: number
  llm_calls: number
  status: 'running' | 'finished' | 'error'
}

export interface SemanticNode {
  node_id: string
  description: string | null
  impl_kind: string
  processing_prompt: string | null
  input_types: string | null
  output_types: string | null
  active: number
  round_created: number | null
  recent_spans?: SignalSpan[]
}

export interface PainSignal {
  id: number
  node_id: string
  signal_text: string
  severity: string
  trace_id: string | null
  round_num: number | null
  created_at: number
}

export interface EvoEntry {
  id: number
  evolution_id: string
  mutation_type: string | null
  target_node_id: string | null
  pre_error_avg: number | null
  post_error_avg: number | null
  delta_error: number | null
  effective: number | null
}

export interface SemanticType {
  type_id: string
  level: number | null
  parent_type: string | null
  description: string | null
  exemplars: string | null
  ta_domain: string | null
  ta_action: string | null
  ta_entity: string | null
  ta_format: string | null
  created_at: string | null
  active: number
  required_fields: string | null
}

// ── Assistant Context Types ───────────────────────────────────────────────────

export interface Workspace {
  key: string
  title: string
  kind: 'folder' | 'url' | 'api' | string
  path: string | null
  url: string | null
  description: string | null
  key_files: string[]
  tags: string[]
  active: number
}

export interface Goal {
  goal_id: string
  title: string
  status: 'planned' | 'active' | 'done' | 'cancelled'
  implementation_proof: string | null
  related_plan: string | null
  related_traces: string[]
  created_at: number
  completed_at: number | null
}

export interface Plan {
  plan_id: string
  title: string
  folder_path: string
  status: 'active' | 'paused' | 'done' | string
  current_phase: string | null
  goal_ids: string[]
  created_at: number
  updated_at: number
}

export interface HistoryEntry {
  session_id: string
  started_at: number | null
  compacted_at: number
  summary: string
  goal_ids: string[]
  plan_ids: string[]
  key_paths: string[]
  open_todos: string[]
}

export interface ExtraItem {
  item_id: string
  kind: 'knowledge' | 'skill' | 'pipeline' | 'rule'
  title: string
  format_in: string | null
  format_out: string | null
  content: string | null
  file_path: string | null
  tags: string[]
  scope: { goal_ids?: string[]; plan_ids?: string[] } | null
  active: number
  created_at: number
}

export interface CronJob {
  job_id: string
  schedule: string
  task_prompt: string
  last_run: number | null
  active: number
}

export interface AssistantContext {
  workspaces: Workspace[]
  active_goals: Goal[]
  planned_goals: Goal[]
  active_plans: Plan[]
  extra_by_kind: Record<string, ExtraItem[]>
  recent_history: HistoryEntry[]
}

// ── Assistant API helpers ─────────────────────────────────────────────────────

async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

async function del(path: string): Promise<void> {
  const r = await fetch(BASE + path, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export const api = {
  rounds: (limit = 20) => get<Round[]>(`/rounds?limit=${limit}`),
  round: (n: number) => get<{ round_num: number; meta: Round | null; routing_events: RoutingEvent[]; pain_signals: PainSignal[] }>(`/round/${n}`),
  trace: (id: string) => get<TraceDetail>(`/trace-detail/${id}`),
  traceList: (params?: { limit?: number; offset?: number; q?: string; source?: string }) => {
    const qs = new URLSearchParams()
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.offset) qs.set('offset', String(params.offset))
    if (params?.q) qs.set('q', params.q)
    if (params?.source) qs.set('source', params.source)
    return get<{ items: TraceListItem[]; total: number }>(`/trace-list?${qs}`)
  },
  nodes: (params?: { active?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.active !== undefined) q.set('active', String(params.active))
    if (params?.limit) q.set('limit', String(params.limit))
    return get<SemanticNode[]>(`/nodes?${q}`)
  },
  node: (id: string) => get<SemanticNode>(`/node/${id}`),
  openLoops: (lastRounds = 20) => get<RoutingEvent[]>(`/open-loops?last_rounds=${lastRounds}`),
  pain: (params?: { node_id?: string; round_num?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.node_id) q.set('node_id', params.node_id)
    if (params?.round_num !== undefined) q.set('round_num', String(params.round_num))
    if (params?.limit) q.set('limit', String(params.limit))
    return get<PainSignal[]>(`/pain?${q}`)
  },
  evo: (limit = 20, node_id?: string) => {
    const q = new URLSearchParams()
    q.set('limit', String(limit))
    if (node_id) q.set('node_id', node_id)
    return get<EvoEntry[]>(`/evo?${q}`)
  },
  types: (params?: { q?: string; active_only?: boolean; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.active_only !== undefined) qs.set('active_only', String(params.active_only))
    if (params?.limit) qs.set('limit', String(params.limit))
    return get<SemanticType[]>(`/types?${qs}`)
  },
  health: () => get<{ status: string }>('/health'),
  updateNode: (id: string, body: Partial<Pick<SemanticNode, 'description' | 'processing_prompt' | 'input_types' | 'output_types' | 'active'>>) => {
    return fetch(BASE + `/node/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => {
      if (!r.ok) return r.json().then(e => Promise.reject(new Error(e.detail || r.statusText)))
      return r.json() as Promise<{ ok: boolean; node_id: string; updated: number }>
    })
  },

  // ── Assistant Context ──────────────────────────────────────────────────────
  assistant: {
    context: () => get<AssistantContext>('/assistant/context'),

    // Workspaces
    workspaces: (activeOnly = true) =>
      get<Workspace[]>(`/assistant/workspaces?active_only=${activeOnly}`),
    putWorkspace: (key: string, body: Omit<Workspace, 'key'>) =>
      put<Workspace>(`/assistant/workspaces/${key}`, { ...body, key }),
    deleteWorkspace: (key: string) => del(`/assistant/workspaces/${key}`),

    // Goals
    goals: (status?: string) =>
      get<Goal[]>(`/assistant/goals${status ? `?status=${status}` : ''}`),
    createGoal: (body: { title: string; status?: string; implementation_proof?: string; related_plan?: string }) =>
      post<Goal>('/assistant/goals', body),
    updateGoal: (id: string, body: Partial<Omit<Goal, 'goal_id' | 'created_at'>>) =>
      patch<Goal>(`/assistant/goals/${id}`, body),
    deleteGoal: (id: string) => del(`/assistant/goals/${id}`),

    // Plans
    plans: (status?: string) =>
      get<Plan[]>(`/assistant/plans${status ? `?status=${status}` : ''}`),
    createPlan: (body: { title: string; folder_path: string; status?: string; current_phase?: string; goal_ids?: string[] }) =>
      post<Plan>('/assistant/plans', body),
    updatePlan: (id: string, body: Partial<Omit<Plan, 'plan_id' | 'created_at' | 'updated_at'>>) =>
      patch<Plan>(`/assistant/plans/${id}`, body),

    // History
    history: (limit = 50, offset = 0) =>
      get<HistoryEntry[]>(`/assistant/history?limit=${limit}&offset=${offset}`),
    historyItem: (sessionId: string) =>
      get<HistoryEntry>(`/assistant/history/${sessionId}`),
    writeHistory: (body: { summary: string; goal_ids?: string[]; plan_ids?: string[]; key_paths?: string[]; open_todos?: string[]; session_id?: string; started_at?: number }) =>
      post<HistoryEntry>('/assistant/history', body),

    // Extra items
    extra: (kind?: string) =>
      get<ExtraItem[]>(`/assistant/extra${kind ? `?kind=${kind}` : ''}`),
    createExtra: (body: { kind: string; title: string; format_in?: string; format_out?: string; content?: string; file_path?: string; tags?: string[]; scope?: { goal_ids?: string[]; plan_ids?: string[] } | null }) =>
      post<ExtraItem>('/assistant/extra', body),
    updateExtra: (id: string, body: Partial<Omit<ExtraItem, 'item_id' | 'created_at'>>) =>
      patch<ExtraItem>(`/assistant/extra/${id}`, body),
    deleteExtra: (id: string) => del(`/assistant/extra/${id}`),

    // PlanUpdate config
    getPlanUpdate: () => get<{ enabled: boolean }>('/assistant/config/plan-update'),
    setPlanUpdate: (enabled: boolean) =>
      post<{ enabled: boolean }>('/assistant/config/plan-update', { enabled }),

    // Work-Until config
    getWorkUntil: () => get<{ active: boolean; plan_title: string | null }>('/assistant/config/work-until'),
    setWorkUntil: (plan_title: string | null) =>
      post<{ ok: boolean; active: boolean; plan_title: string | null }>('/assistant/config/work-until', { plan_title }),

    // Cron jobs
    cron: (activeOnly = false) => get<CronJob[]>(`/assistant/cron?active_only=${activeOnly}`),
    createCron: (body: { schedule: string; task_prompt: string }) =>
      post<CronJob>('/assistant/cron', body),
    updateCron: (id: string, body: Partial<Pick<CronJob, 'schedule' | 'task_prompt' | 'active'>>) =>
      patch<CronJob>(`/assistant/cron/${id}`, body),
    deleteCron: (id: string) => del(`/assistant/cron/${id}`),
  },
}
