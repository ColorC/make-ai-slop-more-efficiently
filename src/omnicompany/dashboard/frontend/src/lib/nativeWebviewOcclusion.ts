/**
 * Native child WebViews are separate compositor surfaces. CSS z-index in the
 * Dashboard cannot place a menu or Dockview floating group above them, so the
 * native surface must be hidden while a Dashboard top layer overlaps it.
 */
export const NATIVE_WEBVIEW_OVERLAY_ATTRIBUTE = 'data-omni-native-webview-overlay'

const OCCLUDER_SELECTOR = [
  `[${NATIVE_WEBVIEW_OVERLAY_ATTRIBUTE}="true"]`,
  '.dv-resize-container:not(.dv-hidden)',
].join(',')

function rectanglesOverlap(a: DOMRect, b: DOMRect): boolean {
  return a.width > 0
    && a.height > 0
    && b.width > 0
    && b.height > 0
    && a.left < b.right
    && a.right > b.left
    && a.top < b.bottom
    && a.bottom > b.top
}

function isRendered(element: Element): boolean {
  const style = window.getComputedStyle(element)
  return style.display !== 'none'
    && style.visibility !== 'hidden'
    && style.opacity !== '0'
}

export function nativeWebviewIsOccluded(surface: Element): boolean {
  const surfaceBounds = surface.getBoundingClientRect()
  return Array.from(document.querySelectorAll<HTMLElement>(OCCLUDER_SELECTOR)).some((candidate) => {
    // A web tab may itself live in a Dockview floating group. Its own container
    // is the carrier, not an occluder; sibling floating groups still occlude it.
    if (candidate === surface || candidate.contains(surface) || surface.contains(candidate)) return false
    return isRendered(candidate) && rectanglesOverlap(surfaceBounds, candidate.getBoundingClientRect())
  })
}

/** Re-check when menus mount/unmount or Dockview moves/resizes a floating group. */
export function observeNativeWebviewOcclusion(surface: Element, onChange: () => void): () => void {
  const root = document.body
  if (!root) return () => undefined
  const observer = new MutationObserver(onChange)
  observer.observe(root, {
    attributes: true,
    attributeFilter: ['class', 'style', NATIVE_WEBVIEW_OVERLAY_ATTRIBUTE],
    childList: true,
    subtree: true,
  })
  return () => observer.disconnect()
}
