import React from 'react'
import { create } from 'zustand'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import HomeThreeCards from './HomeThreeCards'
import CronView from './CronView'
import ProjectBoard from '../project/ProjectBoard'
import QuestBoard from '../quest/QuestBoard'
import MultiagentView from '../multiagent/MultiagentView'
import ReviewOverview from '../review/ReviewOverview'
import { ChatuiHandoff } from '../cc_session/ChatuiHandoff'
import { usePanels } from '../../stores/panelsStore'

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

/** 2026-06 重做: 总控 = 对话(人↔AI 主交互)。原内置"项目/对话·计划·审阅/总控对话"三选一 toggle
 *  已删 —— 项目板是独立首页(rail「项目」), 不再在总控里重复。保留此 store 仅为兼容旧引用。 */
export type ControllerView = 'project' | 'home' | 'chat' | 'cron' | 'quest' | 'multiagent' | 'review'
export const useControllerView = create<{ view: ControllerView; setView: (v: ControllerView) => void }>((set) => ({
  view: 'home',
  setView: (view) => set({ view }),
}))

const S: Record<string, React.CSSProperties> = {
  // root 透明 → 吃 CockpitShell 的全局冷渐变（与其余所有面板一致）。铺实色 var(--fp-bg)
  // 会把渐变整面盖死成死黑块——2026-07-02 玻璃感割裂的根因，别改回来。
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    minWidth: 0,
    minHeight: 0,
    background: 'transparent',
    color: 'var(--fp-text)',
  },
  bar: {
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    padding: '3px 8px',
    borderBottom: '1px solid var(--fp-border-subtle)',
  },
  matBtn: {
    border: '1px solid var(--fp-border)',
    background: 'var(--fp-card)',
    color: 'var(--fp-text-2)',
    borderRadius: 4,
    padding: '3px 8px',
    cursor: 'pointer',
    fontSize: 14,
  },
  chatWrap: { flex: 1, minHeight: 0, minWidth: 0, display: 'flex' },
}

/** 总控对话已迁到收编的 chatui(provider=controller)。这里只渲染落点卡片, 点开
 *  带 ?provider=controller 跳 chatui。总控退居二线(用户 2026-06): 不再在驾驶舱内
 *  嵌手搓聊天面板, 统一走 chatui 一套对话底座。压缩/新会话由 chatui 自带能力承接。 */
function ControllerChat() {
  return <ChatuiHandoff provider="controller" />
}

const Editor: React.FC<{ entity: ControllerEntity }> = () => {
  const openTab = usePanels((s) => s.openTab)
  const view = useControllerView((s) => s.view)
  const setView = useControllerView((s) => s.setView)
  const tBtn = (on: boolean): React.CSSProperties => ({
    border: 0, background: on ? 'var(--fp-accent-weak)' : 'transparent', color: on ? 'var(--fp-link)' : '#9aa7b4',
    padding: '3px 11px', cursor: 'pointer', fontSize: 14, fontWeight: on ? 700 : 500,
  })
  return (
    <div style={S.root} data-testid="boss-controller-root">
      <div style={S.bar}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--fp-border)', borderRadius: 5, overflow: 'hidden' }} role="tablist" aria-label="总控视图">
          <button type="button" data-testid="controller-view-home" style={tBtn(view === 'home')} onClick={() => setView('home')}>最近访问</button>
          <button type="button" data-testid="controller-view-project" style={tBtn(view === 'project')} onClick={() => setView('project')}>项目</button>
          <button type="button" data-testid="controller-view-quest" style={tBtn(view === 'quest')} onClick={() => setView('quest')}>任务窗口</button>
          <button type="button" data-testid="controller-view-multiagent" style={tBtn(view === 'multiagent')} onClick={() => setView('multiagent')}>多Agent</button>
          <button type="button" data-testid="controller-view-chat" style={tBtn(view === 'chat')} onClick={() => setView('chat')}>总控对话</button>
          <button type="button" data-testid="controller-view-review" style={tBtn(view === 'review')} onClick={() => setView('review')}>审阅总览</button>
          <button type="button" data-testid="controller-view-cron" style={tBtn(view === 'cron')} onClick={() => setView('cron')}>定时任务</button>
        </div>
        <button
          type="button"
          data-testid="open-material-registry"
          style={S.matBtn}
          onClick={() => openTab({ type: 'material_registry', id: 'main' }, '任务材料')}
        >
          任务材料
        </button>
      </div>
      {view === 'home' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-home"><HomeThreeCards /></div>}
      {view === 'project' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-project"><ProjectBoard /></div>}
      {view === 'quest' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-quest"><QuestBoard /></div>}
      {/* 点列表只看信息不跳转(用户 2026-06-26);cc_session 对外部 agent 会话是坏的 handoff,不接 onPick。 */}
      {view === 'multiagent' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-multiagent"><MultiagentView /></div>}
      {view === 'chat' && <div style={S.chatWrap} data-testid="controller-chat"><ControllerChat /></div>}
      {view === 'review' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-review"><ReviewOverview pollMs={4000} onOpen={(m) => openTab({ type: 'review_material', id: m.id }, m.title)} /></div>}
      {view === 'cron' && <div style={{ flex: 1, minHeight: 0 }} data-testid="controller-cron"><CronView /></div>}
    </div>
  )
}

export const controllerRegistration: EntityRegistration<ControllerEntity> = {
  resolver,
  renderer: { type: 'controller', Editor },
  label: '总控',
  icon: '◎',
}
