import React, { useEffect, useRef, useState } from 'react'
import { entitiesApi, type EntitySearchResult } from '../../api/entitiesClient'
import { registry } from '../../entities/registry'
import type { EntityType } from '../../entities/types'
import { usePanels } from '../../stores/panelsStore'
import { openProps } from '../../utils/middleClick'

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--fp-bg)', color: 'var(--fp-text)', fontFamily: 'Consolas, Menlo, monospace', fontSize: 14 },
  bar: { display: 'flex', gap: 6, padding: '4px 8px', borderBottom: '1px solid var(--fp-border-subtle)', alignItems: 'center' },
  input: { flex: 1, background: 'var(--fp-bp-solid)', border: '1px solid var(--fp-border-subtle)', borderRadius: 3, color: 'var(--fp-text)', padding: '3px 8px', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace' },
  meta: { color: 'var(--fp-text-2)', fontSize: 14 },
  list: { flex: 1, overflow: 'auto' },
  hit: { padding: '6px 12px', cursor: 'pointer', borderBottom: '1px solid var(--fp-border-subtle)' },
  title: { color: 'var(--fp-link)', fontSize: 14, marginBottom: 2, display: 'flex', gap: 8, alignItems: 'baseline' },
  kind: { color: 'var(--fp-warn)', fontSize: 14, minWidth: 86 },
  uri: { color: 'var(--fp-text-2)', fontSize: 14 },
  snippet: { color: 'var(--fp-text-2)', fontSize: 14 },
  empty: { padding: 16, color: 'var(--fp-text-2)', fontStyle: 'italic' as const },
}

function highlight(snippet: string, q: string): React.ReactNode {
  if (!q.trim()) return snippet
  const ql = q.toLowerCase()
  const sl = snippet.toLowerCase()
  const idx = sl.indexOf(ql)
  if (idx < 0) return snippet
  return (
    <>
      {snippet.slice(0, idx)}
      <mark style={{ background: 'var(--fp-bp-paper-2)', color: '#fff' }}>{snippet.slice(idx, idx + q.length)}</mark>
      {snippet.slice(idx + q.length)}
    </>
  )
}

export default function SearchTab() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<EntitySearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)
  const debounceRef = useRef<number | null>(null)

  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current)
    if (!q.trim()) { setHits([]); setLoading(false); return }
    setLoading(true); setError(null)
    debounceRef.current = window.setTimeout(() => {
      entitiesApi.search(q, 50).then(setHits).catch((e) => setError(String(e))).finally(() => setLoading(false))
    }, 200)
    return () => { if (debounceRef.current) window.clearTimeout(debounceRef.current) }
  }, [q])

  const openHit = (h: EntitySearchResult, bg = false) => {
    const ref = h.open_ref || {}
    if (ref.url) {
      if (bg) { try { window.open(ref.url, '_blank', 'noopener') } catch { /* */ } }
      else window.location.href = ref.url
      return
    }
    if (ref.type && ref.id && registry.has(ref.type as EntityType)) {
      ;(bg ? openTabBg : openTab)({ type: ref.type as EntityType, id: ref.id }, h.title, ref.facet)
      return
    }
    navigator.clipboard?.writeText(h.uri).catch(() => {})
  }

  return (
    <div style={S.root}>
      <div style={S.bar}>
        <input
          style={S.input}
          autoFocus
          placeholder="Ultra 搜索: plan / file / material / worker / session / command..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span style={S.meta}>{loading ? '搜索中...' : `${hits.length} 命中`}</span>
      </div>
      <div style={S.list}>
        {error && <div style={{ ...S.empty, color: 'var(--fp-err)' }}>{error}</div>}
        {!loading && !error && q.trim() && hits.length === 0 && <div style={S.empty}>无匹配</div>}
        {!q.trim() && <div style={S.empty}>输入关键字开始搜索</div>}
        {hits.map((h) => (
          <div key={h.uri} style={S.hit} {...openProps(() => openHit(h), () => openHit(h, true))} title={h.uri}>
            <div style={S.title}>
              <span style={S.kind}>{h.display}</span>
              <span>{h.title}</span>
              <span style={S.uri}>{h.source}</span>
            </div>
            <div style={S.snippet}>{highlight(h.snippet || h.id, q)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
