import React, { useEffect, useState } from 'react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { usePanels } from '../../stores/panelsStore'
import PlanSidebar from './PlanSidebar'
import { ccApi, type CcSessionMeta } from '../../api/ccClient'
import { openProps } from '../../utils/middleClick'
import NotesForTarget from '../authored/NotesForTarget'
import { bossSightApi, type BossSightBindingBucket } from '../../api/bossSightClient'
import ActiveConvoBadge from '../../shared/view/ui/ActiveConvoBadge'

export interface PlanEntity extends Entity {
  type: 'plan'
  topic: string
  date: string | null
  folder_path: string
  archived?: boolean
  has_plan_md?: boolean
  file_count: number
}

interface PlanDetailFile {
  path: string
  is_md: boolean
  size: number
  mtime: number
  note_id_if_md: string | null
  summary?: string  // 后端抽取的一句简述(md 文件)
}

interface PlanDetail extends PlanEntity {
  files: PlanDetailFile[]
}

let _cache: PlanEntity[] | null = null

async function fetchList(): Promise<PlanEntity[]> {
  if (_cache) return _cache
  const r = await fetch('/api/plans')
  if (!r.ok) throw new Error(`list plans: ${r.status}`)
  const d = await r.json() as { items: any[] }
  _cache = d.items.map((p: any) => ({
    type: 'plan' as const,
    id: p.id,
    title: p.date ? `${p.date} ${p.topic}` : p.topic,
    topic: p.topic,
    date: p.date,
    folder_path: p.folder_path,
    archived: p.archived,
    has_plan_md: p.has_plan_md,
    file_count: p.file_count,
    tags: p.archived ? ['archived'] : (p.date || '').slice(0, 7) ? [(p.date || '').slice(0, 7)] : [],
  }))
  return _cache!
}

async function fetchDetail(id: string): Promise<PlanDetail> {
  const r = await fetch(`/api/plans/${id}`)
  if (!r.ok) throw new Error(`get plan: ${r.status}`)
  const p = await r.json()
  return {
    type: 'plan',
    id: p.id,
    title: p.date ? `${p.date} ${p.topic}` : p.topic,
    topic: p.topic,
    date: p.date,
    folder_path: p.folder_path,
    archived: p.archived,
    file_count: p.files.length,
    files: p.files,
  }
}

const resolver: EntityResolver<PlanEntity> = {
  type: 'plan',
  async fetch(id) {
    const list = await fetchList()
    const found = list.find((p) => p.id === id)
    if (found) return found
    const d = await fetchDetail(id)
    return d
  },
  async list() { return fetchList() },
  async search(q) {
    const all = await fetchList()
    const ql = q.toLowerCase()
    return all.filter((p) => p.id.toLowerCase().includes(ql) || p.topic.toLowerCase().includes(ql))
  },
}

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: '#0f0f0f', color: '#e0e0e0', fontFamily: 'Consolas, Menlo, monospace', overflow: 'hidden' },
  header: { padding: '8px 12px', borderBottom: '1px solid #222', background: 'var(--fp-bg)', flexShrink: 0 },
  title: { color: '#90caf9', fontSize: 18, fontWeight: 700 as const, marginBottom: 4 },
  meta: { color: '#666', fontSize: 14 },
  filesLabel: { color: '#7d8da0', fontSize: 14, fontWeight: 600 as const, margin: '4px 0 6px' },
  // 时间线
  timeline: { marginBottom: 16 },
  tlRow: { display: 'flex', alignItems: 'baseline', gap: 8, padding: '3px 8px', borderBottom: '1px solid #141a1f' },
  tlTime: { color: '#5f7081', fontSize: 14, flexShrink: 0, width: 110, fontVariantNumeric: 'tabular-nums' as any },
  tlDotProgress: { flexShrink: 0, width: 7, height: 7, borderRadius: 7, background: 'var(--fp-ok)', alignSelf: 'center' as const },
  tlDotMaterial: { flexShrink: 0, width: 7, height: 7, borderRadius: 7, background: '#3a4a5a', alignSelf: 'center' as const },
  tlText: { flex: 1, minWidth: 0, color: '#c2cdd8', fontSize: 14, lineHeight: 1.45, fontFamily: '-apple-system, "Segoe UI", Roboto, sans-serif' },
  tlBy: { color: '#5f7081' },
  badge: (color: string): React.CSSProperties => ({ display: 'inline-block', padding: '1px 6px', borderRadius: 3, fontSize: 14, color, background: '#1a1a1a', marginRight: 6 }),
  body: { flex: 1, overflow: 'auto', padding: 16 },
  fileList: { fontSize: 14 },
  // 每行 = 文件名行 + 一句简述行(用户要点: 每个计划文档一句简述)
  fileRow: (clickable: boolean): React.CSSProperties => ({
    display: 'flex', flexDirection: 'column', gap: 2, padding: '5px 8px', borderRadius: 3,
    cursor: clickable ? 'pointer' : 'default',
    color: clickable ? '#bbb' : '#666',
    borderBottom: '1px solid #161616',
  }),
  fileRowTop: { display: 'flex', gap: 12, alignItems: 'baseline' as const },
  // 2 整行(用户: 不要在回车处截断, 显示 2 行); -webkit-line-clamp 限两行省略。
  fileSummary: { color: '#7d8da0', fontSize: 14, lineHeight: 1.4, marginLeft: 42, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as any, overflow: 'hidden', fontFamily: '-apple-system, "Segoe UI", Roboto, sans-serif' },
  fpath: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  fmd: { color: '#90caf9', flexShrink: 0, width: 30, textAlign: 'center' as const, fontSize: 14 },
  fsize: { color: '#555', flexShrink: 0, width: 60, textAlign: 'right' as const, fontSize: 14 },
  err: { padding: 16, color: '#ef5350' },
  empty: { padding: 16, color: '#666', fontStyle: 'italic' as const },
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n}B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}K`
  return `${(n / 1024 / 1024).toFixed(1)}M`
}

// CC-PLAN-SESSION-CONTEXT 段四-3: plan 详情页 cc_sessions 反查块。
// binding = SESSION-SELF-BINDING(4.5) 聚合桶(by_plan[planId]), 覆盖 chat+PTY 全部本机会话
// (本块下方列表只查得到 chat session, 标题旁用 binding 的活跃数补全"总共有多少活跃对话"这句)。
const RelatedCcSessions: React.FC<{ planId: string; binding?: BossSightBindingBucket }> = ({ planId, binding }) => {
  const [sessions, setSessions] = useState<CcSessionMeta[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)

  useEffect(() => {
    let cancelled = false
    setSessions(null); setError(null)
    fetch(`/api/cc/sessions?active_plan=${encodeURIComponent(planId)}`)
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then((d) => {
        if (cancelled) return
        const alive: CcSessionMeta[] = (d.items || []).map((m: any) => ({ ...m, status: 'alive' as const }))
        const rec: CcSessionMeta[] = (d.recoverable || []).map((m: any) => ({ ...m, status: 'recoverable' as const }))
        setSessions([...alive, ...rec])
      })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [planId])

  if (error) {
    return <div style={{ color: '#ef5350', fontSize: 14, padding: 8 }}>载入 cc_sessions 失败: {error}</div>
  }
  if (sessions === null) return <div style={{ color: '#666', fontSize: 14, padding: 8 }}>loading…</div>
  if (sessions.length === 0) {
    return <div style={{ color: '#666', fontSize: 14, padding: 8, fontStyle: 'italic' as const }}>(无 cc_session 绑定此 plan)</div>
  }

  const cwdLast = (cwd: string) => cwd.split(/[\\/]/).filter(Boolean).slice(-1)[0] || cwd
  const fmtTs = (ts: number | undefined) => {
    if (!ts) return ''
    const d = Date.now() / 1000 - ts
    if (d < 60) return `${Math.round(d)}s ago`
    if (d < 3600) return `${Math.round(d / 60)}m ago`
    if (d < 86400) return `${Math.round(d / 3600)}h ago`
    return `${Math.round(d / 86400)}d ago`
  }
  return (
    <div data-related-cc-sessions style={{ marginTop: 16 }}>
      <div style={{ color: '#888', fontSize: 14, marginBottom: 6, textTransform: 'uppercase' as const, fontWeight: 600 as const, display: 'flex', alignItems: 'center', gap: 8 }}>
        关联 cc_sessions ({sessions.length})
        {/* chat+PTY 全覆盖的活跃数(本列表只含 chat session, 可能比这个数少) */}
        {binding && <ActiveConvoBadge active={binding.active} total={binding.total} sessions={binding.sessions} />}
      </div>
      {sessions.map((s) => {
        const isAlive = (s.status || (s.alive ? 'alive' : 'recoverable')) === 'alive'
        return (
          <div
            key={s.id}
            data-related-cc-session={s.id}
            onClick={() => openTab({ type: 'cc_session', id: s.id }, `${cwdLast(s.cwd)} · ${s.id.slice(-6)}`)}
            style={{
              display: 'flex', gap: 8, padding: '4px 8px', borderRadius: 3,
              cursor: 'pointer', borderBottom: '1px solid #161616', alignItems: 'baseline' as const, fontSize: 14,
            }}
            title={`${s.id} · ${s.cwd}`}
          >
            <span style={{ color: isAlive ? '#4caf50' : '#666', fontSize: 14, minWidth: 70 }}>
              {isAlive ? '● alive' : '○ ended'}
            </span>
            <span style={{ flex: 1, color: 'var(--fp-link)', overflow: 'hidden' as const, textOverflow: 'ellipsis' as const, whiteSpace: 'nowrap' as const }}>
              {cwdLast(s.cwd)} · <span style={{ color: '#666' }}>{s.id.slice(-6)}</span>
            </span>
            <span style={{ color: '#888', fontSize: 14, minWidth: 60, textAlign: 'right' as const }}>
              {fmtTs(s.started_at)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

interface ProgressEntry { id: string; ref_type: string; ref_id: string; text: string; by: string; created_at: string; updated_at: string }
interface TimelineItem { ts: number; when: string; kind: 'progress' | 'material'; text: string; by?: string }

function fmtWhen(ms: number): string {
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// 时间线: 合并 progress 历史条目(omni progress 记的) + 本计划目录文件的产出/更新时间(material 产出时间), 按时间倒序。
// 用户 2026-06-06: 网页上能看这段时间这个 plan/project 经历了什么、产出了什么; 看 progress 记录时间 + 各 material 产出时间。
const PlanTimeline: React.FC<{ planId: string; files: PlanDetailFile[] }> = ({ planId, files }) => {
  const [entries, setEntries] = useState<ProgressEntry[] | null>(null)
  useEffect(() => {
    let cancelled = false
    setEntries(null)
    fetch(`/api/boss-sight/progress?type=plan&id=${encodeURIComponent(planId)}`)
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then((d) => { if (!cancelled) setEntries((d.entries || []) as ProgressEntry[]) })
      .catch(() => { if (!cancelled) setEntries([]) })
    return () => { cancelled = true }
  }, [planId])

  const items: TimelineItem[] = []
  for (const e of entries || []) {
    const ts = Date.parse(e.created_at)
    items.push({ ts: Number.isNaN(ts) ? 0 : ts, when: fmtWhen(ts), kind: 'progress', text: e.text, by: e.by })
  }
  for (const f of files) {
    const ts = (f.mtime || 0) * 1000
    items.push({ ts, when: fmtWhen(ts), kind: 'material', text: `产出/更新 ${f.path}`, by: undefined })
  }
  items.sort((a, b) => b.ts - a.ts)

  return (
    <div style={S.timeline} data-testid="plan-timeline">
      <div style={S.filesLabel}>时间线 ({items.length}) · 历史用 `omni progress add plan {'<id>'} "..."` 记</div>
      {entries === null && <div style={S.empty}>加载时间线…</div>}
      {entries !== null && items.length === 0 && <div style={S.empty}>暂无历史与产出。</div>}
      {items.map((it, i) => (
        <div key={i} style={S.tlRow}>
          <span style={S.tlTime}>{it.when}</span>
          <span style={it.kind === 'progress' ? S.tlDotProgress : S.tlDotMaterial} />
          <span style={S.tlText}>
            {it.kind === 'progress' ? '' : '📄 '}{it.text}{it.by ? <span style={S.tlBy}> · {it.by}</span> : null}
          </span>
        </div>
      ))}
    </div>
  )
}

// 在网页端启动一个计划的执行(调后端 POST /api/boss-sight/plan/run, 后台跑, 立即返回 run_id)。
const RunPlanButton: React.FC<{ planId: string }> = ({ planId }) => {
  const [state, setState] = useState<'idle' | 'starting' | 'started' | 'blocked' | 'error'>('idle')
  const [msg, setMsg] = useState('')
  const start = async () => {
    if (state === 'starting') return
    if (!window.confirm(`启动执行这个计划？\n\n会让后台的 agent 按计划里的任务一件件去做（会改代码/产出文件）。\n计划没写全会被拦下不执行。`)) return
    setState('starting'); setMsg('')
    try {
      const r = await fetch('/api/boss-sight/plan/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId }),
      })
      if (r.status === 400) {
        const d = await r.json().catch(() => ({}))
        const g = (d.detail && d.detail.gate) || {}
        setState('blocked')
        setMsg(`计划没写全，没启动：${g.summary || (d.detail && d.detail.error) || '完成度检查没过'}`)
        return
      }
      if (!r.ok) { setState('error'); setMsg(`启动失败：${r.status}`); return }
      const d = await r.json()
      setState('started')
      setMsg(`已启动（run_id=${d.run_id}）。去「任务窗口」看 agent 做到哪。`)
    } catch (e) {
      setState('error'); setMsg(`启动失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <button
        data-testid="plan-run-button"
        onClick={start}
        disabled={state === 'starting'}
        style={{ padding: '4px 12px', background: state === 'starting' ? '#1a3a1a' : '#143d14', border: '1px solid #2a5a2a', borderRadius: 4, color: 'var(--fp-ok)', cursor: state === 'starting' ? 'wait' : 'pointer', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace' }}
      >
        {state === 'starting' ? '启动中…' : '▶ 启动执行'}
      </button>
      {msg && (
        <span style={{ fontSize: 13, color: state === 'started' ? '#56d364' : state === 'blocked' ? '#e3b341' : 'var(--fp-err)' }}>
          {msg}
        </span>
      )}
    </div>
  )
}

const Editor: React.FC<{ entity: PlanEntity }> = ({ entity }) => {
  const [detail, setDetail] = useState<PlanDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [binding, setBinding] = useState<BossSightBindingBucket | undefined>(undefined)
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)

  useEffect(() => {
    let cancelled = false
    setDetail(null); setError(null)
    fetchDetail(entity.id).then((d) => { if (!cancelled) setDetail(d) }).catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [entity.id])

  useEffect(() => {
    let cancelled = false
    bossSightApi.activeBindings().then((d) => { if (!cancelled) setBinding(d.by_plan?.[entity.id]) }).catch(() => { if (!cancelled) setBinding(undefined) })
    return () => { cancelled = true }
  }, [entity.id])

  if (error) return <div style={{ ...S.root, ...S.err }}>{error}</div>
  if (!detail) return <div style={{ ...S.root, ...S.empty }}>loading…</div>

  const openFileAsNote = (note_id: string, bg = false) => {
    const title = note_id.split('/').pop() || note_id
    ;(bg ? openTabBg : openTab)({ type: 'note', id: note_id }, title)
  }

  const planMd = detail.files.find((f) => f.path === 'plan.md')

  return (
    <div style={S.root}>
      <div style={S.header}>
        <div style={{ ...S.title, display: 'flex', alignItems: 'center', gap: 8 }}>
          {entity.archived && <span style={S.badge('#ffb74d')}>archived</span>}
          {detail.date && <span style={S.badge('#42a5f5')}>{detail.date}</span>}
          {detail.topic}
          {/* N 活跃对话在推进本计划(chat+PTY 全覆盖) */}
          {binding && <ActiveConvoBadge active={binding.active} total={binding.total} sessions={binding.sessions} size="md" />}
        </div>
        <div style={S.meta}>{detail.folder_path} · {detail.files.length} 文件</div>
      </div>
      <div style={S.body}>
        <div style={{ marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {planMd && (
            <button
              style={{ padding: '4px 12px', background: '#1a2a3a', border: '1px solid #2a3a4a', borderRadius: 4, color: '#90caf9', cursor: 'pointer', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace' }}
              {...openProps(() => planMd.note_id_if_md && openFileAsNote(planMd.note_id_if_md), () => planMd.note_id_if_md && openFileAsNote(planMd.note_id_if_md, true))}
            >📋 打开 plan.md</button>
          )}
          {!entity.archived && detail.has_plan_md !== false && <RunPlanButton planId={entity.id} />}
        </div>
        {/* 时间线: 历史(omni progress)+ 各文档产出/更新时间, 按时间倒序 */}
        <PlanTimeline planId={entity.id} files={detail.files} />
        {/* 每个计划文档一句简述(用户要点): 文件名下方一行中文简述, 知道每个文档是关于什么的。 */}
        <div style={S.filesLabel}>全部文件 ({detail.files.length})· 左键打开 / 中键后台开</div>
        <div style={S.fileList}>
          {detail.files.length === 0 && <div style={S.empty}>无文件</div>}
          {detail.files.map((f) => {
            const clickable = f.is_md && !!f.note_id_if_md
            return (
              <div
                key={f.path}
                style={S.fileRow(clickable)}
                {...(clickable
                  ? openProps(() => openFileAsNote(f.note_id_if_md as string), () => openFileAsNote(f.note_id_if_md as string, true))
                  : {})}
                title={f.is_md ? '左键打开 / 中键后台打开' : '非 markdown, 不可在 KB 打开'}
              >
                <div style={S.fileRowTop}>
                  <span style={S.fmd}>{f.is_md ? 'md' : ''}</span>
                  <span style={S.fpath}>{f.path}</span>
                  <span style={S.fsize}>{fmtBytes(f.size)}</span>
                </div>
                {f.summary && <div style={S.fileSummary}>{f.summary}</div>}
              </div>
            )
          })}
        </div>
        {/* 反查关联 cc_sessions (CC-PLAN-SESSION-CONTEXT 段四-3) */}
        {!entity.archived && <RelatedCcSessions planId={entity.id} binding={binding} />}
        {/* 针对本 plan 的札记(评论/草稿) — 中心 authored store 回显 */}
        <div style={{ marginTop: 16, borderTop: '1px solid #222', paddingTop: 12 }}>
          <NotesForTarget
            kind="plan"
            id={entity.id}
            target={{ kind: 'plan', id: entity.id, plan_id: entity.id, title: detail.topic }}
          />
        </div>
      </div>
    </div>
  )
}

export const planRegistration: EntityRegistration<PlanEntity> = {
  resolver,
  renderer: {
    type: 'plan',
    Editor,                    // kept as fallback if user deep-links to a plan tab
    SidebarView: PlanSidebar,  // PM module uses tree-in-sidebar pattern; clicking a
                                // plan expands inline. Only .md files open in central.
  },
  label: 'Plan',
  icon: '📋',
}

export function invalidatePlanCache(): void { _cache = null }
