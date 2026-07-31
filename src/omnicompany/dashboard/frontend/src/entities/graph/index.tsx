import { lazy } from 'react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'

export interface GraphEntity extends Entity {
  type: 'graph'
}

const SINGLE: GraphEntity = { type: 'graph', id: 'main', title: 'KB 关系图谱' }

const resolver: EntityResolver<GraphEntity> = {
  type: 'graph',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`graph: only 'main' available`)
  },
  async list() { return [SINGLE] },
}

// 重查看器懒加载: cytoscape 实现在 ./GraphEditor, 切到「关系图谱」tab 才下载对应 chunk。
const Editor = lazy(() => import('./GraphEditor'))

export const graphRegistration: EntityRegistration<GraphEntity> = {
  resolver,
  renderer: { type: 'graph', Editor },
  label: '关系图谱',
  icon: '⊕',
}
