import React, { useEffect, useMemo, useState } from 'react'
import { bossSightApi, type BossSightBriefing } from '../../api/bossSightClient'
import { usePanels } from '../../stores/panelsStore'
import { openProps } from '../../utils/middleClick'

const S: Record<string, React.CSSProperties> = {
  root: {
    height: '100%',
    overflow: 'auto',
    background: 'var(--fp-bg)',
    color: '#d8dee9',
    fontFamily: '"Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif',
    padding: 20,
    boxSizing: 'border-box',
  },
  top: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 18,
    marginBottom: 18,
    flexWrap: 'wrap',
  },
  headline: { fontSize: 22, fontWeight: 700, color: 'var(--fp-text)', marginBottom: 8, letterSpacing: 0 },
  sub: { fontSize: 15, color: '#a7b0bd', lineHeight: 1.6, maxWidth: 760 },
  timestamp: { fontSize: 14, color: 'var(--fp-text-3)', whiteSpace: 'nowrap' },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
    gap: 10,
    marginBottom: 16,
  },
  tile: { border: '1px solid #242b33', background: 'var(--fp-solid)', borderRadius: 6, padding: 14, minHeight: 74 },
  tileLabel: { fontSize: 14, color: '#9aa6b2', marginBottom: 8 },
  tileValue: { fontSize: 26, color: 'var(--fp-text)', fontWeight: 700, letterSpacing: 0 },
  sectionGrid: {
    display: 'grid',
    gridTemplateColumns: 'minmax(260px, 1.1fr) minmax(260px, 1fr)',
    gap: 14,
  },
  section: { borderTop: '1px solid #242b33', paddingTop: 12, minWidth: 0 },
  sectionTitle: { fontSize: 15, color: '#90caf9', fontWeight: 700, marginBottom: 8 },
  row: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    padding: '9px 0',
    borderBottom: '1px solid #171d24',
    minWidth: 0,
  },
  rowMain: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  muted: { color: 'var(--fp-text-3)', fontSize: 14 },
  link: { color: '#90caf9', textDecoration: 'none' },
  pill: {
    flexShrink: 0,
    border: '1px solid var(--fp-border)',
    borderRadius: 999,
    padding: '2px 8px',
    fontSize: 14,
    color: '#c9d1d9',
  },
  error: { color: '#ff8a80', fontSize: 15, lineHeight: 1.6 },
}

function relFromIso(ts: string | null | undefined): string {
  if (!ts) return ''
  const t = new Date(ts).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Math.max(0, (Date.now() - t) / 1000)
  if (diff < 60) return `${Math.round(diff)}秒前`
  if (diff < 3600) return `${Math.round(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.round(diff / 3600)}小时前`
  return `${Math.round(diff / 86400)}天前`
}

function severityColor(severity: string): string {
  if (severity === 'critical') return 'var(--fp-err)'
  if (severity === 'attention') return 'var(--fp-warn)'
  return 'var(--fp-ok)'
}

function priorityLabel(priority: string): string {
  if (priority === 'critical') return '高'
  if (priority === 'attention') return '关注'
  if (priority === 'info') return '信息'
  return '平稳'
}

function SummaryTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div style={S.tile}>
      <div style={S.tileLabel}>{label}</div>
      <div style={S.tileValue}>{value}</div>
    </div>
  )
}

function actionHref(target?: string | null): string | null {
  if (!target) return null
  if (target === 'reviewstage' || target === 'review') return '/?open_type=review_queue&open_id=main&open_title=Review%20Queue'
  if (target === 'subagents') return '/?open_type=controller&open_id=main&open_title=BOSS%20SIGHT'
  return null
}

function reviewHref(materialId: string): string {
  const q = new URLSearchParams()
  q.set('open_type', 'review_queue')
  q.set('open_id', 'main')
  q.set('open_title', 'Review Queue')
  q.set('open_facet', materialId)
  q.set('source_type', 'controller')
  q.set('source_id', 'main')
  q.set('source_title', 'BOSS SIGHT')
  return `/?${q.toString()}`
}

export default function BriefingTab() {
  const [data, setData] = useState<BossSightBriefing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)

  useEffect(() => {
    let alive = true
    setError(null)
    bossSightApi.briefing()
      .then((next) => { if (alive) setData(next) })
      .catch((e) => { if (alive) setError(String(e?.message || e)) })
    return () => { alive = false }
  }, [])

  const updated = useMemo(() => {
    if (!data?.generated_at) return ''
    const d = new Date(data.generated_at)
    return Number.isNaN(d.getTime()) ? data.generated_at : d.toLocaleString()
  }, [data?.generated_at])

  if (error) {
    return (
      <div style={S.root} data-testid="boss-briefing-tab">
        <div style={S.error}>总报加载失败: {error}</div>
      </div>
    )
  }
  if (!data) {
    return <div style={S.root} data-testid="boss-briefing-tab"><span style={S.muted}>加载中...</span></div>
  }

  return (
    <div style={S.root} data-testid="boss-briefing-tab">
      <div style={S.top}>
        <div>
          <div style={{ ...S.headline, color: severityColor(data.severity) }} data-testid="briefing-headline">
            {data.headline}
          </div>
          <div style={S.sub}>{data.secretary.body}</div>
        </div>
        <div style={S.timestamp}>{updated}</div>
      </div>

      <div style={S.grid}>
        <SummaryTile label="近24h更新计划" value={data.summary.plans_active} />
        <SummaryTile label="待审材料" value={data.summary.review_pending} />
        <SummaryTile label="必验收阻断" value={data.summary.mandatory_unaccepted} />
        <SummaryTile label="运行线程" value={data.summary.subagents_running} />
      </div>

      {Array.isArray((data as any).plans?.active) && (data as any).plans.active.length > 0 && (
        <div style={{ ...S.section, marginBottom: 14 }} data-testid="briefing-active-plans">
          <div style={S.sectionTitle}>计划 · 按更新时间(高亮 24h 内更新,点击打开)</div>
          {((data as any).plans.active as any[]).map((p) => {
            const fresh = Boolean(p.fresh_24h)
            return (
              <div key={p.plan_id} style={{ ...S.row, ...(fresh ? { background: '#0d2116' } : {}) }} data-fresh={fresh ? '1' : '0'}>
                <button
                  type="button"
                  style={{ ...S.link, ...S.rowMain, color: fresh ? '#8ee6a8' : '#90caf9', background: 'transparent', border: 0, cursor: 'pointer', textAlign: 'left', padding: 0 }}
                  data-testid="briefing-plan-item"
                  {...openProps(
                    () => openTab({ type: 'plan', id: p.plan_id }, p.title || p.plan_id),
                    () => openTabBg({ type: 'plan', id: p.plan_id }, p.title || p.plan_id),
                  )}
                >
                  {fresh && <span style={{ color: 'var(--fp-ok)', marginRight: 6, fontSize: 14 }}>● 24h</span>}
                  {p.title || p.plan_id}
                </button>
                <span style={S.muted}>
                  {relFromIso(p.last_modified_ts)}{p.todo_total ? ` · ${p.todo_done}/${p.todo_total}` : ''}{p.status ? ` · ${p.status}` : ''}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {data.all_green && (
        <div style={{ ...S.section, marginBottom: 14 }} data-testid="briefing-all-green">
          <div style={S.sectionTitle}>当前状态</div>
          <div style={S.muted}>当前没有待审材料、阻断或失败线程。</div>
        </div>
      )}

      <div style={S.sectionGrid}>
        <div style={S.section}>
          <div style={S.sectionTitle}>下一步</div>
          {data.next_actions.length === 0 && <div style={S.muted}>暂无需要处理的事项。</div>}
          {data.next_actions.map((a, index) => {
            const href = actionHref(a.target)
            return (
              <div key={`${a.kind}-${index}`} style={S.row} data-testid="briefing-action">
                <span style={S.rowMain}>{a.label}</span>
                {href ? (
                  <a style={{ ...S.pill, color: severityColor(a.priority), textDecoration: 'none' }} href={href}>
                    {priorityLabel(a.priority)}
                  </a>
                ) : (
                  <span style={{ ...S.pill, color: severityColor(a.priority) }}>{priorityLabel(a.priority)}</span>
                )}
              </div>
            )
          })}
        </div>

        <div style={S.section}>
          <div style={S.sectionTitle}>最近审阅材料</div>
          {data.review.recent.length === 0 && (
            <div style={S.muted}>当前没有待审材料。</div>
          )}
          {data.review.recent.map((m) => (
            <div key={m.id} style={S.row}>
              <a style={{ ...S.link, ...S.rowMain }} href={reviewHref(m.id)}>
                {m.title}
              </a>
              <span style={S.muted}>{m.tier} / {m.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
