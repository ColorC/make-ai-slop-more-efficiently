/**
 * MaterialOverviewCard — 公众号式总览卡片(每卡都有封面;瀑布流紧密排列无缝隙)。
 * 选型(用户 2026-06-25):B1 多头条 + 按时间排列;反馈:封面要有、内容间不要有缝隙。
 *   - 封面:图/视频=真资源;网页/demo/报告=站点卡;文本(markdown/plan/工作报告)=排版式封面
 *     (文件型材料正文在 file 里不在 inline_content → 懒取一次文件文本作摘要)。
 *   - 排版:CSS columns 瀑布流紧密铺,无网格空洞;hero 用 column-span 横跨成头条带。
 *   - size = f(tier, kind):mandatory×可视=hero;mandatory文本/important可视=feature;…
 * 见 docs/plans/dashboard/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (公众号总览 R3)。
 */
import React, { useEffect, useRef, useState } from 'react'
import { BookOpen, Play, Globe, CircleQuestionMark } from 'lucide-react'
import { domainColor } from '../../shell/tokens'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { COLORS, KIND_LABELS, STATUS_LABELS } from './shared'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { usePanels } from '../../stores/panelsStore'

// 卡片右上角「更多」: 与审阅列表卡同款「在项目工作台打开」深链入口(件二 DEC-2026-07-06-082/083)。
// 用 usePanels.getState().openTab 直调, 不给 ReviewOverview / 消费方新增 prop(改动最小)。
function StudioOpenKebab({ m }: { m: Material }) {
  const items: KebabItem[] = [{
    label: '在项目工作台打开',
    icon: <BookOpen size={15} />,
    testid: `overview-card-open-studio-${m.id}`,
    onClick: () => usePanels.getState().openTab({ type: 'studio_reader', id: m.project || 'unfiled' }, `${m.project || 'unfiled'} 阅读`, m.id),
  }]
  // 绝对定位在卡右上; stopPropagation 防止点菜单误触发卡片 onOpen。
  return (
    <div style={{ position: 'absolute', top: 6, right: 6, zIndex: 2 }} onClick={(e) => e.stopPropagation()}>
      <KebabMenu testid={`overview-card-more-${m.id}`} items={items} iconSize={15} />
    </div>
  )
}

export type CardSize = 'hero' | 'feature' | 'normal' | 'compact'

const VISUAL_KINDS = new Set(['image', 'video', 'aigc-image', 'demo', 'html', 'custom_web_template', 'static-report', 'webgame-spec'])
const WEB_KINDS = new Set(['html', 'demo', 'static-report', 'custom_web_template', 'webgame-spec'])
const TEXT_KINDS = new Set(['markdown', 'plan', 'agent-workflow-report'])
const TIER_RANK: Record<string, number> = { mandatory: 3, important: 2, processual: 1, ignored: 0 }
const TIER_COLOR: Record<string, string> = {
  mandatory: COLORS.mandatory, important: COLORS.important, processual: COLORS.processual, ignored: COLORS.ignored,
}
const TIER_TINT: Record<string, string> = {
  // 2026-07-18 W2 去 AI 味: tier 渐变封面 → 实色卡(语义色淡染 tint + 语义色文字, 禁渐变)。
  mandatory: 'color-mix(in srgb, var(--fp-err) 10%, var(--fp-solid))',
  important: 'color-mix(in srgb, var(--fp-warn) 10%, var(--fp-solid))',
  processual: 'var(--fp-solid)',
  ignored: 'var(--fp-bg)',
}

/** 时间序里每张卡的展示大小 = 重要度 + 是否可视化。 */
export function cardSize(m: { kind: string; tier: string }): CardSize {
  const score = (TIER_RANK[m.tier] ?? 0) + (VISUAL_KINDS.has(m.kind) ? 1 : 0)
  // Mandatory visual materials stay in the packed waterfall; no full-width strip.
  if (score >= 3) return 'feature'
  if (score === 2) return 'normal'
  return 'compact'
}

/** Markdown → 纯文本摘要(去掉常见标记,折叠空白,截断)。 */
export function mdExcerpt(src: string | null | undefined, max = 120): string {
  if (!src) return ''
  const text = src
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^---[\s\S]*?---/, ' ')
    .replace(/[#>*_`~|-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function KeyQuestionText(src: string | null | undefined): string {
  try {
    const p = src ? JSON.parse(src) : null
    return (p && p.question) || ''
  } catch {
    return ''
  }
}

function hostOf(url: string): string {
  try { return new URL(url).host } catch { return url }
}

/** 文件型文本材料:正文在 file 里,懒取一次作摘要;inline 优先。 */
function useMaterialText(m: Material): string {
  const [text, setText] = useState<string>(m.inline_content || '')
  useEffect(() => {
    if (m.inline_content) { setText(m.inline_content); return }
    if (!m.file_relpath || !TEXT_KINDS.has(m.kind)) return
    let alive = true
    try {
      void fetch(reviewstageApi.fileUrl(m.id))
        .then((r) => (r.ok ? r.text() : ''))
        .then((t) => { if (alive) setText(t.slice(0, 600)) })
        .catch(() => { /* 静默 */ })
    } catch { /* 测试环境无 fetch */ }
    return () => { alive = false }
  }, [m.id, m.inline_content, m.file_relpath, m.kind])
  return text
}

/** kind 专属"文字版"封面(截图未就绪时的兜底,且始终在 DOM 里供测试断言)。 */
function KindCover({ m, h = 120, text = '' }: { m: Material; h?: number; text?: string }) {
  const extra = (m.extra as Record<string, unknown> | undefined) || {}
  if (m.kind === 'image' || m.kind === 'aigc-image') {
    const src = m.file_relpath ? reviewstageApi.fileUrl(m.id) : `data:image/png;base64,${m.inline_content || ''}`
    return <img data-testid="card-preview-image" src={src} alt={m.title} loading="lazy" decoding="async" style={{ width: '100%', height: h, objectFit: 'cover', borderRadius: 6, background: 'var(--fp-bg)', display: 'block' }} />
  }
  if (m.kind === 'video') {
    const poster = (extra.poster_url as string | undefined) || undefined
    return poster ? (
      <img data-testid="card-preview-video" src={poster} alt={m.title} loading="lazy" decoding="async" style={{ width: '100%', height: h, objectFit: 'cover', borderRadius: 6, display: 'block' }} />
    ) : (
      <div data-testid="card-preview-video" style={{ height: h, borderRadius: 6, background: 'var(--fp-card)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fp-accent)' }}><Play size={Math.max(20, Math.round(h / 3))} /></div>
    )
  }
  if (WEB_KINDS.has(m.kind)) {
    const url = (extra.live_url as string | undefined) || (extra.url as string | undefined) || ''
    return (
      <div data-testid="card-preview-html" style={{ height: h, borderRadius: 6, background: 'color-mix(in srgb, var(--fp-accent) 12%, var(--fp-solid))', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4, padding: 8, overflow: 'hidden' }}>
        <Globe size={Math.max(18, Math.round(h / 4))} color={domainColor.cyan} aria-hidden />
        <span style={{ fontSize: 12, color: COLORS.textDim, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{url ? hostOf(url) : KIND_LABELS[m.kind] || '网页'}</span>
      </div>
    )
  }
  if (m.kind === 'key_question') {
    return <div data-testid="card-preview-kq" style={{ height: h, borderRadius: 6, background: TIER_TINT.important, padding: 10, overflow: 'hidden', fontSize: 13, color: COLORS.text }}><CircleQuestionMark size={13} color="var(--fp-warn)" aria-hidden style={{ verticalAlign: -2 }} /> {KeyQuestionText(m.inline_content)}</div>
  }
  // 文本封面(markdown / plan / agent-workflow-report):排版式封面,正文作摘要。
  return (
    <div data-testid="card-preview-markdown" style={{ height: h, borderRadius: 6, background: TIER_TINT[m.tier] || TIER_TINT.processual, padding: 10, overflow: 'hidden', position: 'relative' }}>
      <p style={{ margin: 0, fontSize: h > 100 ? 13 : 12, color: COLORS.textDim, lineHeight: 1.55, display: '-webkit-box', WebkitLineClamp: Math.max(2, Math.floor(h / 20)), WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {mdExcerpt(text || m.inline_content, 300) || (KIND_LABELS[m.kind] || m.kind)}
      </p>
    </div>
  )
}

/** 截图封面叠在文字兜底之上:截图加载成功才显形,失败/未生成就露出文字兜底。 */
function WithCover({ m, h, coverNonce, children }: { m: Material; h: number; coverNonce: number; children: React.ReactNode }) {
  const [loaded, setLoaded] = useState(false)
  const [retry, setRetry] = useState(0)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    setLoaded(false)
    setRetry(0)
    if (retryTimer.current !== null) clearTimeout(retryTimer.current)
    retryTimer.current = null
    return () => {
      if (retryTimer.current !== null) clearTimeout(retryTimer.current)
      retryTimer.current = null
    }
  }, [coverNonce, m.id, m.updated_at])
  const src = reviewstageApi.coverUrl(m.id, `${m.updated_at || ''}:${coverNonce}:${retry}`)
  return (
    <div style={{ position: 'relative', height: h, borderRadius: 6, overflow: 'hidden' }}>
      {children}
      <img
        data-testid="card-cover"
        src={src}
        alt=""
        loading="lazy"
        decoding="async"
        onLoad={() => {
          if (retryTimer.current !== null) clearTimeout(retryTimer.current)
          retryTimer.current = null
          setLoaded(true)
        }}
        onError={() => {
          setLoaded(false)
          // 提交队列可能仍在截封面。有限指数回退让已打开的总览自行接住结果，
          // 不把“打开页面”重新变成生成触发器，也不在永久失败时无限请求。
          if (retry >= 5 || retryTimer.current !== null) return
          retryTimer.current = setTimeout(() => {
            retryTimer.current = null
            setRetry((value) => value + 1)
          }, Math.min(8_000, 1_000 * (2 ** retry)))
        }}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top', opacity: loaded ? 1 : 0, transition: 'opacity .2s', display: 'block', background: 'var(--fp-bg)' }}
      />
    </div>
  )
}

/** kind 封面:网页/文本/视频用 headless 截图(叠在兜底上),图/关键问题直接用本体。
 *  视频封面=服务端抽的首帧(2026-07-07 视频审阅整改), 未生成时露播放图标兜底。 */
export function CardPreview({ m, h = 120, text = '', coverNonce = 0 }: { m: Material; h?: number; text?: string; coverNonce?: number }) {
  const fallback = <KindCover m={m} h={h} text={text} />
  if (WEB_KINDS.has(m.kind) || TEXT_KINDS.has(m.kind) || m.kind === 'video') {
    return <WithCover m={m} h={h} coverNonce={coverNonce}>{fallback}</WithCover>
  }
  return fallback
}

function MetaRow({ m }: { m: Material }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 7, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--fp-bg)', background: TIER_COLOR[m.tier] || COLORS.processual, borderRadius: 999, padding: '1px 7px' }}>
        {m.tier === 'mandatory' ? '必验收' : m.tier === 'important' ? '重要' : m.tier === 'processual' ? '过程' : '其余'}
      </span>
      <span style={{ fontSize: 12, color: COLORS.textDim, border: `1px solid ${COLORS.border}`, borderRadius: 4, padding: '0 5px' }}>
        {KIND_LABELS[m.kind] || m.kind}
      </span>
      <span data-testid="card-status" style={{ fontSize: 12, color: m.status === 'accepted' ? COLORS.accepted : m.status === 'rejected' ? COLORS.rejected : COLORS.textDim }}>
        {STATUS_LABELS[m.status] || m.status}
      </span>
    </div>
  )
}

function MaterialOverviewCardImpl({ m, size, onOpen, coverNonce = 0 }: { m: Material; size?: CardSize; onOpen?: (m: Material) => void; coverNonce?: number }) {
  const sz = size || cardSize(m)
  const text = useMaterialText(m)
  const base: React.CSSProperties = {
    breakInside: 'avoid',
    marginBottom: 12,
    background: 'var(--fp-glass)',
    backdropFilter: 'var(--fp-blur)',
    WebkitBackdropFilter: 'var(--fp-blur)',
    boxShadow: '0 4px 16px rgba(0,0,0,.28), inset 0 1px 0 rgba(255,255,255,.08)',
    border: `1px solid ${COLORS.border}`,
    borderLeft: `3px solid ${TIER_COLOR[m.tier] || COLORS.border}`,
    borderRadius: 11,
    cursor: onOpen ? 'pointer' : 'default',
    overflow: 'hidden',
    position: 'relative',
  }
  const common = { 'data-testid': 'overview-card', 'data-kind': m.kind, 'data-tier': m.tier, 'data-size': sz, 'data-hero': sz === 'hero' ? '1' : '0', onClick: () => onOpen?.(m) }

  // hero:横跨成头条带(column-span all)。
  if (sz === 'hero') {
    return (
      <article {...common} style={{ ...base, columnSpan: 'all', display: 'flex', gap: 14, padding: 12 }}>
        <StudioOpenKebab m={m} />
        <div style={{ flex: '0 0 300px' }}><CardPreview m={m} h={170} text={text} coverNonce={coverNonce} /></div>
        <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <h2 style={{ margin: 0, fontSize: 20, color: COLORS.text, lineHeight: 1.3 }}>{m.title}</h2>
          <p style={{ margin: '8px 0 0', fontSize: 13, color: COLORS.textDim, lineHeight: 1.6, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{mdExcerpt(text || m.inline_content, 200)}</p>
          <MetaRow m={m} />
        </div>
      </article>
    )
  }
  // feature / normal / compact:瀑布流里的竖卡,封面高度随大小。
  // 文本类封面给更高(渲染图字大但需要更多竖向空间才读得下,用户 2026-06-25)。
  const isText = TEXT_KINDS.has(m.kind)
  const coverH = isText
    ? (sz === 'feature' ? 250 : sz === 'normal' ? 200 : 130)
    : (sz === 'feature' ? 158 : sz === 'normal' ? 116 : 68)
  return (
    <article {...common} style={{ ...base, display: 'flex', flexDirection: 'column' }}>
      <StudioOpenKebab m={m} />
      <div style={{ padding: 8, paddingBottom: 0 }}><CardPreview m={m} h={coverH} text={text} coverNonce={coverNonce} /></div>
      <div style={{ padding: '6px 10px 10px' }}>
        <h3 style={{ margin: 0, fontSize: sz === 'feature' ? 15 : 13.5, color: COLORS.text, lineHeight: 1.35, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{m.title}</h3>
        <MetaRow m={m} />
      </div>
    </article>
  )
}

// memo: 轮询/无关 setState 触发父级重渲染时, 只有"渲染相关字段真变了"的卡片才重渲染,
// 其余跳过(onOpen 在轮询期间引用稳定 → 不会因它误判)。配合父级 diff-before-setItems
// + 模块级 Waterfall, 彻底消除"每隔一会儿每张卡重载一次"。
export const MaterialOverviewCard = React.memo(MaterialOverviewCardImpl, (a, b) =>
  a.coverNonce === b.coverNonce
  && a.size === b.size
  && a.onOpen === b.onOpen
  && a.m.id === b.m.id
  && a.m.updated_at === b.m.updated_at
  && a.m.status === b.m.status
  && a.m.tier === b.m.tier
  && a.m.title === b.m.title
  && a.m.kind === b.m.kind
  && a.m.inline_content === b.m.inline_content,
)
