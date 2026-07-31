// ⚠️ 已退役(DECOMMISSIONED)— 保留文件不删。
// 旧壳左侧导航树 + 新建会话/spawn UI。App.tsx 已只挂 CockpitShell, 此组件无人挂载。
// 新建会话/选执行者 已迁入驾驶舱「执行者(控制台)」面板(entities/controller/ThreadMonitorPanel);
// 实体导航迁入全局搜索 + open_ref。处置依据见 plan.md(§5 / §9 Phase 4 / P1·P2)。
// 确认长期无引用后, 才在独立提交里删。
import React, { useEffect, useMemo, useState } from 'react'
import type { ModuleKey } from './ActivityBar'
import type { Entity, EntityType } from '../entities/types'
import { registry } from '../entities/registry'
import { usePanels } from '../stores/panelsStore'
import { ideApi } from '../api/ideClient'
import { ccApi } from '../api/ccClient'
import { ccChatApi } from '../api/ccChatClient'
import Modal from './Modal'
import { PanelLeftClose } from 'lucide-react'

const MODULE_ENTITIES: Record<ModuleKey, EntityType[]> = {
  controller: ['controller', 'material_registry'],
  kb: ['graph', 'note'],
  pm: ['plan'],
  agent: ['session', 'cc_session'],
  system: ['worker', 'team', 'material'],
  settings: ['settings'],
}

const MODULE_TITLE: Record<ModuleKey, string> = {
  controller: '总控',
  kb: '知识库',
  pm: '项目',
  agent: 'Agent 会话',
  system: '系统',
  settings: '设置',
}

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', width: '100%', background: '#0d0d0d', borderRight: '1px solid #222', flexShrink: 0, height: '100%', overflow: 'hidden' },
  header: { padding: '6px 8px 6px 12px', color: '#90caf9', fontSize: 14, borderBottom: '1px solid #222', fontFamily: 'Consolas, Menlo, monospace', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  headerTitle: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  iconButton: { width: 24, height: 24, border: '1px solid #333', borderRadius: 4, background: '#111', color: '#888', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0 },
  search: { margin: '6px 8px', padding: '4px 8px', background: '#111', border: '1px solid #333', borderRadius: 4, color: '#e0e0e0', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace' },
  list: { flex: 1, overflow: 'auto' },
  // group label: was #444 (almost invisible) — bump to #888 (still muted but readable)
  group: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 12px', color: '#888', fontSize: 14, marginTop: 8, textTransform: 'uppercase' as const, fontFamily: 'Consolas, Menlo, monospace' },
  groupAction: { background: 'transparent', border: 'none', color: '#90caf9', cursor: 'pointer', fontSize: 15, padding: '0 4px', lineHeight: 1 },
  item: (selected: boolean): React.CSSProperties => ({
    padding: '3px 12px', cursor: 'pointer', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace',
    // text was #bbb (light gray) → #d0d0d0 (closer to white) — main item names are emphasized
    color: selected ? '#90caf9' : '#d0d0d0', background: selected ? '#1a2a3a' : 'transparent',
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  }),
  meta: { color: '#888', fontSize: 14, marginLeft: 8 },
  empty: { padding: 12, color: '#888', fontSize: 14 },
  textarea: {
    width: '100%', minHeight: 100, background: '#111', border: '1px solid #333',
    borderRadius: 4, color: '#e0e0e0', padding: 8, fontSize: 14,
    fontFamily: 'Consolas, Menlo, monospace', resize: 'vertical' as const, boxSizing: 'border-box' as const,
  },
  hint: { color: '#666', fontSize: 14, marginTop: 6 },
}

export default function Sidebar({ module, onClose }: { module: ModuleKey; onClose?: () => void }) {
  const [items, setItems] = useState<Entity[]>([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)
  const [newSessionOpen, setNewSessionOpen] = useState(false)
  const [newSessionText, setNewSessionText] = useState('')
  const [newSessionPlan, setNewSessionPlan] = useState<string>('')
  const [planList, setPlanList] = useState<{ id: string; topic: string; date: string | null }[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [submitErr, setSubmitErr] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)
  const activeId = usePanels((s) => s.activeId)

  const types = MODULE_ENTITIES[module]

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setItems([])
    Promise.all(
      types.map(async (t) => {
        const reg = registry.get(t)
        if (!reg) return [] as Entity[]
        if (reg.renderer.SidebarView) return []
        try { return await reg.resolver.list() } catch (e) {
          console.error(`[Sidebar] resolver.list(${t}) failed:`, e)
          return []
        }
      }),
    ).then((groups) => {
      if (cancelled) return
      setItems(groups.flat())
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [module, reloadKey])

  const grouped = useMemo(() => {
    const f = filter.trim().toLowerCase()
    const g: Record<string, Entity[]> = {}
    for (const it of items) {
      if (f && !it.title.toLowerCase().includes(f) && !it.id.toLowerCase().includes(f)) continue
      const key = it.type
      g[key] = g[key] || []
      g[key].push(it)
    }
    return g
  }, [items, filter])

  // 模态打开时拉 plan 列表 (用户选 active_plan 关联给 session, agent 启动时读 plan.md frontmatter)
  useEffect(() => {
    if (!newSessionOpen) return
    fetch('/api/plans').then((r) => r.json()).then((d) => {
      const items = (d.items || []).filter((p: any) => !p.archived).slice(0, 50)
      setPlanList(items.map((p: any) => ({ id: p.id, topic: p.topic, date: p.date })))
    }).catch(() => setPlanList([]))
  }, [newSessionOpen])

  const submitNewSession = async () => {
    const text = newSessionText.trim()
    if (!text) return
    setSubmitting(true); setSubmitErr(null)
    try {
      const resp = await ideApi.send(null, text, {
        active_plan: newSessionPlan || null,
      })
      openTab({ type: 'session', id: resp.trace_id }, text.slice(0, 32))
      setNewSessionOpen(false)
      setNewSessionText('')
      setNewSessionPlan('')
      setReloadKey((k) => k + 1)
    } catch (e) {
      setSubmitErr(String(e))
    } finally { setSubmitting(false) }
  }

  // 默认创 chat session (claude-agent-sdk 包装本地 claude, 网页 chat UI).
  // 老 PTY 路线通过 spawnCcPtySession 入口创 (Shift 点击 / 后续菜单).
  const spawnCcSession = async (preferPty: boolean = false) => {
    try {
      if (preferPty) {
        const m = await ccApi.create({})
        const last = m.cwd.split(/[\\/]/).filter(Boolean).slice(-1)[0] || m.cwd
        openTab({ type: 'cc_session', id: m.id }, `${last} · pty · ${m.id.slice(0, 8)}`)
      } else {
        const m = await ccChatApi.create({})
        const last = m.cwd.split(/[\\/]/).filter(Boolean).slice(-1)[0] || m.cwd
        openTab({ type: 'cc_session', id: m.id }, `${last} · chat · ${m.id.slice(-6)}`)
      }
      setReloadKey((k) => k + 1)
    } catch (e) {
      setSubmitErr(String(e))
    }
  }

  return (
    <div style={S.root}>
      <div style={S.header}>
        <span style={S.headerTitle}>{MODULE_TITLE[module]}</span>
        {onClose && (
          <button type="button" title="关闭左侧栏" aria-label="关闭左侧栏" data-shell-sidebar-close style={S.iconButton} onClick={onClose}>
            <PanelLeftClose size={15} strokeWidth={1.8} />
          </button>
        )}
      </div>
      {types.length > 0 && (
        <input
          style={S.search}
          placeholder="过滤..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}
      <div style={S.list}>
        {types.length === 0 && <div style={S.empty}>占位</div>}
        {loading && <div style={S.empty}>加载中...</div>}
        {!loading && types.map((t) => {
          const reg = registry.get(t)
          const SidebarView = reg?.renderer.SidebarView
          const list = grouped[t] || []
          const action = t === 'session' ? (
            <button
              style={S.groupAction}
              title="新建 agent 会话"
              onClick={() => setNewSessionOpen(true)}
            >+</button>
          ) : t === 'cc_session' ? (
            <button
              style={S.groupAction}
              title="启动 Claude Code 网页 chat 会话 (默认). Shift+点击 改启动 PTY 终端模式"
              data-cc-spawn
              onClick={(e) => spawnCcSession(e.shiftKey)}
            >+</button>
          ) : null
          return (
            <div key={t}>
              <div style={S.group}>
                <span>{reg?.label || t}{!SidebarView && ` · ${list.length}`}</span>
                {action}
              </div>
              {SidebarView ? (
                <SidebarView filter={filter} activeId={activeId} openTab={openTab} />
              ) : list.length === 0 ? (
                <div style={{ ...S.empty, padding: '2px 12px' }}>(空)</div>
              ) : list.map((it) => {
                const tabId = `${it.type}:${it.id}`
                const isRecoverableCc = it.type === 'cc_session' && (it as any).status === 'recoverable'
                const handleClick = async () => {
                  if (!isRecoverableCc) {
                    openTab(it, it.title)
                    return
                  }
                  // Resume: spawn a fresh PTY pointing at the same claude conversation,
                  // open the NEW pty's tab.
                  try {
                    const fresh = await ccApi.resume(it.id)
                    const last = fresh.cwd.split(/[\\/]/).filter(Boolean).slice(-1)[0] || fresh.cwd
                    openTab({ type: 'cc_session', id: fresh.id }, `${last} · ${fresh.id.slice(0, 8)}`)
                    setReloadKey((k) => k + 1)
                  } catch (e) {
                    setSubmitErr(`resume failed: ${e}`)
                  }
                }
                return (
                  <div
                    key={it.id}
                    style={S.item(activeId === tabId)}
                    onClick={handleClick}
                    data-cc-status={isRecoverableCc ? 'recoverable' : undefined}
                    title={isRecoverableCc ? `已断, 点击 resume (${it.id})` : it.id}
                  >
                    {isRecoverableCc ? '↻ ' : ''}{it.title}
                    <span style={S.meta}>{(it.tags || []).join(' ')}</span>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
      <Modal
        title="新建 agent 会话"
        open={newSessionOpen}
        onClose={() => { setNewSessionOpen(false); setSubmitErr(null) }}
        onConfirm={submitting ? undefined : submitNewSession}
        confirmLabel={submitting ? '创建中...' : '创建'}
      >
        <div style={{ marginBottom: 8 }}>
          <label style={{ color: '#888', fontSize: 14, display: 'block', marginBottom: 4 }}>
            关联 plan (可选, 决定 work_type / standards / 退出条件)
          </label>
          <select
            value={newSessionPlan}
            onChange={(e) => setNewSessionPlan(e.target.value)}
            data-new-session-plan
            style={{
              width: '100%', padding: '4px 8px', background: '#111',
              border: '1px solid #333', borderRadius: 4, color: '#e0e0e0',
              fontSize: 14, fontFamily: 'Consolas, Menlo, monospace',
              boxSizing: 'border-box',
            }}
          >
            <option value="">(无 plan, 仅注入 PROGRESS.md 头)</option>
            {planList.map((p) => (
              <option key={p.id} value={p.id}>
                {p.date ? `[${p.date}] ` : ''}{p.topic}
              </option>
            ))}
          </select>
        </div>
        <textarea
          style={S.textarea}
          autoFocus
          placeholder="输入首条消息 (会作为 task description)..."
          value={newSessionText}
          onChange={(e) => setNewSessionText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submitNewSession()
          }}
        />
        <div style={S.hint}>Ctrl/Cmd + Enter 提交 · Esc 取消</div>
        {submitErr && <div style={{ color: '#ef5350', marginTop: 8, fontSize: 14 }}>{submitErr}</div>}
      </Modal>
    </div>
  )
}
