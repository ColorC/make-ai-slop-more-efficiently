import React from 'react'
import { colors, spacing, fonts } from './tokens'

interface Props {
  /** Standard messages: '无 X' for missing-data, 'X 占位' for stub, 'X 加载中' for loading. */
  text: string
  hint?: React.ReactNode
  size?: 'mini' | 'normal' | 'large'
}

const SIZES: Record<NonNullable<Props['size']>, { fontSize: number; padding: number }> = {
  mini: { fontSize: 14, padding: spacing.sm },
  normal: { fontSize: 14, padding: spacing.lg },
  large: { fontSize: 15, padding: spacing.xxl },
}

/** Standard empty-state placeholder. Use across sidebar groups, panels, lists.
 *  Per user round 28: drop italic (hard to read at small sizes), use textMuted
 *  (#bbb) instead of textGhost (#444) — empty state is informative not invisible.
 */
export default function EmptyState({ text, hint, size = 'normal' }: Props) {
  const s = SIZES[size]
  return (
    <div style={{
      padding: s.padding,
      color: colors.textMuted,
      fontSize: s.fontSize,
      fontFamily: fonts.mono,
    }}>
      {text}
      {hint && <div style={{ color: colors.textFaint, marginTop: spacing.sm }}>{hint}</div>}
    </div>
  )
}
