import { lazy } from 'react'
import { api } from '../../api/client'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'

// 懒加载: 打开 trace tab 才下载对应 Editor chunk。
const Editor = lazy(() => import('./Editor'))

export interface TraceEntity extends Entity {
  type: 'trace'
  source: string
  status: string
}

const resolver: EntityResolver<TraceEntity> = {
  type: 'trace',
  async fetch(id) {
    const data = await api.traceList({ limit: 200 })
    const found = data.items.find((t) => t.trace_id === id)
    if (found) {
      return { type: 'trace', id, title: found.task_desc || id.slice(0, 24), source: found.source, status: found.status }
    }
    return { type: 'trace', id, title: id.slice(0, 24), source: 'unknown', status: 'finished' }
  },
  async list() {
    const data = await api.traceList({ limit: 100 })
    return data.items.map((t) => ({
      type: 'trace' as const,
      id: t.trace_id,
      title: t.task_desc || t.trace_id.slice(0, 24),
      source: t.source,
      status: t.status,
      tags: [t.domain],
    }))
  },
  async search(q) {
    const data = await api.traceList({ q, limit: 100 })
    return data.items.map((t) => ({
      type: 'trace' as const,
      id: t.trace_id,
      title: t.task_desc || t.trace_id.slice(0, 24),
      source: t.source,
      status: t.status,
      tags: [t.domain],
    }))
  },
}

export const traceRegistration: EntityRegistration<TraceEntity> = {
  resolver,
  renderer: { type: 'trace', Editor },
  label: 'Trace',
  icon: '∿',
}
