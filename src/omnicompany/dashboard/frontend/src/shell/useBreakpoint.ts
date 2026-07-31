import { useEffect, useState } from 'react'

/**
 * 形态断点 hook(M3 · W3.2 窄屏/平板形态)。matchMedia 驱动, change 时重渲染。
 * 断点数值与 frostpane.css 的 --fp-bp-phone/--fp-bp-desktop 对齐(CSS var 进不了 matchMedia,
 * 数值在此登记为 JS 侧唯一真源): <600 phone / 600-1024 tablet / >1024 desktop。
 * matchMedia 不可用(测试 jsdom / SSR)时按 desktop 完整形态兜底 —— 降级宁缺毋滥。
 */

export type Breakpoint = 'phone' | 'tablet' | 'desktop'

export const BP_PHONE = 600
export const BP_DESKTOP = 1024
/** phone 档布局上限(不含): 低于此宽度左导航抽屉化。834 = iPad 竖屏, 它必须吃到 tablet 完整布局。 */
export const BP_PHONE_LAYOUT = 834

const PHONE_Q = `(max-width: ${BP_PHONE - 1}px)`
const TABLET_Q = `(max-width: ${BP_DESKTOP}px)`

function canMatch(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
}

function readBreakpoint(): Breakpoint {
  if (!canMatch()) return 'desktop'
  if (window.matchMedia(PHONE_Q).matches) return 'phone'
  if (window.matchMedia(TABLET_Q).matches) return 'tablet'
  return 'desktop'
}

/** 三档断点: 'phone' | 'tablet' | 'desktop'。 */
export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>(readBreakpoint)
  useEffect(() => {
    if (!canMatch()) return
    const mqs = [window.matchMedia(PHONE_Q), window.matchMedia(TABLET_Q)]
    const onChange = () => setBp(readBreakpoint())
    mqs.forEach((mq) => mq.addEventListener('change', onChange))
    onChange()
    return () => mqs.forEach((mq) => mq.removeEventListener('change', onChange))
  }, [])
  return bp
}

/** 通用单条 media query hook(与 useBreakpoint 同机制)。matchMedia 不可用时恒 false。 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => (canMatch() ? window.matchMedia(query).matches : false))
  useEffect(() => {
    if (!canMatch()) return
    const mq = window.matchMedia(query)
    const onChange = () => setMatches(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return matches
}

/** 触屏语义档: 非桌面断点或 coarse 指针。触控目标 ≥44、hover 只是增强不是唯一路径。 */
export function useTouchMode(bp: Breakpoint): boolean {
  const coarse = useMediaQuery('(pointer: coarse)')
  return coarse || bp !== 'desktop'
}
