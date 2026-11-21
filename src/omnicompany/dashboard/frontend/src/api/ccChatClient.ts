/**
 * Claude Code chat 后端客户端 — 配套 cc_wrapper/cc_chat_bridge.py.
 *
 * 后端用 claude-agent-sdk (Python) 包装本地 claude binary, 走 claude login
 * 订阅. 不要 ANTHROPIC_API_KEY.
 */

const BASE = '/api/cc/chat'

export interface CcChatSessionMeta {
  id: string
  kind: 'chat'
  /** LLM provider — backend chat.py 阶段 9 新加, claude_code/omni_agent/codex */
  provider?: string
  /** 用户可编辑的 session 显示名字; 空 = UI 用 id tail 兜底 */
  name?: string
  archived?: boolean
  favorite?: boolean
  cwd: string
  cmd: string[]
  cols: 0
  rows: 0
  started_at: number
  alive: boolean
  /** A real provider runtime is currently held by ccdaemon; unlike alive, this excludes lazy historical sessions. */
  runtime_alive?: boolean
  /** True while the structured chat has an in-flight model turn. */
  running?: boolean
  subscribers?: number
  buffered_chunks?: number
  status: 'alive' | 'ended'
  claude_session_id: string | null
  active_plan: string | null
  model: string
  permission_mode?: string
  /** N2b 推理强度档 (low/medium/high/xhigh/max), null=用模型默认 */
  effort?: string | null
  ended_at?: number | null
  exit_reason?: string | null
  first_message?: string
  last_message?: string
  message_count?: number
  token_usage?: {
    total: number
    input: number
    output: number
    cache_creation_input: number
    cache_read_input: number
    source: 'provider_reported'
  } | null
  pinned?: boolean
  /** #2 接管式采纳: adopted=resume 别处会话采纳来的(当 subagent); taken_over=用户已接管(总控不自动 hook)。 */
  adopted?: boolean
  taken_over?: boolean
}

// 'controller' = BOSS SIGHT 总控 (src/omnicompany/dashboard/boss_sight/), 接 claude-agent-sdk
// 跑总控自家 prompts/boundaries/instructions, 不 spawn 子进程
export type CcChatProvider = 'claude_code' | 'omni_agent' | 'codex' | 'controller' | 'codebuddy' | 'kimi' | 'opencode'

export interface CreateCcChatSessionBody {
  cwd?: string
  /** 模型短名 sonnet / opus / haiku / opusplan / sonnet[1m] (claude-agent-sdk 命名) */
  model?: string
  /** LLM provider: claude_code / omni_agent / codex / controller (BOSS SIGHT 总控) */
  provider?: CcChatProvider
  /** #2 载入已有会话: 源 provider session id。claude_code→resume+fork; codex→resume_thread 续接。 */
  fork_from_provider_session_id?: string
  /** #2 接管式采纳: resume 同一 session_id 接管它当 subagent(自动 caller_identity=subagent)。 */
  adopt_session_id?: string
  /** 采纳/spawn 时关联的 plan id。 */
  active_plan?: string
  /** N2b 推理强度档 (low/medium/high/xhigh/max), 省略=用模型默认。 */
  effort?: string | null
}

/** #2 可载入续接的本机历史会话(Claude Code / Codex / Kimi / OpenCode)。 */
export interface ImportableSession {
  provider: 'claude_code' | 'codex' | 'codebuddy' | 'kimi' | 'opencode'
  session_id: string
  cwd: string
  mtime: number
  preview: string
  file: string
  // /active 附带的完成感知(多 agent 视图用): status=运行态, last_did=最后一段助手回复。
  // /importable 不带这俩(只列历史), 故可选。
  status?: 'working' | 'done' | 'waiting' | 'idle'
  last_did?: string
  last_user?: string  // 最后一句真实用户 prompt(无摘要时兜底显示, 优于首条 preview)
  recent_messages?: Array<{ role: 'user' | 'assistant'; text: string }>
  // 性价比模型维护的对话摘要(中文): 项目/计划/在做什么/最近一步。可能还没算出来(回退 preview)。
  digest?: { project?: string; plan?: string; title?: string; last_step?: string }
}

export interface CcChatSessionsListResult {
  items: CcChatSessionMeta[]
  alive_count: number
  total: number
  limit: number
  offset: number
  has_more: boolean
  query?: string
  full_text?: boolean
}

export interface ListCcChatSessionsOptions {
  q?: string
  fullText?: boolean
  limit?: number
  offset?: number
  pinnedId?: string | null
  includeArchived?: boolean
}

export const ccChatApi = {
  async health(): Promise<{
    status: string
    default_model: string
    default_cwd: string
    session_count: number
    claude_agent_sdk_version: string
    note: string
  }> {
    const r = await fetch(`${BASE}/health`)
    if (!r.ok) throw new Error(`cc/chat/health: ${r.status}`)
    return r.json()
  },

  async listPage(options: ListCcChatSessionsOptions = {}): Promise<CcChatSessionsListResult> {
    const params = new URLSearchParams()
    if (options.q) params.set('q', options.q)
    if (options.fullText) params.set('full_text', 'true')
    if (options.limit) params.set('limit', String(options.limit))
    if (options.offset) params.set('offset', String(options.offset))
    if (options.pinnedId) params.set('pinned_id', options.pinnedId)
    if (options.includeArchived) params.set('include_archived', 'true')
    const qs = params.toString()
    const r = await fetch(`${BASE}/sessions${qs ? `?${qs}` : ''}`)
    if (!r.ok) throw new Error(`cc/chat/sessions list: ${r.status}`)
    const d = (await r.json()) as CcChatSessionsListResult
    return {
      ...d,
      items: d.items || [],
      total: d.total ?? (d.items || []).length,
      limit: d.limit ?? (options.limit || 60),
      offset: d.offset ?? (options.offset || 0),
      has_more: Boolean(d.has_more),
    }
  },

  async list(options: ListCcChatSessionsOptions = {}): Promise<CcChatSessionMeta[]> {
    const d = await this.listPage(options)
    return d.items || []
  },

  async create(body?: CreateCcChatSessionBody): Promise<CcChatSessionMeta> {
    const r = await fetch(`${BASE}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/chat/sessions create: ${r.status}`)
    }
    return r.json()
  },

  /** #2 接管/交还一个采纳来的会话。on=true 接管(总控不自动 hook); false=交还给总控当 subagent。 */
  async takeover(sid: string, on: boolean): Promise<{ session_id: string; taken_over: boolean; adopted: boolean }> {
    const r = await fetch(`${BASE}/sessions/${encodeURIComponent(sid)}/takeover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on }),
    })
    if (!r.ok) throw new Error(`takeover ${sid}: ${r.status}`)
    return r.json()
  },

  async kill(id: string): Promise<void> {
    const r = await fetch(`${BASE}/sessions/${id}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`cc/chat/sessions delete: ${r.status}`)
  },

  /** #2 列出可载入续接的本机历史会话(Claude Code + Codex)。 */
  async importable(limit = 40): Promise<ImportableSession[]> {
    const r = await fetch(`${BASE}/importable?limit=${limit}`)
    if (!r.ok) throw new Error(`cc/chat/importable: ${r.status}`)
    const d = await r.json()
    return (d.items || []) as ImportableSession[]
  },

  /** 真正在跑的会话(transcript 近 windowSec 秒有写入, 含别处目录的 claude/codex)。 */
  async activeSessions(windowSec = 600, limit = 30): Promise<ImportableSession[]> {
    const r = await fetch(`${BASE}/active?window_sec=${windowSec}&limit=${limit}`)
    if (!r.ok) throw new Error(`cc/chat/active: ${r.status}`)
    const d = await r.json()
    return (d.items || []) as ImportableSession[]
  },

  /** #3/A1 把选中的已有对话(claude/codex)真实内容载入为【总控对话的前文】(注入总控上下文)。 */
  async loadContext(item: ImportableSession): Promise<{
    ok: boolean; reason?: string; controller_id?: string | null; message_count?: number; truncated?: boolean; chars?: number
  }> {
    const r = await fetch(`${BASE}/load_context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider: item.provider,
        session_id: item.session_id,
        file: item.file,
        cwd: item.cwd,
        title: item.preview ? item.preview.slice(0, 80) : undefined,
      }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/chat/load_context: ${r.status}`)
    }
    return r.json()
  },

  async rename(sid: string, name: string): Promise<{ session_id: string; name: string }> {
    const r = await fetch(`${BASE}/sessions/${sid}/name`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/chat rename: ${r.status}`)
    }
    return r.json()
  },

  async patchMetadata(
    sid: string,
    body: { archived?: boolean; favorite?: boolean; model?: string | null; permission_mode?: string; effort?: string | null },
  ): Promise<{
    session_id: string
    archived: boolean
    favorite: boolean
    model: string
    permission_mode: string
    effort?: string | null
    effort_applied?: string
    effective?: string
  }> {
    const r = await fetch(`${BASE}/sessions/${sid}/metadata`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/chat metadata: ${r.status}`)
    }
    return r.json()
  },

  async patchActivePlan(
    sid: string,
    planId: string | null,
  ): Promise<{
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
      throw new Error(err.detail || `cc/chat patchActivePlan: ${r.status}`)
    }
    return r.json()
  },

  // 压缩上下文: 折叠旧总控会话历史 → 新开干净总控并以折叠记录起步 → 归档旧会话。返回新会话 meta。
  async compact(sid: string): Promise<CcChatSessionMeta> {
    const r = await fetch(`${BASE}/sessions/${sid}/compact`, { method: 'POST' })
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }))
      throw new Error(err.detail || `cc/chat compact: ${r.status}`)
    }
    return r.json()
  },

  wsUrl(id: string): string {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${window.location.host}${BASE}/sessions/${id}/ws`
  },
}

// ── WS 帧类型 (跟 cc_chat_bridge.py 1:1) ────────────────────────────────────
//
// content blocks (来自 claude-agent-sdk, 各带 type 标签):

export type ContentBlock =
  | { type: 'text'; text: string }
  | { type: 'thinking'; thinking: string; signature?: string }
  | { type: 'tool_use'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; tool_use_id: string; content: unknown; is_error?: boolean | null }
  | { type: 'server_tool_use'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'server_tool_result'; tool_use_id: string; content: unknown; is_error?: boolean | null }
  | { type: string; [k: string]: unknown }   // fallback

// server → client frames (kind discriminator):

export interface SnapshotFrame {
  kind: 'snapshot'
  history: { role: 'user' | 'assistant'; text: string }[]
  messages?: Record<string, unknown>[]
  tokenUsage?: Record<string, unknown> | null
}

export interface SystemFrame {
  kind: 'system'
  subtype?: string
  session_id?: string
  [k: string]: unknown
}

export interface ContextResolvedFrame {
  kind: 'context_resolved'
  session_id: string
  trigger: 'plan_switch' | 'turn_injection' | string
  switched?: boolean
  plan_id?: string | null
  summary?: string
  context: Record<string, unknown>
}

export interface AssistantFrame {
  kind: 'assistant'
  content: ContentBlock[]
  model: string
  message_id: string | null
  session_id: string | null
  stop_reason: string | null
  usage: Record<string, unknown> | null
  parent_tool_use_id: string | null
  uuid: string | null
  error: string | null
}

export interface UserMsgFrame {
  kind: 'user'
  content: ContentBlock[] | string
  uuid: string | null
  parent_tool_use_id: string | null
  tool_use_result: Record<string, unknown> | null
}

export interface ResultFrame {
  kind: 'result'
  subtype: string
  duration_ms: number
  duration_api_ms: number
  is_error: boolean
  num_turns: number
  session_id: string
  stop_reason: string | null
  total_cost_usd: number | null
  usage: Record<string, unknown> | null
  result: string | null
  [k: string]: unknown
}

export interface StreamEventFrame {
  kind: 'stream_event'
  [k: string]: unknown
}

export interface RateLimitFrame {
  kind: 'rate_limit'
  [k: string]: unknown
}

export interface ErrorFrame {
  kind: 'error'
  code: string
  message: string
}

export interface ExitFrame {
  kind: 'exit'
  reason: string
}

export type CcChatServerFrame =
  | SnapshotFrame
  | SystemFrame
  | ContextResolvedFrame
  | AssistantFrame
  | UserMsgFrame
  | ResultFrame
  | StreamEventFrame
  | RateLimitFrame
  | ErrorFrame
  | ExitFrame

export type CcChatClientFrame =
  | {
      type: 'user.message'
      content: string | { type: 'text'; text: string }[]
      permissionMode?: string
      skipPermissions?: boolean
    }
  | { type: 'user.interrupt' }
  | { type: 'session.permission_mode'; permissionMode: string }
  | { type: 'session.model'; model: string | null }
