// 消息队列 —— Todo 页最上面那一段。
//
// 用户 2026-09-04：
//   ·「不要一大堆按钮…大部分操作折叠到鼠标悬浮才显示的更多或者说右键菜单里面」
//     → 一行只有一件事：**点它就去处理**；别的全在悬浮出来的 ⋯ 或右键里。
//   ·「消息不要聚合，就是按照各自优先级排序」
//     → 平铺一条队列，从上往下清。聚合是待办段的事（那里项目是归属维度）；
//       消息是待处理流，分了组反而看不出先做哪条。

import React from 'react'
import { MoreHorizontal } from 'lucide-react'
import type { MsgRow } from './messages'
import { TierMark } from './TaskItem'

export interface TaskMessagesProps {
  rows: MsgRow[]
  /** 点一行 = 去处理：总线消息跳深链，会话消息打开那个会话。 */
  onOpen: (row: MsgRow) => void
  onMenu: (row: MsgRow, at: { x: number; y: number }) => void
  menuRowId: string | null
  /** 往后拖了、时候未到的条数。不占视野，但要让人知道压了多少。 */
  deferred: number
  rowError: Record<string, string>
}

export function TaskMessages(props: TaskMessagesProps) {
  const { rows, deferred } = props

  if (rows.length === 0) {
    // 清空后不留一张“完成”卡片，让下面的任务面板直接顶上来。
    return null
  }

  return (
    <div className="tk-msgs" data-testid="tk-msgs">
      {rows.map((row) => (
        <div
          key={row.id}
          className="tk-msg"
          role="button"
          tabIndex={0}
          data-kind={row.kind}
          data-testid={`tk-msg-${row.id}`}
          title={`${row.projectId} · ${row.title} —— 点击去处理`}
          onClick={() => props.onOpen(row)}
          onKeyDown={(e) => { if (e.key === 'Enter') props.onOpen(row) }}
          onContextMenu={(e) => {
            e.preventDefault()
            props.onMenu(row, { x: e.clientX, y: e.clientY })
          }}
        >
          <span className="tk-msg-src">{row.source}</span>
          <TierMark priority={row.priority} />
          <span className="tk-msg-t">{row.title}</span>
          <span className="tk-msg-p">{row.projectId}</span>
          {props.rowError[row.id] && (
            <span className="tk-echo" data-kind="warn">{props.rowError[row.id]}</span>
          )}
          <button
            type="button"
            className="v2-iconbtn tk-kebab"
            aria-label={`更多动作: ${row.title}`}
            aria-haspopup="menu"
            data-open={props.menuRowId === row.id ? '1' : undefined}
            data-testid={`tk-msg-kebab-${row.id}`}
            onClick={(e) => {
              e.stopPropagation()
              const r = e.currentTarget.getBoundingClientRect()
              props.onMenu(row, { x: r.left, y: r.bottom + 4 })
            }}
          >
            <MoreHorizontal size={14} />
          </button>
        </div>
      ))}
      {deferred > 0 && (
        <div className="tk-msg-deferred bp-dim" title="往后拖了、时候未到的条目">
          <i /><b>压着 {deferred}</b><i />
        </div>
      )}
    </div>
  )
}
