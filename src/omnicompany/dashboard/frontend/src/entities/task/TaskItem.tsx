// 一条任务的两种形态: 账页行(list) 与 图纸卡(board)。同一份内容口径, 只换排布。
//
// L1 只给「状态标记 + 标题 + 至多 2 枚元信号」; 更多信息交给 L2 悬停预览与 L3 详情抽屉。
// 状态标记是同色异形的线条件(CSS 画, 不用图标字体), 靠形状读状态, 不靠颜色。

import React, { useRef } from 'react'
import { MessageSquare, MoreHorizontal } from 'lucide-react'
import {
  type BusTask, conversationLinks, dueLabel, dueState, isDone, laneOf, projectKey,
  stateLabel, subtaskProgress, tierLabel, tierOf,
} from './taskBus'

// 触屏拖拽不再等待浏览器自己的长按阈值；短按后即可拖，快速滑动仍留给页面滚动。
const TOUCH_DRAG_DELAY_MS = 120
const TOUCH_DRAG_CANCEL_PX = 8

/** 优先度阶梯记号 —— 一根刻度，不写数字（用户 2026-09-03：优先级不需要显示数字）。
 *
 *  高度即等级，读的是形不是数；名字与判据在 title 里，悬停才要。数字对人没有意义，
 *  「紧急处理单」才有；而排序本身已经把等级说清楚了，行上再重复一遍就是噪声。
 *  它不算 L1 的"元信号 chip"：阶梯是身份的一部分，跟状态标记一样常驻。 */
export function TierMark({ priority }: { priority: number | null | undefined }) {
  if (priority == null) return null
  const tier = tierOf(priority)
  return (
    <span
      className="tk-tier"
      data-tier={priority}
      title={tier ? `${tier.name}${tier.criterion ? ` —— ${tier.criterion}` : ''}` : tierLabel(priority)}
      aria-label={`优先度 ${tierLabel(priority)}`}
      role="img"
    />
  )
}

export function StateMark({ state }: { state: string }) {
  return <span className="tk-mark" data-state={state} aria-label={stateLabel(state)} role="img" />
}

export interface TaskItemProps {
  task: BusTask
  all: BusTask[]
  variant: 'row' | 'card'
  depth?: number
  selected: boolean
  focused: boolean
  /** 已按项目分节时不再重复显示项目标签。 */
  showProject: boolean
  onOpen: (task: BusTask) => void
  onToggleDone: (task: BusTask) => void
  onHover?: (anchor: HTMLElement, task: BusTask) => void
  onHoverEnd?: () => void
  error?: string | null
  /** 拖拽中（本条正被拖）。跨面板拖 = 改分组维度对应的字段，见 taskBus.dropWriteFor。 */
  dragging?: boolean
  onDragStart?: (task: BusTask) => void
  onDragEnd?: () => void
  onPointerDragMove?: (task: BusTask, event: React.PointerEvent<HTMLElement>) => void
  onPointerDrop?: (task: BusTask, event: React.PointerEvent<HTMLElement>) => void
  /** 拖到同组另一条任务时，按目标上/下方写入稳定的 orderKey。 */
  dropTarget?: boolean
  onDragOverItem?: (task: BusTask, event: React.DragEvent<HTMLElement>) => void
  onDropItem?: (task: BusTask, event: React.DragEvent<HTMLElement>) => void
  /** 右键任意位置 / 点行尾的 ⋯ 都开同一份动作菜单（动作要有入口，不能只藏在详情里）。 */
  onMenu?: (task: BusTask, at: { x: number; y: number }) => void
  menuOpen?: boolean
}

export function TaskItem(props: TaskItemProps) {
  const { task, all, variant, depth = 0, selected, focused, showProject, error } = props
  const lane = laneOf(task)
  const due = dueState(task.dueAt)
  const convs = conversationLinks(task).length
  const sub = subtaskProgress(task, all)
  const touchDragRef = useRef<{
    pointerId: number
    element: HTMLElement
    startX: number
    startY: number
    dragging: boolean
    timer: number | null
  } | null>(null)
  const suppressClickRef = useRef(false)

  const clearTouchDrag = (restoreDraggable = true) => {
    const state = touchDragRef.current
    if (!state) return
    if (state.timer !== null) window.clearTimeout(state.timer)
    if (restoreDraggable) state.element.draggable = Boolean(props.onDragStart)
    touchDragRef.current = null
  }

  const finishTouchDrag = (event: React.PointerEvent<HTMLElement>, cancelled: boolean) => {
    const state = touchDragRef.current
    if (!state || event.pointerId !== state.pointerId) return
    if (state.timer !== null) window.clearTimeout(state.timer)
    touchDragRef.current = null
    state.element.draggable = Boolean(props.onDragStart)
    try { state.element.releasePointerCapture?.(event.pointerId) } catch { /* old WebView */ }
    if (!state.dragging) return
    event.preventDefault()
    if (cancelled) props.onDragEnd?.()
    else props.onPointerDrop?.(task, event)
  }

  // 元信号至多 2 枚(spec §3): 截止 → 项目 → 子任务 → 会话。其余交给悬停/抽屉。
  const chips: React.ReactNode[] = []
  if (due === 'overdue' || due === 'soon') {
    chips.push(
      <span key="due" className="tk-chip" data-due={due}>{dueLabel(task.dueAt)}</span>,
    )
  }
  if (chips.length < 2 && showProject && task.projectId) {
    chips.push(<span key="prj" className="tk-chip" data-kind="project">{task.projectId}</span>)
  }
  if (chips.length < 2 && sub) {
    chips.push(
      <span key="sub" className="bp-dim tk-chip-dim" aria-label={`子任务 ${sub.done}/${sub.total}`}>
        <i /><b>{sub.done}/{sub.total}</b><i />
      </span>,
    )
  }
  if (chips.length < 2 && convs > 0) {
    chips.push(
      <span key="conv" className="tk-chip" data-kind="conv">
        <MessageSquare size={11} aria-hidden />{convs}
      </span>,
    )
  }

  const body = (
    <>
      <button
        type="button"
        className="tk-marker-btn"
        title={`${stateLabel(task.stateKey)} · 点击切换完成`}
        aria-label={`切换完成: ${task.title}`}
        onClick={(e) => { e.stopPropagation(); props.onToggleDone(task) }}
      >
        <StateMark state={task.stateKey} />
      </button>
      <TierMark priority={task.priority} />
      <span className="tk-title">{task.title}</span>
      {chips.length > 0 && <span className="tk-meta">{chips}</span>}
      {props.onMenu && (
        <button
          type="button"
          className="v2-iconbtn tk-kebab"
          title="更多动作（也可以右键）"
          aria-label={`更多动作: ${task.title}`}
          aria-haspopup="menu"
          data-open={props.menuOpen ? '1' : undefined}
          data-testid={`tk-kebab-${task.id}`}
          onClick={(e) => {
            e.stopPropagation()
            const r = e.currentTarget.getBoundingClientRect()
            props.onMenu?.(task, { x: r.left, y: r.bottom + 4 })
          }}
        >
          <MoreHorizontal size={14} />
        </button>
      )}
    </>
  )

  const shared = {
    draggable: Boolean(props.onDragStart),
    onDragStart: (e: React.DragEvent<HTMLElement>) => {
      // 触屏使用下面的短长按通道，拦掉浏览器原生的更长拖拽等待。
      if (touchDragRef.current) { e.preventDefault(); return }
      // 必须塞点数据，否则 Firefox 直接不认这次拖拽。
      e.dataTransfer.setData('text/plain', task.id)
      e.dataTransfer.effectAllowed = 'move'
      props.onHoverEnd?.()
      props.onDragStart?.(task)
    },
    onDragEnd: () => props.onDragEnd?.(),
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      if (e.pointerType === 'mouse' || e.button > 0) return
      const target = e.target
      if (target instanceof Element && target.closest('button, a, input, textarea, select')) return
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
      touchDragRef.current = state
      state.timer = window.setTimeout(() => {
        if (touchDragRef.current !== state) return
        state.dragging = true
        suppressClickRef.current = true
        try { element.setPointerCapture?.(e.pointerId) } catch { /* old WebView */ }
        props.onDragStart?.(task)
      }, TOUCH_DRAG_DELAY_MS)
    },
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
      const state = touchDragRef.current
      if (!state || e.pointerId !== state.pointerId) return
      if (!state.dragging) {
        const moved = Math.hypot(e.clientX - state.startX, e.clientY - state.startY)
        if (moved < TOUCH_DRAG_CANCEL_PX) return
        clearTouchDrag()
        return
      }
      e.preventDefault()
      props.onPointerDragMove?.(task, e)
    },
    onPointerUp: (e: React.PointerEvent<HTMLElement>) => finishTouchDrag(e, false),
    onPointerCancel: (e: React.PointerEvent<HTMLElement>) => finishTouchDrag(e, true),
    'data-drag': props.dragging ? '1' : undefined,
    'data-lane': lane,
    'data-state': task.stateKey,
    'data-done': isDone(task) ? '1' : '0',
    'data-selected': selected ? '1' : '0',
    'data-focused': focused ? '1' : '0',
    'data-error': error ? '1' : '0',
    'data-menu': props.menuOpen ? '1' : undefined,
    'data-drop-target': props.dropTarget ? '1' : undefined,
    'data-testid': `tk-item-${task.id}`,
    onContextMenu: (e: React.MouseEvent<HTMLElement>) => {
      if (!props.onMenu) return
      e.preventDefault()
      e.stopPropagation()
      props.onHoverEnd?.()
      props.onMenu(task, { x: e.clientX, y: e.clientY })
    },
    onMouseEnter: (e: React.MouseEvent<HTMLElement>) => props.onHover?.(e.currentTarget, task),
    onMouseLeave: () => props.onHoverEnd?.(),
    onDragOver: (e: React.DragEvent<HTMLElement>) => {
      if (!props.onDragOverItem) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      props.onDragOverItem(task, e)
    },
    onDrop: (e: React.DragEvent<HTMLElement>) => {
      if (!props.onDropItem) return
      e.preventDefault()
      e.stopPropagation()
      props.onDropItem(task, e)
    },
    onClick: (e: React.MouseEvent<HTMLElement>) => {
      if (suppressClickRef.current) {
        e.preventDefault()
        e.stopPropagation()
        suppressClickRef.current = false
        return
      }
      props.onOpen(task)
    },
  }

  if (variant === 'card') {
    return (
      <div className="v2-card tk-card" {...shared}>
        {body}
        {error && <span className="tk-inline-err">{error}</span>}
      </div>
    )
  }
  return (
    <div
      className="tk-row"
      style={{ ['--tk-depth' as string]: String(depth) }}
      {...shared}
    >
      {depth > 0 && <span className="tk-guide" aria-hidden="true" />}
      {body}
      {error && <span className="tk-inline-err">{error}</span>}
    </div>
  )
}

/** L2 悬停预览内容 —— 把 L1 挤掉的信息一次给全, 零点击。 */
export function TaskPreview({ task, all }: { task: BusTask; all: BusTask[] }) {
  const sub = subtaskProgress(task, all)
  const convs = conversationLinks(task)
  return (
    <>
      <div className="tk-pv-t">
        <StateMark state={task.stateKey} />
        <span>{task.title}</span>
      </div>
      {task.description ? <div className="tk-pv-d">{task.description}</div> : null}
      <div className="tk-pv-meta">
        <span>{projectKey(task)}</span>
        <span className="bp-leader" aria-hidden="true" />
        <span>{stateLabel(task.stateKey)}</span>
        {task.dueAt && <span>截止 {dueLabel(task.dueAt)}</span>}
        {task.priority != null && <span>{tierLabel(task.priority)}</span>}
        {sub && <span>子任务 {sub.done}/{sub.total}</span>}
        {convs.length > 0 && <span>会话 {convs.length}</span>}
      </div>
    </>
  )
}
