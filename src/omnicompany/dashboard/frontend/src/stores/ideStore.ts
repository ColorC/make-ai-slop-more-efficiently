/**
 * IDE Store — Zustand 事件分发中枢
 *
 * 借鉴 OpenHands Redux store 模式，但使用更轻量的 Zustand。
 * 中央 handleEvent() 根据 event_type 分发到不同状态切片。
 */

import { create } from 'zustand'
import {
  type IDEEvent,
  type SessionInfo,
  type FileChange,
  connectSSE,
  ideApi,
} from '../api/ideClient'

// ── Types ──

export type AgentState = 'idle' | 'running' | 'thinking' | 'finished' | 'error' | 'cancelled'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'thinking' | 'system'
  content: string
  timestamp: string
}

export interface ToolCallEntry {
  id: string
  parentEventId: string
  tool: string
  args: Record<string, any>
  argsSummary: string
  result?: string
  status: 'running' | 'done' | 'error'
  startTime: string
  endTime?: string
  durationMs?: number
}

export interface TerminalLine {
  command: string
  output: string
  timestamp: string
  exitCode?: number
}

// ── Store ──

interface IDEStore {
  // Connection
  connected: boolean
  activeTraceId: string | null
  eventSource: EventSource | null

  // State
  agentState: AgentState
  messages: ChatMessage[]
  toolCalls: ToolCallEntry[]
  fileChanges: FileChange[]
  terminalLines: TerminalLine[]
  rawEvents: IDEEvent[]
  sessions: SessionInfo[]

  // Dedup
  _seenEventIds: Set<string>

  // Token tracking
  totalPromptTokens: number
  totalCompletionTokens: number
  currentTurn: number

  // Actions
  handleEvent: (event: IDEEvent) => void
  connectToTrace: (traceId: string | null) => Promise<void>
  disconnect: () => void
  sendMessage: (text: string) => Promise<void>
  cancelAgent: () => Promise<void>
  loadSessions: () => Promise<void>
  newSession: () => void
  reset: () => void
}

function summarizeArgs(args: Record<string, any>): string {
  const entries = Object.entries(args)
  if (entries.length === 0) return '(no args)'
  return entries
    .map(([k, v]) => {
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      return `${k}: ${s.length > 60 ? s.slice(0, 57) + '...' : s}`
    })
    .join(', ')
}

function toolIcon(tool: string): string {
  if (tool.includes('bash') || tool.includes('cmd')) return '[Terminal]'
  if (tool.includes('file') || tool.includes('read') || tool.includes('write') || tool.includes('edit')) return '[File]'
  if (tool.includes('search') || tool.includes('grep') || tool.includes('glob')) return '[Search]'
  return '[Tool]'
}

export const useIDEStore = create<IDEStore>((set, get) => ({
  // Initial state
  connected: false,
  activeTraceId: null,
  eventSource: null,
  agentState: 'idle',
  messages: [],
  toolCalls: [],
  fileChanges: [],
  terminalLines: [],
  rawEvents: [],
  sessions: [],
  _seenEventIds: new Set<string>(),
  totalPromptTokens: 0,
  totalCompletionTokens: 0,
  currentTurn: 0,

  handleEvent: (event: IDEEvent) => {
    const state = get()

    // Dedup: skip already-processed events
    if (state._seenEventIds.has(event.id)) return
    state._seenEventIds.add(event.id)

    // Always append to raw events
    set({ rawEvents: [...state.rawEvents, event] })

    switch (event.event_type) {
      case 'task.intent': {
        const msg: ChatMessage = {
          id: event.id,
          role: 'user',
          content: event.payload.instruction || '',
          timestamp: event.timestamp,
        }
        set({ messages: [...state.messages, msg] })
        break
      }

      case 'agent.llm.response': {
        // 旧 IDEAgentLoop 用的事件类型 (本字段保留兼容旧 trace 历史回放)
        const content = event.payload.content || event.payload.text || ''
        if (content) {
          const msg: ChatMessage = {
            id: event.id,
            role: 'assistant',
            content,
            timestamp: event.timestamp,
          }
          set({ messages: [...state.messages, msg] })
        }
        const meta = event.metadata
        if (meta) {
          set({
            totalPromptTokens: state.totalPromptTokens + (meta.prompt_tokens || 0),
            totalCompletionTokens: state.totalCompletionTokens + (meta.completion_tokens || 0),
          })
        }
        break
      }

      case 'router.llm_call.output': {
        // 新 NativeIdeAgent (services/_core/agent.LLMCallRouter) 的事件类型
        const data = event.payload?.data || {}
        const usage = data.usage || {}
        // text 在 router output (text_preview, 完整在 verdict 内, 这里 preview 够展示)
        const textPreview = data.text_preview || ''
        if (textPreview) {
          const msg: ChatMessage = {
            id: event.id,
            role: 'assistant',
            content: textPreview,
            timestamp: event.timestamp,
          }
          set({ messages: [...state.messages, msg] })
        }
        set({
          totalPromptTokens: state.totalPromptTokens + (usage.input_tokens || 0),
          totalCompletionTokens: state.totalCompletionTokens + (usage.output_tokens || 0),
          currentTurn: (data.turn ?? state.currentTurn - 1) + 1,
        })
        break
      }

      case 'agent.think': {
        const msg: ChatMessage = {
          id: event.id,
          role: 'thinking',
          content: event.payload.thought || event.payload.content || '',
          timestamp: event.timestamp,
        }
        set({ messages: [...state.messages, msg] })
        break
      }

      case 'router.tool_dispatch.input':
      case 'agent.tool.call': {
        // router.tool_dispatch.input: 新 NativeIdeAgent 的事件类型 (含 tool_name + args)
        // agent.tool.call: 旧 IDEAgentLoop 的事件类型 (兼容回放历史)
        const data = event.payload?.data || event.payload || {}
        const tool = data.tool_name || data.tool || 'unknown'
        const args = data.args || event.payload.args || {}
        // Use tool_use_id (from LLM) as the correlation key, fallback to event.id
        const toolUseId = event.payload.tool_use_id || event.id
        const entry: ToolCallEntry = {
          id: event.id,
          parentEventId: toolUseId,
          tool,
          args,
          argsSummary: `${toolIcon(tool)} ${tool} ${summarizeArgs(args)}`,
          status: 'running',
          startTime: event.timestamp,
        }
        set({ toolCalls: [...state.toolCalls, entry] })

        // For bash/cmd tools, also add to terminal lines
        if (tool.includes('bash') || tool.includes('cmd')) {
          const cmd = args.command || args.cmd || JSON.stringify(args)
          set({
            terminalLines: [
              ...state.terminalLines,
              { command: cmd, output: '...running...', timestamp: event.timestamp },
            ],
          })
        }
        break
      }

      case 'router.tool_dispatch.output':
      case 'agent.tool.result': {
        // router.tool_dispatch.output: 新 NativeIdeAgent 事件
        // agent.tool.result: 旧 IDEAgentLoop 事件
        const data = event.payload?.data || event.payload || {}
        const tool_use_id = data.tool_use_id || event.payload.tool_use_id
        const parentId = tool_use_id || event.parent_id || event.payload.parent_id
        const result = data.result_preview || data.result || event.payload.result || ''
        const isError = data.is_error || event.payload.error
        const durationMs = data.duration_ms || event.metadata?.duration_ms
        if (parentId) {
          const updatedCalls = state.toolCalls.map((tc) =>
            tc.parentEventId === parentId
              ? {
                  ...tc,
                  result: typeof result === 'string' ? result : JSON.stringify(result),
                  status: (isError ? 'error' : 'done') as 'done' | 'error',
                  endTime: event.timestamp,
                  durationMs,
                }
              : tc,
          )
          set({ toolCalls: updatedCalls })

          // Update terminal output for bash/cmd
          const call = state.toolCalls.find((tc) => tc.parentEventId === parentId)
          if (call && (call.tool.includes('bash') || call.tool.includes('cmd'))) {
            const updatedTerminal = [...state.terminalLines]
            const lastIdx = updatedTerminal.length - 1
            if (lastIdx >= 0) {
              updatedTerminal[lastIdx] = {
                ...updatedTerminal[lastIdx],
                output: typeof result === 'string' ? result : '',
                exitCode: data.exit_code || event.payload.exit_code,
              }
            }
            set({ terminalLines: updatedTerminal })
          }

          // Extract file changes
          if (call) {
            const tool = call.tool
            const args = call.args
            if (
              tool.includes('file') ||
              tool.includes('edit') ||
              tool.includes('write') ||
              tool.includes('str_replace')
            ) {
              const fc: FileChange = {
                path: args.path || args.file_path || '',
                action: tool.includes('read') || tool.includes('view') ? 'read' : 'edit',
                old_text: args.old_str || args.old_text,
                new_text: args.new_str || args.new_text || args.content,
              }
              if (fc.path) {
                set({ fileChanges: [...state.fileChanges, fc] })
              }
            }
          }
        }
        break
      }

      case 'agent.state.change': {
        const toState = event.payload.to_state as AgentState
        set({ agentState: toState })
        break
      }

      case 'task.finish': {
        set({ agentState: 'finished' })
        const msg: ChatMessage = {
          id: event.id,
          role: 'system',
          content: `Task completed: ${event.payload.result || 'done'}`,
          timestamp: event.timestamp,
        }
        set({ messages: [...get().messages, msg] })
        break
      }

      case 'router.extract_result.output': {
        // 新 NativeIdeAgent 收尾事件 (verdict + final text)
        const data = event.payload?.data || {}
        const previewStr = data.output_preview || ''
        // output_preview 是 dict 字符串 ({'text': ..., 'turn_count': ..., ...})
        const m = previewStr.match(/'text':\s*['"]([\s\S]*?)['"](?:,|\})/)
        const text = m ? m[1] : previewStr
        const msg: ChatMessage = {
          id: event.id,
          role: 'assistant',
          content: text,
          timestamp: event.timestamp,
        }
        set({ messages: [...get().messages, msg] })
        break
      }

      case 'task.error': {
        set({ agentState: 'error' })
        const msg: ChatMessage = {
          id: event.id,
          role: 'system',
          content: `Error: ${event.payload.error || event.payload.reason || 'unknown'}`,
          timestamp: event.timestamp,
        }
        set({ messages: [...get().messages, msg] })
        break
      }

      case 'agent_loop.compact': {
        const msg: ChatMessage = {
          id: event.id,
          role: 'system',
          content: '[Context compressed]',
          timestamp: event.timestamp,
        }
        set({ messages: [...get().messages, msg] })
        break
      }

      case 'agent_loop.budget': {
        const msg: ChatMessage = {
          id: event.id,
          role: 'system',
          content: `[Budget warning: ${event.payload.turns_left || '?'} turns remaining]`,
          timestamp: event.timestamp,
        }
        set({ messages: [...get().messages, msg] })
        break
      }

      case 'agent_loop.llm_call': {
        set({ currentTurn: (event.payload.turn ?? state.currentTurn) + 1 })
        break
      }
    }
  },

  connectToTrace: async (traceId: string | null) => {
    // Disconnect + clear old state
    get().disconnect()
    get().reset()

    if (traceId) {
      set({ activeTraceId: traceId })
      // Load history first
      try {
        const history = await ideApi.traceHistory(traceId)
        for (const ev of history) {
          get().handleEvent(ev)
        }
      } catch (e) {
        console.warn('Failed to load trace history:', e)
      }
    }

    // Open SSE connection filtered to this trace
    const es = connectSSE(traceId, (event) => {
      get().handleEvent(event)
    })

    set({
      connected: true,
      activeTraceId: traceId,
      eventSource: es,
    })
  },

  disconnect: () => {
    const { eventSource } = get()
    if (eventSource) {
      eventSource.close()
    }
    set({ connected: false, eventSource: null })
  },

  sendMessage: async (text: string) => {
    const { activeTraceId } = get()
    try {
      const resp = await ideApi.send(activeTraceId, text)
      if (!activeTraceId) {
        // New session — add to sessions list immediately
        const now = new Date().toISOString()
        const newSession: SessionInfo = {
          trace_id: resp.trace_id,
          status: 'running',
          task_desc: text,
          created_at: now,
          last_active: now,
        }
        set({
          activeTraceId: resp.trace_id,
          sessions: [newSession, ...get().sessions],
        })
        // Connect SSE filtered to this trace
        get().disconnect()
        const es = connectSSE(resp.trace_id, (event) => {
          get().handleEvent(event)
        })
        set({ connected: true, eventSource: es })
      }
      set({ agentState: 'running' })
    } catch (e) {
      console.error('Failed to send message:', e)
    }
  },

  cancelAgent: async () => {
    const { activeTraceId } = get()
    if (activeTraceId) {
      try {
        await ideApi.cancel(activeTraceId)
      } catch (e) {
        console.error('Failed to cancel:', e)
      }
    }
  },

  loadSessions: async () => {
    try {
      const sessions = await ideApi.sessions()
      set({ sessions })
    } catch (e) {
      console.warn('Failed to load sessions:', e)
    }
  },

  newSession: () => {
    get().disconnect()
    get().reset()
    // Don't connect SSE yet — will connect when first message is sent
  },

  reset: () => {
    set({
      activeTraceId: null,
      agentState: 'idle',
      messages: [],
      toolCalls: [],
      fileChanges: [],
      terminalLines: [],
      rawEvents: [],
      _seenEventIds: new Set<string>(),
      totalPromptTokens: 0,
      totalCompletionTokens: 0,
      currentTurn: 0,
    })
  },
}))
