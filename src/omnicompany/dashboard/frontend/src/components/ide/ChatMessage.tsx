import React, { useState } from 'react'
import type { ChatMessage as ChatMessageType } from '../../stores/ideStore'

const roleStyles: Record<string, React.CSSProperties> = {
  user: {
    alignSelf: 'flex-end',
    background: '#1a2a3a',
    borderRadius: '12px 12px 4px 12px',
    maxWidth: '80%',
  },
  assistant: {
    alignSelf: 'flex-start',
    background: '#1a1a2a',
    borderRadius: '12px 12px 12px 4px',
    maxWidth: '85%',
  },
  thinking: {
    alignSelf: 'flex-start',
    background: '#1a1a1a',
    borderRadius: 8,
    maxWidth: '85%',
    opacity: 0.7,
    fontStyle: 'italic' as const,
  },
  system: {
    alignSelf: 'center',
    background: '#222',
    borderRadius: 6,
    maxWidth: '90%',
    fontSize: 14,
    color: '#888',
  },
}

const roleLabels: Record<string, string> = {
  user: 'You',
  assistant: 'Agent',
  thinking: 'Thinking',
  system: 'System',
}

interface Props {
  message: ChatMessageType
}

export default function ChatMessage({ message }: Props) {
  const [expanded, setExpanded] = useState(message.role !== 'thinking')
  const style = roleStyles[message.role] || roleStyles.system

  return (
    <div style={{ display: 'flex', flexDirection: 'column', padding: '4px 12px' }}>
      <div style={{ ...style, padding: '8px 12px' }}>
        <div style={{ fontSize: 14, color: '#666', marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
          <span>{roleLabels[message.role] || message.role}</span>
          <span>{new Date(message.timestamp).toLocaleTimeString()}</span>
        </div>
        {message.role === 'thinking' && !expanded ? (
          <div
            onClick={() => setExpanded(true)}
            style={{ cursor: 'pointer', color: '#90caf9', fontSize: 14 }}
          >
            Show reasoning...
          </div>
        ) : (
          <div style={{ fontSize: 15, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {message.content}
          </div>
        )}
        {message.role === 'thinking' && expanded && (
          <div
            onClick={() => setExpanded(false)}
            style={{ cursor: 'pointer', color: '#666', fontSize: 14, marginTop: 4 }}
          >
            Hide reasoning
          </div>
        )}
      </div>
    </div>
  )
}
