import React from 'react'
import { colors, spacing, fonts } from './tokens'

const S = {
  root: {
    padding: `${spacing.md}px ${spacing.lg}px`,
    borderBottom: `1px solid ${colors.border}`,
    background: colors.bgPanel,
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    gap: spacing.md,
    fontFamily: fonts.mono,
  } as React.CSSProperties,
  titleCol: { flex: 1, minWidth: 0 } as React.CSSProperties,
  title: {
    color: colors.accent,
    fontSize: 14,
    marginBottom: 2,
    display: 'flex',
    alignItems: 'center',
    gap: spacing.sm,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  } as React.CSSProperties,
  subtitle: {
    color: colors.textFaint,
    fontSize: 14,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  } as React.CSSProperties,
  actions: { display: 'flex', alignItems: 'center', gap: spacing.sm } as React.CSSProperties,
}

interface Props {
  title: React.ReactNode
  subtitle?: React.ReactNode
  /** Optional right-aligned action area (buttons, status text, etc.). */
  actions?: React.ReactNode
}

/** Standard tab/pane header — title + subtitle + right actions. */
export default function PaneHeader({ title, subtitle, actions }: Props) {
  return (
    <div style={S.root}>
      <div style={S.titleCol}>
        <div style={S.title}>{title}</div>
        {subtitle && <div style={S.subtitle}>{subtitle}</div>}
      </div>
      {actions && <div style={S.actions}>{actions}</div>}
    </div>
  )
}
