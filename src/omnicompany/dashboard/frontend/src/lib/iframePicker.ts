// 同源 iframe 里的"圈选元素"工具。被网页审阅(web_review)面板与审阅材料渲染(entities/review/MaterialViews)共用。
// 注意: 只有 iframe 与宿主同源时 contentDocument 才可读; 跨域会拿到 null, 这里优雅退化为 no-op。

export interface PickedElement {
  selector: string
  tag: string
  text: string
  rect?: { x: number; y: number; width: number; height: number }
}

/** 极简 CSS selector 构造 — 优先最近的 id, 否则 tag(.class|:nth-child) 向上拼到 maxDepth。 */
export function buildSelectorPath(el: Element, doc: Document, maxDepth = 4): string {
  if ((el as HTMLElement).id) return `#${(el as HTMLElement).id}`
  const parts: string[] = []
  let cur: Element | null = el
  let depth = 0
  while (cur && cur !== doc.body && depth < maxDepth) {
    let s = cur.tagName.toLowerCase()
    if (cur.classList.length > 0) {
      s += '.' + Array.from(cur.classList).slice(0, 2).join('.')
    } else {
      const parent = cur.parentElement
      if (parent) {
        const idx = Array.from(parent.children).indexOf(cur) + 1
        s += `:nth-child(${idx})`
      }
    }
    parts.unshift(s)
    cur = cur.parentElement
    depth++
  }
  return parts.join(' > ') || el.tagName.toLowerCase()
}

/**
 * 给一个(同源)iframe 注入圈选: hover 高亮 + 单击回调 PickedElement。
 * 返回 dispose 函数; 若 iframe 跨域/未加载完成(contentDocument 为 null)返回 no-op。
 */
export function attachIframeElementPicker(iframe: HTMLIFrameElement, onPick: (picked: PickedElement) => void): () => void {
  const doc = iframe.contentDocument
  if (!doc) return () => {}

  const clearHover = () =>
    doc.querySelectorAll('[data-omni-pick-hover]').forEach((el) => (el as HTMLElement).removeAttribute('data-omni-pick-hover'))

  const onClick = (ev: MouseEvent) => {
    ev.preventDefault()
    ev.stopPropagation()
    const target = ev.target as Element
    const rect = (target as HTMLElement).getBoundingClientRect?.()
    clearHover()
    onPick({
      selector: buildSelectorPath(target, doc),
      tag: target.tagName.toLowerCase(),
      text: (target.textContent || '').trim().slice(0, 120),
      rect: rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : undefined,
    })
  }
  const onHover = (ev: MouseEvent) => {
    clearHover()
    ;(ev.target as HTMLElement).setAttribute?.('data-omni-pick-hover', 'true')
  }

  const style = doc.createElement('style')
  style.id = 'omni-pick-style'
  style.textContent = `
    [data-omni-pick-hover] { outline: 2px solid var(--fp-accent) !important; outline-offset: 2px; cursor: crosshair !important; }
    * { cursor: crosshair !important; }
  `
  doc.head.appendChild(style)
  doc.addEventListener('click', onClick, true)
  doc.addEventListener('mouseover', onHover, true)
  return () => {
    doc.removeEventListener('click', onClick, true)
    doc.removeEventListener('mouseover', onHover, true)
    clearHover()
    doc.getElementById('omni-pick-style')?.remove()
  }
}
