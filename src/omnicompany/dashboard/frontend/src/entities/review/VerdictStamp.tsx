// 裁决印章 —— 通过/驳回落定时盖一下, 然后退场。
//
// 用户裁决: 纸质感只做局部小巧思, 不能给审阅出障碍。所以这里刻意不做常驻水印:
//   · 只在 status 从"待审"翻到"已通过/已拒绝/已阻断"的那一刻播一次;
//   · 播完停留 900ms 淡出并从 DOM 摘掉, 正文一秒钟都不被压住;
//   · 全程 pointer-events:none, 且 aria-hidden —— 状态的可访问真值仍在顶栏 .v2-status 上。
// 首次挂载不播(打开一个早就审过的材料不该有开场表演), 降低动态效果偏好下直接不播。
import { useEffect, useRef, useState } from 'react'
import type { MaterialStatus } from '../../api/reviewstageClient'
import { prefersReducedMotion } from '../../lib/motion'

const FACE: Partial<Record<MaterialStatus, { text: string; sub: string; rejected?: boolean }>> = {
  accepted: { text: '通过', sub: 'APPROVED' },
  rejected: { text: '驳回', sub: 'REJECTED', rejected: true },
  blocked: { text: '阻断', sub: 'BLOCKED', rejected: true },
}

const HOLD_MS = 900
const FADE_MS = 300

export function VerdictStamp({ status }: { status: MaterialStatus }) {
  const [shown, setShown] = useState<MaterialStatus | null>(null)
  const [leaving, setLeaving] = useState(false)
  const prev = useRef<MaterialStatus | null>(null)

  useEffect(() => {
    const before = prev.current
    prev.current = status
    // 首次挂载只记录, 不播
    if (before === null) return
    if (before === status) return
    if (!FACE[status]) return
    if (prefersReducedMotion()) return

    setShown(status)
    setLeaving(false)
    const t1 = setTimeout(() => setLeaving(true), HOLD_MS)
    const t2 = setTimeout(() => setShown(null), HOLD_MS + FADE_MS)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [status])

  if (!shown) return null
  const face = FACE[shown]!

  return (
    <span
      className={`bp-stamp mo-stamp${face.rejected ? ' rejected' : ''}`}
      data-testid="verdict-stamp"
      aria-hidden="true"
      style={{
        opacity: leaving ? 0 : undefined,
        transition: leaving ? `opacity ${FADE_MS}ms var(--mo-accelerate)` : undefined,
      }}
    >
      <span>{face.text}<small>{face.sub}</small></span>
    </span>
  )
}

export default VerdictStamp
