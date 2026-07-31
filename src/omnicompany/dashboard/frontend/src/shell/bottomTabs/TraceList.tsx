import React, { useEffect, useMemo, useState } from 'react'
import { api, type TraceListItem } from '../../api/client'
import { usePanels } from '../../stores/panelsStore'
import { statusColorOf } from '../tokens'

// 运行历史面 = TraceList 升级改造(决策本体阶段四,界面克制:不另起并列面板)。
// 两档: 运行(trace 聚合) | 留痕(ledger 事件,含 consumed_decisions 消费与 deviation 偏离)。

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: '#bbb', fontFamily: 'Consolas, Menlo, monospace', fontSize: 14 },
  bar: { display: 'flex', gap: 6, padding: '4px 8px', borderBottom: '1px solid #222', alignItems: 'center' },
  input: { flex: 1, background: '#111', border: '1px solid #333', borderRadius: 4, color: '#e0e0e0', padding: '3px 8px', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace' },
  meta: { color: '#666', fontSize: 14 },
  list: { flex: 1, overflow: 'auto' },
  seg: (active: boolean): React.CSSProperties => ({
    padding: '2px 10px', borderRadius: 4, cursor: 'pointer', fontSize: 13,
    background: active ? '#1a2a3a' : 'transparent',
    color: active ? '#8fc7ff' : '#777',
    border: active ? '1px solid #2a4a6a' : '1px solid #2a2a2a',
  }),
  row: (active: boolean): React.CSSProperties => ({
    display: 'flex', gap: 8, padding: '3px 8px', cursor: 'pointer',
    background: active ? '#1a2a3a' : 'transparent',
    borderBottom: '1px solid #161616',
  }),
  ts: { color: '#555', flexShrink: 0, width: 100 },
  src: { color: '#888', flexShrink: 0, width: 110, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  desc: { flex: 1, color: '#bbb', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  status: (s: string): React.CSSProperties => ({
    flexShrink: 0, width: 60, fontSize: 14,
    color: statusColorOf(s),
  }),
  evct: { color: '#555', flexShrink: 0, width: 60, fontSize: 14, textAlign: 'right' as const },
  badge: (color: string): React.CSSProperties => ({
    flexShrink: 0, fontSize: 12, padding: '0 6px', borderRadius: 8,
    border: `1px solid ${color}`, color, alignSelf: 'center',
  }),
}

function fmtTs(s: string | null): string {
  if (!s) return ''
  try { return new Date(s).toLocaleString('zh', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
  catch { return s.slice(0, 16) }
}

interface LedgerItem {
  id: string
  time: string
  type: string
  agent: string
  activity: string
  consumed_decisions: string[]
  deviation: { kind?: string; note?: string; refs?: string[] } | null
  verdict: string
}

function LedgerRows({ deviationsOnly }: { deviationsOnly: boolean }) {
  const [items, setItems] = useState<LedgerItem[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`/api/v2/ledger-recent?limit=200&deviations_only=${deviationsOnly}`)
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d) => { if (!cancelled) setItems(d.items || []) })
      .catch(() => { if (!cancelled) setItems([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [deviationsOnly])
  if (!loading && items.length === 0) {
    return <div style={{ padding: 12, color: '#444' }}>无留痕事件(管线跑过才有;消费留痕=consumed_decisions,偏离=omni ledger deviate)</div>
  }
  return (
    <>
      {items.map((it) => (
        <div key={it.id} style={S.row(false)} title={`${it.id}\n${(it.consumed_decisions || []).join(', ')}`}>
          <span style={S.ts}>{fmtTs(it.time)}</span>
          <span style={S.src}>{it.agent || it.type}</span>
          <span style={S.desc}>{it.activity || it.type}</span>
          {it.consumed_decisions.length > 0 && (
            <span style={S.badge('#5aa9e6')}>消费×{it.consumed_decisions.length}</span>
          )}
          {it.deviation && (
            <span style={S.badge('#e6a23c')}>偏离:{it.deviation.kind || '?'}</span>
          )}
          <span style={S.status(it.verdict === 'verified' ? 'finished' : 'running')}>{it.verdict}</span>
        </div>
      ))}
    </>
  )
}

export default function TraceList() {
  const [mode, setMode] = useState<'runs' | 'ledger' | 'deviations'>('runs')
  const [items, setItems] = useState<TraceListItem[]>([])
  const [total, setTotal] = useState(0)
  const [filter, setFilter] = useState('')
  const [domain, setDomain] = useState('')
  const [loading, setLoading] = useState(true)
  const openTab = usePanels((s) => s.openTab)
  const activeId = usePanels((s) => s.activeId)

  useEffect(() => {
    if (mode !== 'runs') return
    let cancelled = false
    setLoading(true)
    api.traceList({ limit: 200, q: filter || undefined, source: domain || undefined })
      .then((d) => {
        if (cancelled) return
        setItems(d.items); setTotal(d.total)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [filter, domain, mode])

  const domains = useMemo(() => Array.from(new Set(items.map((t) => t.domain))).sort(), [items])

  return (
    <div style={S.root}>
      <div style={S.bar}>
        <span style={S.seg(mode === 'runs')} onClick={() => setMode('runs')}>运行</span>
        <span style={S.seg(mode === 'ledger')} onClick={() => setMode('ledger')}>留痕</span>
        <span style={S.seg(mode === 'deviations')} onClick={() => setMode('deviations')}>偏离</span>
        {mode === 'runs' && (
          <>
            <input style={S.input} placeholder="过滤 task_desc..." value={filter} onChange={(e) => setFilter(e.target.value)} />
            <select
              style={{ ...S.input, flex: 0, width: 120 }}
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
            >
              <option value="">全部域</option>
              {domains.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <span style={S.meta}>{loading ? '加载中...' : `${items.length}/${total}`}</span>
          </>
        )}
      </div>
      <div style={S.list}>
        {mode !== 'runs' && <LedgerRows deviationsOnly={mode === 'deviations'} />}
        {mode === 'runs' && items.length === 0 && !loading && <div style={{ padding: 12, color: '#444' }}>无 trace</div>}
        {mode === 'runs' && items.map((t) => {
          const tabId = `trace:${t.trace_id}`
          return (
            <div
              key={t.trace_id}
              style={S.row(activeId === tabId)}
              onClick={() => openTab({ type: 'trace', id: t.trace_id }, t.task_desc || t.trace_id.slice(0, 24))}
              title={t.trace_id}
            >
              <span style={S.ts}>{fmtTs(t.started_at)}</span>
              <span style={S.src}>{t.domain}</span>
              <span style={S.desc}>{t.task_desc || t.trace_id}</span>
              <span style={S.evct}>{t.event_count}ev</span>
              <span style={S.status(t.status)}>{t.status}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
