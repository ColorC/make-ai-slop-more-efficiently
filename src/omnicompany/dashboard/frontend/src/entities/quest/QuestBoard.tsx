// 任务窗口（驾驶舱主区第 2 个固定页签）— 本地目标管理系统 whatnow 的视图（仿 Leantime）。
// 2026-06-30 frostpane 面板重做（信息/交互/布局重排，行为零变化）：
//  - 无标题头：身份由页签标识，内容从顶部开始；刷新做成右上角浮动小图标。
//  - root 透明吃全局冷渐变；任务线卡 = 磨砂玻璃卡（var(--fp-glass)+blur+rim+r11）。
//  - 交互层级：每行不再一排等权按钮，主操作=点标题打开；低频（复制/置顶/归档）收进行内 ⋯ KebabMenu；
//    任务线级的低频（置顶整条线/显示已归档）收进卡头 ⋯，主操作=点标题展开。
//  - 颜色全 var(--fp-*)（金色映射到 var(--fp-warn) 冷琥珀，保留“进行中/置顶”语义）。
//  - 扁平+归档语义保持：所有任务线一个扁平列表，置顶原地浮到全局最前；进度≥90% 自动归档，
//    顶部 ⋯ 勾「显示已归档」才出现，且每条线里归档仍需点开。
// 数据：GET whatnow /api/board(?archived=1)；置顶 POST /api/pin。

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, ChevronRight, ChevronDown, Download, Pin, Archive, ArchiveRestore, Copy, FolderOpen, History, Inbox } from 'lucide-react'
import { questsApi, type Board, type GoalNode, type TaskNode } from '../../api/questsClient'
import { usePanels } from '../../stores/panelsStore'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import Tooltip from '../../shared/view/ui/Tooltip'
import ActiveConvoBadge from '../../shared/view/ui/ActiveConvoBadge'
import { bossSightApi, type BossSightBindingBucket } from '../../api/bossSightClient'
import { copyText } from '../../lib/copyText'

function txt(v: unknown): string { return v == null ? '' : typeof v === 'string' ? v : String(v) }
/** 进度文案里 recorder 写入的「[plan-progress-recorder] 」类前缀对人无信息量，展示时剥掉。 */
function stripTag(s: string): string { return s.replace(/^\s*\[[^\]]{1,40}\]\s*/, '') }
// plan_id = omnicompany docs/plans 下的相对目录（whatnow 同步管线 _discover_plans 的约定）。
// 本看板只在本机跑，直接拼绝对路径供「复制路径」粘给资源管理器/AI 对话。
const PLANS_ROOT = 'C:/workspace/omnicompany\\docs\\plans'
function planPath(planId: string): string { return `${PLANS_ROOT}\\${planId.replace(/\//g, '\\')}` }
/** 最后一次跟进时间 = 最新一条进度条目的时间（board 已按 ts 倒序）；无进度退回 updated_at。 */
function lastFollowup(t: TaskNode): number { return t.progress?.[0]?.ts || t.updated_at || 0 }
/** 该任务/任务线绑的活跃对话桶(SESSION-SELF-BINDING 4.5)：优先按 by_task[id]（会话级 task_id 绑定，
 *  阶段三前多为空桶），退回 by_plan[plan_id]（本任务/任务线对应的计划目录，与 active_plan 同一 id 空间，
 *  当前更常命中）。两者都没有 → undefined，徽章不渲染。 */
function bindingFor(id: string, planId: string | undefined | null, byTask: Record<string, BossSightBindingBucket>, byPlan: Record<string, BossSightBindingBucket>): BossSightBindingBucket | undefined {
  return byTask[id] || (planId ? byPlan[planId] : undefined)
}
function relTime(ms?: number): string {
  if (!ms) return ''
  const d = Date.now() - ms, day = 86400000
  if (d < 0) return ''
  if (d < day) return '今天'
  const n = Math.floor(d / day)
  return n < 30 ? `${n}天前` : `${Math.floor(n / 30)}月前`
}
// 金色语义（进行中 / 置顶）统一映射到冷琥珀 token，环上的轨道用极淡描边色。
const STATUS: Record<string, string> = { done: 'var(--fp-ok)', in_progress: 'var(--fp-warn)', paused: 'var(--fp-text-3)', todo: 'var(--fp-link)' }
const CH: Record<string, { c: string; t: string }> = {
  local: { c: 'var(--fp-text-3)', t: '本地' }, meego: { c: 'var(--fp-link)', t: 'Meego' },
  multica: { c: 'var(--fp-violet)', t: 'Multica' }, multi: { c: 'var(--fp-warn)', t: '双源' },
}
const isDone = (s: string) => /done|完成|关闭|取消|解决|cancel|closed|resolved/i.test(s)
const bugChip = (title: string) => (title.includes('必现') ? { t: '必现', c: 'var(--fp-err)' } : title.includes('偶现') ? { t: '偶现', c: 'var(--fp-warn)' } : null)
const pinKey = (kind: string, id: string) => `${kind}:${id}`

/** 圈状进度环（取代长进度条）。size>=30 才在环内显示数字(且 >=13px 满足最小字号);小环为纯视觉量规。 */
function Ring({ pct, size = 30, color }: { pct: number; size?: number; color?: string }) {
  const r = (size - 5) / 2, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, pct))
  const col = color || (p >= 100 ? STATUS.done : 'var(--fp-warn)')
  return (
    <svg width={size} height={size} style={{ flexShrink: 0, transform: 'rotate(-90deg)' }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--fp-border-strong)" strokeWidth={3.5} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={col} strokeWidth={3.5}
        strokeDasharray={c} strokeDashoffset={c * (1 - p / 100)} strokeLinecap="round" />
      {size >= 30 && (
        <text x="50%" y="50%" fill="var(--fp-text-2)" fontSize={13} textAnchor="middle"
          dominantBaseline="central" transform={`rotate(90 ${size / 2} ${size / 2})`}>{p}</text>
      )}
    </svg>
  )
}

/** 里程碑点亮：每条计划一个节点，完成则点亮。 */
function Milestones({ tasks }: { tasks: TaskNode[] }) {
  if (!tasks.length) return null
  const dots = tasks.slice(0, 12)
  return (
    <span style={{ display: 'inline-flex', gap: 3, alignItems: 'center', marginLeft: 6 }} title={`${tasks.filter((t) => isDone(t.status)).length}/${tasks.length} 里程碑达成`}>
      {dots.map((t) => (
        <span key={t.id} style={{
          width: 8, height: 8, borderRadius: 4,
          background: isDone(t.status) ? 'var(--fp-ok)' : 'transparent',
          border: `1.5px solid ${isDone(t.status) ? 'var(--fp-ok)' : t.completion > 0 ? 'var(--fp-warn)' : 'var(--fp-border-strong)'}`,
        }} />
      ))}
      {tasks.length > 12 && <span style={{ color: 'var(--fp-text-3)', fontSize: 13 }}>+{tasks.length - 12}</span>}
    </span>
  )
}

const S: Record<string, any> = {
  // root 透明 → 吃 CockpitShell 的统一冷渐变，玻璃卡浮其上；不铺实底把渐变顶掉。
  root: { position: 'relative', height: '100%', overflow: 'auto', background: 'transparent', color: 'var(--fp-text)', padding: '14px 18px 48px', boxSizing: 'border-box' },
  // 顶部工具行：占正常文档流（不再绝对定位压在首卡上），右对齐、行高紧凑。
  toolbar: { display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6, margin: '0 0 4px', minHeight: 30 },
  iconBtn: { width: 30, height: 30, border: '1px solid transparent', borderRadius: 7, background: 'transparent', color: 'var(--fp-text-3)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0, transition: 'background 150ms cubic-bezier(0.175,0.885,0.32,1.1), color 150ms cubic-bezier(0.175,0.885,0.32,1.1)' },
  fu: { color: 'var(--fp-text-3)', fontSize: 12, flexShrink: 0, whiteSpace: 'nowrap' as const, fontVariantNumeric: 'tabular-nums' as const },
  err: { color: 'var(--fp-err)', fontSize: 13.5, padding: '14px', border: '1px solid color-mix(in srgb, var(--fp-err) 40%, transparent)', borderRadius: 8, background: 'color-mix(in srgb, var(--fp-err) 12%, transparent)', margin: '10px 0', lineHeight: 1.6 },
  domainTag: { color: 'var(--fp-text-3)', fontSize: 12, border: '1px solid var(--fp-border)', borderRadius: 999, padding: '0 8px', flexShrink: 0 },
  // 任务线卡 = 磨砂玻璃卡：blur + inset 高光 + r11 + token 描边；置顶/主线左缘一道琥珀提示条。
  goal: (main: boolean, pinned: boolean): React.CSSProperties => ({
    border: '1px solid var(--fp-border)',
    borderLeft: `3px solid ${main || pinned ? 'var(--fp-warn)' : 'var(--fp-border-strong)'}`,
    borderRadius: 11, margin: '12px 0', padding: '14px 16px',
    background: pinned ? 'color-mix(in srgb, var(--fp-warn) 7%, var(--fp-glass))' : 'var(--fp-glass)',
    backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
  }),
  goalHead: { display: 'flex', alignItems: 'center', gap: 8 },
  // 任务线徽章：主线/支线弱底 chip（次级层级）。
  lineBadge: (main: boolean): React.CSSProperties => ({ fontSize: 12, fontWeight: 600, color: main ? 'var(--fp-warn)' : 'var(--fp-text-2)', background: main ? 'color-mix(in srgb, var(--fp-warn) 16%, transparent)' : 'rgba(255,255,255,.06)', border: '1px solid var(--fp-border)', borderRadius: 999, padding: '1px 8px', flexShrink: 0 }),
  // 任务线标题 = 卡片主焦点：16px / 650，点击展开。
  goalTitle: { color: 'var(--fp-text)', fontSize: 16, fontWeight: 650, letterSpacing: '-0.01em', cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  taskRow: { display: 'grid', gridTemplateColumns: '28px 1fr auto', gap: 8, alignItems: 'center', padding: '7px 6px', borderTop: '1px solid var(--fp-border-subtle)' },
  tMid: { minWidth: 0 },
  tTitleRow: { display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer' },
  // 计划名 = 行主焦点：14px 主文字色（此前 13.5 次级色，行内层级不清）。
  tTitle: { color: 'var(--fp-text)', fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  chBadge: (ch: string): React.CSSProperties => ({ fontSize: 12, color: (CH[ch] || CH.local).c, border: `1px solid color-mix(in srgb, ${(CH[ch] || CH.local).c} 40%, transparent)`, borderRadius: 999, padding: '0 7px', flexShrink: 0 }),
  // 最新进度 = 三级辅助行：更淡、无图标（时钟 icon 每行都有等于没有）。
  prog: { color: 'var(--fp-text-3)', fontSize: 13, marginTop: 3, lineHeight: 1.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  // 任务线目标描述：最多 2 行截断（长文进 title 提示），不再整段铺开。
  objective: { color: 'var(--fp-text-2)', fontSize: 13, margin: '4px 0 2px', lineHeight: 1.55, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const, overflow: 'hidden' },
  timeline: { margin: '4px 0 2px 4px', borderLeft: '1px solid var(--fp-border)', paddingLeft: 10 },
  tlItem: { color: 'var(--fp-text-2)', fontSize: 13, padding: '2px 0', lineHeight: 1.5 },
  tlTs: { color: 'var(--fp-text-3)', fontSize: 12, marginRight: 6, fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace" },
  subRow: { display: 'flex', gap: 6, alignItems: 'center', padding: '2px 0 2px 16px', color: 'var(--fp-text-2)', fontSize: 13 },
  more: { color: 'var(--fp-text-3)', fontSize: 13, cursor: 'pointer', padding: '4px 0 0 6px', display: 'inline-flex', alignItems: 'center', gap: 4 },
  empty: { color: 'var(--fp-text-3)', fontSize: 13, padding: '4px 0 2px 6px' },
  // 外部收件箱分区标题：15px / 600（与样板 groupHead 同档）。
  inboxHead: { display: 'flex', alignItems: 'center', gap: 8, margin: '28px 2px 8px', color: 'var(--fp-text-2)', fontSize: 15, fontWeight: 600, flexWrap: 'wrap' as const },
  inboxNote: { color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 400 },
  syncMsg: { color: 'var(--fp-text-3)', fontSize: 13, fontWeight: 400 },
  // 同步外部 = 收件箱区主操作（显眼但克制的 ghost 主钮）。
  primaryBtn: { border: '1px solid var(--fp-accent)', background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', borderRadius: 7, cursor: 'pointer', padding: '5px 11px', fontSize: 14, fontWeight: 550, display: 'inline-flex', alignItems: 'center', gap: 6, transition: 'filter 150ms cubic-bezier(0.175,0.885,0.32,1.1)' },
}

type OpenTab = ReturnType<typeof usePanels.getState>['openTab']
type TogglePin = (kind: 'goal' | 'task', id: string, pinned: boolean) => void

/** 点击计划=直达 plan.md 正文（KB note 渲染；note id 约定 = plans/<plan_id>/plan，与
 *  controlplane plans.py 的 note_id_if_md 同源）。历史与文件信息(plan-folder 视图)收进
 *  行内 ⋯ 菜单（用户 2026-07-05：点开要见正文，不是历史记录和文件信息）。
 *  multica→开网页议题；其余→复制引用。 */
function openTask(t: TaskNode, openTab: OpenTab, copy: (text: string) => void) {
  if (t.plan_id) { openTab({ type: 'note', id: `plans/${t.plan_id}/plan` }, txt(t.title).slice(0, 40) || t.plan_id); return }
  const mref = (t.external_refs || []).find((r) => r.startsWith('multica:'))
  if (mref) { window.open(`https://multica.internal.example.com/demogame-5224/issues/${mref.split(':')[1]}`, '_blank'); return }
  copy(t.external_refs?.[0] || t.id)
}

/** 标题点击行为的悬浮说明（用户 2026-07-05：分不清点哪是展开、点哪是跳转——文字与箭头各自说清楚）。 */
function openHint(t: TaskNode): string {
  if (t.plan_id) return '打开计划正文 plan.md'
  if ((t.external_refs || []).some((r) => r.startsWith('multica:'))) return '在浏览器打开 Multica 议题'
  return '点击复制引用'
}

/** 复制 + 行内反馈。裸 navigator.clipboard 在 VSCode webview/受限 iframe 里被静默拒绝（点了没反应），
 *  必须走 lib/copyText 三级降级；且按 copyText 的约定，成败都给可见反馈。 */
function useCopyFlash(): [string, (text: string) => void] {
  const [msg, setMsg] = useState('')
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const copy = useCallback((text: string) => {
    void copyText(text).then((ok) => {
      window.clearTimeout(timer.current)
      setMsg(ok ? '已复制' : '复制失败（剪贴板受限）')
      timer.current = window.setTimeout(() => setMsg(''), 1500)
    })
  }, [])
  return [msg, copy]
}

const copyFlashStyle = (msg: string): React.CSSProperties =>
  ({ color: msg.includes('失败') ? 'var(--fp-err)' : 'var(--fp-ok)', fontSize: 12, flexShrink: 0, whiteSpace: 'nowrap' })

function TaskRow({ t, onChanged, showDue, pinned, onTogglePin, byTask, byPlan }: {
  t: TaskNode; onChanged: () => void; showDue?: boolean; pinned?: boolean; onTogglePin?: TogglePin
  byTask: Record<string, BossSightBindingBucket>; byPlan: Record<string, BossSightBindingBucket>
}) {
  const [open, setOpen] = useState(false)
  const openTab = usePanels((s) => s.openTab)
  const ch = CH[t.channel] || CH.local
  const hasMore = (t.progress?.length || 0) > 0 || (t.subtasks?.length || 0) > 0
  const bug = bugChip(t.title)
  const ref0 = t.plan_id || t.external_refs?.[0] || t.id
  const fu = lastFollowup(t)
  const [copyMsg, copy] = useCopyFlash()
  const binding = bindingFor(t.id, t.plan_id, byTask, byPlan)
  // 行内低频操作（历史信息 / 复制引用 / 置顶 / 归档）收进 ⋯，主操作=点标题直达 plan.md 正文。
  const items: KebabItem[] = [
    ...(t.plan_id ? [{ label: '历史与文件信息', icon: <History size={14} />, testid: 'task-open-plan-folder', onClick: () => openTab({ type: 'plan', id: t.plan_id! }, txt(t.title).slice(0, 40) || t.plan_id!) }] : []),
    { label: `复制引用：${ref0}`, icon: <Copy size={14} />, testid: 'task-jump', onClick: () => { copy(ref0) } },
    ...(t.plan_id ? [{ label: '复制路径（计划目录）', icon: <FolderOpen size={14} />, testid: 'task-copy-path', onClick: () => { copy(planPath(t.plan_id!)) } }] : []),
    { label: pinned ? '取消置顶' : '置顶（排到列表最前）', icon: <Pin size={14} />, testid: 'task-pin', onClick: () => onTogglePin?.('task', t.id, !!pinned) },
    { label: t.archived ? '取消归档' : '归档', icon: t.archived ? <ArchiveRestore size={14} /> : <Archive size={14} />, testid: 'task-archive', onClick: () => { void questsApi.archive(t.id, !t.archived).then(onChanged) } },
  ]
  return (
    <div className={'qb-row' + (pinned ? ' qb-pinned-row' : '')} style={{ ...S.taskRow, opacity: t.archived && !pinned ? 0.6 : 1 }} data-testid="task-row">
      <Ring pct={t.completion} size={28} />
      <div style={S.tMid}>
        <div style={S.tTitleRow}>
          {hasMore
            ? <Tooltip content={open ? '收起进度与子任务' : '原地展开：进度历史与执行子任务'} containerStyle={{ flexShrink: 0, display: 'inline-flex' }}>
                <button className="qb-ibtn" style={{ padding: 0 }} data-testid="task-expand" aria-label={open ? '收起' : '展开进度与子任务'} onClick={() => setOpen((o) => !o)}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button>
              </Tooltip>
            : <span style={{ width: 13, flexShrink: 0 }} />}
          {pinned && <Pin size={12} fill="currentColor" style={{ color: 'var(--fp-warn)', flexShrink: 0 }} />}
          <Tooltip content={openHint(t)} containerStyle={{ minWidth: 0 }}>
            <span className="qb-task-title" style={{ ...S.tTitle, display: 'block', cursor: 'pointer' }} data-testid="task-open" onClick={() => openTask(t, openTab, copy)}>{txt(t.title)}</span>
          </Tooltip>
          {copyMsg && <span style={copyFlashStyle(copyMsg)} data-testid="task-copy-toast">{copyMsg}</span>}
          {bug && <span style={{ ...S.chBadge('local'), color: bug.c, border: `1px solid color-mix(in srgb, ${bug.c} 40%, transparent)` }}>{bug.t}</span>}
          {/* 「本地」是缺省渠道，行行都标等于没标——只给外部渠道亮牌。 */}
          {t.channel !== 'local' && CH[t.channel] && <span style={S.chBadge(t.channel)}>{ch.t}{t.external_refs?.length > 1 ? ` ${t.external_refs.length}` : ''}</span>}
          {showDue && t.due_date && <span style={{ color: 'var(--fp-warn)', fontSize: 13, flexShrink: 0 }}>排期 {t.due_date}</span>}
          {/* N 活跃对话在推进本计划(chat+PTY 全覆盖) */}
          {binding && <ActiveConvoBadge active={binding.active} total={binding.total} sessions={binding.sessions} />}
          {fu > 0 && <span style={{ ...S.fu, marginLeft: 'auto' }} title={`最后跟进 ${new Date(fu).toLocaleString()}`}>跟进 {relTime(fu)}</span>}
        </div>
        {t.latest_progress && <div style={S.prog} title={txt(t.latest_progress)}>{stripTag(txt(t.latest_progress))}</div>}
        {open && (
          <>
            {(t.progress?.length || 0) > 0 && (
              <div style={S.timeline} data-testid="task-progress-history">
                {t.progress.map((p, i) => <div key={i} style={S.tlItem}><span style={S.tlTs}>{relTime(p.ts)}</span>{txt(p.text)}</div>)}
              </div>
            )}
            {(t.subtasks || []).map((c) => (
              <div key={c.id} style={S.subRow}>
                <Ring pct={c.completion} size={20} />
                <Tooltip content={openHint(c)} containerStyle={{ minWidth: 0 }}>
                  <span className="qb-task-title" style={{ cursor: 'pointer' }} onClick={() => openTask(c, openTab, copy)}>{txt(c.title)}</span>
                </Tooltip>
              </div>
            ))}
          </>
        )}
      </div>
      <div data-omni-capture-ignore="true" onClick={(e) => e.stopPropagation()}>
        <KebabMenu items={items} testid="task-more" iconSize={15} />
      </div>
    </div>
  )
}

function GoalCard({ g, clusterTitle, onChanged, pinnedSet, onTogglePin, showArchived, byTask, byPlan }: {
  g: GoalNode; clusterTitle?: string; onChanged: () => void; pinnedSet: Set<string>; onTogglePin: TogglePin; showArchived: boolean
  byTask: Record<string, BossSightBindingBucket>; byPlan: Record<string, BossSightBindingBucket>
}) {
  const [expanded, setExpanded] = useState(false)
  const [archOpen, setArchOpen] = useState(false)
  const main = g.line === 'main'
  const LATEST = 3
  const isPinned = (t: TaskNode) => pinnedSet.has(pinKey('task', t.id))
  // 主区显示：未归档 + 置顶的（不论 toggle 一致），置顶浮到最前；归档且未置顶的另列「已归档」。
  const top = g.tasks.filter((t) => !t.archived || isPinned(t))
  const archived = g.tasks.filter((t) => t.archived && !isPinned(t))
  const sortedTop = [...top].sort((a, b) => {
    const pa = isPinned(a) ? 1 : 0, pb = isPinned(b) ? 1 : 0
    if (pa !== pb) return pb - pa
    return lastFollowup(b) - lastFollowup(a)
  })
  const shown = expanded ? sortedTop : sortedTop.slice(0, LATEST)
  const goalPct = top.length ? Math.round(top.reduce((a, t) => a + t.completion, 0) / top.length) : 0
  const goalPinned = pinnedSet.has(pinKey('goal', g.id))
  const archivedCount = g.archived_count ?? archived.length
  const [copyMsg, copy] = useCopyFlash()
  const goalBinding = bindingFor(g.id, g.plan_id, byTask, byPlan)
  // 任务线级低频操作收进卡头 ⋯（置顶整条线）。主操作=点标题展开。
  const goalItems: KebabItem[] = [
    { label: goalPinned ? '取消置顶此任务线' : '置顶此任务线（排到最前）', icon: <Pin size={14} />, testid: 'goal-pin', onClick: () => onTogglePin('goal', g.id, goalPinned) },
    ...(g.plan_id ? [{ label: '复制路径（计划目录）', icon: <FolderOpen size={14} />, testid: 'goal-copy-path', onClick: () => { copy(planPath(g.plan_id!)) } }] : []),
  ]
  return (
    <div id={`goal-${g.id}`} style={S.goal(main, goalPinned)} data-testid="goal-card">
      <div style={S.goalHead}>
        {goalPinned && <Pin size={13} fill="currentColor" style={{ color: 'var(--fp-warn)', flexShrink: 0 }} />}
        <span style={S.lineBadge(main)}>{main ? '主线' : '支线'}</span>
        {g.kind && <span style={{ color: 'var(--fp-text-3)', fontSize: 13 }}>{g.kind}</span>}
        <Tooltip content={expanded ? '收起' : '展开这条任务线的全部计划'} containerStyle={{ minWidth: 0 }}>
          <span className="qb-task-title" style={{ ...S.goalTitle, display: 'block' }} onClick={() => setExpanded((e) => !e)}>{txt(g.title)}</span>
        </Tooltip>
        {clusterTitle && <span style={S.domainTag}>{clusterTitle}</span>}
        <Milestones tasks={top} />
        {/* N 活跃对话在推进本任务线(chat+PTY 全覆盖) */}
        {goalBinding && <ActiveConvoBadge active={goalBinding.active} total={goalBinding.total} sessions={goalBinding.sessions} />}
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {copyMsg && <span style={copyFlashStyle(copyMsg)} data-testid="goal-copy-toast">{copyMsg}</span>}
          <span style={{ color: 'var(--fp-text-3)', fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>{top.filter((t) => isDone(t.status)).length}/{top.length}</span>
          <Ring pct={goalPct} size={34} />
          <KebabMenu items={goalItems} testid="goal-more" iconSize={15} />
        </span>
      </div>
      {g.objective && <div style={S.objective} title={txt(g.objective)}>{txt(g.objective)}</div>}
      {shown.map((t) => <TaskRow key={t.id} t={t} onChanged={onChanged} pinned={isPinned(t)} onTogglePin={onTogglePin} byTask={byTask} byPlan={byPlan} />)}
      {sortedTop.length > LATEST && (
        <div style={S.more} onClick={() => setExpanded((e) => !e)}>
          {expanded ? <><ChevronDown size={13} /> 收起</> : <><ChevronRight size={13} /> 展开其余 {sortedTop.length - LATEST} 条</>}
        </div>
      )}
      {/* 空态适配：没有任何活跃计划的任务线 */}
      {sortedTop.length === 0 && (
        <div style={S.empty}>
          {archivedCount > 0
            ? (showArchived ? '（本路线的计划均已归档，见下方）' : `（${archivedCount} 条计划均已归档 · 顶部 ⋯ 勾「显示已归档」查看）`)
            : '（本路线暂无计划）'}
        </div>
      )}
      {/* 归档区：勾选「显示已归档」后出现，仍需点开 */}
      {showArchived && archivedCount > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={S.more} onClick={() => setArchOpen((o) => !o)} data-testid="goal-archived-toggle">
            {archOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />} 已归档 {archivedCount} 条
          </div>
          {archOpen && archived.map((t) => <TaskRow key={t.id} t={t} onChanged={onChanged} pinned={false} onTogglePin={onTogglePin} byTask={byTask} byPlan={byPlan} />)}
        </div>
      )}
    </div>
  )
}

export default function QuestBoard() {
  const [board, setBoard] = useState<Board | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')
  const [inboxOpen, setInboxOpen] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  // 活跃对话聚合(by_task/by_plan) — 取一次即可, 各任务线/计划卡按 id 查桶显示"💬 N"(SESSION-SELF-BINDING 4.5)。
  const [byTask, setByTask] = useState<Record<string, BossSightBindingBucket>>({})
  const [byPlan, setByPlan] = useState<Record<string, BossSightBindingBucket>>({})

  const load = useCallback(() => {
    setBusy(true)
    questsApi.board(showArchived).then((b) => { setBoard(b); setError(null) })
      .catch((e) => setError(String(e?.message || e))).finally(() => setBusy(false))
  }, [showArchived])
  useEffect(() => { load() }, [load])

  useEffect(() => {
    bossSightApi.activeBindings().then((d) => { setByTask(d.by_task || {}); setByPlan(d.by_plan || {}) }).catch(() => { setByTask({}); setByPlan({}) })
  }, [])
  // 本地 board 上面已秒出(~30ms)。外部同步(meego 拉collab platform可 15s+; multica 已停服返 502)
  // 不在打开时阻塞: 延后到后台、且 5 分钟内最多自动一次; 想立刻同步点「同步外部」按钮。
  useEffect(() => {
    let alive = true
    const LAST = 'whatnow:lastAutoSync'
    const now = Date.now()
    if (now - Number(localStorage.getItem(LAST) || 0) < 5 * 60 * 1000) return undefined
    const timer = setTimeout(() => {
      localStorage.setItem(LAST, String(Date.now()))
      // 只自动同步 meego(multica 已停服, 自动调只会徒增 502 + 拖慢); 两个都同步走手动按钮。
      questsApi.syncMeego().then(() => { if (alive) load() }).catch(() => { /* 外部慢/挂不影响本地板 */ })
    }, 1500)
    return () => { alive = false; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sync = async () => {
    setSyncMsg('同步中…')
    const rs = await Promise.allSettled([questsApi.syncMeego(), questsApi.syncMultica()])
    const n = rs.reduce((a, r) => a + (r.status === 'fulfilled' ? (r.value?.synced || 0) : 0), 0)
    setSyncMsg(`同步 ${n} 条`); load(); setTimeout(() => setSyncMsg(''), 4000)
  }

  const pinnedSet = new Set((board?.pins || []).map((p) => pinKey(p.subject_kind, p.subject_id)))
  const togglePin: TogglePin = (kind, id, pinned) => { questsApi.pin(kind, id, !pinned).then(load) }

  // 扁平化：所有任务线汇成一个列表（带各自的域标签）。置顶的浮到全局最前，其余保持
  // cluster 顺序内主线在前的自然次序。
  const clusterTitleOf: Record<string, string> = {}
  ;(board?.clusters || []).forEach((c) => { clusterTitleOf[c.id] = txt(c.title).split(/[·・]/)[0].trim() })
  const allGoals: { g: GoalNode; clusterTitle?: string }[] = [
    ...(board?.clusters || []).flatMap((c) => c.goals.map((g) => ({ g, clusterTitle: clusterTitleOf[c.id] }))),
    ...((board?.orphan_goals || []).map((g) => ({ g, clusterTitle: undefined }))),
  ].sort((a, b) => (pinnedSet.has(pinKey('goal', b.g.id)) ? 1 : 0) - (pinnedSet.has(pinKey('goal', a.g.id)) ? 1 : 0))

  const inbox = board?.loose_tasks || []
  // 低频操作统一收进 ⋯（用户 2026-07-02：已经有 ⋯ 了就别再单独放按钮）。
  const topItems: KebabItem[] = [
    { label: showArchived ? '✓ 显示已归档' : '显示已归档', icon: <Archive size={14} />, testid: 'show-archived-toggle', onClick: () => setShowArchived((v) => !v) },
  ]
  return (
    <div style={S.root} data-testid="quest-board">
      {/* 无标题头（Linear 风内容优先）：页签已标识身份。工具行占正常文档流，
          不再绝对定位压在首卡的进度环/⋯ 上。 */}
      <div style={S.toolbar} data-omni-capture-ignore="true">
        <KebabMenu items={topItems} testid="quest-board-menu" iconSize={16} />
        <button
          type="button"
          style={{ ...S.iconBtn, opacity: busy ? 0.45 : 1 }}
          disabled={busy}
          title={busy ? '刷新中…' : '刷新'}
          data-testid="quest-board-refresh"
          onClick={load}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,.06)'; e.currentTarget.style.color = 'var(--fp-text)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fp-text-3)' }}
        ><RefreshCw size={15} /></button>
      </div>

      {error && (
        <div style={S.err}>进度服务通道不可用 progress-service（{error}）。dashboard 会尝试自动拉起；<br />手动启动：<code>C:/workspace/omnicompany\services\_progress\progress_service\start-progress-service.cmd</code></div>
      )}

      {board && allGoals.map(({ g, clusterTitle }) => (
        <GoalCard key={g.id} g={g} clusterTitle={clusterTitle} onChanged={load} pinnedSet={pinnedSet} onTogglePin={togglePin} showArchived={showArchived} byTask={byTask} byPlan={byPlan} />
      ))}

      {board && (
        <div data-testid="external-inbox">
          <div style={S.inboxHead}>
            <span style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--fp-link)' }} onClick={() => setInboxOpen((o) => !o)}>
              {inboxOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />} <Inbox size={15} /> 外部收件箱
            </span>
            <span style={S.inboxNote}>meego(maintainer) + multica，按排期+bug分级排序，已完成自动归档 · {inbox.length} 条</span>
            {/* 同步外部数据源 — 挪到收件箱旁（不在页面顶部） */}
            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, alignItems: 'center' }}>
              {syncMsg && <span style={S.syncMsg}>{syncMsg}</span>}
              <button className="qb-btn" style={S.primaryBtn} onClick={sync} data-testid="sync-external"
                onMouseEnter={(e) => { e.currentTarget.style.filter = 'brightness(1.08)' }}
                onMouseLeave={(e) => { e.currentTarget.style.filter = 'none' }}><Download size={13} /> 同步外部</button>
            </span>
          </div>
          {inboxOpen && (
            <div style={S.goal(false, false)}>
              {(() => {
                const sortedInbox = [...inbox].sort((a, b) => (pinnedSet.has(pinKey('task', b.id)) ? 1 : 0) - (pinnedSet.has(pinKey('task', a.id)) ? 1 : 0))
                return sortedInbox.slice(0, 60).map((t) => <TaskRow key={t.id} t={t} onChanged={load} showDue pinned={pinnedSet.has(pinKey('task', t.id))} onTogglePin={togglePin} byTask={byTask} byPlan={byPlan} />)
              })()}
              {inbox.length > 60 && <div style={{ color: 'var(--fp-text-3)', fontSize: 13, padding: '4px 0 0 6px' }}>… 共 {inbox.length} 条（按紧急度前 60）</div>}
            </div>
          )}
        </div>
      )}

      {board && allGoals.length === 0 && inbox.length === 0 && !error && <div style={{ color: 'var(--fp-text-3)', fontSize: 13, padding: '30px 8px' }}>目标系统为空。</div>}
    </div>
  )
}
