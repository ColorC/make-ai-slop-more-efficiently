import React, { useCallback, useRef } from 'react'
import { encodeWebPageId, openWebTab } from '../../lib/webTabHost'
import { usePanels } from '../../stores/panelsStore'

export interface WebPageTarget {
  title: string
  url: string
}

/** A chrome-free page surface. Dashboard's Dockview tab is the only visible tab layer. */
const WebPagePanel: React.FC<{ target: WebPageTarget }> = ({ target }) => {
  const frameRef = useRef<HTMLIFrameElement | null>(null)

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
        const opened = openWebTab(child.location.href, target.title, target.url)
        const current = `web_review:${encodeWebPageId(target.url)}`
        if (opened && opened !== current) usePanels.getState().closeTab(current)
        return
      }

      if (doc.documentElement.dataset.omniNestedWebTabBridge === '1') return
      doc.documentElement.dataset.omniNestedWebTabBridge = '1'

      doc.addEventListener('click', (event) => {
        const element = event.target instanceof Element ? event.target : null
        const anchor = element?.closest('a[target="_blank"]') as HTMLAnchorElement | null
        if (!anchor || !anchor.href || anchor.hasAttribute('download')) return
        if (!openWebTab(anchor.href, anchor.textContent || '', target.url)) return
        event.preventDefault()
      }, true)

      const originalOpen = child.open.bind(child)
      child.open = ((url?: string | URL, targetName?: string, features?: string) => {
        const opened = openWebTab(url == null ? '' : String(url), '', target.url)
        if (opened) return child
        return originalOpen(url, targetName, features)
      }) as typeof child.open
    } catch {
      // Cross-origin links are classified before framing and by LOFA's popup bridge.
    }
  }, [target.title, target.url])

  return (
    <div data-testid="web-page-panel" style={{ display: 'flex', width: '100%', height: '100%', minWidth: 0, minHeight: 0, overflow: 'hidden', background: '#fff' }}>
      <iframe
        ref={frameRef}
        data-testid="web-page-frame"
        title={target.title}
        src={target.url}
        allow="clipboard-read; clipboard-write; fullscreen; camera; microphone"
        referrerPolicy="no-referrer"
        onLoad={attachSameOriginBridge}
        style={{ display: 'block', flex: '1 1 auto', width: '100%', height: '100%', minWidth: 0, minHeight: 0, border: 0, background: '#fff' }}
      />
    </div>
  )
}

export default WebPagePanel
