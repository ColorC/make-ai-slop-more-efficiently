import React from 'react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import MaterialRegistryPanel from './MaterialRegistryPanel'

export interface MaterialRegistryEntity extends Entity {
  type: 'material_registry'
}

const SINGLE: MaterialRegistryEntity = {
  type: 'material_registry',
  id: 'main',
  title: '任务材料',
  tags: ['boss-sight', 'registry'],
}

const resolver: EntityResolver<MaterialRegistryEntity> = {
  type: 'material_registry',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`material_registry: unknown id ${id}`)
  },
  async list() {
    return [SINGLE]
  },
}

const Editor: React.FC<{ entity: MaterialRegistryEntity }> = () => <MaterialRegistryPanel />

export const materialRegistryRegistration: EntityRegistration<MaterialRegistryEntity> = {
  resolver,
  renderer: { type: 'material_registry', Editor },
  label: '任务材料',
  icon: '◈',
}
