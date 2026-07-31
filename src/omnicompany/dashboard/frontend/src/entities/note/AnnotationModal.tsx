import React, { useState } from 'react'
import { type Annotation, createAnnotation, deleteAnnotation } from './annotations'
import Modal from '../../shell/Modal'

interface Props {
  noteId: string
  open: boolean
  onClose: () => void
  /** Either: existing anchor (open thread) or new anchor (add first comment). */
  anchor: { hash: string; snippet: string } | null
  existing: Annotation[]
  onChange: (refresh: boolean) => void
}

const S: Record<string, any> = {
  snippet: { padding: 8, background: '#111', borderRadius: 4, color: '#999', fontSize: 14, fontStyle: 'italic' as const, marginBottom: 12 },
  label: { color: '#90caf9', fontSize: 14, marginBottom: 6, marginTop: 12, textTransform: 'uppercase' as const },
  ann: { padding: 10, background: '#111', border: '1px solid #1a1a1a', borderRadius: 4, marginBottom: 8, fontSize: 14 },
  meta: { color: '#666', fontSize: 14, marginTop: 4, display: 'flex', gap: 8, alignItems: 'center' },
  delBtn: { background: 'transparent', border: '1px solid #4a2222', color: '#ef5350', cursor: 'pointer', padding: '1px 8px', fontSize: 14, borderRadius: 3, fontFamily: 'inherit' },
  textarea: { width: '100%', minHeight: 80, background: '#111', border: '1px solid #333', borderRadius: 4, color: '#e0e0e0', padding: 8, fontSize: 14, fontFamily: 'Consolas, Menlo, monospace', resize: 'vertical' as const, boxSizing: 'border-box' as const },
  hint: { color: '#666', fontSize: 14, marginTop: 6 },
  err: { color: '#ef5350', fontSize: 14, marginTop: 6 },
}

function fmtTs(ts: number): string {
  try { return new Date(ts * 1000).toLocaleString('zh', { hour12: false }) }
  catch { return '' }
}

export default function AnnotationModal({ noteId, open, onClose, anchor, existing, onChange }: Props) {
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (!anchor || !text.trim() || submitting) return
    setSubmitting(true); setErr(null)
    try {
      await createAnnotation(noteId, anchor, text.trim())
      setText('')
      onChange(true)
    } catch (e) {
      setErr(String(e))
    } finally { setSubmitting(false) }
  }

  const remove = async (id: string) => {
    if (!window.confirm('删除这条批注?')) return
    try {
      await deleteAnnotation(noteId, id)
      onChange(true)
    } catch (e) { alert(String(e)) }
  }

  return (
    <Modal
      title={existing.length > 0 ? `批注 · ${existing.length} 条` : '添加批注'}
      open={open}
      onClose={() => { setText(''); setErr(null); onClose() }}
      onConfirm={submitting ? undefined : submit}
      confirmLabel={submitting ? '保存中...' : '添加'}
    >
      {anchor && (
        <div style={S.snippet}>段落锚点: "{anchor.snippet}{anchor.snippet.length >= 60 ? '...' : ''}"</div>
      )}
      {existing.length > 0 && (
        <>
          <div style={S.label}>已有 {existing.length} 条</div>
          {existing.map((a) => (
            <div key={a.id} style={S.ann}>
              <div style={{ color: '#ccc' }}>{a.comment}</div>
              <div style={S.meta}>
                <span>{a.author}</span>
                <span>·</span>
                <span>{fmtTs(a.created_at)}</span>
                <span style={{ flex: 1 }} />
                <button style={S.delBtn} onClick={() => remove(a.id)}>删除</button>
              </div>
            </div>
          ))}
        </>
      )}
      <div style={S.label}>{existing.length > 0 ? '添加新批注' : '内容'}</div>
      <textarea
        style={S.textarea}
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit() }}
        placeholder="评论 / 待办 / 提醒 ..."
      />
      <div style={S.hint}>Ctrl/Cmd + Enter 提交 · Esc 关</div>
      {err && <div style={S.err}>{err}</div>}
    </Modal>
  )
}
