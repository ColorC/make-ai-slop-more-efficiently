// 两种布局，一套面板。
//
// 账页（list）= 面板竖着摞，面板内父子缩进；看板（board）= 面板横着排、snap 横滑。
// 分组维度由 TaskPanel 决定（状态/项目/优先级/截止），本文件不认识任何具体维度 ——
// 这正是「面板通用」的意思：换分组不改渲染代码。

import React from 'react'
import { TaskGroup, type TaskGroupProps } from './TaskGroup'
import type { Panel } from './taskBus'

type Shared = Omit<
  TaskGroupProps,
  'panel' | 'layout' | 'collapsed' | 'onToggleCollapse' | 'composing' | 'onOpenComposer' | 'onCloseComposer'
>

export interface TaskViewsProps extends Shared {
  panels: Panel[]
  layout: 'list' | 'board'
  collapsed: Record<string, boolean>
  onToggleCollapse: (key: string) => void
  composingKey: string | null
  onOpenComposer: (key: string) => void
  onCloseComposer: () => void
}

export function TaskViews({
  panels, layout, collapsed, onToggleCollapse, composingKey, onOpenComposer, onCloseComposer, ...rest
}: TaskViewsProps) {
  return (
    <div className={layout === 'board' ? 'tk-lanes' : 'tk-stack'}>
      {panels.map((panel) => (
        <TaskGroup
          key={panel.key}
          panel={panel}
          layout={layout}
          collapsed={Boolean(collapsed[panel.key])}
          onToggleCollapse={() => onToggleCollapse(panel.key)}
          composing={composingKey === panel.key}
          onOpenComposer={() => onOpenComposer(panel.key)}
          onCloseComposer={onCloseComposer}
          {...rest}
        />
      ))}
    </div>
  )
}
