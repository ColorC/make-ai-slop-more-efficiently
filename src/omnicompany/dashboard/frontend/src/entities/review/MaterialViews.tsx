/**
 * entities/review/MaterialViews — 内建 material 渲染器族 + 统一分发入口(MaterialContentView)。
 *
 * R2 从 standalone 审阅台剪切而来 (结构搬移, 行为零变化); R4 起 standalone 已退役,
 * 消费方为驾驶舱 review_queue / review_material 面板.
 *
 * 2026-06-30 frostpane 重做: 信息层级靠字号(15-18/13-14/12 等宽弱灰)、容器透明吃全局冷渐变、
 * 卡片走玻璃配方、语义色统一 var(--fp-*) + color-mix(不再裸 accent/warn rgba)、
 * 低频操作收进共享 KebabMenu。行为零变化, 数据接线 / data-testid 全保留。
 *
 * 统一设计工作室 v2 F3(DEC-2026-07-05-030): 类型分发唯一走 rendererRegistry ——
 * 本文件只登记内建 kind 渲染器, MaterialContentView 用 resolveMaterialRenderer 解析,
 * 业务专属类型(叙事大纲等)在 entities/review/businesses 懒加载登记, 不改本文件。
 */

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { createRenderer, stripFrontmatter } from '@wiki-core/render'
import '@wiki-core/ui.css'
import { COLORS } from './shared'
import { domainColor } from '../../shell/tokens'
import { FileTreeDiffView } from './FileTreeDiffView'
import { WebgameSpecView } from './WebgameSpecView'
import { MaterialEmbed } from './MaterialEmbed'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { ExternalLink, Copy, GitBranch, ArrowRight, TriangleAlert, MessageSquare } from 'lucide-react'
import { registerKindRenderer, registerSchemaRenderer, resolveMaterialRenderer, schemaIdOf } from './rendererRegistry'
import Lightbox from 'yet-another-react-lightbox'
import Zoom from 'yet-another-react-lightbox/plugins/zoom'
import 'yet-another-react-lightbox/styles.css'
import './reviewFlow.css'

// ── frostpane 玻璃外壳/字阶配方(冷色 token 复用 COLORS, 这里只补"卡片解剖"的共用片段)──
// 信息层级靠字号: 标题 15-18 / 次级 13-14 / 最弱 12px 等宽弱灰; 4px 栅格呼吸。
const GLASS = {
  background: 'var(--fp-glass)',
  backdropFilter: 'var(--fp-blur)',
  WebkitBackdropFilter: 'var(--fp-blur)',
  border: `1px solid ${COLORS.border}`,
  borderRadius: 11,
  boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
} as const
const MONO = "'Berkeley Mono','Consolas','Menlo',monospace"
// 图片点击放大查看(image kind 本体 + markdown 正文内嵌图): 深色玻璃背景贴 frostpane 冷色调,
// 两处消费方共用同一份配置(缩放/拖拽交互, 用户 2026-07-09 反馈"图不能点击放大拖拽")。
const LIGHTBOX_ZOOM = { maxZoomPixelRatio: 4, scrollToZoom: true, doubleClickMaxStops: 2 } as const
const LIGHTBOX_STYLES = { container: { backgroundColor: 'rgba(10,13,20,.92)', backdropFilter: 'blur(6px)' } } as const
// yet-another-react-lightbox 默认把内容 portal 到 document.body——但本应用 React 根挂在
// #root(document.getElementById('root')), React 18 createRoot 的事件委托只监听根容器,
// portal 到 #root 之外(document.body 直接子节点)的原生事件(wheel/keydown/pointer)永远
// 到不了根容器的委托监听器: 缩放/拖拽/Esc 关闭全部失效但视觉开合正常(不报错, 极隐蔽)。
// 修法: 把 portal 目标钉在组件自身渲染树内的一个锚点 div 上(仍在 #root 内部)。
function useLightboxPortalRef() {
  const [node, setNodeState] = useState<HTMLDivElement | null>(null)
  const setNode = useCallback((el: HTMLDivElement | null) => setNodeState(el), [])
  return { setNode, portal: node ? { root: node } : {} }
}
const CARD_GRID: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }
// 语义色软底(色调来自 token, 透明度 color-mix; 不再写死 accent/warn 的裸 rgba)
const ACCENT_SOFT = 'color-mix(in srgb, var(--fp-accent) 14%, transparent)'
const WARN_SOFT = 'color-mix(in srgb, var(--fp-warn) 14%, transparent)'
const WARN_RIM = 'color-mix(in srgb, var(--fp-warn) 32%, transparent)'
// 共用工具条(玻璃薄条 + rim 高光): 标题占主, 主操作显眼, 低频进 ⋯。
const TOOLBAR: React.CSSProperties = {
  padding: '10px 16px', flexShrink: 0,
  background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
  borderBottom: `1px solid ${COLORS.border}`, boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  display: 'flex', gap: 10, alignItems: 'center',
}


// 业务类型(data_schema_id)渲染器: 统一登记进 rendererRegistry(F3), 这里只保留内建两例。
// 新载体走 entities/review/businesses 懒加载登记, 不再改本文件。
function parseInlineJson(m: Material): any | null {
  try { return m.inline_content ? JSON.parse(m.inline_content) : null } catch { return null }
}

function localHostPortUrl(port: number, pathAndQuery = '/'): string {
  const path = pathAndQuery.startsWith('/') ? pathAndQuery : `/${pathAndQuery}`
  if (typeof window === 'undefined' || !window.location?.hostname) return `http://127.0.0.1:${port}${path}`
  return `${window.location.protocol}//${window.location.hostname}:${port}${path}`
}

function rewriteLoopbackUrlForLan(raw?: string): string | undefined {
  if (!raw) return undefined
  try {
    const url = new URL(raw)
    if ((url.hostname === '127.0.0.1' || url.hostname === 'localhost') && typeof window !== 'undefined' && window.location?.hostname) {
      url.hostname = window.location.hostname
      return url.toString()
    }
  } catch {
    return raw
  }
  return raw
}

function FileTreeDiffSchemaView({ m }: { m: Material }) {
  const data = parseInlineJson(m)
  if (!data) return <RawInlineFallback m={m} />
  return <FileTreeDiffView data={data} material={m} />
}

function BranchStorylineSchemaView({ m }: { m: Material }) {
  const data = parseInlineJson(m)
  if (!data) return <RawInlineFallback m={m} />
  return branchStorylineBody(data)
}

function RawInlineFallback({ m }: { m: Material }) {
  return (
    <pre style={{ padding: 24, color: COLORS.textDim, fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', fontFamily: MONO }}>
      {m.inline_content || '(no content)'}
    </pre>
  )
}

function branchStorylineBody(data: any): React.ReactElement {
  return (
    <div style={{ padding: 24, color: COLORS.text }}>
      {/* 区标题 18px 主焦点 + 一行弱灰统计(字号建层级, 不堆说明文字) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <span style={{ width: 28, height: 28, borderRadius: 8, display: 'grid', placeItems: 'center', flex: 'none', color: COLORS.borderActive, background: ACCENT_SOFT }}>
          <GitBranch size={16} />
        </span>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 650, letterSpacing: '-0.01em' }}>{data.title || '分支剧情线'}</h2>
      </div>
      <div style={{ color: 'var(--fp-text-3)', fontSize: 12, marginBottom: 20, fontFamily: MONO }}>
        节点 {(data.nodes || []).length} · 分支 {(data.branches || []).length}
      </div>
      {/* 节点从拥挤竖列 → 卡片网格(异构图文, 自适应列宽), 4px 栅格留白呼吸 */}
      <div style={CARD_GRID}>
        {(data.nodes || []).map((n: any, i: number) => (
          <div key={i} style={{ ...GLASS, padding: 16, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            {/* 卡顶: id 弱灰等宽 chip + 标题 15px 醒目 flex1 */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
              <span style={{ fontSize: 12, fontFamily: MONO, color: 'var(--fp-text-3)', flex: 'none' }}>{n.id || `节点${i + 1}`}</span>
              <span style={{ fontSize: 15, fontWeight: 650, letterSpacing: '-0.01em', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{n.title}</span>
            </div>
            {n.body && <div style={{ fontSize: 13, lineHeight: 1.5, marginTop: 8, color: COLORS.textDim }}>{n.body}</div>}
            {n.choices && n.choices.length > 0 && (
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${COLORS.border}`, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {n.choices.map((c: any, j: number) => (
                  <div key={j} style={{ fontSize: 13, color: COLORS.textDim, display: 'flex', alignItems: 'baseline', gap: 6 }}>
                    <span style={{ color: COLORS.borderActive, flex: 'none', display: 'inline-flex' }}><ArrowRight size={13} aria-hidden /></span>
                    <span style={{ flex: 1, minWidth: 0 }}>{c.label}</span>
                    <span style={{ color: 'var(--fp-text-3)', fontSize: 12, fontFamily: MONO, flex: 'none' }}>去 {c.next}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}


// ── Material 渲染组件 ──────────────────────────────────────────────

export function ImageMaterialView({ m }: { m: Material }) {
  const src = m.file_relpath
    ? reviewstageApi.fileUrl(m.id)
    : `data:image/png;base64,${m.inline_content || ''}`
  const [zoomOpen, setZoomOpen] = useState(false)
  const portalRef = useLightboxPortalRef()
  // root 透明吃全局冷渐变(不再铺 --fp-bg 实底); 图片本体居中浮其上。
  return (
    <div style={{ padding: 24, textAlign: 'center', background: 'transparent' }}>
      <img
        src={src}
        alt={m.title}
        onClick={() => setZoomOpen(true)}
        style={{ maxWidth: '100%', maxHeight: '70vh', objectFit: 'contain', borderRadius: 11, boxShadow: 'var(--fp-shadow)', cursor: 'zoom-in' }}
        data-testid="material-image"
      />
      <div ref={portalRef.setNode} style={{ display: 'contents' }} />
      <Lightbox
        open={zoomOpen}
        close={() => setZoomOpen(false)}
        slides={[{ src, alt: m.title }]}
        plugins={[Zoom]}
        zoom={LIGHTBOX_ZOOM}
        styles={LIGHTBOX_STYLES}
        portal={portalRef.portal}
      />
      {m.annotations.length > 0 && (
        <div style={{ marginTop: 16, fontSize: 12, color: 'var(--fp-text-3)', fontFamily: MONO }}>
          {m.annotations.length} 个 AI 批注 · 见右侧批注栏
        </div>
      )}
    </div>
  )
}


export function MarkdownMaterialView({ m }: { m: Material }) {
  const [content, setContent] = useState<string>('')
  const containerRef = useRef<HTMLDivElement>(null)
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const [slides, setSlides] = useState<{ src: string; alt?: string }[]>([])
  const portalRef = useLightboxPortalRef()
  useEffect(() => {
    if (m.inline_content) {
      setContent(m.inline_content)
    } else if (m.file_relpath) {
      fetch(reviewstageApi.fileUrl(m.id)).then(r => r.text()).then(setContent)
    }
  }, [m.id, m.inline_content, m.file_relpath])

  // 共用 wiki 核渲染（obsidian flavor 全量: wikilink/callout/高亮/任务列表/数学等）。
  // DOM 仍是普通段落流, 圈选/批注的段落锚点机制不受影响。
  const html = useMemo(() => (content ? wikiMarkdown.render(stripFrontmatter(content)) : ''), [content])

  // 正文内嵌图片点击放大(事件代理捕获, 不改 markdown-it 渲染器)：html 提交后收集全文档 img
  // 供画廊导航(上一张/下一张)；DOM 更新在 dangerouslySetInnerHTML 提交之后才可查, 故用 effect 不用 render 期计算。
  useEffect(() => {
    const imgs = Array.from(containerRef.current?.querySelectorAll('img') ?? [])
    setSlides(imgs.map((img) => ({ src: img.src, alt: img.alt })))
  }, [html])

  const handleImageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement
    if (target.tagName !== 'IMG') return
    const imgs = Array.from(containerRef.current?.querySelectorAll('img') ?? [])
    const idx = imgs.indexOf(target as HTMLImageElement)
    if (idx >= 0) setLightboxIndex(idx)
  }

  return (
    <>
      <div
        ref={containerRef}
        data-testid="material-markdown"
        className="wiki-prose rf-md"
        onClick={handleImageClick}
        style={{
          padding: 24,
          // 大段阅读区: root 透明吃渐变(标准 ② / ③), 不再铺 --fp-bg 把渐变顶掉。
          // 颜色/字体交给 .rf-md 类(2026-07-19 蓝图 G): 蓝图纸面默认字色;米色文档卡内换墨色系。
          background: 'transparent',
          // 滚动唯一归 selection-surface（外层）；这里 height:100%+overflow:auto 会造出
          // 第二个滚动容器，且 padding 让它恒比外层高 48px → 双滚动条
          minHeight: '100%',
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      <div ref={portalRef.setNode} style={{ display: 'contents' }} />
      <Lightbox
        open={lightboxIndex !== null}
        index={lightboxIndex ?? 0}
        close={() => setLightboxIndex(null)}
        slides={slides}
        plugins={[Zoom]}
        zoom={LIGHTBOX_ZOOM}
        styles={LIGHTBOX_STYLES}
        portal={portalRef.portal}
      />
    </>
  )
}

// markdown-it 实例可复用, 模块级建一份即可
const wikiMarkdown = createRenderer()


export function HtmlMaterialView({ m }: { m: Material }) {
  // 实时网页材料(如 walker-game): extra.live_url 经 dashboard 代理同源; 优先于落盘文件。
  const liveUrl = (m.extra as Record<string, unknown> | undefined)?.live_url as string | undefined
  const src = liveUrl ?? (m.file_relpath ? reviewstageApi.fileUrl(m.id) : undefined)
  const useSrcdoc = !src && !!m.inline_content
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [slides, setSlides] = useState<{ src: string; alt?: string }[]>([])
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const [reviewEmbeds, setReviewEmbeds] = useState<Array<{
    key: string
    target: HTMLElement
    reference: string
    label?: string
  }>>([])
  const portalRef = useLightboxPortalRef()

  // 材料本体是自包含 HTML, 走 iframe 渲染(独立 document, React 事件代理够不到)。
  // 同源时(sandbox 带 allow-same-origin)可直接从父页读 contentDocument, 给里面每张 img
  // 绑点击 → 复用同一份 Lightbox(父页渲染, 不需要在 iframe 里再塞一套)。跨域 iframe
  // (少数 live_url 情况)读取会抛错, 静默跳过不影响正常浏览(用户 2026-07-09 反馈)。
  const wireImages = useCallback(() => {
    try {
      const doc = iframeRef.current?.contentDocument
      if (!doc) return
      const imgs = Array.from(doc.querySelectorAll('img'))
      setSlides(imgs.map((img) => ({ src: img.src, alt: img.alt })))
      imgs.forEach((img, idx) => {
        img.style.cursor = 'zoom-in'
        img.addEventListener('click', (e) => {
          e.preventDefault()
          e.stopPropagation()
          setLightboxIndex(idx)
        })
      })
      setReviewEmbeds(
        Array.from(doc.querySelectorAll<HTMLElement>('[data-review-material-embed]'))
          .map((target, index) => ({
            key: `${index}:${target.dataset.reviewMaterialEmbed || ''}`,
            target,
            reference: target.dataset.reviewMaterialEmbed || '',
            label: target.dataset.reviewMaterialLabel || undefined,
          }))
          .filter((item) => item.reference),
      )
    } catch {
      // 跨域 iframe: contentDocument 访问被拒，图片与复合审阅嵌入都安静降级。
      setReviewEmbeds([])
    }
  }, [])

  // 元素圈选评论走 dashboard 全局捕获工具, 不在每条材料里再叠第二个(用户 2026-06-13)。
  // 全屏看网页本体走顶栏"在页签打开", 这里不再放重复按钮。整块就是网页本体, 让它占满。
  return (
    <>
      <iframe
        ref={iframeRef}
        data-testid="material-html"
        src={src}
        srcDoc={useSrcdoc ? (m.inline_content || undefined) : undefined}
        onLoad={wireImages}
        // allow-popups: 材料内 target=_blank 体验链接(playground/游戏/本地demo)要能弹新页(2026-07-06 用户"点不开");
        // popups-to-escape-sandbox: 弹出的新页不继承沙箱, 否则目标站脚本被禁照样白屏
        sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox"
        style={{ flex: 1, border: 'none', background: '#fff', width: '100%', minHeight: 0 }}
        title={m.title}
      />
      <div ref={portalRef.setNode} style={{ display: 'contents' }} />
      <Lightbox
        open={lightboxIndex !== null}
        index={lightboxIndex ?? 0}
        close={() => setLightboxIndex(null)}
        slides={slides}
        plugins={[Zoom]}
        zoom={LIGHTBOX_ZOOM}
        styles={LIGHTBOX_STYLES}
        portal={portalRef.portal}
      />
      {reviewEmbeds.map((embed) => createPortal(
        <MaterialEmbed reference={embed.reference} label={embed.label} />,
        embed.target,
        embed.key,
      ))}
    </>
  )
}


export function AigcImageView({ m }: { m: Material }) {
  // AIGC 图片不在 8210 内联评判, 走我们的 aigc-lab 审阅台(矩阵对比/保留拒绝/打分)。
  // material 是"枢纽": extra.aigc_lab_url 指向 aigc-lab 对应卡; 没有就回退显示落盘图。
  const extra = (m.extra as Record<string, unknown> | undefined) || {}
  const labUrl = rewriteLoopbackUrlForLan(extra.aigc_lab_url as string | undefined)
    || (extra.aigc_lab_card_id != null ? localHostPortUrl(8077, `/?card_id=${encodeURIComponent(String(extra.aigc_lab_card_id))}`) : undefined)
  if (!labUrl) {
    // 无 aigc-lab 链接 → 回退当普通图片看
    return m.file_relpath
      ? <ImageMaterialView m={m} />
      : <div style={{ padding: 24, color: COLORS.textDim, fontSize: 13, lineHeight: 1.5 }}>AIGC 图片缺 extra.aigc_lab_url / aigc_lab_card_id, 无法跳转 aigc-lab。</div>
  }
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, background: 'transparent' }} data-testid="material-aigc-image">
      {/* 工具条不再一排等权链接: 标题占主, 主操作做显眼按钮, 低频(复制链接)收进 ⋯ */}
      <div style={TOOLBAR}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: domainColor.cyan, flex: 'none' }} />
        <span style={{ fontSize: 14, color: COLORS.text, fontWeight: 600, letterSpacing: '-0.01em', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>AIGC 图片</span>
        <a
          href={labUrl} target="_blank" rel="noreferrer"
          data-testid="material-aigc-open"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 7, background: COLORS.borderActive, color: '#fff', fontSize: 13, fontWeight: 600, textDecoration: 'none', boxShadow: '0 2px 10px rgba(0,0,0,.30)' }}
        >
          <ExternalLink size={14} /> 在 aigc-lab 打开
        </a>
        <KebabMenu testid="material-aigc-more" items={[
          { label: '复制 aigc-lab 链接', icon: <Copy size={15} />, testid: 'material-aigc-copy-url', onClick: () => { try { void navigator.clipboard?.writeText(labUrl) } catch { /* */ } } },
        ] as KebabItem[]} />
      </div>
      <iframe
        data-testid="material-aigc-iframe"
        src={labUrl}
        sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
        style={{ flex: 1, border: 'none', background: '#fff', width: '100%', minHeight: 0 }}
        title={m.title}
      />
    </div>
  )
}


export function CustomTemplateMaterialView({ m }: { m: Material }) {
  // 注册了业务类型渲染器的材料在 MaterialContentView 就被解析走了(schema 优先于 kind),
  // 走到这里的只剩两种: 没声明 data_schema_id, 或声明了但注册表里没有 → 显式回退, 不白屏。
  let parsed: any = null
  try {
    parsed = m.inline_content ? JSON.parse(m.inline_content) : null
  } catch { /* */ }
  const schemaId = schemaIdOf(m)

  // root 透明吃全局渐变; 工具条玻璃薄条粘顶。
  return (
    <div data-testid="material-custom-template" style={{ height: '100%', overflow: 'auto', background: 'transparent' }}>
      {/* 工具条: 标题 + schema 做等宽 chip + 未注册做警告徽章(状态叠图标+文案, 不只靠色) */}
      <div style={{ ...TOOLBAR, position: 'sticky', top: 0, zIndex: 1, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, color: COLORS.text, fontWeight: 600, letterSpacing: '-0.01em' }}>自定义网页模板</span>
        <span style={{ fontSize: 12, fontFamily: MONO, color: COLORS.textDim, padding: '2px 9px', borderRadius: 999, background: 'rgba(255,255,255,.05)', border: `1px solid ${COLORS.border}` }}>
          schema={schemaId || '(none)'}
        </span>
        {schemaId && (
          <span style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 600, padding: '2px 9px', borderRadius: 999, color: COLORS.important, background: WARN_SOFT, border: `1px solid ${WARN_RIM}` }}>
            <TriangleAlert size={11} aria-hidden style={{ verticalAlign: -1 }} /> 未注册 schema · 回退 raw JSON
          </span>
        )}
      </div>
      <pre style={{ padding: 24, color: COLORS.textDim, fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', fontFamily: MONO }}>
        {parsed ? JSON.stringify(parsed, null, 2) : (m.inline_content || '(no content)')}
      </pre>
    </div>
  )
}


export function KeyQuestionMaterialView({ m }: { m: Material }) {
  let parsed: any = null
  try {
    parsed = m.inline_content ? JSON.parse(m.inline_content) : null
  } catch { /* */ }

  if (!parsed) {
    return <pre style={{ padding: 24, color: COLORS.textDim, fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', fontFamily: MONO }}>{m.inline_content || '(no content)'}</pre>
  }
  const question = parsed.question || ''
  const options = parsed.options as string[] | undefined
  const explanation = parsed.explanation || ''

  // root 透明吃渐变; 问题/选项玻璃卡浮其上, 居中阅读宽度。
  return (
    <div data-testid="material-key-question" style={{ padding: 24, color: COLORS.text, height: '100%', overflow: 'auto', maxWidth: 880, margin: '0 auto', background: 'transparent' }}>
      {/* 问题做单一主焦点: 玻璃卡内嵌弱灰标签 chip + 18px 题干, 字号建层级 */}
      <div style={{ ...GLASS, borderLeft: `3px solid ${COLORS.important}`, padding: 20, marginBottom: 20 }}>
        <span style={{ display: 'inline-block', fontSize: 12, fontWeight: 600, letterSpacing: '.04em', color: COLORS.important, padding: '2px 9px', borderRadius: 999, background: WARN_SOFT, marginBottom: 12 }}>关键问题</span>
        <div style={{ fontSize: 18, fontWeight: 600, lineHeight: 1.5, letterSpacing: '-0.01em' }}>{question}</div>
      </div>
      {options && options.length > 0 && (
        // 选项从单列 → 自适应卡片网格, 字母做醒目圆 chip(signifier: 看起来可点)
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
          {options.map((opt, i) => (
            <div key={i}
              style={{
                ...GLASS, padding: 14, cursor: 'pointer', fontSize: 14, lineHeight: 1.5,
                display: 'flex', alignItems: 'flex-start', gap: 12, minWidth: 0,
                transition: 'border-color 150ms cubic-bezier(0.175,0.885,0.32,1.1), background 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = COLORS.borderActive; e.currentTarget.style.background = 'var(--fp-glass-2)' }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = COLORS.border; e.currentTarget.style.background = 'var(--fp-glass)' }}
            >
              <span style={{ flex: 'none', width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 700, fontFamily: MONO, color: COLORS.textDim, background: 'rgba(255,255,255,.06)', border: `1px solid ${COLORS.border}` }}>{String.fromCharCode(65 + i)}</span>
              <span style={{ flex: 1, minWidth: 0 }}>{opt}</span>
            </div>
          ))}
        </div>
      )}
      {explanation && (
        // 说明降级为渐进披露(默认收起), 与选项区拉开间距
        <details style={{ ...GLASS, marginTop: 20, color: COLORS.textDim, fontSize: 13, padding: '12px 16px' }}>
          <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 550, color: COLORS.textDim, listStyle: 'none' }}>说明 ›</summary>
          <div style={{ padding: '12px 0 0', lineHeight: 1.5 }}>{explanation}</div>
        </details>
      )}
    </div>
  )
}


// 视频材料(2026-06-24 公众号总览可读媒体之一): 本地文件走 <video>, YouTube/Vimeo 走 iframe 嵌入。
// extra.video_url = 外链; extra.poster_url = 封面; file_relpath = 本地视频(经 dashboard 代理同源)。
// 注: 计划里的 react-player light 模式后续再换, 这里先零依赖原生实现。
function youtubeEmbed(url: string): string | null {
  const m = url.match(/(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([\w-]{11})/)
  return m ? `https://www.youtube.com/embed/${m[1]}` : null
}

// 2026-07-07 视频审阅整改(用户: "视频从来没有能在审阅台成功播放过, 尺寸也不对"):
// ①尺寸=显式 16:9 纵横比盒 + 72vh 封顶, 不再依赖父级不定高的 maxHeight:100%(塌缩根因);
// ②播放失败不再黑屏死路: onError → 可读错误面板, 动作条(浏览器直开/下载/复制链接)常驻;
// ③帧带兜底: 服务端 headless 抽帧(/frames), 播放器环境再烂(VSCode webview/移动端)帧带也能审。
export function VideoMaterialView({ m }: { m: Material }) {
  const extra = (m.extra as Record<string, unknown> | undefined) || {}
  const videoUrl = (extra.video_url as string | undefined) || undefined
  const posterUrl = (extra.poster_url as string | undefined) || undefined
  const fileSrc = m.file_relpath ? reviewstageApi.fileUrl(m.id) : undefined
  const src = fileSrc ?? (videoUrl ? rewriteLoopbackUrlForLan(videoUrl) : undefined)
  const yt = videoUrl ? youtubeEmbed(videoUrl) : null
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [playError, setPlayError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [frames, setFrames] = useState<{ index: number; position: number; url: string }[]>([])

  const absSrc = useMemo(() => {
    if (!src) return ''
    try { return new URL(src, window.location.origin).toString() } catch { return src }
  }, [src])
  const ext = (m.file_relpath || videoUrl || '').split('.').pop()?.toLowerCase() || ''
  const mime = ({ webm: 'video/webm', mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', ogv: 'video/ogg' } as Record<string, string>)[ext]

  useEffect(() => {
    if (yt || !m.id) return
    let alive = true
    fetch(`${reviewstageApi.fileUrl(m.id).replace(/\/file$/, '/frames')}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d?.frames?.length) setFrames(d.frames) })
      .catch(() => { /* 帧带未生成 → 只显播放器 */ })
    return () => { alive = false }
  }, [m.id, yt])

  if (yt) {
    return (
      <iframe
        data-testid="material-video"
        src={yt}
        sandbox="allow-same-origin allow-scripts allow-presentation"
        allow="encrypted-media; picture-in-picture; fullscreen"
        style={{ flex: 1, border: 'none', background: '#000', width: '100%', minHeight: 0 }}
        title={m.title}
      />
    )
  }
  if (!src) {
    return <div data-testid="material-video" style={{ padding: 24, color: 'var(--fp-text-3)', fontSize: 13 }}>（无视频源）</div>
  }

  const copyLink = () => {
    void navigator.clipboard?.writeText(absSrc).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    }).catch(() => { /* webview 剪贴板被拒时静默 */ })
  }
  const seekTo = (position: number) => {
    const v = videoRef.current
    if (v && !playError && Number.isFinite(v.duration) && v.duration > 0) {
      v.currentTime = v.duration * position
      void v.play().catch(() => { /* 环境禁播放时帧带自身仍可看 */ })
    }
  }

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10, padding: '12px 16px' }}>
      {playError ? (
        <div
          data-testid="material-video-error"
          style={{ ...GLASS, width: '100%', aspectRatio: '16 / 9', maxHeight: '60vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, background: WARN_SOFT, borderColor: WARN_RIM }}
        >
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--fp-text-1)' }}>此环境播放失败（{playError}）</div>
          <div style={{ fontSize: 12.5, color: 'var(--fp-text-3)', maxWidth: 520, textAlign: 'center', lineHeight: 1.6 }}>
            审阅不必卡在播放器：下方帧带是服务端抽好的关键帧；也可用"在浏览器打开"用系统浏览器满血播放。
          </div>
        </div>
      ) : (
        <div style={{ width: '100%', aspectRatio: '16 / 9', maxHeight: '72vh', background: '#000', borderRadius: 10, overflow: 'hidden', flexShrink: 0 }}>
          <video
            ref={videoRef}
            data-testid="material-video"
            poster={posterUrl || reviewstageApi.coverUrl(m.id, m.updated_at || '')}
            controls
            playsInline
            preload="metadata"
            onError={() => {
              const err = videoRef.current?.error
              setPlayError(err ? `code ${err.code}${err.message ? ` · ${err.message}` : ''}` : '未知错误')
            }}
            style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
          >
            <source src={src} {...(mime ? { type: mime } : {})} />
          </video>
        </div>
      )}

      {/* 动作条: 播放器好坏都常驻——浏览器直开是任何环境的保底审阅路径 */}
      <div data-testid="material-video-actions" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', flexShrink: 0 }}>
        <a
          href={absSrc}
          target="_blank"
          rel="noreferrer"
          style={{ ...GLASS, padding: '6px 14px', fontSize: 12.5, color: 'var(--fp-accent)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <ExternalLink size={13} /> 在浏览器打开
        </a>
        <a
          href={absSrc}
          download={`${m.id}.${ext || 'webm'}`}
          style={{ ...GLASS, padding: '6px 14px', fontSize: 12.5, color: 'var(--fp-text-2)', textDecoration: 'none' }}
        >
          下载
        </a>
        <button
          type="button"
          onClick={copyLink}
          style={{ ...GLASS, padding: '6px 14px', fontSize: 12.5, color: 'var(--fp-text-2)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <Copy size={13} /> {copied ? '已复制' : '复制链接'}
        </button>
        {frames.length > 0 && <span style={{ fontSize: 12, color: 'var(--fp-text-3)', fontFamily: MONO }}>帧带 {frames.length} 帧 · 点帧跳播</span>}
      </div>

      {/* 帧带: 播放器之外的审阅保底面(服务端 headless 抽帧) */}
      {frames.length > 0 && (
        <div data-testid="material-video-frames" style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 4, flexShrink: 0 }}>
          {frames.map((f) => (
            <a
              key={f.index}
              href={f.url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => {
                if (!playError) { e.preventDefault(); seekTo(f.position) }
              }}
              title={`${Math.round(f.position * 100)}% 处 · 点击${playError ? '看大图' : '跳播'}`}
              style={{ flexShrink: 0, borderRadius: 8, overflow: 'hidden', border: `1px solid ${COLORS.border}`, lineHeight: 0 }}
            >
              <img src={f.url} alt={`frame ${f.index}`} style={{ height: 86, display: 'block' }} />
            </a>
          ))}
        </div>
      )}
    </div>
  )
}


// 文档里"选中文字就地评论"(用户 2026-06-14): 选一段 → 选区旁冒出"评论"小按钮 → 点开就地写 →
// 落进该材料的评论 .md(锚点=选中文字)。不依赖右侧评论栏是否在显示, 文档自带这条路。
type SelectionRect = { left: number; top: number; width: number; height: number }

function SelectionCommentLayer({ material, surfaceRef }: { material: Material; surfaceRef: React.RefObject<HTMLDivElement> }) {
  const [sel, setSel] = useState<{ text: string; x: number; y: number; rects: SelectionRect[] } | null>(null)
  const [composing, setComposing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')

  useEffect(() => {
    const surface = surfaceRef.current
    if (!surface) return
    const onUp = (event: MouseEvent) => {
      // layer 实际仍是 surface 的 DOM 后代；显式排除它，避免点输入框时被 mouseup
      // 误判成新选区并把 composing 重置掉。
      if (event.target instanceof Element && event.target.closest('[data-selection-comment-ui="true"]')) return
      const s = window.getSelection()
      const text = s?.toString().replace(/\s+/g, ' ').trim() || ''
      if (!text || !s || s.rangeCount === 0 || !s.anchorNode || !surface.contains(s.anchorNode)) return
      const range = s.getRangeAt(0)
      const r = range.getBoundingClientRect()
      const rects = Array.from(range.getClientRects())
        .filter((rect) => rect.width > 0 && rect.height > 0)
        .map((rect) => ({ left: rect.left, top: rect.top, width: rect.width, height: rect.height }))
      if (rects.length === 0 && r.width > 0 && r.height > 0) {
        rects.push({ left: r.left, top: r.top, width: r.width, height: r.height })
      }
      const x = Math.min(Math.max(r.left + r.width / 2, 80), window.innerWidth - 200)
      setSel({ text: text.slice(0, 400), x, y: r.bottom, rects })
      setComposing(false); setDraft('')
    }
    const clear = () => { setSel(null); setComposing(false) }
    surface.addEventListener('mouseup', onUp)
    surface.addEventListener('scroll', clear, true)
    window.addEventListener('resize', clear)
    return () => {
      surface.removeEventListener('mouseup', onUp)
      surface.removeEventListener('scroll', clear, true)
      window.removeEventListener('resize', clear)
    }
  }, [surfaceRef])

  const submit = async () => {
    const text = draft.trim()
    if (!text || !sel) return
    setBusy(true)
    try {
      await reviewstageApi.appendCommentsFile(material.id, text, sel.text, material.title)
      setSel(null); setComposing(false); setDraft('')
      window.getSelection()?.removeAllRanges()
      setToast('已评论'); setTimeout(() => setToast(''), 1200)
    } catch (e) {
      setToast(`失败: ${String(e instanceof Error ? e.message : e)}`); setTimeout(() => setToast(''), 1600)
    } finally { setBusy(false) }
  }

  return createPortal(
    <>
      {sel?.rects.map((rect, index) => (
        <span
          key={`${rect.left}:${rect.top}:${index}`}
          className="rf-selection-mark"
          data-testid="selection-comment-mark"
          aria-hidden="true"
          style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
        />
      ))}
      {sel && !composing && (
        <div
          className="rf-selection-action-menu"
          data-selection-comment-ui="true"
          style={{ left: sel.x, top: sel.y + 7 }}
        >
          <button
            type="button"
            className="rf-selection-comment-action"
            data-testid="selection-comment-btn"
            onMouseDown={(e) => e.preventDefault()} // 别让点击清掉选区
            onClick={() => setComposing(true)}
          ><MessageSquare size={13} aria-hidden /> 评论</button>
        </div>
      )}
      {sel && composing && (
        <div
          className="rf-selection-composer"
          data-selection-comment-ui="true"
          data-testid="selection-comment-composer"
          onMouseDown={(e) => e.stopPropagation()}
          style={{ left: sel.x, top: sel.y + 7 }}
        >
          <div className="rf-selection-anchor">锚点 · {sel.text.slice(0, 60)}{sel.text.length > 60 ? '…' : ''}</div>
          <textarea
            autoFocus
            className="rf-selection-input"
            data-testid="selection-comment-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') void submit() }}
            placeholder="对选中文字写评论(Ctrl+Enter 提交)…"
          />
          <div className="rf-selection-actions">
            <button type="button" className="rf-selection-cancel" onClick={() => { setSel(null); setComposing(false) }}>取消</button>
            <button type="button" className="rf-selection-submit" data-testid="selection-comment-submit" disabled={!draft.trim() || busy} onClick={() => void submit()}>{busy ? '…' : '提交'}</button>
          </div>
        </div>
      )}
      {toast && <div className="rf-selection-toast" data-selection-comment-ui="true">{toast}</div>}
    </>,
    document.getElementById('root') ?? document.body,
  )
}

// ── 内建渲染器登记(模块顶层执行一次): kind/schema → 组件 + 容器形态元数据 ──
// fullBleed=占满型(内含 iframe/复合视图, flex 撑满让 iframe 拉满高度, 否则回落 150px);
// document=文档型(文字内容, 挂"选中即评论"层)。行为与旧内联分支逐项一致。
registerKindRenderer('image', { Component: ImageMaterialView })
registerKindRenderer('markdown', { Component: MarkdownMaterialView, document: true })
registerKindRenderer('html', { Component: HtmlMaterialView, fullBleed: true })
registerKindRenderer('key_question', { Component: KeyQuestionMaterialView, document: true })
registerKindRenderer('custom_web_template', { Component: CustomTemplateMaterialView, document: true })
registerKindRenderer('video', { Component: VideoMaterialView, fullBleed: true })
registerKindRenderer('webgame-spec', { Component: WebgameSpecView, fullBleed: true })
// WORK-REPORT-AND-REVIEW-TYPES 五型: 计划/工作报告=文档, 报告/演示=网页, AI 图=aigc-lab 枢纽
registerKindRenderer('plan', { Component: MarkdownMaterialView, document: true })
registerKindRenderer('agent-workflow-report', { Component: MarkdownMaterialView, document: true })
registerKindRenderer('static-report', { Component: HtmlMaterialView, fullBleed: true })
registerKindRenderer('demo', { Component: HtmlMaterialView, fullBleed: true })
registerKindRenderer('aigc-image', { Component: AigcImageView, fullBleed: true })
// 决策候选(候选流水线唯一队列): markdown 草案正文 + 机器载荷在 extra.candidate
registerKindRenderer('decision-candidate', { Component: MarkdownMaterialView, document: true })
// 内建业务类型两例(原自定义模板内联注册表并入)
registerSchemaRenderer('filetree_diff_v1', { Component: FileTreeDiffSchemaView })
registerSchemaRenderer('branch_storyline_v1', { Component: BranchStorylineSchemaView })

// 统一分发: rendererRegistry 解析(schema 优先于 kind), 未注册显式回退。
// onMouseUp 文本选择 / data-testid="material-selection-surface" 随原样保留.
export function MaterialContentView({ m, onElementSelect, onTextSelection }: {
  m: Material
  onElementSelect: (selector: string) => void
  onTextSelection?: () => void
}) {
  void onElementSelect // html 选元素入口已撤, prop 保留兼容消费方
  const entry = resolveMaterialRenderer(m)
  const surfaceRef = useRef<HTMLDivElement>(null)
  const Body = entry?.Component
  return (
    <div
      ref={surfaceRef}
      className="fp-scroll"
      style={{ flex: 1, minWidth: 0, minHeight: 0, ...(entry?.fullBleed ? { display: 'flex', flexDirection: 'column' } : { overflow: 'auto' }) }}
      onMouseUp={onTextSelection}
      data-testid="material-selection-surface"
    >
      {Body ? (
        <Suspense fallback={<div style={{ padding: 24, color: 'var(--fp-text-3)', fontSize: 13 }}>渲染器载入中…</div>}>
          <Body m={m} />
        </Suspense>
      ) : (
        <div data-testid="material-unknown-kind" style={{ padding: 24, color: COLORS.textDim, fontSize: 13, lineHeight: 1.6 }}>
          未注册的材料类型 <code style={{ fontFamily: MONO }}>{String(m.kind)}</code> — 按原文显示:
          <pre style={{ marginTop: 12, whiteSpace: 'pre-wrap', fontFamily: MONO }}>{m.inline_content || '(无内联内容)'}</pre>
        </div>
      )}
      {entry?.document && <SelectionCommentLayer material={m} surfaceRef={surfaceRef} />}
    </div>
  )
}
