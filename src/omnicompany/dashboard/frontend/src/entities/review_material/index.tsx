/**
 * entities/review_material — 驾驶舱内"单条审阅材料"页签 (R3).
 *
 * 多实例: 每条材料一个页签 (tab id = review_material:<材料id>, 与 cc_session 同模式,
 * 走 panelsStore.openTab 默认分支, 无 review_queue 那种单例特判)。
 * 面板复用 entities/review 的 MaterialDetail 全链路 (富渲染 5 类 + 圈选/文本定位 +
 * 批注评论(@mention/反馈状态) + verdict 三键带理由 + 4 级调级), 数据走 reviewstageApi;
 * 实时刷新订阅 entities/review/streamStore (WS 单连接)。
 * "Return source" 在驾驶舱语义 = 激活 review_queue 单例页签并聚焦本材料。
 */

import React, { useCallback, useEffect, useState } from 'react'
import {
  reviewstageApi,
  type Material,
  type MaterialStatus,
  type MaterialTier,
  type CommentFeedbackStatus,
} from '../../api/reviewstageClient'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { usePanels } from '../../stores/panelsStore'
import { COLORS, MaterialDetail } from '../review'
import { useReviewStream } from '../review/streamStore'
import { openMaterialNative } from '../../lib/surface'
import { VscodeIcon } from '../../components/VscodeIcon'
import { TabSidecarToggleButton } from '../../shell/TabSidecar'
import { useReviewActive } from '../../stores/reviewActiveStore'

export interface ReviewMaterialEntity extends Entity {
  type: 'review_material'
}

/** 页签标题 = 材料标题截断 (dockview 页签条空间有限)。 */
export function materialTabTitle(title: string, max = 24): string {
  const t = (title || '').trim() || '(untitled)'
  return t.length > max ? `${t.slice(0, max - 1)}…` : t
}

function toEntity(m: Material): ReviewMaterialEntity {
  return { type: 'review_material', id: m.id, title: m.title, tags: [m.tier, m.status] }
}

const resolver: EntityResolver<ReviewMaterialEntity> = {
  type: 'review_material',
  async fetch(id) {
    // 后端已有 GET /api/boss-sight/reviewstage/{id} (reviewstageApi.get), 不走 list 过滤。
    return toEntity(await reviewstageApi.get(id))
  },
  async list() {
    const r = await reviewstageApi.list()
    return r.items.map(toEntity)
  },
}

function errText(e: unknown): string {
  return String(e instanceof Error ? e.message : e)
}

export function ReviewMaterialPanel({ id, embedded = false }: { id: string; embedded?: boolean }) {
  const [material, setMaterial] = useState<Material | null>(null)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((s) => s.openTab)
  const streamMaterial = useReviewStream((s) => s.materials[id])
  const setActiveMaterial = useReviewActive((s) => s.setActiveMaterial)

  // WS 实时流: 引用计数订阅 (连接生命周期在 streamStore, 不挂本面板, 详见该文件头注释)。
  useEffect(() => useReviewStream.getState().acquire(), [])

  // 单条材料页签本身就是评价侧栏的选择源；页签激活/重新挂载时立即对齐，
  // 避免侧栏已经展开却仍显示“选中一条审阅材料”的空态。
  useEffect(() => {
    setActiveMaterial(id, 'local')
  }, [id, setActiveMaterial])

  useEffect(() => {
    let alive = true
    setMaterial(null)
    setError(null)
    reviewstageApi.get(id)
      .then((m) => { if (alive) setMaterial(m) })
      .catch((e) => { if (alive) setError(errText(e)) })
    return () => { alive = false }
  }, [id])

  // 流事件携带完整材料 → 热更新本面板 (AI/别处加的评论、verdict 变化即时可见)。
  useEffect(() => {
    if (streamMaterial) setMaterial(streamMaterial)
  }, [streamMaterial])

  const onVerdict = useCallback(async (verdict: MaterialStatus, reason: string) => {
    try {
      setMaterial(await reviewstageApi.setVerdict(id, verdict, reason))
      setError(null)
    } catch (e) {
      setError(`verdict 失败: ${errText(e)}`)
    }
  }, [id])

  const onCommentSubmit = useCallback(async (content: string, target?: Record<string, unknown>) => {
    try {
      await reviewstageApi.addComment(id, content, target)
      setMaterial(await reviewstageApi.get(id))
      setError(null)
    } catch (e) {
      setError(`评论失败: ${errText(e)}`)
    }
  }, [id])

  const onFeedbackChange = useCallback(async (commentId: string, status: CommentFeedbackStatus) => {
    try {
      await reviewstageApi.setCommentFeedback(id, commentId, status)
      setMaterial(await reviewstageApi.get(id))
      setError(null)
    } catch (e) {
      setError(`反馈状态失败: ${errText(e)}`)
    }
  }, [id])

  const onTierChange = useCallback(async (tier: MaterialTier) => {
    try {
      setMaterial(await reviewstageApi.setTier(id, tier))
      setError(null)
    } catch (e) {
      setError(`调级失败: ${errText(e)}`)
    }
  }, [id])

  // "Return source": 激活 review_queue 单例页签并聚焦本材料 (facet 经 panelsStore 转聚焦 store)。
  const onReturnToSource = useCallback(() => {
    openTab({ type: 'review_queue', id: 'main' }, 'Review Queue', id)
  }, [openTab, id])

  return (
    <div
      // 外壳透明: 让背后冷色渐变透出来; 内容(MaterialDetail)自带安静近实色阅读面
      style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'transparent', color: COLORS.text }}
      data-testid="review-material-panel"
    >
      {error && (
        // 错误 = 玻璃浮层告警 (磨砂 + 语义 err 染色 + 边缘高光), 从边缘抬起一点像悬浮通知
        <div
          style={{
            margin: '12px 16px 0',
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            padding: '10px 14px',
            background: 'rgba(58,26,28,0.5)',
            backdropFilter: 'var(--fp-blur)',
            WebkitBackdropFilter: 'var(--fp-blur)',
            border: `1px solid ${COLORS.rejected}55`,
            borderRadius: 11,
            boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
            color: COLORS.rejected,
            fontSize: 13,
            lineHeight: 1.5,
          }}
          data-testid="review-material-error"
        >
          <span style={{ fontSize: 14, fontWeight: 650, flexShrink: 0, letterSpacing: '-0.01em' }}>出错</span>
          <span style={{ color: COLORS.text, minWidth: 0, wordBreak: 'break-word' }}>{error}</span>
        </div>
      )}
      {!material && !error && (
        // 加载态: 安静弱灰, 字号拉开层级 (主提示 13 + 微字旁注收进同一行)
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 24, color: COLORS.textDim, fontSize: 13 }}>
          <span style={{ width: 12, height: 12, flexShrink: 0, borderRadius: '50%', border: `2px solid ${COLORS.borderActive}`, borderRightColor: 'transparent', animation: 'reviewMaterialSpin 1s linear infinite' }} />
          加载材料中…
          <style>{'@keyframes reviewMaterialSpin{to{transform:rotate(360deg)}}'}</style>
        </div>
      )}
      {material && (
        <MaterialDetail
          material={material}
          headerLeft={(
            <TabSidecarToggleButton
              label="评价与批注"
              showWhen="collapsed"
              testId="review-comments-toggle"
            />
          )}
          onVerdict={onVerdict}
          onCommentSubmit={onCommentSubmit}
          onFeedbackChange={onFeedbackChange}
          onTierChange={onTierChange}
          source={embedded ? null : { type: 'review_queue', id: 'main', title: 'Review Queue' }}
          onReturnToSource={onReturnToSource}
          // 顶栏精简(DEC-2026-07-05-003): 消费方动作一律收进「更多」
          moreItems={embedded ? undefined : [{
            label: '在 VSCode 编辑页签打开',
            icon: <VscodeIcon size={15} />,
            testid: 'material-open-vscode',
            onClick: () => openMaterialNative(id, material.title),
          }]}
        />
      )}
    </div>
  )
}

const Editor: React.FC<{ entity: ReviewMaterialEntity; facet?: string }> = ({ entity }) => (
  <ReviewMaterialPanel id={entity.id} />
)

export const reviewMaterialRegistration: EntityRegistration<ReviewMaterialEntity> = {
  resolver,
  renderer: { type: 'review_material', Editor },
  label: '审阅材料',
  icon: '🔍',
}
