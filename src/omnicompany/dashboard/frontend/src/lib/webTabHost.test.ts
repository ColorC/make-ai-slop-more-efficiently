import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanels } from '../stores/panelsStore'
import {
  browserEmbedUrl,
  canRenderExternalWebPage,
  decodeWebPageId,
  decodeWebPageTarget,
  encodeWebPageId,
  isInternalWebUrl,
  isSameOriginWebUrl,
  installWebTabHost,
  NATIVE_OPEN_URL_EVENT,
  normalizeWebTabUrl,
  openWebTab,
  WEB_TAB_MESSAGE,
} from './webTabHost'

function desktopHost() {
  return {
    runtime: 'desktop' as const,
    openSystemUrl: vi.fn(),
    openExternalWebview: vi.fn(async () => 'external-test'),
    updateExternalWebview: vi.fn(async () => undefined),
    closeExternalWebview: vi.fn(async () => undefined),
  }
}

function lofaHost() {
  return { ...desktopHost(), runtime: 'lofa' as const }
}

describe('webTabHost', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: [], activeId: null })
    delete window.__OMNICOMPANY_HOST__
  })

  it('encodes a restorable web page id', () => {
    const url = 'https://example.com/path?a=1#x'
    expect(decodeWebPageId(encodeWebPageId(url))).toBe(url)
  })

  it('keeps an isolated web profile in the page identity', () => {
    const url = 'https://example.com/private'
    expect(decodeWebPageTarget(encodeWebPageId(url, 'private'))).toEqual({ url, profile: 'private' })
  })

  it('rewrites localhost to the reachable Dashboard host', () => {
    expect(normalizeWebTabUrl('http://localhost:7348/chat', 'http://192.0.2.10:8210/?lofaRemoteWeb=1'))
      .toBe('http://192.0.2.10:7348/chat')
  })

  it('opens one Dashboard tab for an internal service URL and reuses it', () => {
    const first = openWebTab('http://192.0.2.10:7348/a', 'Chat', 'http://192.0.2.10:8210/')
    const second = openWebTab('http://192.0.2.10:7348/a', 'Chat again', 'http://192.0.2.10:8210/')
    expect(first).toBe(second)
    expect(usePanels.getState().tabs).toHaveLength(1)
    expect(usePanels.getState().tabs[0].ref.type).toBe('web_review')
  })

  it('routes a same-origin material surface into the native review material tab', () => {
    const opened = openWebTab(
      '/?surface=material&id=mat+candidate',
      'Review candidate',
      'http://192.0.2.10:8210/?lofaRemoteWeb=1',
    )

    expect(opened).toBe('review_material:mat candidate')
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      id: 'review_material:mat candidate',
      ref: { type: 'review_material', id: 'mat candidate' },
      title: 'Review candidate',
    })])
  })

  it('leaves an external material-looking URL to the browser', () => {
    const opened = openWebTab(
      'https://example.com/?surface=material&id=mat_candidate',
      'External',
      'http://192.0.2.10:8210/?lofaRemoteWeb=1',
    )

    expect(opened).toBeNull()
    expect(usePanels.getState().tabs).toHaveLength(0)
  })

  it('leaves an ordinary external page to the browser when no workspace renderer exists', () => {
    const opened = openWebTab(
      'https://example.com/docs',
      'External docs',
      'http://192.0.2.10:8210/',
      { allowExternal: true },
    )

    expect(opened).toBeNull()
    expect(usePanels.getState().tabs).toHaveLength(0)
    expect(canRenderExternalWebPage('https://example.com/docs')).toBe(false)
  })

  it('opens an external page inside the workspace when the desktop child WebView is available', () => {
    window.__OMNICOMPANY_HOST__ = desktopHost()
    const source = usePanels.getState().openTab({ type: 'cc_session', id: 'chat-1' }, 'Conversation')
    const opened = openWebTab(
      'https://example.com/docs',
      'External docs',
      'http://192.0.2.10:8210/',
      { allowExternal: true, sourceTabId: source },
    )

    expect(opened).toBe(`web_review:${encodeWebPageId('https://example.com/docs')}`)
    expect(usePanels.getState().tabs.find((tab) => tab.id === opened)?.returnTo?.id).toBe(source)
  })

  it('opens an external page inside the workspace when LOFA negotiates its native carrier', () => {
    window.__OMNICOMPANY_HOST__ = lofaHost()
    const opened = openWebTab(
      'https://example.com/no-frames',
      'External page',
      'https://192.0.2.10:12443/?lofaRemoteWeb=1&client=lofa',
      { allowExternal: true },
    )

    expect(opened).toBe(`web_review:${encodeWebPageId('https://example.com/no-frames')}`)
    expect(canRenderExternalWebPage('https://example.com/no-frames')).toBe(true)
  })

  it('adapts YouTube video links to the official iframe player', () => {
    expect(browserEmbedUrl('https://youtu.be/abcdefghijk?t=1m30s'))
      .toBe('https://www.youtube.com/embed/abcdefghijk?start=90')
    expect(canRenderExternalWebPage('https://www.youtube.com/watch?v=abcdefghijk')).toBe(true)
    expect(openWebTab(
      'https://www.youtube.com/watch?v=abcdefghijk',
      'Video',
      'http://192.0.2.10:8210/',
      { allowExternal: true },
    )).toBe(`web_review:${encodeWebPageId('https://www.youtube.com/watch?v=abcdefghijk')}`)
  })

  it('rewrites a loopback URL onto the Dashboard origin, protocol and port included', () => {
    // 回归钉子(2026-08-07): 只换 hostname 会得到 `http://192.0.2.10:8210/spark-play/` ——
    // 协议还是 http、端口还是 Dashboard 的回环内部端口。在 https 网关下的 Dashboard 里
    // 那是**混合内容**, Chrome 直接拦掉且根本不发请求, 页签永久打不开(快照会把它一直存着)。
    // 回环地址上的端口是内部实现细节, 对外只有网关那一个入口 —— 要换就换整个 origin。
    expect(normalizeWebTabUrl('http://127.0.0.1:8210/spark-play/', 'https://192.0.2.10:12443/'))
      .toBe('https://192.0.2.10:12443/spark-play/')
    expect(normalizeWebTabUrl('http://localhost:9001/?pick=1', 'https://192.0.2.10:12443/'))
      .toBe('https://192.0.2.10:12443/?pick=1')
    // 非回环的私网服务保持原样(网关另有 /chatui/ 之类的路由, 不能被改写掉)。
    expect(normalizeWebTabUrl('http://192.0.2.10:7348/chat', 'https://192.0.2.10:12443/'))
      .toBe('http://192.0.2.10:7348/chat')
    // 协议一致时只换 hostname、**保留端口** —— 局域网设备确实能直连这类服务自己的端口。
    expect(normalizeWebTabUrl('http://localhost:7348/chat', 'http://192.0.2.10:8210/'))
      .toBe('http://192.0.2.10:7348/chat')
    // base 本身就是回环时不改写(本机开发照常用 127.0.0.1)。
    expect(normalizeWebTabUrl('http://127.0.0.1:8210/spark-play/', 'http://127.0.0.1:8210/'))
      .toBe('http://127.0.0.1:8210/spark-play/')
  })

  it('keeps Workshop on the current Dashboard origin for mobile browser tabs', () => {
    const mobileDashboard = 'https://192.0.2.10:12443/?lofaRemoteWeb=1'

    expect(normalizeWebTabUrl('http://192.0.2.10:8210/bside/canvas', mobileDashboard))
      .toBe('https://192.0.2.10:12443/bside/canvas')
    expect(normalizeWebTabUrl(
      'http://192.0.2.10:8210/workshop/canvas/notes-web.html?session_id=canvas%3Aspark-webtwin-interface-expl-task',
      mobileDashboard,
    )).toBe(
      'https://192.0.2.10:12443/workshop/canvas/notes-web.html?session_id=canvas%3Aspark-webtwin-interface-expl-task',
    )
    expect(normalizeWebTabUrl('http://127.0.0.1:5460/materials?kind=wiki', mobileDashboard))
      .toBe('https://192.0.2.10:12443/bside/materials?kind=wiki')
    expect(normalizeWebTabUrl('http://192.0.2.10:7348/chat', mobileDashboard))
      .toBe('http://192.0.2.10:7348/chat')
  })

  it('classifies same-host and private service URLs as internal', () => {
    expect(isInternalWebUrl('http://192.0.2.10:7348/chat', 'http://192.0.2.10:8210/')).toBe(true)
    expect(isInternalWebUrl('http://192.168.1.20/app', 'http://192.0.2.10:8210/')).toBe(true)
    expect(isInternalWebUrl('https://example.com/', 'http://192.0.2.10:8210/')).toBe(false)
  })

  it('separates workspace routing from iframe-safe same-origin URLs', () => {
    const base = 'https://192.0.2.10:12443/'
    expect(isSameOriginWebUrl('/bside/', base)).toBe(true)
    expect(isInternalWebUrl('https://192.0.2.10:12444/app/demo/', base)).toBe(true)
    expect(isSameOriginWebUrl('https://192.0.2.10:12444/app/demo/', base)).toBe(false)
  })

  it('unwraps a Dashboard root into the native project tab', () => {
    const opened = openWebTab(
      'http://192.0.2.10:8210/?lofaRemoteWeb=1',
      '',
      'http://192.0.2.10:8210/?lofaRemoteWeb=1',
    )
    expect(opened).toBe('project_board:main')
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      id: 'project_board:main',
      ref: { type: 'project_board', id: 'main' },
    })])
  })

  it('leaves an explicitly linked Multiagent surface to a real browser window', () => {
    const opened = openWebTab(
      '/?surface=multiagent&ma_link=pair-1',
      '',
      'http://192.0.2.10:8210/?lofaRemoteWeb=1',
    )
    expect(opened).toBeNull()
    expect(usePanels.getState().tabs).toHaveLength(0)
  })

  it('lets native window.open create the linked Multiagent browser tab', () => {
    const originalOpen = window.open
    const child = { opener: window } as unknown as Window
    const nativeOpen = vi.fn(() => child)
    window.open = nativeOpen
    const uninstall = installWebTabHost(window)
    try {
      const opened = window.open('/?surface=multiagent&ma_link=pair-1', '_blank')
      expect(opened).toBe(child)
      expect(nativeOpen).toHaveBeenCalledWith('/?surface=multiagent&ma_link=pair-1', '_blank', undefined)
      expect(usePanels.getState().tabs).toHaveLength(0)
    } finally {
      uninstall()
      window.open = originalOpen
    }
  })

  it('lets a marked Dashboard tear-off reach the native window layer', () => {
    const originalOpen = window.open
    const child = { opener: window } as unknown as Window
    const nativeOpen = vi.fn(() => child)
    window.open = nativeOpen
    const uninstall = installWebTabHost(window)
    try {
      const url = '/?open_type=cc_session&open_id=session-1&omni_detached=1'
      const opened = window.open(url, '_blank', 'popup=yes,width=1000,height=700')
      expect(opened).toBe(child)
      expect(nativeOpen).toHaveBeenCalledWith(url, '_blank', 'popup=yes,width=1000,height=700')
      expect(usePanels.getState().tabs).toHaveLength(0)
    } finally {
      uninstall()
      window.open = originalOpen
    }
  })

  it('opens external window.open links inside a desktop workspace tab', () => {
    const originalOpen = window.open
    const nativeOpen = vi.fn()
    window.open = nativeOpen
    window.__OMNICOMPANY_HOST__ = desktopHost()
    const uninstall = installWebTabHost(window)
    try {
      const opened = window.open('https://example.com/docs', '_blank')
      expect(opened).toBe(window)
      expect(nativeOpen).not.toHaveBeenCalled()
      expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
        ref: { type: 'web_review', id: encodeWebPageId('https://example.com/docs') },
      })])
    } finally {
      uninstall()
      window.open = originalOpen
      delete window.__OMNICOMPANY_HOST__
    }
  })

  it('captures an ordinary external anchor inside the desktop workspace', () => {
    const originalOpen = window.open
    window.__OMNICOMPANY_HOST__ = desktopHost()
    const uninstall = installWebTabHost(window)
    const anchor = document.createElement('a')
    anchor.href = 'https://example.com/ordinary-link'
    anchor.textContent = 'External page'
    document.body.appendChild(anchor)
    try {
      const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })
      anchor.dispatchEvent(click)
      expect(click.defaultPrevented).toBe(true)
      expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
        ref: { type: 'web_review', id: encodeWebPageId('https://example.com/ordinary-link') },
      })])
    } finally {
      anchor.remove()
      uninstall()
      window.open = originalOpen
      delete window.__OMNICOMPANY_HOST__
    }
  })

  it('captures popups that receive their URL after window.open returns', () => {
    const originalOpen = window.open
    const nativeOpen = vi.fn()
    window.open = nativeOpen
    window.__OMNICOMPANY_HOST__ = desktopHost()
    const uninstall = installWebTabHost(window)
    try {
      const popup = window.open()
      expect(popup).not.toBeNull()
      if (popup) popup.location.href = 'https://example.com/deferred-link'
      expect(nativeOpen).not.toHaveBeenCalled()
      expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
        ref: { type: 'web_review', id: encodeWebPageId('https://example.com/deferred-link') },
      })])
    } finally {
      uninstall()
      window.open = originalOpen
      delete window.__OMNICOMPANY_HOST__
    }
  })

  it('routes native WebView popup events back into the desktop workspace', () => {
    window.__OMNICOMPANY_HOST__ = desktopHost()
    const uninstall = installWebTabHost(window)
    try {
      window.dispatchEvent(new CustomEvent(NATIVE_OPEN_URL_EVENT, {
        detail: 'https://example.com/native-popup',
      }))
      expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
        ref: { type: 'web_review', id: encodeWebPageId('https://example.com/native-popup') },
      })])
    } finally {
      uninstall()
      delete window.__OMNICOMPANY_HOST__
    }
  })

  it('routes a plain Multiagent surface into the native Multiagent tab', () => {
    const opened = openWebTab(
      '/?surface=multiagent',
      '会话',
      'http://192.0.2.10:8210/?lofaRemoteWeb=1',
    )
    expect(opened).toBe('multiagent:main')
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      id: 'multiagent:main',
      ref: { type: 'multiagent', id: 'main' },
    })])
  })

  it('routes raw review material files to the native review tab', () => {
    const opened = openWebTab(
      '/api/boss-sight/reviewstage/mat_file/file',
      '',
      'http://192.0.2.10:8210/',
    )
    expect(opened).toBe('review_material:mat_file')
  })

  it('exports the LOFA bridge message contract', () => {
    expect(WEB_TAB_MESSAGE).toBe('omni:open-web-tab')
  })
})
