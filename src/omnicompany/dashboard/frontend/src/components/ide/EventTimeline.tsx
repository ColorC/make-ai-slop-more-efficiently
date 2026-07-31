/**
 * EventTimeline — 水平时间线可视化
 *
 * 每个事件显示为一个着色圆点，悬停显示详情。
 */

import React, { useRef, useEffect } from 'react'
import { useIDEStore } from '../../stores/ideStore'

const typeColors: Record<string, string> = {
  'task.intent': '#42a5f5',
  'task.finish': '#66bb6a',
  'task.error': '#ef5350',
  'agent.llm.response': '#7e57c2',
  'agent.llm.request': '#7e57c2',
  'agent.tool.call': '#ffb74d',
  'agent.tool.result': '#ffa726',
  'agent.think': '#78909c',
  'agent.state.change': '#26c6da',
  'agent_loop.llm_call': '#7e57c2',
  'agent_loop.tool_call': '#ffb74d',
  'agent_loop.tool_result': '#ffa726',
  'agent_loop.compact': '#555',
  'agent_loop.budget': '#ef5350',
  'agent_loop.finish': '#66bb6a',
}

export default function EventTimeline() {
  const { rawEvents } = useIDEStore()
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth
    }
  }, [rawEvents.length])

  if (rawEvents.length === 0) {
    return (
      <div style={{ padding: 8, color: '#444', fontSize: 14 }}>No events yet</div>
    )
  }

  return (
    <div
      ref={scrollRef}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 3,
        padding: '6px 8px',
        overflow: 'auto',
        background: 'var(--fp-bg)',
        height: '100%',
      }}
    >
      {rawEvents.map((ev) => {
        const color = typeColors[ev.event_type] || '#555'
        const shortType = ev.event_type.split('.').pop() || ev.event_type
        return (
          <div
            key={ev.id}
            title={`${ev.event_type}\n${new Date(ev.timestamp).toLocaleTimeString()}\n${ev.payload.tool || ev.payload.instruction || ''}`}
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: color,
              flexShrink: 0,
              cursor: 'pointer',
            }}
          />
        )
      })}
    </div>
  )
}
