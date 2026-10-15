export const DETACHED_DASHBOARD_MARKER = 'omni_detached'

export interface DragViewportPoint {
  clientX: number
  clientY: number
  screenX: number
  screenY: number
}

export interface ViewportSize {
  width: number
  height: number
}

export function isOutsideViewport(
  point: Pick<DragViewportPoint, 'clientX' | 'clientY'>,
  viewport: ViewportSize,
): boolean {
  return point.clientX <= 0
    || point.clientY <= 0
    || point.clientX >= viewport.width
    || point.clientY >= viewport.height
}

export function detachedDashboardUrl(value: string, baseHref: string = window.location.href): string {
  const target = new URL(value, baseHref)
  target.searchParams.set(DETACHED_DASHBOARD_MARKER, '1')
  return target.toString()
}

export function isDetachedDashboardUrl(value: unknown, baseHref: string = window.location.href): boolean {
  try {
    const base = new URL(baseHref)
    const target = new URL(String(value ?? ''), base)
    return target.origin === base.origin
      && target.searchParams.get(DETACHED_DASHBOARD_MARKER) === '1'
  } catch {
    return false
  }
}

export interface OpenDetachedTabWindowRequest {
  url: string
  title: string
  point: DragViewportPoint
}

/** Open a real top-level window. The native desktop host handles this narrow marker as a Tauri window. */
export function openDetachedTabWindow(request: OpenDetachedTabWindowRequest): boolean {
  const url = detachedDashboardUrl(request.url)
  const width = Math.max(720, Math.min(1280, window.outerWidth || 1120))
  const height = Math.max(560, Math.min(920, window.outerHeight || 780))
  const x = Number.isFinite(request.point.screenX) ? Math.round(request.point.screenX - 72) : undefined
  const y = Number.isFinite(request.point.screenY) ? Math.round(request.point.screenY - 24) : undefined
  const features = [
    'popup=yes',
    `width=${width}`,
    `height=${height}`,
    ...(x === undefined ? [] : [`left=${x}`]),
    ...(y === undefined ? [] : [`top=${y}`]),
  ].join(',')
  const opened = window.open(url, '_blank', features)
  try {
    if (opened && opened !== window) opened.opener = null
  } catch { /* cross-window policy */ }
  return Boolean(opened && opened !== window)
}
