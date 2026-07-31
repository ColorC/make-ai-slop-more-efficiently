import type { OpenedTab } from '../stores/panelsStore'
import type { EntityRef } from '../entities/types'

export interface SerializedState {
  tabs: { type: string; id: string; facet?: string; title: string }[]
  active: string | null
}

export function serialize(tabs: OpenedTab[], activeId: string | null): string {
  const serializableTabs = tabs.filter((t) => !t.pinned)
  const serializableActive = serializableTabs.some((t) => t.id === activeId) ? activeId : null
  const obj: SerializedState = {
    tabs: serializableTabs.map((t) => ({ type: t.ref.type, id: t.ref.id, facet: t.facet, title: t.title })),
    active: serializableActive,
  }
  return btoa(unescape(encodeURIComponent(JSON.stringify(obj))))
}

export function deserialize(s: string): { tabs: OpenedTab[]; active: string | null } | null {
  try {
    const obj: SerializedState = JSON.parse(decodeURIComponent(escape(atob(s))))
    const tabs: OpenedTab[] = obj.tabs.map((t) => ({
      id: t.facet ? `${t.type}:${t.id}#${t.facet}` : `${t.type}:${t.id}`,
      ref: { type: t.type as EntityRef['type'], id: t.id },
      facet: t.facet,
      title: t.title,
    }))
    return { tabs, active: obj.active }
  } catch {
    return null
  }
}
