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
  Bot,
  ClipboardList,
  Command,
  FolderKanban,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  NotebookPen,
  Settings,
  type LucideIcon,
} from 'lucide-react'
import { useMultiagentLink } from '../entities/multiagent/multiagentLink'
import './shellA.css'

export type SpineKey = 'home' | 'authored' | 'review' | 'multiagent' | 'controller' | 'settings'

// 左侧 rail 的目的地:每个点下去 = 切到一个主区视图。
// 2026-11-17 智能 / 内容两极剥离: 驾驶舱自己只管智能这一极(会话、Agent、设置);
// 内容这一极(项目、画布、审阅)归 Workshop 管, 这三个目的地打开的是嵌进来的 Workshop 页面。
// 原「中枢」退役: 它的内容类视图交给 Workshop, 剩下的 assistant / CLI Hub / 定时任务改叫「Agent」。
export const SPINE: Array<{ key: SpineKey; label: string; Icon: LucideIcon; pole: 'agent' | 'content' }> = [
  { key: 'multiagent', label: '会话', Icon: Activity, pole: 'agent' },
  { key: 'controller', label: 'Agent', Icon: Bot, pole: 'agent' },
  { key: 'home', label: '项目', Icon: FolderKanban, pole: 'content' },
  { key: 'authored', label: '画布', Icon: NotebookPen, pole: 'content' },
  { key: 'review', label: '审阅', Icon: ClipboardList, pole: 'content' },
  { key: 'settings', label: '设置', Icon: Settings, pole: 'agent' },
]

export const SPINE_LABEL: Record<SpineKey, string> = {
  home: '项目', authored: '画布', review: '审阅', multiagent: '会话', controller: 'Agent', settings: '设置',
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
  /** Native desktop window fullscreen; absent in the browser build. */
  desktopFullscreenVisible: boolean
  desktopFullscreen: boolean
  onToggleDesktopFullscreen: () => void
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
      {SPINE.map(({ key, label, Icon, pole }, index) => {
        const meta = key === 'review' ? reviewPending : 0
        // Workshop 管的三个目的地成一组, 组的首尾各画一道分隔。
        const groupEdge = index > 0 && SPINE[index - 1].pole !== pole
        return (
          <React.Fragment key={key}>
          {groupEdge ? <span className="sha-rail-sep" aria-hidden="true" /> : null}
          <button
            type="button"
            className={`sha-ri${activeKey === key ? ' on' : ''}`}
            onClick={() => onPick(key)}
            data-testid={`cockpit-nav-${key}`}
            data-pole={pole}
            data-linked={key === 'multiagent' && multiagentConnected ? '1' : undefined}
            aria-label={label}
            aria-current={activeKey === key ? 'page' : undefined}
          >
            <span className="ico-c" aria-hidden="true"><Icon size={22} /></span>
            <span className="lb">{label}</span>
            {meta ? <span className="ct">{meta}</span> : null}
          </button>
          </React.Fragment>
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
      {slots.desktopFullscreenVisible && (
        <button
          type="button"
          className={`sha-ri${slots.desktopFullscreen ? ' on' : ''}`}
          onClick={slots.onToggleDesktopFullscreen}
          data-testid="cockpit-desktop-fullscreen"
          aria-label={slots.desktopFullscreen ? '退出窗口全屏 (F11)' : '窗口全屏 (F11)'}
          aria-pressed={slots.desktopFullscreen}
        >
          <span className="ico-s" aria-hidden="true">
            {slots.desktopFullscreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
          </span>
          <span className="lb">{slots.desktopFullscreen ? '退出全屏' : '全屏'}</span>
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
