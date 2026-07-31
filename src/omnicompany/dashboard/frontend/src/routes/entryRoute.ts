export type EntryRoot = 'app' | 'chat'

export interface EntryRouteResolution {
  root: EntryRoot
  pathname: string
  search: string
  replace: boolean
}

function normalizeSearch(search: string): URLSearchParams {
  return new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
}

function serializeSearch(params: URLSearchParams): string {
  const raw = params.toString()
  return raw ? `?${raw}` : ''
}

export function resolveEntryRoute(pathname: string, search: string): EntryRouteResolution {
  // R4: standalone 审阅台已退役。老链接 /review-stage?material=X(后端 open_ref / 历史书签)
  // 不死: 映射成驾驶舱 deeplink — 有 material 开单条材料页签, 没有开审阅队列。
  if (pathname === '/review-stage') {
    const params = normalizeSearch(search)
    const materialId = params.get('material')
    const next = new URLSearchParams()
    if (materialId) {
      next.set('open_type', 'review_material')
      next.set('open_id', materialId)
    } else {
      next.set('open_type', 'review_queue')
      next.set('open_id', 'main')
    }
    return { root: 'app', pathname: '/', search: serializeSearch(next), replace: true }
  }

  if (pathname !== '/chat-standalone') {
    return { root: 'app', pathname, search, replace: false }
  }

  const params = normalizeSearch(search)
  if (params.has('embedded') || params.get('legacy') === '1') {
    return { root: 'chat', pathname, search, replace: false }
  }

  const sessionId = params.get('session')
  const next = new URLSearchParams()
  for (const [key, value] of params.entries()) {
    if (key === 'session' || key === 'provider' || key === 'legacy' || key === 'embedded') continue
    next.append(key, value)
  }
  if (sessionId) {
    next.set('open_type', 'cc_session')
    next.set('open_id', sessionId)
    next.set('open_title', params.get('title') || sessionId)
  }

  return {
    root: 'app',
    pathname: '/',
    search: serializeSearch(next),
    replace: true,
  }
}

export function applyEntryRoute(win: Window): EntryRoot {
  const resolved = resolveEntryRoute(win.location.pathname, win.location.search)
  if (resolved.replace) {
    win.history.replaceState(null, '', `${resolved.pathname}${resolved.search}${win.location.hash}`)
  }
  return resolved.root
}
