// Shell A 左侧 rail — 2026-07-19 零顶栏版(用户指令:"不要顶栏了,任何都不要,想办法塞进其他地方")。
// dashboard = 左 rail + 内容区(dockview 页签 + 页面),无顶栏。原薄顶栏全件收编:
//   ⌘K 命令面板 → rail 顶部槽位(虚线测量底衬);通知 bell → 目的地之后(badge=urgent 计数);
//   评论抽屉/全屏 → 条件槽位(bell 之后);⋯ 菜单 → rail 底部(状态细点之上);
//   调试 chip → 底部(状态旁,debug 时才有);blocked 状态细点 → rail 底部(原位保持)。
// 几何硬规则(G.5):收起态选中/hover 框精确包住"图标+36px 底衬"整体并水平居中(见 shellA.css)。
import React from 'react'
import {
  Activity,
  Bell,
  ClipboardList,
  Command,
  FolderKanban,
  Maximize2,
  MessageSquare,
  MoreHorizontal,
  NotebookPen,
  Settings,
  type LucideIcon,
} from 'lucide-react'
import { useMultiagentLink } from '../entities/multiagent/multiagentLink'
import './shellA.css'

export type SpineKey = 'home' | 'authored' | 'review' | 'multiagent' | 'controller' | 'settings'

// 左侧 rail 的目的地:每个点下去 = 切到一个主区视图(统一语义,与 V1 顶栏导航一致)。
export const SPINE: Array<{ key: SpineKey; label: string; Icon: LucideIcon }> = [
  { key: 'home', label: '项目', Icon: FolderKanban },
  { key: 'authored', label: '草稿箱', Icon: NotebookPen },
  { key: 'review', label: '审阅', Icon: ClipboardList },
  { key: 'multiagent', label: 'Multiagent', Icon: Activity },
  { key: 'controller', label: '总控', Icon: MessageSquare },
  { key: 'settings', label: '设置', Icon: Settings },
]

export const SPINE_LABEL: Record<SpineKey, string> = {
  home: '项目', authored: '草稿箱', review: '审阅', multiagent: 'Multiagent', controller: '总控', settings: '设置',
}

export interface RailSlots {
  /** ⌘K 命令面板(rail 顶槽)。 */
  onTogglePalette: () => void
  /** 通知:点击开合 + urgent 角标计数(pushed 未读 + 必验收待审;0 不显示)。 */
  notificationsOpen: boolean
  notificationBadge: number
  onToggleNotifications: () => void
  /** 全屏审阅:仅当前页签是 web_review 时出现(条件槽位;退出态由壳层浮动钮承载)。 */
  enterMaximizeVisible: boolean
  onEnterMaximize: () => void
  /** ⋯ 菜单(rail 底槽,状态细点之上)。 */
  moreOpen: boolean
  onToggleMore: () => void
  /** 调试交接 chip(debug 时才有;收起态=琥珀点,展开态=文字)。 */
  debugReady: boolean
}

export const ShellRail = React.memo(function ShellRail({ activeKey, reviewPending, statusTone, statusLabel, onPick, slots }: {
  activeKey: SpineKey
  reviewPending: number
  /** 'critical' | 'attention' | 'calm' — calm 时底部状态点不渲染(少字按需)。 */
  statusTone: string
  statusLabel: string
  onPick: (k: SpineKey) => void
  slots: RailSlots
}) {
  const multiagentConnected = useMultiagentLink((state) => state.connected)
  const statusColor = statusTone === 'critical' ? 'var(--fp-bp-seal)' : statusTone === 'attention' ? 'var(--fp-warn)' : 'var(--fp-ok)'
  return (
    <aside className="sha-rail" data-testid="cockpit-rail" aria-label="主导航">
      {/* ⌘K 命令面板(rail 顶槽,虚线测量底衬;语义=搜索/命令) */}
      <button
        type="button"
        className="sha-ri sha-ri-cmdk"
        onClick={slots.onTogglePalette}
        data-testid="cockpit-cmdk"
        aria-label="搜索 / 命令 (⌘K / Ctrl+K)"
      >
        <span className="ico-s ico-dashed" aria-hidden="true"><Command size={22} /></span>
        <span className="lb">命令面板</span>
        <span className="ct" aria-hidden="true">⌘K</span>
      </button>
      {SPINE.map(({ key, label, Icon }) => {
        const meta = key === 'review' ? reviewPending : 0
        return (
          <button
            key={key}
            type="button"
            className={`sha-ri${activeKey === key ? ' on' : ''}`}
            onClick={() => onPick(key)}
            data-testid={`cockpit-nav-${key}`}
            data-linked={key === 'multiagent' && multiagentConnected ? '1' : undefined}
            aria-label={label}
            aria-current={activeKey === key ? 'page' : undefined}
          >
            <span className="ico-c" aria-hidden="true"><Icon size={22} /></span>
            <span className="lb">{label}</span>
            {meta ? <span className="ct">{meta}</span> : null}
          </button>
        )
      })}
      {/* 通知(badge=urgent 计数;无则不显示) */}
      <button
        type="button"
        className={`sha-ri${slots.notificationsOpen ? ' on' : ''}`}
        onClick={slots.onToggleNotifications}
        data-testid="cockpit-notifications-toggle"
        aria-label="通知"
      >
        <span className="ico-c" aria-hidden="true"><Bell size={22} /></span>
        <span className="lb">通知</span>
        {slots.notificationBadge > 0 ? <span className="ct">{slots.notificationBadge}</span> : null}
      </button>
      {/* 全屏审阅(条件槽位:当前页签是 web_review 时出现) */}
      {slots.enterMaximizeVisible && (
        <button
          type="button"
          className="sha-ri"
          onClick={slots.onEnterMaximize}
          data-testid="cockpit-enter-maximize"
          aria-label="全屏审阅"
        >
          <span className="ico-s" aria-hidden="true"><Maximize2 size={18} /></span>
          <span className="lb">全屏</span>
        </button>
      )}
      {/* 底槽: ⋯ 菜单(整页快照/刷新/停靠/底部事件/管线) */}
      <button
        type="button"
        className={`sha-ri sha-ri-bottom${slots.moreOpen ? ' on' : ''}`}
        onClick={slots.onToggleMore}
        data-testid="cockpit-more"
        aria-label="更多"
      >
        <span className="ico-s" aria-hidden="true"><MoreHorizontal size={18} /></span>
        <span className="lb">更多</span>
      </button>
      {/* 调试交接(debug 时才有;收起态=琥珀点,展开态=文字) */}
      {slots.debugReady && (
        <div className="sha-status" data-testid="cockpit-debug-handoff-pill" title="调试交接已就绪">
          <span className="dot" style={{ background: 'var(--fp-warn)' }} aria-hidden="true" />
          <span className="lb">调试就绪</span>
        </div>
      )}
      {statusTone !== 'calm' && (
        <div className="sha-status" data-testid="cockpit-status" title={statusLabel}>
          <span className="dot" style={{ background: statusColor }} aria-hidden="true" />
          <span className="lb">{statusLabel}</span>
        </div>
      )}
    </aside>
  )
})
