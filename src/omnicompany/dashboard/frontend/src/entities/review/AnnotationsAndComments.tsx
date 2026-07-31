/**
 * entities/review/AnnotationsAndComments — 右侧批注 + 评论栏 (含 @mention 自动完成).
 *
 * R2 从 standalone 审阅台剪切而来 (结构搬移, 行为零变化); R4 起 standalone 已退役,
 * 消费方为驾驶舱 review_queue / review_material 面板.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Material, CommentFeedbackStatus } from '../../api/reviewstageClient'
import { entitiesApi, type EntitySearchResult } from '../../api/entitiesClient'
import {
  COLORS,
  FEEDBACK_LABELS,
  type EntityMention,
  getTargetMentions,
  mentionFromResult,
  findMentionQuery,
  formatTs,
} from './shared'


// ── 右侧批注 + 评论栏 ──────────────────────────────────────────────

export function AnnotationsAndComments({
  material, onCommentSubmit, onFeedbackChange, addingTarget, clearAddingTarget, compact = false,
}: {
  material: Material
  onCommentSubmit: (content: string, target?: Record<string, unknown>) => Promise<void>
  onFeedbackChange: (commentId: string, status: CommentFeedbackStatus) => Promise<void>
  addingTarget: Record<string, unknown> | null
  clearAddingTarget: () => void
  compact?: boolean
}) {
  const [draft, setDraft] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [mentionQuery, setMentionQuery] = useState<{ start: number; query: string } | null>(null)
  const [mentionOptions, setMentionOptions] = useState<EntitySearchResult[]>([])
  const [selectedMentions, setSelectedMentions] = useState<EntityMention[]>([])
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const refreshMentionQuery = useCallback((value: string, caret: number) => {
    const next = findMentionQuery(value, caret)
    setMentionQuery(next)
    if (!next) setMentionOptions([])
  }, [])

  useEffect(() => {
    if (!mentionQuery) return
    let cancelled = false
    const handle = window.setTimeout(() => {
      entitiesApi.suggest(mentionQuery.query, 10)
        .then(items => { if (!cancelled) setMentionOptions(items) })
        .catch(() => { if (!cancelled) setMentionOptions([]) })
    }, 120)
    return () => { cancelled = true; window.clearTimeout(handle) }
  }, [mentionQuery])

  const insertMention = useCallback((item: EntitySearchResult) => {
    if (!mentionQuery) return
    const before = draft.slice(0, mentionQuery.start)
    const after = draft.slice(textareaRef.current?.selectionStart ?? draft.length)
    const token = item.display
    const next = `${before}${token} ${after}`
    setDraft(next)
    setSelectedMentions(prev => {
      const mention = mentionFromResult(item)
      if (prev.some(m => m.uri === mention.uri)) return prev
      return [...prev, mention]
    })
    setMentionQuery(null)
    setMentionOptions([])
    window.setTimeout(() => {
      const pos = before.length + token.length + 1
      textareaRef.current?.focus()
      textareaRef.current?.setSelectionRange(pos, pos)
    }, 0)
  }, [draft, mentionQuery])

  const submit = useCallback(async () => {
    const text = draft.trim()
    if (!text) return
    const liveMentions = selectedMentions.filter(m => text.includes(m.display))
    const target: Record<string, unknown> = addingTarget ? { ...addingTarget } : {}
    if (liveMentions.length > 0) target.mentions = liveMentions
    setSubmitting(true)
    try {
      await onCommentSubmit(text, Object.keys(target).length > 0 ? target : undefined)
      setDraft('')
      setSelectedMentions([])
      setMentionQuery(null)
      setMentionOptions([])
      clearAddingTarget()
    } finally {
      setSubmitting(false)
    }
  }, [draft, selectedMentions, addingTarget, onCommentSubmit, clearAddingTarget])

  return (
    <div style={{
      width: compact ? '100%' : 320,
      maxHeight: compact ? 360 : undefined,
      borderLeft: compact ? 'none' : `1px solid ${COLORS.border}`,
      borderTop: compact ? `1px solid ${COLORS.border}` : 'none',
      display: 'flex', flexDirection: 'column', background: COLORS.panel, color: COLORS.text,
      flexShrink: 0,
    }}>
      <div style={{ padding: '8px 12px', borderBottom: `1px solid ${COLORS.border}`, fontWeight: 600 }}>
        批注 + 评论 <span style={{ color: COLORS.textDim, fontWeight: 400, fontSize: 14 }}>(§4.4 / §4.5)</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {material.annotations.length === 0 && material.comments.length === 0 && (
          <div style={{ color: COLORS.textDim, fontSize: 14 }}>暂无批注 / 评论</div>
        )}
        {material.annotations.map(a => (
          <div key={a.id} style={{
            padding: 8, borderRadius: 4, background: '#1a2730',
            borderLeft: `3px solid ${COLORS.borderActive}`,
          }} data-testid="annotation-item">
            <div style={{ fontSize: 14, color: COLORS.textDim, marginBottom: 4 }}>
              {a.kind === 'ai' ? 'AI 批注' : '用户批注'} · {a.author} · {formatTs(a.created_at)}
              {a.target && Object.keys(a.target).length > 0 && (
                <span style={{ marginLeft: 6 }}>· 定位: {JSON.stringify(a.target).slice(0, 40)}</span>
              )}
            </div>
            <div style={{ fontSize: 15 }}>{a.content}</div>
          </div>
        ))}
        {material.comments.map(c => (
          <div key={c.id} style={{
            padding: 8, borderRadius: 4, background: '#1a1f30',
            borderLeft: `3px solid ${COLORS.accepted}`,
          }} data-testid="comment-item">
            <div style={{ fontSize: 14, color: COLORS.textDim, marginBottom: 4 }}>
              评论 · {c.author} · {formatTs(c.created_at)}
              {c.target && Object.keys(c.target).length > 0 && (
                <span style={{ marginLeft: 6 }}>· 定位</span>
              )}
            </div>
            <div style={{ fontSize: 15 }}>{c.content}</div>
            {getTargetMentions(c.target).length > 0 && (
              <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }} data-testid="comment-mentions">
                {getTargetMentions(c.target).map(m => (
                  <span key={m.uri} title={m.uri} style={{
                    fontSize: 14, color: COLORS.borderActive, border: `1px solid ${COLORS.border}`,
                    borderRadius: 3, padding: '1px 5px', background: '#0d2440',
                  }}>{m.display}</span>
                ))}
              </div>
            )}
            <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }} data-testid="comment-feedback">
              <span style={{ fontSize: 14, color: COLORS.textDim, marginRight: 2 }}>
                {FEEDBACK_LABELS[c.feedback_status || 'delivered']}
              </span>
              {(['read', 'to_todo', 'todo_done'] as CommentFeedbackStatus[]).map(status => (
                <button
                  key={status}
                  type="button"
                  onClick={() => onFeedbackChange(c.id, status)}
                  disabled={c.feedback_status === status}
                  style={{
                    minHeight: 28,
                    minWidth: 64,
                    padding: '5px 10px',
                    borderRadius: 4,
                    border: `1px solid ${COLORS.border}`,
                    background: c.feedback_status === status ? COLORS.border : 'var(--fp-bg)',
                    color: COLORS.text,
                    cursor: c.feedback_status === status ? 'default' : 'pointer',
                    fontSize: 14,
                  }}
                >
                  {FEEDBACK_LABELS[status]}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div style={{ padding: 12, borderTop: `1px solid ${COLORS.border}` }}>
        {addingTarget && (
          <div style={{
            fontSize: 14, color: COLORS.borderActive, marginBottom: 6,
            padding: 6, background: '#0d2440', borderRadius: 4,
          }}>
            带定位: {JSON.stringify(addingTarget).slice(0, 60)}
            <button
              onClick={clearAddingTarget}
              style={{ marginLeft: 8, padding: '0 6px', background: 'transparent', color: COLORS.textDim, border: `1px solid ${COLORS.border}`, borderRadius: 2, cursor: 'pointer' }}
            >清除</button>
          </div>
        )}
        <textarea
          ref={textareaRef}
          data-testid="comment-input"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            refreshMentionQuery(e.target.value, e.target.selectionStart)
          }}
          onKeyUp={(e) => refreshMentionQuery(e.currentTarget.value, e.currentTarget.selectionStart)}
          onClick={(e) => refreshMentionQuery(e.currentTarget.value, e.currentTarget.selectionStart)}
          placeholder="加一条评论… (§7.3)"
          style={{
            width: '100%', minHeight: 60, padding: 8,
            background: 'var(--fp-bg)', color: COLORS.text,
            border: `1px solid ${COLORS.border}`, borderRadius: 4,
            fontSize: 15, fontFamily: 'inherit', resize: 'vertical',
          }}
        />
        {mentionOptions.length > 0 && (
          <div style={{
            maxHeight: 180, overflowY: 'auto', marginTop: 6,
            border: `1px solid ${COLORS.border}`, borderRadius: 4, background: 'var(--fp-bg)',
          }} data-testid="entity-mention-menu">
            {mentionOptions.map(item => (
              <button
                key={item.uri}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); insertMention(item) }}
                style={{
                  width: '100%', textAlign: 'left', display: 'block', padding: '6px 8px',
                  background: 'transparent', color: COLORS.text, border: 'none',
                  borderBottom: `1px solid ${COLORS.border}`, cursor: 'pointer', fontSize: 14,
                }}
                title={item.uri}
              >
                <span style={{ color: COLORS.borderActive }}>{item.display}</span>
                <span style={{ color: COLORS.textDim, marginLeft: 8 }}>{item.title}</span>
              </button>
            ))}
          </div>
        )}
        <button
          data-testid="comment-submit"
          onClick={submit}
          disabled={!draft.trim() || submitting}
          style={{
            marginTop: 6, width: '100%', padding: '6px 12px',
            background: !draft.trim() ? COLORS.border : COLORS.borderActive,
            color: '#fff', border: 'none', borderRadius: 4,
            cursor: !draft.trim() ? 'not-allowed' : 'pointer', fontSize: 15,
          }}
        >
          {submitting ? '发送中…' : '发送评论'}
        </button>
      </div>
    </div>
  )
}
