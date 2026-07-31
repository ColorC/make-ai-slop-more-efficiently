/**
 * entities/review/ReviewQueueSidebar — 审阅材料列表(唯一真源)。
 *
 * 用户 2026-06-14: 列表收敛成一个, 放驾驶舱左栏(取代原 mini ReviewMaterialQueuePanel);
 * 点材料 = 出正文页签(B 区)+ 联动评论(C 区), 不再有"页面内的第二个侧栏"。
 * 这里 = 筛选条 + 数据/WS 接线 + 卡片网格(按 tier 分组/批量/统计), 选中项跟随
 * 共享 store 的"激活材料"。VSCode 原生形态(surface=queue)也复用本组件挂进主侧栏。
 *
 * 2026-06-29 深度重建: 拥挤的单列列表行 → 磨砂玻璃卡片网格(每 tier 一组),
 * 卡片解剖 = 类型 icon + 状态徽章 + 标题醒目 + 来源/时间弱灰微字 + ⋯ 收纳低频操作;
 * 批量操作从一排等权按钮收成头部一栏(主裁决显眼 + ⋯ 收调级/删除/退出)。
 * 数据接线/handlers/props/testid 一律不动。MaterialSidebar 仍服务 review_queue 双列页, 故未改它。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, CheckSquare, BookOpen, ChevronRight, ChevronDown, Layers } from 'lucide-react'
import {
  reviewstageApi,
  type Material,
  type MaterialStats,
  type MaterialStatus,
  type MaterialTier,
} from '../../api/reviewstageClient'
import { useReviewStream } from './streamStore'
import { useReviewActive } from '../../stores/reviewActiveStore'
import { usePanels } from '../../stores/panelsStore'
import { COLORS, TIER_LABELS, STATUS_LABELS, KIND_LABELS, tierColor, statusColor, buildRenderUnits } from './shared'
import { KindIcon } from './kindIcons'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'

type ReviewFilter = 'all' | 'archived' | MaterialStatus

// 聚合折叠: 逻辑在 shared.ts 的 buildRenderUnits (与 MaterialSidebar 共用)。

// D4/C6: 平铺队列降为"待办列表"——默认过滤 pending(待办), 其余状态仍可切。文案统一"待办"口径。
const FILTERS: Array<{ key: ReviewFilter; label: string }> = [
  { key: 'pending', label: '待办' },
  { key: 'blocked', label: '已阻断' },
  { key: 'rejected', label: '已拒绝' },
  { key: 'accepted', label: '已通过' },
  { key: 'all', label: '全部' },
  { key: 'archived', label: '已归档' },
]

const TIER_ORDER: MaterialTier[] = ['mandatory', 'important', 'processual', 'ignored']

// frostpane: 筛选 = 胶囊 chip, 渐进披露 —— 选中态填冷蓝弱底+冷蓝字, 未选中安静近无形(弱灰字+极淡底),
// 让"当前在看哪个状态"一眼跳出, 其余降级不抢视线。
const tabSm = (active: boolean): React.CSSProperties => ({
  border: `1px solid ${active ? 'color-mix(in srgb, var(--fp-accent) 35%, transparent)' : 'transparent'}`,
  background: active ? 'var(--fp-accent-weak)' : 'var(--fp-border-subtle)',
  color: active ? 'var(--fp-link)' : 'var(--fp-text-3)', borderRadius: 999, padding: '4px 12px', cursor: 'pointer',
  fontSize: 12, fontWeight: active ? 600 : 500, flexShrink: 0,
  transition: 'background var(--fp-t-fast) var(--fp-ease), color var(--fp-t-fast) var(--fp-ease)',
})
// frostpane: 图标按钮收成安静近无形, hover 才浮一层极淡玻璃(.iconbtn 手法)
const iconBtn: React.CSSProperties = {
  width: 30, height: 30, border: 0, background: 'transparent', color: 'var(--fp-text-3)', borderRadius: 7,
  cursor: 'pointer', display: 'inline-grid', placeItems: 'center', flexShrink: 0,
  transition: 'background var(--fp-t-fast) var(--fp-ease), color var(--fp-t-fast) var(--fp-ease)',
}

// frostpane 玻璃卡: 半透明冷底 + 26px 模糊 saturate190 + 顶部 inset 高光 + 11 圆角(对齐 ThreadMonitorPanel)。
const CARD_BASE: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', minWidth: 0,
  background: 'var(--fp-glass)',
  backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
  border: `1px solid ${COLORS.border}`, borderRadius: 11, padding: 12,
  boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
  cursor: 'pointer', position: 'relative',
  transition: 'border-color var(--fp-t-fast) var(--fp-ease), transform var(--fp-t-fast) var(--fp-ease)',
}
// 信息层级: 标题 15 醒目, 次级 13, 最弱 12 等宽弱灰(地板 12 禁 11)。
const S = {
  list: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(248px, 1fr))', gap: 12 } as React.CSSProperties,
  cardTop: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 } as React.CSSProperties,
  title: {
    flex: 1, minWidth: 0, color: COLORS.text, fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em',
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  } as React.CSSProperties,
  sub: { color: COLORS.textDim, fontSize: 13, marginTop: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } as React.CSSProperties,
  meta: {
    color: 'var(--fp-text-3)', fontSize: 12, marginTop: 6,
    fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace",
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  } as React.CSSProperties,
  groupHead: {
    gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 8,
    fontSize: 12, fontWeight: 600, color: 'var(--fp-text-3)', letterSpacing: '.04em', textTransform: 'uppercase',
    margin: '4px 0 0',
  } as React.CSSProperties,
}

function statusBadge(status: MaterialStatus): React.CSSProperties {
  const c = statusColor(status)
  return {
    flexShrink: 0, display: 'inline-flex', alignItems: 'center', fontSize: 12, fontWeight: 600,
    padding: '1px 8px', borderRadius: 999, lineHeight: 1.6,
    color: c, background: `color-mix(in srgb, ${c} 18%, transparent)`,
  }
}

function errText(e: unknown): string { return String(e instanceof Error ? e.message : e) }

export function ReviewQueueSidebar({ onOpenMaterial, headerActions }: { onOpenMaterial: (m: Material) => void; headerActions?: React.ReactNode }) {
  const [filter, setFilter] = useState<ReviewFilter>('pending')
  const [items, setItems] = useState<Material[]>([])
  const [stats, setStats] = useState<MaterialStats | null>(null)
  const [bulkIds, setBulkIds] = useState<string[]>([])
  const [multiselectMode, setMultiselectMode] = useState(false)
  const [expandedClusters, setExpandedClusters] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const activeId = useReviewActive((s) => s.activeMaterialId)
  const openTab = usePanels((s) => s.openTab)

  const load = useCallback(() => {
    setError(null)
    Promise.all([
      reviewstageApi.list(filter === 'all' ? {} : filter === 'archived' ? { archived_only: true } : { status: filter }),
      reviewstageApi.stats(),
    ])
      .then(([list, s]) => {
        setItems(list.items || [])
        setStats(s)
        setBulkIds((prev) => prev.filter((id) => list.items.some((it) => it.id === id)))
      })
      .catch((e) => setError(errText(e)))
  }, [filter])

  const streamVersion = useReviewStream((s) => s.version)
  useEffect(() => useReviewStream.getState().acquire(), [])
  useEffect(() => { load() }, [load])
  useEffect(() => { if (streamVersion > 0) load() }, [streamVersion, load])

  const onSelect = useCallback((id: string) => {
    const m = items.find((it) => it.id === id)
    if (m) onOpenMaterial(m)
  }, [items, onOpenMaterial])

  const toggleBulkId = useCallback((id: string) => {
    setBulkIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])
  }, [])

  const runBatch = async (fn: () => Promise<unknown>) => {
    if (bulkIds.length === 0) return
    setError(null)
    try { await fn(); setBulkIds([]); load() } catch (e) { setError(`批量操作失败: ${errText(e)}`) }
  }
  const onBatchVerdict = (verdict: MaterialStatus) => { void runBatch(() => reviewstageApi.batchVerdict(bulkIds, verdict, `batch ${verdict}`)) }
  const onBatchTier = (tier: MaterialTier) => { void runBatch(() => reviewstageApi.batchTier(bulkIds, tier)) }
  const onBatchDelete = () => { void runBatch(() => reviewstageApi.batchDelete(bulkIds, true)) }

  // 按 tier 分组 — 必验收最上(语义来自 tier 色条, 不再靠纯文字标题层占地)
  const groups = useMemo(() => {
    const g: Record<MaterialTier, Material[]> = { mandatory: [], important: [], processual: [], ignored: [] }
    for (const m of items) g[m.tier].push(m)
    return g
  }, [items])

  const enterMultiselect = useCallback((id?: string) => {
    setMultiselectMode(true)
    if (id) toggleBulkId(id)
  }, [toggleBulkId])

  // 头部批量栏的低频裁决/调级/收尾收进 ⋯, 主裁决"通过"显眼露出(渐进披露, 不再一排等权)。
  const batchKebab: KebabItem[] = [
    { label: '全选', icon: <CheckSquare size={15} />, testid: 'review-select-all', onClick: () => setBulkIds(items.map((m) => m.id)) },
    { label: '拒绝所选', onClick: () => onBatchVerdict('rejected') },
    // 阻断下达入口已删(DEC-2026-07-05-003:根本没有用);历史 blocked 状态仅在过滤器里可查看。
    ...(['mandatory', 'important', 'processual', 'ignored'] as MaterialTier[]).map((t) => ({
      label: `调级 → ${TIER_LABELS[t]}`, onClick: () => onBatchTier(t),
    })),
    { label: '删除所选', danger: true, onClick: onBatchDelete },
    { label: '清空选择', onClick: () => setBulkIds([]) },
    { label: '退出多选', testid: 'review-exit-multiselect', onClick: () => { setMultiselectMode(false); setBulkIds([]) } },
  ]

  return (
    <div data-testid="review-queue-sidebar" style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'transparent', color: COLORS.text }}>
      {/* 筛选条 = 玻璃外壳(磨砂 + 顶部高光), 与下方安静近实色的卡片网格分层 */}
      <div style={{
        flexShrink: 0, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', padding: '10px 12px',
        background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
        borderBottom: `1px solid ${COLORS.border}`, boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
      }}>
        {FILTERS.map((f) => (
          <button key={f.key} type="button" style={tabSm(filter === f.key)} onClick={() => setFilter(f.key)}>{f.label}</button>
        ))}
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          {headerActions}
          <button
            type="button"
            style={iconBtn}
            onClick={load}
            data-testid="review-queue-sidebar-refresh"
            title="刷新"
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--fp-border-subtle)'; e.currentTarget.style.color = COLORS.text }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fp-text-3)' }}
          ><RefreshCw size={14} /></button>
        </span>
      </div>

      {/* 必验收待审 = 信号(只在真有未决必验收时冒出), 非标题 */}
      {stats && stats.mandatory_unaccepted > 0 && (
        <div style={{ flexShrink: 0, padding: '6px 14px', fontSize: 13, color: COLORS.mandatory, fontWeight: 600 }} data-testid="stats-mandatory-unaccepted">
          ⚠ {stats.mandatory_unaccepted} 必验收待审
        </div>
      )}

      {/* 批量栏: 多选模式下露出; 主裁决"通过"显眼, 其余低频收进 ⋯(渐进披露) */}
      {multiselectMode && (
        <div
          data-testid="review-batch-toolbar"
          style={{
            flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px',
            borderBottom: `1px solid ${COLORS.border}`,
            background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,.06)', color: COLORS.text, fontSize: 13,
          }}
        >
          <span style={{ color: COLORS.textDim, fontSize: 13 }}>已选 {bulkIds.length}</span>
          <button
            type="button"
            onClick={() => onBatchVerdict('accepted')}
            disabled={bulkIds.length === 0}
            style={{
              marginLeft: 'auto', border: 0, borderRadius: 7, padding: '5px 14px', fontSize: 13, fontWeight: 600,
              color: 'var(--fp-accent-fg)', cursor: bulkIds.length === 0 ? 'default' : 'pointer',
              background: bulkIds.length === 0 ? 'color-mix(in srgb, var(--fp-ok) 35%, transparent)' : COLORS.accepted,
              boxShadow: bulkIds.length === 0 ? 'none' : '0 2px 10px color-mix(in srgb, var(--fp-ok) 25%, transparent)',
            }}
          >通过所选</button>
          <KebabMenu testid="review-batch-more" items={batchKebab} />
        </div>
      )}

      {error && (
        <div
          style={{ flexShrink: 0, padding: '8px 14px', color: COLORS.rejected, fontSize: 13, background: 'color-mix(in srgb, var(--fp-err) 12%, transparent)', borderBottom: `1px solid ${COLORS.border}` }}
          data-testid="review-queue-sidebar-error"
        >{error}</div>
      )}

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 12 }} data-testid="material-sidebar">
        {items.length === 0 ? (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6,
            padding: '40px 24px', color: COLORS.textDim, textAlign: 'center', fontSize: 14, lineHeight: 1.6,
          }}>
            <span style={{ fontSize: 15, color: COLORS.text }}>待办已清空</span>
            <span>没有待处理的材料。处理完的项从待办消失, 仍留在项目的材料轨迹里。</span>
          </div>
        ) : (
          <div style={S.list}>
            {TIER_ORDER.map((tier) => {
              const tierItems = groups[tier]
              if (tierItems.length === 0) return null
              return (
                <React.Fragment key={tier}>
                  <div style={S.groupHead}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: tierColor(tier), flexShrink: 0 }} />
                    {TIER_LABELS[tier]}
                    <span style={{ color: 'var(--fp-idle)', fontWeight: 500 }}>{tierItems.length}</span>
                  </div>
                  {buildRenderUnits(tierItems).flatMap((unit) => {
                    if (unit.type === 'cluster') {
                      const cid = `${tier}|${unit.key}`
                      const expanded = expandedClusters.includes(cid)
                      const header = (
                        <div
                          key={`cluster-${cid}`}
                          data-testid={`material-cluster-${unit.items[0].id}`}
                          onClick={() => setExpandedClusters((prev) =>
                            prev.includes(cid) ? prev.filter((x) => x !== cid) : [...prev, cid])}
                          style={{
                            ...CARD_BASE, gridColumn: '1 / -1', flexDirection: 'row', alignItems: 'center', gap: 10,
                            borderLeft: `3px solid ${tierColor(tier)}`,
                            background: expanded ? 'color-mix(in srgb, var(--fp-accent) 8%, var(--fp-surface))' : CARD_BASE.background,
                          }}
                        >
                          {expanded ? <ChevronDown size={15} style={{ flexShrink: 0, color: 'var(--fp-text-3)' }} /> : <ChevronRight size={15} style={{ flexShrink: 0, color: 'var(--fp-text-3)' }} />}
                          <Layers size={15} style={{ flexShrink: 0, color: 'var(--fp-violet)' }} />
                          <span style={{ ...S.title, flex: 'none', maxWidth: '40%' }}>{unit.project} · {KIND_LABELS[unit.kind]}</span>
                          <span style={{
                            flexShrink: 0, fontSize: 12, fontWeight: 700, color: 'var(--fp-violet)', borderRadius: 999, padding: '1px 9px',
                            background: 'color-mix(in srgb, var(--fp-violet) 14%, transparent)',
                            border: '1px solid color-mix(in srgb, var(--fp-violet) 38%, transparent)',
                          }}>{unit.items.length} 条</span>
                          {multiselectMode && (
                            <button
                              type="button"
                              data-testid={`material-cluster-selectall-${unit.items[0].id}`}
                              onClick={(e) => {
                                e.stopPropagation()
                                setBulkIds((prev) => Array.from(new Set([...prev, ...unit.items.map((x) => x.id)])))
                              }}
                              style={{ flexShrink: 0, border: `1px solid ${COLORS.border}`, background: 'transparent', color: 'var(--fp-text-2)', borderRadius: 6, padding: '3px 9px', fontSize: 12, cursor: 'pointer' }}
                            >整组选择</button>
                          )}
                        </div>
                      )
                      return expanded ? [header, ...unit.items] : [header]
                    }
                    return [unit.m]
                  }).map((entry) => {
                    if (React.isValidElement(entry)) return entry
                    const m = entry as Material
                    const selected = activeId === m.id
                    const inBulk = bulkIds.includes(m.id)
                    const cardKebab: KebabItem[] = multiselectMode
                      ? [{ label: inBulk ? '取消选择' : '选择此项', icon: <CheckSquare size={15} />, onClick: () => toggleBulkId(m.id) }]
                      : [
                          // 在项目工作台(阅读视图)打开并深链定位到该材料(件二 DEC-2026-07-06-082/083)。
                          // facet=材料 id → studio_reader Editor 消费 → StudioReaderPanel 优先选中该 id。
                          {
                            label: '在项目工作台打开',
                            icon: <BookOpen size={15} />,
                            testid: `material-card-open-studio-${m.id}`,
                            onClick: () => openTab({ type: 'studio_reader', id: m.project || 'unfiled' }, `${m.project || 'unfiled'} 阅读`, m.id),
                          },
                          { label: '进入多选', icon: <CheckSquare size={15} />, onClick: () => enterMultiselect(m.id) },
                        ]
                    return (
                      <div
                        key={m.id}
                        data-testid={`material-card-${m.id}`}
                        data-omni-uri={`omni://review_material/${encodeURIComponent(m.id)}`}
                        data-omni-kind="review_material"
                        data-omni-title={m.title}
                        onClick={() => onSelect(m.id)}
                        onContextMenu={(e) => { e.preventDefault(); enterMultiselect(m.id) }}
                        style={{
                          ...CARD_BASE,
                          borderColor: selected ? COLORS.borderActive : (inBulk ? 'color-mix(in srgb, var(--fp-accent) 40%, transparent)' : COLORS.border),
                          background: selected ? 'color-mix(in srgb, var(--fp-accent) 14%, var(--fp-surface))' : CARD_BASE.background,
                          borderLeft: `3px solid ${tierColor(m.tier)}`,
                        }}
                        onMouseEnter={(e) => { if (!selected) e.currentTarget.style.borderColor = 'var(--fp-border-strong)' }}
                        onMouseLeave={(e) => { if (!selected) e.currentTarget.style.borderColor = inBulk ? 'color-mix(in srgb, var(--fp-accent) 40%, transparent)' : COLORS.border }}
                      >
                        <div style={S.cardTop}>
                          {multiselectMode && (
                            <input
                              type="checkbox"
                              checked={inBulk}
                              onChange={(e) => { e.stopPropagation(); toggleBulkId(m.id) }}
                              onClick={(e) => e.stopPropagation()}
                              aria-label={`select ${m.title}`}
                              style={{ margin: 0, flexShrink: 0 }}
                            />
                          )}
                          <KindIcon kind={m.kind} />
                          <span style={S.title}>{m.title}</span>
                          <span style={statusBadge(m.status)}>{STATUS_LABELS[m.status]}</span>
                          <KebabMenu testid={`material-card-more-${m.id}`} items={cardKebab} />
                        </div>
                        <div style={S.sub} title={m.source_plan_id || undefined}>
                          {KIND_LABELS[m.kind]}
                          {m.source_plan_id && <span> · {m.source_plan_id}</span>}
                          {m.pushed_to_user && <span style={{ color: COLORS.important, marginLeft: 6 }}>📌</span>}
                        </div>
                        <div style={S.meta}>{m.id.slice(0, 12)}</div>
                      </div>
                    )
                  })}
                </React.Fragment>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
