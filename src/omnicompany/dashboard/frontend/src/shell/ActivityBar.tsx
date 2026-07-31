// ⚠️ 已退役(DECOMMISSIONED)— 保留文件不删。
// v2-11 起默认入口为 CockpitShell, App.tsx 不再挂载 ActivityBar/Sidebar(旧 6 模块导航)。
// 其能力已迁入驾驶舱: 总控/审阅/材料/观测/设置=工作脊柱; 知识库(note/graph)/项目(plan)/Agent(worker/team/
// cc_session)=全局搜索 + open_ref 按需打开(看什么用什么打开)。
// 处置依据: docs/plans/dashboard/[2026-06-03]界面迁移与报废/plan.md(§5 先显式禁用·保留文件 / §9 Phase 4 现状勘定 / P2)。
// 当前唯一引用: Sidebar.tsx 复用此处 ModuleKey 类型(同属旧壳)。确认长期无引用后, 才在独立提交里删。
import React from 'react'

export type ModuleKey = 'controller' | 'kb' | 'pm' | 'agent' | 'system' | 'settings'

export const MODULES: { key: ModuleKey; label: string; icon: string }[] = [
  { key: 'controller', label: '总控', icon: '◎' },
  { key: 'kb', label: '知识库', icon: '📚' },
  { key: 'pm', label: '项目', icon: '📋' },
  { key: 'agent', label: 'Agent 会话', icon: '🤖' },
  { key: 'system', label: '系统', icon: '🔧' },
  { key: 'settings', label: '设置', icon: '⚙️' },
]

interface Props {
  active: ModuleKey
  onChange: (k: ModuleKey) => void
}

const S: Record<string, any> = {
  bar: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    width: 44, background: 'var(--fp-bg)', borderRight: '1px solid #222',
    padding: '8px 0', flexShrink: 0,
  },
  btn: (active: boolean): React.CSSProperties => ({
    width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'transparent', color: active ? '#90caf9' : '#666',
    border: 'none', borderLeft: active ? '2px solid #90caf9' : '2px solid transparent',
    cursor: 'pointer', fontSize: 18,
  }),
}

export default function ActivityBar({ active, onChange }: Props) {
  return (
    <div style={S.bar}>
      {MODULES.map((m) => (
        <button
          key={m.key}
          title={m.label}
          style={S.btn(active === m.key)}
          onClick={() => onChange(m.key)}
        >
          {m.icon}
        </button>
      ))}
    </div>
  )
}
