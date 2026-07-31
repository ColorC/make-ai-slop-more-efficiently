import React, { useEffect, useMemo, useState } from 'react'
import { api, type TraceDetail, type TraceEvent } from '../../api/client'
import type { TraceEntity } from './index'
import { statusColorOf } from '../../shell/tokens'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { List, GitBranch, Activity, Layers, AlignJustify, ZoomIn, ZoomOut, RotateCcw } from 'lucide-react'

// 事件类型 → 冷色 token。原来一串裸 hex 调色板换成 frostpane 语义/强调色, 改主题只改 frostpane.css 一处。
const eventTypeColor: Record<string, string> = {
  'task.intent': 'var(--fp-accent)', 'task.finish': 'var(--fp-ok)', 'task.error': 'var(--fp-err)',
  'agent.llm.request': 'var(--fp-violet)', 'agent.llm.response': 'var(--fp-violet)',
  'agent.tool.call': 'var(--fp-warn)', 'agent.tool.result': 'var(--fp-warn)',
  'agent.state.change': 'var(--fp-accent-2)', 'agent.think': 'var(--fp-text-3)',
}

function colorOf(ev: TraceEvent): string {
  // task.* → status semantic color; otherwise event-type palette.
  if (ev.event_type === 'task.finish') return statusColorOf('finished')
  if (ev.event_type === 'task.error') return statusColorOf('error')
  if (ev.event_type === 'task.intent') return statusColorOf('active')
  return eventTypeColor[ev.event_type] || 'var(--fp-text-3)'
}

function fmtTs(ts: string | null | undefined): string {
  if (!ts) return ''
  try { return new Date(ts).toLocaleString('zh', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return ts.slice(0, 19) }
}

function eventSummary(ev: TraceEvent): string {
  const p = ev.payload || {}
  switch (ev.event_type) {
    case 'task.intent': return p.instruction || p.task_desc || (p.pipeline ? `pipeline:${p.pipeline} → ${p.entry || ''}` : '-')
    case 'task.finish': return (p.result || '').toString().slice(0, 80) || 'done'
    case 'task.error': return p.error || p.reason || 'error'
    case 'agent.tool.call':
      if (p.tool) return `${p.tool}(${Object.keys(p.args || {}).join(', ')})`
      return `${p.node || '?'}: ${p.format_in || ''} → ${p.format_out || ''}`
    case 'agent.tool.result':
      if (p.tool) return `${p.tool} → ${(p.result || '').toString().slice(0, 60)}`
      return `${p.node || '?'} [${p.verdict || '?'}]`
    case 'agent.llm.response': return (p.content || p.text || '').toString().slice(0, 80)
    case 'agent.llm.request': return p.model ? `model=${p.model}` : `${p.node || '?'}: ${p.format_in || ''} → ${p.format_out || ''}`
    case 'agent.state.change': return p.from_state ? `${p.from_state} → ${p.to_state}` : `step ${p.step || '?'}: ${p.node || ''}`
    case 'agent.think': return (p.thought || '').slice(0, 80)
    default: return JSON.stringify(p).slice(0, 60)
  }
}

interface TreeNode {
  ev: TraceEvent
  depth: number
  children: TreeNode[]
}

/** Build parent_id-rooted forest. Orphans (parent_id refers nothing visible) become roots. */
function buildForest(events: TraceEvent[]): TreeNode[] {
  const byId = new Map<string, TreeNode>()
  for (const ev of events) byId.set(ev.id, { ev, depth: 0, children: [] })
  const roots: TreeNode[] = []
  for (const ev of events) {
    const node = byId.get(ev.id)!
    const parent = ev.parent_id ? byId.get(ev.parent_id) : null
    if (parent) {
      node.depth = parent.depth + 1
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }
  // sort children by timestamp asc
  const cmp = (a: TreeNode, b: TreeNode) => (a.ev.timestamp || '').localeCompare(b.ev.timestamp || '')
  const sortRec = (ns: TreeNode[]) => { ns.sort(cmp); ns.forEach((n) => sortRec(n.children)) }
  sortRec(roots)
  return roots
}

/** Flatten tree to depth-prefixed rows in pre-order. */
function flattenTree(roots: TreeNode[], collapsed: Set<string>): TreeNode[] {
  const out: TreeNode[] = []
  const walk = (n: TreeNode) => {
    out.push(n)
    if (collapsed.has(n.ev.id)) return
    n.children.forEach(walk)
  }
  roots.forEach(walk)
  return out
}

type View = 'list' | 'tree' | 'timeline'

const VIEW_TABS: Array<{ key: View; label: string; icon: React.ReactNode }> = [
  { key: 'list', label: 'List', icon: <List size={14} /> },
  { key: 'tree', label: 'Tree', icon: <GitBranch size={14} /> },
  { key: 'timeline', label: 'Timeline', icon: <Activity size={14} /> },
]

// rim 高光 + token 描边的玻璃外壳(粘顶工具条 / 右侧详情都用), 浮在全局冷渐变上。
const GLASS: React.CSSProperties = {
  background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
  border: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
}

const S: Record<string, any> = {
  // 面板 root 透明 —— 吃 body 全局统一冷渐变, 不再铺 colors.bg 实底把渐变顶掉。
  root: { display: 'flex', flexDirection: 'column' as const, height: '100%', background: 'transparent', color: 'var(--fp-text)', fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace", fontSize: 14 },
  // 顶部仅留视图切换页签(无重复标题头), 玻璃外壳粘顶。
  toolbar: {
    display: 'flex', gap: 10, flexShrink: 0, alignItems: 'center', justifyContent: 'space-between',
    padding: '8px 16px', ...GLASS, borderTop: 'none', borderLeft: 'none', borderRight: 'none',
  },
  tabs: { display: 'flex', gap: 4 },
  // shadcn 风页签: 圆角矩形, 选中与内容同色无缝(同 surface, 无底边线), 未选中弱底凹陷。
  tab: (active: boolean): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', gap: 6,
    padding: '5px 12px', borderRadius: 7, border: '1px solid transparent',
    background: active ? 'var(--fp-surface)' : 'transparent',
    color: active ? 'var(--fp-text)' : 'var(--fp-text-3)',
    cursor: 'pointer', fontSize: 13, fontWeight: active ? 600 : 500, fontFamily: 'inherit',
    transition: 'background 150ms var(--fp-ease), color 150ms var(--fp-ease)',
  }),
  meta: { color: 'var(--fp-text-3)', fontSize: 12 },
  body: { flex: 1, display: 'flex', overflow: 'hidden' },
  // 长事件列表保持安静: 极淡 surface, 不铺玻璃模糊大面积。
  events: { flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column' as const, gap: 3 },
  // 右侧详情 = 玻璃抽屉, 与左侧列表分层。
  detail: { width: 380, overflowY: 'auto', padding: 16, flexShrink: 0, ...GLASS, borderTop: 'none', borderRight: 'none', borderBottom: 'none' },
  evRow: (selected: boolean, color: string): React.CSSProperties => ({
    padding: '5px 9px', borderRadius: 7, cursor: 'pointer',
    background: selected ? 'var(--fp-accent-weak)' : 'transparent',
    borderLeft: `3px solid ${color}`,
    transition: 'background 150ms var(--fp-ease)',
  }),
  treeRow: (selected: boolean, color: string, depth: number): React.CSSProperties => ({
    padding: '5px 9px', borderRadius: 7, cursor: 'pointer',
    background: selected ? 'var(--fp-accent-weak)' : 'transparent',
    borderLeft: `3px solid ${color}`,
    marginLeft: depth * 16,
    display: 'flex', alignItems: 'center', gap: 6,
    transition: 'background 150ms var(--fp-ease)',
  }),
  chev: { width: 12, color: 'var(--fp-text-3)', cursor: 'pointer', userSelect: 'none' as const, textAlign: 'center' as const },
  tlRow: (selected: boolean): React.CSSProperties => ({
    position: 'relative', height: 22, marginBottom: 2, cursor: 'pointer',
    background: selected ? 'var(--fp-accent-weak)' : 'transparent',
    borderRadius: 4,
  }),
  tlLabel: { position: 'absolute' as const, left: 4, top: 3, color: 'var(--fp-text-2)', fontSize: 12, pointerEvents: 'none' as const, whiteSpace: 'nowrap' as const, zIndex: 2, textShadow: '0 0 2px rgba(0,0,0,.8)' },
  tlBar: (color: string, leftPct: number, widthPct: number): React.CSSProperties => ({
    position: 'absolute', top: 4, height: 14, borderRadius: 3,
    left: `${leftPct}%`, width: `${Math.max(widthPct, 0.6)}%`,
    background: color, opacity: 0.8,
  }),
  tlAxis: {
    position: 'sticky' as const, top: 0, zIndex: 20,
    ...GLASS, borderTop: 'none', borderLeft: 'none', borderRight: 'none',
    padding: '6px 10px', fontSize: 12, color: 'var(--fp-text-3)',
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  },
  tlScroll: { overflowX: 'auto' as const, overflowY: 'visible' as const },
  tlInner: (zoom: number): React.CSSProperties => ({
    width: `${100 * zoom}%`, minWidth: '100%', position: 'relative' as const,
  }),
  tlGroupRow: (selected: boolean, expanded: boolean): React.CSSProperties => ({
    position: 'relative' as const, height: 24, marginBottom: 2,
    cursor: 'pointer',
    background: selected ? 'var(--fp-accent-weak)' : (expanded ? 'var(--fp-surface)' : 'transparent'),
    borderRadius: 4,
    borderTop: expanded ? '1px solid var(--fp-border)' : 'none',
  }),
  tlGroupBar: (color: string, leftPct: number, widthPct: number): React.CSSProperties => ({
    position: 'absolute', top: 5, height: 14, borderRadius: 3,
    left: `${leftPct}%`, width: `${Math.max(widthPct, 0.6)}%`,
    background: color, opacity: 0.55,
    boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.05)',
  }),
  tlTick: (color: string, leftPct: number): React.CSSProperties => ({
    position: 'absolute', top: 4, width: 2, height: 16,
    left: `${leftPct}%`, background: color, opacity: 0.95,
    pointerEvents: 'none' as const, transform: 'translateX(-1px)',
  }),
  tlGroupLabel: {
    position: 'absolute' as const, left: 4, top: 4, color: 'var(--fp-text)', fontSize: 12,
    pointerEvents: 'none' as const, whiteSpace: 'nowrap' as const, zIndex: 3,
    textShadow: '0 0 3px rgba(0,0,0,.9), 0 0 3px rgba(0,0,0,.9)',
  },
  tlGroupBadge: {
    position: 'absolute' as const, right: 6, top: 5, fontSize: 12, color: 'var(--fp-text-3)',
    background: 'var(--fp-surface)', padding: '0 6px', borderRadius: 999, zIndex: 3,
    border: '1px solid var(--fp-border)',
  },
  tlChildRow: (selected: boolean): React.CSSProperties => ({
    position: 'relative' as const, height: 18, marginBottom: 1,
    cursor: 'pointer',
    background: selected ? 'var(--fp-accent-weak)' : 'transparent',
    borderRadius: 3, marginLeft: 16,
  }),
  tlChildBar: (color: string, leftPct: number): React.CSSProperties => ({
    position: 'absolute', top: 3, height: 12, width: 4, borderRadius: 2,
    left: `${leftPct}%`, background: color, transform: 'translateX(-2px)',
  }),
  tlChildLabel: {
    position: 'absolute' as const, top: 2, color: 'var(--fp-text-2)', fontSize: 12,
    pointerEvents: 'none' as const, whiteSpace: 'nowrap' as const, zIndex: 2,
    textShadow: '0 0 2px rgba(0,0,0,.8)',
  },
  zoomCtl: {
    display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--fp-text-3)',
  },
  zoomBtn: {
    background: 'transparent', color: 'var(--fp-text-3)', border: '1px solid var(--fp-border)',
    borderRadius: 6, cursor: 'pointer', padding: '2px 7px', fontSize: 12, fontFamily: 'inherit',
    minWidth: 22, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 4,
    transition: 'color 150ms var(--fp-ease), border-color 150ms var(--fp-ease)',
  },
  // 段落标题 15px(比卡名小一档仍醒目), 非纯加粗堆叠。
  sectionTitle: { color: 'var(--fp-text-2)', fontSize: 15, fontWeight: 600, marginBottom: 6, marginTop: 10, letterSpacing: '-0.01em' },
  detailHead: { color: 'var(--fp-text)', fontSize: 16, fontWeight: 650, marginBottom: 8, letterSpacing: '-0.01em', wordBreak: 'break-all' as const },
  // payload mono 块: 安静实底, 长内容滚动。
  pre: { background: 'var(--fp-solid)', padding: 10, borderRadius: 7, border: '1px solid var(--fp-border)', color: 'var(--fp-text-2)', whiteSpace: 'pre-wrap' as const, wordBreak: 'break-all' as const, fontSize: 13, maxHeight: 600, overflow: 'auto' },
  empty: { color: 'var(--fp-text-3)', fontSize: 13 },
}

function KV({ k, v }: { k: string; v: string | null | undefined }) {
  if (!v) return null
  return <div style={{ marginBottom: 4, fontSize: 13 }}><span style={{ color: 'var(--fp-text-3)' }}>{k}: </span><span style={{ color: 'var(--fp-text-2)', wordBreak: 'break-all' }}>{v}</span></div>
}

interface TimelineSpan {
  ev: TraceEvent
  depth: number
  startMs: number
  endMs: number
}

const tsMs = (ev: TraceEvent): number => {
  if (!ev.timestamp) return 0
  const n = Date.parse(ev.timestamp)
  return Number.isFinite(n) ? n : 0
}

/** Pre-order flatten with end = max(self ts, max child end). (Ungrouped mode.) */
function buildTimelineSpans(roots: TreeNode[]): TimelineSpan[] {
  const endOf = new Map<string, number>()
  const computeEnd = (n: TreeNode): number => {
    let end = tsMs(n.ev)
    for (const c of n.children) {
      const ce = computeEnd(c)
      if (ce > end) end = ce
    }
    endOf.set(n.ev.id, end)
    return end
  }
  roots.forEach(computeEnd)
  const out: TimelineSpan[] = []
  const emit = (n: TreeNode) => {
    out.push({ ev: n.ev, depth: n.depth, startMs: tsMs(n.ev), endMs: endOf.get(n.ev.id) || tsMs(n.ev) })
    n.children.forEach(emit)
  }
  roots.forEach(emit)
  return out
}

// ─── Grouped timeline (per user round 21: 同 worker = 一行) ───────────────────

interface TimelineGroupRow {
  kind: 'group'
  key: string  // grouping key (currently == source)
  source: string
  startMs: number
  endMs: number
  events: TraceEvent[]  // sorted by timestamp asc
}

interface TimelineEventRow {
  kind: 'event'
  ev: TraceEvent
  source: string
  startMs: number
  endMs: number
}

type TimelineRow = TimelineGroupRow | TimelineEventRow

/** Group events by `source` (= worker). Each group's bar spans first→last event. */
function buildTimelineGroups(events: TraceEvent[]): TimelineGroupRow[] {
  const bySource = new Map<string, TraceEvent[]>()
  for (const ev of events) {
    const key = ev.source || 'unknown'
    if (!bySource.has(key)) bySource.set(key, [])
    bySource.get(key)!.push(ev)
  }
  const out: TimelineGroupRow[] = []
  for (const [source, evs] of bySource) {
    evs.sort((a, b) => tsMs(a) - tsMs(b))
    out.push({
      kind: 'group', key: source, source,
      startMs: tsMs(evs[0]),
      endMs: tsMs(evs[evs.length - 1]),
      events: evs,
    })
  }
  out.sort((a, b) => a.startMs - b.startMs)
  return out
}

function flattenGroupedTimeline(
  groups: TimelineGroupRow[], expanded: Set<string>,
): TimelineRow[] {
  const out: TimelineRow[] = []
  for (const g of groups) {
    out.push(g)
    if (expanded.has(g.key)) {
      for (const ev of g.events) {
        out.push({ kind: 'event', ev, source: g.source, startMs: tsMs(ev), endMs: tsMs(ev) })
      }
    }
  }
  return out
}

type GroupBy = 'none' | 'source'

export default function TraceEditor({ entity }: { entity: TraceEntity }) {
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<TraceEvent | null>(null)
  const [view, setView] = useState<View>('list')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  // Timeline view controls (per ROADMAP S14):
  //   groupBy: 'source' (default) collapses every event from same worker into one row
  //   zoomFactor: horizontal stretch (1.0 = fit, 0.5 = compressed, up to 5x)
  //   expandedGroups: which group rows are showing their child events
  const [groupBy, setGroupBy] = useState<GroupBy>('source')
  const [zoomFactor, setZoomFactor] = useState(1.0)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    setDetail(null); setSelected(null); setError(null); setCollapsed(new Set())
    setExpandedGroups(new Set())
    api.trace(entity.id).then((d) => { if (!cancelled) setDetail(d) }).catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [entity.id])

  const events = detail?.events || []
  const forest = useMemo(() => buildForest(events), [events])
  const flatTree = useMemo(() => flattenTree(forest, collapsed), [forest, collapsed])
  const timelineSpans = useMemo(() => buildTimelineSpans(forest), [forest])
  const timelineGroups = useMemo(() => buildTimelineGroups(events), [events])
  const groupedRows = useMemo(
    () => flattenGroupedTimeline(timelineGroups, expandedGroups),
    [timelineGroups, expandedGroups],
  )

  const tlBounds = useMemo(() => {
    const src = groupBy === 'source' ? timelineGroups : timelineSpans
    if (src.length === 0) return { min: 0, max: 1, span: 1 }
    let min = Infinity, max = -Infinity
    for (const s of src) {
      if (s.startMs && s.startMs < min) min = s.startMs
      if (s.endMs && s.endMs > max) max = s.endMs
    }
    if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
      return { min, max: min + 1, span: 1 }
    }
    return { min, max, span: max - min }
  }, [timelineSpans, timelineGroups, groupBy])

  const toggleCollapse = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const toggleGroup = (key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  const bumpZoom = (delta: number) => {
    setZoomFactor((z) => Math.max(0.5, Math.min(5, +(z + delta).toFixed(2))))
  }
  const resetZoom = () => setZoomFactor(1)

  if (error) return <div style={{ ...S.root, padding: 16, color: 'var(--fp-err)' }}>{error}</div>
  if (!detail) return <div style={{ ...S.root, padding: 16, color: 'var(--fp-text-3)' }}>loading...</div>

  return (
    <div style={S.root} data-view={view}>
      {/* 无重复标题头(Linear 风内容优先): 页签已标识 trace 身份。顶部仅留视图页签 + 弱 meta 计数。 */}
      <div style={S.toolbar}>
        <div style={S.tabs}>
          {VIEW_TABS.map((t) => (
            <button key={t.key} data-view-btn={t.key} style={S.tab(view === t.key)} onClick={() => setView(t.key)}>
              {t.icon}{t.label}
            </button>
          ))}
        </div>
        <div style={S.meta}>{events.length} events</div>
      </div>

      <div style={S.body}>
        <div style={S.events} data-events-pane>
          {events.length === 0 && <div style={S.empty}>无事件</div>}

          {view === 'list' && events.map((ev) => {
            const color = colorOf(ev)
            return (
              <div key={ev.id} data-ev-id={ev.id} style={S.evRow(selected?.id === ev.id, color)} onClick={() => setSelected(ev)}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color, fontSize: 13, fontWeight: 600 }}>{ev.event_type}</span>
                  <span style={{ color: 'var(--fp-text-3)', fontSize: 12 }}>{fmtTs(ev.timestamp)}</span>
                </div>
                <div style={{ color: 'var(--fp-text-2)', fontSize: 13, marginTop: 2 }}>{eventSummary(ev)}</div>
              </div>
            )
          })}

          {view === 'tree' && flatTree.map((n) => {
            const ev = n.ev
            const color = colorOf(ev)
            const hasChildren = n.children.length > 0
            const isCollapsed = collapsed.has(ev.id)
            return (
              <div
                key={ev.id}
                data-ev-id={ev.id}
                data-depth={n.depth}
                style={S.treeRow(selected?.id === ev.id, color, n.depth)}
                onClick={() => setSelected(ev)}
              >
                <span
                  data-chev
                  style={S.chev}
                  onClick={(e) => { e.stopPropagation(); if (hasChildren) toggleCollapse(ev.id) }}
                >
                  {hasChildren ? (isCollapsed ? '▶' : '▼') : ''}
                </span>
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color, fontSize: 13, fontWeight: 600 }}>{ev.event_type}</span>
                    <span style={{ color: 'var(--fp-text-3)', fontSize: 12 }}>{fmtTs(ev.timestamp)}</span>
                  </div>
                  <div style={{ color: 'var(--fp-text-2)', fontSize: 13, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{eventSummary(ev)}</div>
                </div>
              </div>
            )
          })}

          {view === 'timeline' && (
            <div data-timeline>
              <div style={S.tlAxis}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  <span>{tlBounds.min ? new Date(tlBounds.min).toLocaleTimeString('zh', { hour12: false }) : ''}</span>
                  <span>span: {(tlBounds.span / 1000).toFixed(2)}s</span>
                  <span>{tlBounds.max ? new Date(tlBounds.max).toLocaleTimeString('zh', { hour12: false }) : ''}</span>
                </div>
                {/* 时间轴低频控件(分组/缩放)收进共享 KebabMenu ⋯, 不再一排等权按钮挤轴头。 */}
                <KebabMenu
                  testid="tl-controls"
                  items={[
                    {
                      label: groupBy === 'source' ? '按 worker 合并行: 开' : '按 worker 合并行: 关',
                      icon: groupBy === 'source' ? <Layers size={15} /> : <AlignJustify size={15} />,
                      testid: 'tl-group-toggle',
                      onClick: () => setGroupBy((g) => (g === 'source' ? 'none' : 'source')),
                    },
                    { label: '放大', icon: <ZoomIn size={15} />, testid: 'tl-zoom-in', onClick: () => bumpZoom(0.25) },
                    { label: '缩小', icon: <ZoomOut size={15} />, testid: 'tl-zoom-out', onClick: () => bumpZoom(-0.25) },
                    { label: `重置缩放 (当前 ${zoomFactor.toFixed(2)}x)`, icon: <RotateCcw size={15} />, testid: 'tl-zoom-reset', onClick: resetZoom },
                  ] as KebabItem[]}
                />
              </div>
              <div style={S.tlScroll}>
                <div style={S.tlInner(zoomFactor)}>
                  {groupBy === 'source' ? (
                    groupedRows.map((row) => {
                      if (row.kind === 'group') {
                        const g = row
                        const isExpanded = expandedGroups.has(g.key)
                        // Use the most recent event's color as the group's "tone".
                        const tone = colorOf(g.events[g.events.length - 1])
                        const leftPct = ((g.startMs - tlBounds.min) / tlBounds.span) * 100
                        const widthPct = ((g.endMs - g.startMs) / tlBounds.span) * 100
                        return (
                          <div
                            key={`g:${g.key}`}
                            data-tl-group={g.key}
                            data-tl-expanded={isExpanded ? '1' : '0'}
                            style={S.tlGroupRow(false, isExpanded)}
                            onClick={() => toggleGroup(g.key)}
                          >
                            <div style={S.tlGroupBar(tone, Math.max(leftPct, 0), widthPct)} />
                            {/* Tick marks for individual events on the group bar. */}
                            {g.events.map((ev) => {
                              const evPct = ((tsMs(ev) - tlBounds.min) / tlBounds.span) * 100
                              return (
                                <div
                                  key={`tk:${ev.id}`}
                                  data-tl-tick
                                  style={S.tlTick(colorOf(ev), Math.max(evPct, 0))}
                                />
                              )
                            })}
                            <div style={S.tlGroupLabel}>{isExpanded ? '▾ ' : '▸ '}{g.source}</div>
                            <div style={S.tlGroupBadge}>×{g.events.length}</div>
                          </div>
                        )
                      }
                      // event row (only when group is expanded)
                      const evRow = row
                      const c = colorOf(evRow.ev)
                      const leftPct = ((evRow.startMs - tlBounds.min) / tlBounds.span) * 100
                      return (
                        <div
                          key={`e:${evRow.ev.id}`}
                          data-ev-id={evRow.ev.id}
                          style={S.tlChildRow(selected?.id === evRow.ev.id)}
                          onClick={(e) => { e.stopPropagation(); setSelected(evRow.ev) }}
                        >
                          <div style={S.tlChildBar(c, Math.max(leftPct, 0))} />
                          <div style={{ ...S.tlChildLabel, left: `${Math.max(leftPct, 0)}%`, paddingLeft: 8 }}>
                            {evRow.ev.event_type}
                          </div>
                        </div>
                      )
                    })
                  ) : (
                    timelineSpans.map((s) => {
                      const color = colorOf(s.ev)
                      const leftPct = ((s.startMs - tlBounds.min) / tlBounds.span) * 100
                      const widthPct = ((s.endMs - s.startMs) / tlBounds.span) * 100
                      return (
                        <div
                          key={s.ev.id}
                          data-ev-id={s.ev.id}
                          data-depth={s.depth}
                          style={S.tlRow(selected?.id === s.ev.id)}
                          onClick={() => setSelected(s.ev)}
                        >
                          <div style={S.tlBar(color, Math.max(leftPct, 0), widthPct)} />
                          <div style={{ ...S.tlLabel, paddingLeft: s.depth * 12 }}>{s.ev.event_type}</div>
                        </div>
                      )
                    })
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        <div style={S.detail} data-detail-pane>
          {!selected ? (
            <div style={S.empty}>点事件查看详情</div>
          ) : (
            <div>
              <div style={S.detailHead}>{selected.event_type}</div>
              <KV k="id" v={selected.id} />
              <KV k="source" v={selected.source} />
              <KV k="timestamp" v={fmtTs(selected.timestamp)} />
              {selected.parent_id && <KV k="parent_id" v={selected.parent_id} />}
              <div style={S.sectionTitle}>Payload</div>
              <pre style={S.pre}>{JSON.stringify(selected.payload, null, 2).slice(0, 5000)}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
