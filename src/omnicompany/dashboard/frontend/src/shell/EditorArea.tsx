import React, { useEffect, useRef, useState } from 'react'
import {
  DockviewReact,
  DockviewDefaultTab,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
  type IDockviewPanelHeaderProps,
} from 'dockview'
import { Link2, Check, LayoutPanelLeft } from 'lucide-react'
import { CONTROLLER_TAB_ID, usePanels, type DockDirection, type OpenedTab } from '../stores/panelsStore'
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
import { installTouchTabStripScroller } from '../lib/touchTabStripScroller'
import { VscodeIcon } from '../components/VscodeIcon'
import Tooltip from '../shared/view/ui/Tooltip'
import { useBreakpoint, useMediaQuery } from './useBreakpoint'
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

export function compactCliTabTitle(title: string, maxLength = 30): string {
  const normalized = title.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`
}

// 右键页签 → 打开"最大化审阅"菜单。包一层默认页签, 把 onContextMenu 接到页签根元素上;
// props.api 即该面板的句柄(id / setActive / maximize)。
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
  const liveTitle = statusSnapshot.metas[sessionId]?.title?.trim()

  useEffect(() => {
    if (!liveTitle) return
    const compactTitle = compactCliTabTitle(liveTitle)
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
  }, [liveTitle, props.api, props.params.tab?.id])

  return (
    <div
      className="omni-dock-tab omni-cc-session-tab"
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
  return <DockviewDefaultTab {...props} onContextMenu={(e) => openTabContextMenu(e, props)} />
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
  const left = Math.min(tabMenu.x, (typeof window !== 'undefined' ? window.innerWidth : 1200) - 184)
  const top = Math.min(tabMenu.y, (typeof window !== 'undefined' ? window.innerHeight : 800) - 80)
  return (
    <div
      style={menuStyles.backdrop}
      onClick={close}
      onContextMenu={(e) => {
        e.preventDefault()
        close()
      }}
    >
      <div style={{ ...menuStyles.menu, left, top }} onClick={(e) => e.stopPropagation()}>
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

function SessionCompanionSidecar({ entity }: { entity: any }) {
  const sidecar = useTabSidecar()
  return (
    <SessionCompanion
      sessionId={entity.id}
      alive={entity.alive}
      mode="embedded"
      headerActions={(
        <TabSidecarToggleButton
          label="伴随视图"
          showWhen="open"
          testId="session-ctx-toggle"
        />
      )}
      onHeaderDoubleClick={sidecar?.toggleWidth}
      onRequestWide={sidecar?.requestWide}
    />
  )
}

function ReviewCommentsBridge({ tab }: { tab: OpenedTab }) {
  const sidecar = useTabSidecar()
  const setActiveMaterial = useReviewActive((state) => state.setActiveMaterial)

  useEffect(() => {
    if (tab.ref.type === 'review_material') {
      setActiveMaterial(String(tab.ref.id), 'local')
    }
  }, [setActiveMaterial, tab.ref.id, tab.ref.type])

  useEffect(() => {
    const openComments = (event: Event) => {
      const materialId = (event as CustomEvent<{ materialId?: string }>).detail?.materialId
      if (!materialId) return
      if (tab.ref.type === 'review_material' && String(tab.ref.id) !== materialId) return
      setActiveMaterial(materialId, 'local')
      sidecar?.open()
    }
    window.addEventListener(REVIEW_COMMENTS_OPEN_EVENT, openComments)
    return () => window.removeEventListener(REVIEW_COMMENTS_OPEN_EVENT, openComments)
  }, [setActiveMaterial, sidecar, tab.ref.id, tab.ref.type])

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

const EntityPanel: React.FC<IDockviewPanelProps<{ tab: OpenedTab }>> = (props) => {
  const tab = props.params.tab
  const reg = registry.get(tab.ref.type)
  if (!reg) {
    return <div style={{ padding: 16, color: 'var(--fp-err)' }}>未注册的实体类型: {tab.ref.type}</div>
  }
  const Editor = reg.renderer.Editor as React.ComponentType<{ entity: any; facet?: string }>
  const [entity, setEntity] = React.useState<any>(null)
  const [error, setError] = React.useState<string | null>(null)
  React.useEffect(() => {
    let alive = true
    setError(null)
    setEntity(null)
    reg.resolver.fetch(tab.ref.id).then((resolved) => {
      if (alive) setEntity(resolved)
    }).catch((e) => {
      if (alive) setError(String(e))
    })
    return () => { alive = false }
  }, [reg.resolver, tab.ref.id, tab.ref.type])

  React.useEffect(() => {
    if (!entity) return
    const resolvedTitle = tab.ref.type === 'cc_session'
      ? compactCliTabTitle(entity.title)
      : entity.title
    if (resolvedTitle && resolvedTitle !== tab.title) {
      props.api.setTitle(resolvedTitle)
      usePanels.getState().renameTab(tab.id, resolvedTitle)
    }
  }, [entity, props.api, tab.id, tab.ref.type, tab.title])

  if (error) return <div style={{ padding: 16, color: 'var(--fp-err)' }}>{error}</div>
  // 实体解析/懒 chunk 下载期间的等待面: 所有页签共用这一处, 用骨架扫光而不是一行灰字,
  // 让"正在加载"始终可见(2026-07-17 用户: 加载过程要看得见)。
  if (!entity) return <PanelSkeleton />
  // Suspense 兜住懒加载的 Editor(切到该 tab 才下载对应 chunk); 非懒加载 Editor 同步渲染不受影响。
  const editor = <Editor entity={entity} facet={tab.facet} />
  return (
    <React.Suspense fallback={<PanelSkeleton />}>
      {tab.ref.type === 'cc_session' ? (
        <TabSidecarLayout sidecar={<SessionCompanionSidecar entity={entity} />}>
          {editor}
        </TabSidecarLayout>
      ) : tab.ref.type === 'review_material' || tab.ref.type === 'review_queue' ? (
        <TabSidecarLayout sidecar={<ReviewCommentsSidecar />}>
          <ReviewCommentsBridge tab={tab} />
          {editor}
        </TabSidecarLayout>
      ) : editor}
    </React.Suspense>
  )
}

const components = { entity: EntityPanel }
type DockPanel = NonNullable<ReturnType<DockviewApi['getPanel']>>

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
  const tabs = usePanels((s) => s.tabs)
  const activeId = usePanels((s) => s.activeId)
  const closeTab = usePanels((s) => s.closeTab)
  const activate = usePanels((s) => s.activate)
  const lastSyncedTabsRef = useRef<string>('')
  const [readyVersion, setReadyVersion] = useState(0)
  // M3 窄屏降级: 非桌面断点或 coarse 指针 → 单面板组(页签切换代替平铺) + 官方 disableDnd/
  // disableFloatingGroups 禁拖拽拆分(不禁页签点击切换; 不用 CSS pointer-events 粗暴全局禁)。
  // 桌面档行为完全不变(matchMedia 不可用的测试环境也走桌面档)。
  const bp = useBreakpoint()
  const coarsePointer = useMediaQuery('(pointer: coarse)')
  const degraded = bp !== 'desktop' || coarsePointer

  const syncPanels = (api: DockviewApi, nextTabs: OpenedTab[], nextActiveId: string | null) => {
    const sig = (degraded ? 'S|' : '') + nextTabs.map((t) => `${t.id}:${placementSignature(t)}`).join('|') + '#' + nextActiveId
    if (sig === lastSyncedTabsRef.current) return
    lastSyncedTabsRef.current = sig

    const existing = new Set(api.panels.map((p) => p.id))
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
      const anchor = !referencePanel && nextActiveId && tab.id !== nextActiveId ? api.getPanel(nextActiveId) : undefined
      const desiredIndex = nextTabs.findIndex((t) => t.id === tab.id)
      api.addPanel({
        id: tab.id,
        component: 'entity',
        title: tab.ref.type === 'cc_session' ? compactCliTabTitle(tab.title) : tab.title,
        params: { tab },
        // 性能(2026-06-06): 所有 tab 一律 'onlyWhenVisible' —— 切走即卸载, 不让打开过的页
        // (审阅/材料/plan/会话)常驻后台渲染/轮询/连 WS, 这是"切页很卡 + 做啥都卡"的主因。
        // (2026-07 删总控 'always' 特判: 其存在理由已过期 —— 总控对话已迁 chatui,
        //  controller 页签只剩 ChatuiHandoff 落点卡, 无 WS/滚动/运行态需要保留。)
        renderer: tab.id === CONTROLLER_TAB_ID ? 'always' : 'onlyWhenVisible',
        // 中键后台打开: 新增的非活跃 tab 用 inactive 挂载, 不抢焦点(activeId 未变)。
        // nextActiveId 为空时强制第一个面板激活建组, 否则每个 inactive 面板又会各建一组。
        inactive: nextActiveId ? tab.id !== nextActiveId : tab !== orderedAdds[0],
        ...(referencePanel
          ? { position: { referencePanel, direction: tab.placement?.direction } }
          : anchor && desiredIndex > 0
            ? { position: { referencePanel: anchor, index: desiredIndex } }
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
  }, [tabs, activeId, readyVersion, degraded])

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

  return (
    // data-review-maximized 驱动 index.css 隐藏 dockview 页签条(只在全屏审阅时)。
    <div ref={rootRef} style={{ position: 'absolute', inset: 0 }} data-review-maximized={maximized ? 'true' : undefined}>
      <DockviewReact
        components={components}
        onReady={onReady}
        defaultTabComponent={ReviewTab}
        watermarkComponent={Welcome as any}
        // 全tab默认 'onlyWhenVisible'(切走即卸载, 省后台开销); 原总控 'always' 特判已随
        // 总控对话迁 chatui 删除(2026-07)。详见 addPanel 处注释。
        className="dockview-theme-abyss"
        // M3 降级档: 官方 DockviewOptions 禁拖拽拆分/浮组(reactive, updateOptions 热切换);
        // 页签点击切换/右键菜单均不受影响。桌面档两个都是 false, 行为不变。
        disableDnd={degraded}
        disableFloatingGroups={degraded}
      />
      <TabContextMenu />
    </div>
  )
}

// 性能(2026-06-06): 用 React.memo 包出口。本组件无 props, 父级(CockpitShell)的高频 state
// (简报/工作流轮询/toast/通知)变更不再级联重渲整个 dockview 编辑区子树; 它对 tabs/activeId
// 的 store 订阅仍会按需重渲, 不受影响。这是 §6.3「避免整树重渲」的低风险落地之一。
export default React.memo(EditorArea)
