import React from 'react'
import { Activity } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import MultiagentView from './MultiagentView'

export interface MultiagentEntity extends Entity {
  type: 'multiagent'
}

const SINGLE: MultiagentEntity = {
  type: 'multiagent',
  id: 'main',
  title: '活跃会话',
  tags: ['live', 'cli'],
}

const resolver: EntityResolver<MultiagentEntity> = {
  type: 'multiagent',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`multiagent: unknown id ${id}`)
  },
  async list() {
    return [SINGLE]
  },
}

const Editor: React.FC<{ entity: MultiagentEntity }> = () => <MultiagentView />

export const multiagentRegistration: EntityRegistration<MultiagentEntity> = {
  resolver,
  renderer: { type: 'multiagent', Editor },
  label: '活跃会话',
  icon: <Activity size={14} />,
}
