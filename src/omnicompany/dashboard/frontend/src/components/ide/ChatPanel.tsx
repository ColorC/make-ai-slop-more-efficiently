/**
 * ChatPanel — 对话面板
 *
 * 消息和工具调用卡片按时间交替排列，
 * 类似 OpenHands 的 ChatPanel 组件。
 */

import React, { useEffect, useRef, useState } from 'react'
import { useIDEStore } from '../../stores/ideStore'
import ChatMessage from './ChatMessage'
import ToolCallCard from './ToolCallCard'

interface Props {
  onFileClick?: (path: string) => void
  appendText?: string | null
  onAppendConsumed?: () => void
}

interface TimelineItem {
  type: 'message' | 'tool'
  timestamp: string
  id: string
}

export default function ChatPanel({ onFileClick, appendText, onAppendConsumed }: Props) {
  const { messages, toolCalls, agentState, sendMessage } = useIDEStore()
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // Append context text when triggered from ContextPanel
  useEffect(() => {
    if (!appendText) return
    setInput(prev => prev ? `${prev}\n\n${appendText}` : appendText)
    onAppendConsumed?.()
  }, [appendText])

  // Build interleaved timeline
  const timeline: TimelineItem[] = []
  for (const m of messages) {
    timeline.push({ type: 'message', timestamp: m.timestamp, id: m.id })
  }
  for (const tc of toolCalls) {
    timeline.push({ type: 'tool', timestamp: tc.startTime, id: tc.id })
  }
  timeline.sort((a, b) => a.timestamp.localeCompare(b.timestamp))

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages.length, toolCalls.length, autoScroll])

  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 50)
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    sendMessage(text)
    setInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const isRunning = agentState === 'running' || agentState === 'thinking'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* Messages area */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          paddingTop: 8,
          paddingBottom: 8,
        }}
      >
        {timeline.length === 0 && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ color: '#444', fontSize: 15, textAlign: 'center' }}>
              <div style={{ fontSize: 24, marginBottom: 8 }}>omnicompany</div>
              <div>Type a message to start an agent session</div>
            </div>
          </div>
        )}

        {timeline.map((item) => {
          if (item.type === 'message') {
            const msg = messages.find((m) => m.id === item.id)
            if (!msg) return null
            return <ChatMessage key={item.id} message={msg} />
          } else {
            const tc = toolCalls.find((t) => t.id === item.id)
            if (!tc) return null
            return <ToolCallCard key={item.id} entry={tc} onFileClick={onFileClick} />
          }
        })}

        {isRunning && (
          <div style={{ padding: '4px 12px' }}>
            <span style={{ color: '#ffb74d', fontSize: 14, animation: 'pulse 1.5s infinite' }}>
              Agent is {agentState}...
            </span>
          </div>
        )}
      </div>

      {/* Input area */}
      <div
        style={{
          display: 'flex',
          gap: 8,
          padding: '8px 12px',
          borderTop: '1px solid #222',
          background: 'var(--fp-bg)',
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isRunning ? 'Agent is working...' : 'Type your message... (Enter to send)'}
          disabled={isRunning}
          style={{
            flex: 1,
            background: '#111',
            border: '1px solid #333',
            borderRadius: 8,
            color: '#e0e0e0',
            padding: '8px 12px',
            fontSize: 15,
            fontFamily: 'Consolas, Menlo, monospace',
            resize: 'none',
            minHeight: 36,
            maxHeight: 120,
            outline: 'none',
          }}
          rows={1}
        />
        <button
          onClick={handleSend}
          disabled={isRunning || !input.trim()}
          style={{
            background: input.trim() && !isRunning ? '#1a3a5a' : '#1a1a1a',
            border: '1px solid #2a3a4a',
            borderRadius: 8,
            color: input.trim() && !isRunning ? '#90caf9' : '#444',
            padding: '8px 16px',
            cursor: input.trim() && !isRunning ? 'pointer' : 'not-allowed',
            fontSize: 14,
            fontFamily: 'Consolas, Menlo, monospace',
          }}
        >
          Send
        </button>
      </div>
    </div>
  )
}
