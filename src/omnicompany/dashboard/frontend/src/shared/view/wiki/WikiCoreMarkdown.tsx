import React, { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { createRenderer, stripFrontmatter } from '@wiki-core/render'
import '@wiki-core/ui.css'
import './wikiCoreMarkdown.css'
import { parseWikilink } from '../../../entities/note/wikilinks'
import type { EntityType } from '../../../entities/types'
import { usePanels } from '../../../stores/panelsStore'

export interface WikiCoreEmbedTarget {
  kind: 'material' | 'material_graph' | 'review_material' | 'note'
  target: string
  anchor?: string
  label?: string
}

interface EmbedMount extends WikiCoreEmbedTarget {
  element: HTMLElement
}

export interface WikiCoreMarkdownProps {
  source: string
  className?: string
  materialProject?: string
  renderEmbed?: (target: WikiCoreEmbedTarget) => React.ReactNode
}

function entityHref(type: EntityType, target: string, heading?: string): string {
  const query = new URLSearchParams({ type, target })
  if (heading) query.set('heading', heading)
  return `#omni-wiki?${query}`
}

function parseEmbedTarget(raw: string, label?: string): WikiCoreEmbedTarget {
  const parsed = parseWikilink(raw)
  const kind = parsed.entityType === 'material'
    || parsed.entityType === 'material_graph'
    || parsed.entityType === 'review_material'
    ? parsed.entityType
    : 'note'
  return {
    kind,
    target: parsed.target,
    anchor: parsed.heading,
    label: label || parsed.display,
  }
}

/**
 * Dashboard's browser-safe Wiki Core bridge.
 *
 * Project pages and Material Core both consume this component so Obsidian
 * syntax has one renderer. Rich material transclusions remain React-owned via
 * portals mounted into Wiki Core's canonical `.transclude` placeholders.
 */
export default function WikiCoreMarkdown({ source, className, materialProject, renderEmbed }: WikiCoreMarkdownProps) {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const openTab = usePanels((state) => state.openTab)
  const [embedMounts, setEmbedMounts] = useState<EmbedMount[]>([])

  const renderer = useMemo(() => createRenderer({
    resolveLink: (name: string, anchor?: string, alias?: string) => {
      const parsed = parseWikilink(name)
      const heading = parsed.heading || anchor?.replace(/^#/, '') || undefined
      return {
        href: entityHref(parsed.entityType, parsed.target, heading),
        text: alias || parsed.display,
        broken: false,
      }
    },
    resolveEmbed: (name: string, anchor?: string) => ({
      href: entityHref(parseWikilink(name).entityType, parseWikilink(name).target, anchor?.replace(/^#/, '') || undefined),
      targetSlug: `${name}${anchor || ''}`,
      broken: false,
    }),
    resolveDocLink: (href: string) => {
      const clean = href.split('#', 1)[0]
      if (!clean.toLowerCase().endsWith('.md')) return null
      return { href: entityHref('note', clean.replace(/^\.\//, '').replace(/\.md$/i, '')) }
    },
  }), [])
  const html = useMemo(() => renderer.render(stripFrontmatter(source)), [renderer, source])

  useLayoutEffect(() => {
    if (!renderEmbed || !rootRef.current) {
      rootRef.current?.querySelectorAll('.wiki-core-rich-embed').forEach((element) => {
        element.classList.remove('wiki-core-rich-embed')
      })
      setEmbedMounts([])
      return
    }
    const mounts = [...rootRef.current.querySelectorAll<HTMLElement>('.transclude[data-target]')]
      .map((element) => {
        const target = parseEmbedTarget(
          element.dataset.target || '',
          element.querySelector<HTMLElement>('.transclude-fallback')?.textContent || undefined,
        )
        if (target.kind === 'note') return null
        element.classList.add('wiki-core-rich-embed')
        return { ...target, element }
      })
      .filter((mount): mount is EmbedMount => mount !== null)
    setEmbedMounts(mounts)
  }, [html, renderEmbed])

  const onClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const anchor = (event.target as Element | null)?.closest('a')
    const href = anchor?.getAttribute('href') || ''
    if (!href.startsWith('#omni-wiki?')) return
    event.preventDefault()
    const query = new URLSearchParams(href.slice('#omni-wiki?'.length))
    const type = query.get('type') as EntityType | null
    const target = query.get('target')
    if (!type || !target) return
    if (materialProject && (type === 'material' || type === 'material_graph')) {
      openTab(
        { type: 'studio_reader', id: materialProject },
        `${materialProject} 阅读`,
        type === 'material' ? target : undefined,
      )
      return
    }
    openTab({ type, id: target }, anchor?.textContent?.trim() || target.split('/').pop() || target, query.get('heading') || undefined)
  }

  return <>
    <div
      ref={rootRef}
      className={`wiki-prose wiki-core-markdown${className ? ` ${className}` : ''}`}
      onClick={onClick}
      dangerouslySetInnerHTML={{ __html: html }}
    />
    {embedMounts.map((mount, index) => renderEmbed
      ? createPortal(renderEmbed(mount), mount.element, `${mount.kind}:${mount.target}:${index}`)
      : null)}
  </>
}
