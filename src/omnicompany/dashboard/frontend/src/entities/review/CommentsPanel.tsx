/**
 * entities/review/CommentsPanel — C 区(评论/批注)独立面板。
 *
 * 评论自成一区, 与材料正文(B 区)同屏共存(用户 2026-06-14: "肯定要能够共存", 不是切走才能看)。
 * 读共享 store 的"激活材料 id"决定看哪条材料的评论, 随激活材料联动; 锚点也走共享 store,
 * B 区正文圈一段文字 → 这里追加框接住。Dashboard 默认形态挂在当前审阅页签的伴随视图;
 * VSCode 原生形态(surface=comments)整块挂进次级侧栏 —— 同一份组件, 只是挂载位置不同。
 */
import React, { useEffect, useState } from 'react'
import { MessageSquare } from 'lucide-react'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { useReviewStream } from './streamStore'
import { useReviewActive } from '../../stores/reviewActiveStore'
import { COLORS } from './shared'

// CommentsFileView 懒加载(2026-07 首屏拆包): 它静态引 @wiki-core/render(markdown-it + 第二份
// katex, ~300KB), CommentsPanel 是审阅页签的按需伴随视图, 直引会把渲染核钉进首屏主包。真有激活材料、
// 首次渲染评论区时才下载该 chunk。
const CommentsFileView = React.lazy(() => import('./CommentsFileView').then((m) => ({ default: m.CommentsFileView })))

export function CommentsPanel({ headerActions }: { headerActions?: React.ReactNode }) {
  const activeId = useReviewActive((s) => s.activeMaterialId)
  const pendingAnchor = useReviewActive((s) => (
    s.pendingAnchorMaterialId === s.activeMaterialId ? s.pendingAnchor : null
  ))
  const clearPendingAnchor = useReviewActive((s) => s.clearPendingAnchor)
  const streamMaterial = useReviewStream((s) => (activeId ? s.materials[activeId] : undefined))
  const [material, setMaterial] = useState<Material | null>(null)

  // WS 流引用计数(评论文件本身走 REST, 但材料元数据热更新靠流)。
  useEffect(() => useReviewStream.getState().acquire(), [])

  // 激活材料变了 → 先用流里的快照, 没有就拉一次。
  useEffect(() => {
    if (!activeId) { setMaterial(null); return }
    if (streamMaterial) { setMaterial(streamMaterial); return }
    let alive = true
    reviewstageApi.get(activeId).then((m) => { if (alive) setMaterial(m) }).catch(() => { /* 静默 */ })
    return () => { alive = false }
  }, [activeId, streamMaterial])

  // 激活材料后整片交给 CommentsFileView(玻璃卡解剖 + ⋯ 收纳低频操作); 这里只管空态外壳。
  // frostpane 标准: 无重复页签名的面板标题头 + root 透明吃全局冷渐变。空态只留一条右对齐
  // 工具条承接 headerActions(无标题), 居中给"是什么 + 下一步"的引导卡(非整段说明), 信息层级靠字号。
  return (
    <div data-testid="comments-panel" style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', background: 'transparent', color: COLORS.text }}>
      {material
        ? (
          <React.Suspense fallback={<div style={{ padding: 24, fontSize: 13, color: COLORS.textDim }}>加载评论区…</div>}>
            <CommentsFileView material={material} title={material.title} headerActions={headerActions} pendingAnchor={pendingAnchor} clearPendingAnchor={clearPendingAnchor} />
          </React.Suspense>
        )
        : (
          <>
            <div style={{ flexShrink: 0, minHeight: 48, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 12px 8px 16px', borderBottom: `1px solid ${COLORS.border}` }}>
              <strong style={{ fontSize: 14, color: COLORS.text }}>评价与批注</strong>
              {headerActions}
            </div>
            <div style={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
              <div style={{ maxWidth: 280, textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                <span aria-hidden style={{
                  width: 44, height: 44, borderRadius: 11, display: 'grid', placeItems: 'center',
                  background: 'var(--fp-accent-weak)', border: `1px solid ${COLORS.border}`, color: COLORS.borderActive,
                }}>
                  <MessageSquare size={20} />
                </span>
                <div style={{ fontSize: 15, fontWeight: 600, color: COLORS.text }}>暂无评论</div>
                <div style={{ fontSize: 13, color: COLORS.textDim, lineHeight: 1.5 }}>选中一条审阅材料, 这里显示它的评论。</div>
              </div>
            </div>
          </>
        )}
    </div>
  )
}
