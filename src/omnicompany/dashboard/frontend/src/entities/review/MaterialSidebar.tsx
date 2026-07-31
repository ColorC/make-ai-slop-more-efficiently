/**
 * entities/review/MaterialSidebar — 列表 sidebar + 批量操作工具条 + 结构警告徽标.
 *
 * R2 从 standalone 审阅台剪切而来 (结构搬移); R4 起 standalone 已退役,
 * 消费方为驾驶舱 review_queue / review_material 面板.
 *
 * 2026-07-19 阶段四第四波 · 蓝图 G 重置(合同 = demo/MAPPING.md 页面映射「审阅详情」队列列):
 *   · 队列行 = 整行可点(role=button + aria-current): tier 左缘色条 + 类型 icon + 标题 2 行 clamp
 *     + 状态色点;状态文字 pill 退役(筛选同值重复),副标题(kind·plan 全路径)收进 hover 预览卡
 *     (深色描图纸;D 密度收敛)。多选模式行首勾框 = v2-checkrow 同族白线方框 + hatch。
 *   · 红色警告行 → 队列构成条 .rf-compose(必验收 N / 普通 M 分段 hatch 堆叠 + mono 计数)。
 *   · tier 分区头 = mono 大写 + ⊢N⊣ 计数标注(bp-dim 黄铜) + 细分隔线。
 *   · 批量条: 原生 <select> 调级清零 → TraceMenu 四档;计数 = 黄铜徽章;动作分级色虚线件。
 */

import { useMemo, useState, isValidElement } from 'react'
import type {
  Material,
  MaterialStatus,
  MaterialTier,
  MaterialStats,
} from '../../api/reviewstageClient'
import {
  COLORS,
  TIER_LABELS,
  STATUS_LABELS,
  STATUS_V2,
  KIND_LABELS,
  type StructureWarning,
  tierColor,
  statusColor,
  planShort,
  buildRenderUnits,
} from './shared'
import { KindIcon } from './kindIcons'
import { Check, Pin, ChevronDown, ChevronRight } from 'lucide-react'
import { DimText } from '../../components/Segmented'
import { useHoverPreview } from '../../components/HoverCard'
import { TraceMenu } from './TraceMenu'
import { relTimeZh } from '../../lib/time'
import { canonicalMaterialRef, CANONICAL_REVIEW_KIND } from './materialReference'
import './reviewFlow.css'


// ── 批量操作工具条(计数徽章 + 分级动作;调级四档收 TraceMenu) ──────────────

export function BatchReviewToolbar({
  count, onAccept, onReject, onBlock, onTier, onDelete, onClear,
  multiselectMode = false, onSelectAll, onExitMultiselect,
}: {
  count: number
  onAccept: () => void
  onReject: () => void
  onBlock: () => void
  onTier: (tier: MaterialTier) => void
  onDelete: () => void
  onClear: () => void
  multiselectMode?: boolean
  onSelectAll?: () => void
  onExitMultiselect?: () => void
}) {
  // 多选模式下即便 0 选也显示(给全选/退出入口); 非多选且 0 选则隐藏。
  if (!multiselectMode && count === 0) return null
  return (
    <div className="rf-batch" data-testid="review-batch-toolbar">
      <span className="v2-count hot">已选 {count}</span>
      {onSelectAll && <button type="button" className="bb" onClick={onSelectAll} data-testid="review-select-all">全选</button>}
      <button type="button" className="bb ok" onClick={onAccept}>通过</button>
      <button type="button" className="bb err" onClick={onReject}>拒绝</button>
      {/* 阻断下达入口已删(DEC-2026-07-05-003:根本没有用) */}
      <TraceMenu
        label="批量调级"
        trigger={(open, toggle) => (
          <button type="button" className="bb" aria-expanded={open} aria-haspopup="true" onClick={toggle}>
            调级…
          </button>
        )}
      >
        {(close) => (['mandatory', 'important', 'processual', 'ignored'] as MaterialTier[]).map((t) => (
          <button
            key={t}
            type="button"
            className="v2-checkrow"
            role="radio"
            aria-checked={false}
            data-testid={`batch-tier-${t}`}
            onClick={() => { onTier(t); close() }}
          >
            <span className="cb" aria-hidden><Check size={11} strokeWidth={3} /></span>
            <span className="cr-t" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: tierColor(t), display: 'inline-block' }} aria-hidden />
              {TIER_LABELS[t]}
            </span>
          </button>
        ))}
      </TraceMenu>
      <button type="button" className="bb" onClick={onDelete}>删除</button>
      <button type="button" className="bb" onClick={onClear}>清空</button>
      {multiselectMode && onExitMultiselect && (
        <button type="button" className="bb" onClick={onExitMultiselect} data-testid="review-exit-multiselect">退出多选</button>
      )}
    </div>
  )
}


// ── 列表 sidebar (按 tier 分组, 必验收 sticky) ──────────────────────

/** 行 hover 预览卡内容(被收起的次级信息: 状态/tier 徽章 + kind·plan·时间元数据)。 */
function QueueCardPreview({ m }: { m: Material }) {
  return (
    <div>
      <div className="rf-pv-t">{m.title}</div>
      <div className="rf-pv-badges">
        <span className={`v2-status ${STATUS_V2[m.status]}`}><i className="led" aria-hidden />{STATUS_LABELS[m.status]}</span>
        <span className="v2-status st-hollow"><i className="led" aria-hidden />{TIER_LABELS[m.tier]}</span>
      </div>
      <div className="rf-pv-meta">
        <span>{KIND_LABELS[m.kind]}</span>
        {m.source_plan_id && <span title={m.source_plan_id}>plan · {planShort(m.source_plan_id)}</span>}
        {relTimeZh(m.updated_at) && <span>{relTimeZh(m.updated_at)}</span>}
        {m.pushed_to_user && <span>已推送{m.pushed_reason || ''}</span>}
      </div>
    </div>
  )
}

const finePointer = () => typeof window !== 'undefined' && window.matchMedia('(hover: hover) and (pointer: fine)').matches

/** 「新」= 24h 内新建(demo data.js new 标记的真源语义;created_at 缺失/非法时不标)。 */
function isNewMaterial(m: Material): boolean {
  const t = Date.parse(m.created_at || '')
  return Number.isFinite(t) && Date.now() - t < 24 * 3600 * 1000
}

export function MaterialSidebar({
  materials, selectedId, selectedIds, onSelect, onToggleSelect,
  onBatchVerdict, onBatchTier, onBatchDelete, onClearBatch, stats, compact = false,
  multiselectMode = false, onEnterMultiselect, onSelectAll, onExitMultiselect,
  loading = false,
}: {
  materials: Material[]
  selectedId: string | null
  selectedIds: string[]
  /** 首次列表请求进行中: 空列表显示骨架扫光而不是"还没有 material"空态文案 */
  loading?: boolean
  onSelect: (id: string) => void
  onToggleSelect: (id: string) => void
  onBatchVerdict: (verdict: MaterialStatus) => void
  onBatchTier: (tier: MaterialTier) => void
  onBatchDelete: () => void
  onClearBatch: () => void
  stats: MaterialStats | null
  compact?: boolean
  // 多选: 默认隐藏 checkbox; 右键卡片进入多选模式; 支持全选/退出
  multiselectMode?: boolean
  onEnterMultiselect?: () => void
  onSelectAll?: () => void
  onExitMultiselect?: () => void
}) {
  // 按 tier 分组 — 必验收最上
  const groups = useMemo(() => {
    const g: Record<MaterialTier, Material[]> = {
      mandatory: [], important: [], processual: [], ignored: [],
    }
    for (const m of materials) {
      g[m.tier].push(m)
    }
    return g
  }, [materials])
  // 聚合折叠(同项目同类≥3条, 2026-07-07): 折叠态在本组件内, 与驾驶舱左栏一致
  const [expandedClusters, setExpandedClusters] = useState<string[]>([])
  const preview = useHoverPreview()

  // 队列构成(必验收待审 vs 普通待审): stats 优先(服务端全量口径), 缺轮回落已加载列表。
  const compose = useMemo(() => {
    let mand = stats?.mandatory_unaccepted
    let norm: number | undefined
    if (stats?.by_status) {
      const pendingTotal = Number(stats.by_status.pending || 0)
      norm = Math.max(0, pendingTotal - (mand || 0))
    }
    if (mand == null) {
      mand = 0; norm = 0
      for (const m of materials) {
        if (m.archived || m.status !== 'pending') continue
        if (m.tier === 'mandatory') mand += 1
        else norm += 1
      }
    }
    return { mand, norm: norm ?? 0 }
  }, [stats, materials])

  return (
    <div className="rf-side" style={{
      width: compact ? '100%' : 320,
      height: '100%',
      flex: '1 1 0%',
      borderRight: compact ? 'none' : `1px solid ${COLORS.border}`,
      display: 'flex', flexDirection: 'column',
      color: COLORS.text,
      minWidth: 0, minHeight: 0,
      overflow: 'hidden',
    }} data-testid="material-sidebar">
      {/* 队列构成条(替代红色警告行): 必验收/普通分段 hatch 堆叠 + mono 计数;
          仍只在真有未决必验收时冒出来(是信号不是标题),testid 锚点保留。 */}
      {compose.mand > 0 && (
        <div className="rf-compose" data-testid="stats-mandatory-unaccepted">
          <span className="bar" aria-hidden="true">
            <i className="mand" style={{ width: `${(compose.mand / Math.max(1, compose.mand + compose.norm)) * 100}%` }} />
            <i className="norm" style={{ width: `${(compose.norm / Math.max(1, compose.mand + compose.norm)) * 100}%` }} />
          </span>
          <span className="ct"><i className="led" style={{ background: 'var(--fp-err)' }} />必验收 <b>{compose.mand}</b></span>
          <span className="ct"><i className="led" style={{ background: 'var(--fp-warn)' }} />普通 <b>{compose.norm}</b></span>
        </div>
      )}
      <BatchReviewToolbar
        count={selectedIds.length}
        onAccept={() => onBatchVerdict('accepted')}
        onReject={() => onBatchVerdict('rejected')}
        onBlock={() => onBatchVerdict('blocked')}
        onTier={onBatchTier}
        onDelete={onBatchDelete}
        onClear={onClearBatch}
        multiselectMode={multiselectMode}
        onSelectAll={onSelectAll}
        onExitMultiselect={onExitMultiselect}
      />
      <div className="fp-scroll" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {(['mandatory', 'important', 'processual', 'ignored'] as MaterialTier[]).map((tier) => {
          const items = groups[tier]
          if (items.length === 0) return null
          return (
            <div key={tier}>
              {/* tier 分区头(mono 大写 + ⊢N⊣ 计数标注;chrome 区) */}
              <div className="rf-sechead">
                <span className="nm" style={{ color: tierColor(tier) }}>{TIER_LABELS[tier]}</span>
                <DimText>{items.length}</DimText>
                <span className="ln" aria-hidden="true" />
              </div>
              {buildRenderUnits(items).flatMap((unit) => {
                if (unit.type === 'cluster') {
                  const cid = `${tier}|${unit.key}`
                  const expanded = expandedClusters.includes(cid)
                  const header = (
                    <div
                      key={`cluster-${cid}`}
                      className="rf-qrow rf-cluster"
                      role="button"
                      tabIndex={0}
                      aria-expanded={expanded}
                      data-testid={`material-cluster-${unit.items[0].id}`}
                      onClick={() => setExpandedClusters((prev) =>
                        prev.includes(cid) ? prev.filter((x) => x !== cid) : [...prev, cid])}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setExpandedClusters((prev) =>
                            prev.includes(cid) ? prev.filter((x) => x !== cid) : [...prev, cid])
                        }
                      }}
                    >
                      <span className="q-tdot" style={{ background: tierColor(tier) }} role="img" aria-label={TIER_LABELS[tier]} />
                      <span style={{ flexShrink: 0, color: 'var(--fp-text-3)', display: 'inline-flex', marginTop: 3 }}>
                        {expanded ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
                      </span>
                      <span className="q-main">
                        <span className="q-t" style={{ WebkitLineClamp: 1 }}>
                          {unit.project} · {KIND_LABELS[unit.kind]}
                        </span>
                      </span>
                      <span className="v2-count c-n" aria-label={`${unit.items.length} 条`}>{unit.items.length}</span>
                    </div>
                  )
                  return expanded ? [header, ...unit.items] : [header]
                }
                return [unit.m]
              }).map((entry) => {
                if (isValidElement(entry)) return entry
                const m = entry as Material
                const selected = selectedId === m.id
                const checked = selectedIds.includes(m.id)
                return (
                  <div
                    key={m.id}
                    className="rf-qrow"
                    role="button"
                    tabIndex={0}
                    aria-current={selected}
                    aria-checked={multiselectMode ? checked : undefined}
                    data-testid={`material-card-${m.id}`}
                    data-omni-uri={canonicalMaterialRef(m.id)}
                    data-omni-kind={CANONICAL_REVIEW_KIND}
                    data-omni-title={m.title}
                    onClick={() => onSelect(m.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(m.id) } }}
                    onContextMenu={(e) => {
                      // 右键: 进入多选模式(不弹系统菜单)
                      if (onEnterMultiselect) { e.preventDefault(); onEnterMultiselect(); onToggleSelect(m.id) }
                    }}
                    onMouseEnter={(e) => { if (finePointer()) preview.show(e.currentTarget, <QueueCardPreview m={m} />) }}
                    onMouseLeave={() => preview.scheduleHide()}
                  >
                    {/* 多选勾框: 默认隐藏, 右键进入多选模式才显示(v2-checkrow 同族) */}
                    {multiselectMode && (
                      <span
                        className="cb"
                        aria-hidden="true"
                        onClick={(e) => { e.stopPropagation(); onToggleSelect(m.id) }}
                      ><Check size={11} strokeWidth={3} /></span>
                    )}
                    {/* tier 10px 色点(demo .rv-qrow .tier 真源化; 3px 左缘条 2026-07-20 波六退役) */}
                    <span className="q-tdot" style={{ background: tierColor(m.tier) }} role="img" aria-label={TIER_LABELS[m.tier]} />
                    {/* 类型 icon (lucide, 2026-07-18 起全项目统一; 分型交给 icon, 不再占文字行) */}
                    <span className="q-ic"><KindIcon kind={m.kind} size={15} /></span>
                    <span className="q-main">
                      <span className="q-t">{m.title}</span>
                    </span>
                    {/* 「新」标注框(demo .bp-newtag 真源化): 24h 内新建材料; 与尺寸标注同族细线小方框 */}
                    {isNewMaterial(m) && <span className="bp-newtag" aria-label="新材料">新</span>}
                    {m.pushed_to_user && (
                      <span className="q-pin" role="img" aria-label={`已推送${m.pushed_reason || ''}`}><Pin size={12} aria-hidden /></span>
                    )}
                    {/* 状态 = 色点(筛选同值的文字 pill 已退役; 全文在 hover 预览卡) */}
                    <span
                      className="q-sdot"
                      style={{ background: statusColor(m.status) }}
                      role="img"
                      aria-label={STATUS_LABELS[m.status]}
                    />
                  </div>
                )
              })}
            </div>
          )
        })}
        {materials.length === 0 && (loading ? (
          <div className="rf-skel" data-testid="material-sidebar-skeleton">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="fp-skeleton" style={{ height: 64 }} />
            ))}
          </div>
        ) : (
          <div className="rf-empty">
            还没有 material. 跟总控对话, 让它派 subagent 干活并产出.
          </div>
        ))}
      </div>
      {preview.host}
    </div>
  )
}


// ── 结构警告徽标 (MaterialDetail 头部下方) ──────────────────────────

export function StructureWarningsBadge({ warnings }: { warnings: StructureWarning[] }) {
  if (warnings.length === 0) return null
  return (
    <details className="rf-warnings" data-testid="structure-warnings">
      <summary>
        结构警告 ({warnings.length})
      </summary>
      <div style={{ padding: '0 10px 8px', display: 'grid', gap: 6 }}>
        {warnings.map((w, i) => (
          <div key={`${w.code || 'warning'}-${i}`} className="wrow">
            <b>{w.code || 'warning'}</b>
            {w.path && <span> · {w.path}</span>}
            <span> · {w.message || 'structure warning'}</span>
          </div>
        ))}
      </div>
    </details>
  )
}
