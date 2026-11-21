// 桌面壳 0.1.39 起是无边框窗口: 没有标题栏, 右上角盖着最小化 / 最大化 / 关闭三个按钮
// (宿主的独立小页面)。这里做两件页面侧的事:
//   1. 让位: 找到顶到窗口右上角的那条页头(dockview 页签条 / 会话侧栏导航), 给它右侧
//      留出三键的宽度, 页签和按钮不会被盖住;
//   2. 换色: 三键的底色 / 字色 / 悬停色取当前皮肤, 推给宿主。
import type { OmnicompanyHostBridge as DesktopHostBridge } from '../lib/workspaceNavigation'

export const WINDOW_CORNER_ATTR = 'data-omni-window-corner'
const HEADER_SELECTOR = '.dv-tabs-and-actions-container, .cccp-nav, [data-omni-window-header]'
const EDGE = 4

/** 标出顶到窗口右上角的页头; 返回被标出的元素个数。 */
export function markWindowCornerHeaders(
  root: ParentNode = document,
  viewportWidth: number = window.innerWidth,
): number {
  let marked = 0
  root.querySelectorAll<HTMLElement>(HEADER_SELECTOR).forEach((element) => {
    const rect = element.getBoundingClientRect()
    const atCorner = rect.width > 0 && rect.top <= EDGE && rect.right >= viewportWidth - EDGE
    if (atCorner) {
      marked += 1
      if (!element.hasAttribute(WINDOW_CORNER_ATTR)) element.setAttribute(WINDOW_CORNER_ATTR, '')
    } else if (element.hasAttribute(WINDOW_CORNER_ATTR)) {
      element.removeAttribute(WINDOW_CORNER_ATTR)
    }
  })
  return marked
}

/** 布局变化(拖分栏、开关侧栏、换页签)后重新标; 用 rAF 合并同一帧里的多次变化。 */
export function watchWindowCornerHeaders(): () => void {
  let frame = 0
  const schedule = () => {
    if (frame) return
    frame = window.requestAnimationFrame(() => {
      frame = 0
      markWindowCornerHeaders()
    })
  }
  const mutations = new MutationObserver(schedule)
  mutations.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'hidden'] })
  window.addEventListener('resize', schedule)
  schedule()
  return () => {
    mutations.disconnect()
    window.removeEventListener('resize', schedule)
    if (frame) window.cancelAnimationFrame(frame)
    document.querySelectorAll(`[${WINDOW_CORNER_ATTR}]`).forEach((element) => element.removeAttribute(WINDOW_CORNER_ATTR))
  }
}

/** 皮肤变量可能是 var()/color-mix(); 借一个隐藏元素让浏览器算成 rgb()。 */
export function resolvedChromeColors(): { background: string; foreground: string; hover: string } {
  const probe = document.createElement('div')
  probe.style.cssText = 'position:fixed;left:-9999px;top:0;width:1px;height:1px;pointer-events:none;'
    + 'background:var(--fp-bg);color:var(--fp-text-2);border-color:var(--fp-glass-2, rgba(255,255,255,.09));border-style:solid;border-width:1px;'
  document.body.appendChild(probe)
  const style = window.getComputedStyle(probe)
  const colors = { background: style.backgroundColor, foreground: style.color, hover: style.borderTopColor }
  probe.remove()
  return colors
}

export function pushWindowControlsTheme(bridge: DesktopHostBridge | null | undefined): void {
  if (!bridge?.setWindowControlsTheme) return
  const colors = resolvedChromeColors()
  // 底色透明(皮肤只给了渐变底)时宁可不推, 保留宿主默认的深色底。
  if (/rgba\([^)]*,\s*0\)$/.test(colors.background) || colors.background === 'transparent') {
    void bridge.setWindowControlsTheme({ foreground: colors.foreground, hover: colors.hover }).catch(() => {})
    return
  }
  void bridge.setWindowControlsTheme(colors).catch(() => {})
}
