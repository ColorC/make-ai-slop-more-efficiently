/**
 * entities/studio_reader/DecisionPanel — 阅读视图主区的「决策过程」面。
 *
 * 数据源 = GET /api/v2/material-graph?project=X&status=adopted,proposed(决策库投影),
 * 与 review-canvas 的详情栏同一端点、同一 pickDecisions 口径(那份实现在 ReviewCanvas.tsx
 * 内, 属禁改文件, 故此处独立一份最小拆分)。adopted 列已拍板裁决(带 statement + anchor 原话
 * 摘录), proposed 高亮为待你裁决。裁决本体经对话/agent 落库, 此处只呈现。
 */
import { useEffect, useState, type CSSProperties } from 'react'

interface DecisionNode {
  id: string
  record_kind?: string
  label?: string
  statement?: string
  status?: string
  anchor?: { kind?: string; ref?: string; excerpt?: string }
}

function pickDecisions(graph: { nodes?: DecisionNode[] } | null, status: string): DecisionNode[] {
  if (!graph || !Array.isArray(graph.nodes)) return []
  return graph.nodes.filter((n) => n.record_kind === 'decision'
    && (status === 'adopted' ? (!n.status || n.status === 'adopted') : n.status === status)
    // 待你裁决只列真工作提议; 历史对话炼化的 observation(锚在 session:)是语料不是待办
    && !(status === 'proposed' && String(n.anchor?.ref || '').startsWith('session:')))
}

export default function DecisionPanel({ project }: { project: string }) {
  const [adopted, setAdopted] = useState<DecisionNode[] | null>(null)
  const [proposed, setProposed] = useState<DecisionNode[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setAdopted(null); setProposed([]); setError(null)
    fetch(`/api/v2/material-graph?project=${encodeURIComponent(project)}&status=adopted,proposed&include_deleted=false`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d) => {
        if (!alive) return
        setAdopted(pickDecisions(d, 'adopted'))
        setProposed(pickDecisions(d, 'proposed'))
      })
      .catch((e) => { if (alive) { setAdopted([]); setError(String(e instanceof Error ? e.message : e)) } })
    return () => { alive = false }
  }, [project])

  return (
    <div style={S.wrap} data-testid="studio-reader-decisions">
      {error && <div style={S.error} data-testid="studio-reader-decisions-error">载入决策出错: {error}</div>}

      <section>
        <div style={S.sectionHead}>
          <span style={{ ...S.dot, background: 'var(--fp-warn)' }} />
          待你裁决<span style={S.count}>{proposed.length}</span>
        </div>
        {proposed.length === 0 ? (
          // 错误样本(v2 一期): 无待裁决时显式"暂无", 不留白。
          <div style={S.dim} data-testid="studio-reader-proposed-empty">暂无待你裁决的提议。</div>
        ) : (
          <ul style={S.list} data-testid="studio-reader-proposed">
            {proposed.map((d) => (
              <li key={d.id} style={{ ...S.block, borderLeftColor: 'var(--fp-warn)' }} data-testid={`studio-reader-decision-${d.id}`}>
                <div style={S.blockHead}>
                  <span style={S.did}>{d.id}</span>
                  <span style={{ ...S.state, color: 'var(--fp-warn)', borderColor: 'color-mix(in srgb, var(--fp-warn) 40%, transparent)' }}>提议中</span>
                </div>
                <div style={{ ...S.stmt, color: 'var(--fp-warn)' }}>{d.statement || d.label || d.id}</div>
                {d.anchor?.excerpt && <div style={S.quote}>“{d.anchor.excerpt}”</div>}
                {d.anchor?.ref && <div style={S.ref}>{d.anchor.ref}</div>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div style={S.sectionHead}>
          <span style={{ ...S.dot, background: 'var(--fp-ok)' }} />
          已采纳{adopted ? <span style={S.count}>{adopted.length}</span> : null}
        </div>
        {adopted === null ? (
          <div style={S.dim}>载入中…</div>
        ) : adopted.length === 0 ? (
          <div style={S.dim} data-testid="studio-reader-adopted-empty">该项目决策库暂无已拍板裁决。</div>
        ) : (
          <ul style={S.list} data-testid="studio-reader-adopted">
            {adopted.map((d) => (
              <li key={d.id} style={S.block} data-testid={`studio-reader-decision-${d.id}`}>
                <div style={S.blockHead}>
                  <span style={S.did}>{d.id}</span>
                  <span style={{ ...S.state, color: 'var(--fp-ok)', borderColor: 'color-mix(in srgb, var(--fp-ok) 40%, transparent)' }}>已采纳</span>
                </div>
                <div style={S.stmt}>{d.statement || d.label || d.id}</div>
                {d.anchor?.excerpt && <div style={S.quote}>“{d.anchor.excerpt}”</div>}
                {d.anchor?.ref && <div style={S.ref}>{d.anchor.ref}</div>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  wrap: { maxWidth: 920, margin: '0 auto', padding: '30px 40px 90px', fontSize: 14, color: 'var(--fp-text)' },
  error: { color: 'var(--fp-err)', fontSize: 13, marginBottom: 16 },
  sectionHead: {
    display: 'flex', alignItems: 'center', gap: 7, margin: '18px 0 12px',
    fontSize: 12, color: 'var(--fp-text-3)', letterSpacing: '0.4px',
  },
  dot: { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  count: { marginLeft: 6, fontVariantNumeric: 'tabular-nums', color: 'var(--fp-text-3)' },
  list: { margin: 0, padding: 0, listStyle: 'none' },
  block: {
    background: 'var(--fp-solid)', border: '1px solid var(--fp-border-subtle)',
    borderLeft: '3px solid var(--fp-accent-2)', borderRadius: 13,
    padding: '15px 17px', marginBottom: 13,
  },
  blockHead: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 },
  did: { fontSize: 12, color: 'var(--fp-text-3)', fontFamily: 'var(--fp-font-mono)', fontVariantNumeric: 'tabular-nums' },
  state: { fontSize: 12, padding: '2px 9px', borderRadius: 6, border: '1px solid var(--fp-border-subtle)', background: 'var(--fp-surface)' },
  stmt: { fontSize: 14, color: 'var(--fp-text)', lineHeight: 1.6 },
  quote: {
    fontSize: 13, color: 'var(--fp-text-2)', lineHeight: 1.6, fontStyle: 'italic',
    borderLeft: '2px solid color-mix(in srgb, var(--fp-accent-2) 55%, transparent)',
    padding: '6px 12px', margin: '11px 0 0',
    background: 'color-mix(in srgb, var(--fp-accent-2) 6%, transparent)', borderRadius: '0 7px 7px 0',
    whiteSpace: 'pre-wrap',
  },
  ref: { fontSize: 11, color: 'var(--fp-text-3)', fontFamily: 'var(--fp-font-mono)', marginTop: 6, wordBreak: 'break-all' },
  dim: { color: 'var(--fp-text-3)', fontSize: 13 },
}
