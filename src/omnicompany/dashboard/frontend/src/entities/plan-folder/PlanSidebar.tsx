import React, { useEffect, useMemo, useState } from 'react'
import type { SidebarViewProps } from '../registry'
import type { PlanEntity } from './index'
import { openInVscode } from '../../lib/openInVscode'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import ActiveConvoBadge from '../../shared/view/ui/ActiveConvoBadge'
import { bossSightApi, type BossSightBindingBucket } from '../../api/bossSightClient'
import { Code2, FileText, ScrollText, ChevronRight, ChevronDown, FolderOpen } from 'lucide-react'

interface PlanDetailFile {
  path: string
  is_md: boolean
  size: number
  mtime: number
  note_id_if_md: string | null
}

interface DirNode {
  name: string
  path: string  // category prefix like "_infra" or "domain/voxelcraft"
  children: Map<string, DirNode>
  plans: PlanEntity[]
}

function buildTree(plans: PlanEntity[]): DirNode {
  const root: DirNode = { name: '', path: '', children: new Map(), plans: [] }
  for (const p of plans) {
    // p.id is like "_infra/[2026-05-01]WEB-FOUNDATION" or "[2026-04-22]X" (no category)
    const parts = p.id.split('/')
    const planLeaf = parts[parts.length - 1]
    const dirParts = parts.slice(0, -1)
    let cur = root
    for (const seg of dirParts) {
      let next = cur.children.get(seg)
      if (!next) {
        next = { name: seg, path: cur.path ? `${cur.path}/${seg}` : seg, children: new Map(), plans: [] }
        cur.children.set(seg, next)
      }
      cur = next
    }
    cur.plans.push(p)
  }
  return root
}

// frostpane 深度重建(2026-06-29): 抛弃拥挤等宽折叠树, 改成「分区分组 + 玻璃计划卡网格」。
// - 目录 = 分区标题条(15px 醒目 / mono 计数弱灰), 低频的 project.md 收进区头 ⋯。
// - 计划 = 磨砂玻璃卡(var(--fp-glass)+blur26 saturate190+inset 高光+radius11),
//   卡解剖: 状态徽章 + 标题 flex1 醒目 + 共享 KebabMenu ⋯ 收纳低频(开全页/project.md);
//   文件计数·id 弱灰 12px 等宽; 主操作「在 VSCode 打开」做底部整宽按钮; 展开看 md 文件。
// - 卡网格 repeat(auto-fill,minmax(260px,1fr)): 窄侧栏退化单列, 宽面板自动成网格。
// - 信息层级靠字阶(15/13/12)非纯加粗; 4px 栅格放宽呼吸; 冷色复用现有 token。
const MONO = "'Berkeley Mono','SF Mono','Cascadia Code',Consolas,Menlo,monospace"
const SANS = 'var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif)'
const GLASS = 'var(--fp-blur)'
const ACCENT_WEAK = 'var(--fp-accent-weak)'
// 强调主操作底色: accent 透出薄底, 用 color-mix 由 accent token 派生 (禁裸 hex)。
const PRIMARY_BG = 'color-mix(in srgb, var(--fp-accent) 14%, transparent)'
const PRIMARY_BG_HOVER = 'color-mix(in srgb, var(--fp-accent) 24%, transparent)'

const S: Record<string, any> = {
  empty: { padding: '12px', color: 'var(--fp-text-3)', fontSize: 13 },
  // root 透明吃 body 全局冷渐变 (var(--fp-bg-grad)), 不铺实底; 玻璃卡浮其上。
  root: { background: 'transparent', fontFamily: SANS, padding: '4px 4px 16px' },

  // 分区(目录): 间距编码层级, 标题靠字号建立 — 不再用一排折叠箭头制造拥挤感。
  section: (depth: number): React.CSSProperties => ({
    marginTop: depth === 0 ? 12 : 8,
    marginLeft: depth > 0 ? 8 : 0,
    paddingLeft: depth > 0 ? 8 : 0,
    borderLeft: depth > 0 ? '1px solid var(--fp-border-subtle)' : undefined,
  }),
  sectionHead: (depth: number): React.CSSProperties => ({
    display: 'flex', alignItems: 'center', gap: 6,
    padding: '4px 6px', cursor: 'pointer', userSelect: 'none' as const,
    borderRadius: 7,
    transition: 'background 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  // 顶层目录 = 本区最重要信息 15px/650; 越深越收敛 13px。
  sectionTitle: (depth: number): React.CSSProperties => ({
    color: depth === 0 ? 'var(--fp-text)' : 'var(--fp-text-2)',
    fontSize: depth === 0 ? 15 : 13,
    fontWeight: depth === 0 ? 650 : 550,
    letterSpacing: depth === 0 ? '-0.01em' : undefined,
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const,
  }),
  caret: { color: 'var(--fp-text-3)', display: 'inline-flex', flexShrink: 0 },
  // 计数 = 最次级 12px 弱灰等宽。
  count: { color: 'var(--fp-text-3)', marginLeft: 'auto', fontSize: 12, fontFamily: MONO, flexShrink: 0 },

  // 计划卡网格: 窄侧栏 1 列 / 宽面板自动多列。
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8, marginTop: 6 },
  // 磨砂玻璃卡: inset 顶部高光 + 冷色描边 + 11 圆角。
  card: (active: boolean): React.CSSProperties => ({
    display: 'flex', flexDirection: 'column', minWidth: 0,
    background: 'var(--fp-glass)', backdropFilter: GLASS, WebkitBackdropFilter: GLASS,
    border: `1px solid ${active ? 'var(--fp-accent)' : 'var(--fp-border)'}`, borderRadius: 11, padding: 12,
    boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
    transition: 'border-color 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  cardTop: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 },
  // 标题 = 卡内主信息 15px/650 醒目。
  cardTitle: { flex: 1, minWidth: 0, color: 'var(--fp-text)', fontWeight: 650, fontSize: 15, letterSpacing: '-0.01em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const, cursor: 'pointer' },
  // 状态/分类徽章 = 微胶囊语义弱底, 不抢标题。
  badge: (color: string): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', padding: '1px 8px', borderRadius: 999,
    fontSize: 12, fontWeight: 600, color, background: 'rgba(255,255,255,0.06)', flexShrink: 0,
  }),
  // 卡内次信息 = 弱灰 12px 等宽。
  meta: { color: 'var(--fp-text-3)', fontSize: 12, marginTop: 8, fontFamily: MONO, display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  // 主操作 = 底部整宽显眼按钮。
  primary: { marginTop: 10, width: '100%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, border: '1px solid var(--fp-border)', background: PRIMARY_BG, color: 'var(--fp-link)', borderRadius: 7, padding: '6px 10px', cursor: 'pointer', fontSize: 13, fontWeight: 550, fontFamily: SANS, transition: 'all 150ms cubic-bezier(0.175,0.885,0.32,1.1)' },
  // 展开后的文件列表 = 最次级 12px 弱灰等宽路径感。
  fileWrap: { marginTop: 8, borderTop: '1px solid var(--fp-border-subtle)', paddingTop: 6, display: 'flex', flexDirection: 'column', gap: 1 },
  fileRow: (active: boolean): React.CSSProperties => ({
    padding: '3px 6px', cursor: 'pointer',
    color: active ? 'var(--fp-accent)' : 'var(--fp-text-3)',
    background: active ? ACCENT_WEAK : 'transparent',
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const,
    fontSize: 12, fontFamily: MONO, borderRadius: 6,
    transition: 'background 150ms cubic-bezier(0.175,0.885,0.32,1.1), color 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  fileEmpty: { padding: '3px 6px', color: 'var(--fp-text-3)', fontSize: 12, fontFamily: MONO },
}

interface PlanCardProps {
  plan: PlanEntity
  isOpen: boolean
  files: PlanDetailFile[]
  loading: boolean
  isOrphan: boolean
  activeId: string | null
  togglePlan: (id: string) => void
  onOpenNote: (noteId: string) => void
  onOpenPlanTab: (planId: string, title: string) => void
  /** 本计划的活跃对话桶(by_plan[p.id]) — undefined = 无绑定对话, 徽章不渲染。 */
  binding?: BossSightBindingBucket
}

// 单个计划 = 一张磨砂玻璃卡。低频动作(开全页/project.md)进 ⋯; 主操作(VSCode 打开)做底部按钮。
function PlanCard({
  plan: p, isOpen, files, loading, isOrphan, activeId,
  togglePlan, onOpenNote, onOpenPlanTab, binding,
}: PlanCardProps) {
  const tabId = `plan:${p.id}`
  const active = activeId === tabId
  const kebab: KebabItem[] = [
    { label: '打开全页(关联会话)', icon: <ScrollText size={15} />, testid: `plan-open-tab-${p.id}`, onClick: () => onOpenPlanTab(p.id, p.title) },
  ]
  // 非 orphan 才有 project subdir, 给 project.md 入口(vision + 退出条件)。
  if (!isOrphan) {
    kebab.push({ label: '打开 project.md', icon: <FileText size={15} />, testid: `plan-project-md-${p.id}`, onClick: () => onOpenNote(`plans/${p.id.split('/').slice(0, -1).join('/')}/project`) })
  }
  return (
    <div
      style={S.card(active)}
      data-plan-id={p.id}
      data-omni-uri={`omni://plan/${encodeURIComponent(p.id)}`}
      data-omni-kind="plan"
      data-omni-title={p.title}
      data-orphan={isOrphan ? 'true' : undefined}
      title={isOrphan
        ? `⚠ orphan plan (在 docs/plans/ 根下, 缺 project subdir). 应放入某个 project (例 _infra/<project>/${p.id})`
        : p.id}
      onClick={() => togglePlan(p.id)}
      onMouseEnter={(e) => { if (!active) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--fp-border-strong)' }}
      onMouseLeave={(e) => { if (!active) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--fp-border)' }}
    >
      <div style={S.cardTop}>
        {isOrphan && <span style={S.badge('var(--fp-err)')}>orphan</span>}
        {p.archived && <span style={S.badge('var(--fp-warn)')}>archived</span>}
        <span
          style={S.cardTitle}
          title={`${p.title} — 点击展开文件`}
        >
          {p.title}
        </span>
        {/* N 活跃对话在推进本计划(chat+PTY 全覆盖) — 由父级按 p.id 从 by_plan 查桶传入 */}
        {binding && <ActiveConvoBadge active={binding.active} total={binding.total} sessions={binding.sessions} />}
        {/* 低频操作(开全页 / project.md)收进共享 ⋯ 菜单 — 不再一排等权按钮 */}
        <KebabMenu testid={`plan-more-${p.id}`} items={kebab} />
      </div>
      {/* 次级元信息: 文件计数 + id 尾段, 弱灰等宽 (整卡点击即展开) */}
      <div style={S.meta} title={p.id}>
        {isOpen ? <ChevronDown size={13} style={S.caret} /> : <ChevronRight size={13} style={S.caret} />}
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {p.file_count != null ? `${p.file_count} 文件` : ''} · {p.id.split('/').pop()}
        </span>
      </div>
      {/* 主操作 = 显眼底部整宽按钮: 在 VSCode 打开计划文件夹 */}
      {p.folder_path && (
        <button
          type="button"
          style={S.primary}
          data-open-plan-vscode={p.id}
          title={`在 VSCode 打开计划文件夹 ${p.folder_path}`}
          onClick={(e) => { e.stopPropagation(); openInVscode(p.folder_path!) }}
          onMouseEnter={(e) => { e.currentTarget.style.background = PRIMARY_BG_HOVER; e.currentTarget.style.color = 'var(--fp-text)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = PRIMARY_BG; e.currentTarget.style.color = 'var(--fp-link)' }}
        >
          <Code2 size={14} /> 在 VSCode 打开
        </button>
      )}
      {isOpen && (
        <div style={S.fileWrap}>
          {loading && <div style={S.fileEmpty}>加载中…</div>}
          {!loading && files.filter(f => f.is_md).length === 0 && <div style={S.fileEmpty}>无 md 文件</div>}
          {!loading && files.filter(f => f.is_md).map((f) => {
            const noteId = f.note_id_if_md
            const noteTab = noteId ? `note:${noteId}` : ''
            return (
              <div
                key={f.path}
                style={S.fileRow(activeId === noteTab)}
                title={f.path}
                data-plan-file={f.path}
                data-omni-uri={noteId ? `omni://file/${encodeURIComponent(noteId)}` : undefined}
                data-omni-kind={noteId ? 'file' : undefined}
                data-omni-title={f.path}
                onClick={(e) => { e.stopPropagation(); if (noteId) onOpenNote(noteId) }}
                onMouseEnter={(e) => { if (activeId !== noteTab) (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.05)' }}
                onMouseLeave={(e) => { if (activeId !== noteTab) (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}
              >
                {f.path}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

interface DirSectionProps {
  node: DirNode
  depth: number
  expandedDirs: Set<string>
  expandedPlans: Set<string>
  toggleDir: (path: string) => void
  togglePlan: (id: string) => void
  planFiles: Map<string, PlanDetailFile[]>
  loadingPlanIds: Set<string>
  activeId: string | null
  onOpenNote: (noteId: string) => void
  onOpenPlanTab: (planId: string, title: string) => void
  forceExpand?: boolean
  bindings: Record<string, BossSightBindingBucket>
}

function DirSection({
  node, depth, expandedDirs, expandedPlans, toggleDir, togglePlan,
  planFiles, loadingPlanIds, activeId, onOpenNote, onOpenPlanTab, forceExpand, bindings,
}: DirSectionProps) {
  const isExpanded = forceExpand || expandedDirs.has(node.path)
  const total = countPlans(node)
  // project.md 立于 plan 之上的元数据(vision + 退出条件) — 收进区头 ⋯, 低频不抢标题。
  const showProjectLink = depth >= 1 && total > 0
  const headKebab: KebabItem[] = showProjectLink
    ? [{ label: '打开 project.md', icon: <FileText size={15} />, testid: `section-project-md-${node.path}`, onClick: () => onOpenNote(`plans/${node.path}/project`) }]
    : []
  return (
    <div style={S.section(depth)}>
      <div
        style={S.sectionHead(depth)}
        onClick={() => toggleDir(node.path)}
        title={node.path || '(root)'}
        onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.04)' }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}
      >
        <span style={S.caret}>{isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span style={S.sectionTitle(depth)}>{node.name || 'plans'}</span>
        {showProjectLink && (
          <span data-project-md={node.path} onClick={(e) => e.stopPropagation()}>
            <KebabMenu testid={`section-more-${node.path}`} items={headKebab} />
          </span>
        )}
        <span style={S.count}>{total}</span>
      </div>
      {isExpanded && (
        <div>
          {[...node.children.values()].map((c) => (
            <DirSection
              key={c.path} node={c} depth={depth + 1}
              expandedDirs={expandedDirs} expandedPlans={expandedPlans}
              toggleDir={toggleDir} togglePlan={togglePlan}
              planFiles={planFiles} loadingPlanIds={loadingPlanIds}
              activeId={activeId} onOpenNote={onOpenNote} onOpenPlanTab={onOpenPlanTab}
              forceExpand={forceExpand} bindings={bindings}
            />
          ))}
          {node.plans.length > 0 && (
            <div style={S.grid}>
              {node.plans.map((p) => (
                <PlanCard
                  key={p.id}
                  plan={p}
                  isOpen={expandedPlans.has(p.id)}
                  files={planFiles.get(p.id) || []}
                  loading={loadingPlanIds.has(p.id)}
                  isOrphan={!p.id.includes('/')}
                  activeId={activeId}
                  togglePlan={togglePlan}
                  onOpenNote={onOpenNote}
                  onOpenPlanTab={onOpenPlanTab}
                  binding={bindings[p.id]}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function countPlans(n: DirNode): number {
  let c = n.plans.length
  for (const ch of n.children.values()) c += countPlans(ch)
  return c
}

export default function PlanSidebar({ filter, activeId, openTab }: SidebarViewProps) {
  const [list, setList] = useState<PlanEntity[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set())
  const [expandedPlans, setExpandedPlans] = useState<Set<string>>(new Set())
  const [planFiles, setPlanFiles] = useState<Map<string, PlanDetailFile[]>>(new Map())
  const [loadingPlanIds, setLoadingPlanIds] = useState<Set<string>>(new Set())
  // 活跃对话聚合(by_plan) — 取一次即可(侧栏挂载时), 各计划卡按 id 查桶显示"💬 N"。
  const [bindings, setBindings] = useState<Record<string, BossSightBindingBucket>>({})

  useEffect(() => {
    bossSightApi.activeBindings().then((d) => setBindings(d.by_plan || {})).catch(() => setBindings({}))
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch('/api/plans').then((r) => r.json()).then((d: { items: any[] }) => {
      if (cancelled) return
      const items: PlanEntity[] = d.items.map((p: any) => ({
        type: 'plan' as const,
        id: p.id,
        title: p.date ? `${p.date} ${p.topic}` : p.topic,
        topic: p.topic,
        date: p.date,
        folder_path: p.folder_path,
        archived: p.archived,
        has_plan_md: p.has_plan_md,
        file_count: p.file_count,
        tags: p.archived ? ['archived'] : [],
      }))
      setList(items)
      // default-expand top-level (_infra / domain / _cross) + project subdir 二层
      // (例 _infra/dashboard, _infra/agent-framework, domain/demogame/ux-figma)
      // round 5 plan 重组后 plan dir 在二层 project subdir 下, 不展二层 plan 看不到
      const expandSet = new Set<string>()
      for (const it of items) {
        const parts = it.id.split('/')
        if (parts.length >= 2) {
          expandSet.add(parts[0])  // top: _infra
        }
        if (parts.length >= 3) {
          expandSet.add(`${parts[0]}/${parts[1]}`)  // project: _infra/dashboard
        }
      }
      setExpandedDirs(expandSet)
      setLoading(false)
    }).catch(() => setLoading(false))
    return () => { cancelled = true }
  }, [])

  const tree = useMemo(() => {
    const ql = filter.trim().toLowerCase()
    const filtered = ql
      ? list.filter((p) => p.id.toLowerCase().includes(ql) || p.topic.toLowerCase().includes(ql))
      : list
    return buildTree(filtered)
  }, [list, filter])

  const toggleDir = (path: string) => setExpandedDirs((s) => {
    const n = new Set(s); n.has(path) ? n.delete(path) : n.add(path); return n
  })

  const togglePlan = async (id: string) => {
    setExpandedPlans((s) => {
      const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
    })
    if (planFiles.has(id)) return
    setLoadingPlanIds((s) => new Set(s).add(id))
    try {
      const r = await fetch(`/api/plans/${id}`)
      const d = await r.json()
      setPlanFiles((m) => new Map(m).set(id, d.files || []))
    } catch {
      setPlanFiles((m) => new Map(m).set(id, []))
    } finally {
      setLoadingPlanIds((s) => { const n = new Set(s); n.delete(id); return n })
    }
  }

  const onOpenNote = (noteId: string) => {
    const title = noteId.split('/').pop() || noteId
    openTab({ type: 'note', id: noteId }, title)
  }

  const onOpenPlanTab = (planId: string, title: string) => {
    // 跟 SessionContextPanel 同 pattern: openTab({type:'plan'}) → 走 plan-folder Editor
    // (含 RelatedCcSessions 反查块, 列绑定此 plan 的所有 cc_sessions)
    openTab({ type: 'plan', id: planId }, planId.split('/').pop() || title)
  }

  if (loading) return <div style={S.empty}>加载中…</div>
  if (tree.children.size === 0 && tree.plans.length === 0) {
    return <div style={S.empty}>{filter ? '无匹配' : '无 plan'}</div>
  }

  return (
    <div style={S.root} data-tree="plan">
      {[...tree.children.values()].map((c) => (
        <DirSection
          key={c.path} node={c} depth={0}
          expandedDirs={expandedDirs} expandedPlans={expandedPlans}
          toggleDir={toggleDir} togglePlan={togglePlan}
          planFiles={planFiles} loadingPlanIds={loadingPlanIds}
          activeId={activeId} onOpenNote={onOpenNote} onOpenPlanTab={onOpenPlanTab}
          forceExpand={!!filter.trim()} bindings={bindings}
        />
      ))}
      {/* root 直接子 plan = orphan(缺 project subdir), 单独成网格, 卡上带 orphan 徽章 + 红描边语义 */}
      {tree.plans.length > 0 && (
        <div style={S.grid}>
          {tree.plans.map((p) => (
            <PlanCard
              key={p.id}
              plan={p}
              isOpen={expandedPlans.has(p.id)}
              files={planFiles.get(p.id) || []}
              loading={loadingPlanIds.has(p.id)}
              isOrphan={!p.id.includes('/')}
              activeId={activeId}
              togglePlan={togglePlan}
              onOpenNote={onOpenNote}
              onOpenPlanTab={onOpenPlanTab}
              binding={bindings[p.id]}
            />
          ))}
        </div>
      )}
    </div>
  )
}
