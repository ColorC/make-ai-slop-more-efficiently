import React, { useState } from 'react'
import { type Annotation } from './annotations'

interface Props {
  hash: string
  snippet: string
  matched: Annotation[]
  onAdd: (hash: string, snippet: string) => void
  onOpen: (hash: string) => void
  children: React.ReactNode
}

const S: Record<string, any> = {
  wrap: {
    position: 'relative' as const,
    paddingRight: 36,
  },
  badge: (hover: boolean): React.CSSProperties => ({
    position: 'absolute' as const,
    right: 0,
    top: 0,
    cursor: 'pointer',
    padding: '0 6px',
    fontSize: 14,
    color: hover ? '#90caf9' : '#666',
    border: '1px solid',
    borderColor: hover ? '#2a3a4a' : '#222',
    borderRadius: 3,
    background: 'var(--fp-bg)',
    fontFamily: 'Consolas, Menlo, monospace',
    userSelect: 'none' as const,
    transition: 'opacity 80ms',
  }),
  add: (hover: boolean): React.CSSProperties => ({
    position: 'absolute' as const,
    right: 0,
    top: 0,
    cursor: 'pointer',
    padding: '0 6px',
    fontSize: 15,
    color: '#666',
    border: '1px solid #222',
    borderRadius: 3,
    background: 'var(--fp-bg)',
    fontFamily: 'Consolas, Menlo, monospace',
    userSelect: 'none' as const,
    opacity: hover ? 1 : 0,
    transition: 'opacity 80ms',
  }),
}

export default function AnnotatedParagraph({ hash, snippet, matched, onAdd, onOpen, children }: Props) {
  const [hover, setHover] = useState(false)
  const has = matched.length > 0
  return (
    <div style={S.wrap} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      {children}
      {has ? (
        <span
          style={S.badge(hover)}
          title={`${matched.length} 条批注 · 点击查看`}
          onClick={(e) => { e.stopPropagation(); onOpen(hash) }}
        >💬 {matched.length}</span>
      ) : (
        <span
          style={S.add(hover)}
          title="添加批注"
          onClick={(e) => { e.stopPropagation(); onAdd(hash, snippet) }}
        >+</span>
      )}
    </div>
  )
}
