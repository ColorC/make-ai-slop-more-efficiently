/**
 * ContextPanel — IDE 侧边上下文面板
 *
 * 在 IDE 对话旁展示所有 assistant 上下文域：
 *   Workspaces · Goals · Plans · History · Extra Help
 *
 * 每个条目可以"载入到当前对话"（将其摘要文本附加到下一条消息）。
 * 用户也可以搜索、展开详情、快速跳转 PM 页面。
 */
import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import type { AssistantContext, Goal, Plan, HistoryEntry, ExtraItem, Workspace } from '../../api/client'

const S: Record<string, React.CSSProperties> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--fp-bg)', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace', color: '#e0e0e0' },
  header: { padding: '6px 10px', borderBottom: '1px solid #1a1a1a', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 },
  search: { background: '#111', border: '1px solid #222', borderRadius: 3, color: '#e0e0e0', padding: '3px 7px', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace', width: '100%', boxSizing: 'border-box' as const },
  body: { flex: 1, overflowY: 'auto', padding: '4px 0' },
  section: { borderBottom: '1px solid #111', paddingBottom: 2 },
  sectionHdr: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 10px', cursor: 'pointer', color: '#555', fontSize: 14 },
  sectionHdrOpen: { color: '#90caf9' },
  item: { padding: '4px 10px 4px 16px', cursor: 'pointer', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 4 },
  itemHover: { background: '#141414' },
  badge: { display: 'inline-block', padding: '0px 4px', borderRadius: 2, fontSize: 14 },
  loadBtn: { background: '#1a2a3a', border: '1px solid #2a3a4a', borderRadius: 3, color: '#90caf9', padding: '1px 5px', cursor: 'pointer', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace', flexShrink: 0 },
  detail: { padding: '4px 10px 6px 16px', color: '#666', fontSize: 14, whiteSpace: 'pre-wrap', wordBreak: 'break-word' as const },
}

const statusColor: Record<string, string> = { planned: '#888', active: '#42a5f5', done: '#4caf50', cancelled: '#444' }
const kindColor: Record<string, string> = { knowledge: '#7e57c2', skill: '#42a5f5', pipeline: '#ffb74d', rule: '#ef5350' }

function fmtTs(ts: number | null): string {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// ─────────────────────────────────────────────────────────────────────────────

interface ContextPanelProps {
  onLoadContext: (text: string) => void  // 将文本附加到下一条消息
}

export default function ContextPanel({ onLoadContext }: ContextPanelProps) {
  const [ctx, setCtx] = useState<AssistantContext | null>(null)
  const [search, setSearch] = useState('')
  const [openSections, setOpenSections] = useState<Set<string>>(new Set(['goals', 'plans']))
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set())
  const [hoveredItem, setHoveredItem] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<Set<string>>(new Set())  // flash feedback

  const load = useCallback(async () => {
    try { setCtx(await api.assistant.context()) } catch (e) { console.error(e) }
  }, [])

  useEffect(() => { load() }, [load])

  const toggleSection = (s: string) =>
    setOpenSections(prev => { const n = new Set(prev); n.has(s) ? n.delete(s) : n.add(s); return n })

  const toggleItem = (id: string) =>
    setExpandedItems(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })

  const flashLoaded = (id: string) => {
    setLoaded(prev => new Set(prev).add(id))
    setTimeout(() => setLoaded(prev => { const n = new Set(prev); n.delete(id); return n }), 1500)
  }

  const q = search.toLowerCase()

  const filterStr = (s: string | null | undefined) => !q || (s || '').toLowerCase().includes(q)

  // ── Renderers ──

  const renderGoals = () => {
    if (!ctx) return null
    const goals = [...ctx.active_goals, ...ctx.planned_goals].filter(g =>
      filterStr(g.title) || filterStr(g.implementation_proof)
    )
    if (goals.length === 0 && q) return null

    const loadGoal = (g: Goal) => {
      const text = `[Goal: ${g.title}]\nStatus: ${g.status}\n${g.implementation_proof ? `Implementation proof:\n${g.implementation_proof}` : ''}`
      onLoadContext(text)
      flashLoaded(g.goal_id)
    }

    return (
      <Section id="goals" title={`Goals (${ctx.active_goals.length} active)`} openSections={openSections} onToggle={toggleSection}>
        {goals.map(g => (
          <React.Fragment key={g.goal_id}>
            <div
              style={{ ...S.item, background: hoveredItem === g.goal_id ? '#141414' : 'transparent' }}
              onMouseEnter={() => setHoveredItem(g.goal_id)}
              onMouseLeave={() => setHoveredItem(null)}
            >
              <div style={{ flex: 1, minWidth: 0 }} onClick={() => toggleItem(g.goal_id)}>
                <span style={{ color: statusColor[g.status], marginRight: 4 }}>●</span>
                <span style={{ color: '#ccc' }}>{g.title}</span>
              </div>
              <button
                style={{ ...S.loadBtn, color: loaded.has(g.goal_id) ? '#4caf50' : '#90caf9' }}
                onClick={() => loadGoal(g)}
                title="Load into conversation"
              >{loaded.has(g.goal_id) ? '✓' : '+'}</button>
            </div>
            {expandedItems.has(g.goal_id) && g.implementation_proof && (
              <div style={S.detail}>{g.implementation_proof}</div>
            )}
          </React.Fragment>
        ))}
        {goals.length === 0 && <div style={{ padding: '4px 16px', color: '#444' }}>No goals. Add in PM tab.</div>}
      </Section>
    )
  }

  const renderPlans = () => {
    if (!ctx) return null
    const plans = ctx.active_plans.filter(p => filterStr(p.title) || filterStr(p.current_phase))
    if (plans.length === 0 && q) return null

    const loadPlan = (p: Plan) => {
      const text = `[Plan: ${p.title}]\nFolder: ${p.folder_path}\nCurrent phase: ${p.current_phase || 'unknown'}`
      onLoadContext(text)
      flashLoaded(p.plan_id)
    }

    return (
      <Section id="plans" title={`Plans (${ctx.active_plans.length} active)`} openSections={openSections} onToggle={toggleSection}>
        {plans.map(p => (
          <div
            key={p.plan_id}
            style={{ ...S.item, background: hoveredItem === p.plan_id ? '#141414' : 'transparent' }}
            onMouseEnter={() => setHoveredItem(p.plan_id)}
            onMouseLeave={() => setHoveredItem(null)}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: '#ccc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.title}</div>
              {p.current_phase && <div style={{ color: '#555', fontSize: 14 }}>📍 {p.current_phase}</div>}
            </div>
            <button
              style={{ ...S.loadBtn, color: loaded.has(p.plan_id) ? '#4caf50' : '#90caf9' }}
              onClick={() => loadPlan(p)}
              title="Load into conversation"
            >{loaded.has(p.plan_id) ? '✓' : '+'}</button>
          </div>
        ))}
        {plans.length === 0 && <div style={{ padding: '4px 16px', color: '#444' }}>No active plans.</div>}
      </Section>
    )
  }

  const renderWorkspaces = () => {
    if (!ctx) return null
    const ws = ctx.workspaces.filter(w => filterStr(w.key) || filterStr(w.title) || filterStr(w.description) || filterStr(w.path))
    if (ws.length === 0 && q) return null

    const loadWs = (w: Workspace) => {
      const text = `[Workspace: ${w.key}]\nTitle: ${w.title}\n${w.path ? `Path: ${w.path}` : ''}${w.description ? `\nDesc: ${w.description}` : ''}${w.key_files?.length ? `\nKey files: ${w.key_files.join(', ')}` : ''}`
      onLoadContext(text)
      flashLoaded(w.key)
    }

    return (
      <Section id="workspaces" title={`Workspaces (${ctx.workspaces.length})`} openSections={openSections} onToggle={toggleSection}>
        {ws.map(w => (
          <div
            key={w.key}
            style={{ ...S.item, background: hoveredItem === w.key ? '#141414' : 'transparent' }}
            onMouseEnter={() => setHoveredItem(w.key)}
            onMouseLeave={() => setHoveredItem(null)}
          >
            <div style={{ flex: 1, minWidth: 0 }} onClick={() => toggleItem(w.key)}>
              <span style={{ color: '#90caf9' }}>{w.key}</span>
              <span style={{ color: '#555', marginLeft: 6 }}>{w.title}</span>
              {expandedItems.has(w.key) && (
                <div style={{ color: '#555', fontSize: 14, marginTop: 2 }}>
                  {w.path && <div>{w.path}</div>}
                  {w.description && <div>{w.description}</div>}
                  {w.key_files?.length > 0 && <div>{w.key_files.join(', ')}</div>}
                </div>
              )}
            </div>
            <button
              style={{ ...S.loadBtn, color: loaded.has(w.key) ? '#4caf50' : '#90caf9' }}
              onClick={() => loadWs(w)}
            >{loaded.has(w.key) ? '✓' : '+'}</button>
          </div>
        ))}
        {ws.length === 0 && <div style={{ padding: '4px 16px', color: '#444' }}>No workspaces.</div>}
      </Section>
    )
  }

  const renderHistory = () => {
    if (!ctx) return null
    const entries = ctx.recent_history.filter(h => filterStr(h.summary))
    if (entries.length === 0 && q) return null

    const loadHistory = (h: HistoryEntry) => {
      const text = `[Work History: ${fmtTs(h.compacted_at)}]\n${h.summary}${h.open_todos.length ? `\nOpen todos:\n${h.open_todos.map(t => `- ${t}`).join('\n')}` : ''}`
      onLoadContext(text)
      flashLoaded(h.session_id)
    }

    return (
      <Section id="history" title={`Recent History (${ctx.recent_history.length})`} openSections={openSections} onToggle={toggleSection}>
        {entries.map(h => (
          <React.Fragment key={h.session_id}>
            <div
              style={{ ...S.item, background: hoveredItem === h.session_id ? '#141414' : 'transparent' }}
              onMouseEnter={() => setHoveredItem(h.session_id)}
              onMouseLeave={() => setHoveredItem(null)}
            >
              <div style={{ flex: 1, minWidth: 0 }} onClick={() => toggleItem(h.session_id)}>
                <span style={{ color: '#888' }}>{fmtTs(h.compacted_at)}</span>
                {h.open_todos.length > 0 && (
                  <span style={{ ...S.badge, background: '#2a1a00', color: '#ffb74d', marginLeft: 6 }}>{h.open_todos.length} todo</span>
                )}
              </div>
              <button
                style={{ ...S.loadBtn, color: loaded.has(h.session_id) ? '#4caf50' : '#90caf9' }}
                onClick={() => loadHistory(h)}
              >{loaded.has(h.session_id) ? '✓' : '+'}</button>
            </div>
            {expandedItems.has(h.session_id) && (
              <div style={{ ...S.detail, maxHeight: 120, overflowY: 'auto' }}>{h.summary}</div>
            )}
          </React.Fragment>
        ))}
        {entries.length === 0 && <div style={{ padding: '4px 16px', color: '#444' }}>No history yet.</div>}
      </Section>
    )
  }

  const renderExtra = () => {
    if (!ctx) return null
    const allExtra: ExtraItem[] = Object.values(ctx.extra_by_kind).flat()
    const filtered = allExtra.filter(i => filterStr(i.title) || filterStr(i.format_in) || filterStr(i.format_out) || filterStr(i.content))
    if (filtered.length === 0 && q) return null

    const loadExtra = (item: ExtraItem) => {
      const text = `[${item.kind}: ${item.title}]${item.format_in ? `\nWhen: ${item.format_in}` : ''}${item.format_out ? `\nOutput: ${item.format_out}` : ''}${item.content ? `\n\n${item.content}` : ''}`
      onLoadContext(text)
      flashLoaded(item.item_id)
    }

    // Group by kind for display
    const byKind: Record<string, ExtraItem[]> = {}
    filtered.forEach(i => { byKind[i.kind] = byKind[i.kind] || []; byKind[i.kind].push(i) })

    const total = allExtra.length
    const SHOW_MAX = 5

    return (
      <Section id="extra" title={`Extra Help (${total})`} openSections={openSections} onToggle={toggleSection}>
        {filtered.length > SHOW_MAX && !q && (
          <div style={{ padding: '2px 16px', color: '#555', fontSize: 14 }}>
            Showing {SHOW_MAX} of {total} — use search to filter
          </div>
        )}
        {filtered.slice(0, q ? undefined : SHOW_MAX).map(item => (
          <React.Fragment key={item.item_id}>
            <div
              style={{ ...S.item, background: hoveredItem === item.item_id ? '#141414' : 'transparent' }}
              onMouseEnter={() => setHoveredItem(item.item_id)}
              onMouseLeave={() => setHoveredItem(null)}
            >
              <div style={{ flex: 1, minWidth: 0 }} onClick={() => toggleItem(item.item_id)}>
                <span style={{ ...S.badge, background: '#111', color: kindColor[item.kind], marginRight: 4 }}>{item.kind}</span>
                <span style={{ color: '#ccc' }}>{item.title}</span>
                {item.format_in && <div style={{ color: '#555', fontSize: 14, paddingLeft: 4 }}>↳ {item.format_in}</div>}
              </div>
              <button
                style={{ ...S.loadBtn, color: loaded.has(item.item_id) ? '#4caf50' : '#90caf9' }}
                onClick={() => loadExtra(item)}
              >{loaded.has(item.item_id) ? '✓' : '+'}</button>
            </div>
            {expandedItems.has(item.item_id) && item.content && (
              <div style={{ ...S.detail, maxHeight: 100, overflowY: 'auto' }}>{item.content}</div>
            )}
          </React.Fragment>
        ))}
        {allExtra.length === 0 && <div style={{ padding: '4px 16px', color: '#444' }}>No extra help items. Add in PM tab.</div>}
      </Section>
    )
  }

  return (
    <div style={S.root}>
      <div style={S.header}>
        <span style={{ color: '#90caf9', fontWeight: 'bold', fontSize: 14 }}>Context</span>
        <button
          onClick={load}
          style={{ background: 'transparent', border: 'none', color: '#444', cursor: 'pointer', fontSize: 14, padding: 0 }}
          title="Refresh"
        >↺</button>
      </div>
      <div style={{ padding: '4px 8px', borderBottom: '1px solid #111', flexShrink: 0 }}>
        <input
          style={S.search}
          placeholder="Search context..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>
      <div style={S.body}>
        {renderGoals()}
        {renderPlans()}
        {renderWorkspaces()}
        {renderHistory()}
        {renderExtra()}
        {!ctx && <div style={{ padding: 16, color: '#444', textAlign: 'center' }}>Loading context...</div>}
      </div>
    </div>
  )
}

// ── Section collapsible wrapper ───────────────────────────────────────────────

function Section({ id, title, openSections, onToggle, children }: {
  id: string; title: string
  openSections: Set<string>; onToggle: (id: string) => void
  children: React.ReactNode
}) {
  const isOpen = openSections.has(id)
  return (
    <div style={S.section}>
      <div
        style={{ ...S.sectionHdr, ...(isOpen ? S.sectionHdrOpen : {}) }}
        onClick={() => onToggle(id)}
      >
        <span>{isOpen ? '▾' : '▸'} {title}</span>
      </div>
      {isOpen && children}
    </div>
  )
}
