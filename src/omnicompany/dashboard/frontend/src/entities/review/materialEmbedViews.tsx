import { ExternalLink, FileText, Video } from 'lucide-react'

import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { ReviewReferenceCards } from './ReviewReferenceCards'
import {
  registerKindEmbedRenderer,
  registerProfileEmbedRenderer,
} from './rendererRegistry'

function value(value: unknown): string {
  return value == null ? '' : String(value).trim()
}

function imageSource(material: Material): string {
  if (material.file_relpath) return reviewstageApi.fileUrl(material.id)
  const raw = value(material.inline_content)
  if (!raw) return ''
  return raw.startsWith('data:') ? raw : `data:image/png;base64,${raw}`
}

function ImageMaterialEmbedView({ m }: { m: Material }) {
  const src = imageSource(m)
  if (!src) return null
  return (
    <span className="rf-material-embed-media" data-testid="material-embed-image">
      <img src={src} alt={m.title} loading="lazy" />
    </span>
  )
}

function AigcCandidateMaterialEmbedView({ m }: { m: Material }) {
  const extra = m.extra || {}
  const src = imageSource(m)
  const labUrl = value(extra.aigc_lab_url)
  const model = value(extra.generation_model || extra.model)
  const run = value(extra.generation_run_id || extra.run_id)
  const style = value(extra.candidate_style || extra.style)
  const candidate = value(extra.aigc_lab_candidate_id || extra.candidate_id)
  return (
    <span className="rf-material-embed-aigc" data-testid="material-embed-aigc-candidate">
      {src && (
        <span className="rf-material-embed-media">
          <img src={src} alt={m.title} loading="lazy" />
        </span>
      )}
      <span className="rf-material-embed-aigc-meta">
        {[model && `模型 ${model}`, run && `run ${run}`, style && `style ${style}`, candidate && `候选 ${candidate}`]
          .filter(Boolean)
          .map((item) => <span key={item}>{item}</span>)}
        {labUrl && (
          <a href={labUrl} target="_blank" rel="noreferrer">
            打开 AIGC Lab <ExternalLink size={11} aria-hidden />
          </a>
        )}
      </span>
    </span>
  )
}

function AigcComparisonMaterialEmbedView({ m }: { m: Material }) {
  return (
    <span className="rf-material-embed-comparison" data-testid="material-embed-aigc-comparison">
      <ReviewReferenceCards references={m.review_context?.references || []} />
    </span>
  )
}

function DocumentMaterialEmbedView({ m }: { m: Material }) {
  const raw = value(m.inline_content)
  const excerpt = raw.length > 560 ? `${raw.slice(0, 560).trimEnd()}…` : raw
  return (
    <span className="rf-material-embed-document" data-testid="material-embed-document">
      <span className="rf-material-embed-preview-label">
        <FileText size={13} aria-hidden />
        {m.kind}
      </span>
      <span>{excerpt || '正文保存在材料文件中；打开完整审阅可查看全文并进行选区评论。'}</span>
    </span>
  )
}

function KeyQuestionMaterialEmbedView({ m }: { m: Material }) {
  let parsed: Record<string, unknown> | null = null
  try {
    const value = m.inline_content ? JSON.parse(m.inline_content) : null
    parsed = value && typeof value === 'object' && !Array.isArray(value) ? value : null
  } catch {
    parsed = null
  }
  if (!parsed) return <DocumentMaterialEmbedView m={m} />
  const options = Array.isArray(parsed.options) ? parsed.options : []
  return (
    <span className="rf-material-embed-question" data-testid="material-embed-key-question">
      <strong>{value(parsed.question) || m.title}</strong>
      {options.length > 0 && (
        <span className="rf-material-embed-option-list">
          {options.slice(0, 6).map((option, index) => (
            <span key={`${index}:${value(option)}`}>{String.fromCharCode(65 + index)}. {value(option)}</span>
          ))}
        </span>
      )}
    </span>
  )
}

function VideoMaterialEmbedView({ m }: { m: Material }) {
  if (!m.file_relpath) {
    return (
      <span className="rf-material-embed-preview-label" data-testid="material-embed-video-fallback">
        <Video size={13} aria-hidden />
        视频文件未落盘；打开完整审阅查看来源与失败降级。
      </span>
    )
  }
  return (
    <span className="rf-material-embed-video" data-testid="material-embed-video">
      <video src={reviewstageApi.fileUrl(m.id)} controls preload="metadata" />
    </span>
  )
}

registerKindEmbedRenderer('image', {
  Component: ImageMaterialEmbedView,
  rendererId: 'kind:image',
})
registerKindEmbedRenderer('aigc-image', {
  Component: AigcCandidateMaterialEmbedView,
  rendererId: 'profile:aigc-candidate',
})
registerKindEmbedRenderer('markdown', {
  Component: DocumentMaterialEmbedView,
  rendererId: 'kind:markdown',
})
registerKindEmbedRenderer('plan', {
  Component: DocumentMaterialEmbedView,
  rendererId: 'kind:markdown',
})
registerKindEmbedRenderer('agent-workflow-report', {
  Component: DocumentMaterialEmbedView,
  rendererId: 'kind:markdown',
})
registerKindEmbedRenderer('decision-candidate', {
  Component: DocumentMaterialEmbedView,
  rendererId: 'kind:markdown',
})
registerKindEmbedRenderer('key_question', {
  Component: KeyQuestionMaterialEmbedView,
  rendererId: 'kind:key_question',
})
registerKindEmbedRenderer('video', {
  Component: VideoMaterialEmbedView,
  rendererId: 'kind:video',
})
registerProfileEmbedRenderer('aigc-candidate', {
  Component: AigcCandidateMaterialEmbedView,
  rendererId: 'profile:aigc-candidate',
})
registerProfileEmbedRenderer('aigc-comparison', {
  Component: AigcComparisonMaterialEmbedView,
  rendererId: 'profile:aigc-comparison',
})
