import React, { useEffect } from 'react'

const S: Record<string, any> = {
  backdrop: {
    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
    background: 'rgba(0,0,0,0.55)', zIndex: 9998,
    display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: 100,
  },
  panel: {
    width: 520, maxWidth: '90vw', background: '#0d0d0d', border: '1px solid #2a3a4a',
    borderRadius: 6, boxShadow: '0 6px 32px rgba(0,0,0,.6)', overflow: 'hidden',
    fontFamily: 'Consolas, Menlo, monospace',
  },
  header: { padding: '10px 14px', color: '#90caf9', borderBottom: '1px solid #222', fontSize: 14 },
  body: { padding: 14 },
  footer: { display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '10px 14px', borderTop: '1px solid #222' },
  btn: { padding: '5px 14px', border: '1px solid #333', borderRadius: 4, color: '#ccc', background: 'transparent', cursor: 'pointer', fontSize: 14, fontFamily: 'inherit' },
  btnPrimary: { padding: '5px 14px', border: '1px solid #2a3a4a', borderRadius: 4, color: '#90caf9', background: '#1a2a3a', cursor: 'pointer', fontSize: 14, fontFamily: 'inherit' },
}

interface Props {
  title: string
  open: boolean
  onClose: () => void
  onConfirm?: () => void
  confirmLabel?: string
  cancelLabel?: string
  children: React.ReactNode
}

export default function Modal({ title, open, onClose, onConfirm, confirmLabel = '确认', cancelLabel = '取消', children }: Props) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])
  if (!open) return null
  return (
    <div style={S.backdrop} onClick={onClose}>
      <div style={S.panel} onClick={(e) => e.stopPropagation()}>
        <div style={S.header}>{title}</div>
        <div style={S.body}>{children}</div>
        <div style={S.footer}>
          <button style={S.btn} onClick={onClose}>{cancelLabel}</button>
          {onConfirm && <button style={S.btnPrimary} onClick={onConfirm}>{confirmLabel}</button>}
        </div>
      </div>
    </div>
  )
}
