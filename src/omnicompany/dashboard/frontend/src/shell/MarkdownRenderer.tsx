/**
 * MarkdownRenderer — single shared renderer used by Note Editor / Worker.设计 / future Plan / etc.
 * Style base: github-markdown-css (dark theme). Custom additions: callouts, wikilinks, mermaid.
 */
import React, { useState, useRef, lazy, Suspense } from 'react'
import HoverCard from './HoverCard'
import type { EntityType } from '../entities/types'
import { usePanels } from '../stores/panelsStore'
import { useUiSettings, PREVIEW_FONT_LIMITS } from '../stores/uiSettingsStore'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import remarkFrontmatter from 'remark-frontmatter'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
// 代码高亮器懒加载: react-syntax-highlighter(PrismLight + 语言集)拆到 ./LazyHighlighterLight,
// 渲染到代码块时才下载 syntax-highlighter chunk(详见该文件注释)。
const LazyHighlighterLight = lazy(() => import('./LazyHighlighterLight'))
import 'github-markdown-css/github-markdown-dark.css'
import 'katex/dist/katex.min.css'
import { remarkWikilinks } from '../entities/note/wikilinks'
import { remarkCallouts, CALLOUT_COLORS } from '../entities/note/callouts'
import { remarkFrontmatterRender } from '../entities/note/frontmatter'
import { remarkObsidianHighlight, stripObsidianComments } from '../entities/note/obsidianExtras'
import MermaidBlock from '../entities/note/MermaidBlock'
import { MaterialEmbed } from '../entities/review/MaterialEmbed'
import Tooltip from '../shared/view/ui/Tooltip'
import './markdown-extras.css'

export interface WikilinkClickPayload {
  type: EntityType
  target: string
  heading?: string
}

export interface MarkdownRendererProps {
  source: string
  /** Legacy: called for note-only wikilinks (back-compat). Prefer onEntityLink. */
  onWikilinkClick?: (target: string, heading?: string) => void
  /** New: called for any [[..]] including omni:// cross-entity. */
  onEntityLink?: (payload: WikilinkClickPayload) => void
  /** Optional override / extension components (e.g. Note adds paragraph annotation wrap). */
  componentsOverride?: Record<string, any>
  /** Extra className appended to .markdown-body wrapper. */
  className?: string
  /** Note ID of the current document (e.g. `standards/terminology`). Used to resolve relative `.md` links. */
  currentPath?: string
}

const ABSOLUTE_URL_RE = /^([a-z][a-z0-9+.-]*):/i  // http: / https: / mailto: / tel: / file: / etc.

/** Resolve a markdown link href into a note id, or `null` if it's not a recognizable doc reference. */
export function resolveMdHref(href: string, currentPath?: string): string | null {
  if (!href) return null
  if (ABSOLUTE_URL_RE.test(href)) return null
  if (href.startsWith('#')) return null

  let path = href
  let frag = ''
  const hashIdx = path.indexOf('#')
  if (hashIdx >= 0) { frag = path.slice(hashIdx); path = path.slice(0, hashIdx) }
  const qIdx = path.indexOf('?')
  if (qIdx >= 0) path = path.slice(0, qIdx)

  if (!path.endsWith('.md')) return null

  // Resolve `./` and `../` relative to currentPath's directory.
  if (path.startsWith('./') || path.startsWith('../')) {
    if (!currentPath) return null
    const baseParts = currentPath.split('/').slice(0, -1)
    for (const seg of path.split('/')) {
      if (seg === '' || seg === '.') continue
      if (seg === '..') baseParts.pop()
      else baseParts.push(seg)
    }
    path = baseParts.join('/')
  }

  if (path.startsWith('/')) path = path.slice(1)
  if (path.startsWith('docs/')) path = path.slice('docs/'.length)
  if (path.endsWith('.md')) path = path.slice(0, -3)

  return path + frag
}

export function buildBaseComponents(
  onWikilinkClick?: (target: string, heading?: string) => void,
  onEntityLink?: (p: WikilinkClickPayload) => void,
  hover?: { onEnter: (t: EntityType, id: string, el: HTMLElement) => void; onLeave: () => void },
  openNoteTab?: (noteId: string) => void,
  currentPath?: string,
) {
  return {
    a({ node, children, href, ...props }: any) {
      const wl = node?.properties?.['data-wikilink'] || node?.properties?.dataWikilink
      if (wl) {
        const entityType = (node?.properties?.['data-entity-type'] || node?.properties?.dataEntityType || 'note') as EntityType
        const heading = node?.properties?.['data-heading'] || node?.properties?.dataHeading
        return (
          <a href="#" className={`wikilink wikilink-${entityType}`}
            onClick={(e) => {
              e.preventDefault()
              if (onEntityLink) onEntityLink({ type: entityType, target: wl, heading: heading || undefined })
              else if (entityType === 'note') onWikilinkClick?.(wl, heading || undefined)
            }}
            onMouseEnter={(e) => hover?.onEnter(entityType, wl, e.currentTarget)}
            onMouseLeave={() => hover?.onLeave()}
            {...props}>
            {children}
          </a>
        )
      }

      const h: string = href || ''

      // External URLs (http, https, mailto, tel, etc.) → new tab.
      if (ABSOLUTE_URL_RE.test(h)) {
        return <a href={h} target="_blank" rel="noreferrer" {...props}>{children}</a>
      }

      // In-page anchor — let browser handle.
      if (h.startsWith('#')) {
        return <a href={h} {...props}>{children}</a>
      }

      // Relative / docs-rooted .md → open as note tab.
      const noteId = resolveMdHref(h, currentPath)
      if (noteId !== null && openNoteTab) {
        // Strip any trailing #frag for now (heading anchor jump TBD).
        const cleanId = noteId.split('#')[0]
        return (
          <a href="#" className="md-link" data-md-link={cleanId}
            onClick={(e) => { e.preventDefault(); openNoteTab(cleanId) }}
            {...props}>
            {children}
          </a>
        )
      }

      // Unknown relative path — fall back to external new-tab behavior.
      return <a href={h} target="_blank" rel="noreferrer" {...props}>{children}</a>
    },
    img({ node, src, alt, ...props }: any) {
      const s: string = src || ''
      // Asset embed sentinel emitted by remarkWikilinks for `![[image.png]]`
      if (s.startsWith('#asset:') && currentPath) {
        const assetPath = s.slice('#asset:'.length)
        // Encode each path segment so spaces / unicode survive URL routing.
        const encoded = assetPath.split('/').map(encodeURIComponent).join('/')
        const url = `/api/notes/${encodeURIComponent(currentPath)}/asset/${encoded}`
        return (
          <img
            src={url} alt={alt || assetPath}
            data-wiki-asset={assetPath}
            style={{ maxWidth: '100%', borderRadius: 4, margin: '8px 0' }}
            {...props}
          />
        )
      }
      // Pass-through for vanilla `![alt](url)` (absolute or external)
      return <img src={s} alt={alt} style={{ maxWidth: '100%' }} {...props} />
    },
    span({ node, children, ...props }: any) {
      const reference = node?.properties?.['data-review-material-embed']
        || node?.properties?.dataReviewMaterialEmbed
      if (reference) {
        const label = node?.properties?.['data-review-material-label']
          || node?.properties?.dataReviewMaterialLabel
        return <MaterialEmbed reference={String(reference)} label={label ? String(label) : undefined} />
      }
      return <span {...props}>{children}</span>
    },
    code({ node, inline, className, children, ...props }: any) {
      const match = /language-(\w+)/.exec(className || '')
      const lang = match ? match[1] : ''
      const value = String(children).replace(/\n$/, '')
      if (!inline && lang === 'mermaid') return <MermaidBlock source={value} />
      if (!inline && lang) {
        return (
          <Suspense fallback={<pre style={{ margin: '8px 0', borderRadius: 6, fontSize: 14, background: 'var(--fp-bg)', padding: 12, overflow: 'auto', color: '#c9d1d9' }}><code>{value}</code></pre>}>
            <LazyHighlighterLight language={lang} value={value} />
          </Suspense>
        )
      }
      return <code className={className} {...props}>{children}</code>
    },
    div({ node, children, className, ...props }: any) {
      const callout = node?.properties?.['data-callout'] || node?.properties?.dataCallout
      if (callout) {
        const title = node?.properties?.['data-callout-title'] || node?.properties?.dataCalloutTitle || callout
        const conf = CALLOUT_COLORS[callout as string] || CALLOUT_COLORS.note
        return (
          <div data-callout={callout} className={`callout callout-${callout}`}
            style={{
              background: conf.bg, borderLeft: `3px solid ${conf.color}`,
              padding: '10px 14px', borderRadius: 6, margin: '12px 0',
            }}>
            <div style={{ color: conf.color, fontWeight: 600, marginBottom: 6, fontSize: 15 }}>
              {conf.icon} {title}
            </div>
            <div style={{ color: '#c9d1d9', fontSize: 15, lineHeight: 1.5 }}>{children}</div>
          </div>
        )
      }
      const fmRaw = node?.properties?.['data-frontmatter'] || node?.properties?.dataFrontmatter
      if (fmRaw) {
        const json = node?.properties?.['data-frontmatter-json'] || node?.properties?.dataFrontmatterJson || '{}'
        const err = node?.properties?.['data-frontmatter-error'] || node?.properties?.dataFrontmatterError || ''
        let entries: [string, unknown][] = []
        try { entries = Object.entries(JSON.parse(json)) } catch { /* */ }
        return (
          <div data-frontmatter="1" className="frontmatter-card"
            style={{
              background: 'var(--fp-bg)', border: '1px solid #1f2933', borderRadius: 6,
              padding: '8px 12px', margin: '4px 0 14px', fontSize: 14,
              fontFamily: 'Consolas, Menlo, monospace', color: '#c9d1d9',
            }}>
            <div style={{ color: '#90caf9', fontSize: 14, textTransform: 'uppercase', marginBottom: 6, letterSpacing: 0.5 }}>
              frontmatter {err && <span style={{ color: '#ef5350' }}>· parse error</span>}
            </div>
            {entries.length === 0 ? (
              <div style={{ color: '#666', fontStyle: 'italic' }}>(empty)</div>
            ) : (
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <tbody>
                  {entries.map(([k, v]) => (
                    <tr key={k} data-fm-key={k}>
                      <td style={{ color: 'var(--fp-link)', padding: '2px 8px 2px 0', verticalAlign: 'top', whiteSpace: 'nowrap' }}>{k}</td>
                      <td style={{ color: '#c9d1d9', wordBreak: 'break-all' }}>
                        {typeof v === 'object' ? <code>{JSON.stringify(v)}</code> : String(v)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )
      }
      return <div className={className} {...props}>{children}</div>
    },
  }
}

// remark-frontmatter (and our render hook) MUST come before others so the
// `yaml` node is replaced before downstream visits text.
export const REMARK_PLUGINS = [
  remarkFrontmatter, remarkFrontmatterRender,
  remarkGfm, remarkMath,
  remarkObsidianHighlight,        // ==text== → <mark>
  remarkCallouts, remarkWikilinks,
]
// rehype-raw allows inline HTML (sup / sub / kbd / details / etc.) per Obsidian spec.
export const REHYPE_PLUGINS = [rehypeRaw, rehypeKatex]

const FS_BTN: React.CSSProperties = {
  background: 'transparent', color: '#8a9ba8', border: '1px solid #2a3a4a', borderRadius: 3,
  cursor: 'pointer', padding: '1px 6px', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace',
  userSelect: 'none',
}

const FontSizeControl: React.FC = () => {
  const fs = useUiSettings((s) => s.previewFontSize)
  const bump = useUiSettings((s) => s.bumpPreviewFontSize)
  const reset = useUiSettings((s) => s.resetPreviewFontSize)
  return (
    // absolute 定位挪到 Tooltip wrapper(原容器自身 absolute; 包一层后定位参照会变成 wrapper)。
    <Tooltip content={`预览字号 ${fs}px (${PREVIEW_FONT_LIMITS.min}-${PREVIEW_FONT_LIMITS.max})`} position="left" containerStyle={{ position: 'absolute', top: 6, right: 8, zIndex: 5 }}>
      <div
        data-md-fontctl
        style={{
          display: 'flex', gap: 2, opacity: 0.55,
          background: 'rgba(15,15,15,0.7)', padding: '2px 4px', borderRadius: 4,
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.opacity = '1' }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.opacity = '0.55' }}
      >
        <button data-md-fontctl-minus style={FS_BTN} aria-label="缩小字号" onClick={() => bump(-1)}>A−</button>
        <button data-md-fontctl-reset style={{ ...FS_BTN, minWidth: 28 }} aria-label="重置字号" onClick={reset}>{fs}</button>
        <button data-md-fontctl-plus style={FS_BTN} aria-label="放大字号" onClick={() => bump(1)}>A+</button>
      </div>
    </Tooltip>
  )
}

export default function MarkdownRenderer({ source, onWikilinkClick, onEntityLink, componentsOverride, className, currentPath }: MarkdownRendererProps) {
  const openTab = usePanels((s) => s.openTab)
  const fontSize = useUiSettings((s) => s.previewFontSize)
  const [hover, setHover] = useState<{ type: EntityType; id: string; el: HTMLElement } | null>(null)
  const hoverTimer = useRef<number | null>(null)

  const defaultEntityLink = (p: WikilinkClickPayload) => {
    openTab({ type: p.type, id: p.target }, p.target.split('/').pop() || p.target)
  }
  const effectiveEntityLink = onEntityLink || defaultEntityLink

  const openNoteTab = (noteId: string) => {
    openTab({ type: 'note', id: noteId }, noteId.split('/').pop() || noteId)
  }

  const handleHoverEnter = (type: EntityType, id: string, el: HTMLElement) => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
    hoverTimer.current = window.setTimeout(() => setHover({ type, id, el }), 400)
  }
  const handleHoverLeave = () => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
    hoverTimer.current = window.setTimeout(() => setHover(null), 200)
  }

  const components = {
    ...buildBaseComponents(onWikilinkClick, effectiveEntityLink, { onEnter: handleHoverEnter, onLeave: handleHoverLeave }, openNoteTab, currentPath),
    ...(componentsOverride || {}),
  }
  return (
    <div
      className={`markdown-body${className ? ' ' + className : ''}`}
      style={{ background: 'transparent', fontSize: `${fontSize}px`, position: 'relative' }}
      data-preview-fontsize={fontSize}
    >
      <FontSizeControl />
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={components as any}>
        {stripObsidianComments(source)}
      </ReactMarkdown>
      {hover && (
        <HoverCard
          type={hover.type}
          id={hover.id}
          anchorEl={hover.el}
          onClose={() => setHover(null)}
        />
      )}
    </div>
  )
}
