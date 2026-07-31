import React, { useState } from 'react'
import { PanelBottomClose } from 'lucide-react'
import BriefingTab from './bottomTabs/Briefing'
import EventStream from './bottomTabs/EventStream'
import TraceList from './bottomTabs/TraceList'
import SearchTab from './bottomTabs/Search'

const TABS = [
  { key: 'briefing', label: '总报', Component: BriefingTab },
  { key: 'events', label: '事件流', Component: EventStream },
  { key: 'traces', label: 'Trace 列表', Component: TraceList },
  { key: 'search', label: '全文搜索', Component: SearchTab },
] as const

const S: Record<string, any> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent' },
  bar: { display: 'flex', alignItems: 'center', borderTop: '1px solid #222', borderBottom: '1px solid #222', padding: 0 },
  btn: (active: boolean): React.CSSProperties => ({
    padding: '4px 14px',
    background: active ? '#1a2a3a' : 'transparent',
    color: active ? '#90caf9' : '#666',
    border: 'none',
    borderBottom: active ? '2px solid #90caf9' : '2px solid transparent',
    cursor: 'pointer',
    fontSize: 14,
    fontFamily: 'Consolas, Menlo, monospace',
  }),
  closeButton: {
    width: 28,
    height: 24,
    marginRight: 4,
    border: 'none',
    background: 'transparent',
    color: '#777',
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
  },
  body: { flex: 1, overflow: 'hidden' },
}

export default function BottomPanel({ onClose }: { onClose?: () => void }) {
  const [active, setActive] = useState<string>(TABS[0].key)
  const Cur = (TABS.find((t) => t.key === active) || TABS[0]).Component
  return (
    <div style={S.root}>
      <div style={S.bar}>
        {TABS.map((t) => (
          <button key={t.key} style={S.btn(active === t.key)} onClick={() => setActive(t.key)}>
            {t.label}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        {onClose && (
          <button type="button" title="关闭底部面板" aria-label="关闭底部面板" data-shell-bottom-close style={S.closeButton} onClick={onClose}>
            <PanelBottomClose size={15} strokeWidth={1.8} />
          </button>
        )}
      </div>
      <div style={S.body}>
        <Cur />
      </div>
    </div>
  )
}
