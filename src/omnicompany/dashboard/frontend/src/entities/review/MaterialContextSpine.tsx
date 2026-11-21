import { useEffect, useState, type ReactNode } from 'react'

import {
  reviewstageApi,
  type MaterialContextField,
  type MaterialContextSpine,
  type ReviewContext,
  type ReviewReference,
} from '../../api/reviewstageClient'
import { ReviewReferenceCards } from './ReviewReferenceCards'
import './reviewFlow.css'


function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function scalar(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

function ObjectValue({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value).filter(([, item]) => item != null && item !== '')
  return (
    <dl className="rf-context-object">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{renderValue(item)}</dd>
        </div>
      ))}
    </dl>
  )
}

function renderValue(value: unknown): ReactNode {
  if (Array.isArray(value)) {
    if (value.length === 0) return null
    return (
      <ul className="rf-context-list">
        {value.map((item, index) => (
          <li key={`${index}-${isRecord(item) ? scalar(item.id || item.key || item.target || item.trace_id) : scalar(item)}`}>
            {isRecord(item) ? <ObjectValue value={item} /> : scalar(item)}
          </li>
        ))}
      </ul>
    )
  }
  if (isRecord(value)) return <ObjectValue value={value} />
  return <span className="rf-context-scalar">{scalar(value)}</span>
}

function ContextField({ field }: { field: MaterialContextField }) {
  const reviewReferences = (
    field.key === 'references'
    && Array.isArray(field.value)
  )
    ? field.value.filter((item): item is ReviewReference => (
      isRecord(item)
      && typeof item.target === 'string'
      && typeof item.relation === 'string'
    ))
    : []
  return (
    <div className="rf-context-field" data-status={field.status}>
      <div className="rf-context-field-head">
        <span>{field.label}</span>
        <em title={`${field.authority} · ${field.source}`}>
          {field.status === 'derived' ? '派生' : field.authoritative ? '真源' : '声明'}
        </em>
      </div>
      <div className="rf-context-value">{renderValue(field.value)}</div>
      {reviewReferences.length > 0 && (
        <ReviewReferenceCards references={reviewReferences} />
      )}
    </div>
  )
}

function LegacyReviewContext({ context }: { context: ReviewContext }) {
  return (
    <div className="rf-context-legacy" data-testid="material-context-legacy">
      <div className="p-head">
        <strong>{context.profile_id}</strong>
        <span>{context.resolution.routing_level} · {Math.round(context.resolution.confidence * 100)}%</span>
      </div>
      <div className="p-trace">
        {context.resolution.selected_by} · {context.resolution.reason}
      </div>
      {context.schema_id && (
        <div className="p-schema">schema <code>{context.schema_id}</code></div>
      )}
      {context.references.length > 0 && (
        <div className="p-section">
          <b>关联背景</b>
          {context.references.map((ref, index) => (
            <div className="p-ref" key={`${ref.relation}-${ref.target}-${index}`}>
              <span>{ref.relation}</span>
              <code title={ref.target}>{ref.label || ref.target}</code>
            </div>
          ))}
        </div>
      )}
      {context.reminders.length > 0 && (
        <div className="p-section">
          <b>旧合同提交时记录</b>
          {context.reminders.map((reminder) => (
            <div className="p-reminder" data-severity={reminder.severity} key={reminder.code}>
              [{reminder.severity}] {reminder.message}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function MaterialContextSpineView({
  materialId,
  initial,
  fallbackReviewContext,
  compact = false,
}: {
  materialId: string
  initial?: MaterialContextSpine
  fallbackReviewContext?: ReviewContext | null
  compact?: boolean
}) {
  const [spine, setSpine] = useState<MaterialContextSpine | null>(
    initial?.material_id === materialId ? initial : null,
  )
  const [loading, setLoading] = useState(!spine)
  const [error, setError] = useState('')

  useEffect(() => {
    if (initial?.material_id === materialId) {
      setSpine(initial)
      setLoading(false)
      setError('')
      return undefined
    }
    let alive = true
    setSpine(null)
    setLoading(true)
    setError('')
    reviewstageApi.context(materialId)
      .then((payload) => { if (alive) setSpine(payload) })
      .catch((reason) => {
        if (alive) setError(String(reason instanceof Error ? reason.message : reason))
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [initial, materialId])

  if (!spine) {
    if (fallbackReviewContext) {
      return <LegacyReviewContext context={fallbackReviewContext} />
    }
    if (loading) return <div className="rf-context-state">加载材料上下文…</div>
    return (
      <div className="rf-context-state" data-testid="material-context-error">
        上下文暂不可用{error ? `：${error}` : ''}
      </div>
    )
  }

  const missing = spine.completeness.missing
  return (
    <div
      className={`rf-context-spine${compact ? ' compact' : ''}`}
      data-testid="material-context-spine"
      data-material-ref={spine.canonical_ref}
    >
      <div className="rf-context-canonical">
        <code>{spine.canonical_ref}</code>
        <span>{spine.completeness.recorded}/{spine.completeness.expected} 已记录</span>
      </div>
      {spine.sections.map((section) => {
        const fields = section.fields.filter((field) => field.status !== 'unrecorded')
        if (fields.length === 0) return null
        return (
          <section key={section.id} className="rf-context-section" data-section={section.id}>
            <h4>{section.label}</h4>
            {fields.map((field) => <ContextField key={field.key} field={field} />)}
          </section>
        )
      })}
      {missing.length > 0 && (
        <details className="rf-context-missing">
          <summary>尚未记录 {missing.length} 项</summary>
          <div>{missing.map((item) => <code key={item}>{item}</code>)}</div>
        </details>
      )}
    </div>
  )
}
