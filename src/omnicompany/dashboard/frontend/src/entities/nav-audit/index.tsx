import { lazy } from 'react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'

// 可达性审计:列出所有功能(实体类型)能不能从主页到达、怎么到达、能不能返回主页,找"孤岛"。
export interface NavAuditEntity extends Entity { type: 'nav_audit' }

const BOARD: NavAuditEntity = { type: 'nav_audit', id: 'main', title: '可达性审计', icon: '🧭' }

const resolver: EntityResolver<NavAuditEntity> = {
  type: 'nav_audit',
  async fetch() { return BOARD },
  async list() { return [BOARD] },
}

const Editor = lazy(() => import('./Audit'))

export const navAuditRegistration: EntityRegistration<NavAuditEntity> = {
  resolver,
  renderer: { type: 'nav_audit', Editor },
  label: '可达性审计',
  icon: '🧭',
}
