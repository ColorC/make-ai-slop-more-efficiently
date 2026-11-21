import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  ChevronLeft,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react'
import './tabSidecar.css'

// v2 (2026-08-16): 坏纪元的 '1' 遗留在不少浏览器里(伴随侧栏默认折叠导致札记入口
// 整体不可见)。键升位让新默认(伴随侧栏展开)生效一次; 用户之后主动收起仍会被记住。
const COLLAPSED_PREF_KEY = 'omni.tabSidecar.collapsed.v2'
// v2 resets the former 380px default once: the companion now has four page
// tabs plus session actions and needs enough room to keep them on one row.
const WIDTH_PREF_KEY = 'omni.tabSidecar.width.v2'
const MIN_WIDTH = 280
// 上限只防误拖出屏; 实际可用宽度由 renderedWidth 的"给主区留 MAIN_MIN_WIDTH"规则决定
// (2026-08-12 用户: 720px 封顶在宽屏上太窄, 札记/材料需要更大的阅读面)。
const MAX_WIDTH = 1600
const MAIN_MIN_WIDTH = 320
const DEFAULT_WIDTH = 520
const WIDE_WIDTH = 600
const AUTO_COLLAPSE_WIDTH = 980
const DRAWER_WIDTH = 720
// 手机档: 宿主宽到连「侧栏最小宽 + 主区最小宽」都放不下时, 并排就是假的
// (实测 390px 屏上主区只剩 47px)。此档改成同一位置上的二选一: 默认只给主区,
// 展开伴随视图时它整幅接管, 左缘留一条返回条把主区换回来。仍在 flex 流里、
// 没有浮层也没有 scrim, 不违反「侧栏不许悬浮盖在主区上」。
const STACKED_WIDTH = MIN_WIDTH + MAIN_MIN_WIDTH

type TabSidecarContextValue = {
  /** 本页签是否真有伴随视图。没有时布局仍然存在(保住 dock 不被重挂), 只是不渲染 aside。 */
  hasSidecar: boolean
  collapsed: boolean
  width: number
  open: () => void
  close: () => void
  toggle: () => void
  requestWide: () => void
  toggleWidth: () => void
}

const TabSidecarContext = createContext<TabSidecarContextValue | null>(null)

function clampWidth(value: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(value)))
}

/** 只读「用户自己收/放过」这一件事; 没有记录时返回 null, 由调用方决定默认。 */
function readCollapsedPref(preferenceKey: string | null): boolean | null {
  if (!preferenceKey) return null
  try {
    const value = window.localStorage.getItem(preferenceKey)
    if (value === '1') return true
    if (value === '0') return false
  } catch { /* privacy mode */ }
  return null
}

function readWidthPref(preferenceKey: string | null, fallback: number): number {
  if (!preferenceKey) return clampWidth(fallback)
  try {
    const value = Number(window.localStorage.getItem(preferenceKey))
    return Number.isFinite(value) && value > 0 ? clampWidth(value) : clampWidth(fallback)
  } catch {
    return clampWidth(fallback)
  }
}

function writeCollapsedPref(preferenceKey: string | null, collapsed: boolean) {
  if (!preferenceKey) return
  try { window.localStorage.setItem(preferenceKey, collapsed ? '1' : '0') } catch { /* privacy mode */ }
}

function writeWidthPref(preferenceKey: string | null, width: number) {
  if (!preferenceKey) return
  try { window.localStorage.setItem(preferenceKey, String(clampWidth(width))) } catch { /* privacy mode */ }
}

export function useTabSidecar(): TabSidecarContextValue | null {
  return useContext(TabSidecarContext)
}

export function SidebarToggleButton({
  side = 'right',
  open,
  onToggle,
  label = '侧栏',
  testId,
  className = '',
}: {
  side?: 'left' | 'right'
  open: boolean
  onToggle: () => void
  label?: string
  testId?: string
  className?: string
}) {
  const Icon = side === 'left'
    ? (open ? PanelLeftClose : PanelLeftOpen)
    : (open ? PanelRightClose : PanelRightOpen)
  const action = open ? '收起' : '展开'
  return (
    <button
      type="button"
      className={`tab-sidecar-toggle ${className}`.trim()}
      title={`${action}${label}`}
      aria-label={`${action}${label}`}
      aria-expanded={open}
      data-testid={testId}
      onClick={onToggle}
    >
      <Icon size={15} strokeWidth={1.8} aria-hidden />
    </button>
  )
}

export function TabSidecarToggleButton({
  label,
  testId = 'tab-sidecar-toggle',
  showWhen = 'always',
  className,
}: {
  label: string
  testId?: string
  showWhen?: 'always' | 'collapsed' | 'open'
  className?: string
}) {
  const sidecar = useTabSidecar()
  if (!sidecar || !sidecar.hasSidecar) return null
  if (showWhen === 'collapsed' && !sidecar.collapsed) return null
  if (showWhen === 'open' && sidecar.collapsed) return null
  return (
    <SidebarToggleButton
      side="right"
      open={!sidecar.collapsed}
      onToggle={sidecar.toggle}
      label={label}
      testId={testId}
      className={className}
    />
  )
}

export default function TabSidecarLayout({
  children,
  sidecar,
  defaultCollapsed = null,
  defaultWidth = DEFAULT_WIDTH,
  collapsedPreferenceKey = COLLAPSED_PREF_KEY,
  widthPreferenceKey = WIDTH_PREF_KEY,
  resetKey,
}: {
  children: React.ReactNode
  /** null = 本页签没有伴随视图。布局照旧渲染(见 EditorArea 的说明), 只是不出 aside。 */
  sidecar: React.ReactNode
  defaultCollapsed?: boolean | null
  defaultWidth?: number
  collapsedPreferenceKey?: string | null
  widthPreferenceKey?: string | null
  resetKey?: string
}) {
  const hasSidecar = sidecar !== null && sidecar !== undefined && sidecar !== false
  const rootRef = useRef<HTMLDivElement>(null)
  const [explicitCollapsed, setExplicitCollapsed] = useState<boolean | null>(() => (
    readCollapsedPref(collapsedPreferenceKey)
  ))
  const [width, setWidth] = useState(() => readWidthPref(widthPreferenceKey, defaultWidth))
  const [hostWidth, setHostWidth] = useState<number>(() => (
    typeof window === 'undefined' ? 1440 : window.innerWidth
  ))

  useEffect(() => {
    setExplicitCollapsed(readCollapsedPref(collapsedPreferenceKey))
    setWidth(readWidthPref(widthPreferenceKey, defaultWidth))
  }, [collapsedPreferenceKey, defaultCollapsed, defaultWidth, resetKey, widthPreferenceKey])

  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    const update = () => {
      const nextWidth = root.getBoundingClientRect().width
      // jsdom 和尚未完成布局的 Dockview 面板会短暂返回 0；不要把它误判为窄屏并自动收起。
      if (nextWidth > 0) setHostWidth(nextWidth)
    }
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const observer = new ResizeObserver(update)
    observer.observe(root)
    return () => observer.disconnect()
  }, [])

  const stacked = hostWidth < STACKED_WIDTH
  // 优先级: 用户自己收放过 > 本页签声明的默认 > 宿主宽度。手机档把「页签声明默认展开」
  // (CLI 的札记侧栏)也压回收起 —— 那条默认是给宽屏定的, 在手机上等于把主区挤没。
  const collapsed = !hasSidecar || (
    explicitCollapsed ?? (stacked ? true : (defaultCollapsed ?? hostWidth < AUTO_COLLAPSE_WIDTH))
  )
  const setCollapsed = useCallback((next: boolean) => {
    setExplicitCollapsed(next)
    writeCollapsedPref(collapsedPreferenceKey, next)
  }, [collapsedPreferenceKey])
  const setSidecarWidth = useCallback((next: number) => {
    const clamped = clampWidth(next)
    setWidth(clamped)
    writeWidthPref(widthPreferenceKey, clamped)
  }, [widthPreferenceKey])
  const controls = useMemo<TabSidecarContextValue>(() => ({
    hasSidecar,
    collapsed,
    width,
    open: () => setCollapsed(false),
    close: () => setCollapsed(true),
    toggle: () => setCollapsed(!collapsed),
    requestWide: () => setSidecarWidth(WIDE_WIDTH),
    toggleWidth: () => setSidecarWidth(width >= (defaultWidth + WIDE_WIDTH) / 2 ? defaultWidth : WIDE_WIDTH),
  }), [collapsed, defaultWidth, hasSidecar, setCollapsed, setSidecarWidth, width])

  const beginResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || event.isPrimary === false) return
    event.preventDefault()
    event.stopPropagation()
    const handle = event.currentTarget
    const pointerId = event.pointerId
    const startX = event.clientX
    const startWidth = width
    let lastX = startX
    let animationFrame = 0

    // The notes page is an iframe. Without explicit capture, moving a finger
    // from the narrow separator into that iframe can strand the parent page
    // after only a few pixels. Older WebViews may not expose capture; the
    // window listeners below remain the fallback.
    try { handle.setPointerCapture(pointerId) } catch { /* old WebView */ }

    const render = () => {
      animationFrame = 0
      setWidth(clampWidth(startWidth + startX - lastX))
    }
    const move = (next: PointerEvent) => {
      if (next.pointerId !== pointerId) return
      lastX = next.clientX
      if (!animationFrame) animationFrame = window.requestAnimationFrame(render)
    }
    const finish = (next: PointerEvent) => {
      if (next.pointerId !== pointerId) return
      if (Number.isFinite(next.clientX)) lastX = next.clientX
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      if (animationFrame) window.cancelAnimationFrame(animationFrame)
      try { handle.releasePointerCapture(pointerId) } catch { /* already released */ }
      setSidecarWidth(startWidth + startX - lastX)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
  }

  // 2026-08-21 用户裁决: 伴随侧栏永远占布局位置, 任何宽度都不许悬浮盖在主区上。
  // drawer 仅作窄屏宽度钳制(0.88 上限), 不再切换成覆盖层; data-sidecar-drawer 保留输出但不产生覆盖样式。
  const drawer = hostWidth < DRAWER_WIDTH
  const renderedWidth = drawer
    ? Math.min(width, Math.max(MIN_WIDTH, hostWidth * 0.88))
    : Math.min(width, Math.max(MIN_WIDTH, hostWidth - MAIN_MIN_WIDTH))

  return (
    <TabSidecarContext.Provider value={controls}>
      <div
        ref={rootRef}
        className="tab-sidecar-layout"
        data-testid="tab-sidecar-layout"
        data-sidecar-collapsed={collapsed ? '1' : '0'}
        data-sidecar-present={hasSidecar ? '1' : '0'}
        data-sidecar-drawer={drawer ? '1' : '0'}
        data-sidecar-stacked={stacked ? '1' : '0'}
      >
        <div className="tab-sidecar-main">{children}</div>
        {!collapsed && (
          <aside
            className="tab-sidecar-aside"
            style={stacked ? undefined : { width: renderedWidth }}
            data-testid="tab-sidecar"
          >
            {stacked ? (
              // 手机档主区被换走, 终端列上的复展开手柄跟着不可见 —— 返回入口必须
              // 由侧栏自己带, 否则展开一次就出不来了。
              <button
                type="button"
                className="tab-sidecar-back"
                aria-label="返回主区"
                title="返回主区"
                data-testid="tab-sidecar-back"
                onClick={controls.close}
              >
                <ChevronLeft size={16} strokeWidth={1.8} aria-hidden />
              </button>
            ) : (
              <div
                className="tab-sidecar-resize"
                role="separator"
                aria-label="调整侧栏宽度"
                aria-orientation="vertical"
                onPointerDown={beginResize}
                onDoubleClick={controls.toggleWidth}
                data-testid="tab-sidecar-resize"
              />
            )}
            {sidecar}
          </aside>
        )}
      </div>
    </TabSidecarContext.Provider>
  )
}
