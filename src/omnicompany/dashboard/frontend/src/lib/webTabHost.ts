import { usePanels } from '../stores/panelsStore'
import type { EntityRef, EntityType } from '../entities/types'
import type { DockPlacement } from '../stores/panelsStore'
import { isDetachedDashboardUrl } from './detachedTabWindow'

export const WEB_PAGE_ID_PREFIX = 'web-page:'
export const WEB_TAB_MESSAGE = 'omni:open-web-tab'
export const NATIVE_OPEN_URL_EVENT = 'omnicompany:native-open-url'
export const WEB_LINK_CONTEXT_MENU_EVENT = 'omnicompany:web-link-context-menu'

export interface WebLinkContextMenuDetail {
  url: string
  title: string
  x: number
  y: number
  baseHref: string
}

export type WebProfile = 'human' | 'private' | 'system' | 'automation'

export interface WebPageIdentity {
  url: string
  profile: WebProfile
}

export interface OpenWebTabOptions {
  /** Desktop/workspace navigation may embed public pages; browser-global interception may not. */
  allowExternal?: boolean
  profile?: Exclude<WebProfile, 'system'>
  sourceTabId?: string | null
  placement?: DockPlacement
}

export interface OpenWebTabMessage {
  type: typeof WEB_TAB_MESSAGE
  url: string
  title?: string
}

function isLoopbackHostname(hostname: string): boolean {
  return /^(localhost|127\.0\.0\.1|\[::1\]|::1)$/i.test(hostname)
}

/**
 * Dashboard 已经为 Workshop 提供同源 `/bside/` 入口。页签历史中仍可能保存
 * 8210 的旧公开地址或 5460 的内部地址；在手机的 12443 HTTPS 入口里直接使用
 * 这些地址会分别变成跨源混合内容或不可达内网端口。
 *
 * 这里只修复明确属于 Workshop 的挂载，不把普通局域网服务任意改写成同源。
 */
function dashboardMountedUrl(target: URL, base: URL): string | null {
  const sameMachine = target.hostname === base.hostname || isLoopbackHostname(target.hostname)
  if (!sameMachine) return null

  const suffix = `${target.pathname}${target.search}${target.hash}`
  if (target.pathname === '/bside' || target.pathname.startsWith('/bside/')) {
    return new URL(suffix, base.origin).href
  }
  // 8210 is the Dashboard's LAN/public alias for the same mounted application.
  // On Pad the shell itself runs through the 12443 HTTPS gateway, so preserving
  // an absolute http://host:8210 Workshop link turns an otherwise same-site page
  // into mixed/cross-origin content and sends it to the generic CSP fallback.
  // Keep the path/query/hash but use the active Dashboard origin.
  if (target.port === '8210') {
    return new URL(suffix, base.origin).href
  }
  if (target.port === '5460') {
    const mountedPath = target.pathname.startsWith('/') ? `/bside${suffix}` : `/bside/${suffix}`
    return new URL(mountedPath, base.origin).href
  }
  return null
}

export function normalizeWebTabUrl(value: unknown, baseHref?: string): string | null {
  const raw = String(value ?? '').trim()
  if (!raw) return null
  try {
    const base = baseHref || (typeof window !== 'undefined' ? window.location.href : 'http://localhost/')
    const parsed = new URL(raw, base)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null

    // 注册表/项目配置里常常存着回环地址(localhost / 127.0.0.1)。从局域网设备看,
    // 回环指向的是**它自己**, 必须改写成这台 Dashboard 真正可达的地址。
    //
    // ⚠ 要换就得换**整个 origin**(协议 + 主机 + 端口), 只换 hostname 是错的
    // (2026-08-07 实锤, 用户为同一现象反复报障):
    //   `http://127.0.0.1:8210/spark-play/` 在 base 为 `https://192.0.2.10:12443/` 时,
    //   只换 hostname 会得到 `http://192.0.2.10:8210/spark-play/` ——
    //   ① 协议还是 http, 而页面是 https ⇒ **混合内容, Chrome 直接拦掉且根本不发请求**
    //      (网关访问日志里因此一条记录都没有, 服务端怎么查都是好的);
    //   ② 端口还是 8210, 那是 Dashboard 的**回环内部端口**, 局域网压根不开放。
    // 这两条都指向同一个判断错误: 回环地址上的"端口"是内部实现细节, 对外只有网关那一个入口。
    //
    // 页签快照(sessionStorage)会把算出来的绝对 URL 一直存着 —— 所以一旦算错,
    // 那个页签就**永久废掉**, 表现为"每次都一样、修好又坏"。
    const baseUrl = new URL(base)
    const mountedUrl = dashboardMountedUrl(parsed, baseUrl)
    if (mountedUrl) return mountedUrl
    if (isLoopbackHostname(parsed.hostname) && !isLoopbackHostname(baseUrl.hostname)) {
      // 协议对不上(页面 https、目标 http)时**换整个 origin**, 不只是 hostname。
      // 这时保留端口毫无意义: 那个 http 地址在 https 页面里是混合内容, 浏览器 100% 拦掉,
      // 换成网关 origin 至少还有网关路由兜底。协议一致时维持原行为(保留端口), 因为
      // 局域网设备确实能直连 chatui(7348) 这类服务自己的端口。
      if (parsed.protocol !== baseUrl.protocol) {
        return new URL(parsed.pathname + parsed.search + parsed.hash, baseUrl.origin).href
      }
      parsed.hostname = baseUrl.hostname
    }
    return parsed.href
  } catch {
    return null
  }
}

function youtubeVideoId(target: URL): string | null {
  const host = target.hostname.toLowerCase().replace(/^www\./, '')
  let candidate = ''
  if (host === 'youtu.be') {
    candidate = target.pathname.split('/').filter(Boolean)[0] ?? ''
  } else if (host === 'youtube.com' || host === 'm.youtube.com' || host === 'music.youtube.com' || host === 'youtube-nocookie.com') {
    const parts = target.pathname.split('/').filter(Boolean)
    if (parts[0] === 'embed' || parts[0] === 'shorts' || parts[0] === 'live') candidate = parts[1] ?? ''
    else if (target.pathname === '/watch') candidate = target.searchParams.get('v') ?? ''
  }
  return /^[A-Za-z0-9_-]{11}$/.test(candidate) ? candidate : null
}

function youtubeStartSeconds(target: URL): string | null {
  const raw = target.searchParams.get('start') || target.searchParams.get('t') || ''
  if (/^\d+$/.test(raw)) return String(Number(raw))
  const match = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(raw)
  if (!match || !raw) return null
  const seconds = Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0)
  return seconds > 0 ? String(seconds) : null
}

/**
 * Browser iframes cannot bypass X-Frame-Options / CSP frame-ancestors. Keep a
 * deliberately small adapter list for providers that publish an official embed URL.
 */
export function browserEmbedUrl(value: unknown, baseHref?: string): string | null {
  const normalized = normalizeWebTabUrl(value, baseHref)
  if (!normalized) return null
  const target = new URL(normalized)
  const videoId = youtubeVideoId(target)
  if (!videoId) return null
  const embedded = new URL(`https://www.youtube.com/embed/${videoId}`)
  const start = youtubeStartSeconds(target)
  if (start) embedded.searchParams.set('start', start)
  return embedded.href
}

function hasNativeExternalWebview(win: Window = window): boolean {
  if (typeof window === 'undefined') return false
  const host = win.__OMNICOMPANY_HOST__
  return Boolean(host?.openExternalWebview && host.updateExternalWebview && host.closeExternalWebview)
}

/** Whether an external URL has a real workspace renderer rather than a doomed iframe. */
export function canRenderExternalWebPage(value: unknown, baseHref?: string): boolean {
  const normalized = normalizeWebTabUrl(value, baseHref)
  if (!normalized) return false
  if (isInternalWebUrl(normalized, baseHref)) return true
  return hasNativeExternalWebview() || browserEmbedUrl(normalized, baseHref) !== null
}

export function encodeWebPageId(url: string, profile: Exclude<WebProfile, 'system'> = 'human'): string {
  const profilePrefix = profile === 'human' ? '' : `${profile}:`
  return WEB_PAGE_ID_PREFIX + profilePrefix + encodeURIComponent(url)
}

export function decodeWebPageTarget(id: string): WebPageIdentity | null {
  if (!id.startsWith(WEB_PAGE_ID_PREFIX)) return null
  try {
    let encoded = id.slice(WEB_PAGE_ID_PREFIX.length)
    let profile: WebProfile = 'human'
    for (const candidate of ['private', 'automation'] as const) {
      if (encoded.startsWith(`${candidate}:`)) {
        profile = candidate
        encoded = encoded.slice(candidate.length + 1)
        break
      }
    }
    const url = normalizeWebTabUrl(decodeURIComponent(encoded))
    return url ? { url, profile } : null
  } catch {
    return null
  }
}

export function decodeWebPageId(id: string): string | null {
  return decodeWebPageTarget(id)?.url ?? null
}

export function webTabTitle(url: string, title?: string): string {
  const named = String(title ?? '').trim()
  if (named) return named
  try { return new URL(url).hostname || '\u7f51\u9875' } catch { return '\u7f51\u9875' }
}

const ENTITY_TYPES = new Set<EntityType>([
  'note', 'graph', 'plan', 'trace', 'session', 'cc_session', 'cc_companion',
  'multiagent', 'controller', 'material_registry', 'review_queue',
  'review_material', 'worker', 'material', 'team', 'team_board', 'plan_audit',
  'settings', 'web_review', 'project', 'project_board', 'quest_board',
  'authored', 'material_graph', 'nav_audit', 'studio_reader', 'overlay_file',
  'file_bridge',
])

function isPrivateHostname(hostname: string): boolean {
  const host = hostname.toLowerCase()
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local')) return true
  if (host === '::1' || host.startsWith('127.')) return true
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host)
  if (!match) return false
  const octets = match.slice(1).map(Number)
  if (octets.some((value) => value < 0 || value > 255)) return false
  return octets[0] === 10
    || octets[0] === 127
    || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
    || (octets[0] === 192 && octets[1] === 168)
}

/** Internal services stay inside Dashboard tabs; public Internet URLs stay in the browser. */
export function isInternalWebUrl(value: unknown, baseHref?: string): boolean {
  const normalized = normalizeWebTabUrl(value, baseHref)
  if (!normalized) return false
  try {
    const base = new URL(baseHref || (typeof window !== 'undefined' ? window.location.href : 'http://localhost/'))
    const target = new URL(normalized)
    return target.origin === base.origin
      || target.hostname === base.hostname
      || (isPrivateHostname(target.hostname) && isPrivateHostname(base.hostname))
  } catch {
    return false
  }
}

/**
 * Whether a URL can use the browser iframe path that requires DOM access.
 *
 * "Workspace-internal" is intentionally broader than "same origin": a LAN
 * service on another port is still ours, but it has an independent auth/CSP
 * boundary.  Treating those two concepts as the same made protected surfaces
 * enter an iframe where WebView2 cannot answer Basic-auth challenges.  Callers
 * that choose an iframe/native-WebView/browser carrier must use this stricter
 * predicate; routing and tab ownership can keep using isInternalWebUrl.
 */
export function isSameOriginWebUrl(value: unknown, baseHref?: string): boolean {
  const normalized = normalizeWebTabUrl(value, baseHref)
  if (!normalized) return false
  try {
    const base = new URL(baseHref || (typeof window !== 'undefined' ? window.location.href : 'http://localhost/'))
    return new URL(normalized).origin === base.origin
  } catch {
    return false
  }
}

function entityRefFromUrl(target: URL): EntityRef | null {
  const openType = target.searchParams.get('open_type')?.trim() as EntityType | undefined
  const openId = target.searchParams.get('open_id')?.trim()
  if (openType && openId && ENTITY_TYPES.has(openType) && openType !== 'web_review') {
    return { type: openType, id: openId }
  }

  if (target.searchParams.get('surface') === 'material') {
    const materialId = target.searchParams.get('id')?.trim()
    if (materialId) return { type: 'review_material', id: materialId }
  }

  if (target.searchParams.get('surface') === 'multiagent') {
    return { type: 'multiagent', id: 'main' }
  }

  if (target.pathname.replace(/\/+$/, '') === '/review-stage') {
    const materialId = target.searchParams.get('material')?.trim()
      || target.searchParams.get('id')?.trim()
    return materialId
      ? { type: 'review_material', id: materialId }
      : { type: 'review_queue', id: 'main' }
  }

  const fileMatch = /^\/api\/boss-sight\/reviewstage\/([^/]+)\/(?:file|cover|frames(?:\/\d+)?)\/?$/.exec(target.pathname)
  if (fileMatch) {
    try {
      return { type: 'review_material', id: decodeURIComponent(fileMatch[1]) }
    } catch {
      return null
    }
  }
  return null
}

function looksLikeDashboardRoot(target: URL, base: URL): boolean {
  if (target.pathname !== '/' && target.pathname !== '') return false
  if (target.searchParams.has('open_type') || target.searchParams.has('surface')) return true
  // A plain same-origin root and the two supported Dashboard gateway ports are shells,
  // not generic web pages. Query-only host markers do not change that identity.
  return target.origin === base.origin
    || target.port === '8210'
    || target.port === '12443'
}

function openDashboardOwnedTab(
  url: string,
  title: string | undefined,
  baseHref?: string,
  options: OpenWebTabOptions = {},
): string | null {
  try {
    const base = new URL(baseHref || (typeof window !== 'undefined' ? window.location.href : 'http://localhost/'))
    const target = new URL(url)
    if (!isInternalWebUrl(target.href, base.href)) return null

    const ref = entityRefFromUrl(target)
    if (ref) {
      const fallbackTitle = ref.type === 'review_material'
        ? ref.id
        : ref.type === 'multiagent' ? '会话' : '审阅'
      return usePanels.getState().openTab(
        ref,
        String(title || target.searchParams.get('open_title') || fallbackTitle).trim() || fallbackTitle,
        undefined,
        options.placement,
        options.sourceTabId,
      )
    }

    if (!looksLikeDashboardRoot(target, base)) return null
    return usePanels.getState().openTab(
      { type: 'project_board', id: 'main' },
      String(title || '项目').trim() || '项目',
      undefined,
      options.placement,
      options.sourceTabId,
    )
  } catch {
    return null
  }
}

export function openWebTab(
  value: unknown,
  title?: string,
  baseHref?: string,
  options: OpenWebTabOptions = {},
): string | null {
  const url = normalizeWebTabUrl(value, baseHref)
  const internal = url ? isInternalWebUrl(url, baseHref) : false
  if (!url || (!internal && (!options.allowExternal || !canRenderExternalWebPage(url, baseHref)))) return null
  const target = new URL(url)

  // A linked Multiagent surface is intentionally a second browser window.
  // Let the native window.open / target=_blank path handle it instead of
  // collapsing it into the current Dockview as a project tab.
  if (
    target.searchParams.get('surface') === 'multiagent'
    && !!target.searchParams.get('ma_link')
  ) return null

  // Dashboard-owned standalone surfaces must land in the existing Dockview layer.
  // Loading them through web_review would iframe another Dashboard shell and create
  // the unacceptable "tabs inside a tab" hierarchy on LOFA.
  const ownedTab = internal ? openDashboardOwnedTab(url, title, baseHref, options) : null
  if (ownedTab) return ownedTab

  const profile = options.profile ?? 'human'
  return usePanels.getState().openTab(
    { type: 'web_review', id: encodeWebPageId(url, profile) },
    webTabTitle(url, title),
    undefined,
    options.placement,
    options.sourceTabId,
  )
}

/**
 * Chromium-compatible stand-in for the common `const popup = window.open();
 * popup.location.href = url` pattern. The desktop host cannot keep an
 * untrusted about:blank child WebView around, so defer creating the workspace
 * tab until the caller supplies the real URL.
 */
export function deferredWebTabWindow(
  baseHref: string,
  openFallback: (url: string) => Window | null,
  allowExternal = false,
): Window {
  let closed = false
  let currentHref = 'about:blank'
  const navigate = (value: unknown) => {
    if (closed) return
    const normalized = normalizeWebTabUrl(value, baseHref)
    if (!normalized) return
    currentHref = normalized
    if (!openWebTab(normalized, '', baseHref, { allowExternal })) openFallback(normalized)
  }
  const location = {
    get href() { return currentHref },
    set href(value: string) { navigate(value) },
    assign: navigate,
    replace: navigate,
    toString: () => currentHref,
  }
  const popup: Record<string, unknown> = {
    opener: null,
    get closed() { return closed },
    close: () => { closed = true },
    focus: () => undefined,
    blur: () => undefined,
    postMessage: () => undefined,
  }
  Object.defineProperty(popup, 'location', {
    configurable: false,
    enumerable: true,
    get: () => location,
    set: navigate,
  })
  return popup as unknown as Window
}

function anchorFromEvent(event: MouseEvent, includeSameTab: boolean): HTMLAnchorElement | null {
  if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return null
  const target = event.target
  if (!(target instanceof Element)) return null
  const anchor = target.closest('a[href]')
  if (!(anchor instanceof HTMLAnchorElement)) return null
  const targetName = anchor.target.toLowerCase()
  return includeSameTab || targetName === '_blank' ? anchor : null
}

/**
 * Enables Dashboard-owned web tabs in LOFA and top-level Dashboard browsers.
 * Internal links are intercepted. External links stay in a workspace tab when
 * Desktop or LOFA has negotiated a real native page carrier.
 */
export function installWebTabHost(win: Window = window): () => void {
  const remoteWeb = new URL(win.location.href).searchParams.get('lofaRemoteWeb') === '1'
  const active = remoteWeb || win.parent === win
  const nativeCarrier = () => hasNativeExternalWebview(win)
  if (active) win.document.documentElement.dataset.omniWebTabHost = '1'

  const originalOpen = win.open.bind(win)
  if (active) {
    win.open = ((url?: string | URL, target?: string, features?: string) => {
      if (url == null || String(url).trim() === '') {
        return deferredWebTabWindow(
          win.location.href,
          (resolved) => originalOpen(resolved, '_blank', 'noopener,noreferrer'),
          nativeCarrier(),
        )
      }
      // A tear-off request must reach the browser/Tauri popup layer instead of
      // being recaptured as another Dockview tab in this same window.
      if (!isDetachedDashboardUrl(url, win.location.href)) {
        const opened = openWebTab(String(url), '', win.location.href, {
          allowExternal: nativeCarrier(),
        })
        if (opened) return win
      }
      return originalOpen(url, target, features)
    }) as typeof win.open
  }

  const onClick = (event: MouseEvent) => {
    if (!active || event.defaultPrevented) return
    const anchor = anchorFromEvent(event, nativeCarrier())
    if (!anchor || anchor.hasAttribute('download') || anchor.hasAttribute('data-omni-native-browser')) return
    const opened = openWebTab(
      anchor.href,
      anchor.textContent || anchor.getAttribute('aria-label') || '',
      win.location.href,
      { allowExternal: nativeCarrier() },
    )
    if (!opened) return
    event.preventDefault()
  }

  const onNativeOpenUrl = (event: Event) => {
    if (!nativeCarrier()) return
    const url = String((event as CustomEvent<unknown>).detail ?? '')
    const opened = openWebTab(url, '', win.location.href, { allowExternal: true })
    if (!opened) void win.__OMNICOMPANY_HOST__?.openSystemUrl(url)
  }

  const onMessage = (event: MessageEvent) => {
    const data = event.data as Partial<OpenWebTabMessage> | null
    if (!data || data.type !== WEB_TAB_MESSAGE || typeof data.url !== 'string') return
    const allowedSource = win.parent === win ? event.source === win : event.source === win.parent
    if (!allowedSource) return
    const opened = openWebTab(data.url, typeof data.title === 'string' ? data.title : '', win.location.href, {
      allowExternal: nativeCarrier(),
    })
    if (!opened && active) originalOpen(data.url, '_blank', 'noopener,noreferrer')
  }

  win.document.addEventListener('click', onClick, true)
  win.addEventListener(NATIVE_OPEN_URL_EVENT, onNativeOpenUrl)
  win.addEventListener('message', onMessage)
  return () => {
    win.document.removeEventListener('click', onClick, true)
    win.removeEventListener(NATIVE_OPEN_URL_EVENT, onNativeOpenUrl)
    win.removeEventListener('message', onMessage)
    if (active) {
      win.open = originalOpen
      delete win.document.documentElement.dataset.omniWebTabHost
    }
  }
}
