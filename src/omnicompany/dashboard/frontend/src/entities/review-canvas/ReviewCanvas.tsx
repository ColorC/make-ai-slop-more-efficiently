/**
 * entities/review-canvas/ReviewCanvas — 「材料轨迹」画布(第二期 C2, v2 泳道单程向右流)。
 *
 * A10 铁律: 命名一律"材料轨迹", 与"决策树"(决策库投影)强制区分 —— 各管各数据源, 互相以
 * 链接跳转, 不互相冒充。数据源 = 审阅台 store 的 GET /review-canvas?project=X 投影。
 *
 * 渲染(自绘, 形态权威 = docs/reviews/2026-07-04-trajectory-mockup/轨迹样张v2.html):
 *   - 撤 reactflow, 改绝对定位卡片 + SVG 曲线层, 容器 overflow 滚动(不做缩放);
 *   - 泳道 = track 横向色带, 时间从旧到新左→右(严格列网格, 见 layout.ts);
 *   - 卡片本身即节点(标题 / 版本徽章 / 状态色点 / 评论数), links = 卡片间贝塞尔曲线(汇入/分出);
 *   - supersedes 承袭线中点挂"决策过程"徽章, 点它/连线 → 右侧详情栏切到决策过程面(适用规范);
 *   - 点卡片 → 详情栏(元信息 / 打开材料 / 该版本评论 + 当场写 / 适用规范 / 待你裁决 / 发起下一步)。
 *
 * 版本级评论真源 = authored Note(target.kind='material_version'); 读=画布投影已水合(comments 带
 * version 键), 写=authoredApi.create 后刷新画布。C5 发起下一步=组装上下文包一键复制。
 */
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { BookOpen, MessageSquare, Flag } from 'lucide-react'
import { reviewstageApi, type CanvasResponse, type CanvasMaterial, type CanvasLink, type Comment } from '../../api/reviewstageClient'
import { authoredApi } from '../../api/authoredClient'
import { copyText } from '../../lib/copyText'
import { computeLayout, CARD_W, CARD_H, LANE_H, LANE_GUTTER, type CanvasLayout } from './layout'
import DecisionLens from './DecisionLens'

// 姿态切换(裁决=材料轨迹可操作 / 浏览=决策库只读透镜), 两姿态共用一套图壳。
function PostureToggle({ posture, onChange }: {
  posture: 'adjudicate' | 'browse'
  onChange: (p: 'adjudicate' | 'browse') => void
}) {
  const seg = (key: 'adjudicate' | 'browse', label: string): ReactNode => (
    <button
      key={key} type="button" data-testid={`posture-${key}`}
      onClick={() => onChange(key)}
      style={{
        padding: '2px 9px', fontSize: 12, cursor: 'pointer', borderRadius: 5,
        background: posture === key ? 'color-mix(in srgb, var(--fp-accent) 18%, transparent)' : 'transparent',
        color: posture === key ? 'var(--fp-accent)' : 'var(--fp-text-3)',
        border: `1px solid ${posture === key ? 'var(--fp-accent)' : 'var(--fp-border-subtle)'}`,
      }}
    >{label}</button>
  )
  return <span style={{ display: 'inline-flex', gap: 4 }}>{seg('adjudicate', '轨迹')}{seg('browse', '决策')}</span>
}

// links 关系 → 中文标签(承袭/下一版/相关), 与决策树 REL_LABEL 保持一致口径。
const REL_LABEL: Record<string, string> = { parent: '承袭', supersedes: '下一版', related: '相关' }

// status → 色点(对齐 review/shared 的语义色 token)。
// 决策库记录状态(浏览姿态用)与材料状态共用一张表: 生效绿/进行黄/否定红/中止紫。
const STATUS_COLOR: Record<string, string> = {
  pending: 'var(--fp-warn)', accepted: 'var(--fp-ok)', rejected: 'var(--fp-err)', blocked: 'var(--fp-violet)',
  adopted: 'var(--fp-ok)', proposed: 'var(--fp-warn)', superseded: 'var(--fp-text-3)', revoked: 'var(--fp-err)',
  untested: 'var(--fp-warn)', searching: 'var(--fp-warn)', challenged: 'var(--fp-violet)',
  supported: 'var(--fp-ok)', partial: '#39c5cf', falsified: 'var(--fp-err)',
  open: 'var(--fp-warn)', resolved: 'var(--fp-ok)', promoted: 'var(--fp-ok)',
}
export const STATUS_LABEL: Record<string, string> = {
  pending: '待审', accepted: '已通过', rejected: '已拒绝', blocked: '已阻断',
  adopted: '已拍板', proposed: '拟', superseded: '被取代', revoked: '已撤销',
  untested: '未验', searching: '查证中', challenged: '被挑战',
  supported: '已支持', partial: '部分成立', falsified: '被证伪',
  open: '开放', resolved: '已解决', promoted: '已晋升',
}
export const statusColor = (s?: string) => STATUS_COLOR[s || ''] ?? 'var(--fp-text-3)'

const KIND_LABEL: Record<string, string> = {
  image: '界面图', demo: '可玩演示', markdown: '评审文档', html: '网页', video: '视频',
  plan: '计划', 'static-report': '报告', 'aigc-image': 'AI 图', 'agent-workflow-report': '工作报告',
}
const kindLabel = (k: string) => KIND_LABEL[k] ?? k

// ── C4: adopted 裁决(决策库投影)最小结构 ──
interface DecisionNode {
  id: string
  record_kind?: string
  label?: string
  statement?: string
  status?: string
  anchor?: { kind?: string; ref?: string; excerpt?: string }
}

// 版本评论 = comments 数组里带 version 键且 === 本版本号 的; 兼容 version 为 null(挂整条材料)时也显示。
function versionComments(mat: CanvasMaterial): Comment[] {
  const v = mat.version
  return (mat.comments || []).filter((c) => {
    const cv = (c as Comment).version
    return cv == null || cv === v
  })
}

export interface ReviewCanvasProps {
  project: string
  /** 打开材料(复用既有材料打开路径: 驾驶舱 review_material 页签)。 */
  onOpenMaterial?: (mat: CanvasMaterial) => void
  /** 提供时在工具条渲染「阅读视图」按钮(切到项目的阅读态)。 */
  onOpenReader?: () => void
}

export default function ReviewCanvas({ project, onOpenMaterial, onOpenReader }: ReviewCanvasProps) {
  const [data, setData] = useState<CanvasResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selNode, setSelNode] = useState<string | null>(null)
  const [selEdge, setSelEdge] = useState<CanvasLink | null>(null)
  // 一套图壳两种姿态(决策本体阶段四): 裁决=材料轨迹(本体) / 浏览=决策库透镜(DecisionLens)。
  const [posture, setPosture] = useState<'adjudicate' | 'browse'>('adjudicate')

  const load = useCallback(() => {
    setLoading(true); setError(null)
    reviewstageApi.canvas(project)
      .then((d) => setData(d))
      .catch((e) => { setData(null); setError(String(e instanceof Error ? e.message : e)) })
      .finally(() => setLoading(false))
  }, [project])

  useEffect(() => { setSelNode(null); setSelEdge(null); load() }, [load])

  const layout: CanvasLayout = useMemo(() => computeLayout(data), [data])
  const matById = layout.cardById
  const empty = !loading && layout.cards.length === 0

  const selMat = selNode ? matById.get(selNode)?.mat ?? null : null

  const edgeSelected = useCallback(
    (l: CanvasLink) => selEdge != null && selEdge.source === l.source && selEdge.target === l.target,
    [selEdge],
  )
  const onCardClick = useCallback((id: string) => {
    setSelEdge(null)
    setSelNode((c) => (c === id ? null : id))
  }, [])
  const onEdgeClick = useCallback((l: CanvasLink) => {
    setSelNode(null)
    setSelEdge(l)
  }, [])
  const clearSel = useCallback(() => { setSelNode(null); setSelEdge(null) }, [])

  if (posture === 'browse') {
    return (
      <div style={S.root} data-testid="review-canvas">
        <div style={{ ...S.main, position: 'relative' }}>
          <div style={{ position: 'absolute', top: 6, right: 10, zIndex: 5, display: 'flex', gap: 4 }}>
            <PostureToggle posture={posture} onChange={setPosture} />
          </div>
          <DecisionLens project={project} />
        </div>
      </div>
    )
  }

  return (
    <div style={S.root} data-testid="review-canvas">
      <div style={S.main}>
        <div style={S.toolbar}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fp-text-2)' }}>材料轨迹</span>
          <PostureToggle posture={posture} onChange={setPosture} />
          {loading && <span style={S.loading}>载入中…</span>}
          {data?.stats && (
            <span style={S.count}>
              {data.stats.tracks} 轨 · {data.stats.total} 版本 · {data.stats.links} 连线
              {data.stats.unassigned > 0 && ` · ${data.stats.unassigned} 未分轨`}
            </span>
          )}
          {onOpenReader && (
            <button type="button" style={S.readerBtn} data-testid="canvas-open-reader" onClick={onOpenReader}>
              <BookOpen size={13} /> 阅读视图
            </button>
          )}
        </div>

        <div style={S.canvasWrap}>
          {error ? (
            <div style={S.emptyBox} data-testid="canvas-error">载入材料轨迹出错: {error}</div>
          ) : empty ? (
            <div style={S.emptyBox} data-testid="canvas-empty">
              该项目({project})还没有带 track/version 标签的材料。
              <div style={{ marginTop: 6 }}>
                agent 提交材料时带上 <code style={S.code}>--project {project} --track ... --version ...</code>, 就会自动落位到这里。
              </div>
            </div>
          ) : (
            <div style={S.scroll} data-testid="canvas-stage" onClick={clearSel}>
              <div style={{ position: 'relative', width: layout.width, height: layout.height, minWidth: '100%' }}>
                <LaneBands layout={layout} />
                <FlowLayer layout={layout} onEdgeClick={onEdgeClick} edgeSelected={edgeSelected} />
                {layout.cards.map((c) => (
                  <CardNode
                    key={c.mat.id}
                    x={c.x} y={c.y} color={c.color}
                    mat={c.mat}
                    selected={selNode === c.mat.id}
                    commentCount={versionComments(c.mat).length}
                    onClick={onCardClick}
                  />
                ))}
                {layout.decisionAnchor && (
                  <DecisionBadge
                    x={layout.decisionAnchor.x} y={layout.decisionAnchor.y}
                    active={edgeSelected(layout.decisionAnchor.link)}
                    onClick={() => onEdgeClick(layout.decisionAnchor!.link)}
                  />
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {selMat && (
        <NodeDetail key={selMat.id} mat={selMat} project={project} onReload={load} onOpenMaterial={onOpenMaterial} />
      )}
      {selEdge && !selMat && (
        <EdgeDetail edge={selEdge} project={project} matById={matById} />
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// 泳道色带(横向) + 左端道名
// ─────────────────────────────────────────────────────────────────────────
export function LaneBands({ layout }: { layout: CanvasLayout }) {
  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} aria-hidden>
      {layout.lanes.map((l) => (
        <div key={l.track} style={{ position: 'absolute', left: 0, right: 0, top: l.top, height: LANE_H }}>
          <div style={{
            position: 'absolute', inset: 0,
            background: `linear-gradient(90deg, ${hexA(l.color, 0.09)} 0%, rgba(255,255,255,.012) 46%, transparent 100%)`,
            borderTop: '1px solid var(--fp-border-subtle)', borderBottom: '1px solid var(--fp-border-subtle)',
          }} />
          <div style={{
            position: 'absolute', left: 0, top: 0, bottom: 0, width: LANE_GUTTER,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'linear-gradient(90deg, color-mix(in srgb, var(--fp-solid) 94%, transparent), color-mix(in srgb, var(--fp-solid) 60%, transparent))',
            borderRight: '1px solid var(--fp-border-subtle)',
          }}>
            <span style={{ position: 'absolute', left: 0, top: '14%', bottom: '14%', width: 3, borderRadius: '0 3px 3px 0', background: l.color }} />
            <span style={{
              writingMode: 'vertical-rl', textOrientation: 'upright',
              fontSize: 13, letterSpacing: 6, fontWeight: 600, color: l.color, textShadow: `0 0 14px ${hexA(l.color, 0.5)}`,
            }}>{l.track}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// SVG 曲线层(连线 = 卡片间贝塞尔; supersedes 实线带辉光 / 其余虚线)
// ─────────────────────────────────────────────────────────────────────────
export function FlowLayer({ layout, onEdgeClick, edgeSelected }: {
  layout: CanvasLayout
  onEdgeClick: (l: CanvasLink) => void
  edgeSelected: (l: CanvasLink) => boolean
}) {
  return (
    <svg
      width={layout.width} height={layout.height}
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      style={{ position: 'absolute', top: 0, left: 0, overflow: 'visible' }}
    >
      {layout.flows.map((f, i) => {
        const sel = edgeSelected(f.link)
        return (
          <g key={i} style={{ cursor: 'pointer' }} onClick={(e) => { e.stopPropagation(); onEdgeClick(f.link) }}>
            {/* 加宽透明命中区(细线不好点) */}
            <path d={f.path} fill="none" stroke="transparent" strokeWidth={14} />
            <path
              d={f.path} fill="none" stroke={f.color}
              strokeWidth={f.supersede ? (sel ? 3.6 : 3) : (sel ? 2.6 : 2)}
              strokeLinecap="round"
              strokeDasharray={f.supersede ? undefined : '2 7'}
              opacity={f.supersede ? 1 : 0.85}
              style={f.supersede ? { filter: `drop-shadow(0 0 5px ${hexA(f.color, 0.55)})` } : undefined}
            />
            <path d={f.arrow} fill={f.color} />
          </g>
        )
      })}
    </svg>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// 材料卡片 = 节点本身
// ─────────────────────────────────────────────────────────────────────────
export function CardNode({ x, y, color, mat, selected, commentCount, onClick }: {
  x: number; y: number; color: string
  mat: CanvasMaterial
  selected: boolean
  commentCount: number
  onClick: (id: string) => void
}) {
  const st = statusColor(mat.status)
  return (
    <div
      data-testid={`canvas-node-${mat.id}`}
      onClick={(e) => { e.stopPropagation(); onClick(mat.id) }}
      style={{
        position: 'absolute', left: x, top: y, width: CARD_W, minHeight: CARD_H, boxSizing: 'border-box',
        display: 'flex', flexDirection: 'column', gap: 8, padding: '11px 13px',
        background: 'linear-gradient(180deg, color-mix(in srgb, var(--fp-solid) 92%, #fff 3%), var(--fp-solid))',
        border: `1px solid ${selected ? color : 'var(--fp-border)'}`,
        borderLeft: `3px solid ${color}`, borderRadius: 11, cursor: 'pointer',
        boxShadow: selected ? `var(--fp-shadow-sm), 0 0 0 1px ${color}` : 'var(--fp-shadow-sm)',
      }}
    >
      <div style={{ fontSize: 13, color: 'var(--fp-text)', lineHeight: 1.5, fontWeight: 500, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {mat.title}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.badge}>{kindLabel(mat.kind)}</span>
        {mat.version != null && <span style={{ ...S.badge, fontVariantNumeric: 'tabular-nums' }}>v{mat.version}</span>}
        <span style={{ ...S.badge, color: st, borderColor: `color-mix(in srgb, ${st} 40%, var(--fp-border))` }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: st, flexShrink: 0 }} />
          {STATUS_LABEL[mat.status] || mat.status}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 'auto' }}>
        <span style={{ fontSize: 12, color: 'var(--fp-text-3)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
          {fmtTime(mat.created_at)}
        </span>
        {commentCount > 0 && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginLeft: 'auto', fontSize: 12, color: 'var(--fp-text-3)' }} title="评论数">
            <MessageSquare size={12} /> {commentCount}
          </span>
        )}
      </div>
    </div>
  )
}

// 决策过程徽章(骑在 supersedes 承袭线中点, 略上浮, 引线连到线上)。
function DecisionBadge({ x, y, active, onClick }: { x: number; y: number; active: boolean; onClick: () => void }) {
  const LEADER = 16
  return (
    <button
      type="button"
      data-testid="canvas-decision-badge"
      onClick={(e) => { e.stopPropagation(); onClick() }}
      style={{
        position: 'absolute', left: x, top: y - LEADER, transform: 'translate(-50%, -100%)',
        display: 'inline-flex', alignItems: 'center', gap: 7, whiteSpace: 'nowrap',
        padding: '6px 11px', borderRadius: 9, cursor: 'pointer', fontFamily: 'inherit',
        background: 'linear-gradient(135deg, color-mix(in srgb, var(--fp-violet) 24%, var(--fp-solid)), var(--fp-solid))',
        border: `1px solid ${active ? 'var(--fp-violet)' : 'color-mix(in srgb, var(--fp-violet) 55%, transparent)'}`,
        color: 'var(--fp-text)', fontSize: 12, boxShadow: 'var(--fp-shadow-sm)',
      }}
    >
      <Flag size={13} color="var(--fp-violet)" />
      决策过程
      {/* 引线: 徽章底部中心向下连到承袭线 */}
      <span aria-hidden style={{
        position: 'absolute', left: '50%', top: '100%', width: 2, height: LEADER,
        background: 'linear-gradient(180deg, var(--fp-violet), color-mix(in srgb, var(--fp-violet) 35%, transparent))',
        transform: 'translateX(-50%)',
      }} />
      <span aria-hidden style={{
        position: 'absolute', left: '50%', top: `calc(100% + ${LEADER}px)`, width: 7, height: 7, borderRadius: '50%',
        background: 'var(--fp-violet)', transform: 'translate(-50%, -50%)', boxShadow: '0 0 8px var(--fp-violet)',
      }} />
    </button>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// 版本节点详情栏
// ─────────────────────────────────────────────────────────────────────────
function NodeDetail({ mat, project, onReload, onOpenMaterial }: {
  mat: CanvasMaterial
  project: string
  onReload: () => void
  onOpenMaterial?: (mat: CanvasMaterial) => void
}) {
  const [draft, setDraft] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [commentErr, setCommentErr] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [adopted, setAdopted] = useState<DecisionNode[] | null>(null)
  const [proposed, setProposed] = useState<DecisionNode[]>([])

  const comments = versionComments(mat)
  const liveUrl = (mat.extra as Record<string, unknown> | undefined)?.live_url as string | undefined

  // 适用规范(场景三/六素材): 拉本 project 的 adopted(已拍板)+proposed(待裁决)裁决(决策库投影)。
  useEffect(() => {
    let alive = true
    fetch(`/api/v2/material-graph?project=${encodeURIComponent(project)}&status=adopted,proposed&include_deleted=false`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!alive) return
        setAdopted(pickDecisions(d, 'adopted'))
        setProposed(pickDecisions(d, 'proposed'))
      })
      .catch(() => { if (alive) { setAdopted([]); setProposed([]) } })
    return () => { alive = false }
  }, [project])

  const submitComment = useCallback(async () => {
    const content = draft.trim()
    if (!content) return
    setSubmitting(true); setCommentErr(null)
    try {
      // 版本级评论真源 = authored Note(target.kind='material_version'); 写完刷新画布水合。
      await authoredApi.create({
        content, author: 'user', uses: ['comment'],
        target: { kind: 'material_version', material_id: mat.id, version: mat.version ?? undefined },
      })
      setDraft('')
      onReload()
    } catch (e) {
      setCommentErr(String(e instanceof Error ? e.message : e))
    } finally {
      setSubmitting(false)
    }
  }, [draft, mat.id, mat.version, onReload])

  // C5 发起下一步: 组装带上下文的工作包文本, 一键复制。
  // TODO(四期): 增强形态经 omni dispatch 派发到目标对话(见 plan §8.1 场景六增强形态 / §7 omni dispatch)。
  const nextStepPackage = useCallback((): string => {
    const openComments = comments.filter((c) => c.feedback_status !== 'todo_done')
    const adoptedList = adopted ?? []
    const lines: string[] = []
    lines.push(`# 发起下一步工作包`)
    lines.push('')
    lines.push(`- 项目: ${project}`)
    lines.push(`- 轨道(track): ${mat.track || '(未分轨)'}`)
    lines.push(`- 版本族(family): ${mat.version_family || mat.title}`)
    lines.push(`- 版本: v${mat.version ?? '?'}`)
    lines.push(`- 材料标题: ${mat.title}`)
    if (mat.file_relpath) lines.push(`- 文件路径: ${mat.file_relpath}`)
    if (liveUrl) lines.push(`- 在线地址: ${liveUrl}`)
    lines.push(`- 材料 id: ${mat.id}`)
    lines.push('')
    lines.push(`## 未决审阅意见(${openComments.length})`)
    if (openComments.length === 0) lines.push('(无)')
    else for (const c of openComments) lines.push(`- ${c.content.replace(/\n+/g, ' ')}`)
    lines.push('')
    lines.push(`## 适用的已拍板裁决 id(${adoptedList.length})`)
    if (adoptedList.length === 0) lines.push('(无 / 该项目决策库暂无 adopted 裁决)')
    else for (const d of adoptedList) lines.push(`- ${d.id}${d.statement ? ` — ${d.statement}` : ''}`)
    if (proposed.length) {
      lines.push('')
      lines.push(`## 待用户裁决(阻塞项, ${proposed.length})`)
      for (const d of proposed) lines.push(`- ${d.id}${d.statement ? ` — ${d.statement}` : ''}`)
    }
    lines.push('')
    lines.push(`请基于以上材料与规范进行下一步实现工作。`)
    return lines.join('\n')
  }, [comments, adopted, proposed, project, mat, liveUrl])

  const onNextStep = useCallback(() => {
    void copyText(nextStepPackage()).then((ok) => {
      setCopied(ok)
      window.setTimeout(() => setCopied(false), 1600)
    })
  }, [nextStepPackage])

  return (
    <div style={S.sidebar} data-testid="canvas-detail">
      <div style={S.metaLine}>
        {kindLabel(mat.kind)} · {mat.tier} · {STATUS_LABEL[mat.status] || mat.status} · v{mat.version ?? '?'}
      </div>
      <div style={S.sidebarTitle}>{mat.title}</div>
      <div style={{ fontSize: 12, color: 'var(--fp-text-3)', fontFamily: 'var(--fp-font-mono)' }}>
        {mat.track || '(未分轨)'} / {mat.version_family || mat.title}
      </div>

      {/* 打开材料: 复用既有材料打开路径(驾驶舱 review_material 页签); 有 live_url 再给一个直达 */}
      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <button type="button" style={S.primaryBtn} data-testid="canvas-open-material"
          onClick={() => onOpenMaterial?.(mat)}>打开材料</button>
        {liveUrl && (
          <a href={liveUrl} target="_blank" rel="noreferrer" style={S.linkBtnBox} data-testid="canvas-open-live">打开在线</a>
        )}
      </div>

      {/* 该版本评论列表 + 当场写新评论(挂 material_version) */}
      <Section title={`该版本审阅意见(${comments.length})`}>
        {comments.length === 0 ? (
          <div style={{ color: 'var(--fp-text-3)', fontSize: 12.5 }}>还没有针对本版本的意见。</div>
        ) : (
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }} data-testid="canvas-comment-list">
            {comments.map((c) => (
              <li key={c.id} style={S.commentItem}>
                <div style={{ color: 'var(--fp-text)', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{c.content}</div>
                <div style={{ fontSize: 11, color: 'var(--fp-text-3)', marginTop: 3 }}>{c.author} · {relShort(c.created_at)}</div>
              </li>
            ))}
          </ul>
        )}
        <textarea
          data-testid="canvas-comment-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="给这个版本写一条审阅意见…"
          style={S.textarea}
        />
        {commentErr && <div style={{ color: 'var(--fp-err)', fontSize: 12, marginTop: 4 }} data-testid="canvas-comment-error">{commentErr}</div>}
        <button type="button" style={{ ...S.primaryBtn, marginTop: 8, opacity: draft.trim() && !submitting ? 1 : 0.55 }}
          data-testid="canvas-comment-submit" disabled={!draft.trim() || submitting}
          onClick={() => void submitComment()}>{submitting ? '提交中…' : '提交意见'}</button>
      </Section>

      {/* C4: 适用规范(该 project 的 adopted 裁决) */}
      <Section title={`适用规范${adopted ? `(${adopted.length})` : ''}`}>
        <div data-testid="canvas-rules">
          {adopted === null ? (
            <div style={{ color: 'var(--fp-text-3)', fontSize: 12.5 }}>载入中…</div>
          ) : adopted.length === 0 ? (
            <div style={{ color: 'var(--fp-text-3)', fontSize: 12.5 }}>该项目决策库暂无已拍板裁决。</div>
          ) : (
            <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {adopted.map((d) => <RuleItem key={d.id} d={d} />)}
            </ul>
          )}
        </div>
      </Section>

      {/* 待你裁决(proposed): 用户在画布上的裁决时刻入口 —— 裁决本体经对话/agent 落库, 此处只呈现 */}
      {proposed.length > 0 && (
        <Section title={`待你裁决(${proposed.length})`}>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }} data-testid="canvas-pending-rulings">
            {proposed.map((d) => (
              <li key={d.id} style={S.ruleItem}>
                <div style={{ color: 'var(--fp-warn)', lineHeight: 1.45 }}>{d.statement || d.label || d.id}</div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* C5 发起下一步 */}
      <div style={{ marginTop: 16 }}>
        <button type="button" style={S.nextStepBtn} data-testid="canvas-next-step" onClick={onNextStep}>
          {copied ? '已复制上下文包' : '发起下一步(复制上下文包)'}
        </button>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// 连线详情栏 = 决策过程面(C4: 点连线/决策徽章 → 列适用规范)
// ─────────────────────────────────────────────────────────────────────────
function EdgeDetail({ edge, project, matById }: {
  edge: CanvasLink
  project: string
  matById: Map<string, { mat: CanvasMaterial }>
}) {
  const [adopted, setAdopted] = useState<DecisionNode[] | null>(null)
  useEffect(() => {
    let alive = true
    fetch(`/api/v2/material-graph?project=${encodeURIComponent(project)}&status=adopted&include_deleted=false`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setAdopted(pickDecisions(d, 'adopted')) })
      .catch(() => { if (alive) setAdopted([]) })
    return () => { alive = false }
  }, [project])

  const src = matById.get(edge.source)?.mat
  const tgt = matById.get(edge.target)?.mat
  return (
    <div style={S.sidebar} data-testid="canvas-detail">
      <div style={S.metaLine}>决策过程 · {REL_LABEL[edge.rel] ?? edge.rel}</div>
      <div style={S.sidebarTitle}>
        {src?.title?.slice(0, 20) || edge.source} → {tgt?.title?.slice(0, 20) || edge.target}
      </div>
      <Section title={`本次修改适用的规范${adopted ? `(${adopted.length})` : ''}`}>
        <div data-testid="canvas-rules">
          {adopted === null ? (
            <div style={{ color: 'var(--fp-text-3)', fontSize: 12.5 }}>载入中…</div>
          ) : adopted.length === 0 ? (
            <div style={{ color: 'var(--fp-text-3)', fontSize: 12.5 }}>该项目决策库暂无已拍板裁决。</div>
          ) : (
            <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {adopted.map((d) => <RuleItem key={d.id} d={d} />)}
            </ul>
          )}
        </div>
      </Section>
    </div>
  )
}

function RuleItem({ d }: { d: DecisionNode }) {
  return (
    <li style={S.ruleItem}>
      <div style={{ color: 'var(--fp-text)', lineHeight: 1.45 }}>{d.statement || d.label || d.id}</div>
      {d.anchor?.excerpt && (
        <div style={{ fontStyle: 'italic', color: 'var(--fp-text-3)', marginTop: 3, whiteSpace: 'pre-wrap' }}>“{d.anchor.excerpt}”</div>
      )}
      {d.anchor?.ref && (
        <div style={{ fontFamily: 'var(--fp-font-mono)', color: 'var(--fp-text-3)', fontSize: 11, marginTop: 2, wordBreak: 'break-all' }}>{d.anchor.ref}</div>
      )}
    </li>
  )
}

// 裁决抽取: material-graph 投影里 record_kind=decision 按 status 分拣。
function pickDecisions(graph: { nodes?: DecisionNode[] } | null, status: string): DecisionNode[] {
  if (!graph || !Array.isArray(graph.nodes)) return []
  return graph.nodes.filter((n) => n.record_kind === 'decision'
    && (status === 'adopted' ? (!n.status || n.status === 'adopted') : n.status === status)
    // 待你裁决只列真工作提议; 历史对话炼化的 observation(锚在 session:)是语料不是待办
    && !(status === 'proposed' && String(n.anchor?.ref || '').startsWith('session:')))
}

function fmtTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${p(d.getHours())}:${p(d.getMinutes())}`
}
function relShort(iso?: string): string {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) } catch { return iso }
}

// #rrggbb + alpha → rgba()(样张里泳道/辉光用半透明色的等价物)。
export function hexA(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--fp-text-3)', marginBottom: 5 }}>{title}</div>
      {children}
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  root: { display: 'flex', height: '100%', width: '100%', fontFamily: 'var(--fp-font-sans)', background: 'transparent', color: 'var(--fp-text)' },
  main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', padding: 10, gap: 10, boxSizing: 'border-box' },
  toolbar: {
    display: 'flex', alignItems: 'center', gap: 10, padding: '7px 12px', borderRadius: 11,
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  },
  loading: { color: 'var(--fp-text-3)', fontSize: 12 },
  count: { marginLeft: 'auto', color: 'var(--fp-text-3)', fontSize: 12 },
  readerBtn: {
    display: 'inline-flex', alignItems: 'center', gap: 6, border: '1px solid var(--fp-border)',
    background: 'var(--fp-surface)', color: 'var(--fp-text-2)', borderRadius: 8, padding: '5px 11px',
    fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
  },
  canvasWrap: {
    flex: 1, minHeight: 0, position: 'relative', borderRadius: 11, overflow: 'hidden',
    background: 'var(--fp-solid)', border: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.05)',
  },
  scroll: { position: 'absolute', inset: 0, overflow: 'auto' },
  emptyBox: { padding: 28, color: 'var(--fp-text-3)', fontSize: 13, lineHeight: 1.7 },
  code: { color: 'var(--fp-text-2)', fontFamily: 'var(--fp-font-mono)' },
  badge: {
    display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, padding: '1px 8px', borderRadius: 6,
    border: '1px solid var(--fp-border-subtle)', color: 'var(--fp-text-2)', background: 'color-mix(in srgb, var(--fp-text) 3%, transparent)',
    whiteSpace: 'nowrap',
  },
  sidebar: {
    width: 380, flexShrink: 0, overflow: 'auto', padding: 16, fontSize: 13, margin: '10px 10px 10px 0', borderRadius: 11,
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: '1px solid var(--fp-border)', boxShadow: 'var(--fp-shadow-sm), inset 0 1px 0 rgba(255,255,255,.08)',
    color: 'var(--fp-text-2)',
  },
  metaLine: { fontSize: 12, color: 'var(--fp-text-3)' },
  sidebarTitle: { fontWeight: 600, margin: '4px 0 6px', color: 'var(--fp-text)', fontSize: 15, lineHeight: 1.4 },
  primaryBtn: {
    border: '1px solid var(--fp-border)', background: 'color-mix(in srgb, var(--fp-accent) 12%, transparent)',
    color: 'var(--fp-link)', borderRadius: 7, padding: '7px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
    fontFamily: 'inherit',
  },
  linkBtnBox: {
    border: '1px solid var(--fp-border)', background: 'var(--fp-surface)', color: 'var(--fp-text-2)',
    borderRadius: 7, padding: '7px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer', textDecoration: 'none',
    display: 'inline-flex', alignItems: 'center',
  },
  commentItem: { padding: '8px 0', borderBottom: '1px solid var(--fp-border-subtle)', fontSize: 12.5 },
  ruleItem: { padding: '8px 0', borderBottom: '1px solid var(--fp-border-subtle)', fontSize: 12.5 },
  textarea: {
    width: '100%', boxSizing: 'border-box', marginTop: 8, minHeight: 58, resize: 'vertical',
    background: 'var(--fp-solid)', color: 'var(--fp-text)', border: '1px solid var(--fp-border)', borderRadius: 7,
    padding: '7px 9px', fontSize: 13, fontFamily: 'inherit', lineHeight: 1.5,
  },
  nextStepBtn: {
    width: '100%', boxSizing: 'border-box', border: '1px solid var(--fp-accent)',
    background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', borderRadius: 7, padding: '9px 0',
    fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
  },
}
