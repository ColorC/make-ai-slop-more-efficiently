import { lazy } from 'react'
import { workerResolver, type WorkerEntity } from './resolver'
import WorkerSidebar from './WorkerSidebar'
import type { EntityRegistration } from '../registry'

// 懒加载: 打开 worker tab 才下载对应 Editor chunk。
const Editor = lazy(() => import('./Editor'))

export const workerRegistration: EntityRegistration<WorkerEntity> = {
  resolver: workerResolver,
  renderer: {
    type: 'worker',
    Editor,
    SidebarView: WorkerSidebar,
    facets: [
      { key: 'design', label: '设计' },
      { key: 'live', label: '运行' },
      { key: 'history', label: '历史' },
    ],
    defaultFacet: 'design',
  },
  label: '工作节点',
  icon: '⚙',
}
