// 项目详情页共享卡片底座 — 2026-07-06 从 ProjectDetail.tsx 抽出, 供 ProjectDetail /
// ProjectSkills(技能页签) / ProjectFileTree(文件页签) 三处复用同一套玻璃卡片形态,
// 避免新页签各自手搓样式(用户规范: 用已有成品组件)。样式值与抽出前逐字一致。

import React from 'react'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'

// frostpane 玻璃外壳配方 (磨砂 + saturate + 顶部高光 inset), 复用于卡片/面板
export const GLASS = {
  background: 'var(--fp-glass)',
  backdropFilter: 'var(--fp-blur)',
  WebkitBackdropFilter: 'var(--fp-blur)',
  border: '1px solid var(--fp-border)',
  borderRadius: 11,
  boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
} as const
export const MONO = "'Berkeley Mono','Consolas','Menlo',monospace"
export const EASE = 'cubic-bezier(0.175,0.885,0.32,1.1)'

export const gridStyle: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12,
}
export const dimStyle: React.CSSProperties = {
  color: 'var(--fp-text-3)', fontSize: 13, padding: 24, textAlign: 'center',
}
export const secTitleStyle: React.CSSProperties = {
  color: 'var(--fp-text-2)', fontSize: 13, fontWeight: 650, letterSpacing: '.02em', margin: '24px 0 12px',
}

const C: Record<string, any> = {
  itemCard: { ...GLASS, display: 'flex', flexDirection: 'column', minWidth: 0, padding: 14, cursor: 'pointer', transition: `border-color 150ms ${EASE}` },
  itemTop: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 },
  typeBadge: { display: 'inline-flex', alignItems: 'center', gap: 4, flexShrink: 0, color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 600 },
  itemTitle: { color: 'var(--fp-text)', fontSize: 15, fontWeight: 600, letterSpacing: '-0.005em', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  itemMeta: { color: 'var(--fp-text-3)', fontSize: 12, fontFamily: MONO, marginTop: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  openBtn: { marginTop: 12, width: '100%', boxSizing: 'border-box', border: '1px solid var(--fp-border)', background: 'color-mix(in srgb, var(--fp-accent) 10%, transparent)', color: 'var(--fp-link)', borderRadius: 7, padding: '7px 0', fontSize: 13, fontWeight: 600, cursor: 'pointer', transition: `all 150ms ${EASE}` },
}

// 卡片 hover 描边反馈(整套卡片共用)
export const cardHover = {
  onMouseEnter: (e: React.MouseEvent<HTMLDivElement>) => { e.currentTarget.style.borderColor = 'var(--fp-border-strong)' },
  onMouseLeave: (e: React.MouseEvent<HTMLDivElement>) => { e.currentTarget.style.borderColor = 'var(--fp-border)' },
}
export const openBtnHover = {
  onMouseEnter: (e: React.MouseEvent<HTMLButtonElement>) => { e.currentTarget.style.background = 'color-mix(in srgb, var(--fp-accent) 18%, transparent)'; e.currentTarget.style.borderColor = 'var(--fp-border-strong)' },
  onMouseLeave: (e: React.MouseEvent<HTMLButtonElement>) => { e.currentTarget.style.background = 'color-mix(in srgb, var(--fp-accent) 10%, transparent)'; e.currentTarget.style.borderColor = 'var(--fp-border)' },
}

/** 通用内容卡: 类型徽章 + flex1 醒目标题 + ⋯收纳低频 + 弱灰等宽元信息 + 底部整宽打开。 */
export function ItemCard({ icon, badge, title, titleAttr, meta, onOpen, openLabel = '打开', openTestid, kebab, kebabTestid, cardTestid }: {
  icon: React.ReactNode
  badge?: string
  title: string
  titleAttr?: string
  meta?: React.ReactNode
  onOpen: () => void
  openLabel?: string
  openTestid?: string
  kebab?: KebabItem[]
  kebabTestid?: string
  cardTestid?: string
}) {
  return (
    <div style={C.itemCard} {...cardHover} onClick={onOpen} data-testid={cardTestid}>
      <div style={C.itemTop}>
        <span style={C.typeBadge}>{icon}{badge}</span>
        <span style={C.itemTitle} title={titleAttr || title}>{title}</span>
        {kebab && kebab.length > 0 && (
          <span onClick={(e) => e.stopPropagation()}>
            <KebabMenu testid={kebabTestid} items={kebab} />
          </span>
        )}
      </div>
      {meta != null && <div style={C.itemMeta}>{meta}</div>}
      <button type="button" style={C.openBtn} {...openBtnHover} data-testid={openTestid}
        onClick={(e) => { e.stopPropagation(); onOpen() }}>{openLabel}</button>
    </div>
  )
}
