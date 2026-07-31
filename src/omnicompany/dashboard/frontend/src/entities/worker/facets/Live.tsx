import React, { useEffect, useRef, useState } from 'react'
import { connectSSE, type IDEEvent } from '../../../api/ideClient'
import type { WorkerEntity } from '../resolver'

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: '#bbb', fontFamily: 'Consolas, Menlo, monospace', fontSize: 14 },
  hint: { padding: '6px 12px', color: '#666', fontSize: 14, borderBottom: '1px solid #222' },
  list: { flex: 1, overflow: 'auto', padding: '4px 8px' },
  row: { padding: '3px 4px', borderBottom: '1px solid #161616', display: 'flex', gap: 8 },
  ts: { color: '#555', flexShrink: 0, width: 70 },
  type: { color: '#90caf9', flexShrink: 0, width: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  pl: { color: '#bbb', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  empty: { padding: 16, color: '#666', fontStyle: 'italic' },
}

const MAX_KEEP = 100

export default function WorkerLiveFacet({ entity }: { entity: WorkerEntity }) {
  const leaf = entity.title
  const [events, setEvents] = useState<IDEEvent[]>([])
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    setEvents([])
    try {
      const src = connectSSE(null, (ev) => {
        const involves =
          ev.source === leaf ||
          (typeof ev.payload === 'object' && JSON.stringify(ev.payload).includes(leaf))
        if (!involves) return
        setEvents((prev) => {
          const next = [ev, ...prev]
          return next.length > MAX_KEEP ? next.slice(0, MAX_KEEP) : next
        })
      })
      sourceRef.current = src
    } catch (e) {
      console.error('worker.live SSE error', e)
    }
    return () => sourceRef.current?.close()
  }, [leaf])

  return (
    <div style={S.root}>
      <div style={S.hint}>实时事件 · 按 source / payload 含 "<span style={{ color: '#90caf9' }}>{leaf}</span>" 过滤</div>
      <div style={S.list}>
        {events.length === 0 ? (
          <div style={S.empty}>
            暂无与本 worker 相关的事件. 该 worker 被 agent 调用 / pipeline 跑过时, 事件会实时显示在此.
          </div>
        ) : events.map((ev) => (
          <div key={ev.id} style={S.row}>
            <span style={S.ts}>{ev.timestamp?.slice(11, 19)}</span>
            <span style={S.type}>{ev.event_type}</span>
            <span style={S.pl}>{JSON.stringify(ev.payload).slice(0, 200)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
