/**
 * ReviewOverview — 公众号式审阅总览(独立总览页签)。多头条 + 按时间排列 + 瀑布流紧密铺。
 * 选型(用户 2026-06-25):B1 多头条;反馈:封面要有、内容间不要有缝隙、已通过/拒绝默认不显示可展开。
 *   → CSS columns 瀑布流(无网格空洞),hero 用 column-span 横跨成头条带;按 updated_at 倒序;
 *     默认只显示未决(pending/blocked),已通过/已拒绝折叠成「已归档 N」可展开。
 * 见 docs/plans/dashboard/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (公众号总览 R4)。
 *
 * 当前交互约束:
 *   - 布局使用一条紧密瀑布流,不额外铺设筛选结果摘要或分区控制条。
 *   - 审阅/总览之间只用一个紧凑图标按钮往返;排序、归档与刷新封面收进共享 KebabMenu 的 ⋯。
 *   - 总览不重复渲染标题栏；操作悬浮在内容右上角，不占一整行。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { COLORS } from './shared'
import { MaterialOverviewCard } from './MaterialOverviewCard'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { Archive, EyeOff, RefreshCw, LayoutGrid, Clock, ListChecks } from 'lucide-react'

// 模块级稳定引用 —— 默认取数,避免每次 render 造新闭包让 effect 死循环。
const defaultFetch = (): Promise<Material[]> => reviewstageApi.list().then((r) => r.items)
const ARCHIVED = new Set(['accepted', 'rejected'])

// 分区 = 重要度。区头按 rank 降序铺(必验收最先), 给每区一条带 tier 色的安静分组头。
const TIER_ORDER: Material['tier'][] = ['mandatory', 'important', 'processual', 'ignored']

// frostpane 呈现层: 阅读区留安静近实色；低频操作只占右上角一个小浮层。
const S: Record<string, React.CSSProperties> = {
  root: {
    height: '100%', overflow: 'auto', position: 'relative',
    background: 'transparent', color: COLORS.text,
  },
  // 不做横贯页面的标题栏。操作只占内容右上角的小块，并允许瀑布流从第一行开始。
  actions: {
    position: 'sticky', top: 8, zIndex: 3, float: 'right', display: 'inline-flex',
    alignItems: 'center', gap: 6, margin: '8px 12px -44px 8px', padding: 4,
    border: `1px solid ${COLORS.border}`, borderRadius: 8,
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  },
  body: { padding: 16, minHeight: '100%' },
  error: { color: COLORS.rejected, fontSize: 13, marginBottom: 12 },
  empty: { color: COLORS.textDim, fontSize: 13 },
  // 分区: 区间 24 呼吸。
  section: { marginBottom: 24 },
  // 分组头: 安静一条 — tier 色点 + 名(13 次级) + 计数(12 弱灰), 下细分隔。
  sectionHead: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '0 2px 8px',
    marginBottom: 12, borderBottom: `1px solid ${COLORS.border}`,
  },
  sectionDot: { width: 8, height: 8, borderRadius: 999, flex: 'none' },
  sectionName: { fontSize: 13, fontWeight: 600, color: COLORS.text, letterSpacing: '-0.005em' },
  sectionCount: { fontSize: 12, color: COLORS.textDim },
  // CSS columns 瀑布流: 列间留 12 缝呼吸 (hero 卡内 column-span:all 横跨成头条带)。
  waterfall: { columns: '300px', columnGap: 12 } as React.CSSProperties,
}

function ts(m: Material): number {
  const t = Date.parse(m.updated_at || m.created_at || '')
  return Number.isNaN(t) ? 0 : t
}

/** 按时间倒序(稳定:同时间保持原序)。 */
function byTimeDesc(items: Material[]): Material[] {
  return items
    .map((m, i) => ({ m, i }))
    .sort((a, b) => ts(b.m) - ts(a.m) || a.i - b.i)
    .map((x) => x.m)
}

/** 两份列表渲染上是否等价(只比影响展示的字段)。等价就别 setState → 不重渲染 → 卡片不闪。 */
function sameList(a: Material[], b: Material[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    const x = a[i]; const y = b[i]
    if (x.id !== y.id || x.updated_at !== y.updated_at || x.status !== y.status
        || x.tier !== y.tier || x.title !== y.title || x.kind !== y.kind) return false
  }
  return true
}

// 瀑布流容器 —— 必须在组件外定义。曾定义在组件内 → 每次 render 都是"新组件类型",
// React 卸载重挂整棵卡片子树(每张 <img> 重新加载 = 用户看到的"每隔一会儿重载一次")。
// 提到模块级后, 同一组件类型 + 稳定 key → 重渲染只 reconcile, 不重挂, 封面 loaded 态保留, 不闪。
function Waterfall({ list, coverNonce, onOpen }: {
  list: Material[]; coverNonce: number; onOpen?: (m: Material) => void
}) {
  return (
    <div style={S.waterfall} data-testid="overview-waterfall">
      {list.map((m) => (
        <MaterialOverviewCard key={m.id} m={m} onOpen={onOpen} coverNonce={coverNonce} />
      ))}
    </div>
  )
}

export default function ReviewOverview({
  fetcher,
  pollMs = 0,
  onOpen,
  onOpenReview,
}: {
  fetcher?: () => Promise<Material[]>
  pollMs?: number
  onOpen?: (m: Material) => void
  onOpenReview?: () => void
}) {
  const [items, setItems] = useState<Material[]>([])
  const [err, setErr] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [coverNonce, setCoverNonce] = useState(0)
  // 视图: 默认按重要度分组(深度重建主视图), 另留按时间单流。
  const [order, setOrder] = useState<'priority' | 'time'>('priority')

  const load = useCallback(async () => {
    try {
      const list = await (fetcher ?? defaultFetch)()
      // 轮询拿到等价数据就保持旧引用 → 不触发重渲染 → 卡片不闪(关键修复)。
      setItems((prev) => (sameList(prev, list || []) ? prev : (list || [])))
      setErr('')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [fetcher])

  useEffect(() => {
    void load()
    if (pollMs > 0) {
      const t = setInterval(() => void load(), pollMs)
      return () => clearInterval(t)
    }
    return undefined
  }, [load, pollMs])

  // items 引用不变(等价轮询)就不重算 → 衍生数组引用稳定 → 子树不被无谓重渲染。
  const ordered = useMemo(() => byTimeDesc(items), [items])
  const archivedCount = useMemo(() => ordered.filter((m) => ARCHIVED.has(m.status)).length, [ordered])
  const visible = useMemo(
    () => (showArchived ? ordered : ordered.filter((m) => !ARCHIVED.has(m.status))),
    [ordered, showArchived],
  )
  const arranged = useMemo(
    () => order === 'time' ? visible : TIER_ORDER.flatMap((tier) => visible.filter((m) => m.tier === tier)),
    [order, visible],
  )

  const refreshVisibleCovers = useCallback(async () => {
    const ids = visible.map((m) => m.id)
    if (!ids.length) return
    try {
      await reviewstageApi.refreshCovers(ids)
      setCoverNonce((n) => n + 1)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [visible])

  // 低频操作收进 ⋯: 展开/隐藏已归档(有归档才出) + 显式强制刷新封面。
  // 常规封面由提交时写入的后台队列生成；打开总览本身不再触发生成。
  const kebabItems: KebabItem[] = [
    {
      label: order === 'priority' ? '按时间排列' : '按优先级排列',
      icon: order === 'priority' ? <Clock size={15} /> : <LayoutGrid size={15} />,
      testid: 'overview-order-toggle',
      onClick: () => setOrder((value) => value === 'priority' ? 'time' : 'priority'),
    },
    ...(archivedCount > 0
      ? [{
          label: showArchived ? '隐藏已归档' : `展开已归档 ${archivedCount}`,
          icon: showArchived ? <EyeOff size={15} /> : <Archive size={15} />,
          testid: 'overview-archived-toggle',
          onClick: () => setShowArchived((v) => !v),
        } as KebabItem]
      : []),
    {
      label: '刷新封面',
      icon: <RefreshCw size={15} />,
      testid: 'overview-refresh-covers',
      onClick: () => { void refreshVisibleCovers() },
    },
  ]

  return (
    <div className="fp-scroll" data-testid="review-overview" style={S.root}>
      <div style={S.actions} data-testid="overview-actions">
        {onOpenReview && (
          <button
            type="button"
            className="v2-iconbtn"
            data-testid="overview-view-review"
            onClick={onOpenReview}
            title="返回审阅"
            aria-label="返回审阅"
          >
            <ListChecks size={15} aria-hidden />
          </button>
        )}
        <KebabMenu testid="overview-more" items={kebabItems} />
      </div>
      <div style={S.body}>
        {err && (
          <div data-testid="overview-error" style={S.error}>
            取数失败: {err}
          </div>
        )}
        {visible.length === 0 && !err && (
          <div style={S.empty}>
            {items.length === 0 ? '暂无审阅材料。' : '没有待审材料(已通过/拒绝的在「已归档」里)。'}
          </div>
        )}
        {/* One priority-ordered waterfall packs every tier without section-sized holes. */}
        {arranged.length > 0 && <Waterfall list={arranged} coverNonce={coverNonce} onOpen={onOpen} />}
      </div>
    </div>
  )
}
