import { lazy } from 'react'
import { PanelRight } from 'lucide-react'
import { ccApi } from '../../api/ccClient'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'

export interface CcCompanionEntity extends Entity {
  type: 'cc_companion'
}

const Editor = lazy(() => import('./Editor'))

const resolver: EntityResolver<CcCompanionEntity> = {
  type: 'cc_companion',
  async fetch(id) {
    const sessions = await ccApi.list().catch(() => [])
    const session = sessions.find((item) => item.id === id)
    const name = session?.display_title || session?.provider_title || id.slice(0, 12)
    return {
      type: 'cc_companion',
      id,
      title: `伴随 · ${name}`,
      tags: [session?.alive ? 'alive' : session?.status || 'recoverable'],
    }
  },
  async list() {
    const sessions = await ccApi.list().catch(() => [])
    return sessions.map((session) => ({
      type: 'cc_companion' as const,
      id: session.id,
      title: `伴随 · ${session.display_title || session.provider_title || session.id.slice(0, 12)}`,
      tags: [session.alive ? 'alive' : session.status || 'recoverable'],
    }))
  },
}

export const ccCompanionRegistration: EntityRegistration<CcCompanionEntity> = {
  resolver,
  renderer: { type: 'cc_companion', Editor },
  label: 'CLI 伴随视图',
  icon: <PanelRight size={14} />,
}
