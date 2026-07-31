/**
 * Unified Session Context Panel — cc_session 跟 native session 共用 (S16 round 2 + S6 round 4).
 *
 * 三段折叠结构 (per user round 21 b 原话: "这部分统计界面请保持结构性"):
 *   1. 上下文  : active plan + plan_meta (work_type / standards / project / 退出条件) + cwd + agent state
 *   2. 修改记录: files edited + bash redirects (从 trace events 聚合)
 *   3. 新增产出: new worker / material files
 *
 * 数据源 by kind:
 *   - 'cc'    : ccApi.context(id)         → /api/cc/sessions/{id}/context
 *   - 'native': ideApi.context(traceId)   → /api/v2/ide/trace/{id}/context
 *
 * 两端 schema 完全对齐 (UnifiedSessionContext = ccClient.SessionContext = ideClient.SessionContext).
 *
 * Auto-refreshes every 5s while alive.
 *
 * 2026-07-19 蓝图 G 重置(阶段四第四波; analysis/cc_session.md 逐条核销):
 *   · 折叠段=厚框纸件(2px 白框);空段(计数 0)收成一行摘要(虚线未探索边界,不渲染 body,
 *     data-ctx-section 契约保留);「注入上下文」改默认折叠 + 按来源分组 + >10 给过滤框。
 *   · state=状态徽章(与页头 alive/ended 同一套 v2-status 语言);resolver ok/missing=徽章;
 *     字段空值行((plan 未设)/(plan 未列))整行不渲染;说明文字收进 ⓘ tooltip。
 *   · 切 plan=显示当前值的虚线 select(控件即当前态,消灭"值文本+切按钮"割裂两件套);
 *     VS 按钮=行尾 icon hover 才现;路径=真 button 链接态(不再是伪装可点的裸文字)。
 *   · 文字面平滑纸面禁纹理;mono 只给路径/计数/标注;零玻璃。数据接线/handler 全保留。
 */

import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { RefreshCw, Check, Circle, ChevronDown, ChevronRight, ChevronUp, Ban, Info, Code2, Search, ExternalLink } from 'lucide-react'
import { ccApi, type ResolvedContextItem, type SessionContext as CcContext } from '../../api/ccClient'
import { ideApi, type SessionContext as IdeContext } from '../../api/ideClient'
import { usePanels } from '../../stores/panelsStore'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import NotesForTarget from '../authored/NotesForTarget'
import './cc_session.css'

type UnifiedContext = CcContext | IdeContext

interface Props {
  sessionId: string
  alive: boolean  // poll faster when alive; static if dead
  kind?: 'cc' | 'native'  // 默认 cc (向后兼容 round 27 caller)
}

/** Strip leading repo prefix to keep file paths skimmable.
 *  兼容仓库改名 (omnifactory → omnicompany 2026-05-08): 匹配任一目录名 */
function shortPath(p: string): string {
  const m = p.match(/[\\/]omni(?:factory|company)[\\/](.+)$/)
  return m ? m[1].replace(/\\/g, '/') : p
}

/** plan_id `_infra/dashboard/[2026-05-03]X` → `X`(切 plan 控件当前态显示用) */
function planShort(planId: string): string {
  const last = planId.split('/').pop() || planId
  return last.replace(/^\[\d{4}-\d{2}-\d{2}\]/, '')
}

function postToOmniHost(message: Record<string, unknown>): boolean {
  const payload = { __omnichat: true, ...message }
  let posted = false
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage(payload, '*')
      posted = true
    }
  } catch { /* browser fallback */ }
  try {
    if (window.top && window.top !== window && window.top !== window.parent) {
      window.top.postMessage(payload, '*')
      posted = true
    }
  } catch { /* browser fallback */ }
  return posted
}

function providerLabel(provider: string | null | undefined, kind: 'cc' | 'native'): string {
  if (kind === 'native') return 'Native Agent'
  if (provider === 'codex') return 'Codex'
  if (provider === 'omni_agent') return 'OmniAgent'
  if (provider === 'claude_code') return 'Claude Code'
  if (provider === 'chat') return 'OmniChat'
  return 'OmniChat'
}

function pathToNoteId(filePath: string): string | null {
  // docs/foo/bar.md → foo/bar
  const m = filePath.match(/[\\/]docs[\\/](.+)\.md$/i)
  if (!m) return null
  return m[1].replace(/\\/g, '/')
}

/** agent_state 文字 → v2-status 态(与页头 alive/ended 同一套状态语言的两个切面)。 */
function stateCls(agentState: string): string {
  const s = agentState.toLowerCase()
  if (/err|fail/.test(s)) return 'st-err'
  if (/run|think|active|work/.test(s)) return 'st-ok'
  if (/wait/.test(s)) return 'st-hollow'
  return 'st-idle'
}

const Section: React.FC<{ title: string; count?: number; children?: React.ReactNode; defaultOpen?: boolean; testId?: string; empty?: boolean }> =
  ({ title, count, children, defaultOpen = true, testId, empty = false }) => {
    const [open, setOpen] = useState(defaultOpen)
    // 空段(计数 0) = 一行摘要: 虚线未探索边界,不渲染 body(空容器不给常驻占位)。
    if (empty) {
      return (
        <div className="cs-section" data-empty="1" data-ctx-section={testId}>
          <div className="cs-sechead" aria-disabled="true">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              {title}{typeof count === 'number' && <span className="v2-count">{count}</span>}
            </span>
          </div>
        </div>
      )
    }
    return (
      <div className="cs-section" data-ctx-section={testId}>
        <button type="button" className="cs-sechead" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className="chev" aria-hidden="true">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
          {title}{typeof count === 'number' && <span className="v2-count">{count}</span>}
        </button>
        {open && <div className="cs-secbody">{children}</div>}
      </div>
    )
  }


// ─── PlanPicker · 切 plan 下拉 (CC-PLAN-SESSION-CONTEXT 段三-2) ─────
//
// 蓝图 G: 触发钮=显示当前 plan 短名的虚线 select(控件本身显示当前值,
// 消灭旧版"值文本 + 切按钮"割裂两件套)。点开的下拉照旧列 /api/plans 非 archived plan,
// 选中调 ccApi.patchActivePlan → alive session 下条 turn UserPromptSubmit hook 重注入 plan_meta。

interface PlanPickerProps {
  sessionId: string
  currentPlanId: string | null
  alive: boolean
  onChange: () => void
}

const _planListCache: { items: any[]; ts: number } = { items: [], ts: 0 }

const PlanPicker: React.FC<PlanPickerProps> = ({ sessionId, currentPlanId, alive, onChange }) => {
  const [open, setOpen] = useState(false)
  const [plans, setPlans] = useState<any[]>(_planListCache.items)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null) // plan_id being switched to
  const [toast, setToast] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  // Portal 渲下拉避免父级 overflow:auto 裁剪 — 用 fixed 定位锚到按钮
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null)
  useEffect(() => {
    if (!open) { setAnchorRect(null); return }
    const update = () => { if (btnRef.current) setAnchorRect(btnRef.current.getBoundingClientRect()) }
    update()
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [open])

  const loadPlans = async () => {
    if (Date.now() - _planListCache.ts < 30_000 && _planListCache.items.length > 0) {
      setPlans(_planListCache.items)
      return
    }
    setLoading(true); setError(null)
    try {
      const r = await fetch('/api/plans')
      if (!r.ok) throw new Error(`${r.status}`)
      const d = await r.json() as { items: any[] }
      const items = (d.items || []).filter((p) => !p.archived && p.has_plan_md)
      // sort by date desc (matches CLI omni plan list default)
      items.sort((a, b) => (b.date || '').localeCompare(a.date || ''))
      _planListCache.items = items; _planListCache.ts = Date.now()
      setPlans(items)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const onToggle = () => {
    const next = !open
    setOpen(next); setError(null); setToast(null); setFilter('')
    if (next) loadPlans()
  }

  const onSwitch = async (planId: string | null) => {
    setBusy(planId || '__unbind__'); setError(null)
    try {
      const res = await ccApi.patchActivePlan(sessionId, planId)
      onChange()
      const verb = planId ? '切到' : '解绑'
      const when = res.alive ? '下条 turn 自动注入' : '已生效'
      setToast(`${verb} ${planId ? planId.split('/').pop() : '(无)'} · ${when}`)
      setOpen(false)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(null)
    }
  }

  const filtered = plans.filter((p) => {
    if (!filter) return true
    const q = filter.toLowerCase()
    return p.id.toLowerCase().includes(q) || (p.topic || '').toLowerCase().includes(q)
  })

  return (
    <span style={{ display: 'inline-block', position: 'relative', maxWidth: '100%' }}>
      <button
        ref={btnRef}
        type="button"
        onClick={onToggle}
        data-plan-picker-toggle
        data-ctx-active-plan
        aria-expanded={open}
        title={alive ? `切 plan (下条 turn 自动注入新 plan_meta)\n当前: ${currentPlanId || '(未关联)'}` : `切 plan (立即生效)\n当前: ${currentPlanId || '(未关联)'}`}
        className="cs-planpick"
      >
        <span className={`pv${currentPlanId ? '' : ' unset'}`}>{currentPlanId ? planShort(currentPlanId) : '关联 plan'}</span>
        {open ? <ChevronUp size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />}
      </button>
      {toast && (
        <div data-plan-picker-toast style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 10,
          padding: '4px 8px', background: 'color-mix(in srgb, var(--fp-ok) 16%, var(--fp-solid))', color: 'var(--fp-ok)',
          border: '1px solid var(--fp-border)', borderRadius: 'var(--fp-r1)', fontSize: 'var(--fp-fs-4)', whiteSpace: 'nowrap' as const,
          maxWidth: 320,
        }}>
          <Check size={13} aria-hidden style={{ verticalAlign: -2 }} /> {toast}
        </div>
      )}
      {open && anchorRect && createPortal(
        <div data-plan-picker-dropdown style={{
          position: 'fixed',
          // 锚到按钮下方右对齐, 不被父级 overflow 裁
          top: Math.min(anchorRect.bottom + 4, window.innerHeight - 420),
          right: Math.max(8, window.innerWidth - anchorRect.right),
          zIndex: 10000,
          minWidth: 340, maxWidth: 440, maxHeight: 420, overflow: 'auto',
          background: 'var(--fp-solid)', border: 'var(--fp-bp-frame-w) solid var(--fp-border-strong)',
          borderRadius: 'var(--fp-r2)',
          padding: 8, boxShadow: 'var(--fp-bp-shadow-pop)',
          fontFamily: 'var(--fp-font-sans)',
        }}>
          <input
            type="text" placeholder="过滤 plan id / topic..."
            value={filter} onChange={(e) => setFilter(e.target.value)}
            autoFocus
            style={{
              width: '100%', padding: '6px 10px', marginBottom: 4, boxSizing: 'border-box' as const,
              background: 'var(--fp-card)', color: 'var(--fp-text)', border: '1px dashed var(--fp-border)',
              borderRadius: 'var(--fp-r1)', fontSize: 'var(--fp-fs-3)', fontFamily: 'var(--fp-font-sans)',
            }}
          />
          {error && <div style={{ color: 'var(--fp-err)', fontSize: 'var(--fp-fs-3)', padding: 4 }}>err: {error}</div>}
          {loading && <div style={{ color: 'var(--fp-text-3)', fontSize: 'var(--fp-fs-3)', padding: 4 }}>loading…</div>}
          {currentPlanId && (
            <button
              type="button"
              onClick={() => onSwitch(null)} disabled={busy !== null}
              data-plan-picker-unbind
              style={{
                width: '100%', textAlign: 'left' as const, padding: '6px 10px', marginBottom: 4,
                background: 'color-mix(in srgb, var(--fp-err) 14%, var(--fp-solid))', color: 'var(--fp-err)', border: '1px solid var(--fp-border)',
                borderRadius: 'var(--fp-r1)', cursor: 'pointer', fontSize: 'var(--fp-fs-3)', fontFamily: 'var(--fp-font-sans)',
              }}
            >
              {busy === '__unbind__' ? <RefreshCw size={12} aria-hidden style={{ verticalAlign: -2 }} /> : <Ban size={12} aria-hidden style={{ verticalAlign: -2 }} />} 解绑 (active_plan = null)
            </button>
          )}
          {filtered.length === 0 && !loading && (
            <div style={{ color: 'var(--fp-text-3)', fontSize: 'var(--fp-fs-3)', padding: 4 }}>(无可选 plan)</div>
          )}
          {(() => {
            // 按 category 分组
            const groups: Record<string, typeof filtered> = {}
            for (const p of filtered) {
              const cat = p.category || '(未分类)'
              ;(groups[cat] ||= []).push(p)
            }
            const sortedCats = Object.keys(groups).sort()
            return sortedCats.map((cat) => (
              <div key={cat} style={{ marginBottom: 4 }}>
                <div style={{
                  padding: '4px 8px', fontSize: 'var(--fp-fs-4)', color: 'var(--fp-text-3)', fontWeight: 600,
                  fontFamily: 'var(--fp-font-mono)', textTransform: 'uppercase' as const, letterSpacing: '.1em',
                  borderBottom: '1px solid var(--fp-border)', marginBottom: 2,
                  position: 'sticky' as const, top: 0, background: 'var(--fp-solid)', zIndex: 1,
                }}>
                  {cat}
                </div>
                {groups[cat].map((p) => {
                  const isCurrent = p.id === currentPlanId
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => !isCurrent && onSwitch(p.id)}
                      disabled={isCurrent || busy !== null}
                      data-plan-picker-option={p.id}
                      title={p.id}
                      style={{
                        display: 'block', width: '100%', textAlign: 'left' as const,
                        padding: '4px 8px 4px 12px', marginBottom: 1,
                        background: isCurrent ? 'var(--fp-bp-hatch), var(--fp-bp-paper-2)' : 'transparent',
                        color: isCurrent ? 'var(--fp-text)' : 'var(--fp-text-2)',
                        border: '1px solid', borderColor: isCurrent ? 'var(--fp-border-strong)' : 'transparent',
                        borderRadius: 'var(--fp-r1)', cursor: isCurrent ? 'default' : 'pointer',
                        fontSize: 'var(--fp-fs-3)', fontFamily: 'var(--fp-font-sans)',
                        overflow: 'hidden' as const, textOverflow: 'ellipsis' as const, whiteSpace: 'nowrap' as const,
                      }}
                    >
                      {busy === p.id && <RefreshCw size={12} aria-hidden style={{ verticalAlign: -2, marginRight: 4 }} />}
                      {isCurrent && <Circle size={8} fill="currentColor" stroke="none" aria-hidden style={{ verticalAlign: 1, marginRight: 4 }} />}
                      <span style={{ color: 'var(--fp-text-3)' }}>{p.date}</span>{' '}
                      <span style={{ fontWeight: isCurrent ? 600 as const : 400 as const }}>{p.topic}</span>
                      {p.meta?.work_type && (
                        <span style={{ marginLeft: 6, color: 'var(--fp-accent-2)', fontSize: 'var(--fp-fs-4)' }}>· {p.meta.work_type}</span>
                      )}
                    </button>
                  )
                })}
              </div>
            ))
          })()}
          <div style={{ marginTop: 4, padding: 4, color: 'var(--fp-text-3)', fontSize: 'var(--fp-fs-4)', borderTop: '1px solid var(--fp-border)' }}>
            {alive
              ? 'alive 进程: 切完后下条 turn 自动注入 (b 方案, 不破缓存)'
              : '已结束 session: 立即写入 (resume 时生效)'}
          </div>
        </div>,
        document.body
      )}
    </span>
  )
}

export default function SessionContextPanel({ sessionId, alive, kind = 'cc' }: Props) {
  const [ctx, setCtx] = useState<UnifiedContext | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ctxFilter, setCtxFilter] = useState('')  // 「注入上下文」>10 项时的过滤框
  const openTab = usePanels((s) => s.openTab)

  const reload = React.useCallback(() => {
    const fetcher = kind === 'native'
      ? ideApi.context(sessionId)
      : ccApi.context(sessionId)
    // 5s 轮询: 上下文没变就保持旧引用 → 侧栏不每 5s 重渲染一次。
    fetcher
      .then((next) => setCtx((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next)))
      .catch((e) => setError(String(e)))
  }, [sessionId, kind])

  useEffect(() => {
    setCtx(null); setError(null)
    reload()
  }, [sessionId, reload])

  // Poll while alive (5s); pause when session dead
  useEffect(() => {
    if (!alive) return
    const id = window.setInterval(reload, 5000)
    return () => window.clearInterval(id)
  }, [alive, reload])

  if (error) return <div className="cs-root" style={{ padding: 16, color: 'var(--fp-err)' }}>{error}</div>
  if (!ctx) return <div className="cs-root" style={{ padding: 16, color: 'var(--fp-text-3)' }}>loading…</div>

  const c = ctx.context
  // 上下文真信息源 = plan.md frontmatter (plan-level) + project.md frontmatter (project-level)
  const planMeta: Record<string, any> = (c as any).plan_meta || {}
  const projectMeta: Record<string, any> = (c as any).project_meta || {}
  const userCtx = c.user_context || {}
  const resolvedContext = (c as any).resolved_context
  const workType = planMeta.work_type || userCtx.work_type
  const standards: string[] = planMeta.standards || userCtx.standards || []
  const project: string | undefined = planMeta.project
  const exitCriteria: string[] = planMeta.exit_criteria || []
  const projectVision: string[] = projectMeta.vision || []
  const projectExitCriteria: string[] = projectMeta.exit_criteria || []
  const agentState = (c as any).agent_state || (kind === 'cc' ? 'cc-session' : 'native-session')
  const provider = (c as any).provider as string | null | undefined
  const providerName = providerLabel(provider, kind)

  const openPlan = () => {
    if (c.active_plan) openTab({ type: 'plan', id: c.active_plan }, c.active_plan.split('/').pop() || c.active_plan)
  }
  const openNoteIfMd = (filePath: string) => {
    const id = pathToNoteId(filePath)
    if (id) openTab({ type: 'note', id }, id.split('/').pop() || id)
  }
  const openContextTarget = (item: ResolvedContextItem) => {
    const target = item.dashboard_target
    if (target?.type === 'plan') {
      openTab({ type: 'plan', id: target.id }, target.id.split('/').pop() || target.id)
      return
    }
    if (target?.type === 'note') {
      openTab({ type: 'note', id: target.id }, target.id.split('/').pop() || target.id)
      return
    }
    const path = item.abs_path || item.path
    if (postToOmniHost({ type: 'open-file', path })) return
    if (item.vscode_uri) {
      window.open(item.vscode_uri, '_blank', 'noopener,noreferrer')
    }
  }
  const openContextInVscode = (item: ResolvedContextItem) => {
    const path = item.abs_path || item.path
    if (postToOmniHost({ type: 'open-file', path })) return
    if (item.vscode_uri) {
      window.open(item.vscode_uri, '_blank', 'noopener,noreferrer')
    }
  }

  // 「注入上下文」列表: 过滤 + 按来源分组(standards_index/registries/templates…)
  const ctxItems: ResolvedContextItem[] = resolvedContext?.contexts || []
  const ctxFiltered = ctxItems.filter((it) => {
    const q = ctxFilter.trim().toLowerCase()
    if (!q) return true
    return `${it.path} ${it.reason || ''} ${it.source || ''}`.toLowerCase().includes(q)
  })
  const ctxGroups: Array<{ source: string; items: ResolvedContextItem[] }> = []
  for (const it of ctxFiltered.slice(0, 80)) {
    const src = it.source || 'resolver'
    const last = ctxGroups[ctxGroups.length - 1]
    if (last && last.source === src) last.items.push(it)
    else ctxGroups.push({ source: src, items: [it] })
  }

  const modifiedCount = ctx.modified_files.length
  const addedCount = ctx.added_workers.length + ctx.added_materials.length

  return (
    <div className="cs-root" data-session-context-panel data-session-id={sessionId} data-session-kind={kind}>
      {/* 无标题头: 仅留一条贴顶 chrome 工具条 — provider 弱标识 + 低频刷新收 ⋯。 */}
      <div className="cs-tools">
        <span className="cs-provider">{providerName}</span>
        <KebabMenu testid="session-ctx-actions" items={[
          { label: '刷新', icon: <RefreshCw size={15} />, testid: 'session-ctx-refresh', onClick: reload },
        ] as KebabItem[]} />
      </div>

      {/* ── 上下文 ─────────────────────────────────────────── */}
      <Section title="上下文" testId="context">
        <div className="cs-kv">
          <span className="cs-k">state</span>
          <span className="cs-v"><span className={`v2-status ${stateCls(agentState)}`}><i className="led" aria-hidden />{agentState}</span></span>
        </div>
        <div className="cs-kv">
          <span className="cs-k">active plan</span>
          <span className="cs-v">
            {kind === 'cc' ? (
              <>
                <PlanPicker
                  sessionId={sessionId}
                  currentPlanId={c.active_plan || null}
                  alive={alive}
                  onChange={() => reload()}
                />
                {c.active_plan && (
                  <button type="button" className="v2-iconbtn" style={{ width: 24, height: 24, marginLeft: 4, verticalAlign: '-6px' }} aria-label="打开 plan" title={c.active_plan} onClick={openPlan}>
                    <ExternalLink size={12} aria-hidden />
                  </button>
                )}
              </>
            ) : (
              c.active_plan
                ? <button type="button" className="cs-link" data-ctx-active-plan onClick={openPlan}>{c.active_plan}</button>
                : <span className="cs-empty">(未关联)</span>
            )}
          </span>
        </div>
        <div className="cs-kv">
          <span className="cs-k">cwd</span>
          <span className="cs-v" data-ctx-cwd title={c.cwd || ''}>{c.cwd ? shortPath(c.cwd) : '?'}</span>
        </div>
        {c.claude_session_id && (
          <div className="cs-kv">
            <span className="cs-k">claude id</span>
            <span className="cs-v" style={{ fontFamily: 'var(--fp-font-mono)' }} title={c.claude_session_id}>{c.claude_session_id.slice(0, 12)}…</span>
          </div>
        )}
        {project && (
          <div className="cs-kv">
            <span className="cs-k">project</span>
            <span className="cs-v">{project}</span>
          </div>
        )}
        {/* 空值行((plan 未设)/(plan 未列))整行不渲染,不再占无信息占位 */}
        {workType && (
          <div className="cs-kv">
            <span className="cs-k">work type</span>
            <span className="cs-v">{workType}</span>
          </div>
        )}
        {standards.length > 0 && (
          <div className="cs-kv">
            <span className="cs-k">standards</span>
            <span className="cs-v">
              {standards.map((s, i) => <span key={i} className="cs-chip">{s}</span>)}
            </span>
          </div>
        )}
        {exitCriteria.length > 0 && (
          <div className="cs-kv">
            <span className="cs-k">退出条件</span>
            <span className="cs-v">
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {exitCriteria.map((e, i) => <li key={i} style={{ fontSize: 'var(--fp-fs-3)' }}>{e}</li>)}
              </ul>
            </span>
          </div>
        )}
        {/* native 专有: model / turn / token 用量(空值行同样不收) */}
        {kind === 'native' && (ctx as any).stats && (
          <>
            {(ctx as any).stats.model && (
              <div className="cs-kv">
                <span className="cs-k">model</span>
                <span className="cs-v" style={{ fontFamily: 'var(--fp-font-mono)' }}>{(ctx as any).stats.model}</span>
              </div>
            )}
            <div className="cs-kv">
              <span className="cs-k">turn</span>
              <span className="cs-v" style={{ fontFamily: 'var(--fp-font-mono)' }}>{(ctx as any).stats.turn_count}</span>
            </div>
            <div className="cs-kv">
              <span className="cs-k">tokens</span>
              <span className="cs-v" style={{ fontFamily: 'var(--fp-font-mono)' }}>
                {(ctx as any).stats.total_tokens.toLocaleString()}
                <span style={{ color: 'var(--fp-text-3)', fontSize: 'var(--fp-fs-4)', marginLeft: 6 }}>
                  ({(ctx as any).stats.input_tokens.toLocaleString()} in / {(ctx as any).stats.output_tokens.toLocaleString()} out)
                </span>
              </span>
            </div>
          </>
        )}
        {/* 说明文字收进 ⓘ tooltip(不再常驻 3 行灰字) */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 2 }}>
          <button
            type="button"
            className="cs-hintico"
            aria-label="字段来源说明"
            title="字段值来自 plan.md 顶部 frontmatter (work_type / standards / project / exit_criteria) — 编辑 plan.md 即改值, 无私有 user_context"
          >
            <Info size={13} aria-hidden />
          </button>
        </div>
      </Section>

      {/* ── 札记 (针对本会话的评论/草稿, 中心 store 回显) ──────────── */}
      <Section title="札记" defaultOpen={false} testId="authored">
        <NotesForTarget
          kind="llm_session"
          id={sessionId}
          title={(c.active_plan && c.active_plan.split('/').pop()) || sessionId.slice(0, 12)}
        />
      </Section>

      {/* ── 渐进上下文注入包(默认折叠;>10 项给过滤框;按来源分组) ─────────── */}
      {resolvedContext && (
        <Section
          title="注入上下文"
          count={resolvedContext.total || 0}
          defaultOpen={false}
          testId="progressive-context"
        >
          <div className="cs-kv">
            <span className="cs-k">resolver</span>
            <span className="cs-v">
              omni context resolve{' '}
              {resolvedContext.missing_total ? (
                <span className="v2-status st-warn"><i className="led" aria-hidden />missing {resolvedContext.missing_total}</span>
              ) : (
                <span className="v2-status st-ok"><i className="led" aria-hidden />ok</span>
              )}
            </span>
          </div>
          {resolvedContext.error && (
            <div style={{ color: 'var(--fp-err)', fontSize: 'var(--fp-fs-4)', marginBottom: 8 }}>
              {resolvedContext.error}
            </div>
          )}
          {ctxItems.length > 10 && (
            <div className="cs-filter">
              <label className="v2-search">
                <Search size={13} aria-hidden />
                <input
                  placeholder={`过滤 ${ctxItems.length} 条注入路径…`}
                  value={ctxFilter}
                  onChange={(e) => setCtxFilter(e.target.value)}
                  data-testid="ctx-inject-filter"
                />
              </label>
            </div>
          )}
          {ctxGroups.map((g) => (
            <div key={g.source}>
              <div className="cs-grplabel">{g.source}</div>
              {g.items.map((item: ResolvedContextItem) => (
                <div key={`${item.path}:${item.source}:${item.reason}`} className="cs-frow" data-ctx-context-path={item.path}>
                  <button
                    type="button"
                    className="cs-path"
                    title={`${item.path}\n${item.reason || ''}`}
                    onClick={() => openContextTarget(item)}
                  >
                    {item.path}
                  </button>
                  <button
                    type="button"
                    className="v2-iconbtn cs-vs"
                    style={{ width: 26, height: 26 }}
                    title="在 VS Code / 宿主编辑器中打开"
                    aria-label="在 VS Code / 宿主编辑器中打开"
                    onClick={() => openContextInVscode(item)}
                    data-ctx-open-vscode
                  >
                    <Code2 size={13} aria-hidden />
                  </button>
                </div>
              ))}
            </div>
          ))}
          {ctxFiltered.length === 0 && <div className="cs-empty">无匹配路径</div>}
          {ctxFiltered.length > 80 && (
            <div className="cs-empty">+{ctxFiltered.length - 80} more …</div>
          )}
          <div className="cs-foot" style={{ textAlign: 'left', padding: '6px 0 0' }}>
            点击 markdown/plan 路径会在网页内打开；VS 图标走 VS Code host bridge(浏览器 fallback 到 vscode://file 链接)。
          </div>
        </Section>
      )}

      {/* ── project 上下文 (立于 plan 之上, 含 vision + 退出条件) ─────── */}
      {(projectVision.length > 0 || projectExitCriteria.length > 0) && (
        <Section title={`Project · ${project || '?'}`} testId="project">
          {projectVision.length > 0 && (
            <div className="cs-kv">
              <span className="cs-k">vision</span>
              <span className="cs-v">
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {projectVision.map((v, i) => <li key={i} style={{ fontSize: 'var(--fp-fs-3)' }}>{v}</li>)}
                </ul>
              </span>
            </div>
          )}
          {projectExitCriteria.length > 0 && (
            <div className="cs-kv">
              <span className="cs-k">退出条件 (project)</span>
              <span className="cs-v">
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {projectExitCriteria.map((e, i) => <li key={i} style={{ fontSize: 'var(--fp-fs-3)' }}>{e}</li>)}
                </ul>
              </span>
            </div>
          )}
          <div className="cs-foot" style={{ textAlign: 'left', padding: '6px 0 0' }}>
            来自 project.md frontmatter — 立于 plan 之上, 跨 plan 共享 vision + 退出条件
          </div>
        </Section>
      )}

      {/* ── 修改记录(空段收成一行摘要) ─────────────────────────── */}
      <Section
        title="修改记录"
        count={modifiedCount}
        defaultOpen={modifiedCount > 0}
        testId="modified"
        empty={modifiedCount === 0}
      >
        {ctx.modified_files.slice(0, 50).map((f) => {
          const md = pathToNoteId(f.path)
          return (
            <div key={f.path} className="cs-frow" data-ctx-modified={f.path}>
              <button
                type="button"
                className={`cs-path${md ? '' : ' plain'}`}
                title={`${f.path}\n${f.last_tool} · last ${f.last_ts}`}
                onClick={() => openNoteIfMd(f.path)}
              >
                {shortPath(f.path)}
              </button>
              <span className="cs-fcount">×{f.count}</span>
            </div>
          )
        })}
        {ctx.modified_files.length > 50 && (
          <div className="cs-empty">+{ctx.modified_files.length - 50} more …</div>
        )}
        {ctx.bash_writes.length > 0 && (
          <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px dashed var(--fp-border-subtle)' }}>
            <div className="cs-grplabel">bash 写入 ({ctx.bash_writes.length})</div>
            {ctx.bash_writes.slice(0, 10).map((b, i) => (
              <div key={i} className="cs-frow" data-ctx-bash-write>
                <span className="cs-path plain" style={{ color: 'var(--fp-warn)' }} title={b.snippet}>{shortPath(b.path)}</span>
                <span className="cs-fcount">{b.ts.slice(11, 19)}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ── 新增产出 (worker/material;空段收成一行摘要) ─────────────── */}
      <Section
        title="新增产出"
        count={addedCount}
        defaultOpen={addedCount > 0}
        testId="added"
        empty={addedCount === 0}
      >
        {ctx.added_workers.length > 0 && (
          <div style={{ marginBottom: 6 }}>
            <div className="cs-grplabel" style={{ color: 'var(--fp-link)' }}>worker / team</div>
            {ctx.added_workers.map((p) => (
              <div key={p} className="cs-frow" data-ctx-added-worker>
                <span className="cs-path plain" title={p}>{shortPath(p)}</span>
              </div>
            ))}
          </div>
        )}
        {ctx.added_materials.length > 0 && (
          <div>
            <div className="cs-grplabel" style={{ color: 'var(--fp-accent-2)' }}>material</div>
            {ctx.added_materials.map((p) => (
              <div key={p} className="cs-frow" data-ctx-added-material>
                <span className="cs-path plain" title={p}>{shortPath(p)}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <div className="cs-foot">
        共 {ctx.event_count} 个 trace 事件
      </div>
    </div>
  )
}
