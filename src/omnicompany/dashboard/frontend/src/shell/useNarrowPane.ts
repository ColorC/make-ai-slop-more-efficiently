import { useEffect, useState, type RefObject } from 'react'

/** 列表 + 详情并排的最小容器宽度。低于它两栏都不成形, 改成同位二选一。 */
export const NARROW_PANE_WIDTH = 600

/**
 * 容器窄到放不下「列表 + 详情」两栏时返回 true。
 *
 * 量的是**容器**不是窗口: 同一个板可能被塞进伴随侧栏、分屏、或手机上的整幅页签,
 * 用 window.innerWidth 判会在侧栏里判错。容器尚未布局(0 宽)时不下结论。
 */
export function useNarrowPane(
  ref: RefObject<HTMLElement | null>,
  minSideBySide: number = NARROW_PANE_WIDTH,
): boolean {
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    const update = () => {
      const width = node.getBoundingClientRect().width
      if (width > 0) setNarrow(width < minSideBySide)
    }
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [minSideBySide, ref])
  return narrow
}
