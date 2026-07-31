// project 实体 — 项目工作板(首页)与项目详情页签。
// 数据源 /api/projects (注册表 + PROJECT_INDEX.md frontmatter 浮出), 与 omni project CLI 同源。
// 注册进 entity registry 后, 项目自动出现在全局搜索/命令面板(搜 name/id/tags)。

import React from 'react'
import type { Entity } from '../types'
import type { EntityRegistration } from '../registry'
import { projectsApi, type ProjectItem } from '../../api/projectsClient'

export interface ProjectEntity extends Entity {
  type: 'project'
  meta: { project: ProjectItem }
}

let _cache: { ts: number; items: ProjectEntity[] } | null = null
const TTL = 15_000

async function fetchList(): Promise<ProjectEntity[]> {
  if (_cache && Date.now() - _cache.ts < TTL) return _cache.items
  const b = await projectsApi.list()
  const items = b.projects.map((p): ProjectEntity => ({
    type: 'project',
    id: p.id,
    title: p.name || p.id,
    tags: [p.group, ...(p.tags || [])],
    meta: { project: p },
  }))
  _cache = { ts: Date.now(), items }
  return items
}

const ProjectDetail = React.lazy(() => import('./ProjectDetail'))

export const projectRegistration: EntityRegistration<ProjectEntity> = {
  label: '项目',
  icon: 'folder-kanban',
  resolver: {
    type: 'project',
    fetch: async (id: string) => {
      const items = await fetchList()
      const found = items.find((e) => e.id === id)
      if (!found) throw new Error(`未注册的项目: ${id}`)
      return found
    },
    list: fetchList,
    search: async (q: string) => {
      const items = await fetchList()
      const s = q.trim().toLowerCase()
      if (!s) return items
      return items.filter((e) => `${e.id} ${e.title} ${(e.tags || []).join(' ')}`.toLowerCase().includes(s))
    },
  },
  renderer: {
    type: 'project',
    Editor: ProjectDetail as any,
  },
}

export { default as ProjectBoard } from './ProjectBoard'

// ── 项目工作板(首页) — 单例固定页签, 与总控并列且默认活跃 ──────────────────
// 用户 /goal (2026-06-12): "首页也是工作板"。panelsStore 把它播种为开机第一页签。

import ProjectBoardComp from './ProjectBoard'

const boardEntity: Entity = { type: 'project_board', id: 'main', title: '项目' }

export const projectBoardRegistration: EntityRegistration = {
  label: '项目工作板',
  icon: 'layout-grid',
  resolver: {
    type: 'project_board',
    fetch: async () => boardEntity,
    list: async () => [boardEntity],
  },
  renderer: {
    type: 'project_board',
    Editor: ProjectBoardComp as any,
  },
}
