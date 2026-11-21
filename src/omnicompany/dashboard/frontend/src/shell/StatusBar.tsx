import React from 'react'
import { PanelBottomClose, PanelBottomOpen, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { usePanels } from '../stores/panelsStore'
import ClipboardSyncToggle from './ClipboardSyncToggle'

interface Props {
  sidebarVisible?: boolean
  bottomVisible?: boolean
  onToggleSidebar?: () => void
  onToggleBottom?: () => void
}

const S: Record<string, any> = {
  root: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    height: 22,
    padding: '0 8px',
    background: 'var(--fp-bp-paper-2)',
    color: 'var(--fp-link)',
    fontSize: 14,
    fontFamily: 'Consolas, Menlo, monospace',
    flexShrink: 0,
    borderTop: '1px solid var(--fp-border-subtle)',
  },
  action: (active: boolean): React.CSSProperties => ({
    height: 18,
    border: 'none',
    borderRadius: 3,
    background: active ? 'rgba(144,202,249,.16)' : 'transparent',
    color: active ? 'var(--fp-link)' : 'var(--fp-text-2)',
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '0 6px',
    fontSize: 14,
    fontFamily: 'Consolas, Menlo, monospace',
  }),
}

export default function StatusBar({ sidebarVisible = true, bottomVisible = true, onToggleSidebar, onToggleBottom }: Props) {
  const tabs = usePanels((s) => s.tabs)
  const activeId = usePanels((s) => s.activeId)
  const active = tabs.find((t) => t.id === activeId)
  const SidebarIcon = sidebarVisible ? PanelLeftClose : PanelLeftOpen
  const BottomIcon = bottomVisible ? PanelBottomClose : PanelBottomOpen

  return (
    <div style={S.root}>
      <span>omnicompany</span>
      <span style={{ color: 'var(--fp-text-2)' }}>/</span>
      <span>{tabs.length} 标签</span>
      {active && (
        <>
          <span style={{ color: 'var(--fp-text-2)' }}>/</span>
          <span>当前：{active.title}</span>
        </>
      )}
      <span style={{ flex: 1 }} />
      <ClipboardSyncToggle />
      {onToggleSidebar && (
        <button
          type="button"
          title={sidebarVisible ? '关闭左侧栏' : '打开左侧栏'}
          aria-label={sidebarVisible ? '关闭左侧栏' : '打开左侧栏'}
          data-shell-sidebar-toggle
          style={S.action(sidebarVisible)}
          onClick={onToggleSidebar}
        >
          <SidebarIcon size={13} strokeWidth={1.8} />
          <span>侧栏</span>
        </button>
      )}
      {onToggleBottom && (
        <button
          type="button"
          title={bottomVisible ? '关闭底部面板' : '打开底部面板'}
          aria-label={bottomVisible ? '关闭底部面板' : '打开底部面板'}
          data-shell-bottom-toggle
          style={S.action(bottomVisible)}
          onClick={onToggleBottom}
        >
          <BottomIcon size={13} strokeWidth={1.8} />
          <span>事件</span>
        </button>
      )}
      <span style={{ color: 'var(--fp-text-2)' }}>Ctrl+K / Cmd+K 跨实体跳转</span>
    </div>
  )
}
