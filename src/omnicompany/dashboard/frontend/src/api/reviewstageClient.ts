/**
 * BOSS SIGHT 块 4 审阅台前端 API 客户端 — 配套
 * src/omnicompany/dashboard/boss_sight/reviewstage/routes.py
 *
 * 端点 mounted at /api/boss-sight/reviewstage.
 * WS at /api/boss-sight/reviewstage/stream (cc_proxy 透传).
 */

const BASE = '/api/boss-sight/reviewstage'

export type MaterialKind =
  | 'image' | 'markdown' | 'html' | 'key_question' | 'custom_web_template' | 'video'
  // WORK-REPORT-AND-REVIEW-TYPES 五个典型审阅类型
  | 'plan' | 'static-report' | 'demo' | 'aigc-image' | 'agent-workflow-report'
  // 决策本体候选流水线(唯一改陈述库队列): 人机同门排队人裁
  | 'decision-candidate'
export type MaterialTier = 'mandatory' | 'important' | 'processual' | 'ignored'
export type MaterialStatus = 'pending' | 'accepted' | 'rejected' | 'blocked'
export type AnnotationKind = 'ai' | 'user'
export type CommentFeedbackStatus = 'delivered' | 'read' | 'to_todo' | 'todo_done'

export interface Annotation {
  id: string
  kind: AnnotationKind
  content: string
  target: Record<string, unknown>
  created_at: string
  author: string
}

export interface Comment {
  id: string
  content: string
  author: string
  target: Record<string, unknown>
  created_at: string
  feedback_status: CommentFeedbackStatus
  feedback_history: Record<string, unknown>[]
  // A6(第二期): material_version 级评论水合进 material.comments 时带 version 键,
  // 让"材料轨迹"画布知道这条评论挂在哪个版本节点上(不是笼统挂整条材料)。空=挂整条材料。
  version?: number | null
}

/** Material 契约链新字段(第二期 A1): 复用 decisions 库 project 名录做投影。 */
export interface MaterialLinks {
  parent?: string          // 承袭: 挂靠的上游 material id(工作报告挂产物用)
  supersedes?: string[]    // 下一版: 本版取代的旧版 material id
  related?: string[]       // 相关: 关联但非版本/承袭关系的 material id
}

export type ReviewReferenceRelation =
  | 'source' | 'evidence' | 'candidate' | 'comparison_member' | 'embedded_review'
  | 'external_surface' | 'related'
export type ReviewReminderEvent =
  | 'submission_preflight' | 'embed_preflight' | 'material_open'
export type ReviewReminderSeverity = 'info' | 'warning' | 'blocking'

export interface ReviewReference {
  target: string
  relation: ReviewReferenceRelation
  label: string
}

export interface ReviewReminder {
  code: string
  event: ReviewReminderEvent
  severity: ReviewReminderSeverity
  message: string
  field_path: string
  suggested_profile_id: string
}

export interface ReviewResolutionTrace {
  selected_by: string
  routing_level: 'L1' | 'L2' | 'L3'
  confidence: number
  reason: string
  candidates: string[]
}

export interface ReviewContext {
  profile_id: string
  profile_version: number
  schema_id: string
  references: ReviewReference[]
  resolution: ReviewResolutionTrace
  reminders: ReviewReminder[]
}

export interface ReviewCapability {
  profile_id: string
  version: number
  title: string
  description: string
  status: 'available' | 'partial' | 'planned'
  material_format_id: string
  accepted_carriers: string[]
  schema_ids: string[]
  renderer_id: string
  /** catalog v2; absent on a rolling v1 backend means generic-card fallback. */
  embed_renderer_id?: string
  fallback_profile_id: string
  fallback_by_carrier: Record<string, string>
  standard_paths: string[]
  context_requirements: Array<Record<string, unknown>>
  reference_relations: ReviewReferenceRelation[]
  comment_anchors: string[]
  actions: string[]
  reminder_events: ReviewReminderEvent[]
  detectors: Array<Record<string, unknown>>
}

export interface ReviewFacilityDiscovery {
  policy: 'automatic_first'
  selection?: 'automatic' | 'manual_required'
  consult_manual?: boolean
  manual_path: string
  search_terms?: string[]
  reason?: string
  load_policy?: string
  facility_shapes?: string[]
}

export interface ReviewCapabilityCatalog {
  catalog_version: number
  authority: {
    material_semantics: 'FormatRegistry'
    review_instances: 'MaterialStore'
    catalog_role: 'validated implementation projection'
  }
  delivery: 'on_demand'
  /** catalog v3; clients must not load the handbook unless resolution asks for it. */
  facility_discovery?: ReviewFacilityDiscovery
  profiles: ReviewCapability[]
  validation: { ok: boolean; errors: string[] }
}

export interface ReviewSubmissionResolution {
  catalog_version: number
  delivery: 'on_demand'
  blocked: boolean
  review_context: ReviewContext
  capability: ReviewCapability
  /** catalog v3; absent on a rolling v2 backend means automatic/default behavior. */
  facility_discovery?: ReviewFacilityDiscovery
}

export type ContextFieldStatus = 'recorded' | 'derived' | 'unrecorded'

export interface MaterialContextField {
  key: string
  label: string
  value: unknown
  status: ContextFieldStatus
  source: string
  authority: string
  authoritative: boolean
}

export interface MaterialContextSection {
  id: 'source' | 'scope' | 'producer' | 'review' | 'lineage' | string
  label: string
  fields: MaterialContextField[]
}

export interface MaterialContextRelationship {
  relation: string
  target_type: string
  target_id?: string | null
  target_ref?: string | null
  provider?: string | null
  trace_id?: string | null
  conversation_id?: string | null
  label?: string
  authority: string
  source: string
}

export interface MaterialContextSpine {
  schema_version: number
  material_id: string
  canonical_ref: string
  authority: {
    material: string
    review: string
    session: string
    legacy_extra: string
  }
  sections: MaterialContextSection[]
  relationships: MaterialContextRelationship[]
  completeness: {
    recorded: number
    expected: number
    ratio: number
    missing: string[]
    delivery: 'on_material_open'
    emits_reminders: false
  }
}

export interface Material {
  id: string
  kind: MaterialKind
  tier: MaterialTier
  title: string
  status: MaterialStatus
  source_subagent_id: string | null
  source_plan_id: string | null
  file_relpath: string | null
  inline_content: string | null
  /** 列表响应里 inline_content 被后端裁剪过(治 22MB 列表; 全文走 GET /{id} 详情) */
  inline_content_clipped?: boolean
  annotations: Annotation[]
  comments: Comment[]
  annotations_allowed: boolean
  created_at: string
  updated_at: string
  history: Record<string, unknown>[]
  pushed_to_user: boolean
  pushed_reason: string | null
  pushed_at: string | null
  archived?: boolean
  extra: Record<string, unknown>
  /** 一等审阅场景；缺失表示尚未迁移的历史材料。 */
  review_context?: ReviewContext | null
  context_spine?: MaterialContextSpine
  // ── Material 契约链(第二期 A1): "材料轨迹"画布用它做投影 ──
  project?: string          // 复用 decisions 库 project 名录; "unfiled"=未分组
  track?: string            // 阶段/轨道名(如 信息审阅稿/交互审阅稿/工作报告)
  version?: number | null   // 版本号(同 version_family 内递增)
  version_family?: string   // 版本族(同一份稿的多版本共 family; 默认=title)
  subject_id?: string       // 内容主体唯一 ID，例如 EP0
  subject_type?: string     // 主体类型，例如 episode
  revision?: number | null  // 跨阶段共享的整体修改版本；不同于材料族 version
  links?: MaterialLinks     // 版本/承袭/相关关系, 值=material id
}

/** GET /review-canvas 材料轨迹投影(A3)返回形状。material 走 to_dict + 评论水合。 */
export interface CanvasMaterial extends Material {
  project: string
  track: string
  version: number | null
  version_family: string
  subject_id?: string
  subject_type?: string
  revision?: number | null
  links: MaterialLinks
}
export interface CanvasFamily { family: string; materials: CanvasMaterial[] }
export interface CanvasTrack { track: string; families: CanvasFamily[] }
export interface CanvasLink { source: string; target: string; rel: 'parent' | 'supersedes' | 'related' }
export interface CanvasSubjectRevision {
  revision: number
  stages: string[]
  material_ids: string[]
  archived_count: number
}
export interface CanvasSubject {
  subject_id: string
  subject_type: string
  title: string
  revisions: CanvasSubjectRevision[]
  unversioned_material_ids: string[]
}
export interface CanvasResponse {
  tracks: CanvasTrack[]
  links: CanvasLink[]
  unassigned: CanvasMaterial[]
  subjects?: CanvasSubject[]
  stats: { total: number; tracks: number; unassigned: number; links: number; subjects?: number; revisions?: number }
}

/** GET /domain-tree 决策树=具象管线投影(项目所属各注册域的产物层级步骤序列)。 */
export interface DomainTreeSample {
  id: string
  title: string
  version: number | null
  status: MaterialStatus
  file_relpath: string | null
}
export interface DomainTreeRuling {
  id: string
  statement: string
  anchor: string          // 原话摘录(anchor.excerpt, 回退 statement)
}
export interface DomainTreeStep {
  name: string            // 层级名(=material track)
  order: number
  desc: string            // 这一步做什么(人话)
  expected_kinds: string[]
  gate: { enforcer: string }
  samples: DomainTreeSample[]        // 本项目 track==层级名 的材料, 最新版优先, 上限 3
  adopted_rulings: DomainTreeRuling[] // 适用已拍板裁决(域映射 ∩ 库真实 adopted)
  next: string | null     // 下一层名(末层 null)
}
export interface DomainTreeDomain { domain: string; steps: DomainTreeStep[] }
export interface DomainTreeResponse { domains: DomainTreeDomain[] }

export interface MaterialStats {
  total: number
  by_status: Record<string, number>
  by_tier: Record<string, number>
  mandatory_unaccepted: number
  pushed_unread: number
}

export interface ReviewReadbackItem {
  id: string
  title: string
  status: MaterialStatus
  tier: MaterialTier
  kind: MaterialKind
  plan_id: string | null
  reason: string | null
  has_concrete_content: boolean
  presentation: 'highlight' | 'explained' | 'background'
  mentioned_in_conversation: boolean
  mention_evidence: string | null
  association: 'session_binding' | 'conversation_mention'
  created_at: string
}

export interface ReviewReadback {
  kind: 'review_readback'
  context: {
    resolved: boolean
    provider?: string
    session_id?: string
    trace_id?: string
    conversation_id?: string
    confidence?: 'high' | 'medium' | 'none'
  }
  counts: {
    all: number
    highlight?: number
    explained?: number
    background?: number
  }
  items: ReviewReadbackItem[]
  missing_material_ids?: string[]
}

export type ReviewCaptureKind = 'element_comment' | 'page_snapshot' | 'debug_start'

export interface ReviewCaptureBody {
  capture_kind: ReviewCaptureKind
  title?: string
  comment?: string
  author?: string
  url?: string
  route?: string
  active_tab?: Record<string, unknown>
  target?: Record<string, unknown>
  page?: Record<string, unknown>
  text_snapshot?: string
  dom_snapshot?: string
  debug_allowed?: boolean
}

export type StreamEvent =
  | { event_type: 'snapshot'; items: Material[] }
  | { event_type: 'created' | 'updated' | 'verdict_changed' | 'comment_added' | 'annotation_added' | 'pushed' | 'deleted'; material: Material }
  | { event_type: 'active_material'; material_id: string }
  | { event_type: 'ping' }

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail || '' } catch { /* */ }
    throw new Error(`${r.status} ${r.statusText} ${detail}`)
  }
  return r.json() as Promise<T>
}

const INTERNAL_REVIEW_KINDS = new Set<MaterialKind>([
  'decision-candidate',
  'image',
  'aigc-image',
])

function isInternalReviewMaterial(material: Material): boolean {
  return (
    INTERNAL_REVIEW_KINDS.has(material.kind)
    || material.extra?.reviewstage_visibility === 'internal'
  )
}

function withoutInternalReviewMaterials(
  result: { count: number; items: Material[] },
): { count: number; items: Material[] } {
  // ccdaemon 为保护长会话不会随代码改动重启；在它下次安全重启、后端默认过滤生效前，
  // 客户端也必须守住普通审阅面。图片通过父报告展示，不在队列里单独占一条。
  const items = result.items.filter((material) => !isInternalReviewMaterial(material))
  return {
    ...result,
    count: Math.max(0, result.count - (result.items.length - items.length)),
    items,
  }
}

function withoutInternalReviewStreamEvent(event: StreamEvent): StreamEvent | null {
  if (event.event_type === 'snapshot') {
    return {
      ...event,
      items: event.items.filter((material) => !isInternalReviewMaterial(material)),
    }
  }
  if ('material' in event && isInternalReviewMaterial(event.material)) return null
  return event
}

export const reviewstageApi = {
  capabilities: async (): Promise<ReviewCapabilityCatalog> => {
    const r = await fetch(`${BASE}/capabilities`)
    return jsonOrThrow(r)
  },

  readback: async (filter: {
    session_id?: string
    conversation_id?: string
    provider?: string
    include_archived?: boolean
    limit?: number
  } = {}): Promise<ReviewReadback> => {
    const q = new URLSearchParams()
    if (filter.session_id) q.set('session_id', filter.session_id)
    if (filter.conversation_id) q.set('conversation_id', filter.conversation_id)
    if (filter.provider) q.set('provider', filter.provider)
    if (filter.include_archived) q.set('include_archived', 'true')
    if (filter.limit) q.set('limit', String(filter.limit))
    const suffix = q.size ? `?${q.toString()}` : ''
    const r = await fetch(`${BASE}/readback${suffix}`)
    return jsonOrThrow(r)
  },

  /** 只在显式事件发生时调用；它解析一次，不安装会话/工具常驻 hook。 */
  resolveSubmission: async (
    intent: Record<string, unknown>,
  ): Promise<ReviewSubmissionResolution> => {
    const r = await fetch(`${BASE}/resolve-submission`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(intent),
    })
    return jsonOrThrow(r)
  },

  list: async (filter: {
    status?: MaterialStatus; tier?: MaterialTier; plan_id?: string; pushed_only?: boolean
    include_archived?: boolean; archived_only?: boolean; project?: string; track?: string
    subject_id?: string; revision?: number
    limit?: number
  } = {}): Promise<{ count: number; items: Material[] }> => {
    const q = new URLSearchParams()
    if (filter.status) q.set('status', filter.status)
    if (filter.tier) q.set('tier', filter.tier)
    if (filter.plan_id) q.set('plan_id', filter.plan_id)
    if (filter.pushed_only) q.set('pushed_only', 'true')
    if (filter.include_archived) q.set('include_archived', 'true')
    if (filter.archived_only) q.set('archived_only', 'true')
    if (filter.project) q.set('project', filter.project)
    if (filter.track) q.set('track', filter.track)
    if (filter.subject_id) q.set('subject_id', filter.subject_id)
    if (filter.revision) q.set('revision', String(filter.revision))
    if (filter.limit) q.set('limit', String(filter.limit))
    const qs = q.toString()
    // 审阅深链时 index.html 随 HTML 预取过审阅队列的开页调用(同 query), 15s 窗口内直接吃它,
    // 避免列表请求排在启动洪峰队尾(实测能晚 5s+)。其余参数组合照常走网络。
    const pf = (window as any).__omniPrefetch
    if (qs === 'include_archived=true&limit=1000' && pf?.reviewList && Date.now() - (pf.t || 0) < 15000) {
      const pre = await (pf.reviewList as Promise<{ count: number; items: Material[] } | null>)
      if (pre) return withoutInternalReviewMaterials(pre)
    }
    const r = await fetch(`${BASE}?${qs}`)
    return withoutInternalReviewMaterials(await jsonOrThrow(r))
  },

  /** 材料轨迹投影(A3): 给"材料轨迹"画布用。track=泳道, family 内版本按 version 升序, links=边表。 */
  canvas: async (project: string, options: { includeArchived?: boolean } = {}): Promise<CanvasResponse> => {
    const q = new URLSearchParams({ project })
    if (options.includeArchived) q.set('include_archived', 'true')
    const r = await fetch(`${BASE}/review-canvas?${q.toString()}`)
    return jsonOrThrow(r)
  },

  /** 决策树=具象管线投影: 项目所属各注册域的产物层级步骤序列。项目不属注册域时 domains 为空。 */
  domainTree: async (project: string): Promise<DomainTreeResponse> => {
    const r = await fetch(`${BASE}/domain-tree?project=${encodeURIComponent(project)}`)
    return jsonOrThrow(r)
  },

  /** 软归档/还原一条材料(不删文件)。 */
  setArchived: async (id: string, archived = true): Promise<Material> => {
    const r = await fetch(`${BASE}/${id}/archive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ archived, by: 'user' }),
    })
    return jsonOrThrow(r)
  },

  stats: async (): Promise<MaterialStats> => {
    const r = await fetch(`${BASE}/_stats`)
    return jsonOrThrow(r)
  },

  get: async (id: string): Promise<Material> => {
    const r = await fetch(`${BASE}/${id}`)
    return jsonOrThrow(r)
  },

  /** Read-only context join, fetched only when a material context is opened. */
  context: async (id: string): Promise<MaterialContextSpine> => {
    const r = await fetch(`${BASE}/${encodeURIComponent(id)}/context`)
    return jsonOrThrow(r)
  },

  /** 材料落盘文件绝对路径(复制给其他 agent 用)。inline 材料 file_abs_path 为 null。 */
  getPath: async (id: string): Promise<{ material_id: string; file_relpath: string | null; file_abs_path: string | null }> => {
    const r = await fetch(`${BASE}/${id}/path`)
    return jsonOrThrow(r)
  },

  setVerdict: async (id: string, verdict: MaterialStatus, reason = ''): Promise<Material> => {
    const r = await fetch(`${BASE}/${id}/verdict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict, by: 'user', reason }),
    })
    return jsonOrThrow(r)
  },

  addComment: async (id: string, content: string, target?: Record<string, unknown>): Promise<Comment> => {
    const r = await fetch(`${BASE}/${id}/comment`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, author: 'user', target }),
    })
    return jsonOrThrow(r)
  },

  // 评论文件(每材料一个 markdown): 读 / 追加。不进 Comment 数组、不唤起总控(用户 2026-06-13)。
  getCommentsFile: async (id: string, title?: string): Promise<{ content: string; path: string; abs_path: string; exists: boolean }> => {
    const q = title ? `?title=${encodeURIComponent(title)}` : ''
    const r = await fetch(`${BASE}/${id}/comments-file${q}`)
    return jsonOrThrow(r)
  },

  appendCommentsFile: async (id: string, content: string, anchor?: string, title?: string): Promise<{ content: string; path: string; abs_path: string }> => {
    const r = await fetch(`${BASE}/${id}/comments-file`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, author: 'user', anchor, title }),
    })
    return jsonOrThrow(r)
  },

  // 整文件替换(就地编辑/删除某条评论后存回)。
  writeCommentsFile: async (id: string, content: string): Promise<{ content: string; path: string; abs_path: string }> => {
    const r = await fetch(`${BASE}/${id}/comments-file`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
    return jsonOrThrow(r)
  },

  // 跨表面"激活材料"广播(三区化): 队列/材料页签选中某材料 → POST 这里 → 后端在审阅 WS 流上
  // 回广播 active_material 事件, 别的 webview(评论次级侧栏等)收到后切到该材料。单表面也无妨(本地已先生效)。
  setActiveMaterial: async (id: string): Promise<void> => {
    await fetch(`${BASE}/active`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ material_id: id }),
    }).catch(() => { /* 后端老版本/无端点: 静默, 本地联动仍生效 */ })
  },

  setCommentFeedback: async (
    id: string,
    commentId: string,
    status: CommentFeedbackStatus,
    note = '',
  ): Promise<Comment> => {
    const r = await fetch(`${BASE}/${id}/comments/${commentId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, by: 'user', note }),
    })
    return jsonOrThrow(r)
  },

  addAnnotation: async (id: string, content: string, target?: Record<string, unknown>): Promise<Annotation> => {
    const r = await fetch(`${BASE}/${id}/annotation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, kind: 'user', author: 'user', target }),
    })
    return jsonOrThrow(r)
  },

  setTier: async (id: string, new_tier: MaterialTier): Promise<Material> => {
    const r = await fetch(`${BASE}/${id}/tier`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_tier, by: 'user' }),
    })
    return jsonOrThrow(r)
  },

  batchVerdict: async (ids: string[], verdict: MaterialStatus, reason = ''): Promise<{
    ok: boolean; changed_count: number; changed_ids: string[]; not_found: string[]; skipped: Array<Record<string, string>>
  }> => {
    const r = await fetch(`${BASE}/batch_verdict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, verdict, by: 'user', reason }),
    })
    return jsonOrThrow(r)
  },

  batchTier: async (ids: string[], new_tier: MaterialTier): Promise<{
    ok: boolean; changed_count: number; changed_ids: string[]; not_found: string[]; skipped: Array<Record<string, string>>
  }> => {
    const r = await fetch(`${BASE}/batch_tier`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, new_tier, by: 'user' }),
    })
    return jsonOrThrow(r)
  },

  batchDelete: async (ids: string[], includePending = false): Promise<{
    ok: boolean; deleted_count: number; deleted_ids: string[]; skipped_pending: number; not_found: string[]
  }> => {
    const r = await fetch(`${BASE}/batch_delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, include_pending: includePending }),
    })
    return jsonOrThrow(r)
  },

  remove: async (id: string): Promise<void> => {
    const r = await fetch(`${BASE}/${id}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },

  /** 返回 inline_content 或文件内容 (image: binary blob URL; markdown/html/json: text). */
  capture: async (body: ReviewCaptureBody): Promise<Material> => {
    const r = await fetch(`${BASE}/capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return jsonOrThrow(r)
  },

  /** #3f 把聊天里出现的文件路径变成审阅材料。严格匹配→matched+material; 否则→matched:false+candidates。 */
  fromPath: async (path: string, title?: string): Promise<{
    matched: boolean
    material?: Material
    candidates?: Array<{ path: string; rel: string; name: string }>
    query?: string
  }> => {
    const r = await fetch(`${BASE}/from_path`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, title }),
    })
    return jsonOrThrow(r)
  },

  fileUrl: (id: string): string => `${BASE}/${id}/file`,

  /** 封面预览图 URL(headless 截图缓存);v 带内容版本 + nonce 做缓存击穿。 */
  coverUrl: (id: string, v = ''): string => `${BASE}/${id}/cover${v ? `?v=${encodeURIComponent(v)}` : ''}`,

  /** 触发(指定/全部非归档)材料补生成封面;Playwright 后端跑。失败静默返回 null。 */
  refreshCovers: async (ids?: string[]): Promise<{ generated: string[]; available: boolean } | null> => {
    try {
      const r = await fetch(`${BASE}/covers/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ids && ids.length ? { ids } : {}),
      })
      return r.ok ? r.json() : null
    } catch {
      return null
    }
  },

  /** 实时 WS 流. 返回 close fn. */
  openStream: (onEvent: (e: StreamEvent) => void, onError?: (e: Event) => void): () => void => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${window.location.host}${BASE}/stream`
    const ws = new WebSocket(url)
    let closed = false
    ws.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data) as StreamEvent
        const visible = withoutInternalReviewStreamEvent(parsed)
        if (visible) onEvent(visible)
      } catch { /* ignore malformed */ }
    }
    ws.onerror = (ev) => onError?.(ev)
    ws.onclose = () => { closed = true }
    return () => { if (!closed) try { ws.close() } catch { /* */ } }
  },
}
