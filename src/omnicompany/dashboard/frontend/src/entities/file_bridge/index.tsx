import React from 'react'
import { HardDrive } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'

const FileBridgeView = React.lazy(() => import('./FileBridgeView'))

export interface FileBridgeEntity extends Entity {
  type: 'file_bridge'
}

const SINGLE: FileBridgeEntity = {
  type: 'file_bridge',
  id: 'main',
  title: 'Agent 暂存区',
  tags: ['upload', 'read-only'],
}

const resolver: EntityResolver<FileBridgeEntity> = {
  type: 'file_bridge',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`file_bridge: unknown id ${id}`)
  },
  async list() {
    return [SINGLE]
  },
}

const Editor: React.FC<{ entity: FileBridgeEntity }> = () => (
  <React.Suspense fallback={<div className="fb-loading">正在载入 Agent 暂存区…</div>}>
    <FileBridgeView />
  </React.Suspense>
)

export const fileBridgeRegistration: EntityRegistration<FileBridgeEntity> = {
  resolver,
  renderer: { type: 'file_bridge', Editor },
  label: 'Agent 暂存区',
  icon: <HardDrive size={14} />,
}
