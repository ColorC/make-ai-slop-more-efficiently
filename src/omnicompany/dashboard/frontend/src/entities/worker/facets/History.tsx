import React, { useEffect, useState } from 'react'
import { usePanels } from '../../../stores/panelsStore'
import type { WorkerEntity } from '../resolver'

interface TraceItem {
  trace_id: string
  started_at: string
  ended_at: string
  event_count: number
  domain: string
}

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: '#bbb', fontFamily: 'Consolas, Menlo, monospace', fontSize: 14 },
  hint: { padding: '6px 12px', color: '#666', fontSize: 14, borderBottom: '1px solid #222' },
  list: { flex: 1, overflow: 'auto' },
  row: { display: 'flex', gap: 8, padding: '4px 12px', cursor: 'pointer', borderBottom: '1px solid #161616' },
  ts: { color: '#555', flexShrink: 0, width: 100 },
  domain: { color: '#888', flexShrink: 0, width: 110 },
  tid: { flex: 1, color: '#bbb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  evct: { color: '#555', flexShrink: 0, width: 60, fontSize: 14, textAlign: 'right' as const },
  empty: { padding: 16, color: '#666', fontStyle: 'italic' },
}

function fmtTs(s: string | null | undefined): string {
  if (!s) return ''
  try { return new Date(s).toLocaleString('zh', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
  catch { return s.slice(0, 16) }
}

export default function WorkerHistoryFacet({ entity }: { entity: WorkerEntity }) {
  const [items, setItems] = useState<TraceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)

  useEffect(() => {
    setLoading(true); setError(null); setItems([])
    fetch(`/api/workers/${entity.id}/traces?limit=50`)
      .then((r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json() })
      .then((d) => setItems(d.items || []))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [entity.id])

  return (
    <div style={S.root}>
      <div style={S.hint}>历史 trace · 后端按 source = "<span style={{ color: '#90caf9' }}>{entity.title}</span>" 或 payload 含此名匹配</div>
      <div style={S.list}>
        {loading && <div style={S.empty}>加载中...</div>}
        {error && <div style={{ ...S.empty, color: '#ef5350' }}>错误: {error}</div>}
        {!loading && !error && items.length === 0 && (
          <div style={S.empty}>无历史 trace. 该 worker 被实际跑过后, 这里会列出相关 trace.</div>
        )}
        {!loading && items.map((it) => (
          <div key={it.trace_id} style={S.row} onClick={() => openTab({ type: 'trace', id: it.trace_id }, it.trace_id.slice(0, 24))} title={it.trace_id}>
            <span style={S.ts}>{fmtTs(it.started_at)}</span>
            <span style={S.domain}>{it.domain}</span>
            <span style={S.tid}>{it.trace_id}</span>
            <span style={S.evct}>{it.event_count}ev</span>
          </div>
        ))}
      </div>
    </div>
  )
}
