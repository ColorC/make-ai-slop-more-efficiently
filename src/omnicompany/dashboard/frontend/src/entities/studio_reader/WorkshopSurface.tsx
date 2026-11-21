import { useEffect, useRef } from 'react'
import { workshopSurfaceUrl, type WorkshopSurfaceKind } from '../../lib/workshopSurface'

export function WorkshopSurface({
  kind, project, materialId, materialViewId, canvas, note, sessionId, card, title, onReady, onError,
}: {
  kind: WorkshopSurfaceKind
  project?: string
  materialId?: string
  materialViewId?: string
  canvas?: string
  note?: string
  sessionId?: string
  card?: string
  title?: string
  onReady?: () => void
  onError?: (error: string) => void
}) {
  const src = workshopSurfaceUrl(kind, {
    project,
    id: materialId?.replace(/^material-wiki:/, ''),
    view: materialViewId,
    canvas,
    note,
    session: sessionId,
    card,
  })
  const frameRef = useRef<HTMLIFrameElement>(null)
  const probeTimer = useRef<number | null>(null)

  // canvas 面(notes-web)有真实的就绪协议 omni-notes-ready / omni-notes-error(同源
  // postMessage)。iframe onLoad 不作为它的就绪信号: 加载成功但内部水合失败时,
  // 门必须留在原地展示错误与恢复入口, 而不是被过早清掉变成"看似正常的空白"。
  // materials 面(workshop Vue)没有该协议, onLoad 即就绪。
  // 2026-08-16 兜底: 就绪协议是 engine 专有的; 原生画布/其他接入方没有该协议会永远
  // 卡在"正在打开画布…"。onLoad 后 2.5s 做同源 DOM 探测 —— 有实际内容(画布节点/
  // 应用根文本)就放行; 没有则继续等 engine 协议或慢门恢复, 错误展示语义不变。
  useEffect(() => {
    if (kind !== 'canvas') return
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return
      const data = event.data
      if (!data || typeof data !== 'object') return
      if (data.type === 'omni-notes-ready') onReady?.()
      else if (data.type === 'omni-notes-error') onError?.(String(data.error || '画布打开失败'))
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [kind, onError, onReady])

  useEffect(() => () => {
    if (probeTimer.current) window.clearTimeout(probeTimer.current)
  }, [])

  const startCanvasContentProbe = () => {
    if (probeTimer.current) window.clearTimeout(probeTimer.current)
    probeTimer.current = window.setTimeout(() => {
      try {
        const doc = frameRef.current?.contentDocument
        if (!doc) return
        // 2026-08-18 统一应用鉴权: iframe 里出现网关登录表单 = 会话面鉴权失效,
        // 上报为 auth-required, 由宿主界面引导重新认证, 而不是把登录表单留在画布里。
        if (doc.querySelector('#lf, input[type="password"]')) {
          onError?.('auth-required')
          return
        }
        const hasContent = Boolean(
          doc.querySelector('.vue-flow__node, [data-material-canvas], .material-notes-workspace')
          || (doc.body?.innerText?.trim().length ?? 0) > 40,
        )
        if (hasContent) onReady?.()
      } catch { /* 跨源探测失败(同源代理下不应发生)——静默, 继续等协议 */ }
    }, 2500)
  }

  return (
    <iframe
      ref={frameRef}
      title={title ?? (kind === 'materials' ? '资料' : '画布')}
      src={src}
      data-testid={`workshop-surface-${kind}`}
      data-workshop-src={src}
      onLoad={() => { if (kind !== 'canvas') onReady?.(); else startCanvasContentProbe() }}
      style={{ width: '100%', height: '100%', border: 0, background: 'transparent', display: 'block' }}
    />
  )
}
