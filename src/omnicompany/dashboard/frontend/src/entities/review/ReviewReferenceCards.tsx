import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, Sparkles } from 'lucide-react'

import {
  reviewstageApi,
  type Material,
  type ReviewReference,
} from '../../api/reviewstageClient'
import {
  materialReviewSurfaceUrl,
  parseCanonicalMaterialRef,
} from './materialReference'

function text(value: unknown): string {
  return value == null ? '' : String(value).trim()
}

function CandidateReferenceCard({
  materialId,
  relation,
  label,
}: {
  materialId: string
  relation: string
  label: string
}) {
  const [material, setMaterial] = useState<Material | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setMaterial(null)
    setError('')
    reviewstageApi.get(materialId)
      .then((value) => { if (alive) setMaterial(value) })
      .catch((reason) => {
        if (alive) setError(String(reason instanceof Error ? reason.message : reason))
      })
    return () => { alive = false }
  }, [materialId])

  if (!material) {
    return (
      <div className="rf-reference-card loading" data-testid="review-reference-card">
        <strong>{label || materialId}</strong>
        <small>{error || '载入候选背景…'}</small>
      </div>
    )
  }

  const extra = material.extra || {}
  const model = text(extra.generation_model || extra.model)
  const run = text(extra.generation_run_id || extra.run_id)
  const style = text(extra.candidate_style || extra.style)
  const candidateId = text(extra.aigc_lab_candidate_id || extra.candidate_id)
  const candidateIndex = text(extra.candidate_index)
  const labUrl = text(extra.aigc_lab_url)
  const completeness = material.context_spine?.completeness

  return (
    <article
      className="rf-reference-card"
      data-testid="review-reference-card"
      data-material-id={material.id}
    >
      <header>
        <Sparkles size={14} aria-hidden />
        <span>
          <strong>{material.title || label || material.id}</strong>
          <small>{material.review_context?.profile_id || relation}</small>
        </span>
      </header>
      <dl>
        {model && <><dt>模型</dt><dd>{model}</dd></>}
        {run && <><dt>run</dt><dd>{run}</dd></>}
        {style && <><dt>style</dt><dd>{style}</dd></>}
        {(candidateId || candidateIndex) && (
          <><dt>候选</dt><dd>{[candidateId && `#${candidateId}`, candidateIndex && `index ${candidateIndex}`].filter(Boolean).join(' · ')}</dd></>
        )}
        {completeness && (
          <><dt>上下文</dt><dd>{completeness.recorded}/{completeness.expected} 已记录</dd></>
        )}
      </dl>
      <footer>
        <a href={materialReviewSurfaceUrl(material.id)} target="_blank" rel="noreferrer">
          审阅候选 <ExternalLink size={11} aria-hidden />
        </a>
        {labUrl && (
          <a href={labUrl} target="_blank" rel="noreferrer">
            打开 AIGC Lab <ExternalLink size={11} aria-hidden />
          </a>
        )}
      </footer>
    </article>
  )
}

export function ReviewReferenceCards({
  references,
}: {
  references: ReviewReference[]
}) {
  const candidates = useMemo(
    () => references
      .filter((reference) => (
        reference.relation === 'comparison_member'
        || reference.relation === 'candidate'
      ))
      .map((reference) => ({
        reference,
        materialId: parseCanonicalMaterialRef(reference.target),
      }))
      .filter((item): item is {
        reference: ReviewReference
        materialId: string
      } => Boolean(item.materialId)),
    [references],
  )

  if (candidates.length === 0) return null
  return (
    <div className="rf-reference-cards" data-testid="review-reference-cards">
      {candidates.map(({ reference, materialId }) => (
        <CandidateReferenceCard
          key={`${reference.relation}-${materialId}`}
          materialId={materialId}
          relation={reference.relation}
          label={reference.label}
        />
      ))}
    </div>
  )
}
