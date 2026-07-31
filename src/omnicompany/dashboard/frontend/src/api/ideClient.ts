/**
 * IDE Client — SSE 事件流 + REST 交互
 *
 * 映射 OpenHands 的 oh_event/oh_user_action 模式，
 * 使用浏览器原生 EventSource API 接收 SSE 事件。
 */

const BASE = '/api/v2'

// ── Types ──

export interface IDEEvent {
  id: string
  trace_id: string
  parent_id: string | null
  event_type: string
  source: string
  payload: Record<string, any>
  timestamp: string
  tags: string[]
  metadata?: {
    prompt_tokens?: number
    completion_tokens?: number
    model?: string
    cost_usd?: number
    latency_ms?: number
    tool_name?: string
    duration_ms?: number
  }
}

export interface SessionInfo {
  trace_id: string
  status: string
  task_desc: string | null
  created_at: string
  last_active: string
}

export interface FileChange {
  path: string
  action: 'read' | 'write' | 'edit' | 'create'
  old_text?: string | null
  new_text?: string | null
}

export interface SendResponse {
  trace_id: string
  event_id: string
}

// ── SSE Connection ──

export function connectSSE(
  traceId: string | null,
  onEvent: (event: IDEEvent) => void,
  onError?: (err: Event) => void,
): EventSource {
  const params = new URLSearchParams()
  if (traceId) params.set('trace_id', traceId)

  const url = `${BASE}/ide/events?${params}`
  const source = new EventSource(url)

  source.onmessage = (msg) => {
    try {
      const event: IDEEvent = JSON.parse(msg.data)
      onEvent(event)
    } catch (e) {
      console.error('Failed to parse SSE event:', e)
    }
  }

  source.onerror = (err) => {
    console.warn('SSE connection error, will auto-reconnect', err)
    onError?.(err)
  }

  return source
}

// ── REST API ──

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || r.statusText)
  }
  return r.json()
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export interface SessionContext {
  session_id: string
  kind: 'cc' | 'native'
  context: {
    active_plan?: string | null
    plan_meta?: Record<string, any>
    project_meta?: Record<string, any>  // project.md frontmatter (立于 plan 之上)
    cwd?: string | null
    agent_state?: string
    started_at?: number | string | null
    claude_session_id?: string | null  // cc only
    user_context?: { work_type?: string; standards?: string[]; notes?: string }  // legacy
  }
  modified_files: { path: string; count: number; last_ts: string; last_tool: string }[]
  bash_writes: { path: string; snippet: string; ts: string }[]
  added_workers: string[]
  added_materials: string[]
  tool_calls?: { tool: string; ts: string }[]
  stats?: {
    model?: string | null
    turn_count: number
    input_tokens: number
    output_tokens: number
    total_tokens: number
  }
  event_count: number
}

export const ideApi = {
  send: (traceId: string | null, instruction: string, opts?: { active_plan?: string | null; cwd?: string | null }) =>
    post<SendResponse>('/ide/send', {
      trace_id: traceId,
      instruction,
      active_plan: opts?.active_plan ?? null,
      cwd: opts?.cwd ?? null,
    }),

  sessions: () => get<SessionInfo[]>('/ide/sessions'),

  traceHistory: (traceId: string) =>
    get<IDEEvent[]>(`/ide/trace/${traceId}/history`),

  traceFiles: (traceId: string) =>
    get<FileChange[]>(`/ide/trace/${traceId}/files`),

  context: (traceId: string) =>
    get<SessionContext>(`/ide/trace/${traceId}/context`),

  cancel: (traceId: string) =>
    post<{ ok: boolean }>('/ide/cancel', { trace_id: traceId }),
}
