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
        borderBottom: '1px solid var(--fp-border-subtle)',
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
          style={{ color: 'var(--fp-text-3)', cursor: 'pointer' }}
          title={activeTraceId}
          onClick={() => navigator.clipboard.writeText(activeTraceId)}
        >
          {activeTraceId.slice(0, 10)}...
        </span>
      )}

      {/* Turn counter */}
      {currentTurn > 0 && <span style={{ color: 'var(--fp-text-3)' }}>Turn {currentTurn}</span>}

      {/* Token usage */}
      {(totalPromptTokens > 0 || totalCompletionTokens > 0) && (
        <span style={{ color: 'var(--fp-text-3)' }}>
          {((totalPromptTokens + totalCompletionTokens) / 1000).toFixed(1)}k tokens
        </span>
      )}

      <div style={{ flex: 1 }} />

      {/* Action buttons */}
      {isRunning && (
        <button
          onClick={cancelAgent}
          style={{
            background: 'color-mix(in srgb, var(--fp-err) 14%, transparent)',
            border: '1px solid color-mix(in srgb, var(--fp-err) 45%, transparent)',
            borderRadius: 'var(--fp-bp-r1)',
            color: 'var(--fp-err)',
            padding: '2px 8px',
            cursor: 'pointer',
            fontSize: 'var(--fp-fs-4)',
            fontFamily: 'var(--fp-font-mono)',
            textTransform: 'uppercase',
            letterSpacing: '.09em',
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
            background: showTimeline ? 'color-mix(in srgb, var(--fp-ok) 14%, transparent)' : 'transparent',
            border: '1px solid color-mix(in srgb, var(--fp-ok) 45%, transparent)',
            borderRadius: 'var(--fp-bp-r1)',
            color: showTimeline ? 'var(--fp-ok)' : 'var(--fp-text-3)',
            padding: '2px 8px',
            cursor: 'pointer',
            fontSize: 'var(--fp-fs-4)',
            fontFamily: 'var(--fp-font-mono)',
            textTransform: 'uppercase',
            letterSpacing: '.09em',
          }}
        >
          Events
        </button>
      )}
      <button
        onClick={newSession}
        style={{
          background: 'var(--fp-bp-paper-2)',
          border: '1px solid var(--fp-border)',
          borderRadius: 'var(--fp-bp-r1)',
          color: 'var(--fp-link)',
          padding: '2px 8px',
          cursor: 'pointer',
          fontSize: 'var(--fp-fs-4)',
          fontFamily: 'var(--fp-font-mono)',
          textTransform: 'uppercase',
          letterSpacing: '.09em',
        }}
      >
        New
      </button>
    </div>
  )
}
