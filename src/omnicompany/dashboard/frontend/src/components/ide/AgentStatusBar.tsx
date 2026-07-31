import React from 'react'
import { useIDEStore, type AgentState } from '../../stores/ideStore'

const stateColors: Record<AgentState, string> = {
  idle: '#666',
  running: '#ffb74d',
  thinking: '#90caf9',
  finished: '#66bb6a',
  error: '#ef5350',
  cancelled: '#888',
}

const stateLabels: Record<AgentState, string> = {
  idle: 'Idle',
  running: 'Running',
  thinking: 'Thinking',
  finished: 'Finished',
  error: 'Error',
  cancelled: 'Cancelled',
}

interface StatusBarProps {
  showTimeline?: boolean
  onToggleTimeline?: () => void
}

export default function AgentStatusBar({ showTimeline, onToggleTimeline }: StatusBarProps) {
  const {
    agentState,
    activeTraceId,
    currentTurn,
    totalPromptTokens,
    totalCompletionTokens,
    cancelAgent,
    newSession,
  } = useIDEStore()

  const color = stateColors[agentState]
  const isRunning = agentState === 'running' || agentState === 'thinking'

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '4px 12px',
        background: 'var(--fp-bg)',
        borderBottom: '1px solid #222',
        fontSize: 14,
        fontFamily: 'Consolas, Menlo, monospace',
        flexShrink: 0,
      }}
    >
      {/* State indicator */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ color, fontSize: 14 }}>{'\u25CF'}</span>
        <span style={{ color }}>{stateLabels[agentState]}</span>
      </div>

      {/* Trace ID */}
      {activeTraceId && (
        <span
          style={{ color: '#555', cursor: 'pointer' }}
          title={activeTraceId}
          onClick={() => navigator.clipboard.writeText(activeTraceId)}
        >
          {activeTraceId.slice(0, 10)}...
        </span>
      )}

      {/* Turn counter */}
      {currentTurn > 0 && <span style={{ color: '#555' }}>Turn {currentTurn}</span>}

      {/* Token usage */}
      {(totalPromptTokens > 0 || totalCompletionTokens > 0) && (
        <span style={{ color: '#555' }}>
          {((totalPromptTokens + totalCompletionTokens) / 1000).toFixed(1)}k tokens
        </span>
      )}

      <div style={{ flex: 1 }} />

      {/* Action buttons */}
      {isRunning && (
        <button
          onClick={cancelAgent}
          style={{
            background: '#3a1a1a',
            border: '1px solid #5a2a2a',
            borderRadius: 4,
            color: '#ef5350',
            padding: '2px 8px',
            cursor: 'pointer',
            fontSize: 14,
            fontFamily: 'inherit',
          }}
        >
          Stop
        </button>
      )}
      {onToggleTimeline && (
        <button
          onClick={onToggleTimeline}
          title="Toggle event timeline"
          style={{
            background: showTimeline ? '#1a2a1a' : 'transparent',
            border: '1px solid #2a3a2a',
            borderRadius: 4,
            color: showTimeline ? '#66bb6a' : '#555',
            padding: '2px 8px',
            cursor: 'pointer',
            fontSize: 14,
            fontFamily: 'inherit',
          }}
        >
          Events
        </button>
      )}
      <button
        onClick={newSession}
        style={{
          background: '#1a2a3a',
          border: '1px solid #2a3a4a',
          borderRadius: 4,
          color: '#90caf9',
          padding: '2px 8px',
          cursor: 'pointer',
          fontSize: 14,
          fontFamily: 'inherit',
        }}
      >
        New
      </button>
    </div>
  )
}
