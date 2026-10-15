import type { EntityType } from '../types'

const KNOWN_TYPES: ReadonlyArray<EntityType> = [
  'note', 'graph', 'plan', 'trace', 'session',
  'worker', 'material', 'material_graph', 'review_material', 'team', 'settings',
]

const CANONICAL_TYPE_ALIASES: Readonly<Record<string, EntityType>> = {
  review: 'review_material',
}

export interface ParsedWikilink {
  entityType: EntityType
  target: string
  display: string
  heading?: string
}

function detectEntityType(body: string): { type: EntityType; rest: string } {
  const omniMatch = body.match(/^omni:\/\/([\w-]+)\/(.+)$/)
  if (omniMatch) {
    const type = CANONICAL_TYPE_ALIASES[omniMatch[1]] || omniMatch[1] as EntityType
    if (KNOWN_TYPES.includes(type)) {
      let rest = omniMatch[2]
      try { rest = decodeURIComponent(rest) } catch { /* keep malformed input visible */ }
      return { type, rest }
    }
  }
  const shortMatch = body.match(/^([\w-]+):(.+)$/)
  if (shortMatch) {
    const type = CANONICAL_TYPE_ALIASES[shortMatch[1]] || shortMatch[1] as EntityType
    if (KNOWN_TYPES.includes(type)) return { type, rest: shortMatch[2] }
  }
  return { type: 'note', rest: body }
}

/** Dashboard entity routing for links already parsed by Wiki Core. */
export function parseWikilink(raw: string): ParsedWikilink {
  let body = raw
  let display = raw
  if (body.includes('|')) {
    const [target, alias] = body.split('|', 2)
    body = target.trim()
    display = (alias || target).trim()
  }
  let heading: string | undefined
  if (body.includes('#')) {
    const [target, anchor] = body.split('#', 2)
    body = target.trim()
    heading = (anchor || '').trim()
    if (!raw.includes('|')) display = body + (heading ? ` > ${heading}` : '')
  }
  const { type, rest } = detectEntityType(body)
  return { entityType: type, target: rest, display, heading }
}
