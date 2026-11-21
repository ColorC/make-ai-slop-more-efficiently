// 通用面板 —— 账页列表的一节 与 看板的一条泳道是同一个组件，只有 layout 不同。
//
// 旧版把「看板=按状态、列表=按项目」写死成两套渲染代码，于是面板不通用、分组维度
// 也换不了。这里对齐成熟看板产品的正交两轴：分组维度（状态/项目/优先级/截止）×
// 布局（账页/看板），面板本身只认 Panel 这一个契约。
//
// 面板一律带：组名 · 未完成计数 · `+` 就地新建 · 折叠 · 拖放接收区。
//
// 2026-09-05：面板本身就是一块板（task.css「面板材质」）。组头那道向右延伸的引线
// 随之撤掉 —— 它是一根压在格纸上的裸横线，正是用户要求去掉的东西；分节的活由板
// 自己的边界担着，组头只剩「名 · 计数 · +」。

import React, { useRef } from 'react'
import { ChevronDown, Plus } from 'lucide-react'
import { DimText } from '../../components/Segmented'
import { inertWhen } from '../../lib/motion'
import { TaskComposer, type ComposerInput } from './TaskComposer'
import { TaskItem, type TaskItemProps } from './TaskItem'
import { type BusTask, type DropPlacement, type GroupSeed, type Panel, type SortKey, buildTree } from './taskBus'

type ItemHooks = Pick<TaskItemProps, 'onOpen' | 'onToggleDone' | 'onHover' | 'onHoverEnd' | 'onMenu'>

export interface TaskGroupProps extends ItemHooks {
  panel: Panel
  layout: 'list' | 'board'
  sort: SortKey
  all: BusTask[]
  projects: { id: string; label: string }[]
  showProject: boolean
  collapsed: boolean
  onToggleCollapse: () => void
  selectedId: string | null
  focusId: string | null
  rowError: Record<string, string>
  menuTaskId: string | null
  busy: boolean

  composing: boolean
  /** 右键「新建子任务」时用它盖掉面板 seed（带 parentTaskId），复用同一个 composer。 */
  composerSeed: GroupSeed | null
  onOpenComposer: () => void
  onCloseComposer: () => void
  onCreate: (input: ComposerInput) => Promise<boolean>

  dragging: string | null
  dropTarget: string | null
  onDragStart: (task: BusTask) => void
  onDragEnd: () => void
  onDragOverPanel: (panel: Panel) => void
  onDropPanel: (panel: Panel) => void
  onDragOverItem: (task: BusTask, panel: Panel) => void
  onDropItem: (task: BusTask, panel: Panel, placement: DropPlacement) => void
  onPointerDragMove?: (task: BusTask, event: React.PointerEvent<HTMLElement>) => void
  onPointerDrop?: (task: BusTask, event: React.PointerEvent<HTMLElement>) => void

  /** 按项目分组时，面板本身也可拖拽，顺序只保存在本地视图偏好里。 */
  panelDraggable?: boolean
  panelDragKey?: string | null
  panelDropTarget?: string | null
  onPanelDragStart?: (panel: Panel) => void
  onPanelDragEnd?: () => void
  onPanelDragOver?: (panel: Panel) => void
  onPanelDrop?: (panel: Panel) => void
  onPanelPointerDragMove?: (panel: Panel, event: React.PointerEvent<HTMLElement>) => void
  onPanelPointerDrop?: (panel: Panel, event: React.PointerEvent<HTMLElement>) => void
}

export function TaskGroup(props: TaskGroupProps) {
  const { panel, layout, all, collapsed, dragging, dropTarget } = props
  // 账页布局要父子缩进；看板一列里再缩进只会更乱，扁平铺。
  const nodes = layout === 'list'
    ? buildTree(panel.tasks, props.sort)
    : panel.tasks.map((task) => ({ task, depth: 0 }))

  const active = dragging !== null
  const isTarget = dropTarget === panel.key
  const rejecting = active && isTarget && !panel.droppable
  const panelDragging = props.panelDragKey !== null && props.panelDragKey !== undefined
  const panelTarget = props.panelDropTarget === panel.key
  const panelTouchDragRef = useRef<{
    pointerId: number
    element: HTMLElement
    startX: number
    startY: number
    dragging: boolean
    timer: number | null
  } | null>(null)
  const suppressPanelClickRef = useRef(false)

  const clearPanelTouchDrag = () => {
    const state = panelTouchDragRef.current
    if (!state) return
    if (state.timer !== null) window.clearTimeout(state.timer)
    state.element.draggable = Boolean(props.panelDraggable)
    panelTouchDragRef.current = null
  }

  const finishPanelTouchDrag = (event: React.PointerEvent<HTMLElement>, cancelled: boolean) => {
    const state = panelTouchDragRef.current
    if (!state || event.pointerId !== state.pointerId) return
    if (state.timer !== null) window.clearTimeout(state.timer)
    panelTouchDragRef.current = null
    state.element.draggable = Boolean(props.panelDraggable)
    try { state.element.releasePointerCapture?.(event.pointerId) } catch { /* old WebView */ }
    if (!state.dragging) return
    event.preventDefault()
    if (cancelled) props.onPanelDragEnd?.()
    else props.onPanelPointerDrop?.(panel, event)
  }

  const itemHooks: ItemHooks = {
    onOpen: props.onOpen,
    onToggleDone: props.onToggleDone,
    onHover: props.onHover,
    onHoverEnd: props.onHoverEnd,
    onMenu: props.onMenu,
  }

  return (
    <section
      className="tk-group"
      data-layout={layout}
      data-testid={`tk-group-${panel.key}`}
      data-drop={isTarget ? (panel.droppable ? 'ok' : 'no') : undefined}
      data-panel-drop={panelTarget ? '1' : undefined}
      data-dragging={active ? '1' : undefined}
      onDragOver={(e) => {
        if (panelDragging) {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'move'
          props.onPanelDragOver?.(panel)
          return
        }
        if (!active) return
        if ((e.target as HTMLElement).closest('[data-testid^="tk-item-"]')) return
        e.preventDefault()
        e.dataTransfer.dropEffect = panel.droppable ? 'move' : 'none'
        props.onDragOverPanel(panel)
      }}
      onDrop={(e) => {
        if (panelDragging) {
          e.preventDefault()
          e.stopPropagation()
          props.onPanelDrop?.(panel)
          return
        }
        if (!active) return
        e.preventDefault()
        props.onDropPanel(panel)
      }}
    >
      <header
        className="tk-ghead"
        draggable={Boolean(props.panelDraggable)}
        data-panel-drag={props.panelDraggable ? '1' : undefined}
        onPointerDown={(e) => {
          if (!props.panelDraggable || e.pointerType === 'mouse' || e.button > 0) return
          const target = e.target
          if (target instanceof Element && target.closest('.tk-add')) return
          const element = e.currentTarget
          element.draggable = false
          const state = {
            pointerId: e.pointerId,
            element,
            startX: e.clientX,
            startY: e.clientY,
            dragging: false,
            timer: null as number | null,
          }
          panelTouchDragRef.current = state
          state.timer = window.setTimeout(() => {
            if (panelTouchDragRef.current !== state) return
            state.dragging = true
            suppressPanelClickRef.current = true
            try { element.setPointerCapture?.(e.pointerId) } catch { /* old WebView */ }
            props.onPanelDragStart?.(panel)
          }, 120)
        }}
        onPointerMove={(e) => {
          const state = panelTouchDragRef.current
          if (!state || e.pointerId !== state.pointerId) return
          if (!state.dragging) {
            const moved = Math.hypot(e.clientX - state.startX, e.clientY - state.startY)
            if (moved < 8) return
            clearPanelTouchDrag()
            return
          }
          e.preventDefault()
          props.onPanelPointerDragMove?.(panel, e)
        }}
        onPointerUp={(e) => finishPanelTouchDrag(e, false)}
        onPointerCancel={(e) => finishPanelTouchDrag(e, true)}
        onClickCapture={(e) => {
          if (!suppressPanelClickRef.current) return
          e.preventDefault()
          e.stopPropagation()
          suppressPanelClickRef.current = false
        }}
        onDragStart={(e) => {
          if (!props.panelDraggable) return
          if (panelTouchDragRef.current) { e.preventDefault(); return }
          e.dataTransfer.setData('text/plain', `panel:${panel.key}`)
          e.dataTransfer.effectAllowed = 'move'
          props.onPanelDragStart?.(panel)
        }}
        onDragEnd={() => props.onPanelDragEnd?.()}
      >
        <button
          type="button"
          className="tk-ghead-hit"
          aria-expanded={!collapsed}
          onClick={props.onToggleCollapse}
        >
          <ChevronDown size={13} className="chev" aria-hidden />
          <span className="gh-name">{panel.title}</span>
          <DimText>{panel.open}</DimText>
        </button>
        {panel.addable !== false && (
        <button
          type="button"
          className="v2-iconbtn tk-add"
          title={`在「${panel.title}」里新建（快捷键 n）`}
          aria-label={`在 ${panel.title} 里新建`}
          data-testid={`tk-add-${panel.key}`}
          onClick={props.onOpenComposer}
        >
          <Plus size={14} />
        </button>
        )}
      </header>

      {rejecting && panel.readonlyReason && (
        <p className="tk-drop-reason" role="status">{panel.readonlyReason}</p>
      )}

      <div className="mo-collapse" data-collapsed={collapsed ? 'true' : undefined} {...inertWhen(collapsed)}>
        <div className="tk-group-body">
          {props.composing && panel.addable !== false && (
            <TaskComposer
              seed={props.composerSeed ?? panel.seed}
              projects={props.projects}
              busy={props.busy}
              onCreate={props.onCreate}
              onClose={props.onCloseComposer}
            />
          )}

          <div className="tk-items">
            {nodes.map((node) => (
              <TaskItem
                key={node.task.id}
                task={node.task}
                all={all}
                variant={layout === 'board' ? 'card' : 'row'}
                depth={node.depth}
                showProject={props.showProject}
                selected={props.selectedId === node.task.id}
                focused={props.focusId === node.task.id}
                error={props.rowError[node.task.id] || null}
                menuOpen={props.menuTaskId === node.task.id}
                dragging={dragging === node.task.id}
                dropTarget={props.dropTarget === `item:${node.task.id}`}
                onDragStart={props.onDragStart}
                onDragEnd={props.onDragEnd}
                onDragOverItem={(task, e) => {
                  if (!active || dragging === task.id) return
                  props.onDragOverItem(task, panel)
                  // Placement is computed again on drop from the final pointer position.
                  void e
                }}
                onPointerDragMove={props.onPointerDragMove}
                onPointerDrop={props.onPointerDrop}
                onDropItem={(task, e) => {
                  if (!active || dragging === task.id) return
                  const rect = e.currentTarget.getBoundingClientRect()
                  const placement: DropPlacement = e.clientY >= rect.top + rect.height / 2 ? 'after' : 'before'
                  props.onDropItem(task, panel, placement)
                }}
                {...itemHooks}
              />
            ))}
          </div>

          {nodes.length === 0 && !props.composing && (
            <button
              type="button"
              className="tk-group-empty"
              disabled={panel.addable === false && !active}
              onClick={() => { if (panel.addable !== false) props.onOpenComposer() }}
            >
              {active ? '拖到这里' : (panel.addable === false ? '空' : '＋ 记一条')}
            </button>
          )}
        </div>
      </div>
    </section>
  )
}
