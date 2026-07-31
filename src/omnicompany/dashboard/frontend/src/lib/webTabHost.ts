import { usePanels } from '../stores/panelsStore'
import type { EntityRef, EntityType } from '../entities/types'

export const WEB_PAGE_ID_PREFIX = 'web-page:'
export const WEB_TAB_MESSAGE = 'omni:open-web-tab'

export interface OpenWebTabMessage {
  type: typeof WEB_TAB_MESSAGE
  url: string
  title?: string
}

export function normalizeWebTabUrl(value: unknown, baseHref?: string): string | null {
  const raw = String(value ?? '').trim()
  if (!raw) return null
  try {
    const base = baseHref || (typeof window !== 'undefined' ? window.location.href : 'http://localhost/')
    const parsed = new URL(raw, base)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null

    // Project/app registries often contain localhost URLs. From a LAN tablet they must
    // keep the port/path but use the Dashboard host that the tablet can actually reach.
    const baseUrl = new URL(base)
    if (/^(localhost|127\.0\.0\.1)$/i.test(parsed.hostname) && !/^(localhost|127\.0\.0\.1)$/i.test(baseUrl.hostname)) {
      parsed.hostname = baseUrl.hostname
    }
    return parsed.href
  } catch {
    return null
  }
}

export function encodeWebPageId(url: string): string {
  return WEB_PAGE_ID_PREFIX + encodeURIComponent(url)
}

export function decodeWebPageId(id: string): string | null {
  if (!id.startsWith(WEB_PAGE_ID_PREFIX)) return null
  try {
    return normalizeWebTabUrl(decodeURIComponent(id.slice(WEB_PAGE_ID_PREFIX.length)))
  } catch {
    return null
  }
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

function openDashboardOwnedTab(url: string, title: string | undefined, baseHref?: string): string | null {
  try {
    const base = new URL(baseHref || (typeof window !== 'undefined' ? window.location.href : 'http://localhost/'))
    const target = new URL(url)
    if (!isInternalWebUrl(target.href, base.href)) return null

    const ref = entityRefFromUrl(target)
    if (ref) {
      const fallbackTitle = ref.type === 'review_material'
        ? ref.id
        : ref.type === 'multiagent' ? 'Multiagent' : '审阅'
      return usePanels.getState().openTab(
        ref,
        String(title || target.searchParams.get('open_title') || fallbackTitle).trim() || fallbackTitle,
      )
    }

    if (!looksLikeDashboardRoot(target, base)) return null
    return usePanels.getState().openTab(
      { type: 'project_board', id: 'main' },
      String(title || '项目').trim() || '项目',
    )
  } catch {
    return null
  }
}

export function openWebTab(value: unknown, title?: string, baseHref?: string): string | null {
  const url = normalizeWebTabUrl(value, baseHref)
  if (!url || !isInternalWebUrl(url, baseHref)) return null
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
  const ownedTab = openDashboardOwnedTab(url, title, baseHref)
  if (ownedTab) return ownedTab

  return usePanels.getState().openTab(
    { type: 'web_review', id: encodeWebPageId(url) },
    webTabTitle(url, title),
  )
}

function anchorFromEvent(event: MouseEvent): HTMLAnchorElement | null {
  const target = event.target
  if (!(target instanceof Element)) return null
  const anchor = target.closest('a[target]')
  if (!(anchor instanceof HTMLAnchorElement)) return null
  return anchor.target.toLowerCase() === '_blank' ? anchor : null
}

/**
 * Enables Dashboard-owned web tabs in LOFA and top-level Dashboard browsers.
 * Internal links are intercepted; external links always fall back to the
 * original browser path.
 */
export function installWebTabHost(win: Window = window): () => void {
  const remoteWeb = new URL(win.location.href).searchParams.get('lofaRemoteWeb') === '1'
  const active = remoteWeb || win.parent === win
  if (active) win.document.documentElement.dataset.omniWebTabHost = '1'

  const originalOpen = win.open.bind(win)
  if (active) {
    win.open = ((url?: string | URL, target?: string, features?: string) => {
      const opened = openWebTab(url == null ? '' : String(url), '', win.location.href)
      if (opened) return win
      return originalOpen(url, target, features)
    }) as typeof win.open
  }

  const onClick = (event: MouseEvent) => {
    if (!active || event.defaultPrevented) return
    const anchor = anchorFromEvent(event)
    if (!anchor || anchor.hasAttribute('download')) return
    const opened = openWebTab(anchor.href, anchor.textContent || anchor.getAttribute('aria-label') || '', win.location.href)
    if (!opened) return
    event.preventDefault()
  }

  const onMessage = (event: MessageEvent) => {
    const data = event.data as Partial<OpenWebTabMessage> | null
    if (!data || data.type !== WEB_TAB_MESSAGE || typeof data.url !== 'string') return
    const allowedSource = win.parent === win ? event.source === win : event.source === win.parent
    if (!allowedSource) return
    const opened = openWebTab(data.url, typeof data.title === 'string' ? data.title : '', win.location.href)
    if (!opened && active) originalOpen(data.url, '_blank', 'noopener,noreferrer')
  }

  win.document.addEventListener('click', onClick, true)
  win.addEventListener('message', onMessage)
  return () => {
    win.document.removeEventListener('click', onClick, true)
    win.removeEventListener('message', onMessage)
    if (active) {
      win.open = originalOpen
      delete win.document.documentElement.dataset.omniWebTabHost
    }
  }
}
