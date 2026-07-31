import React, { useEffect, useRef, useState } from 'react'
import { connectSSE, type IDEEvent } from '../../api/ideClient'

const S: Record<string, React.CSSProperties> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: '#bbb', fontFamily: 'Consolas, Menlo, monospace', fontSize: 14 },
  hint: { padding: '4px 12px', color: '#444', fontSize: 14, borderBottom: '1px solid #222' },
  list: { flex: 1, overflow: 'auto', padding: '4px 8px' },
  row: { padding: '2px 0', borderBottom: '1px solid #1a1a1a', display: 'flex', gap: 8 },
  ts: { color: '#555', flexShrink: 0, width: 70 },
  type: { color: '#90caf9', flexShrink: 0, width: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  src: { color: '#888', flexShrink: 0, width: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  pl: { color: '#bbb', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
}

const MAX_KEEP = 200

export default function EventStream() {
  const [events, setEvents] = useState<IDEEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    try {
      const src = connectSSE(
        null,
        (ev) => {
          setEvents((prev) => {
            const next = [ev, ...prev]
            return next.length > MAX_KEEP ? next.slice(0, MAX_KEEP) : next
          })
        },
        () => setError('SSE 连接断开 (会自动重连)'),
      )
      sourceRef.current = src
    } catch (e) {
      setError(String(e))
    }
    return () => sourceRef.current?.close()
  }, [])

  return (
    <div style={S.root}>
      <div style={S.hint}>实时事件流 · 来源 SQLiteBus (ide_events.db) {error && `· ${error}`}</div>
      <div style={S.list}>
        {events.length === 0 && (
          <div style={{ color: '#444', padding: 12, fontSize: 14, lineHeight: 1.6 }}>
            <div>等待事件...</div>
            <div style={{ marginTop: 8, color: '#666' }}>
              暂无事件. 启动一个 agent 会话即可看到 LLM 调用 / 工具调用 / 状态变化 实时流入.
            </div>
            <div style={{ marginTop: 6, color: '#666' }}>
              入口: 系统模块 → AGENT 会话 组的 <span style={{ color: '#90caf9' }}>+</span> 按钮.
            </div>
          </div>
        )}
        {events.map((ev) => (
          <div key={ev.id} style={S.row}>
            <span style={S.ts}>{ev.timestamp?.slice(11, 19)}</span>
            <span style={S.type}>{ev.event_type}</span>
            <span style={S.src}>{ev.source}</span>
            <span style={S.pl}>{JSON.stringify(ev.payload).slice(0, 120)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
