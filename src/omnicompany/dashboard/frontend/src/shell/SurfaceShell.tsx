/**
 * shell/SurfaceShell — 单区渲染壳。
 *
 * ?surface=<queue|material|comments|project|plan|threads|authored> 时, 整页只渲染那一个区,
 * 供挂进 VSCode 原生表面: 主侧栏的 项目/计划/对话/审阅材料/札记 各 section, 编辑区材料页, 次级侧栏评论。
 * surface=full/缺省 走 App(完整驾驶舱), 不经这里。
 *
 * section 列表(项目/计划/对话/札记)复用驾驶舱里现成的面板, 只把 openTab 改成"在 omnidashboard 编辑区
 * 打开该条目"(发宿主消息) —— 侧栏只管导航, 条目去编辑区开。审阅材料/材料/评论沿用三区原有联动。
 */
import React, { useEffect, useRef } from 'react'
import { Undo2 } from 'lucide-react'
// @ts-ignore — jsx 文件没 .d.ts
import { ThemeProvider } from '../contexts/ThemeContext'
import Tooltip from '../shared/view/ui/Tooltip'
// 直连具体文件, 不走 entities/review 的 export * barrel(2026-07 首屏拆包: 见 CockpitShell 同注)。
import { ReviewQueueSidebar } from '../entities/review/ReviewQueueSidebar'
import { CommentsPanel } from '../entities/review/CommentsPanel'
import { MaterialEmbed } from '../entities/review/MaterialEmbed'
import {
  canonicalMaterialRef,
  materialReviewSurfaceUrl,
} from '../entities/review/materialReference'
import { ReviewMaterialPanel } from '../entities/review_material'
import ProjectsPanel from './ProjectsPanel'
import PlanSidebar from '../entities/plan-folder/PlanSidebar'
import ThreadMonitorPanel from '../entities/controller/ThreadMonitorPanel'
// 两个重组件懒加载(2026-07 首屏拆包): SurfaceShell 挂在主入口静态图上, 直引会把它们钉进主包。
const MultiagentView = React.lazy(() => import('../entities/multiagent/MultiagentView'))
const ReviewOverview = React.lazy(() => import('../entities/review/ReviewOverview'))
const SessionCompanion = React.lazy(() => import('../entities/cc_session/SessionCompanion'))
import { authoredRegistration } from '../entities/authored'
import { multiagentSessionUrl, useMultiagentLink } from '../entities/multiagent/multiagentLink'
import { usePanels } from '../stores/panelsStore'
import { useReviewActive } from '../stores/reviewActiveStore'
import {
  isInExtShell,
  postHostMessage,
  openInOmnidashboard,
  openMaterialNative,
  type Surface,
} from '../lib/surface'

class SurfaceErrorBoundary extends React.Component<
  { surface: Surface; children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div style={{ minHeight: '100vh', padding: 18, color: 'var(--fp-err)', background: 'var(--fp-bg)' }} data-testid="surface-error">
        <strong>{this.props.surface} 加载失败</strong>
        <div style={{ marginTop: 8, color: 'var(--fp-text-2)', fontFamily: 'var(--fp-font-mono)', whiteSpace: 'pre-wrap' }}>
          {this.state.error.message || String(this.state.error)}
        </div>
      </div>
    )
  }
}

const backBtn: React.CSSProperties = {
  border: '1px solid var(--fp-border)', background: 'var(--fp-card)', color: 'var(--fp-link)',
  borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 13,
}

function BackToOmnichat({ region }: { region: Surface }) {
  return (
    <Tooltip content="切回 omnichat 完整界面" position="bottom">
      <button type="button" style={backBtn} data-testid="surface-back-to-omnichat"
        aria-label="切回 omnichat 完整界面"
        onClick={() => postHostMessage({ type: 'restore-region-internal', region })}>
        <Undo2 size={13} aria-hidden style={{ verticalAlign: -2 }} /> 回 omnichat
      </button>
    </Tooltip>
  )
}

// section 列表里点条目 → 不在本侧栏开(没有 dockview), 改请宿主在 omnidashboard 编辑区开。
const surfaceOpenTab = (ref: any, title?: string, facet?: string): string => {
  if (ref && ref.type && ref.id != null) openInOmnidashboard(String(ref.type), String(ref.id), facet, title)
  return ref ? `${ref.type}:${ref.id}` : ''
}
/** 把全局 panelsStore 的 openTab/openTabBackground 改成"在 omnidashboard 编辑区开"。
 * 这样内部用 usePanels 的面板(项目/对话/札记)在 surface 下点条目即去编辑区, 无需改各面板。 */
function usePatchedPanelsForSurface() {
  const patched = useRef(false)
  if (!patched.current) {
    patched.current = true
    usePanels.setState({ openTab: surfaceOpenTab as any, openTabBackground: surfaceOpenTab as any })
  }
}

function ProjectSurface() {
  usePatchedPanelsForSurface()
  return <div style={{ height: '100vh', overflow: 'auto' }} data-testid="surface-project"><ProjectsPanel /></div>
}

function PlanSurface() {
  usePatchedPanelsForSurface()
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }} data-testid="surface-plan">
      <PlanSidebar filter={''} activeId={null} openTab={surfaceOpenTab as any} />
    </div>
  )
}

function ThreadsSurface() {
  usePatchedPanelsForSurface()
  return <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }} data-testid="surface-threads"><ThreadMonitorPanel /></div>
}

function AuthoredSurface() {
  usePatchedPanelsForSurface()
  const Editor = authoredRegistration.renderer.Editor
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }} data-testid="surface-authored">
      <Editor entity={{ type: 'authored', id: 'main', title: '札记', tags: [] } as any} />
    </div>
  )
}

function QueueSurface() {
  const setActiveMaterial = useReviewActive((s) => s.setActiveMaterial)
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'transparent' }} data-testid="surface-queue">
      <ReviewQueueSidebar
        headerActions={<BackToOmnichat region="queue" />}
        onOpenMaterial={(m) => {
          setActiveMaterial(m.id, 'local')
          openMaterialNative(m.id, m.title)
        }} />
    </div>
  )
}

function MaterialSurface({ id }: { id: string | null }) {
  const setActiveMaterial = useReviewActive((s) => s.setActiveMaterial)
  useEffect(() => { if (id) setActiveMaterial(id, 'local') }, [id, setActiveMaterial])
  if (!id) return <div style={{ padding: 18, color: 'var(--fp-text-3)' }}>缺少材料 id(?surface=material&id=…)。</div>
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }} data-testid="surface-material">
      <ReviewMaterialPanel id={id} embedded />
    </div>
  )
}

function MaterialEmbedSurface({ id }: { id: string | null }) {
  if (!id) {
    return <div style={{ padding: 18, color: 'var(--fp-text-3)' }}>缺少材料 id（?surface=material-embed&amp;id=…）。</div>
  }
  return (
    <div
      style={{ minHeight: '100vh', padding: 12, boxSizing: 'border-box', background: 'var(--fp-bg)' }}
      data-testid="surface-material-embed"
    >
      <MaterialEmbed
        reference={canonicalMaterialRef(id)}
        onOpen={(materialId, title) => {
          if (isInExtShell()) {
            openInOmnidashboard('review_material', materialId, undefined, title)
            return
          }
          window.location.assign(materialReviewSurfaceUrl(materialId))
        }}
      />
    </div>
  )
}

function CommentsSurface() {
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'transparent' }} data-testid="surface-comments">
      <CommentsPanel headerActions={<BackToOmnichat region="comments" />} />
    </div>
  )
}

function MultiagentSurface() {
  // 点会话行 → 请宿主在 omnidashboard 编辑区开该对话(深链),侧栏只管选。
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }} data-testid="surface-multiagent">
      <React.Suspense fallback={<div style={{ padding: 18, color: 'var(--fp-text-3)' }}>加载中…</div>}>
        <MultiagentView
          onOpen={(tab) => {
            if (isInExtShell()) {
              openInOmnidashboard('cc_session', tab.ref.id, undefined, tab.title)
              return
            }
            window.location.assign(
              multiagentSessionUrl(tab.ref.id, useMultiagentLink.getState().linkId),
            )
          }}
        />
      </React.Suspense>
    </div>
  )
}

function ReviewOverviewSurface() {
  const setActiveMaterial = useReviewActive((s) => s.setActiveMaterial)
  // 公众号式总览:点卡 → 设为当前材料 + 请宿主在编辑区开材料正文页。
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }} data-testid="surface-review-overview">
      <React.Suspense fallback={<div style={{ padding: 18, color: 'var(--fp-text-3)' }}>加载中…</div>}>
        <ReviewOverview
          pollMs={4000}
          onOpenReview={() => openInOmnidashboard('review_queue', 'main', undefined, '审阅')}
          onOpen={(m) => {
            setActiveMaterial(m.id, 'local')
            openMaterialNative(m.id, m.title)
          }}
        />
      </React.Suspense>
    </div>
  )
}

function SessionCompanionSurface({ id }: { id: string | null }) {
  if (!id) {
    return <div style={{ padding: 18, color: 'var(--fp-text-3)' }}>缺少 CLI 会话 id（?surface=session-companion&amp;id=…）。</div>
  }
  return (
    <div style={{ height: '100vh', minWidth: 0, display: 'flex', flexDirection: 'column' }} data-testid="surface-session-companion">
      <React.Suspense fallback={<div style={{ padding: 18, color: 'var(--fp-text-3)' }}>加载伴随视图中…</div>}>
        <SessionCompanion sessionId={id} mode="surface" />
      </React.Suspense>
    </div>
  )
}

export default function SurfaceShell({ surface, id }: { surface: Surface; id: string | null }) {
  let body: React.ReactNode
  if (surface === 'queue') body = <QueueSurface />
  else if (surface === 'material') body = <MaterialSurface id={id} />
  else if (surface === 'material-embed') body = <MaterialEmbedSurface id={id} />
  else if (surface === 'comments') body = <CommentsSurface />
  else if (surface === 'project') body = <ProjectSurface />
  else if (surface === 'plan') body = <PlanSurface />
  else if (surface === 'threads') body = <ThreadsSurface />
  else if (surface === 'multiagent') body = <MultiagentSurface />
  else if (surface === 'review-overview') body = <ReviewOverviewSurface />
  else if (surface === 'session-companion') body = <SessionCompanionSurface id={id} />
  else if (surface === 'authored') body = <AuthoredSurface />
  else body = null
  return (
    <ThemeProvider>
      <SurfaceErrorBoundary surface={surface}>{body}</SurfaceErrorBoundary>
    </ThemeProvider>
  )
}
