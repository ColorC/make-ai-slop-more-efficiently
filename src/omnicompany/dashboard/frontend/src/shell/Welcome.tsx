import React from 'react'
import ProjectBoard from '../entities/project/ProjectBoard'

// 首页重置(用户 /goal 2026-06-12): 不再是 agent/plan 列表式控制台, 而是项目工作板 —
// "工作时第一考虑的是我要搞的是什么内容相关的东西"。原控制台(Briefing)仍在底部面板可用。

const S: Record<string, React.CSSProperties> = {
  root: {
    height: '100%',
    background: 'var(--fp-bg)',
    color: 'var(--fp-text)',
    minWidth: 0,
    minHeight: 0,
  },
}

export default function Welcome() {
  return (
    <div style={S.root} data-testid="boss-sight-home">
      <ProjectBoard />
    </div>
  )
}
