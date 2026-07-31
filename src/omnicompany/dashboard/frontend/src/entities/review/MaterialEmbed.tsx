import { Component, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ExternalLink, GitBranch, Sparkles } from 'lucide-react'

import {
  reviewstageApi,
  type Material,
  type MaterialContextField,
} from '../../api/reviewstageClient'
import { usePanels } from '../../stores/panelsStore'
import { MaterialContextSpineView } from './MaterialContextSpine'
import {
  CANONICAL_REVIEW_KIND,
  canonicalMaterialRef,
  parseCanonicalMaterialRef,
} from './materialReference'
import { resolveMaterialEmbedRenderer } from './rendererRegistry'
import './materialEmbedViews'
import './reviewFlow.css'

class MaterialEmbedPreviewBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return (
        <span className="rf-material-embed-preview-fallback">
          类型化嵌入暂不可用，已保留通用材料卡与完整审阅入口。
        </span>
      )
    }
    return this.props.children
  }
}

function contextField(
  material: Material,
  sectionId: string,
  key: string,
): MaterialContextField | undefined {
  return material.context_spine?.sections
    .find((section) => section.id === sectionId)
    ?.fields.find((field) => field.key === key && field.status !== 'unrecorded')
}

function compactValue(value: unknown): string {
  if (value == null) return ''
  if (Array.isArray(value)) return value.map(compactValue).filter(Boolean).join(' · ')
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item != null && item !== '')
      .map(([key, item]) => `${key}: ${compactValue(item)}`)
      .join(' · ')
  }
  return String(value)
}

function materialTabTitle(title: string): string {
  const value = title.trim() || '审阅材料'
  return value.length > 24 ? `${value.slice(0, 23)}…` : value
}

export function MaterialEmbed({
  reference,
  label,
  onOpen,
}: {
  reference: string
  label?: string
  onOpen?: (materialId: string, title: string) => void
}) {
  const materialId = useMemo(() => parseCanonicalMaterialRef(reference), [reference])
  const [material, setMaterial] = useState<Material | null>(null)
  const [error, setError] = useState('')
  const [detailsOpen, setDetailsOpen] = useState(false)
  const openTab = usePanels((state) => state.openTab)

  useEffect(() => {
    if (!materialId) {
      setMaterial(null)
      setError('无效的审阅材料引用')
      return undefined
    }
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

  if (!materialId) {
    return (
      <span className="rf-material-embed invalid" data-testid="material-embed-invalid">
        {error || '无效的审阅材料引用'}：<code>{reference}</code>
      </span>
    )
  }

  const canonicalRef = canonicalMaterialRef(materialId)
  if (!material) {
    return (
      <span
        className={`rf-material-embed ${error ? 'invalid' : 'loading'}`}
        data-testid={error ? 'material-embed-error' : 'material-embed-loading'}
        data-omni-uri={canonicalRef}
        data-omni-kind={CANONICAL_REVIEW_KIND}
      >
        {error || `正在载入 ${label || canonicalRef}`}
      </span>
    )
  }

  const profile = material.review_context?.profile_id || 'generic'
  const candidateCount = material.review_context?.references
    .filter((item) => item.relation === 'comparison_member').length || 0
  const project = compactValue(contextField(material, 'scope', 'project')?.value || material.project)
  const plan = compactValue(contextField(material, 'scope', 'plan')?.value)
  const producer = compactValue(
    contextField(material, 'producer', 'declared_producer')?.value,
  )
  const missing = material.context_spine?.completeness.missing.length || 0
  const embedRenderer = resolveMaterialEmbedRenderer(material)
  const EmbeddedView = embedRenderer?.Component

  return (
    <span
      className="rf-material-embed"
      data-testid="material-embed"
      data-material-id={material.id}
      data-omni-uri={canonicalRef}
      data-omni-kind={CANONICAL_REVIEW_KIND}
      data-embed-renderer={embedRenderer?.rendererId || 'generic-card'}
    >
      <span className="rf-material-embed-head">
        <span className="rf-material-embed-icon" aria-hidden>
          {profile.startsWith('aigc-') ? <Sparkles size={15} /> : <GitBranch size={15} />}
        </span>
        <span className="rf-material-embed-title">
          <strong>{label || material.title}</strong>
          <small>
             {profile}
             {candidateCount > 0 ? ` · ${candidateCount} 个候选` : ''}
             {material.version ? ` · v${material.version}` : ''}
             {embedRenderer ? ` · ${embedRenderer.rendererId}` : ' · 通用卡'}
           </small>
         </span>
        <button
          type="button"
          className="rf-material-embed-open"
          onClick={() => {
            if (onOpen) {
              onOpen(material.id, material.title)
              return
            }
            openTab(
              { type: 'review_material', id: material.id },
              materialTabTitle(material.title),
            )
          }}
        >
          <ExternalLink size={13} />
          打开审阅
        </button>
      </span>
      <span className="rf-material-embed-background">
        {(project || plan) && (
          <span><b>归属</b>{[project, plan].filter(Boolean).join(' · ')}</span>
        )}
        {producer && <span><b>生产背景</b>{producer}</span>}
        <span>
          <b>上下文</b>
          {material.context_spine
            ? `${material.context_spine.completeness.recorded}/${material.context_spine.completeness.expected} 已记录${missing ? `，缺 ${missing}` : ''}`
            : '历史材料，尚无统一 Context Spine'}
         </span>
       </span>
      {EmbeddedView && (
        <span className="rf-material-embed-preview">
          <MaterialEmbedPreviewBoundary key={`${material.id}:${embedRenderer.rendererId}`}>
            <EmbeddedView m={material} />
          </MaterialEmbedPreviewBoundary>
        </span>
      )}
      <details
        className="rf-material-embed-details"
        onToggle={(event) => setDetailsOpen(event.currentTarget.open)}
      >
        <summary>查看来源、会话与谱系</summary>
        {detailsOpen && (
          <MaterialContextSpineView
            materialId={material.id}
            initial={material.context_spine}
            fallbackReviewContext={material.review_context}
            compact
          />
        )}
      </details>
    </span>
  )
}
