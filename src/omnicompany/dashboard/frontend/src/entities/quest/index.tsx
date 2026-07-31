// quest_board 实体 — 任务窗口(驾驶舱主区第 2 个固定页签)。
// 单例固定页签, 排在「项目工作板」之后、「总控」之前。数据 /api/quests, 与项目工作板同源。

import type { Entity } from '../types'
import type { EntityRegistration } from '../registry'
import QuestBoardComp from './QuestBoard'

const questEntity: Entity = { type: 'quest_board', id: 'main', title: '任务窗口' }

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
    Editor: QuestBoardComp as any,
  },
}

export { default as QuestBoard } from './QuestBoard'
