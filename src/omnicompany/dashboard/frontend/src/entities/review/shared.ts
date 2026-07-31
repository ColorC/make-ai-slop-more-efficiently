/**
 * entities/review/shared — 审阅台共享纯工具 (设计 token / 标签 / 纯函数).
 *
 * R2 从 standalone 审阅台剪切而来 (结构搬移, 行为零变化); R4 起 standalone 已退役,
 * 消费方为驾驶舱 review_queue / review_material 面板.
 */

import type * as React from 'react'
import type {
  Material,
  MaterialKind,
  MaterialStatus,
  MaterialTier,
  CommentFeedbackStatus,
} from '../../api/reviewstageClient'
import type { EntitySearchResult } from '../../api/entitiesClient'

// ── 设计 token ──────────────────────────────────────────────────────
// 2026-06-29 A0: 并入 frostpane 冷色(原 GitHub 黑并行调色板撤掉, 值对齐 shell/tokens)。
export const COLORS = {
  bg: 'var(--fp-bg)',
  panel: 'var(--fp-solid)',
  panelHover: 'var(--fp-card)',
  border: 'var(--fp-border)',
  borderActive: 'var(--fp-accent)',
  text: 'var(--fp-text)',
  textDim: 'var(--fp-text-2)',
  // tier 色
  mandatory: 'var(--fp-err)',
  important: 'var(--fp-warn)',
  processual: 'var(--fp-text-3)',
  ignored: '#5b6477',
  // status 色
  pending: 'var(--fp-warn)',
  accepted: 'var(--fp-ok)',
  rejected: 'var(--fp-err)',
  blocked: 'var(--fp-warn)',  // 2026-07-18 violet 退位: 阻断并入 warn(门禁/警告族)
}

export interface StructureWarning {
  code?: string
  severity?: string
  message?: string
  path?: string
}

export function getStructureWarnings(material: Material): StructureWarning[] {
  const raw = material.extra?.structure_warnings
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is StructureWarning => !!item && typeof item === 'object')
}

export interface EntityMention {
  uri: string
  display: string
  kind: string
  id: string
  title: string
}

export function getTargetMentions(target: Record<string, unknown> | undefined): EntityMention[] {
  const raw = target?.mentions
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is EntityMention =>
    !!item &&
    typeof item === 'object' &&
    typeof (item as any).uri === 'string' &&
    typeof (item as any).display === 'string',
  )
}

export function mentionFromResult(item: EntitySearchResult): EntityMention {
  return {
    uri: item.uri,
    display: item.display,
    kind: item.kind,
    id: item.id,
    title: item.title,
  }
}

export function findMentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at < 0) return null
  if (at > 0 && /\S/.test(before[at - 1])) return null
  const query = before.slice(at + 1)
  if (/\s/.test(query) || query.length > 80) return null
  return { start: at, query }
}

export const TIER_LABELS: Record<MaterialTier, string> = {
  mandatory: '必验收',
  important: '重要',
  processual: '过程性',
  ignored: '其余',
}

export const STATUS_LABELS: Record<MaterialStatus, string> = {
  pending: '待审',
  accepted: '已通过',
  rejected: '已拒绝',
  blocked: '已阻断',
}

export const FEEDBACK_LABELS: Record<CommentFeedbackStatus, string> = {
  delivered: '已送达',
  read: '已读',
  to_todo: '转 todo',
  todo_done: 'todo 完成',
}

// 状态 → v2-status 六态档(2026-07-19 蓝图 G;blocked 历史态并入 warn 族, 与 COLORS 同口径)。
export const STATUS_V2: Record<MaterialStatus, string> = {
  pending: 'st-warn', accepted: 'st-ok', rejected: 'st-err', blocked: 'st-warn',
}

/** plan 路径取末段名(kv/行内/预览卡只展示短名; 全路径在 title 悬浮)。 */
export function planShort(p?: string | null): string {
  if (!p) return '—'
  const segs = p.split('/').filter(Boolean)
  return segs[segs.length - 1] || p
}

export const KIND_LABELS: Record<MaterialKind, string> = {
  image: '图',
  markdown: '文档',
  html: '网页',
  key_question: '关键问题',
  custom_web_template: '自定义模板',
  video: '视频',
  plan: '计划',
  'static-report': '报告网页',
  demo: 'Demo',
  'aigc-image': 'AIGC 图',
  'agent-workflow-report': 'Agent 报告',
  'decision-candidate': '决策候选',
}

// 审阅来源 (从哪里进入 material 详情, "返回源"按钮用)
export interface ReviewSource {
  type: string
  id: string
  title?: string
}

// ── Helpers ─────────────────────────────────────────────────────────

export function batchButtonStyle(background: string): React.CSSProperties {
  return {
    minHeight: 28,
    padding: '5px 10px',
    background,
    color: '#fff',
    border: `1px solid ${COLORS.border}`,
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 14,
  }
}

export function tierColor(t: MaterialTier): string {
  return COLORS[t] || COLORS.ignored
}

export function statusColor(s: MaterialStatus): string {
  return COLORS[s] || COLORS.textDim
}

export function formatTs(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return iso }
}

// 聚合折叠(2026-07-07 用户: "审阅台该考虑聚合了, 同项目同类内容可以折叠"):
// 同 tier 内 (project, kind) 相同且 ≥CLUSTER_MIN 条 → 收成一张可展开的叠层卡, 默认折叠。
// ReviewQueueSidebar(驾驶舱左栏) 与 MaterialSidebar(双列审阅页) 共用。
export const CLUSTER_MIN = 3
export type ClusterRenderUnit<M extends { id: string; kind: string; project?: string }> =
  | { type: 'single'; m: M }
  | { type: 'cluster'; key: string; project: string; kind: M['kind']; items: M[] }

export function buildRenderUnits<M extends { id: string; kind: string; project?: string }>(
  tierItems: M[],
): ClusterRenderUnit<M>[] {
  const counts = new Map<string, number>()
  for (const m of tierItems) {
    const k = `${m.project || 'unfiled'}|${m.kind}`
    counts.set(k, (counts.get(k) || 0) + 1)
  }
  const emitted = new Set<string>()
  const units: ClusterRenderUnit<M>[] = []
  for (const m of tierItems) {
    const k = `${m.project || 'unfiled'}|${m.kind}`
    if ((counts.get(k) || 0) >= CLUSTER_MIN) {
      if (!emitted.has(k)) {
        emitted.add(k)
        units.push({
          type: 'cluster', key: k, project: m.project || 'unfiled', kind: m.kind,
          items: tierItems.filter((x) => `${x.project || 'unfiled'}|${x.kind}` === k),
        })
      }
    } else {
      units.push({ type: 'single', m })
    }
  }
  return units
}
