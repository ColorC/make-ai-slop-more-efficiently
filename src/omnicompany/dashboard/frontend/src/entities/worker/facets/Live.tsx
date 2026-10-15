import React, { useEffect, useRef, useState } from 'react'
import { connectSSE, type IDEEvent } from '../../../api/ideClient'
import { usePanelVisible } from '../../../shell/panelRuntime'
import type { WorkerEntity } from '../resolver'

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: 'var(--fp-text)', fontFamily: 'Consolas, Menlo, monospace', fontSize: 14 },
  hint: { padding: '6px 12px', color: 'var(--fp-text-2)', fontSize: 14, borderBottom: '1px solid var(--fp-border-subtle)' },
  list: { flex: 1, overflow: 'auto', padding: '4px 8px' },
  row: { padding: '3px 4px', borderBottom: '1px solid var(--fp-border-subtle)', display: 'flex', gap: 8 },
  ts: { color: 'var(--fp-text-3)', flexShrink: 0, width: 70 },
  type: { color: 'var(--fp-link)', flexShrink: 0, width: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  pl: { color: 'var(--fp-text)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  empty: { padding: 16, color: 'var(--fp-text-2)', fontStyle: 'italic' },
}

const MAX_KEEP = 100

export default function WorkerLiveFacet({ entity }: { entity: WorkerEntity }) {
  const leaf = entity.title
  const [events, setEvents] = useState<IDEEvent[]>([])
  const sourceRef = useRef<EventSource | null>(null)
  const panelVisible = usePanelVisible()

  // 页签常驻(2026-08-07): 实时事件流只在面板可见时连着 —— 没人看的时候不占 SSE 长连接额度
  // (HTTP/1.1 每域只有 6 条)。切回来重新订阅, 从那一刻起的事件继续流。
  useEffect(() => {
    if (!panelVisible) return undefined
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
  }, [leaf, panelVisible])

  return (
    <div style={S.root}>
      <div style={S.hint}>实时事件 · 按 source / payload 含 "<span style={{ color: 'var(--fp-link)' }}>{leaf}</span>" 过滤</div>
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
