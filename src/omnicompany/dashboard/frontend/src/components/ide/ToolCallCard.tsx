/**
 * ToolCallCard — 工具调用可视化卡片
 *
 * 借鉴 OpenHands 的 ExpandableMessage 组件。
 * 折叠态显示工具名+状态，展开态显示完整 args 和 result。
 */

import React, { useState } from 'react'
import type { ToolCallEntry } from '../../stores/ideStore'

interface Props {
  entry: ToolCallEntry
  onFileClick?: (path: string) => void
}

const statusIndicator: Record<string, { icon: string; color: string }> = {
  running: { icon: '\u25CF', color: '#ffb74d' },
  done: { icon: '\u2713', color: '#66bb6a' },
  error: { icon: '\u2717', color: '#ef5350' },
}

function formatDuration(ms?: number): string {
  if (!ms) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export default function ToolCallCard({ entry, onFileClick }: Props) {
  const [expanded, setExpanded] = useState(false)
  const si = statusIndicator[entry.status] || statusIndicator.running

  // Detect file path from args
  const filePath = entry.args?.path || entry.args?.file_path

  return (
    <div
      style={{
        margin: '4px 12px',
        background: '#161622',
        border: '1px solid #2a2a3a',
        borderRadius: 8,
        overflow: 'hidden',
      }}
    >
      {/* Header — always visible */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 10px',
          cursor: 'pointer',
          fontSize: 14,
          fontFamily: 'Consolas, Menlo, monospace',
        }}
      >
        <span style={{ color: '#666', fontSize: 14 }}>{expanded ? '\u25BC' : '\u25B6'}</span>
        <span style={{ color: si.color, fontSize: 15 }}>
          {entry.status === 'running' ? (
            <span style={{ animation: 'pulse 1s infinite' }}>{si.icon}</span>
          ) : (
            si.icon
          )}
        </span>
        <span style={{ color: '#90caf9', fontWeight: 'bold' }}>{entry.tool}</span>
        <span style={{ color: '#666', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {entry.argsSummary}
        </span>
        {entry.durationMs && (
          <span style={{ color: '#555', fontSize: 14 }}>{formatDuration(entry.durationMs)}</span>
        )}
      </div>

      {/* Body — expanded */}
      {expanded && (
        <div style={{ borderTop: '1px solid #2a2a3a', padding: 10 }}>
          {/* Args */}
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 14, color: '#666', marginBottom: 4 }}>Arguments</div>
            <pre
              style={{
                background: '#111',
                padding: 8,
                borderRadius: 4,
                fontSize: 14,
                color: '#ccc',
                overflow: 'auto',
                maxHeight: 200,
                margin: 0,
              }}
            >
              {JSON.stringify(entry.args, null, 2)}
            </pre>
          </div>

          {/* File link */}
          {filePath && onFileClick && (
            <div style={{ marginBottom: 8 }}>
              <span
                onClick={() => onFileClick(filePath)}
                style={{ color: '#90caf9', cursor: 'pointer', fontSize: 14, textDecoration: 'underline' }}
              >
                Open {filePath}
              </span>
            </div>
          )}

          {/* Result */}
          {entry.result !== undefined && (
            <div>
              <div style={{ fontSize: 14, color: '#666', marginBottom: 4 }}>
                Result {entry.status === 'error' && '(Error)'}
              </div>
              <pre
                style={{
                  background: entry.status === 'error' ? '#1a0a0a' : '#0a1a0a',
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 14,
                  color: entry.status === 'error' ? '#ef9a9a' : '#a5d6a7',
                  overflow: 'auto',
                  maxHeight: 300,
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {entry.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
