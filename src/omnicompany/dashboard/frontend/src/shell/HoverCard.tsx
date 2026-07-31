import React, { useEffect, useRef, useState } from 'react'
import type { EntityType } from '../entities/types'
import { colors, fonts } from './tokens'

interface CardData { title: string; preview: string; meta?: string }

async function fetchPreview(type: EntityType, id: string): Promise<CardData> {
  switch (type) {
    case 'note': {
      const r = await fetch(`/api/notes/${id}`)
      if (!r.ok) throw new Error(`${r.status}`)
      const d = await r.json()
      return {
        title: d.title || id,
        preview: (d.content || '').slice(0, 400),
        meta: d.path,
      }
    }
    case 'worker': {
      const r = await fetch(`/api/workers/${id}`)
      if (!r.ok) throw new Error(`${r.status}`)
      const d = await r.json()
      return {
        title: d.name,
        preview: (d.design_md || d.source || '').slice(0, 400),
        meta: d.package,
      }
    }
    case 'plan': {
      const r = await fetch(`/api/plans/${id}`)
      if (!r.ok) throw new Error(`${r.status}`)
      const d = await r.json()
      return {
        title: d.topic,
        preview: `${d.files?.length || 0} 文件 · ${d.date || ''}`,
        meta: d.folder_path,
      }
    }
    case 'trace': {
      const r = await fetch(`/api/v2/trace-detail/${id}`)
      if (!r.ok) throw new Error(`${r.status}`)
      const d = await r.json()
      const events = d.events || []
      const first = events[0]
      return {
        title: id.slice(0, 24),
        preview: events.slice(0, 4).map((e: any) => `${e.event_type} (${e.source})`).join('\n') || '(无事件)',
        meta: `${events.length} events · ${first?.source || ''}`,
      }
    }
    case 'session': {
      const r = await fetch(`/api/v2/ide/sessions`)
      if (!r.ok) throw new Error(`${r.status}`)
      const sessions = await r.json()
      const s = sessions.find((x: any) => x.trace_id === id)
      if (!s) return { title: id, preview: '会话不存在', meta: '' }
      return { title: s.task_desc || id.slice(0, 24), preview: s.task_desc || '', meta: `status: ${s.status}` }
    }
    default:
      return { title: id, preview: `entity type: ${type}`, meta: '' }
  }
}

const S = {
  card: {
    position: 'fixed' as const,
    zIndex: 9999,
    background: '#0d0d0d',
    border: `1px solid #2a3a4a`,
    borderRadius: 6,
    padding: 12,
    width: 360,
    maxHeight: 320,
    overflow: 'auto' as const,
    boxShadow: '0 6px 24px rgba(0,0,0,.6)',
    fontFamily: fonts.mono,
    fontSize: 14,
    color: colors.textMuted,
  } as React.CSSProperties,
  type: { color: colors.textFaint, fontSize: 14, textTransform: 'uppercase' as const, marginBottom: 4 } as React.CSSProperties,
  title: { color: colors.accent, fontSize: 14, marginBottom: 4, wordBreak: 'break-all' as const } as React.CSSProperties,
  meta: { color: colors.textFaint, fontSize: 14, marginBottom: 8, wordBreak: 'break-all' as const } as React.CSSProperties,
  preview: { color: colors.textMuted, fontSize: 14, whiteSpace: 'pre-wrap' as const, lineHeight: 1.5, fontFamily: fonts.ui } as React.CSSProperties,
  err: { color: '#ef5350' } as React.CSSProperties,
  loading: { color: colors.textFaint, fontStyle: 'italic' as const } as React.CSSProperties,
}

interface HoverCardProps {
  type: EntityType
  id: string
  anchorEl: HTMLElement
  onClose: () => void
}

export default function HoverCard({ type, id, anchorEl, onClose }: HoverCardProps) {
  const [data, setData] = useState<CardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const cardRef = useRef<HTMLDivElement | null>(null)

  // Fetch preview
  useEffect(() => {
    let cancelled = false
    setData(null); setError(null)
    fetchPreview(type, id).then((d) => { if (!cancelled) setData(d) })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [type, id])

  // Position relative to anchor
  const rect = anchorEl.getBoundingClientRect()
  const top = Math.min(rect.bottom + 6, window.innerHeight - 340)
  const left = Math.min(rect.left, window.innerWidth - 380)

  return (
    <div ref={cardRef} style={{ ...S.card, top, left }} onMouseLeave={onClose}>
      <div style={S.type}>{type}</div>
      {error ? (
        <div style={S.err}>preview 失败: {error}</div>
      ) : !data ? (
        <div style={S.loading}>loading…</div>
      ) : (
        <>
          <div style={S.title}>{data.title}</div>
          {data.meta && <div style={S.meta}>{data.meta}</div>}
          <div style={S.preview}>{data.preview}</div>
        </>
      )}
    </div>
  )
}
