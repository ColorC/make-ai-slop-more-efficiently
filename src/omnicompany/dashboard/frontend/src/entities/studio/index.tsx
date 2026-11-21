import React from 'react'
import { Building2 } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { usePanelVisible } from '../../shell/panelRuntime'

// 胡闹游戏 Studio 的单例整页页签(id 固定 main): 左栏群聊和会话列表(view=chat), 右边会话区直接挂会话终端。
// 入口: 点会话侧栏的 Studio 页签(侧栏那页只放 3D 办公楼 view=scene)。这个页签在前台时
// 主区域侧栏照样挂着、停在 Studio 页(见 EditorArea 的 SharedEditorSidecar)。
// 页面本体放在仪表盘外(/api/embed/studio)。
const StudioView = React.lazy(() => import('./StudioView'))

export const STUDIO_TAB_TITLE = 'Studio'

export interface StudioEntity extends Entity {
  type: 'studio'
}

const SINGLE: StudioEntity = {
  type: 'studio',
  id: 'main',
  title: STUDIO_TAB_TITLE,
  tags: ['hunao-games'],
}

const resolver: EntityResolver<StudioEntity> = {
  type: 'studio',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`studio: unknown id ${id}`)
  },
  async list() {
    return [SINGLE]
  },
}

// 页签切走就卸载 iframe: 任何时候最多只留可见的那一个 Studio 场景(WebGL 较重)。
const Editor: React.FC<{ entity: StudioEntity }> = () => {
  const visible = usePanelVisible()
  if (!visible) return null
  return (
    <React.Suspense fallback={<div style={{ padding: 16 }}>正在打开 Studio…</div>}>
      <StudioView />
    </React.Suspense>
  )
}

export const studioRegistration: EntityRegistration<StudioEntity> = {
  resolver,
  renderer: { type: 'studio', Editor },
  label: 'Studio',
  icon: <Building2 size={14} />,
}
