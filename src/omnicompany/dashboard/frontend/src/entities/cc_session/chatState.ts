/**
 * Dashboard 原生聊天状态机。
 *
 * wire 契约以 ccdaemon/normalized_protocol.py 的 kind 判别为准。这里刻意不认
 * Claude/Codex SDK 的原始帧，避免 Dashboard 再长出第三套聊天协议。
 */

export type ChatItem =
  | { type: 'user'; id: string; text: string }
  | { type: 'assistant'; id: string; text: string; streaming: boolean }
  | { type: 'thinking'; id: string; text: string }
  | {
      type: 'tool'
      id: string
      toolId: string
      toolName: string
      input?: unknown
      result?: string
      isError: boolean
      done: boolean
    }
  | { type: 'system'; id: string; text: string; level: 'info' | 'error' }
  | { type: 'context'; id: string; summary: string; planId: string | null }

export interface NativeChatState {
  sessionId: string
  items: ChatItem[]
  byId: Record<string, ChatItem>
  toolByToolId: Record<string, Extract<ChatItem, { type: 'tool' }>>
  streamingId: string | null
  running: boolean
  aborted: boolean
  status: string | null
  tokenBudget: { used?: number; total?: number; [key: string]: unknown } | null
  seq: number
}

export interface PendingPermission {
  requestId: string
  toolName: string
  input?: unknown
}

export type NormalizedFrame = {
  kind?: string
  id?: string
  role?: string
  content?: unknown
  text?: string
  message?: string
  error?: unknown
  code?: string
  toolId?: string
  toolName?: string
  name?: string
  toolInput?: unknown
  input?: unknown
  resultText?: unknown
  result?: unknown
  isError?: boolean
  exitCode?: number
  summary?: string
  status?: string
  planId?: string
  sessionId?: string
  newSessionId?: string
  aborted?: boolean
  reason?: string
  tokenBudget?: NativeChatState['tokenBudget']
  tokenUsage?: NativeChatState['tokenBudget']
  messages?: NormalizedFrame[]
  history?: Array<{ role?: string; text?: string }>
  requestId?: string
  [key: string]: unknown
}

export function createChatState(sessionId: string): NativeChatState {
  return {
    sessionId,
    items: [],
    byId: Object.create(null),
    toolByToolId: Object.create(null),
    streamingId: null,
    running: false,
    aborted: false,
    status: null,
    tokenBudget: null,
    seq: 0,
  }
}

function localId(state: NativeChatState, kind: string): string {
  return `local_${kind}_${state.seq++}`
}

function put<T extends ChatItem>(state: NativeChatState, item: T): T {
  const existing = state.byId[item.id]
  if (existing && existing.type === item.type) {
    Object.assign(existing, item)
    return existing as T
  }
  state.byId[item.id] = item
  state.items.push(item)
  return item
}

function safeText(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}

function resultText(frame: NormalizedFrame): string {
  if (frame.content != null) return safeText(frame.content)
  if (frame.resultText != null) return safeText(frame.resultText)
  return safeText(frame.result)
}

function isInterrupt(frame: NormalizedFrame): boolean {
  if (frame.code === 'interrupted') return true
  return /interrupted|user interrupt/i.test(safeText(frame.message ?? frame.error ?? frame.content))
}

function ingest(state: NativeChatState, frame: NormalizedFrame): void {
  const kind = frame.kind
  if (kind === 'text') {
    const content = safeText(frame.content)
    if (!content.trim()) return
    if (frame.role === 'user') {
      put(state, { type: 'user', id: frame.id || localId(state, 'user'), text: content })
      return
    }
    if (state.streamingId) {
      const streaming = state.byId[state.streamingId]
      if (streaming?.type === 'assistant') {
        streaming.text = content
        streaming.streaming = false
        state.streamingId = null
        return
      }
    }
    put(state, {
      type: 'assistant', id: frame.id || localId(state, 'assistant'), text: content, streaming: false,
    })
    return
  }
  if (kind === 'thinking') {
    const content = safeText(frame.content)
    if (content.trim()) put(state, { type: 'thinking', id: frame.id || localId(state, 'thinking'), text: content })
    return
  }
  if (kind === 'tool_use') {
    const toolId = String(frame.toolId || '')
    const item = put(state, {
      type: 'tool',
      id: toolId ? `tool_${toolId}` : (frame.id || localId(state, 'tool')),
      toolId,
      toolName: String(frame.toolName || frame.name || 'tool'),
      input: frame.toolInput ?? frame.input,
      isError: false,
      done: false,
    })
    if (toolId) state.toolByToolId[toolId] = item
    return
  }
  if (kind === 'tool_result') {
    const toolId = String(frame.toolId || '')
    const isError = Boolean(frame.isError || (frame.exitCode != null && Number(frame.exitCode) !== 0))
    const existing = toolId ? state.toolByToolId[toolId] : undefined
    if (existing) {
      existing.result = resultText(frame)
      existing.isError = isError
      existing.done = true
      return
    }
    const item = put(state, {
      type: 'tool',
      id: toolId ? `tool_${toolId}` : (frame.id || localId(state, 'tool')),
      toolId,
      toolName: String(frame.toolName || 'tool'),
      result: resultText(frame),
      isError,
      done: true,
    })
    if (toolId) state.toolByToolId[toolId] = item
    return
  }
  if (kind === 'error') {
    const interrupted = isInterrupt(frame)
    put(state, {
      type: 'system',
      id: frame.id || localId(state, interrupted ? 'interrupt' : 'error'),
      text: interrupted ? '（已中断）' : safeText(frame.message ?? frame.error ?? frame.content) || 'unknown error',
      level: interrupted ? 'info' : 'error',
    })
    return
  }
  if (kind === 'context_event') {
    put(state, {
      type: 'context',
      id: frame.id || localId(state, 'context'),
      summary: String(frame.summary || frame.status || '上下文已更新'),
      planId: frame.planId ? String(frame.planId) : null,
    })
  }
}

export function applySnapshot(state: NativeChatState, frame: NormalizedFrame): NativeChatState {
  state.items = []
  state.byId = Object.create(null)
  state.toolByToolId = Object.create(null)
  state.streamingId = null
  state.running = false
  state.aborted = false
  state.status = null
  state.tokenBudget = frame.tokenUsage || null

  if (Array.isArray(frame.messages) && frame.messages.length > 0) {
    for (const message of frame.messages) ingest(state, message)
    return state
  }
  for (const history of frame.history || []) {
    ingest(state, {
      kind: 'text', role: history.role === 'user' ? 'user' : 'assistant', content: history.text || '',
    })
  }
  return state
}

export function applyFrame(state: NativeChatState, frame: NormalizedFrame): NativeChatState {
  switch (frame.kind) {
    case 'snapshot':
      return applySnapshot(state, frame)
    case 'stream_delta': {
      if (!state.streamingId || state.byId[state.streamingId]?.type !== 'assistant') {
        const id = localId(state, 'stream')
        put(state, { type: 'assistant', id, text: '', streaming: true })
        state.streamingId = id
      }
      const item = state.byId[state.streamingId]
      if (item?.type === 'assistant') {
        item.text += safeText(frame.content)
        item.streaming = true
      }
      return state
    }
    case 'stream_end': {
      const item = state.streamingId ? state.byId[state.streamingId] : undefined
      if (item?.type === 'assistant') item.streaming = false
      state.streamingId = null
      return state
    }
    case 'text':
    case 'thinking':
    case 'tool_use':
    case 'tool_result':
    case 'context_event':
      ingest(state, frame)
      return state
    case 'error':
      ingest(state, frame)
      state.running = false
      state.streamingId = null
      state.aborted = isInterrupt(frame)
      state.status = null
      return state
    case 'status':
      if (frame.tokenBudget) state.tokenBudget = frame.tokenBudget
      if (frame.text === 'rate_limited') {
        put(state, { type: 'system', id: localId(state, 'rate'), text: '（已限流，稍后重试）', level: 'info' })
      } else if (frame.text && frame.text !== 'token_budget') {
        state.status = frame.text
      }
      return state
    case 'complete': {
      const item = state.streamingId ? state.byId[state.streamingId] : undefined
      if (item?.type === 'assistant') item.streaming = false
      state.streamingId = null
      state.running = false
      state.status = null
      state.aborted = Boolean(frame.aborted)
      if (frame.aborted) {
        put(state, { type: 'system', id: localId(state, 'interrupt'), text: '（已中断）', level: 'info' })
      }
      return state
    }
    case 'result':
      state.running = false
      state.streamingId = null
      return state
    case 'session_created':
      if (frame.newSessionId) state.sessionId = frame.newSessionId
      return state
    case 'exit':
      state.running = false
      state.streamingId = null
      put(state, {
        type: 'system', id: localId(state, 'exit'), text: `会话已结束: ${frame.reason || 'ended'}`, level: 'info',
      })
      return state
    default:
      return state
  }
}

export function markUserSent(state: NativeChatState, text: string): void {
  put(state, { type: 'user', id: localId(state, 'user'), text })
  state.running = true
  state.aborted = false
  state.status = null
}

export function markInterrupting(state: NativeChatState): void {
  state.status = '正在停止…'
}
