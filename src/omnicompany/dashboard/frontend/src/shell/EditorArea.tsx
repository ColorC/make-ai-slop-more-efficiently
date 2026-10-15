import React, { useEffect, useRef, useState } from 'react'
import {
  DockviewReact,
  DockviewDefaultTab,
  type DockviewApi,
  type DockviewPanelRenderer,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
  type IDockviewPanelHeaderProps,
} from 'dockview'
import { Link2, Check, ExternalLink, LayoutPanelLeft, CopyMinus } from 'lucide-react'
import { usePanels, type DockDirection, type OpenedTab } from '../stores/panelsStore'
import { TAB_TITLE_LIMITS, useUiSettings } from '../stores/uiSettingsStore'
import { useReviewMaximize } from '../stores/reviewMaximizeStore'
import { registry } from '../entities/registry'
import {
  CC_TAB_WORKING_TITLE,
  useCcTabStatusSnapshot,
  useCcTabRunState,
  type CcTabSessionKind,
} from '../entities/cc_session/tabStatus'
import { openInVscode } from '../lib/openInVscode'
import { copyText } from '../lib/copyText'
import { openNativeBrowserUrl } from '../lib/workspaceNavigation'
import { decodeWebPageTarget } from '../lib/webTabHost'
import { NATIVE_WEBVIEW_OVERLAY_ATTRIBUTE } from '../lib/nativeWebviewOcclusion'
import { installTouchTabStripScroller } from '../lib/touchTabStripScroller'
import { isOutsideViewport, openDetachedTabWindow } from '../lib/detachedTabWindow'
import { VscodeIcon } from '../components/VscodeIcon'
import Tooltip from '../shared/view/ui/Tooltip'
import { useBreakpoint, useMediaQuery } from './useBreakpoint'
import { PanelRuntimeProvider, useDomPanelVisible, usePanelVisible, type PanelRuntimeValue } from './panelRuntime'
import { usePanelHydration } from './panelHydration'
import TabSidecarLayout, {
  TabSidecarToggleButton,
  useTabSidecar,
} from './TabSidecar'
import { REVIEW_COMMENTS_OPEN_EVENT, useReviewActive } from '../stores/reviewActiveStore'
import Welcome from './Welcome'
import {
  multiagentLinkedUrl,
  openMultiagentLinkedWindow,
  useMultiagentLink,
} from '../entities/multiagent/multiagentLink'

// 从页签背后的实体里挑一个可在 VSCode 打开的本地路径(不同实体字段不同; 无文件的页签返回 null)。
function pickVscodePath(entity: unknown): string | null {
  if (!entity || typeof entity !== 'object') return null
  const e = entity as Record<string, unknown>
  const p = e.json_path || e.folder_path || e.file_path || e.source_path || e.abs_path || e.path
  return typeof p === 'string' && p ? p : null
}

// 页签的一行深链: 打开驾驶舱并直达该页签(CockpitShell 挂载时消费 open_type/open_id/open_facet/open_title)。
// 对任何实体类型都成立(材料/计划/项目/对话/札记), 贴到浏览器地址栏/笔记/发给 agent 都能一键回到此处。
// 标题截 24 字: 让链接自述身份, 又不至于编码后长成一段。
function buildTabDeeplink(tabId: string): string | null {
  const tab = usePanels.getState().tabs.find((t) => t.id === tabId)
  if (!tab) return null
  if (tab.ref.type === 'multiagent') {
    return multiagentLinkedUrl(useMultiagentLink.getState().ensureOwnerLink())
  }
  const u = new URL(`${window.location.origin}/`)
  u.searchParams.set('open_type', tab.ref.type)
  u.searchParams.set('open_id', tab.ref.id)
  if (tab.facet) u.searchParams.set('open_facet', tab.facet)
  const title = (tab.title || '').trim()
  if (title) u.searchParams.set('open_title', title.length > 24 ? title.slice(0, 24) : title)
  return u.toString()
}

/** Web tabs expose their original URL; every other tab exposes its shareable Dashboard deep link. */
function buildTabBrowserUrl(tabId: string): string | null {
  const tab = usePanels.getState().tabs.find((candidate) => candidate.id === tabId)
  if (!tab) return null
  if (tab.ref.type === 'web_review') return decodeWebPageTarget(tab.ref.id)?.url ?? buildTabDeeplink(tabId)
  return buildTabDeeplink(tabId)
}

export function compactTabTitle(title: string, maxLength = TAB_TITLE_LIMITS.default): string {
  const normalized = title.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`
}

export const compactCliTabTitle = compactTabTitle

// Context menu for review/maximize actions.
function openTabContextMenu(e: React.MouseEvent, props: IDockviewPanelHeaderProps): void {
  e.preventDefault()
  useReviewMaximize.getState().openTabMenu({
    x: e.clientX,
    y: e.clientY,
    tabId: props.api.id,
    title: props.api.title ?? props.api.id,
  })
}

type TabHeaderParams = { tab?: OpenedTab }

const CcSessionReviewTab: React.FC<IDockviewPanelHeaderProps<TabHeaderParams>> = (props) => {
  const sessionId = props.params.tab?.ref.id || props.api.id.slice('cc_session:'.length).split('#')[0]
  const kind: CcTabSessionKind = sessionId.startsWith('chat-') ? 'chat' : 'pty'
  const state = useCcTabRunState(sessionId, kind)
  const statusSnapshot = useCcTabStatusSnapshot()
  const tabTitleMaxLength = useUiSettings((state) => state.tabTitleMaxLength)
  const liveTitle = statusSnapshot.metas[sessionId]?.title?.trim()

  useEffect(() => {
    if (!liveTitle) return
    const compactTitle = compactTabTitle(liveTitle, tabTitleMaxLength)
    if (props.api.title !== compactTitle) props.api.setTitle(compactTitle)
    const tabId = props.params.tab?.id || props.api.id
    const stored = usePanels.getState().tabs.find((tab) => tab.id === tabId)
    if (stored && stored.title !== liveTitle) {
      usePanels.setState((current) => ({
        tabs: current.tabs.map((tab) => (
          tab.id === tabId ? { ...tab, title: liveTitle } : tab
        )),
      }))
    }
  }, [liveTitle, props.api, props.params.tab?.id, tabTitleMaxLength])

  return (
    <div
      className="omni-dock-tab omni-cc-session-tab"
      data-omni-tab-id={props.params.tab?.id || props.api.id}
      data-cc-tab-status={state}
      title={liveTitle || props.params.tab?.title || props.api.title || sessionId}
      onContextMenu={(e) => openTabContextMenu(e, props)}
    >
      {/* Keep the status slot mounted for every state. Only its visibility changes,
          so a poll transition cannot push the tab title or resize the tab. */}
      <span
        className="omni-cc-tab-state"
        title={state === 'working' ? CC_TAB_WORKING_TITLE : undefined}
        role={state === 'working' ? 'status' : undefined}
        aria-label={state === 'working' ? CC_TAB_WORKING_TITLE : undefined}
        aria-hidden={state === 'working' ? undefined : true}
      >
        <i className="omni-cc-tab-state-dot" aria-hidden="true" />
      </span>
      <DockviewDefaultTab {...props} />
    </div>
  )
}

const ReviewTab: React.FC<IDockviewPanelHeaderProps<TabHeaderParams>> = (props) => {
  if (props.params.tab?.ref.type === 'cc_session' || props.api.id.startsWith('cc_session:')) {
    return <CcSessionReviewTab {...props} />
  }
  return (
    <div
      className="omni-dock-tab"
      data-omni-tab-id={props.params.tab?.id || props.api.id}
      onContextMenu={(e) => openTabContextMenu(e, props)}
    >
      <DockviewDefaultTab {...props} />
    </div>
  )
}

const menuStyles = {
  backdrop: { position: 'fixed' as const, inset: 0, zIndex: 1000 },
  menu: {
    position: 'fixed' as const,
    minWidth: 168,
    border: '1px solid var(--fp-border)',
    borderRadius: 'var(--fp-r2)',
    background: 'var(--fp-bp-tracing-strong)',
    boxShadow: 'var(--fp-bp-shadow-pop)',
    padding: 6,
  },
  item: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    width: '100%',
    textAlign: 'left' as const,
    border: 0,
    background: 'transparent',
    color: 'var(--fp-text)',
    borderRadius: 'var(--fp-r1)',
    padding: '7px 9px',
    fontSize: 'var(--fp-fs-3)',
    fontFamily: 'var(--fp-font-sans)',
    cursor: 'pointer',
    whiteSpace: 'nowrap' as const,
  },
}

const TabContextMenu: React.FC = () => {
  const tabMenu = useReviewMaximize((s) => s.tabMenu)
  const maximizedTabId = useReviewMaximize((s) => s.maximizedTabId)
  const maximize = useReviewMaximize((s) => s.maximize)
  const exit = useReviewMaximize((s) => s.exit)
  const close = useReviewMaximize((s) => s.closeTabMenu)
  // 菜单打开时解析该页签对应的本地文件路径; 解析到才显示「在 VSCode 打开」(无文件的页签不显示, 免得点了没反应)。
  const [vscPath, setVscPath] = useState<string | null>(null)
  // 复制链接的反馈态: 复制是异步降级链, 成功打勾后自动收起, 失败原地提示(不静默)。
  const [copyState, setCopyState] = useState<'idle' | 'ok' | 'fail'>('idle')
  useEffect(() => {
    setVscPath(null)
    setCopyState('idle')
    if (!tabMenu) return
    const tab = usePanels.getState().tabs.find((t) => t.id === tabMenu.tabId)
    const reg = tab && registry.get(tab.ref.type)
    if (!tab || !reg) return
    let alive = true
    reg.resolver.fetch(tab.ref.id).then((e) => { if (alive) setVscPath(pickVscodePath(e)) }).catch(() => { /* 解析失败就当无文件 */ })
    return () => { alive = false }
  }, [tabMenu])
  if (!tabMenu) return null
  const menuTab = usePanels.getState().tabs.find((tab) => tab.id === tabMenu.tabId)
  const isMultiagent = menuTab?.ref.type === 'multiagent'
  const isThisMaximized = maximizedTabId === tabMenu.tabId
  const otherTabCount = usePanels.getState().tabs.filter((tab) => tab.id !== tabMenu.tabId).length
  const left = Math.min(tabMenu.x, (typeof window !== 'undefined' ? window.innerWidth : 1200) - 184)
  const top = Math.min(tabMenu.y, (typeof window !== 'undefined' ? window.innerHeight : 800) - 220)
  return (
    <div
      style={menuStyles.backdrop}
      {...{ [NATIVE_WEBVIEW_OVERLAY_ATTRIBUTE]: 'true' }}
      onClick={close}
      onContextMenu={(e) => {
        e.preventDefault()
        close()
      }}
    >
      <div style={{ ...menuStyles.menu, left, top }} onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          style={{
            ...menuStyles.item,
            opacity: otherTabCount > 0 ? 1 : 0.45,
            cursor: otherTabCount > 0 ? 'pointer' : 'default',
          }}
          data-testid="tab-menu-close-others"
          disabled={otherTabCount === 0}
          onClick={() => {
            usePanels.getState().closeOtherTabs(tabMenu.tabId)
            close()
          }}
          onMouseEnter={(e) => {
            if (otherTabCount > 0) e.currentTarget.style.background = 'var(--fp-bp-panel-hover)'
          }}
          onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
        >
          <CopyMinus size={14} /> 只留当前页签
        </button>
        {isMultiagent && (
          <button
            type="button"
            style={menuStyles.item}
            data-testid="tab-menu-open-linked-multiagent"
            onClick={() => {
              openMultiagentLinkedWindow()
              close()
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--fp-bp-panel-hover)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <LayoutPanelLeft size={14} /> 在联动窗口打开
          </button>
        )}
        <button
          type="button"
          style={menuStyles.item}
          data-testid="tab-menu-toggle-maximize"
          onClick={() => {
            if (isThisMaximized) exit()
            else maximize(tabMenu.tabId)
            close()
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--fp-bp-panel-hover)')}
          onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
        >
          {isThisMaximized ? '退出最大化' : '最大化审阅（全屏）'}
        </button>
        <Tooltip content={buildTabBrowserUrl(tabMenu.tabId) || ''} position="left" containerStyle={{ display: 'block' }}>
          <button
            type="button"
            style={menuStyles.item}
            data-testid="tab-menu-open-system-browser"
            onClick={() => {
              const url = buildTabBrowserUrl(tabMenu.tabId)
              if (url) openNativeBrowserUrl(url)
              close()
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--fp-bp-panel-hover)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <ExternalLink size={14} /> 在系统浏览器打开
          </button>
        </Tooltip>
        <Tooltip content={buildTabDeeplink(tabMenu.tabId) || ''} position="left" containerStyle={{ display: 'block' }}>
          <button
            type="button"
            style={menuStyles.item}
            data-testid="tab-menu-copy-link"
            onClick={async () => {
              const link = buildTabDeeplink(tabMenu.tabId)
              if (!link) return
              const ok = await copyText(link)
              if (ok) {
                setCopyState('ok')
                setTimeout(close, 600)
              } else {
                setCopyState('fail')
              }
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--fp-bp-panel-hover)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            {copyState === 'ok' ? <Check size={14} /> : <Link2 size={14} />}
            {copyState === 'ok' ? '已复制' : copyState === 'fail' ? '复制失败(剪贴板受限)' : '复制链接'}
          </button>
        </Tooltip>
        {vscPath && (
          <Tooltip content={vscPath} position="left" containerStyle={{ display: 'block' }}>
            <button
              type="button"
              style={menuStyles.item}
              data-testid="tab-menu-open-vscode"
              onClick={() => {
                openInVscode(vscPath)
                close()
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--fp-bp-panel-hover)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <VscodeIcon size={14} /> 在 VSCode 打开
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  )
}

// 页签通用等待骨架: 顶部一条题头 + 卡片网格轮廓, 同 index.html 启动骨架/.fp-skeleton 一套扫光。
const PanelSkeleton: React.FC = () => (
  <div style={{ padding: 16, height: '100%', boxSizing: 'border-box', overflow: 'hidden' }}>
    <div className="fp-skeleton" style={{ height: 30, width: 320, marginBottom: 18 }} />
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="fp-skeleton" style={{ height: 148 }} />
      ))}
    </div>
  </div>
)

const SessionCompanion = React.lazy(() => import('../entities/cc_session/SessionCompanion'))
const CommentsPanel = React.lazy(() => import('../entities/review/CommentsPanel').then((module) => ({
  default: module.CommentsPanel,
})))

function SessionCompanionSidecar({ sessionId }: { sessionId: string }) {
  const sidecar = useTabSidecar()
  return (
    <SessionCompanion
      sessionId={sessionId}
      mode="embedded"
      headerActions={(
        <TabSidecarToggleButton
          label="会话信息"
          showWhen="open"
          testId="session-ctx-toggle"
        />
      )}
      onHeaderDoubleClick={sidecar?.toggleWidth}
    />
  )
}

function ReviewCommentsBridge({ tab }: { tab: OpenedTab }) {
  const sidecar = useTabSidecar()
  const setActiveMaterial = useReviewActive((state) => state.setActiveMaterial)
  // 页签常驻(2026-08-07): 所有审阅页签都挂着, 所以"当前材料是我"只能由**可见**的那个宣告。
  // 否则最后水合的后台页签会赢, 评价侧栏指向看不见的材料(还会 POST 广播到别的 webview)。
  const panelVisible = usePanelVisible()

  useEffect(() => {
    if (!panelVisible) return
    if (tab.ref.type === 'review_material') {
      setActiveMaterial(String(tab.ref.id), 'local')
    }
  }, [panelVisible, setActiveMaterial, tab.ref.id, tab.ref.type])

  useEffect(() => {
    if (!panelVisible) return undefined
    const openComments = (event: Event) => {
      const materialId = (event as CustomEvent<{ materialId?: string }>).detail?.materialId
      if (!materialId) return
      if (tab.ref.type === 'review_material' && String(tab.ref.id) !== materialId) return
      setActiveMaterial(materialId, 'local')
      sidecar?.open()
    }
    window.addEventListener(REVIEW_COMMENTS_OPEN_EVENT, openComments)
    return () => window.removeEventListener(REVIEW_COMMENTS_OPEN_EVENT, openComments)
  }, [panelVisible, setActiveMaterial, sidecar, tab.ref.id, tab.ref.type])

  return null
}

const ReviewCommentsSidecar = () => (
  <CommentsPanel
    headerActions={(
      <TabSidecarToggleButton
        label="评价与批注"
        showWhen="open"
        testId="review-comments-toggle"
      />
    )}
  />
)

function SharedEditorSidecar({
  activeTab,
  children,
}: {
  activeTab: OpenedTab | undefined
  children: React.ReactNode
}) {
  const companionSessionId = activeTab?.ref.type === 'cc_session'
    ? activeTab.ref.id
    : activeTab?.ref.type === 'review_material' && activeTab.returnTo?.ref.type === 'cc_session'
      ? activeTab.returnTo.ref.id
      : null
  const sidecar = companionSessionId
    ? <SessionCompanionSidecar sessionId={companionSessionId} />
    : activeTab?.ref.type === 'review_material' || activeTab?.ref.type === 'review_queue'
      ? <ReviewCommentsSidecar />
      : null

  const reviewSidecar = !companionSessionId
    && (activeTab?.ref.type === 'review_material' || activeTab?.ref.type === 'review_queue')
  /**
   * ⚠ **必须永远渲染 TabSidecarLayout, 哪怕没有伴随视图**(2026-08-07 实证根因)。
   *
   * 这里原来是 `if (!sidecar) return <>{children}</>` —— 于是包着 `<DockviewReact>` 的那一层
   * 会在 `Fragment` 和 `TabSidecarLayout` 之间来回变。React 按**树的位置与类型**做协调:
   * 父节点类型一换, 整个子树连同 children **卸载重挂**, 即使 children 的元素引用没变。
   *
   * 后果是致命的: 只要焦点页签跨过"有伴随视图"的边界(CLI / 审阅材料 ↔ 其它任何页签),
   * **整个 dock 就被拆了重建** —— 所有面板重新创建、所有 iframe 从头加载、终端 dispose
   * 后重连重放历史、滚动位置全丢。也就是说无论 renderer 设成什么、热集留几个,
   * "切换页签就重载"都必然发生: 用户报的 `http://…:8210/spark-webtwin/` 每次都重新加载
   * 就是这条路(从 CLI 页签切到它 → 边界跨越 → dock 重建 → 重型页面重新拉一遍)。
   *
   * 所以没有伴随视图时也走同一个布局组件、同一套 DOM 外壳(div.tab-sidecar-layout >
   * div.tab-sidecar-main), 只是不渲染右侧 aside —— 树的形状与 children 的 DOM 祖先链
   * 全程不变(搬 iframe 的 DOM 节点同样等于重载, 这也是不能换外壳的原因)。
   */
  return (
    <TabSidecarLayout
      sidecar={sidecar ? <React.Suspense fallback={<PanelSkeleton />}>{sidecar}</React.Suspense> : null}
      // 伴随侧栏(札记所在)默认展开 — 札记的唯一入口就是侧栏页签, 不再提供终端旁路按钮
      // (2026-08-16 用户裁决)。CLI 冷启动不被伴随数据拖慢: sidecar 走 Suspense 骨架异步水合。
      // 审阅评论侧栏维持默认收起(它是辅助批注面, 不是常驻工作区)。
      defaultCollapsed={reviewSidecar ? true : companionSessionId ? false : null}
      {...(reviewSidecar && activeTab ? {
        defaultWidth: 360,
        collapsedPreferenceKey: null,
        widthPreferenceKey: 'omni.tabSidecar.reviewComments.width',
        resetKey: activeTab.id,
      } : {})}
    >
      {children}
    </TabSidecarLayout>
  )
}

const EntityPanel: React.FC<IDockviewPanelProps<{ tab: OpenedTab }>> = (props) => {
  const tab = props.params.tab
  const reg = registry.get(tab.ref.type)
  if (!reg) {
    return <div style={{ padding: 16, color: 'var(--fp-err)' }}>未注册的实体类型: {tab.ref.type}</div>
  }
  const Editor = reg.renderer.Editor as React.ComponentType<{ entity: any; facet?: string; panelVisible?: boolean }>
  const [entity, setEntity] = React.useState<any>(null)
  const [error, setError] = React.useState<string | null>(null)
  // "panelVisible" 对终端而言必须是「当前激活页签且真的在布局里」:
  // dockview 切走页签时只给内容祖先 display:none, 不发任何可见性 API 事件
  // (isVisible/isActive 都保持 true) —— 只靠 dockview API 的话终端的
  // fit/refresh/visibility 补量在切回时永远不跑。DOM 量尺寸才是事实信号。
  const domVisible = useDomPanelVisible()
  // isActive 在旧 mock/测试里可能不存在 —— undefined 按"是激活"处理, 只认显式 false。
  const [apiVisible, setApiVisible] = React.useState(
    () => props.api.isVisible !== false && props.api.isActive !== false,
  )
  const panelVisible = apiVisible && domVisible.visible
  const tabTitleMaxLength = useUiSettings((state) => state.tabTitleMaxLength)
  // 全部常驻后, 隐藏页签也在挂载中 —— 它们的内容按「可见优先 + 空闲有界并发」分批水合,
  // 免得刷新驾驶舱时十几个页签在同一帧里一起解析实体、下载懒 chunk。
  // hydrated 单向: 放行之后永不回到骨架, 这正是"切回页签不再加载"的落点。
  const { hydrated, settle } = usePanelHydration(tab.id, panelVisible)

  React.useEffect(() => {
    const update = () => setApiVisible(props.api.isVisible !== false && props.api.isActive !== false)
    update()
    const visibilitySubscription = props.api.onDidVisibilityChange(() => update())
    const activeSubscription = props.api.onDidActiveChange(() => update())
    return () => {
      visibilitySubscription.dispose()
      activeSubscription.dispose()
    }
  }, [props.api])
  React.useEffect(() => {
    if (!hydrated) return undefined
    let alive = true
    setError(null)
    setEntity(null)
    reg.resolver.fetch(tab.ref.id).then((resolved) => {
      if (alive) setEntity(resolved)
    }).catch((e) => {
      if (alive) setError(String(e))
    }).finally(() => {
      // 让出水合名额: 成功或失败都算这一轮结束, 下一个后台页签可以进场了。
      settle()
    })
    return () => { alive = false }
  }, [hydrated, reg.resolver, settle, tab.ref.id, tab.ref.type])

  React.useEffect(() => {
    if (!entity) return
    const resolvedTitle = typeof entity.title === 'string' ? entity.title.trim() : ''
    if (!resolvedTitle) return
    const panelTitle = compactTabTitle(resolvedTitle, tabTitleMaxLength)
    if (props.api.title !== panelTitle) props.api.setTitle(panelTitle)
    if (resolvedTitle !== tab.title) usePanels.getState().renameTab(tab.id, resolvedTitle)
  }, [entity, props.api, tab.id, tab.title, tabTitleMaxLength])

  const runtime = React.useMemo<PanelRuntimeValue>(
    () => ({ tabId: tab.id, visible: panelVisible }),
    [panelVisible, tab.id],
  )

  if (error) return <div style={{ padding: 16, color: 'var(--fp-err)' }}>{error}</div>
  // 实体解析/懒 chunk 下载期间的等待面: 所有页签共用这一处, 用骨架扫光而不是一行灰字,
  // 让"正在加载"始终可见(2026-07-17 用户: 加载过程要看得见)。
  // 未水合的后台页签也停在这里 —— 骨架不联网、不排版, 排队时几乎不花钱。
  // 注意: 骨架必须渲染在尺寸探针 div **内部**, 否则探针 div 没布局盒 → domVisible
  // 永远 false → 水合永远不放行(死锁)。
  // Suspense 兜住懒加载的 Editor(切到该 tab 才下载对应 chunk); 非懒加载 Editor 同步渲染不受影响。
  const editor = hydrated && entity
    ? <Editor entity={entity} facet={tab.facet} panelVisible={panelVisible} />
    : <PanelSkeleton />
  return (
    <PanelRuntimeProvider value={runtime}>
      <React.Suspense fallback={<PanelSkeleton />}>
        <PanelSwapIn key={`${tab.id}:${tab.facet ?? ''}`} visible={panelVisible}>
          {/* 尺寸探针根节点: useDomPanelVisible 量它判断面板是否真在布局里 */}
          <div ref={domVisible.ref} style={{ height: '100%', minHeight: 0, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
            {tab.ref.type === 'review_material' || tab.ref.type === 'review_queue'
              ? <><ReviewCommentsBridge tab={tab} />{editor}</>
              : editor}
          </div>
        </PanelSwapIn>
      </React.Suspense>
    </PanelRuntimeProvider>
  )
}

/** 页签内容切换的淡入壳(2026-08-06)。
 *
 * ⚠ 动画播完必须把 class 摘掉, 这不是洁癖 —— **带 animation 的元素会创建 backdrop root**,
 * 它内部所有元素的"背后"就成了空的, backdrop-filter 会糊出一片虚无。
 * 本壳包着每一个面板, 一旦常驻就等于把全站面板内部的玻璃材质(蓝图玻璃卡片、描图纸弹层、
 * 终端玻璃…)一起废掉 —— 2026-08-06 实测踩过这个坑, 现象是"CLI 压根没有模糊效果"。
 *
 * ⚠ 同样致命的是"在 display:none 里挂动画": CSS 动画在隐藏子树里**不会播**, 于是
 * animationend 永远不来, class 永远摘不掉 —— 页签常驻(2026-08-07)之后隐藏面板也在挂载,
 * 这条路必然被走到。所以 class 只在**面板可见**时挂: 隐藏期间是普通 div,
 * 页签切回来的那一刻(visible 由假变真)重新播一次, 播完照旧摘掉。
 */
export function PanelSwapIn({ visible = true, children }: { visible?: boolean; children: React.ReactNode }) {
  const [animating, setAnimating] = React.useState(visible)
  const wasVisible = React.useRef(visible)
  React.useEffect(() => {
    if (visible && !wasVisible.current) setAnimating(true)
    wasVisible.current = visible
  }, [visible])
  return (
    <div
      className={visible && animating ? 'mo-swap-in' : undefined}
      onAnimationEnd={() => setAnimating(false)}
      // 不能用 display:contents —— 那样不生成盒子, animation 无从作用。
      // 撑满父容器的 flex 列: 子面板无论用 height:100% 还是 flex:1 都照常铺满。
      style={{ height: '100%', minHeight: 0, minWidth: 0, display: 'flex', flexDirection: 'column' }}
    >
      {children}
    </div>
  )
}

const components = { entity: EntityPanel }
type DockPanel = NonNullable<ReturnType<DockviewApi['getPanel']>>

/**
 * 页签常驻档(2026-08-07 用户改定「先改一个彻底 —— 所有打开的页签都一并打开」)。
 *
 * dockview 的 `always` 会把面板内容挂到一个绝对定位的 overlay 容器里, 切走只是
 * `display:none`(见 dockview-core/overlay/overlayRenderContainer): DOM、滚动位置、
 * 表单、SPA 路由、iframe、原生 WebView 全部原样保留, 不排版也不绘制。所以"全部常驻"
 * 的**绘制**开销与"只挂当前一个"几乎一样, 真正要管住的是两件事:
 *
 *   ① 首屏别被后台页签一起加载挤掉 → panelHydration 有界并发分批水合;
 *   ② 后台页签的轮询别一直跑 → panelRuntime 的 useBackgroundPoll 隐藏即挂起。
 *
 * 之前这里是"总控 + 最近 4 个重型页签"的有界热集(LRU), 其余 onlyWhenVisible ——
 * 那正是用户报的"切换任何页签都要加载": 热集外的页签每次切走都被 dockview 卸载。
 */
const PANEL_RENDERER: DockviewPanelRenderer = 'always'

// M3 窄屏降级: 把所有已存在的 dock 组并回一组(页签切换代替平铺)。api.groups/activeGroup 是
// dockview 官方 API; 测试 mock 里没有这两个字段, 防御性读取, 拿不到即视为无需合并。
function mergeIntoSingleGroup(api: DockviewApi) {
  const anyApi = api as unknown as { groups?: Array<{ panels?: Array<{ api: { moveTo: (o: { group: unknown }) => void } }> }>; activeGroup?: unknown }
  const groups = anyApi.groups || []
  if (groups.length <= 1) return
  const target = anyApi.activeGroup || groups[0]
  for (const g of groups) {
    if (g === target) continue
    for (const p of [...(g.panels || [])]) {
      try { p.api.moveTo({ group: target }) } catch { /* 并组失败不阻塞渲染 */ }
    }
  }
}

function placementSignature(tab: OpenedTab): string {
  if (!tab.placement) return ''
  return `${tab.placement.direction}:${tab.placement.referenceTabId || ''}`
}

function toDockPosition(direction: DockDirection): 'left' | 'right' | 'top' | 'bottom' {
  if (direction === 'above') return 'top'
  if (direction === 'below') return 'bottom'
  return direction
}

function findPlacementReference(api: DockviewApi, tab: OpenedTab, fallbackActiveId: string | null): DockPanel | undefined {
  const explicit = tab.placement?.referenceTabId
  if (explicit && explicit !== tab.id) {
    const panel = api.getPanel(explicit)
    if (panel) return panel
  }
  if (fallbackActiveId && fallbackActiveId !== tab.id) {
    const panel = api.getPanel(fallbackActiveId)
    if (panel) return panel
  }
  return api.panels.find((p) => p.id !== tab.id)
}

function EditorArea() {
  const rootRef = useRef<HTMLDivElement>(null)
  const apiRef = useRef<DockviewApi | null>(null)
  const draggingTabIdRef = useRef<string | null>(null)
  const dragLeftViewportRef = useRef(false)
  const tearOffPendingRef = useRef(new Set<string>())
  const tabs = usePanels((s) => s.tabs)
  const activeId = usePanels((s) => s.activeId)
  const closeTab = usePanels((s) => s.closeTab)
  const activate = usePanels((s) => s.activate)
  const tabLayout = useUiSettings((s) => s.tabLayout)
  const tabTitleMaxLength = useUiSettings((s) => s.tabTitleMaxLength)
  const lastSyncedTabsRef = useRef<string>('')
  const [readyVersion, setReadyVersion] = useState(0)
  // M3 窄屏降级: 非桌面断点或 coarse 指针 → 单面板组(页签切换代替平铺) + 官方 disableDnd/
  // disableFloatingGroups 禁拖拽拆分(不禁页签点击切换; 不用 CSS pointer-events 粗暴全局禁)。
  // 桌面档行为完全不变(matchMedia 不可用的测试环境也走桌面档)。
  const bp = useBreakpoint()
  const coarsePointer = useMediaQuery('(pointer: coarse)')
  const degraded = bp !== 'desktop' || coarsePointer

  const syncPanels = (api: DockviewApi, nextTabs: OpenedTab[], nextActiveId: string | null) => {
    const sig = `${degraded ? 'S' : 'D'}|${tabTitleMaxLength}|${nextTabs.map((t) => `${t.id}:${t.title}:${placementSignature(t)}`).join('|')}#${nextActiveId}`
    if (sig === lastSyncedTabsRef.current) return
    lastSyncedTabsRef.current = sig

    const existing = new Set(api.panels.map((p) => p.id))
    for (const tab of nextTabs) {
      const panel = api.getPanel(tab.id)
      const title = compactTabTitle(tab.title, tabTitleMaxLength)
      if (panel && panel.api.title !== title) panel.api.setTitle(title)
      // 常驻档没有"降级/晋升"这回事了 —— 所有面板一开始就按 always 创建。
      // (dockview 的 renderer 只在创建时决定内容挂在哪; 事后 setRenderer 会触发
      //  group.model.rerender 把内容元素搬家, 而搬 iframe = 整页重载, 所以宁可一次定档。)
      if (panel && panel.api.renderer !== PANEL_RENDERER) panel.api.setRenderer(PANEL_RENDERER)
    }
    const tabsWithPlacement: OpenedTab[] = nextTabs.filter((t) => t.placement)
    // 先加 active 页签、再按原顺序加其余(2026-07-20 实证根因): dockview 在无 activeGroup 时
    // 收到 inactive addPanel 会给每个面板各建一个新 dock 组 —— 固定三页签按 [项目, 任务, 总控]
    // 顺序加、前两个 inactive 各成一组, 总控再建第三组, 就是用户看到的"三个区域同屏 +
    // 任务窗口里也是项目"(水印 Welcome 残留中间组)。active 先建组并激活后,
    // 其余 inactive 面板都落进同一个 activeGroup 成为同组页签。
    const toAdd = nextTabs.filter((t) => !existing.has(t.id))
    const orderedAdds = nextActiveId
      ? [...toAdd].sort((a, b) => (a.id === nextActiveId ? -1 : b.id === nextActiveId ? 1 : 0))
      : toAdd
    // Dockview 4.13 can leave its content container on the first inserted tab
    // when an active tab is added first and then re-indexed behind existing tabs.
    // Its header/API still report the requested tab as active, so a normal
    // `if (!isActive) setActive()` cannot repair the empty/stale content pane.
    const activeWillBeReindexed = Boolean(
      nextActiveId
      && orderedAdds.length > 1
      && orderedAdds[0]?.id === nextActiveId
      && nextTabs.findIndex((tab) => tab.id === nextActiveId) > 0,
    )
    for (const tab of orderedAdds) {
      // 降级档忽略分屏意图: 新面板一律作为同组页签打开(不带 position direction)。
      const referencePanel = !degraded && tab.placement ? findPlacementReference(api, tab, nextActiveId) : undefined
      // 无 placement 的新面板按 tabs 原序插入(index 形式需要 referencePanel —— 用 active 面板做锚,
      // 它本轮第一个加入必然已存在; 锚拿不到就不带 position, 落 activeGroup 末尾)。
      const desiredIndex = nextTabs.findIndex((t) => t.id === tab.id)
      const orderAnchor = !referencePanel
        ? [...nextTabs.slice(0, desiredIndex).reverse(), ...nextTabs.slice(desiredIndex + 1)]
          .map((candidate) => api.getPanel(candidate.id))
          .find((candidate): candidate is DockPanel => Boolean(candidate))
        : undefined
      api.addPanel({
        id: tab.id,
        component: 'entity',
        title: compactTabTitle(tab.title, tabTitleMaxLength),
        params: { tab },
        // 全部常驻: 所有页签一并挂载, 切走只 display:none, 不卸载也不重载。
        // 首屏不会被后台页签压垮 —— 隐藏页签的内容由 panelHydration 在空闲时分批水合。
        renderer: PANEL_RENDERER,
        // 中键后台打开: 新增的非活跃 tab 用 inactive 挂载, 不抢焦点(activeId 未变)。
        // nextActiveId 为空时强制第一个面板激活建组, 否则每个 inactive 面板又会各建一组。
        inactive: nextActiveId ? tab.id !== nextActiveId : tab !== orderedAdds[0],
        ...(referencePanel
          ? { position: { referencePanel, direction: tab.placement?.direction } }
          : orderAnchor
            ? { position: { referencePanel: orderAnchor, index: desiredIndex } }
            : {}),
      })
    }
    for (const tab of tabsWithPlacement) {
      const panel = api.getPanel(tab.id)
      const referencePanel = !degraded ? findPlacementReference(api, tab, nextActiveId) : undefined
      if (panel && referencePanel && tab.placement && panel.group === referencePanel.group) {
        panel.api.moveTo({ group: referencePanel.group, position: toDockPosition(tab.placement.direction) })
      }
    }
    for (const tab of tabsWithPlacement) {
      usePanels.getState().clearDockPlacement(tab.id)
    }
    for (const p of api.panels) {
      if (!nextTabs.some((t) => t.id === p.id)) p.api.close()
    }
    if (nextActiveId) {
      const p = api.getPanel(nextActiveId)
      if (p && activeWillBeReindexed && p.api.isActive) {
        // Force one real active-panel transition so Dockview re-attaches the
        // requested panel's `onlyWhenVisible` content. Both calls are sync;
        // React only commits the final requested panel from this call stack.
        const sibling = api.panels.find((candidate) => candidate.id !== p.id && candidate.group === p.group)
        sibling?.api.setActive()
        p.api.setActive()
      } else if (p && !p.api.isActive) {
        p.api.setActive()
      }
    }
    // 降级档: 同步完若仍是多组(例如从桌面拖出过分屏后收窄), 并回单组。
    if (degraded) mergeIntoSingleGroup(api)
  }

  const onReady = (event: DockviewReadyEvent) => {
    apiRef.current = event.api
    lastSyncedTabsRef.current = ''
    setReadyVersion((v) => v + 1)
    useReviewMaximize.getState().registerApi(event.api)
    event.api.onDidActivePanelChange((p) => {
      if (p) activate(p.id)
    })
    event.api.onDidRemovePanel((p) => {
      const tab = usePanels.getState().tabs.find((t) => t.id === p.id)
      if (tab) closeTab(tab.id)
    })
    // dockview 自身退出最大化(关页/拖拽)时回收外壳状态。
    event.api.onDidMaximizedGroupChange(() => useReviewMaximize.getState().syncFromDockview())
    const state = usePanels.getState()
    syncPanels(event.api, state.tabs, state.activeId)
    // 就绪收敛(2026-07-20): 此刻不可能有任何 placement(只产生于就绪后的用户动作),
    // 若仍出现多 dock 组(异常序列/旧版脏态残留), 以 tabs 为准并回单组 ——
    // "三个区域同一屏出现"不再发生; 之后用户手动拖出的分屏不受影响。
    if (!degraded) {
      try {
        const groups = (event.api as unknown as { groups?: unknown[] }).groups || []
        if (groups.length > 1) mergeIntoSingleGroup(event.api)
      } catch { /* mock/旧版 dockview 无 groups 时跳过 */ }
    }
  }

  useEffect(() => {
    const api = apiRef.current
    if (!api) return
    syncPanels(api, tabs, activeId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabs, activeId, readyVersion, degraded, tabTitleMaxLength])

  // 降级档双保险: 任何新 dock 组出现(程序性 split / 意外 drop)立刻并回单组。
  useEffect(() => {
    const api = apiRef.current
    if (!api || !degraded) return
    mergeIntoSingleGroup(api)
    const sub = (api as unknown as { onDidAddGroup?: (cb: () => void) => { dispose: () => void } }).onDidAddGroup?.(() => mergeIntoSingleGroup(api))
    return () => sub?.dispose?.()
  }, [degraded, readyVersion])

  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    return installTouchTabStripScroller(root)
  }, [])

  useEffect(() => {
    const root = rootRef.current
    if (!root || degraded) return

    const tabIdFromTarget = (target: EventTarget | null): string | null => {
      if (!(target instanceof Element)) return null
      const own = target.closest<HTMLElement>('[data-omni-tab-id]')
      if (own?.dataset.omniTabId) return own.dataset.omniTabId
      return target.querySelector<HTMLElement>('[data-omni-tab-id]')?.dataset.omniTabId || null
    }
    const onDragStart = (event: DragEvent) => {
      draggingTabIdRef.current = tabIdFromTarget(event.target)
      dragLeftViewportRef.current = false
    }
    const onDragOver = () => {
      if (draggingTabIdRef.current) dragLeftViewportRef.current = false
    }
    const onDragLeave = (event: DragEvent) => {
      if (draggingTabIdRef.current && event.relatedTarget === null) {
        dragLeftViewportRef.current = true
      }
    }
    const onDragEnd = (event: DragEvent) => {
      const tabId = draggingTabIdRef.current
      const leftViewport = dragLeftViewportRef.current
      draggingTabIdRef.current = null
      dragLeftViewportRef.current = false
      if (!tabId) return

      const outside = isOutsideViewport(
        { clientX: event.clientX, clientY: event.clientY },
        { width: window.innerWidth, height: window.innerHeight },
      )
      // WebView2 reports a cancelled HTML drag at (0, 0). Only accept that
      // ambiguous coordinate after a real document-level dragleave.
      if (!outside || (event.clientX === 0 && event.clientY === 0 && !leftViewport)) return

      const state = usePanels.getState()
      const tab = state.tabs.find((candidate) => candidate.id === tabId)
      const url = buildTabDeeplink(tabId)
      if (!tab || !url || tearOffPendingRef.current.has(tabId)) return

      tearOffPendingRef.current.add(tabId)
      try {
        const opened = openDetachedTabWindow({
          url,
          title: tab.title,
          point: {
            clientX: event.clientX,
            clientY: event.clientY,
            screenX: event.screenX,
            screenY: event.screenY,
          },
        })
        if (opened && usePanels.getState().tabs.some((candidate) => candidate.id === tabId)) {
          usePanels.getState().closeTab(tabId)
        }
      } finally {
        tearOffPendingRef.current.delete(tabId)
      }
    }

    root.addEventListener('dragstart', onDragStart, true)
    window.addEventListener('dragover', onDragOver, true)
    window.addEventListener('dragleave', onDragLeave, true)
    window.addEventListener('dragend', onDragEnd, true)
    return () => {
      root.removeEventListener('dragstart', onDragStart, true)
      window.removeEventListener('dragover', onDragOver, true)
      window.removeEventListener('dragleave', onDragLeave, true)
      window.removeEventListener('dragend', onDragEnd, true)
      draggingTabIdRef.current = null
      dragLeftViewportRef.current = false
      tearOffPendingRef.current.clear()
    }
  }, [degraded])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && useReviewMaximize.getState().maximizedTabId) {
        useReviewMaximize.getState().exit()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      useReviewMaximize.getState().registerApi(null)
    }
  }, [])

  const maximized = useReviewMaximize((s) => s.maximizedTabId !== null)
  const activeTab = tabs.find((tab) => tab.id === activeId)

  return (
    // data-review-maximized 驱动 index.css 隐藏 dockview 页签条(只在全屏审阅时)。
    <div
      ref={rootRef}
      style={{
        position: 'absolute',
        inset: 0,
        '--omni-tab-title-max-ch': tabTitleMaxLength,
      } as React.CSSProperties & { '--omni-tab-title-max-ch': number }}
      data-review-maximized={maximized ? 'true' : undefined}
      data-tab-layout={tabLayout}
    >
      <SharedEditorSidecar activeTab={activeTab}>
        <DockviewReact
          components={components}
          onReady={onReady}
          defaultTabComponent={ReviewTab}
          watermarkComponent={Welcome as any}
          // 所有页签常驻(renderer=always): 切换页签不再卸载重建, 因此不再"每次切换都加载"。
          // dockview 自己创建的面板(浮动组/布局恢复)也走这个默认档, 不留 onlyWhenVisible 的漏。
          defaultRenderer={PANEL_RENDERER}
          className="dockview-theme-abyss"
          // M3 降级档: 官方 DockviewOptions 禁拖拽拆分/浮组(reactive, updateOptions 热切换);
          // 页签点击切换/右键菜单均不受影响。桌面档两个都是 false, 行为不变。
          disableDnd={degraded}
          disableFloatingGroups={degraded}
        />
        <TabContextMenu />
      </SharedEditorSidecar>
    </div>
  )
}

// 性能(2026-06-06): 用 React.memo 包出口。本组件无 props, 父级(CockpitShell)的高频 state
// (简报/工作流轮询/toast/通知)变更不再级联重渲整个 dockview 编辑区子树; 它对 tabs/activeId
// 的 store 订阅仍会按需重渲, 不受影响。这是 §6.3「避免整树重渲」的低风险落地之一。
export default React.memo(EditorArea)
