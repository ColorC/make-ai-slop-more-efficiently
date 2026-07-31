/**
 * entities/review/MaterialDetail — 主详情视图 (verdict 组+理由输入 / tier 单选 chip /
 * 返回源按钮 / 文本选择定位 / 内容区走 MaterialContentView 统一分发).
 *
 * R2 从 standalone 审阅台剪切而来 (结构搬移); R4 起 standalone 已退役,
 * 消费方为驾驶舱 review_queue / review_material 面板.
 *
 * 2026-07-21 审阅详情收为单一顶栏：标题、级别/状态、版本、目录、评论、通过、驳回和
 * 更多都在同一行；删除正文上方的重复标题/元数据块和底部裁决栏。文档正文仍使用米色
 * 阅读卡，长文目录和选区评论行为保持不变。
 */

import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, X, ArrowLeftToLine, Copy, Download, ChevronDown, List, History, MessageSquare } from 'lucide-react'
import {
  reviewstageApi,
  type Material,
  type MaterialStatus,
  type MaterialTier,
  type CommentFeedbackStatus,
} from '../../api/reviewstageClient'
import {
  COLORS,
  TIER_LABELS,
  STATUS_LABELS,
  STATUS_V2,
  tierColor,
  type ReviewSource,
} from './shared'
// MaterialContentView 懒加载(2026-07 首屏拆包): MaterialViews 静态拖着 wiki-core 渲染核
// (markdown-it + 第二份 katex)与 lightbox, 直引会把 ~400KB 钉进首屏主包。内建 kind 渲染器在
// MaterialViews 模块顶层登记, 随首次真实渲染材料详情时下载 chunk 并完成登记(业务 schema
// 渲染器在 businesses/ 启动期独立登记, 不受影响)。
const MaterialContentView = lazy(() => import('./MaterialViews').then((m) => ({ default: m.MaterialContentView })))
import { requestReviewCommentsOpen, useReviewActive } from '../../stores/reviewActiveStore'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { copyText } from '../../lib/copyText'
import { notice } from '../../lib/surface'
import { resolveMaterialRenderer, type BusinessToolbarSpec } from './rendererRegistry'
import { TraceMenu } from './TraceMenu'
import { MaterialContextSpineView } from './MaterialContextSpine'
import { canonicalMaterialRef, CANONICAL_REVIEW_KIND } from './materialReference'
import './reviewFlow.css'


// 需理由的裁决(驳回)走二段式: 先点亮 → 顶出理由条 → 确认。通过直发无理由。
// 阻断裁决项已删(DEC-2026-07-05-003:根本没有用);历史 blocked 状态仅展示,不再可下达。
type RejectKind = Extract<MaterialStatus, 'rejected'>

const REJECT_META: Record<RejectKind, { label: string }> = {
  rejected: { label: '驳回' },
}

const REVIEW_PROFILE_LABELS: Record<string, string> = {
  'aigc-candidate': 'AIGC 候选',
  'aigc-comparison': 'AIGC 比较',
  'spreadsheet-review': '表格',
  'workflow-review': '工作流',
  'game-content-review': '游戏内容',
  'feishu-authoring-review': 'collab platform编写',
}

// ── 主详情视图 (含 verdict 组 + tier 调整) ────────────────────────

export function MaterialDetail({
  material, onVerdict, onCommentSubmit, onFeedbackChange, onTierChange, source, onReturnToSource, compact = false,
  headerLeft, moreItems,
}: {
  material: Material
  onVerdict: (verdict: MaterialStatus, reason: string) => Promise<void>
  onCommentSubmit: (content: string, target?: Record<string, unknown>) => Promise<void>
  onFeedbackChange: (commentId: string, status: CommentFeedbackStatus) => Promise<void>
  onTierChange: (tier: MaterialTier) => Promise<void>
  source: ReviewSource | null
  onReturnToSource: () => void
  compact?: boolean
  /** 消费方(如 review_queue)塞进顶栏左侧的件(如侧栏收起开关)。 */
  headerLeft?: ReactNode
  /** 消费方的审阅动作(源/在页签打开/归档/删除等)——一律并进「更多」菜单(DEC-2026-07-05-003)。 */
  moreItems?: KebabItem[]
}) {
  // 驳回走二段式: 记下待确认的裁决 + 理由。通过(accepted)直发。
  const [pendingVerdict, setPendingVerdict] = useState<RejectKind | null>(null)
  const [reason, setReason] = useState('')
  // 评论已独立成区(C 区: 右栏/次级侧栏), 不再藏在材料面板的切换里。这里只负责正文 +
  // 顶栏审阅动作; 选中正文一段文字 → 写共享 store 的待写锚点, C 区评论框接住(可跨 webview)。
  const setPendingAnchor = useReviewActive((s) => s.setPendingAnchor)
  const setActiveMaterial = useReviewActive((s) => s.setActiveMaterial)

  // html 选元素入口已撤(由 dashboard 全局捕获工具承担); 保留 no-op 满足 MaterialContentView prop。
  const onElementSelect = useCallback(() => {}, [])

  // 选中正文文本 → 作为锚点写进共享 store(评论落每材料 .md 文件, 不发总控)。同时确保 C 区看的是本材料。
  const onTextSelection = useCallback(() => {
    if (material.kind === 'html' || material.kind === 'image') return
    const selectedText = window.getSelection()?.toString().trim()
    if (!selectedText) return
    setActiveMaterial(material.id)
    setPendingAnchor(selectedText.slice(0, 200), material.id)
  }, [material.kind, material.id, setActiveMaterial, setPendingAnchor])

  const openComments = useCallback(() => {
    setActiveMaterial(material.id)
    requestReviewCommentsOpen(material.id)
  }, [material.id, setActiveMaterial])

  const accepted = material.status === 'accepted'

  // 业务顶栏(件一 DEC-2026-07-06-082/083): 若该材料的渲染器声明了 toolbar 工厂, 顶栏并成单条合并栏
  // (业务身份/chips/actions + 审阅动作)。无则一切照旧(零回归)。工厂是纯函数, 直接调用即可。
  const bizToolbar: BusinessToolbarSpec | null = resolveMaterialRenderer(material)?.toolbar?.(material) ?? null
  // 文档型(文字内容)使用米色阅读卡；占满型(iframe/视频)不上米卡。
  // MaterialContentView 是 lazy chunk：首帧内建 renderer 可能尚未完成模块顶层登记。
  // 此时若只查 registry，markdown 会被误判成 bleed，目录扫描也永远不会启动。
  const renderer = resolveMaterialRenderer(material)
  const builtinDocumentKind = new Set([
    'markdown', 'key_question', 'custom_web_template', 'plan',
    'agent-workflow-report', 'decision-candidate',
  ]).has(material.kind)
  const docCard = renderer ? renderer.document === true : builtinDocumentKind

  // 复制材料落盘路径 → 粘给其他 agent 直接 Read。inline 材料无文件, 退化复制内容 API 地址。
  const copyMaterialPath = useCallback(async () => {
    try {
      const p = await reviewstageApi.getPath(material.id)
      const text = p.file_abs_path
        || `${window.location.origin}/api/boss-sight/reviewstage/${encodeURIComponent(material.id)}/file`
      const ok = await copyText(text)
      if (!ok) { notice('复制失败(剪贴板不可用)', 'err'); return }
      notice(p.file_abs_path ? `已复制文件路径\n${text}` : `该材料为内联内容无落盘文件，已复制内容 API 地址\n${text}`, 'ok')
    } catch (e) {
      notice(`复制文件路径失败: ${String(e).slice(0, 160)}`, 'err')
    }
  }, [material.id])

  const downloadMaterialFile = useCallback(() => {
    const link = document.createElement('a')
    link.href = `/api/boss-sight/reviewstage/${encodeURIComponent(material.id)}/file?raw=1&download=1`
    const stagedName = material.file_relpath?.split(/[\\/]/).pop() || ''
    const stagedSuffix = stagedName.match(/\.[^.]+$/)?.[0] || ''
    const fallbackSuffix: Partial<Record<Material['kind'], string>> = {
      markdown: '.md',
      html: '.html',
      'static-report': '.html',
      demo: '.html',
      custom_web_template: '.html',
      key_question: '.json',
    }
    const suffix = stagedSuffix || fallbackSuffix[material.kind] || '.txt'
    const safeTitle = material.title.replace(/[\u0000-\u001f<>:"/\\|?*]+/g, '_').trim() || 'review-material'
    link.download = safeTitle.toLowerCase().endsWith(suffix.toLowerCase())
      ? safeTitle
      : `${safeTitle}${suffix}`
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    link.remove()
    notice('已开始下载文件', 'ok')
  }, [material.file_relpath, material.id, material.kind, material.title])

  // 除通过/驳回/调级外一切操作收进「更多」(DEC-2026-07-05-003): 复制路径 + 消费方动作 + 返回源。
  // (调级已按 V2 合同提到栏面 tier chip, 不再藏二级菜单。)
  const kebabItems: KebabItem[] = [
    {
      label: '下载文件',
      icon: <Download size={15} />,
      testid: 'review-download-file',
      onClick: downloadMaterialFile,
    },
    {
      label: '复制文件路径',
      icon: <Copy size={15} />,
      testid: 'review-copy-path',
      onClick: () => { void copyMaterialPath() },
    },
    ...(moreItems || []),
    ...(source ? [{
      label: `返回源${source.title ? `: ${source.title}` : ''}`,
      icon: <ArrowLeftToLine size={15} />,
      testid: 'review-return-source',
      onClick: onReturnToSource,
    } as KebabItem] : []),
  ]

  // Long-document TOC: scan h2 headings and show them in a dedicated right rail.
  const rootRef = useRef<HTMLDivElement>(null)
  const [toc, setToc] = useState<string[]>([])
  const [tocOpen, setTocOpen] = useState(false)
  useEffect(() => {
    if (!docCard) { setToc([]); return undefined }
    const root = rootRef.current
    if (!root) return undefined
    let raf = 0
    const scan = () => {
      const hs = Array.from(root.querySelectorAll('.wiki-prose h2')) as HTMLElement[]
      hs.forEach((h, i) => { if (!h.id) h.id = `rf-h2-${i}`; h.style.scrollMarginTop = '12px' })
      const labels = hs.map((h) => (h.textContent || '').trim()).filter(Boolean)
      setToc((prev) => (prev.length === labels.length && prev.every((v, i) => v === labels[i]) ? prev : labels))
    }
    const mo = new MutationObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(scan) })
    mo.observe(root, { subtree: true, childList: true, characterData: true })
    scan()
    return () => { mo.disconnect(); cancelAnimationFrame(raf) }
  }, [docCard, material.id])
  useEffect(() => { setTocOpen(false) }, [material.id])
  useEffect(() => {
    if (!tocOpen) return undefined
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setTocOpen(false) }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [tocOpen])
  const scrollToH2 = useCallback((i: number) => {
    const hs = rootRef.current?.querySelectorAll('.wiki-prose h2')
    const el = hs?.[i] as HTMLElement | undefined
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  // tier 单选 chip(提到栏面; TraceMenu 四档 radio, 选完即收)。
  const tierChip = (
    <TraceMenu
      label="调级"
      trigger={(open, toggle) => (
        <button
          type="button"
          className="rf-tierchip"
          aria-expanded={open}
          aria-haspopup="true"
          aria-label={`调级(当前: ${TIER_LABELS[material.tier]})`}
          onClick={toggle}
        >
          <span className="tdot" style={{ background: tierColor(material.tier) }} aria-hidden />
          {TIER_LABELS[material.tier]}
          <ChevronDown size={11} aria-hidden />
        </button>
      )}
    >
      {(close) => (['mandatory', 'important', 'processual', 'ignored'] as MaterialTier[]).map((t) => (
        <button
          key={t}
          type="button"
          className="v2-checkrow"
          role="radio"
          aria-checked={material.tier === t}
          data-testid={`tier-set-${t}`}
          onClick={() => { if (material.tier !== t) void onTierChange(t); close() }}
        >
          <span className="cb" aria-hidden><Check size={11} strokeWidth={3} /></span>
          <span className="cr-t" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: tierColor(t), display: 'inline-block' }} aria-hidden />
            {TIER_LABELS[t]}
          </span>
        </button>
      ))}
    </TraceMenu>
  )

  // 审阅动作集中在单一顶栏，正文上下不再各占一条操作栏。
  const verdictActions = (
    <div className="v2-verdict" role="radiogroup" aria-label="审阅裁决">
      <div className="vd-group">
        <button
          type="button"
          className="vd-i"
          role="radio"
          data-v="accepted"
          aria-checked={accepted}
          disabled={accepted}
          data-testid="verdict-accept"
          title={accepted ? '已通过' : '通过'}
          onClick={() => onVerdict('accepted', '')}
        ><i className="led" aria-hidden />通过</button>
      </div>
      {/* 搁置按 DEC-2026-07-05-003 不下达(根本没有用); 评论 = C 区右抽屉, 不在此组。 */}
      <button
        type="button"
        className="vd-reject"
        role="radio"
        data-v="rejected"
        aria-checked={material.status === 'rejected'}
        data-testid="verdict-reject"
        title="驳回(填可选理由)"
        onClick={() => { setPendingVerdict('rejected'); setReason('') }}
      ><X size={13} aria-hidden />驳回…</button>
    </div>
  )
  const commentAction = (
    <button type="button" className="rf-comment-toggle" data-testid="material-open-comments" onClick={openComments}>
      <MessageSquare size={13} aria-hidden />评论
    </button>
  )
  // 其余一切操作收进「更多」: 复制路径 + 消费方动作 + 返回源(留在顶栏右端,demo nav 同位)。
  const moreActions = <KebabMenu testid="material-detail-more" items={kebabItems} iconSize={16} />
  // tier·status: 文档头已取消，统一由顶栏这一处展示和调整。
  const tierStatus = (
    <span data-testid="material-tier-status" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flex: 'none' }}>
      {tierChip}
      <span className={`v2-status ${STATUS_V2[material.status]}`}><i className="led" aria-hidden />{STATUS_LABELS[material.status]}</span>
    </span>
  )
  // Long-document TOC: use a dedicated right rail instead of covering the article with a popover.
  const hasToc = docCard && toc.length >= 3
  const tocBtn = hasToc ? (
    <button
      type="button"
      className="rf-tocbtn"
      aria-expanded={tocOpen}
      aria-controls="material-toc-rail"
      onClick={() => setTocOpen((open) => !open)}
      data-testid="material-toc-btn"
    ><List size={13} aria-hidden />目录 {toc.length}</button>
  ) : null
  const tocRail = hasToc && tocOpen ? (
    <aside id="material-toc-rail" className="rf-tocrail" data-testid="material-toc-rail" aria-label="文章目录">
      <div className="rf-tocrail-head">
        <span><List size={13} aria-hidden />目录 {toc.length}</span>
        <button type="button" onClick={() => setTocOpen(false)} aria-label="关闭目录"><X size={14} aria-hidden /></button>
      </div>
      <div className="rf-toc">
        {toc.map((t, i) => (
          <button key={`${i}-${t}`} type="button" className="toc-i" onClick={() => scrollToH2(i)}>
            <span className="n" aria-hidden>{String(i + 1).padStart(2, '0')}</span>
            <span className="t">{t}</span>
          </button>
        ))}
      </div>
    </aside>
  ) : null
  const materialExtra = (material.extra || {}) as Record<string, unknown>
  const materialVersion = Number(materialExtra.version || 1)
  const previousVersions = (materialExtra.previous_versions as unknown[] | undefined) || []
  const versionChip = materialVersion > 1 || previousVersions.length > 0 ? (
    <span className="rf-versionchip" data-testid="material-version" title={previousVersions.length > 0 ? `之前 ${previousVersions.length} 版已归档` : undefined}>
      <History size={12} aria-hidden />v{materialVersion}
    </span>
  ) : null
  // 普通材料保持安静；只有专用场景、schema、引用或提醒存在时才露出场景入口。
  // 入口是材料自身元数据，不依赖会话常驻 hook；展开后可追到 AIGC/工作簿/工作流/collab platform背景。
  const reviewContext = material.review_context
  const showReviewContext = Boolean(
    material.context_spine
    || (
      reviewContext
      && (
        !reviewContext.profile_id.startsWith('generic-')
        || reviewContext.schema_id
        || reviewContext.references.length
        || reviewContext.reminders.length
      )
    ),
  )
  const reviewContextChip = showReviewContext ? (
    <TraceMenu
      label="材料上下文"
      align="right"
      minWidth={440}
      trigger={(open, toggle) => (
        <button
          type="button"
          className="rf-profilechip"
          aria-expanded={open}
          aria-haspopup="true"
          data-testid="material-review-context"
          onClick={toggle}
          title={`材料上下文: ${material.context_spine?.canonical_ref || material.id}`}
        >
          {reviewContext && !reviewContext.profile_id.startsWith('generic-')
            ? REVIEW_PROFILE_LABELS[reviewContext.profile_id] || reviewContext.profile_id
            : '上下文'}
          <ChevronDown size={11} aria-hidden />
        </button>
      )}
    >
      <div className="rf-profilepanel" data-testid="material-review-context-panel">
        <MaterialContextSpineView
          materialId={material.id}
          initial={material.context_spine}
          fallbackReviewContext={reviewContext}
        />
      </div>
    </TraceMenu>
  ) : null
  const barTitle = `${material.title} · ${TIER_LABELS[material.tier]} · ${STATUS_LABELS[material.status]}${material.source_plan_id ? ` · plan=${material.source_plan_id}` : ''}${material.pushed_to_user ? ` · [已推送]${material.pushed_reason || ''}` : ''}`


  // minHeight:0 必须有：flex 子项默认 min-height:auto 会被长 markdown 撑开，
  // 外层面板 overflow:hidden 直接裁掉 → 整个审阅台滚不动（2026-06-12 用户上报）
  return (
    <div ref={rootRef} style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'transparent', color: COLORS.text, minWidth: 0, minHeight: 0 }} data-testid="material-detail"
      data-omni-uri={canonicalMaterialRef(material.id)}
      data-omni-kind={CANONICAL_REVIEW_KIND}
      data-omni-title={material.title}>
      {/* 单一顶栏：标题、级别/状态、目录、评论、裁决与更多。 */}
      {bizToolbar ? (
        <div className="rf-bar" data-testid="material-detail-merged-toolbar" title={barTitle}>
          {headerLeft}
          {bizToolbar.icon && <span className="rf-bizicon">{bizToolbar.icon}</span>}
          <span data-testid="material-title" className="rf-title" style={{ flex: 'none' }}>{bizToolbar.title}</span>
          <div className="rf-bizchips">
            {bizToolbar.sub && <span className="rf-bizsub">{bizToolbar.sub}</span>}
            {(bizToolbar.chips ?? []).map((c, i) => (
              <span key={i} className="rf-bizchip" data-clickable={c.onClick ? 'true' : 'false'} title={c.title} onClick={c.onClick}>{c.label}</span>
            ))}
            {bizToolbar.aux}
          </div>
          {(bizToolbar.actions ?? []).map((a, i) => (
            <button key={i} type="button" className="rf-bizaction" title={a.title} onClick={a.onClick}>{a.icon}{a.label}</button>
          ))}
          {tierStatus}
          {versionChip}
          {reviewContextChip}
          {tocBtn}
          {commentAction}
          {verdictActions}
          {moreActions}
        </div>
      ) : (
        <div className="rf-bar" data-testid="material-detail-toolbar">
          {headerLeft}
          <div className="rf-titlewrap" title={barTitle}>
            <span data-testid="material-title" className="rf-title">{material.title}</span>
            {tierStatus}
          </div>
          {versionChip}
          {reviewContextChip}
          {tocBtn}
          {commentAction}
          {verdictActions}
          {moreActions}
        </div>
      )}

      {pendingVerdict && (
        <div className="rf-reasonbar">
          <span className="rl">{REJECT_META[pendingVerdict].label} 原因:</span>
          <input
            value={reason}
            onChange={e => setReason(e.target.value)}
            placeholder="(可选)"
            data-testid="verdict-reason"
            autoFocus
            onKeyDown={async (e) => {
              if (e.key === 'Escape') { setPendingVerdict(null); setReason('') }
              if (e.key === 'Enter') { const v = pendingVerdict; await onVerdict(v, reason); setPendingVerdict(null); setReason('') }
            }}
          />
          <button
            type="button"
            className="ok"
            data-testid="verdict-confirm"
            onClick={async () => {
              await onVerdict(pendingVerdict, reason)
              setPendingVerdict(null); setReason('')
            }}
          >确认{REJECT_META[pendingVerdict].label}</button>
          <button type="button" className="no" onClick={() => { setPendingVerdict(null); setReason('') }}>取消</button>
        </div>
      )}
      {/* 文档型使用米色正文卡；占满型直接使用内容区。 */}
      <div className={`rf-body${tocRail ? ' has-toc' : ''}`}>
        <div className={docCard ? 'rf-docwrap' : 'rf-bleedwrap'}>
          <Suspense fallback={<div style={{ padding: 24, fontSize: 13, color: COLORS.processual }}>加载渲染器…</div>}>
            <MaterialContentView m={material} onElementSelect={onElementSelect} onTextSelection={onTextSelection} />
          </Suspense>
        </div>
        {tocRail}
      </div>

    </div>
  )
}
