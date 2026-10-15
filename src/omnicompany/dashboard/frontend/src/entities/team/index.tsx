// team 实体注册壳(2026-08-06 瘦身)。
//
// 本文件曾是 1.28 万行 / 620KB 的单文件, 类型 / 取数 / 呈现 / 注册四层揉在一起;
// 2026-07 为了首屏拆包把整份复制成 TeamEditor.tsx, 但没接线 —— 那份副本零 import,
// 又和本体各自演进了 18 天, 结果是"改哪个都对不上", 全站 742 处旧圆角有 543 处堆在这两份里。
// 现在按四层真正拆开, 只留注册:
//   · teamTypes.ts   —— 纯类型(76 个), 零运行时
//   · teamApi.ts     —— 取数 + EntityResolver + 列表缓存
//   · TeamEditor.tsx —— 呈现层, 由本文件 React.lazy 懒加载(团队图谱不再进首屏包)
//   · index.tsx      —— 就是这里: 注册 + 侧栏 + 看板
import React from 'react'
import type { Entity } from '../types'
import type { EntityRegistration } from '../registry'
import { CodeFileSidebar } from './sidebar'
import type { TeamEntity } from './teamTypes'
import { fetchList, resolver } from './teamApi'

export type { TeamEntity } from './teamTypes'
export { invalidateTeamCache } from './teamApi'

// 呈现层带着 reactflow + dagre + monaco(约 494KB), 是全站最重的一块; 懒加载后它退出首屏包,
// 切到 team 页签才下载。Suspense 边界由 EditorArea 统一提供(照 TeamRecentBoard 的既有做法),
// 这里不再自己包一层 —— 多包一层只会吞掉 renderer 契约里的 facet 透传。
const TeamEditor = React.lazy(() => import('./TeamEditor'))

export const teamRegistration: EntityRegistration<TeamEntity> = {
  resolver,
  renderer: {
    type: 'team',
    Editor: TeamEditor,
    SidebarView: (props) => <CodeFileSidebar entityType="team" fetchList={fetchList} {...props} />,
  },
  label: '团队',
  icon: 'T',
}

// ── 最近 team 看板(单例固定页签, 照 projectBoardRegistration 模式; 把被赶进角落的 team 设施拉回可达) ──
const TeamRecentBoard = React.lazy(() => import('./TeamRecentBoard'))
const teamBoardEntity: Entity = { type: 'team_board', id: 'main', title: '管线' }

export const teamBoardRegistration: EntityRegistration = {
  label: '管线 (team)',
  icon: 'layout-grid',
  resolver: {
    type: 'team_board',
    fetch: async () => teamBoardEntity,
    list: async () => [teamBoardEntity],
  },
  renderer: { type: 'team_board', Editor: TeamRecentBoard as any },
}
