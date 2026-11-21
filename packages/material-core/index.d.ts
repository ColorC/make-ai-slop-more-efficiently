export const MATERIAL_CORE_VERSION: '0.3.0'
export const SUPPORTED_MATERIAL_CORE_VERSIONS: readonly ['0.1.0', '0.2.0', '0.3.0']
export type MaterialContractVersion = (typeof SUPPORTED_MATERIAL_CORE_VERSIONS)[number]
export interface MaterialSourceRef { kind: string; ref: string; selector?: string }
export interface MaterialRevisionRef { provider: string; object: string; revision: string }
export interface MaterialVersionRef { material_id: string; revision_ref: MaterialRevisionRef }
export interface MaterialPresetRef { profile_id: string; profile_revision: string; preset_id: string }
export interface MaterialClassificationRef {
  schema: 'omni.material-classification-ref/v1'
  profile_id: string
  profile_revision: string
  primary_path: string[]
  facets: Record<string, string[][]>
  [key: string]: unknown
}
export interface MaterialClassificationContextRef {
  schema: 'omni.material-classification-context-ref/v1'
  profile_id: string
  profile_revision: string
  project_id: string
  profile_ref: string
  read_via: string
  [key: string]: unknown
}
export interface ActorRef { kind: string; ref: string }
export interface AuthorityScope { object: string; purpose: string; scope: string }
export interface FormalArtifactRef { kind: string; ref: string; revision_ref: MaterialRevisionRef }
export interface MaterialRepresentation { id: string; media_type: string; role: string; title?: string; source_ref: MaterialSourceRef; /** Canonical frontend protocol tag; media_type is transport only. */ format_tag?: string; /** @deprecated compatibility selector; use format_tag. */ renderer_hint?: string; content?: string; content_format?: 'markdown' | 'text' | 'json' | 'html' | 'latex' | 'mermaid' }
export interface MaterialRelationship { from: string; predicate: string; to: string }
export interface MaterialViewMember { material_id: string; representation_id?: string; group?: string; order?: number; display?: MaterialDisplayOverride }
export interface MaterialViewGroup { id: string; label?: string; title?: string; display?: MaterialDisplayOverride; [key: string]: unknown }
export interface MaterialViewDefinition { members: MaterialViewMember[]; groups?: MaterialViewGroup[]; display?: MaterialDisplayOverride; presentation?: { mode?: string; density?: string; theme?: unknown; [key: string]: unknown } }
export interface MaterialWorkbenchLink { id: string; label: string; href: string; query?: Record<string, string | number | boolean>; display?: { placement?: 'toolbar' | 'menu' | 'both'; target?: 'same' | 'new'; icon?: 'map' | 'material' | 'external'; tone?: 'default' | 'primary' } }
export interface FormalDomain { product_ref: MaterialSourceRef; semantic_scope: string; formal_artifact_kinds: string[]; authority_scopes: AuthorityScope[]; rule_set_ref: MaterialVersionRef; responsibility_model: { required_roles: string[] }; index_policy: { default_visibility: string; history_visibility: string }; lifecycle: { operations: string[] }; adapter_ref: MaterialSourceRef; extensions?: Record<string, unknown> }
export interface Formalization { operation: 'enter' | 'revise' | 'supersede' | 'withdraw'; outcome: 'succeeded' | 'rejected'; domain_ref: MaterialVersionRef; input_material: MaterialVersionRef; artifacts: FormalArtifactRef[]; sources: MaterialSourceRef[]; responsibility: { execution: ActorRef; content: ActorRef; maintenance: ActorRef }; base_revision_ref?: MaterialRevisionRef; rule_results: Array<{ rule_id: string; status: 'passed' | 'failed' | 'waived'; evidence: MaterialSourceRef[]; note?: string }>; authority_effect: { action: 'establish' | 'replace' | 'withdraw' | 'none'; scopes: AuthorityScope[]; replaces?: MaterialVersionRef[] }; occurred_at: string; idempotency_key: string; extensions?: Record<string, unknown> }
export interface Material { id: string; title: string; summary?: string; labels: string[]; revision_ref: MaterialRevisionRef; preset_ref?: MaterialPresetRef; representations: MaterialRepresentation[]; relationship?: MaterialRelationship; view?: MaterialViewDefinition; workbench_links?: MaterialWorkbenchLink[]; formal_domain?: FormalDomain; formalization?: Formalization; metadata?: Record<string, unknown>; classification_ref?: MaterialClassificationRef; classification_context_ref?: MaterialClassificationContextRef }
export interface MaterialGraph { contract_version: MaterialContractVersion; graph_id: string; generated_at: string; active_view_id?: string; materials: Material[] }
export interface MaterialGraphIssue { path: string; message: string }
export function validateMaterialGraph(input: unknown): { ok: boolean; issues: MaterialGraphIssue[] }
export function assertMaterialGraph(input: unknown): asserts input is MaterialGraph
export function materialMap(graph: MaterialGraph): Map<string, Material>
export interface MaterialFormatDefinition { tag: string; mediaTypes: string[]; contentFormats: string[]; rendererIds: string[]; aliases: string[] }
export const MATERIAL_FORMATS: Readonly<Record<string, MaterialFormatDefinition>>
export const MATERIAL_FORMAT_TAGS: readonly string[]
export function normalizeFormatTag(value: unknown): string | null
export function materialFormat(tag: unknown): MaterialFormatDefinition | null
export function formatSupportsMediaType(tag: unknown, mediaType: unknown): boolean
export function formatTagsForMediaType(mediaType: unknown): string[]
export function inferFormatTag(representation?: Partial<MaterialRepresentation>): string | null
export function validateMaterialRepresentationFormat(representation: Partial<MaterialRepresentation>, where?: string, options?: { requireFormatTag?: boolean }): Array<{ path: string; code: string; severity: 'error' | 'warning'; message: string }>
export const MATERIAL_CLASSIFICATION_REF_SCHEMA: 'omni.material-classification-ref/v1'
export const MATERIAL_CLASSIFICATION_CONTEXT_REF_SCHEMA: 'omni.material-classification-context-ref/v1'
export function validateMaterialClassificationRef(value: unknown, where?: string): Array<{ path: string; code: string; severity: 'error' | 'warning'; message: string }>
export function validateMaterialClassificationContextRef(value: unknown, where?: string): Array<{ path: string; code: string; severity: 'error' | 'warning'; message: string }>
export function materialClassificationRef(material: unknown): MaterialClassificationRef | null
export function materialClassificationContextRef(material: unknown): MaterialClassificationContextRef | null
export interface MaterialLintIssue { path: string; code: string; severity: 'error' | 'warning'; message: string }
export interface MaterialLintOptions {
  requireFormatTag?: boolean
  /** Require a formal classification_ref and classification_context_ref on strict writes. */
  requireClassification?: boolean
  /** Require a host resolver instead of only validating the copy-free ref shape. */
  requireClassificationResolution?: boolean
  /** Expected owner profile pointer used by hosts that lint one project at a time. */
  classificationProfile?: Partial<Pick<MaterialClassificationContextRef, 'profile_id' | 'profile_revision' | 'project_id' | 'profile_ref'>>
  allowLegacyMaterialKind?: boolean
  renderers?: Array<Record<string, unknown>>
  resolveClassificationRef?: (ref: unknown) => true | string | unknown
  resolvePresetRef?: (ref: unknown) => true | string | unknown
}
export function lintMaterialGraph(graph: unknown, options?: MaterialLintOptions): { ok: boolean; issues: MaterialLintIssue[] }
export function lintCanvasDocument(document: unknown, options?: Pick<MaterialLintOptions, 'requireFormatTag' | 'requireClassification' | 'requireClassificationResolution' | 'classificationProfile' | 'allowLegacyMaterialKind' | 'renderers' | 'resolveClassificationRef'>): { ok: boolean; issues: MaterialLintIssue[] }
export const materialLintCodes: Readonly<Record<string, string>>
export function resolveView(graph: MaterialGraph, viewId: string, options?: { mode?: 'source' | 'expanded'; max_depth?: number; max_items?: number }): { contract_version: string; graph_id: string; view: Material; mode: 'source' | 'expanded'; items: Array<{ material: Material; member: MaterialViewMember; depth: number; owner_view_id: string }>; relationships: Material[]; diagnostics: Array<{ kind: string; at: string; detail: string }> }
export function sliceGraphForView(graph: MaterialGraph, viewId: string): MaterialGraph
export function canonicalManifest(graph: MaterialGraph): Record<string, unknown>
export interface MaterialPresetDefinition {
  id: string
  label: string
  definition: string
  is_not: string
  source_ref: MaterialSourceRef
  classification_ref: { schema: 'omni.material-classification-ref/v1'; profile_id: string; profile_revision: string; primary_path: string[]; facets: Record<string, string[][]>; [key: string]: unknown }
  representation_defaults: Partial<MaterialRepresentation> & { media_type: string }
  display: { badge_label: string; [key: string]: unknown }
}
export interface MaterialPresetProfile {
  schema: 'omni.material-preset-profile/v1'
  profile_id: string
  project_id: string
  revision: string
  presets: MaterialPresetDefinition[]
}
export function validateMaterialPresetProfile(profile: unknown): string[]
export function validateMaterialPresetRef(ref: unknown, profile: MaterialPresetProfile): string[]
export function resolveMaterialPreset(ref: MaterialPresetRef, profile: MaterialPresetProfile): MaterialPresetDefinition
export function applyMaterialPreset<T extends Material>(material: T & { preset_ref: MaterialPresetRef }, profile: MaterialPresetProfile): T & { metadata: Record<string, unknown> }
export interface MaterialMergeSource {
  source_id: string
  revision_ref: { provider: string; object: string; revision: string }
  data: Record<string, unknown>
}
export interface MaterialMergeRecipe {
  fields: Record<string, {
    strategy?: 'reject-conflict' | 'prefer-source' | 'coalesce' | 'concat' | 'keyed-table'
    source_id?: string
    key?: string
    on_conflict?: 'reject' | 'last-wins'
  }>
  display?: Record<string, unknown>
}
export function mergeMaterialPayloads(sources: MaterialMergeSource[], recipe: MaterialMergeRecipe): {
  data: Record<string, unknown>
  provenance: Record<string, { strategy: string; sources: string[]; row_sources?: Record<string, string> }>
  source_revisions: Record<string, MaterialMergeSource['revision_ref']>
  display: Record<string, unknown>
}
export function materialDomain(id: string): string
export function belongsToDomain(id: string, domain: string): boolean
export function parseDomainRegistry(input: unknown): { host_domain: string; foreign_domains: Array<{ domain: string; graph_entry?: string; allow_write: boolean }> }
export function registryAllows(registry: { host_domain: string; foreign_domains: Array<{ domain: string }> } | null | undefined, hostDomain: string, foreignDomain: string): boolean
export function isVisibleInHost(materialId: string, hostDomain: string, registry?: { host_domain: string; foreign_domains: Array<{ domain: string }> } | null): boolean
export function filterMaterialsForHost<T extends { id: string }>(materials: T[], hostDomain: string, registry?: { host_domain: string; foreign_domains: Array<{ domain: string }> } | null): T[]
export function filterViewMembersForHost<T extends { material_id: string }>(members: T[], hostDomain: string, registry?: { host_domain: string; foreign_domains: Array<{ domain: string }> } | null): T[]
export function composeDomainGraphs(hostGraph: MaterialGraph, foreignGraph: MaterialGraph, hostDomain: string, registry?: { host_domain: string; foreign_domains: Array<{ domain: string }> } | null): { graph: MaterialGraph; held_apart: Array<{ origin: string; id: string }>; merged: boolean }
export function genericDisplay(material: Material): { kind: 'generic'; member_id: string; title: string; body: string; media_type: string; writable: false }
export interface MaterialDisplayOverride { pluginId: string }
export interface DisplayPluginDefinition { id: string; hints?: string[]; mediaTypes?: string[]; formatTags?: string[]; labels?: string[]; acceptsFlowRoles?: string[]; acceptsArtifactKinds?: string[]; acceptsFormats?: string[]; acceptsFormatTags?: string[] }
export interface DisplayPluginContext {
  memberOverride?: MaterialDisplayOverride | string | null
  groupOverride?: MaterialDisplayOverride | string | null
  compositionOverride?: MaterialDisplayOverride | string | null
  member?: { display?: MaterialDisplayOverride | null }
  group?: { display?: MaterialDisplayOverride | null }
  composition?: { display?: MaterialDisplayOverride | null }
}
export function resolveDisplayPlugin(material: Material, plugins?: DisplayPluginDefinition[], context?: DisplayPluginContext): { kind: 'generic' | 'plugin'; plugin_id?: string; resolution: 'member-override' | 'group-override' | 'composition-override' | 'renderer-hint' | 'media-type' | 'material-label' | 'generic-fallback'; member_id: string; title: string; body: string; media_type: string; writable: false }
export const MATERIAL_PROCESS_PLUGINS: ReadonlyArray<{ id: string; provider: string; can_write: boolean }>
export function resolveProcessPlugin(revisionRef: MaterialRevisionRef, plugins?: Array<{ id: string; provider?: string; providers?: string[]; can_write?: boolean }>): { kind: 'generic' | 'plugin'; plugin_id?: string; provider: string; can_write: boolean }
export function viewMembersForProcess(view: unknown, members: MaterialViewMember[]): Array<{ material_id: string; order?: number; group?: string }>

export const WORKFLOW_LABELS: Readonly<{ SOURCE: 'source'; SPEC: 'spec'; RUN: 'run'; CHECK: 'check'; GATE: 'gate'; TEMPLATE: 'template'; PLACEHOLDER: 'placeholder'; ENV_PROFILE: 'env-profile'; EPHEMERAL: 'ephemeral' }>
export const WORKFLOW_NODE_KINDS: readonly ['anchor', 'worker', 'check', 'gate']
export const WORKFLOW_EDGE_KINDS: readonly ['sequence', 'feedback']
export const ASSIGNMENT_ROLES: readonly ['owner', 'reviewer', 'assistant']
export const WORKFLOW_PREDICATES: readonly ['executes', 'overrides', 'instantiates', 'feedback', 'syncs-to']
export interface WorkflowSpecNode { id: string; title?: string; kind: 'anchor' | 'worker' | 'check' | 'gate'; worker_ref?: string; check_ref?: string; format_in?: string; format_out?: string; params?: Record<string, unknown> }
export interface WorkflowSpecEdge { from: string; to: string; kind?: 'sequence' | 'feedback'; condition?: string; max?: number }
export interface WorkflowSpec { id: string; title: string; entry: string; nodes: WorkflowSpecNode[]; edges?: WorkflowSpecEdge[]; params?: Record<string, unknown> }
export interface WorkflowSpecIssue { path: string; message: string }
export function validateWorkflowSpec(spec: unknown): { ok: boolean; issues: WorkflowSpecIssue[] }
export function assertWorkflowSpec(spec: unknown): asserts spec is WorkflowSpec
export function parseSpecMaterial(material: Material | null | undefined): WorkflowSpec | null
export interface UnrolledRunNode { id: string; source_node_id: string; iteration: number; node: WorkflowSpecNode }
export interface UnrolledRunEdge { from: string; to: string; kind: 'sequence' | 'feedback'; condition?: string; iteration_from?: number; iteration_to?: number }
export function unrollSpec(spec: WorkflowSpec, options?: { default_max?: number }): { spec_id: string; nodes: UnrolledRunNode[]; edges: UnrolledRunEdge[]; entry: string }
export interface SyncBinding { plugin: string; external_ref: string; last_synced_revision: string | null; direction: string }
export function parseSyncBindings(value: unknown): SyncBinding[]
export interface AssignmentEntry { actor: ActorRef; role: 'owner' | 'reviewer' | 'assistant'; mode: string | null; time: { due?: string; window?: { earliest: string; latest: string }; after?: string } | null }
export function parseAssignments(value: unknown): AssignmentEntry[]
export const MATERIAL_SYNC_PLUGINS: ReadonlyArray<{ id: string; direction: string; events: string[]; can_write: boolean }>
export function resolveSyncPlugin(material: Material, plugins?: Array<{ id: string }>): Array<{ plugin: { id: string }; binding: Record<string, unknown> }>

/* ── 语义类型注册表（core 0.3.0，唯一裁决入口；见 REQ-MATERIAL-CORE-0.3.0.md） ────────── */
export const KIND_REGISTRY: Readonly<Record<string, unknown>>
export const KIND_REGISTRY_VERSION: string
/** 流程角色轴：封闭值集，跨项目通用。不得在项目侧新增。 */
export const FLOW_ROLES: ReadonlyArray<string>
export const CONTENT_CLASSES: ReadonlyArray<string>
export const VISIBILITY_VALUES: ReadonlyArray<string>
export const AUTHORITY_VALUES: ReadonlyArray<string>
export function flowRoleSpec(role: string): { display_name: string; doc: string; requires: string[]; content_classes: string[] } | null
export function requiredAssertions(role: string): ReadonlyArray<string> | null
export function validateArtifactKindRegistration(entry: { kind?: string; content_class?: string; project_scope?: string }): string[]
export function validateKindPair(input: { flow_role?: string; artifact_kind?: string; content_class?: string }): string[]
export function validateNaming(input: { id?: string; display_name?: string; summary?: string }): string[]
export function validateVisibility(input: { visibility?: string; navigation?: Record<string, unknown> }, options?: { homeViewId?: string }): string[]
export function validateAuthority(input: { authority?: string; writable?: boolean }): string[]
export function validateDerivation(derivation: Record<string, unknown> | null | undefined): string[]
/** 单材料语义校验总入口。**返回非空即拒绝注册**，调用方不得降级为警告。 */
export function validateMaterialSemantics(
  material: Material,
  options?: { resolveContentClass?: (kind: string) => string | undefined; homeViewId?: string },
): string[]

/* ── 能力声明反转（core 0.3.0 §3.3）────────────────────────────────────────────── */
/** 材料是否声明了语义（flow_role / artifact_kind 任一）。声明了就不允许在"无人接受"时静默退成 generic。 */
export function declaresSemantics(material: Material): boolean
export function describeSemantics(material: Material, representation?: unknown): {
  flow_role: string | null; artifact_kind: string | null; format: string | null
}
/** 消费者侧能力匹配：plugin 声明的每个维度都必须命中；未声明视为不约束。 */
export function semanticCandidates(
  material: Material,
  representation: unknown,
  plugins?: Array<{ id: string; acceptsFlowRoles?: string[]; acceptsArtifactKinds?: string[]; acceptsFormats?: string[]; acceptsFormatTags?: string[]; hints?: string[] }>,
): Array<{ id: string }>

/* ── 组与组视图（core 0.3.0 §3.4）───────────────────────────────────────────────── */
/** 关系表达模式，封闭值集：spatial=空间关联（坐标/边），sequential=顺序关联（次序/分支）。 */
export const GROUP_VIEW_RELATIONS: ReadonlyArray<'spatial' | 'sequential'>
export interface MaterialGroupMember { material_id: string; representation_id?: string }
export interface MaterialGroup {
  id: string
  display_name: string
  summary?: string
  member_kinds?: { flow_roles?: string[]; artifact_kinds?: string[] }
  members?: Array<MaterialGroupMember | string>
  selector?: Record<string, unknown>
}
export interface MaterialGroupView {
  id: string
  display_name: string
  group_id?: string
  relation: 'spatial' | 'sequential'
  /** 视图**局部**字段：spatial 允许 x/y/width/height/edges；sequential 允许 order/branch。 */
  members?: Record<string, Record<string, unknown>>
}
/** 该关系模式允许的视图局部成员字段；未知模式返回 null。 */
/** 成员键：material_id 或 material_id#representation_id。同一材料的多个阶段靠后者区分。 */
export function memberKey(member: MaterialGroupMember | string): string
export function relationMemberFields(relation: string): ReadonlyArray<string> | null
export function resolveGroupMembers(
  group: MaterialGroup,
  options?: { resolveSelector?: (selector: Record<string, unknown>) => Array<MaterialGroupMember | string> },
): MaterialGroupMember[]
export function validateGroup(
  group: MaterialGroup,
  options?: {
    resolveSelector?: (selector: Record<string, unknown>) => Array<MaterialGroupMember | string>
    resolveMaterialSemantics?: (materialId: string) => { flow_role?: string; artifact_kind?: string } | undefined
  },
): string[]
/** 核心不变量：视图不得引入组里没有的成员，且局部字段必须属于该关系模式。 */
export function validateGroupView(
  view: MaterialGroupView,
  group: MaterialGroup,
  options?: { resolveSelector?: (selector: Record<string, unknown>) => Array<MaterialGroupMember | string> },
): string[]
/** 组 + 视图 → 渲染端成员序列。两种模式共用同一份成员，差异只体现在这里。 */
export function projectGroupView(
  group: MaterialGroup,
  view: MaterialGroupView,
  options?: { resolveSelector?: (selector: Record<string, unknown>) => Array<MaterialGroupMember | string> },
): Array<MaterialGroupMember & { view_local: Record<string, unknown> }>
