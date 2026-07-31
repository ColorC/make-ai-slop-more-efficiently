import React from 'react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { Gamepad2 } from 'lucide-react'
import WebReviewPanel, { type WebReviewTarget } from './WebReviewPanel'
import WebPagePanel from './WebPagePanel'
import { decodeWebPageId, webTabTitle } from '../../lib/webTabHost'

export interface WebReviewEntity extends Entity {
  type: 'web_review'
}

// 内置网页审阅目标。url 是 dashboard 同源路径, 由 vite 代理转发到各自的开发服务
// (见 vite.config.ts 的 /walker-game 代理 + 游戏侧 `npm run dev:dashboard`)。
// 同源是圈选元素/快照能读 iframe 内容的前提。
export const WEB_REVIEW_TARGETS: Record<string, WebReviewTarget> = {
  'walker-game': { title: '行者无乡（walker-game）', url: '/walker-game/', route: '/' },
  'vilo-os': { title: 'Vilo OS on Web', url: '/vilo-os/', route: '/' },
  'vilo-demo': {
    title: 'Vilo · 镜窗初开 Demo',
    // demo 在 webworks 根下的 /apps/tabletop-simulator/，且引擎走相对 ../../packages，
    // 所以同源代理要指到这个真实子路径(不是 /vilo-demo/ 根，那是 webworks 目录列表)。
    url: '/vilo-demo/apps/tabletop-simulator/?scenario=vilo-7plus3-demo',
    route: '/apps/tabletop-simulator/?scenario=vilo-7plus3-demo',
  },
}

function entityFor(id: string): WebReviewEntity {
  const webPageUrl = decodeWebPageId(id)
  if (webPageUrl) {
    return {
      type: 'web_review',
      id,
      title: webTabTitle(webPageUrl),
      tags: ['web'],
      meta: { url: webPageUrl, webPage: true },
    }
  }
  const t = WEB_REVIEW_TARGETS[id]
  if (!t) throw new Error(`web_review: unknown target ${id}`)
  return { type: 'web_review', id, title: t.title, tags: ['review', 'web'], meta: { url: t.url, route: t.route } }
}

const resolver: EntityResolver<WebReviewEntity> = {
  type: 'web_review',
  async fetch(id) {
    return entityFor(id)
  },
  async list() {
    return Object.keys(WEB_REVIEW_TARGETS).map(entityFor)
  },
}

const Editor: React.FC<{ entity: WebReviewEntity }> = ({ entity }) => {
  const target: WebReviewTarget =
    WEB_REVIEW_TARGETS[entity.id] ?? {
      title: entity.title,
      url: String(entity.meta?.url ?? ''),
      route: entity.meta?.route as string | undefined,
    }
  if (entity.meta?.webPage === true) {
    return <WebPagePanel target={{ title: target.title, url: target.url }} />
  }
  return <WebReviewPanel target={target} />
}

export const webReviewRegistration: EntityRegistration<WebReviewEntity> = {
  resolver,
  renderer: { type: 'web_review', Editor },
  label: '网页审阅',
  icon: <Gamepad2 size={14} />,
}
