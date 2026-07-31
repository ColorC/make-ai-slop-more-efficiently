import { visit, SKIP } from 'unist-util-visit'
import type { EntityType } from '../types'

// `![[image.png]]` (embed) or `[[Name]]` (link). Group 1 = `!` if embed, else empty.
const WIKILINK = /(!?)\[\[([^\[\]]+)\]\]/g

const IMAGE_EXT = /\.(png|jpe?g|gif|svg|webp|bmp|ico|avif)$/i

const KNOWN_TYPES: ReadonlyArray<EntityType> = [
  'note', 'graph', 'plan', 'trace', 'session',
  'worker', 'material', 'review_material', 'team', 'settings',
]

const CANONICAL_TYPE_ALIASES: Readonly<Record<string, EntityType>> = {
  review: 'review_material',
}

export interface ParsedWikilink {
  /** Default 'note' (back-compat); 'omni://<type>/<id>' or '<type>:<id>' resolves to other entity types. */
  entityType: EntityType
  target: string
  display: string
  heading?: string
}

function detectEntityType(body: string): { type: EntityType; rest: string } {
  // omni://<type>/<id...>
  const omniMatch = body.match(/^omni:\/\/([\w-]+)\/(.+)$/)
  if (omniMatch) {
    const type = CANONICAL_TYPE_ALIASES[omniMatch[1]] || omniMatch[1] as EntityType
    if (KNOWN_TYPES.includes(type)) {
      let rest = omniMatch[2]
      try { rest = decodeURIComponent(rest) } catch { /* keep malformed input visible */ }
      return { type, rest }
    }
  }
  // <type>:<id> short form (only if type matches and rest non-empty + colon not part of normal note title)
  const shortMatch = body.match(/^([\w-]+):(.+)$/)
  if (shortMatch) {
    const type = CANONICAL_TYPE_ALIASES[shortMatch[1]] || shortMatch[1] as EntityType
    if (KNOWN_TYPES.includes(type)) return { type, rest: shortMatch[2] }
  }
  return { type: 'note', rest: body }
}

export function parseWikilink(raw: string): ParsedWikilink {
  let body = raw
  let display = raw
  if (body.includes('|')) {
    const [t, d] = body.split('|', 2)
    body = t.trim()
    display = (d || t).trim()
  }
  let heading: string | undefined
  if (body.includes('#')) {
    const [t, h] = body.split('#', 2)
    body = t.trim()
    heading = (h || '').trim()
    if (!raw.includes('|')) display = body + (heading ? ` > ${heading}` : '')
  }
  const { type, rest } = detectEntityType(body)
  return { entityType: type, target: rest, display, heading }
}

/** remark plugin: turn `[[Name]]` → `<a>` (entity wikilink); `![[image.png]]` → `<img>` (asset embed).
 * Image src is set to a sentinel `#asset:<path>`; MarkdownRenderer resolves it against
 * the current note's directory at render time.
 */
export function remarkWikilinks() {
  return (tree: any) => {
    visit(tree, 'text', (node: any, index: any, parent: any) => {
      if (!parent || index == null) return
      const value: string = node.value
      if (!value || !value.includes('[[')) return
      const out: any[] = []
      let last = 0
      WIKILINK.lastIndex = 0
      let m: RegExpExecArray | null
      while ((m = WIKILINK.exec(value)) !== null) {
        if (m.index > last) {
          out.push({ type: 'text', value: value.slice(last, m.index) })
        }
        const isEmbed = m[1] === '!'
        const inner = m[2]

        if (isEmbed && IMAGE_EXT.test(inner.split('|')[0].trim())) {
          // Image embed: emit <img> with sentinel src that MarkdownRenderer resolves.
          const [pathRaw, altRaw] = inner.split('|', 2)
          const assetPath = pathRaw.trim()
          out.push({
            type: 'image',
            url: `#asset:${assetPath}`,
            alt: (altRaw || assetPath.split('/').pop() || '').trim(),
            data: {
              hName: 'img',
              hProperties: {
                src: `#asset:${assetPath}`,
                'data-asset-target': assetPath,
                alt: (altRaw || assetPath.split('/').pop() || '').trim(),
                className: ['wiki-asset'],
              },
            },
          })
          last = m.index + m[0].length
          continue
        }

        // Embed of a non-image (e.g. `![[other-note]]`) — treat as wikilink to that note.
        // (block-level note embed is a future feature; for now just link.)
        const parsed = parseWikilink(inner)
        if (isEmbed && parsed.entityType === 'review_material') {
          const canonicalRef = `omni://review/${encodeURIComponent(parsed.target)}`
          out.push({
            type: 'link',
            url: '#review-material-embed',
            data: {
              hName: 'span',
              hProperties: {
                'data-review-material-embed': canonicalRef,
                'data-review-material-label': inner.includes('|') ? parsed.display : '',
                className: ['wiki-review-material-embed'],
              },
            },
            children: [{ type: 'text', value: parsed.display }],
          })
          last = m.index + m[0].length
          continue
        }
        out.push({
          type: 'link',
          url: '#wikilink',
          data: {
            hName: 'a',
            hProperties: {
              href: '#wikilink',
              'data-wikilink': parsed.target,
              'data-entity-type': parsed.entityType,
              'data-heading': parsed.heading || '',
              className: ['wikilink', `wikilink-${parsed.entityType}`],
            },
          },
          children: [{ type: 'text', value: parsed.display }],
        })
        last = m.index + m[0].length
      }
      if (last === 0) return
      if (last < value.length) {
        out.push({ type: 'text', value: value.slice(last) })
      }
      parent.children.splice(index, 1, ...out)
      return [SKIP, index + out.length]
    })
  }
}
