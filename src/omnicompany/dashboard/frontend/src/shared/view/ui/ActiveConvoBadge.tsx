// 可复用「活跃对话」徽章 — 挂在计划/项目/任务卡片与列表行上, 显示"💬 N"(N=在跑对话数),
// hover 列出会话名单(标题优先, 缺失退回会话 id 尾段)。数据来自 GET /api/boss-sight/active-bindings
// (bossSightApi.activeBindings), 各页按各自 id 从 by_plan/by_project/by_task 里查桶后传入本组件,
// 本组件不自己取数(避免每张卡片各发一次请求)。见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.5)。
import React from 'react'
import type { BossSightBindingSession } from '../../../api/bossSightClient'

function sessionLabel(s: BossSightBindingSession): string {
  const name = s.title || s.name || (s.session_id ? s.session_id.slice(0, 8) : s.key)
  return `${s.running ? '●' : '○'} ${name}`
}

export interface ActiveConvoBadgeProps {
  /** 在跑对话数(running=true)。<=0 时本组件不渲染(无活跃对话不占版面)。 */
  active: number
  /** 绑到该目标的全部对话数(含已停), 有值且大于 active 时显示 "N/total"。 */
  total?: number
  /** 悬浮清单: 会话名 + 在跑/已停。省略则退回"N 个活跃对话在推进"。 */
  sessions?: BossSightBindingSession[]
  size?: 'sm' | 'md'
  /** 附加样式(卡片按需微调外边距等)。 */
  style?: React.CSSProperties
  testid?: string
}

export default function ActiveConvoBadge({
  active, total, sessions, size = 'sm', style, testid = 'active-convo-badge',
}: ActiveConvoBadgeProps) {
  if (!active || active <= 0) return null
  const tip = sessions && sessions.length > 0
    ? sessions.slice(0, 12).map(sessionLabel).join('\n') + (sessions.length > 12 ? `\n… 共 ${sessions.length} 条` : '')
    : `${active} 个活跃对话在推进`
  const small = size === 'sm'
  return (
    <span
      data-testid={testid}
      title={tip}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3, flexShrink: 0,
        fontSize: small ? 12 : 13, fontWeight: 600, color: 'var(--fp-ok)',
        background: 'color-mix(in srgb, var(--fp-ok) 14%, transparent)',
        borderRadius: 999, padding: small ? '1px 8px' : '2px 9px',
        ...style,
      }}
    >
      💬 {active}{total != null && total > active ? `/${total}` : ''}
    </span>
  )
}
