import React, { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Activity, Check, CircleStop, ClipboardCheck, Plus, Send, X } from 'lucide-react'
import { ccChatApi, type CcChatSessionMeta } from '../../api/ccChatClient'
import ConnectionStatus from '../../components/ConnectionStatus'
import { useWsAutoReconnect } from '../../lib/wsAutoReconnect'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { usePanels } from '../../stores/panelsStore'
import { TabSidecarToggleButton } from '../../shell/TabSidecar'
import {
  applyFrame,
  createChatState,
  markInterrupting,
  markUserSent,
  type ChatItem,
  type NormalizedFrame,
  type PendingPermission,
} from './chatState'
import './native_chat.css'

const PERMISSION_MODES = [
  { value: 'default', label: '每次确认' },
  { value: 'acceptEdits', label: '自动改文件' },
  { value: 'bypassPermissions', label: '完全授权' },
  { value: 'plan', label: '只规划' },
]

function providerDisplayName(provider?: string | null): string {
  const names: Record<string, string> = {
    claude_code: 'Claude', codex: 'Codex', codebuddy: 'CodeBuddy', kimi: 'Kimi', opencode: 'OpenCode',
    omni_agent: 'OmniAgent', controller: '总控',
  }
  return names[String(provider || '')] || String(provider || 'Agent')
}

function stringify(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}

function argSummary(value: unknown): string {
  if (!value || typeof value !== 'object') return stringify(value)
  const input = value as Record<string, unknown>
  for (const key of ['command', 'file_path', 'path', 'pattern', 'query', 'prompt', 'description']) {
    if (input[key] != null && input[key] !== '') return String(input[key])
  }
  return stringify(value)
}

function ToolCard({ item }: { item: Extract<ChatItem, { type: 'tool' }> }) {
  return (
    <details className={`nc-tool${item.done ? (item.isError ? ' err' : ' ok') : ' running'}`}>
      <summary>
        <span className="nc-tool-state" aria-hidden>
          {!item.done ? <span className="nc-spin" /> : item.isError ? <X size={13} /> : <Check size={13} />}
        </span>
        <code>{item.toolName}</code>
        <span className="nc-tool-arg">{argSummary(item.input)}</span>
      </summary>
      <div className="nc-tool-body">
        {item.input !== undefined && <><label>入参</label><pre>{stringify(item.input)}</pre></>}
        {item.done && <><label>结果</label><pre data-error={item.isError ? '1' : undefined}>{item.result || '(无输出)'}</pre></>}
      </div>
    </details>
  )
}

function ChatRow({ item }: { item: ChatItem }) {
  if (item.type === 'context') {
    return <div className="nc-context">上下文 · {item.summary}{item.planId ? ` · ${item.planId}` : ''}</div>
  }
  if (item.type === 'system') {
    return <div className={`nc-system${item.level === 'error' ? ' err' : ''}`}>{item.text}</div>
  }
  if (item.type === 'thinking') {
    return <details className="nc-thinking"><summary>思考过程</summary><pre>{item.text}</pre></details>
  }
  if (item.type === 'tool') return <ToolCard item={item} />
  if (item.type === 'user') return <div className="nc-row user"><div className="nc-bubble user">{item.text}</div></div>
  return (
    <div className="nc-row assistant">
      <div className={`nc-bubble assistant${item.streaming ? ' streaming' : ''}`}>
        {item.streaming
          ? <span style={{ whiteSpace: 'pre-wrap' }}>{item.text}</span>
          : <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>}
      </div>
    </div>
  )
}

export interface NativeChatPanelProps {
  sessionId: string
  initialMeta?: CcChatSessionMeta | null
  title?: string
  onNewSession?: () => void | Promise<void>
}

/** 直接消费 Dashboard → ccdaemon 的真实归一化 WS，不经过 iframe/chatui。 */
export default function NativeChatPanel({ sessionId, initialMeta, title, onNewSession }: NativeChatPanelProps) {
  const stateRef = useRef(createChatState(sessionId))
  const [version, setVersion] = useState(0)
  const [meta, setMeta] = useState<CcChatSessionMeta | null>(initialMeta || null)
  const [input, setInput] = useState('')
  const [permissionMode, setPermissionMode] = useState(initialMeta?.permission_mode || 'bypassPermissions')
  const [pendingPermissions, setPendingPermissions] = useState<PendingPermission[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const shouldStickRef = useRef(true)

  const redraw = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    stateRef.current = createChatState(sessionId)
    setMeta(initialMeta || null)
    setPermissionMode(initialMeta?.permission_mode || 'bypassPermissions')
    setPendingPermissions([])
    redraw()
    let live = true
    void ccChatApi.list({ limit: 100, includeArchived: true }).then((items) => {
      if (!live) return
      const found = items.find((item) => item.id === sessionId)
      if (found) {
        setMeta(found)
        setPermissionMode(found.permission_mode || 'bypassPermissions')
      }
    }).catch(() => { /* WS 会给出真实错误，meta 失败不阻断聊天 */ })
    return () => { live = false }
  }, [initialMeta, redraw, sessionId])

  const onMessage = useCallback((event: MessageEvent) => {
    let frame: NormalizedFrame
    try { frame = JSON.parse(String(event.data)) as NormalizedFrame } catch { return }
    if (frame.kind === 'permission_request' && frame.requestId) {
      const request: PendingPermission = {
        requestId: String(frame.requestId),
        toolName: String(frame.toolName || 'tool'),
        input: frame.input,
      }
      setPendingPermissions((items) => items.some((item) => item.requestId === request.requestId) ? items : [...items, request])
    } else if (frame.kind === 'permission_cancelled' && frame.requestId) {
      setPendingPermissions((items) => items.filter((item) => item.requestId !== frame.requestId))
    }
    applyFrame(stateRef.current, frame)
    redraw()
  }, [redraw])

  const connection = useWsAutoReconnect({
    url: ccChatApi.wsUrl(sessionId),
    onMessage,
  })

  useEffect(() => {
    if (!shouldStickRef.current) return
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [version, pendingPermissions.length])

  const sendFrame = useCallback((frame: Record<string, unknown>) => {
    connection.send(JSON.stringify(frame))
  }, [connection.send])

  const sendMessage = useCallback(() => {
    const text = input.trim()
    if (!text || stateRef.current.running || !meta?.alive) return
    markUserSent(stateRef.current, text)
    setInput('')
    shouldStickRef.current = true
    sendFrame({ type: 'user.message', content: text, permissionMode })
    redraw()
  }, [input, meta?.alive, permissionMode, redraw, sendFrame])

  const interrupt = useCallback(() => {
    if (!stateRef.current.running) return
    markInterrupting(stateRef.current)
    sendFrame({ type: 'user.interrupt' })
    redraw()
  }, [redraw, sendFrame])

  const decidePermission = useCallback((requestId: string, allow: boolean) => {
    sendFrame({
      type: 'claude-permission-response', requestId, allow,
      message: allow ? undefined : 'User denied tool use',
    })
    setPendingPermissions((items) => items.filter((item) => item.requestId !== requestId))
  }, [sendFrame])

  const onPermissionMode = (next: string) => {
    setPermissionMode(next)
    sendFrame({ type: 'session.permission_mode', permissionMode: next })
  }

  const state = stateRef.current
  const canSend = Boolean(input.trim() && meta?.alive && connection.state === 'connected' && !state.running)
  const budget = state.tokenBudget
  const budgetText = budget && typeof budget.used === 'number' && typeof budget.total === 'number'
    ? `${Math.round(budget.used / 1000)}k / ${Math.round(budget.total / 1000)}k`
    : ''

  return (
    <section className="nc-root" data-testid="native-chat-panel" data-session-id={sessionId}>
      <header className="nc-head">
        <div className="nc-titlebox">
          <strong>{title || meta?.name || (meta?.provider === 'controller' ? '总控对话' : '真实对话')}</strong>
          <span>{providerDisplayName(meta?.provider)} · {meta?.model || 'default'} · {sessionId.slice(-8)}</span>
        </div>
        <select
          className="nc-mode"
          value={permissionMode}
          onChange={(event) => onPermissionMode(event.target.value)}
          aria-label="权限模式"
          data-testid="native-chat-permission-mode"
        >
          {PERMISSION_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
        </select>
        <ConnectionStatus
          state={connection.state}
          reconnectAttempts={connection.reconnectAttempts}
          disconnectedAt={connection.disconnectedAt}
          label="chat"
        />
        <TabSidecarToggleButton
          label="伴随视图"
          showWhen="collapsed"
          testId="session-ctx-toggle"
        />
        <KebabMenu testid="native-chat-actions" items={[
          {
            label: '活跃会话 / Multiagent',
            icon: <Activity size={15} />,
            testid: 'native-chat-open-multiagent',
            onClick: () => usePanels.getState().openTab({ type: 'multiagent', id: 'main' }, '活跃会话'),
          },
          {
            label: '审阅',
            icon: <ClipboardCheck size={15} />,
            testid: 'native-chat-open-review',
            onClick: () => usePanels.getState().openTab({ type: 'review_queue', id: 'main' }, '审阅'),
          },
        ] as KebabItem[]} />
        {onNewSession && (
          <button type="button" className="nc-new" onClick={() => { void onNewSession() }} data-testid="native-chat-new">
            <Plus size={14} />新对话
          </button>
        )}
      </header>

      <div
        className="nc-messages"
        ref={scrollRef}
        onScroll={(event) => {
          const el = event.currentTarget
          shouldStickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96
        }}
        data-testid="native-chat-messages"
      >
        {state.items.length === 0 && (
          <div className="nc-empty">
            <strong>{meta?.provider === 'controller' ? '这是总控真实会话' : '这是原生真实会话'}</strong>
            <span>消息、工具调用和中断都直接连接 ccdaemon；没有 iframe，也没有跳转到另一套会话。</span>
          </div>
        )}
        {state.items.map((item) => <ChatRow key={item.id} item={item} />)}
      </div>

      {pendingPermissions.map((request) => (
        <div className="nc-permission" key={request.requestId} data-testid="native-chat-permission">
          <div><strong>{request.toolName}</strong><span>{argSummary(request.input)}</span></div>
          <button type="button" onClick={() => decidePermission(request.requestId, false)}>拒绝</button>
          <button type="button" className="allow" onClick={() => decidePermission(request.requestId, true)}>允许</button>
        </div>
      ))}

      {(state.running || state.status || budgetText) && (
        <div className="nc-run" data-testid="native-chat-running">
          {state.running && <span className="nc-spin" />}
          <span>{state.status || (state.running ? '正在响应…' : '')}</span>
          {budgetText && <code>{budgetText}</code>}
        </div>
      )}

      <footer className="nc-compose">
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              if (state.running) interrupt()
              else sendMessage()
            }
          }}
          placeholder={meta?.alive === false ? '会话已结束' : '给总控发消息…（Shift+Enter 换行）'}
          disabled={meta?.alive === false}
          data-testid="native-chat-input"
          rows={1}
        />
        <button
          type="button"
          className={state.running ? 'stop' : 'send'}
          onClick={state.running ? interrupt : sendMessage}
          disabled={state.running ? false : !canSend}
          aria-label={state.running ? '停止' : '发送'}
          data-testid={state.running ? 'native-chat-stop' : 'native-chat-send'}
        >
          {state.running ? <CircleStop size={19} /> : <Send size={19} />}
        </button>
      </footer>
    </section>
  )
}
