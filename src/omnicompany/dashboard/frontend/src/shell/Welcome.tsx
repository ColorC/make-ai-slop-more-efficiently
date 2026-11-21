import React from 'react'
import { usePanels } from '../stores/panelsStore'

// 页签全关时的落脚页。驾驶舱专注 agent 之后这里不再铺项目板(项目是 Workshop 的内容),
// 只给三个去处。
const S: Record<string, React.CSSProperties> = {
  root: {
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    background: 'transparent',
    color: 'var(--fp-text)',
    minWidth: 0,
    minHeight: 0,
  },
}

export default function Welcome() {
  const openTab = usePanels((state) => state.openTab)
  return (
    <div style={S.root} data-testid="boss-sight-home">
      <button type="button" className="v2-btn primary" onClick={() => openTab({ type: 'multiagent', id: 'main' }, '会话')}>会话</button>
      <button type="button" className="v2-btn" onClick={() => openTab({ type: 'controller', id: 'main' }, 'Agent')}>Agent</button>
      <button type="button" className="v2-btn" onClick={() => openTab({ type: 'workshop', id: 'projects' }, '项目')}>项目</button>
    </div>
  )
}
