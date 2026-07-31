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
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react'
import './tabSidecar.css'

const COLLAPSED_PREF_KEY = 'omni.tabSidecar.collapsed'
// v2 resets the former 380px default once: the companion now has four page
// tabs plus session actions and needs enough room to keep them on one row.
const WIDTH_PREF_KEY = 'omni.tabSidecar.width.v2'
const MIN_WIDTH = 280
const MAX_WIDTH = 720
const DEFAULT_WIDTH = 520
const WIDE_WIDTH = 680
const AUTO_COLLAPSE_WIDTH = 980
const DRAWER_WIDTH = 720

type TabSidecarContextValue = {
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

function readCollapsedPref(): boolean | null {
  try {
    const value = window.localStorage.getItem(COLLAPSED_PREF_KEY)
    if (value === '1') return true
    if (value === '0') return false
  } catch { /* privacy mode */ }
  return null
}

function readWidthPref(): number {
  try {
    const value = Number(window.localStorage.getItem(WIDTH_PREF_KEY))
    return Number.isFinite(value) && value > 0 ? clampWidth(value) : DEFAULT_WIDTH
  } catch {
    return DEFAULT_WIDTH
  }
}

function writeCollapsedPref(collapsed: boolean) {
  try { window.localStorage.setItem(COLLAPSED_PREF_KEY, collapsed ? '1' : '0') } catch { /* privacy mode */ }
}

function writeWidthPref(width: number) {
  try { window.localStorage.setItem(WIDTH_PREF_KEY, String(clampWidth(width))) } catch { /* privacy mode */ }
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
  if (!sidecar) return null
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
}: {
  children: React.ReactNode
  sidecar: React.ReactNode
}) {
  const rootRef = useRef<HTMLDivElement>(null)
  const [explicitCollapsed, setExplicitCollapsed] = useState<boolean | null>(readCollapsedPref)
  const [width, setWidth] = useState(readWidthPref)
  const [hostWidth, setHostWidth] = useState<number>(() => (
    typeof window === 'undefined' ? 1440 : window.innerWidth
  ))

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

  const collapsed = explicitCollapsed ?? hostWidth < AUTO_COLLAPSE_WIDTH
  const setCollapsed = useCallback((next: boolean) => {
    setExplicitCollapsed(next)
    writeCollapsedPref(next)
  }, [])
  const setSidecarWidth = useCallback((next: number) => {
    const clamped = clampWidth(next)
    setWidth(clamped)
    writeWidthPref(clamped)
  }, [])
  const controls = useMemo<TabSidecarContextValue>(() => ({
    collapsed,
    width,
    open: () => setCollapsed(false),
    close: () => setCollapsed(true),
    toggle: () => setCollapsed(!collapsed),
    requestWide: () => setSidecarWidth(WIDE_WIDTH),
    toggleWidth: () => setSidecarWidth(width >= (DEFAULT_WIDTH + WIDE_WIDTH) / 2 ? DEFAULT_WIDTH : WIDE_WIDTH),
  }), [collapsed, setCollapsed, setSidecarWidth, width])

  const beginResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    const startX = event.clientX
    const startWidth = width
    const move = (next: PointerEvent) => {
      setWidth(clampWidth(startWidth + startX - next.clientX))
    }
    const finish = (next: PointerEvent) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      setSidecarWidth(startWidth + startX - next.clientX)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
  }

  const drawer = hostWidth < DRAWER_WIDTH
  const renderedWidth = drawer
    ? Math.min(width, Math.max(MIN_WIDTH, hostWidth * 0.88))
    : Math.min(width, Math.max(MIN_WIDTH, hostWidth * 0.72))

  return (
    <TabSidecarContext.Provider value={controls}>
      <div
        ref={rootRef}
        className="tab-sidecar-layout"
        data-testid="tab-sidecar-layout"
        data-sidecar-collapsed={collapsed ? '1' : '0'}
        data-sidecar-drawer={drawer ? '1' : '0'}
      >
        <div className="tab-sidecar-main">{children}</div>
        {!collapsed && drawer && (
          <button
            type="button"
            className="tab-sidecar-scrim"
            aria-label="收起侧栏"
            onClick={controls.close}
          />
        )}
        {!collapsed && (
          <aside
            className="tab-sidecar-aside"
            style={{ width: renderedWidth }}
            data-testid="tab-sidecar"
          >
            <div
              className="tab-sidecar-resize"
              role="separator"
              aria-label="调整侧栏宽度"
              aria-orientation="vertical"
              onPointerDown={beginResize}
              onDoubleClick={controls.toggleWidth}
              data-testid="tab-sidecar-resize"
            />
            {sidecar}
          </aside>
        )}
      </div>
    </TabSidecarContext.Provider>
  )
}
