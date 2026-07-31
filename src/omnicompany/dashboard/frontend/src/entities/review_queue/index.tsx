import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { ExternalLink, FileSearch, RefreshCw, Archive, ArchiveRestore, Trash2, Crosshair, Search, LayoutGrid } from 'lucide-react'
import { VscodeIcon } from '../../components/VscodeIcon'
import { openMaterialNative } from '../../lib/surface'
import {
  reviewstageApi,
  type Material,
  type MaterialStats,
  type MaterialStatus,
  type MaterialTier,
  type CommentFeedbackStatus,
} from '../../api/reviewstageClient'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { usePanels } from '../../stores/panelsStore'
import { useReviewQueueFocus } from '../../stores/reviewQueueFocusStore'
// 直连具体文件, 不走 ../review 的 export * barrel(2026-07 首屏拆包: 见 CockpitShell 同注)。
import { MaterialDetail } from '../review/MaterialDetail'
import { MaterialSidebar } from '../review/MaterialSidebar'
import { useReviewStream } from '../review/streamStore'
import { materialTabTitle } from '../review_material'
import { VSplitter } from '../../shell/Splitter'
import { SidebarToggleButton, TabSidecarToggleButton } from '../../shell/TabSidecar'
import { PickerMenu } from '../../components/PickerMenu'
import ReviewOverview from '../review/ReviewOverview'
import '../review/reviewFlow.css'

export interface ReviewQueueEntity extends Entity {
  type: 'review_queue'
}

const SINGLE: ReviewQueueEntity = {
  type: 'review_queue',
  id: 'main',
  title: 'Review Queue',
  tags: ['boss-sight', 'review'],
}

const resolver: EntityResolver<ReviewQueueEntity> = {
  type: 'review_queue',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`review_queue: unknown id ${id}`)
  },
  async list() {
    return [SINGLE]
  },
}

// 2026-07-19 阶段四第四波 · 蓝图 G 重置(合同=demo/MAPPING.md 页面映射「审阅详情」):
//   · 侧栏工具行只保留搜索、状态菜单和紧凑图标按钮,不展示额外的筛选/结果摘要块。
//   · 列表卡/详情/裁决的蓝图化在共享件 MaterialSidebar / MaterialDetail(同波重置)。
//   · 队列栏 = chrome/scene 区铺格纸(.rf-tools/.rf-side);行卡文字面平滑纸面禁纹理。

// 状态"桶": 归档件归"已归档"桶(不论其 verdict), 未归档件按 verdict 状态归桶 ——
// 与旧的一排 tab 语义一致, 但允许多选组合。列表一次拉全量(include_archived),
// 桶切换纯客户端过滤, 不重新请求。
type StatusBucket = MaterialStatus | 'archived'

const STATUS_BUCKETS: Array<{ key: StatusBucket; label: string }> = [
  { key: 'pending', label: '待审' },
  { key: 'blocked', label: '受阻' },
  { key: 'rejected', label: '已拒' },
  { key: 'accepted', label: '已过' },
  { key: 'archived', label: '已归档' },
]
const ALL_BUCKETS: StatusBucket[] = STATUS_BUCKETS.map((b) => b.key)
export const DEFAULT_REVIEW_STATUS_BUCKETS: readonly StatusBucket[] = ['pending']

function bucketOf(m: Material): StatusBucket {
  return m.archived ? 'archived' : m.status
}

function webReviewTargetId(m: Material): string | null {
  const explicit = m.extra?.web_review_id
  if (typeof explicit === 'string' && explicit.trim()) return explicit.trim()
  const liveUrl = typeof m.extra?.live_url === 'string' ? m.extra.live_url : ''
  if (liveUrl.startsWith('/walker-game')) return 'walker-game'
  if (liveUrl.startsWith('/vilo-os')) return 'vilo-os'
  if (liveUrl.startsWith('/vilo-demo')) return 'vilo-demo'
  return null
}

function errText(e: unknown): string {
  return String(e instanceof Error ? e.message : e)
}

function ReviewQueuePanel({
  initialSelectedId,
  focusNonce,
  onOpenOverview,
}: {
  initialSelectedId?: string
  focusNonce?: number
  onOpenOverview?: () => void
}) {
  // 所有入口都默认只看待审。外部聚焦只定位材料,不得偷偷放开其他状态。
  const [statusSel, setStatusSel] = useState<Set<StatusBucket>>(
    () => new Set<StatusBucket>(DEFAULT_REVIEW_STATUS_BUCKETS),
  )
  const [search, setSearch] = useState('')
  const [leftWidth, setLeftWidth] = useState(360)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [items, setItems] = useState<Material[]>([])
  const [stats, setStats] = useState<MaterialStats | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [bulkIds, setBulkIds] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)

  // 每桶计数(基于已加载全量, 显示在多选菜单里)。
  const bucketCounts = useMemo(() => {
    const counts = new Map<StatusBucket, number>()
    for (const m of items) counts.set(bucketOf(m), (counts.get(bucketOf(m)) || 0) + 1)
    return counts
  }, [items])

  // 过滤 = 状态桶多选 ∩ 搜索(空格分词, 每个词都要命中标题/计划/项目/轨道/版本族/
  // kind/id 任一字段的子串, 不区分大小写)。全在客户端, 即输即得。
  const visibleItems = useMemo(() => {
    const terms = search.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return items.filter((m) => {
      if (!statusSel.has(bucketOf(m))) return false
      if (terms.length === 0) return true
      const hay = [m.title, m.id, m.project, m.track, m.version_family, m.kind, m.source_plan_id]
        .filter(Boolean).join('\n').toLowerCase()
      return terms.every((t) => hay.includes(t))
    })
  }, [items, search, statusSel])

  const selected = useMemo(
    () => visibleItems.find((item) => item.id === selectedId) || visibleItems[0] || null,
    [visibleItems, selectedId],
  )

  // 列表响应默认裁剪了 inline_content(后端 _LIST_INLINE_CLIP, 治 22MB 列表拖慢开页);
  // 选中的那条补拉一次全量给详情栏。全量到达前先用裁剪版顶着(渐进显示, 不闪空)。
  const [selectedFull, setSelectedFull] = useState<Material | null>(null)
  useEffect(() => {
    let alive = true
    if (!selected) { setSelectedFull(null); return undefined }
    if (!(selected as any).inline_content_clipped) { setSelectedFull(null); return undefined }
    reviewstageApi.get(selected.id)
      .then((f) => { if (alive) setSelectedFull(f) })
      .catch(() => { /* 拉全量失败就继续用裁剪版, 详情最多少尾巴 */ })
    return () => { alive = false }
  }, [selected])
  const selectedForDetail = selectedFull && selected && selectedFull.id === selected.id ? selectedFull : selected

  const selectedWebReviewId = selected ? webReviewTargetId(selected) : null

  const load = () => {
    setLoading(true)
    setError(null)
    // 列表与 stats 各自独立到达: 列表一到就渲染, 不等慢的 _stats(实测 0.8-2.2s,
    // 曾用 Promise.all 绑一起, 把开页列表拖慢好几秒)。stats 只喂"必验收待审"徽标, 晚到无妨。
    reviewstageApi.stats().then(setStats).catch(() => { /* 徽标缺一轮不碍事 */ })
    reviewstageApi.list({ include_archived: true, limit: 1000 })
      // 一次拉全量(含归档, limit 提到 1000 免得默认 200 截断搜索面), 状态桶/搜索
      // 都在客户端过滤 —— 切换筛选零请求。
      .then((list) => {
        setItems(list.items || [])
        setSelectedId((prev) => {
          if (prev && list.items.some((item) => item.id === prev)) return prev
          if (initialSelectedId && list.items.some((item) => item.id === initialSelectedId)) return initialSelectedId
          return list.items[0]?.id || null
        })
        setBulkIds((prev) => prev.filter((id) => list.items.some((item) => item.id === id)))
      })
      .catch((e) => setError(errText(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [initialSelectedId])

  // R3: WS 实时流。连接生命周期在 streamStore(引用计数, 不受本面板 onlyWhenVisible
  // 卸载牵连); 这里只订阅 version, 每个流事件触发一次重拉(带当前 filter 的服务端过滤
  // + stats)。手动 Refresh 按钮保留。
  const streamVersion = useReviewStream((s) => s.version)
  useEffect(() => useReviewStream.getState().acquire(), [])
  useEffect(() => {
    if (streamVersion > 0) load()
  }, [streamVersion])

  // 单例 tab 下被要求聚焦某材料(点不同材料链接): 只更新定位,保留用户当前筛选。
  // 初次进入时筛选永远是「待审」；focusNonce 让同一材料再次点击也重定位。
  useEffect(() => {
    if (!initialSelectedId) return
    setSelectedId(initialSelectedId)
  }, [initialSelectedId, focusNonce])

  const replaceItem = (updated: Material) => {
    setItems((prev) => prev.map((item) => item.id === updated.id ? updated : item))
  }

  // R3.5: 明细操作走共享 MaterialDetail 的回调形状(与 review_material 面板同源)。
  const onVerdict = useCallback(async (verdict: MaterialStatus, reason: string) => {
    if (!selected) return
    setError(null)
    try {
      replaceItem(await reviewstageApi.setVerdict(selected.id, verdict, reason || 'cockpit review queue'))
      setStats(await reviewstageApi.stats())
    } catch (e) {
      setError(`verdict 失败: ${errText(e)}`)
    }
  }, [selected])

  const onCommentSubmit = useCallback(async (content: string, target?: Record<string, unknown>) => {
    if (!selected) return
    setError(null)
    try {
      // 评论进审阅台 → comment_added → reviewstage.comment → ControllerWaker 唤起唯一总控
      // (P1 已验证), 人↔总控反馈闭环在驾驶舱内闭合。
      await reviewstageApi.addComment(selected.id, content, target)
      replaceItem(await reviewstageApi.get(selected.id))
    } catch (e) {
      setError(`评论失败: ${errText(e)}`)
    }
  }, [selected])

  const onFeedbackChange = useCallback(async (commentId: string, status: CommentFeedbackStatus) => {
    if (!selected) return
    setError(null)
    try {
      await reviewstageApi.setCommentFeedback(selected.id, commentId, status)
      replaceItem(await reviewstageApi.get(selected.id))
    } catch (e) {
      setError(`反馈状态失败: ${errText(e)}`)
    }
  }, [selected])

  const onTierChange = useCallback(async (tier: MaterialTier) => {
    if (!selected) return
    setError(null)
    try {
      replaceItem(await reviewstageApi.setTier(selected.id, tier))
      setStats(await reviewstageApi.stats())
    } catch (e) {
      setError(`调级失败: ${errText(e)}`)
    }
  }, [selected])

  // R3.5: 多选批量(共享 BatchReviewToolbar 内嵌在 MaterialSidebar)。
  const toggleBulkId = useCallback((id: string) => {
    setBulkIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])
  }, [])

  const runBatch = async (fn: () => Promise<unknown>) => {
    if (bulkIds.length === 0) return
    setError(null)
    try {
      await fn()
      setBulkIds([])
      load()
    } catch (e) {
      setError(`批量操作失败: ${errText(e)}`)
    }
  }
  const onBatchVerdict = (verdict: MaterialStatus) => { void runBatch(() => reviewstageApi.batchVerdict(bulkIds, verdict, `batch ${verdict}`)) }
  const onBatchTier = (tier: MaterialTier) => { void runBatch(() => reviewstageApi.batchTier(bulkIds, tier)) }
  const onBatchDelete = () => { void runBatch(() => reviewstageApi.batchDelete(bulkIds, true)) }

  const setArchivedSelected = async (archived: boolean) => {
    if (!selected) return
    setError(null)
    try {
      await reviewstageApi.setArchived(selected.id, archived)
      // 归档后(在非归档视图)它会从列表消失; 还原后(在已归档视图)同理。重载列表。
      setSelectedId(null)
      load()
    } catch (e) {
      setError(errText(e))
    }
  }

  const deleteSelected = async () => {
    if (!selected) return
    if (typeof window !== 'undefined' && !window.confirm('删除这条审阅材料? 不可恢复(会删文件)。如只想隐藏请用"归档"。')) return
    setError(null)
    try {
      await reviewstageApi.remove(selected.id)
      setSelectedId(null)
      load()
    } catch (e) {
      setError(errText(e))
    }
  }

  const openSource = () => {
    if (!selected) return
    if (selected.source_plan_id) {
      openTab({ type: 'plan', id: selected.source_plan_id }, selected.source_plan_id)
    } else if (selected.source_subagent_id) {
      openTab({ type: 'cc_session', id: selected.source_subagent_id }, selected.source_subagent_id)
    }
  }

  const showSidebar = sidebarOpen || !selected

  // 审阅材料动作 —— 顶栏精简(DEC-2026-07-05-003): 除通过/驳回外一律收进「更多」。
  const reviewMoreItems = selected ? [
    {
      label: '在 VSCode 编辑页签打开',
      icon: <VscodeIcon size={15} />,
      testid: 'review-queue-open-vscode',
      onClick: () => openMaterialNative(selected.id, selected.title),
    },
    ...(selected.source_plan_id || selected.source_subagent_id ? [{
      label: '跳到源(计划/会话)',
      icon: <Crosshair size={15} />,
      testid: 'review-queue-source',
      onClick: openSource,
    }] : []),
    selected.archived
      ? { label: '取消归档', icon: <ArchiveRestore size={15} />, testid: 'review-queue-unarchive', onClick: () => { void setArchivedSelected(false) } }
      : { label: '归档', icon: <Archive size={15} />, testid: 'review-queue-archive', onClick: () => { void setArchivedSelected(true) } },
    {
      label: selectedWebReviewId ? '在页签打开网页本体' : '在页签打开材料',
      icon: selectedWebReviewId ? <ExternalLink size={15} /> : <FileSearch size={15} />,
      testid: 'review-queue-open-detail-tab',
      onClick: () => selectedWebReviewId
        ? openTab({ type: 'web_review', id: selectedWebReviewId }, selected.title)
        : openTab({ type: 'review_material', id: selected.id }, materialTabTitle(selected.title)),
    },
    {
      label: '删除(不可恢复, 会删文件)',
      icon: <Trash2 size={15} />,
      testid: 'review-queue-delete',
      danger: true,
      onClick: () => { void deleteSelected() },
    },
  ] : undefined

  const overviewButton = onOpenOverview ? (
    <button type="button" className="v2-iconbtn" onClick={onOpenOverview} data-testid="review-queue-open-overview" title="切到审阅总览" aria-label="切到审阅总览">
      <LayoutGrid size={15} aria-hidden />
    </button>
  ) : null

  const detailHeaderLeft = (
    <>
      <SidebarToggleButton
        side="left"
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((value) => !value)}
        label="材料列表"
        testId="review-queue-toggle-sidebar"
      />
      {overviewButton}
      <TabSidecarToggleButton
        label="评价与批注"
        showWhen="collapsed"
        testId="review-comments-toggle"
      />
    </>
  )

  return (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', background: 'transparent', color: 'var(--fp-text)', fontFamily: 'var(--fp-font-sans)', overflow: 'hidden' }} data-testid="review-queue-panel">
      {error && (
        <div style={{ padding: '8px 12px', color: 'var(--fp-err)', fontSize: 'var(--fp-fs-3)', borderBottom: '1px solid var(--fp-border-subtle)' }} data-testid="review-queue-error">{error}</div>
      )}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {showSidebar && (
          <div style={{ display: 'flex', flexDirection: 'column', width: leftWidth, flexShrink: 0, minHeight: 0, borderRight: '1px solid var(--fp-border-subtle)' }}>
            {/* 搜索/状态沿用列表自己的紧凑工具行；不为视图切换另占一行。 */}
            <div className="rf-tools rf-filter-tools v2-filterbar">
              <label className="v2-search" style={{ maxWidth: 'none' }}>
                <Search size={13} aria-hidden />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="搜标题/计划/项目…"
                  data-testid="review-queue-search"
                />
              </label>
              <PickerMenu
                label="状态"
                selected={ALL_BUCKETS.filter((b) => statusSel.has(b))}
                onChange={(next) => setStatusSel(new Set(next as StatusBucket[]))}
                options={STATUS_BUCKETS.map((b) => ({ value: b.key, label: b.label, count: bucketCounts.get(b.key) || 0 }))}
              />
              <button type="button" className="v2-iconbtn" onClick={load} data-testid="review-queue-refresh" title="刷新" aria-label="刷新">
                <RefreshCw size={15} aria-hidden />
              </button>
            </div>
            <MaterialSidebar
              materials={visibleItems}
              loading={loading}
              selectedId={selected?.id || null}
              selectedIds={bulkIds}
              onSelect={setSelectedId}
              onToggleSelect={toggleBulkId}
              onBatchVerdict={onBatchVerdict}
              onBatchTier={onBatchTier}
              onBatchDelete={onBatchDelete}
              onClearBatch={() => setBulkIds([])}
              stats={stats}
              compact
            />
          </div>
        )}
        {showSidebar && <VSplitter side="right" onResize={(d) => setLeftWidth((w) => Math.max(240, Math.min(820, w + d)))} />}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }} data-testid="review-queue-detail">
          {!selected && <div className="rf-empty">选择一条审阅材料。</div>}
          {selected && (
            <MaterialDetail
              material={selectedForDetail}
              headerLeft={detailHeaderLeft}
              moreItems={reviewMoreItems}
              onVerdict={onVerdict}
              onCommentSubmit={onCommentSubmit}
              onFeedbackChange={onFeedbackChange}
              onTierChange={onTierChange}
              source={null}
              onReturnToSource={() => { /* 已在队列里 */ }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

const Editor: React.FC<{ entity: ReviewQueueEntity; facet?: string }> = ({ facet }) => {
  // 聚焦哪条材料走 store(单例 tab, 不靠 facet 拼 tab id)。facet 作首开兜底。
  const focusedId = useReviewQueueFocus((s) => s.focusedId)
  const nonce = useReviewQueueFocus((s) => s.nonce)
  // 审阅台默认行为保持队列/详情；总览只是显式按钮切入的同页视图。
  const [mode, setMode] = useState<'overview' | 'queue'>('queue')
  useEffect(() => {
    if (focusedId) setMode('queue')
  }, [focusedId, nonce])

  if (mode === 'overview') {
    return (
      <ReviewOverview
        pollMs={4000}
        onOpenReview={() => setMode('queue')}
        onOpen={(material) => {
          useReviewQueueFocus.getState().setFocused(material.id)
          setMode('queue')
        }}
      />
    )
  }
  return (
    <ReviewQueuePanel
      initialSelectedId={focusedId ?? facet}
      focusNonce={nonce}
      onOpenOverview={() => setMode('overview')}
    />
  )
}

export const reviewQueueRegistration: EntityRegistration<ReviewQueueEntity> = {
  resolver,
  renderer: { type: 'review_queue', Editor },
  label: 'Review Queue',
  icon: 'R',
}
