import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  DESKTOP_WINDOW_STATE_EVENT,
  desktopHostBridge,
  type DesktopWindowState,
} from '../lib/workspaceNavigation'
import { desktopWebviewSession, ensureDesktopSession } from '../lib/desktopSession'

export interface DesktopHostMode extends DesktopWindowState {
  available: boolean
  toggleFullscreen: () => Promise<void>
}

const BROWSER_STATE: DesktopWindowState = {
  fullscreen: false,
  maximized: false,
  titlebarHeight: 0,
  fullscreenGuardHotEdge: 0,
}

function isWindowState(value: unknown): value is DesktopWindowState {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<DesktopWindowState>
  return typeof candidate.fullscreen === 'boolean'
    && typeof candidate.maximized === 'boolean'
    && Number.isFinite(candidate.titlebarHeight)
    && Number.isFinite(candidate.fullscreenGuardHotEdge)
}

/** Single Web Core entry for desktop-only presentation and window capabilities. */
export function useDesktopHostMode(): DesktopHostMode {
  const bridge = useMemo(() => desktopHostBridge(), [])
  const [windowState, setWindowState] = useState<DesktopWindowState>(BROWSER_STATE)

  useEffect(() => {
    if (!bridge) return
    const root = document.documentElement
    root.dataset.omnicompanyHost = 'desktop'
    root.dataset.omnicompanySession = 'pending'
    void ensureDesktopSession()
      .then(async (ok) => {
        // mint 成功后把 10 年期 webview_session token 交给宿主持久化;
        // 宿主下次启动会在任何导航前把它注入回 cookie jar(零交互持久鉴权)。
        if (!ok) throw new Error('desktop session mint rejected')
        const session = await desktopWebviewSession()
        if (session && bridge.storeDesktopSession) {
          await bridge.storeDesktopSession(session.token, session.expiresAt)
        }
        root.dataset.omnicompanySession = 'ready'
      })
      .catch((cause) => {
        // 不再静默吞掉: 会话 mint 失败时任何网关保护面(含画布)都会开始要鉴权。
        root.dataset.omnicompanySession = 'failed'
        bridge.logClientEvent?.('warn', 'desktop.session_mint_failed',
          cause instanceof Error ? cause.message : String(cause))
      })

    const apply = (next: DesktopWindowState) => {
      setWindowState(next)
      root.dataset.omnicompanyFullscreen = next.fullscreen ? 'true' : 'false'
      root.style.setProperty('--omnicompany-desktop-titlebar-height', `${next.titlebarHeight}px`)
      root.style.setProperty('--omnicompany-fullscreen-guard-edge', `${next.fullscreenGuardHotEdge}px`)
    }
    const onWindowState = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail
      if (isWindowState(detail)) apply(detail)
    }

    window.addEventListener(DESKTOP_WINDOW_STATE_EVENT, onWindowState)
    void bridge.windowState?.().then(apply).catch(() => {})
    return () => {
      window.removeEventListener(DESKTOP_WINDOW_STATE_EVENT, onWindowState)
      delete root.dataset.omnicompanyHost
      delete root.dataset.omnicompanyFullscreen
      delete root.dataset.omnicompanySession
      root.style.removeProperty('--omnicompany-desktop-titlebar-height')
      root.style.removeProperty('--omnicompany-fullscreen-guard-edge')
    }
  }, [bridge])

  const toggleFullscreen = useCallback(async () => {
    if (!bridge?.toggleFullscreen) return
    const fullscreen = await bridge.toggleFullscreen()
    setWindowState((current) => ({
      ...current,
      fullscreen,
    }))
  }, [bridge])

  return {
    ...windowState,
    available: Boolean(bridge?.toggleFullscreen),
    toggleFullscreen,
  }
}
