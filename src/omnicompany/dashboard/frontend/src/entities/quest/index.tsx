// quest_board 实体 — 任务窗口。M4(2026-08-13)退役: 任务展示已迁移到画布计划泳道
// (omni canvas add-plan-lane)。注册保留+退役壳, 旧入口/书签不生 404; QuestBoard 组件与
// questsClient 保留(:8230 终末归档前仍是数据兜底)。

import type { Entity } from '../types'
import type { EntityRegistration } from '../registry'
import { lazy } from 'react'
import { RetiredSurface } from '../retired'

const QuestBoardComp = lazy(() => import('./QuestBoard'))
void QuestBoardComp // 实现保留; 渲染壳已换退役页

const questEntity: Entity = { type: 'quest_board', id: 'main', title: '任务窗口' }

const RetiredQuestBoard: React.FC = () => (
  <RetiredSurface
    title="任务窗口已迁移到计划泳道"
    lines={[
      '任务真源已切到 plan-groups；把任务组铺进任意会话札记画布即可查看：',
      'omni canvas add-plan-lane --canvas <画布key> --group <组id>',
      'QuestBoard 组件与 questsClient 保留未删，:8230 服务终末归档前仍是数据兜底。',
    ]}
    link={{ href: '/api/embed/notes-web/', label: '打开会话札记' }}
  />
)

export const questBoardRegistration: EntityRegistration = {
  label: '任务窗口',
  icon: 'scroll-text',
  resolver: {
    type: 'quest_board',
    fetch: async () => questEntity,
    list: async () => [questEntity],
  },
  renderer: {
    type: 'quest_board',
    Editor: RetiredQuestBoard as any,
  },
}

export { default as QuestBoard } from './QuestBoard'
