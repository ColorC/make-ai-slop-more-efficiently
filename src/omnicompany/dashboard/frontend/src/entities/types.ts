export type EntityType = 'note' | 'graph' | 'plan' | 'trace' | 'session' | 'cc_session' | 'cc_companion' | 'multiagent' | 'controller' | 'material_registry' | 'review_queue' | 'review_material' | 'worker' | 'material' | 'team' | 'team_board' | 'plan_audit' | 'settings' | 'web_review' | 'project' | 'project_board' | 'quest_board' | 'authored' | 'material_graph' | 'nav_audit' | 'studio_reader' | 'overlay_file' | 'file_bridge'

export interface EntityRef {
  type: EntityType
  id: string
}

export interface Entity extends EntityRef {
  title: string
  icon?: string
  tags?: string[]
  meta?: Record<string, unknown>
}

export interface EntityFacet {
  key: string
  label: string
}

export function uriOf(ref: EntityRef, facet?: string): string {
  const base = `omni://${ref.type}/${encodeURIComponent(ref.id)}`
  return facet ? `${base}?facet=${facet}` : base
}

export function parseUri(uri: string): { ref: EntityRef; facet?: string } | null {
  const m = uri.match(/^omni:\/\/([^/]+)\/([^?]+)(?:\?facet=(.+))?$/)
  if (!m) return null
  return {
    ref: { type: m[1] as EntityType, id: decodeURIComponent(m[2]) },
    facet: m[3],
  }
}
