import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ExternalLink, RotateCw } from 'lucide-react'
import {
  browserEmbedUrl,
  deferredWebTabWindow,
  encodeWebPageId,
  isSameOriginWebUrl,
  normalizeWebTabUrl,
  openWebTab,
  WEB_LINK_CONTEXT_MENU_EVENT,
  type WebProfile,
} from '../../lib/webTabHost'
import { desktopWebviewSessionForUrl } from '../../lib/desktopSession'
import {
  nativeWebviewIsOccluded,
  observeNativeWebviewOcclusion,
} from '../../lib/nativeWebviewOcclusion'
import {
  DESKTOP_WINDOW_STATE_EVENT,
  EXTERNAL_WEBVIEW_URL_EVENT,
  nativeWebviewHostBridge,
  nativeWebviewId,
  openNativeBrowserUrl,
  type NativeWebviewBounds,
} from '../../lib/workspaceNavigation'
import { usePanels } from '../../stores/panelsStore'

export interface WebPageTarget {
  title: string
  url: string
  profile?: WebProfile
}

/** A chrome-free page surface. Dashboard's Dockview tab is the only visible tab layer. */
const WebPagePanel: React.FC<{
  target: WebPageTarget
  panelVisible?: boolean
  /** Stable identity when the surface is embedded outside a standalone web_review tab. */
  instanceId?: string
  /** Material views already own a mode bar, so they can reuse the carrier without a second toolbar. */
  showToolbar?: boolean
}> = ({ target, panelVisible = true, instanceId, showToolbar = true }) => {
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const nativeSurfaceRef = useRef<HTMLDivElement | null>(null)
  const panelVisibleRef = useRef(panelVisible)
  const nativeVisibilitySyncRef = useRef<(() => void) | null>(null)
  panelVisibleRef.current = panelVisible
  const [reloadKey, setReloadKey] = useState(0)
  const [nativeError, setNativeError] = useState<string | null>(null)
  const [, setNativeHostRevision] = useState(0)
  const profile = target.profile === 'private' || target.profile === 'automation' ? target.profile : 'human'
  /**
   * 每次渲染都按**当前页面地址**重新规一次目标 URL。
   *
   * 页签快照(sessionStorage)存的是开页签那一刻算出来的**绝对** URL。一旦那次算错
   * (历史上 `normalizeWebTabUrl` 只换 hostname、不换协议和端口, 于是回环地址被算成
   * `http://<局域网IP>:8210/...`), 这个页签就**永久废掉** —— 在 https 的 Dashboard 里
   * 它是混合内容, Chrome 直接拦掉且根本不发请求, 表现为"打不开", 而且换个新链接手动开
   * 能好、下次从旧页签进又坏。用户为此反复报障。
   *
   * 所以规一化不能只做在"开页签"那一刻: 渲染时再规一次, 旧快照才能自愈。
   */
  const resolvedUrl = useMemo(
    () => normalizeWebTabUrl(target.url) ?? target.url,
    [target.url],
  )
  // 地址栏跟随真实页面: native webview 走 EXTERNAL_WEBVIEW_URL_EVENT 回传,
  // 同源 iframe 在 onLoad 时回读 contentWindow.location。
  const [currentUrl, setCurrentUrl] = useState(resolvedUrl)
  const [addressDraft, setAddressDraft] = useState(resolvedUrl)
  const [frameUrlOverride, setFrameUrlOverride] = useState<string | null>(null)
  const addressFocusedRef = useRef(false)
  useEffect(() => {
    setCurrentUrl(resolvedUrl)
    setFrameUrlOverride(null)
  }, [resolvedUrl])
  useEffect(() => {
    if (!addressFocusedRef.current) setAddressDraft(currentUrl)
  }, [currentUrl])
  const tabId = useMemo(
    () => instanceId || `web_review:${encodeWebPageId(target.url, profile)}`,
    [instanceId, profile, target.url],
  )
  const standaloneTab = !instanceId
  const returnTo = usePanels((state) => state.tabs.find((tab) => tab.id === tabId)?.returnTo)
  const returnFromTab = usePanels((state) => state.returnFromTab)
  // Only same-origin pages belong in an iframe. A LAN URL on another port is
  // workspace-internal for routing, but it has its own auth/CSP boundary and
  // must use the desktop native WebView or the browser's new-window fallback.
  const sameOrigin = isSameOriginWebUrl(resolvedUrl)
  const host = nativeWebviewHostBridge()
  const native = !sameOrigin
    && Boolean(host?.openExternalWebview && host.updateExternalWebview && host.closeExternalWebview)
  const adaptedEmbedUrl = !sameOrigin && !native ? browserEmbedUrl(resolvedUrl) : null
  const frameUrl = frameUrlOverride ?? (sameOrigin ? resolvedUrl : adaptedEmbedUrl)
  const externalBrowserOnly = !sameOrigin && !native && !frameUrl
  const nativeId = useMemo(() => nativeWebviewId(tabId), [tabId])

  useEffect(() => {
    const refreshNativeHost = () => setNativeHostRevision((value) => value + 1)
    window.addEventListener('omnicompany:lofa-bridge-state', refreshNativeHost)
    return () => window.removeEventListener('omnicompany:lofa-bridge-state', refreshNativeHost)
  }, [])

  useEffect(() => {
    if (!native || !host?.openExternalWebview || !host.updateExternalWebview || !host.closeExternalWebview) return
    const surface = nativeSurfaceRef.current
    if (!surface) return

    let disposed = false
    let opened = false
    let frame = 0
    let lastSignature = ''
    let lastReportedVisible: boolean | null = null

    const measure = (): { bounds: NativeWebviewBounds; visible: boolean } => {
      const rect = surface.getBoundingClientRect()
      const visible = panelVisibleRef.current
        && document.visibilityState === 'visible'
        && !nativeWebviewIsOccluded(surface)
        && rect.width >= 1
        && rect.height >= 1
        && rect.bottom > 0
        && rect.right > 0
        && rect.top < window.innerHeight
        && rect.left < window.innerWidth
      return {
        bounds: {
          x: Math.max(0, rect.left),
          y: Math.max(0, rect.top),
          width: Math.max(1, rect.width),
          height: Math.max(1, rect.height),
        },
        visible,
      }
    }

    const sync = () => {
      frame = 0
      if (!opened || disposed) return
      const measured = measure()
      const signature = JSON.stringify(measured)
      if (signature === lastSignature) return
      lastSignature = signature
      lastReportedVisible = measured.visible
      void host.updateExternalWebview?.({ id: nativeId, ...measured }).catch((error) => {
        if (!disposed) setNativeError(String(error))
      })
    }
    const scheduleSync = () => {
      // 页签常驻(2026-08-07): 所有网页页签的这套监听都活着。已经上报过"隐藏"的面板不必再为
      // 每一次滚动/尺寸变化排一帧 —— 否则开 N 个网页页签时, 随便滚一下就是 N 次
      // getBoundingClientRect + 序列化, 白花的钱随页签数线性涨。
      if (!panelVisibleRef.current && lastReportedVisible === false) return
      if (!frame) frame = window.requestAnimationFrame(sync)
    }
    const scheduleForcedSync = () => {
      // The native Dashboard child moves between y=38 and y=0 when fullscreen
      // changes. Its DOM-relative rectangle can remain identical, so bypass the
      // signature cache and let Rust translate against the new child origin.
      lastSignature = ''
      lastReportedVisible = null
      scheduleSync()
    }

    nativeVisibilitySyncRef.current = scheduleForcedSync
    const stopOcclusionObserver = observeNativeWebviewOcclusion(surface, scheduleForcedSync)

    const resizeObserver = new ResizeObserver(scheduleSync)
    resizeObserver.observe(surface)
    window.addEventListener('resize', scheduleSync)
    window.addEventListener('scroll', scheduleSync, true)
    document.addEventListener('visibilitychange', scheduleSync)
    window.addEventListener(DESKTOP_WINDOW_STATE_EVENT, scheduleForcedSync)

    const initial = measure()
    setNativeError(null)
    void desktopWebviewSessionForUrl(target.url).catch(() => null).then((webviewSession) => {
      if (disposed) return
      return host.openExternalWebview?.({
        id: nativeId,
        url: target.url,
        profile,
        ...initial,
        ...(webviewSession ? { webviewSession } : {}),
      })
    }).then((label) => {
      if (label == null) return
      opened = true
      if (disposed) {
        void host.closeExternalWebview?.(nativeId)
        return
      }
      scheduleSync()
    }).catch((error) => {
      if (!disposed) setNativeError(String(error))
    })

    return () => {
      disposed = true
      if (frame) window.cancelAnimationFrame(frame)
      resizeObserver.disconnect()
      stopOcclusionObserver()
      window.removeEventListener('resize', scheduleSync)
      window.removeEventListener('scroll', scheduleSync, true)
      document.removeEventListener('visibilitychange', scheduleSync)
      window.removeEventListener(DESKTOP_WINDOW_STATE_EVENT, scheduleForcedSync)
      if (nativeVisibilitySyncRef.current === scheduleForcedSync) nativeVisibilitySyncRef.current = null
      if (opened) void host.closeExternalWebview?.(nativeId)
    }
  }, [host, native, nativeId, profile, target.url])

  useEffect(() => {
    nativeVisibilitySyncRef.current?.()
  }, [panelVisible])

  useEffect(() => {
    if (!native) return
    const onExternalUrlChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ id?: string; url?: string }>).detail
      if (!detail || detail.id !== nativeId || typeof detail.url !== 'string') return
      setCurrentUrl(detail.url)
    }
    window.addEventListener(EXTERNAL_WEBVIEW_URL_EVENT, onExternalUrlChanged)
    return () => window.removeEventListener(EXTERNAL_WEBVIEW_URL_EVENT, onExternalUrlChanged)
  }, [native, nativeId])

  const reload = useCallback(() => {
    if (native && host?.reloadExternalWebview) {
      void host.reloadExternalWebview(nativeId).catch((error) => setNativeError(String(error)))
      return
    }
    setReloadKey((value) => value + 1)
  }, [host, native, nativeId])

  const commitAddress = useCallback((raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed) {
      setAddressDraft(currentUrl)
      return
    }
    const candidate = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed) ? trimmed : `https://${trimmed}`
    if (native && host?.navigateExternalWebview) {
      void host.navigateExternalWebview(nativeId, candidate).catch((error) => setNativeError(String(error)))
      return
    }
    if (sameOrigin) {
      const normalized = normalizeWebTabUrl(candidate) ?? candidate
      if (isSameOriginWebUrl(normalized)) {
        setFrameUrlOverride(normalized)
        setCurrentUrl(normalized)
        return
      }
    }
    openNativeBrowserUrl(candidate)
  }, [host, native, nativeId, sameOrigin, currentUrl])

  const attachSameOriginBridge = useCallback(() => {
    const frame = frameRef.current
    if (!frame) return
    try {
      const doc = frame.contentDocument
      const child = frame.contentWindow
      if (!doc || !child) return

      // URL classification prevents known Dashboard URLs from being framed. This
      // DOM guard catches aliases/redirects that only reveal their identity after load.
      const nestedDashboard = doc.documentElement.dataset.omniDashboardShell === '1'
        || doc.querySelector('[data-testid="cockpit-shell"]') !== null
      if (nestedDashboard) {
        const opened = openWebTab(child.location.href, target.title, target.url, { allowExternal: true })
        const current = `web_review:${encodeWebPageId(target.url)}`
        if (opened && opened !== current) usePanels.getState().closeTab(current)
        return
      }

      const frameLocation = child.location.href
      if (frameLocation && frameLocation !== 'about:blank') setCurrentUrl(frameLocation)

      if (doc.documentElement.dataset.omniNestedWebTabBridge === '1') return
      doc.documentElement.dataset.omniNestedWebTabBridge = '1'

      doc.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null
        const anchor = element?.closest('a[target="_blank"]') as HTMLAnchorElement | null
        if (!anchor || !anchor.href || anchor.hasAttribute('download')) return
        if (!openWebTab(anchor.href, anchor.textContent || '', target.url, { allowExternal: true })) return
        event.preventDefault()
      }, true)

      doc.addEventListener('contextmenu', (event) => {
        const element = event.target instanceof Element ? event.target : null
        const anchor = element?.closest('a[href]') as HTMLAnchorElement | null
        if (!anchor || !anchor.href || anchor.hasAttribute('download')) return
        event.preventDefault()
        const bounds = frame.getBoundingClientRect()
        document.dispatchEvent(new CustomEvent(WEB_LINK_CONTEXT_MENU_EVENT, { detail: {
          url: anchor.href,
          title: anchor.textContent?.trim() || anchor.getAttribute('aria-label') || anchor.href,
          x: bounds.left + event.clientX,
          y: bounds.top + event.clientY,
          baseHref: target.url,
        } }))
      }, true)

      const originalOpen = child.open.bind(child)
      child.open = ((url?: string | URL, targetName?: string, features?: string) => {
        if (url == null || String(url).trim() === '') {
          return deferredWebTabWindow(
            target.url,
            (resolved) => originalOpen(resolved, '_blank', 'noopener,noreferrer'),
            true,
          )
        }
        const opened = openWebTab(url == null ? '' : String(url), '', target.url, { allowExternal: true })
        if (opened) return child
        return originalOpen(url, targetName, features)
      }) as typeof child.open
    } catch {
      // Cross-origin links are classified before framing and by LOFA's popup bridge.
    }
  }, [target.title, target.url])

  return (
    <div data-testid="web-page-panel" style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%', minWidth: 0, minHeight: 0, overflow: 'hidden', background: '#fff' }}>
      {showToolbar && <div
        data-testid="web-page-toolbar"
        style={{
          display: 'flex', alignItems: 'center', gap: 7, minHeight: 34, padding: '4px 7px',
          borderBottom: '1px solid rgba(80, 95, 120, .18)', background: 'var(--fp-bp-solid)', color: 'var(--fp-text-3)',
          fontSize: 12,
        }}
      >
        <button
          type="button"
          disabled={!returnTo}
          title={returnTo ? `返回 ${returnTo.title}` : '没有来源页签'}
          aria-label={returnTo ? `返回 ${returnTo.title}` : '没有来源页签'}
          data-testid="web-page-back"
          onClick={() => returnFromTab(tabId)}
          style={{ display: 'inline-grid', placeItems: 'center', width: 26, height: 24, padding: 0 }}
        >
          <ArrowLeft size={14} />
        </button>
        <input
          data-testid="web-page-address"
          value={addressDraft}
          title={currentUrl}
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => setAddressDraft(event.target.value)}
          onFocus={(event) => {
            addressFocusedRef.current = true
            event.target.select()
          }}
          onBlur={() => {
            addressFocusedRef.current = false
            setAddressDraft(currentUrl)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              commitAddress(addressDraft)
              event.currentTarget.blur()
            } else if (event.key === 'Escape') {
              setAddressDraft(currentUrl)
              event.currentTarget.blur()
            }
          }}
          style={{ flex: '1 1 auto', minWidth: 0, font: 'inherit', color: 'inherit', background: 'rgba(80, 95, 120, .10)', border: '1px solid rgba(80, 95, 120, .18)', borderRadius: 6, padding: '2px 8px', outline: 'none' }}
        />
        {profile !== 'human' && (
          <span title="此页面使用隔离的浏览配置" style={{ flex: '0 0 auto', color: 'var(--fp-link)' }}>
            {profile === 'private' ? '无痕' : '自动化隔离'}
          </span>
        )}
        <button
          type="button"
          title="刷新页面"
          aria-label="刷新页面"
          onClick={reload}
          style={{ display: 'inline-grid', placeItems: 'center', width: 26, height: 24, padding: 0 }}
        >
          <RotateCw size={13} />
        </button>
        <button
          type="button"
          title="在系统浏览器打开（登录或嵌入失败时使用）"
          aria-label="在系统浏览器打开"
          data-testid="web-page-open-system"
          onClick={() => {
            openNativeBrowserUrl(currentUrl)
            if (externalBrowserOnly && standaloneTab) usePanels.getState().closeTab(tabId)
          }}
          style={{ display: 'inline-grid', placeItems: 'center', width: 26, height: 24, padding: 0 }}
        >
          <ExternalLink size={13} />
        </button>
      </div>}
      {native ? (
        <div
          ref={nativeSurfaceRef}
          data-testid="web-page-native-surface"
          style={{ position: 'relative', flex: '1 1 auto', minWidth: 0, minHeight: 0, background: '#fff' }}
        >
          {nativeError && (
            <div
              role="alert"
              style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'grid', placeItems: 'center', padding: 24, color: 'var(--fp-err)', background: '#fff' }}
            >
              <div>
                <strong>桌面网页视图启动失败</strong>
                <div style={{ marginTop: 8, color: 'var(--fp-text-2)' }}>{nativeError}</div>
                <button type="button" style={{ marginTop: 12 }} onClick={() => openNativeBrowserUrl(currentUrl)}>
                  在系统浏览器打开
                </button>
              </div>
            </div>
          )}
        </div>
      ) : externalBrowserOnly ? (
        <div
          data-testid="web-page-external-fallback"
          style={{ flex: '1 1 auto', display: 'grid', placeItems: 'center', padding: 24, color: 'var(--fp-text-3)', background: '#fff' }}
        >
          <div style={{ maxWidth: 560, textAlign: 'center' }}>
            <strong>此网站不支持安全的网页内嵌</strong>
            <div style={{ marginTop: 8, color: 'var(--fp-text-2)', lineHeight: 1.6 }}>
              浏览器无法绕过网站设置的 X-Frame-Options 或 CSP。新的外链点击会直接在新窗口打开。
            </div>
            <button
              type="button"
              style={{ marginTop: 14 }}
              onClick={() => {
                openNativeBrowserUrl(currentUrl)
                if (standaloneTab) usePanels.getState().closeTab(tabId)
              }}
            >
              在新窗口打开
            </button>
          </div>
        </div>
      ) : (
        <iframe
          key={reloadKey}
          ref={frameRef}
          data-testid="web-page-frame"
          title={target.title}
          src={frameUrl ?? undefined}
          allow={sameOrigin ? 'clipboard-read; clipboard-write; fullscreen; camera; microphone' : 'fullscreen; picture-in-picture'}
          referrerPolicy={sameOrigin ? 'no-referrer' : 'strict-origin-when-cross-origin'}
          onLoad={sameOrigin ? attachSameOriginBridge : undefined}
          style={{ display: 'block', flex: '1 1 auto', width: '100%', height: '100%', minWidth: 0, minHeight: 0, border: 0, background: '#fff' }}
        />
      )}
    </div>
  )
}

export default WebPagePanel
