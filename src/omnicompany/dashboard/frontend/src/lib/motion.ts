// 动效系统的 JS 侧小件(样式主体在 styles/motion.css)。
// 只放"CSS 表达不了"的那几样, 其余一律留在 CSS —— 动效跑在合成器上才不吃性能。

import { useEffect, useRef, useState } from 'react'

/**
 * 折叠容器收起时把子树标记为 inert。
 *
 * .mo-collapse 用 grid-template-rows: 0fr 把内容压扁而不是 display:none —— 这样才能动画,
 * 但代价是内容仍在无障碍树与 Tab 序列里(视觉上看不见的按钮还能被聚焦/点击)。inert 是这件事的
 * 标准解, Chromium 原生支持。React 18 的类型里还没有这个属性, 故在此收敛成一处断言,
 * 不让每个调用点各写各的 as any。
 */
export function inertWhen(collapsed: boolean): Record<string, unknown> {
  return collapsed ? { inert: '', 'aria-hidden': true } : {}
}

/** 是否处于"降低动态效果"偏好下 —— JS 侧动效(计数滚动等)也要跟着让路。 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * 数值徐变: 计数/度量值从旧值滚到新值, 而不是直接跳。
 *
 * 用 requestAnimationFrame 而不是 setInterval —— 后者与刷新率不同步, 在 120Hz 屏上会抖。
 * 首次挂载不播(从 0 滚到 137 是噪音, 用户要的是"这个数变了"的提示, 不是开场表演)。
 * 变化幅度小于 2 也不播, 免得列表里一堆数字持续微动。
 */
export function useCountUp(value: number, duration = 420): number {
  const [shown, setShown] = useState(value)
  const fromRef = useRef(value)
  const firstRef = useRef(true)

  useEffect(() => {
    const from = fromRef.current
    fromRef.current = value
    if (firstRef.current) {
      firstRef.current = false
      setShown(value)
      return
    }
    if (prefersReducedMotion() || Math.abs(value - from) < 2) {
      setShown(value)
      return
    }
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / duration)
      // easeOutCubic —— 与 CSS 侧 --mo-decelerate 同一手感
      const eased = 1 - Math.pow(1 - p, 3)
      setShown(Math.round(from + (value - from) * eased))
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value, duration])

  return shown
}

/**
 * 一次性重放某个入场动画: 依赖变化时换 key, 让元素重新挂载从而重播 animation。
 * 比手动 remove/add class 可靠 —— 后者要强制 reflow 才能重启动画。
 */
export function useReplayKey(dep: unknown): number {
  const [k, setK] = useState(0)
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    setK((n) => n + 1)
  }, [dep])
  return k
}
