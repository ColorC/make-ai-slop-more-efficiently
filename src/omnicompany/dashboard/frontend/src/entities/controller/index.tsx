import React, { Suspense, lazy } from 'react'
import { MessageSquare, Package } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import './controller.css'

// 子视图逐个懒加载（2026-07 首屏拆包）：总控注册进入首屏静态图，
// 静态引入这些面板会把它们全部钉进主包；切到对应视图时再下载各自 chunk。
const HomeThreeCards = lazy(() => import('./HomeThreeCards'))
const CronView = lazy(() => import('./CronView'))
const ProjectBoard = lazy(() => import('../project/ProjectBoard'))
const QuestBoard = lazy(() => import('../quest/QuestBoard'))
const MultiagentView = lazy(() => import('../multiagent/MultiagentView'))
const ReviewOverview = lazy(() => import('../review/ReviewOverview'))
const ThreadMonitorPanel = lazy(() => import('./ThreadMonitorPanel'))
const ControllerChat = lazy(() => import('./ControllerChat'))
import { usePanels } from '../../stores/panelsStore'
import { useControllerView } from './viewStore'

export interface ControllerEntity extends Entity {
  type: 'controller'
}

const SINGLE: ControllerEntity = {
  type: 'controller',
  id: 'main',
  title: '总控',
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
  const openTab = usePanels((s) => s.openTab)
  const view = useControllerView((s) => s.view)
  const setView = useControllerView((s) => s.setView)
  const views: Array<{ key: typeof view; label: string; testid: string }> = [
    { key: 'chat', label: '总控对话', testid: 'controller-view-chat' },
    { key: 'sessions', label: '会话 / CLI', testid: 'controller-view-sessions' },
    { key: 'home', label: '最近访问', testid: 'controller-view-home' },
    { key: 'project', label: '项目', testid: 'controller-view-project' },
    { key: 'quest', label: '任务窗口', testid: 'controller-view-quest' },
    { key: 'review', label: '审阅总览', testid: 'controller-view-review' },
    { key: 'cron', label: '定时任务', testid: 'controller-view-cron' },
  ]

  return (
    <div style={S.root} data-testid="boss-controller-root">
      <div className="ct-bar">
        <span className="v2-seg" role="radiogroup" aria-label="总控视图">
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
        <button
          type="button"
          data-testid="open-material-registry"
          className="ct-matbtn"
          onClick={() => openTab({ type: 'material_registry', id: 'main' }, '任务材料')}
        >
          <Package size={13} aria-hidden />任务材料
        </button>
      </div>
      <Suspense fallback={<div style={{ padding: 24, fontSize: 'var(--fp-fs-3)', color: 'var(--fp-text-3)' }}>加载视图…</div>}>
        {view === 'home' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-home"><HomeThreeCards /></div>}
        {view === 'project' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-project"><ProjectBoard /></div>}
        {view === 'quest' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-quest"><QuestBoard /></div>}
        {/* Multiagent 已移到一级 rail；保留该视图分支供旧状态/深链兼容。 */}
        {view === 'multiagent' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-multiagent"><MultiagentView /></div>}
        {view === 'chat' && <div style={S.chatWrap} data-testid="controller-chat"><ControllerChat /></div>}
        {view === 'sessions' && <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }} data-testid="controller-sessions"><ThreadMonitorPanel /></div>}
        {view === 'review' && (
          <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-review">
            <ReviewOverview
              pollMs={4000}
              onOpen={(material) => openTab({ type: 'review_material', id: material.id }, material.title)}
              onOpenReview={() => openTab({ type: 'review_queue', id: 'main' }, '审阅')}
            />
          </div>
        )}
        {view === 'cron' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-cron"><CronView /></div>}
      </Suspense>
    </div>
  )
}

export const controllerRegistration: EntityRegistration<ControllerEntity> = {
  resolver,
  renderer: { type: 'controller', Editor },
  label: '总控',
  icon: <MessageSquare size={14} />,
}
