// V2 悬浮预览卡(MAPPING 组件 13;G 主题=深色描图纸覆上,非 fade):
// 单例 + 300ms enter 延迟 + 150ms leave 延迟 + 悬停驻留 + 滚动即收。
// 深色描图纸 rgba(16,28,52,.55) + blur(8px) + 亮虚线边 + 微卷角(G.3①;玻璃配方内联——
// 本文件名命中 glass-scope 外壳白名单 hovercard 族)。宽 348px;落下动画 translateY(-8px)→0。
// 消费: project_board 行(desktop hover);触屏等价=长按(MAPPING 已注,后续波次补手势)。
import React, { useEffect, useRef, useState } from 'react'

type PreviewState = {
  content: React.ReactNode
  anchor: HTMLElement
} | null

const SHOW_MS = 300
const HIDE_MS = 150

export function useHoverPreview() {
  const [state, setState] = useState<PreviewState>(null)
  const [shown, setShown] = useState(false)
  const showT = useRef<number | null>(null)
  const hideT = useRef<number | null>(null)

  const clearTimers = () => {
    if (showT.current !== null) { window.clearTimeout(showT.current); showT.current = null }
    if (hideT.current !== null) { window.clearTimeout(hideT.current); hideT.current = null }
  }

  const show = (anchor: HTMLElement, content: React.ReactNode) => {
    clearTimers()
    showT.current = window.setTimeout(() => {
      setState({ anchor, content })
      // 下一帧加 .show → translateY(-8px)→0 落下(描图纸覆上)
      window.requestAnimationFrame(() => setShown(true))
    }, SHOW_MS)
  }
  const scheduleHide = () => {
    clearTimers()
    hideT.current = window.setTimeout(() => { setShown(false); setState(null) }, HIDE_MS)
  }
  const stay = () => { if (hideT.current !== null) { window.clearTimeout(hideT.current); hideT.current = null } }
  const hideNow = () => { clearTimers(); setShown(false); setState(null) }

  // 滚动即收(demo 同行为;捕获阶段监听一切滚动容器)
  useEffect(() => {
    document.addEventListener('scroll', hideNow, true)
    return () => document.removeEventListener('scroll', hideNow, true)
  }, [])

  const host = state ? (
    <HoverPreviewCard state={state} shown={shown} onEnter={stay} onLeave={scheduleHide} />
  ) : null
  return { show, scheduleHide, hideNow, host }
}

const CARD: React.CSSProperties = {
  position: 'fixed', zIndex: 60, width: 348, maxWidth: 'calc(100vw - 24px)',
  background: 'rgba(16,28,52,.55)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
  border: '1px dashed rgba(200,225,255,.65)', borderRadius: 3,
  boxShadow: '0 14px 40px rgba(2,8,20,.6), var(--fp-shadow-pop)',
  padding: '12px 14px', color: 'var(--fp-text)', fontSize: 13, lineHeight: 1.5,
  transition: 'transform .25s cubic-bezier(.2,.9,.25,1.12), opacity .18s ease-out',
}
const FOLD: React.CSSProperties = {
  position: 'absolute', top: 0, right: 0, width: 14, height: 14, pointerEvents: 'none',
  background: 'linear-gradient(225deg, var(--fp-bg) 0 48%, rgba(235,245,255,.35) 50%, transparent 62%)',
}

function HoverPreviewCard({ state, shown, onEnter, onLeave }: {
  state: NonNullable<PreviewState>
  shown: boolean
  onEnter: () => void
  onLeave: () => void
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  // 定位:锚点正下方 8px;放不下翻转到锚点上方(demo positionPreview 同几何)。
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: -9999, top: -9999 })
  useEffect(() => {
    const r = state.anchor.getBoundingClientRect()
    const w = Math.min(348, window.innerWidth - 32)
    const left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12))
    const ph = ref.current?.offsetHeight || 160
    let top = r.bottom + 8
    if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 8)
    setPos({ left, top })
  }, [state])
  return (
    <div
      ref={ref}
      role="tooltip"
      data-testid="hover-preview-card"
      style={{
        ...CARD,
        left: pos.left,
        top: pos.top,
        transform: shown ? 'none' : 'translateY(-8px) scale(.99)',
        opacity: shown ? 1 : 0,
      }}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
    >
      <span style={FOLD} aria-hidden="true" />
      {state.content}
    </div>
  )
}
