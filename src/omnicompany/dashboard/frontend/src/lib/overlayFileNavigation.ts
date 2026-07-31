import { usePanels } from '../stores/panelsStore'

export interface OverlayFileOpenTarget {
  name: string
  path: string
  kind: string
  open_token: string
}

/** Open an Overlay search hit as a first-class Dashboard tab, never through a host application. */
export function openOverlayFileInDashboard(
  target: OverlayFileOpenTarget,
  background = false,
): string {
  const panels = usePanels.getState()
  const open = background ? panels.openTabBackground : panels.openTab
  return open({ type: 'overlay_file', id: target.open_token }, target.name)
}

/** Browser URL that restores the same Dashboard file tab. */
export function overlayFileWebUrl(
  target: Pick<OverlayFileOpenTarget, 'name' | 'open_token'>,
  origin = window.location.origin,
): string {
  const url = new URL('/', origin)
  url.searchParams.set('open_type', 'overlay_file')
  url.searchParams.set('open_id', target.open_token)
  url.searchParams.set('open_title', target.name.slice(0, 48))
  return url.toString()
}
