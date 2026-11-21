import React, { Suspense, lazy } from 'react'
import { Bot } from 'lucide-react'
import CronView from './CronView'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import './controller.css'

// 子视图逐个懒加载（2026-07 首屏拆包）：总控注册进入首屏静态图，
// 静态引入这些面板会把它们全部钉进主包；切到对应视图时再下载各自 chunk。
const ThreadMonitorPanel = lazy(() => import('./ThreadMonitorPanel'))
const ControllerChat = lazy(() => import('./ControllerChat'))
import { useControllerView } from './viewStore'

export interface ControllerEntity extends Entity {
  type: 'controller'
}

const SINGLE: ControllerEntity = {
  type: 'controller',
  id: 'main',
  title: 'Agent',
  tags: ['fixed', 'boss-sight'],
}

const resolver: EntityResolver<ControllerEntity> = {
  type: 'controller',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`controller: unknown id ${id}`)
  },
  async list() {
    return [SINGLE]
  },
}

const S: Record<string, React.CSSProperties> = {
  // root 透明，透出 CockpitShell 的统一蓝图背景；不要重新铺实色。
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    minWidth: 0,
    minHeight: 0,
    background: 'transparent',
    color: 'var(--fp-text)',
  },
  chatWrap: { flex: 1, minHeight: 0, minWidth: 0, display: 'flex' },
}

const Editor: React.FC<{ entity: ControllerEntity }> = () => {
  const view = useControllerView((s) => s.view)
  const setView = useControllerView((s) => s.setView)
  // 只留智能这一极的视图; 项目板 / 任务窗口 / 审阅总览 / 最近访问 / 任务材料是内容, 已交给 Workshop。
  const views: Array<{ key: typeof view; label: string; testid: string }> = [
    { key: 'chat', label: 'assistant', testid: 'controller-view-chat' },
    { key: 'sessions', label: 'CLI Hub', testid: 'controller-view-sessions' },
    { key: 'cron', label: '定时任务', testid: 'controller-view-cron' },
  ]

  return (
    <div style={S.root} data-testid="boss-controller-root">
      <div className="ct-bar">
        <span className="v2-seg" role="radiogroup" aria-label="Agent 视图">
          {views.map((item) => (
            <button
              key={item.key}
              type="button"
              role="radio"
              aria-checked={view === item.key}
              className="seg-i"
              data-testid={item.testid}
              onClick={() => setView(item.key)}
            >
              {item.label}
            </button>
          ))}
        </span>
      </div>
      <Suspense fallback={<div style={{ padding: 24, fontSize: 'var(--fp-fs-3)', color: 'var(--fp-text-3)' }}>加载视图…</div>}>
        {view === 'chat' && <div style={S.chatWrap} data-testid="controller-chat"><ControllerChat /></div>}
        {view === 'sessions' && <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }} data-testid="controller-sessions"><ThreadMonitorPanel /></div>}
        {view === 'cron' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-cron"><CronView /></div>}
      </Suspense>
    </div>
  )
}

export const controllerRegistration: EntityRegistration<ControllerEntity> = {
  resolver,
  renderer: { type: 'controller', Editor },
  label: 'Agent',
  icon: <Bot size={14} />,
}
