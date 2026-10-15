export { belongsToDomain, composeDomainGraphs, filterMaterialsForHost, filterViewMembersForHost, isVisibleInHost, materialDomain, parseDomainRegistry, registryAllows } from './domains.js'
export { declaresSemantics, describeSemantics, genericDisplay, resolveDisplayPlugin, semanticCandidates, resolveProcessPlugin, resolveSyncPlugin, viewMembersForProcess, MATERIAL_PROCESS_PLUGINS, MATERIAL_SYNC_PLUGINS } from './plugin-slots.js'
export { ASSIGNMENT_ROLES, WORKFLOW_EDGE_KINDS, WORKFLOW_LABELS, WORKFLOW_NODE_KINDS, WORKFLOW_PREDICATES, assertWorkflowSpec, parseAssignments, parseSpecMaterial, parseSyncBindings, unrollSpec, validateWorkflowSpec } from './workflow.js'
export {
  AUTHORITY_VALUES, CONTENT_CLASSES, FLOW_ROLES, KIND_REGISTRY, KIND_REGISTRY_VERSION, VISIBILITY_VALUES,
  flowRoleSpec, requiredAssertions, validateArtifactKindRegistration, validateAuthority, validateDerivation,
  validateKindPair, validateMaterialSemantics, validateNaming, validateVisibility,
} from './kinds.js'
export {
  GROUP_VIEW_RELATIONS, memberKey, projectGroupView, relationMemberFields, resolveGroupMembers, validateGroup, validateGroupView,
} from './groups.js'
export { applyMaterialPreset, resolveMaterialPreset, validateMaterialPresetProfile, validateMaterialPresetRef } from './presets.js'
export { mergeMaterialPayloads } from './merge.js'
export {
  MATERIAL_FORMATS, MATERIAL_FORMAT_TAGS, formatSupportsMediaType, formatTagsForMediaType,
  inferFormatTag, materialFormat, normalizeFormatTag, validateMaterialRepresentationFormat,
} from './formats.js'
export {
  MATERIAL_CLASSIFICATION_CONTEXT_REF_SCHEMA, MATERIAL_CLASSIFICATION_REF_SCHEMA,
  materialClassificationContextRef, materialClassificationRef,
  validateMaterialClassificationContextRef, validateMaterialClassificationRef,
} from './classification.js'
export { lintCanvasDocument, lintMaterialGraph, materialLintCodes } from './lint.js'
export const MATERIAL_CORE_VERSION = '0.3.0'
// 0.1.0 / 0.2.0 保留为**可读**版本；新写入一律 0.3.0。0.3.0 的语义校验"未声明即拒绝"，
// 不在沉默处退化 —— 这正是两套类型词汇能长期并存的原因（见 REQ-MATERIAL-CORE-0.3.0.md）。
export const SUPPORTED_MATERIAL_CORE_VERSIONS = Object.freeze(['0.1.0', '0.2.0', MATERIAL_CORE_VERSION])

const isRecord = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0
const stableCompare = (left, right) => left < right ? -1 : left > right ? 1 : 0
const isoDateTime = (value) => typeof value === 'string'
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  && !Number.isNaN(Date.parse(value))
const enumValue = (value, values) => values.includes(value)
const cloneSemantic = (value) => {
  if (Array.isArray(value)) return value.map(cloneSemantic)
  if (!isRecord(value)) return value
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, cloneSemantic(value[key])]))
}

/** Structural validation only; authority, review and governance decisions are outside Core. */
export function validateMaterialGraph(input) {
  const issues = []
  const issuePaths = new Set()
  const issue = (path, message) => {
    if (!issuePaths.has(path)) {
      issuePaths.add(path)
      issues.push({ path, message })
    }
  }
  const requiredRecord = (value, path, fields) => {
    if (!isRecord(value)) { issue(path, 'must be an object'); return false }
    let ok = true
    for (const field of fields) if (!nonEmpty(value[field])) { issue(`${path}.${field}`, 'must be a non-empty string'); ok = false }
    return ok
  }
  const validateSource = (value, path) => requiredRecord(value, path, ['kind', 'ref'])
  const validateRevision = (value, path) => requiredRecord(value, path, ['provider', 'object', 'revision'])
  const validateVersion = (value, path) => {
    if (!isRecord(value)) { issue(path, 'must be an object'); return false }
    let ok = nonEmpty(value.material_id)
    if (!ok) issue(`${path}.material_id`, 'must be a non-empty string')
    return validateRevision(value.revision_ref, `${path}.revision_ref`) && ok
  }
  const validatePreset = (value, path) => requiredRecord(value, path, ['profile_id', 'profile_revision', 'preset_id'])
  const validateAuthority = (value, path) => requiredRecord(value, path, ['object', 'purpose', 'scope'])
  const validateActor = (value, path) => requiredRecord(value, path, ['kind', 'ref'])

  if (!isRecord(input)) return { ok: false, issues: [{ path: '$', message: 'graph must be an object' }] }
  if (!SUPPORTED_MATERIAL_CORE_VERSIONS.includes(input.contract_version)) issue('contract_version', `must be one of ${SUPPORTED_MATERIAL_CORE_VERSIONS.join(', ')}`)
  if (!nonEmpty(input.graph_id)) issue('graph_id', 'must be a non-empty string')
  if (!isoDateTime(input.generated_at)) issue('generated_at', 'must be an ISO-8601 date-time string')
  if (!Array.isArray(input.materials)) issue('materials', 'must be an array')
  if (issues.length || !Array.isArray(input.materials)) return { ok: false, issues }

  const ids = new Set()
  for (const [index, material] of input.materials.entries()) {
    const path = `materials[${index}]`
    if (!isRecord(material)) { issue(path, 'must be an object'); continue }
    if (!nonEmpty(material.id)) issue(`${path}.id`, 'must be a non-empty string')
    else if (ids.has(material.id)) issue(`${path}.id`, `duplicate material id ${material.id}`)
    else ids.add(material.id)
    if (!nonEmpty(material.title)) issue(`${path}.title`, 'must be a non-empty string')
    if (material.preset_ref !== undefined) validatePreset(material.preset_ref, `${path}.preset_ref`)
    if (!Array.isArray(material.labels) || material.labels.some((label) => !nonEmpty(label))) issue(`${path}.labels`, 'must be an array of non-empty strings')
    validateRevision(material.revision_ref, `${path}.revision_ref`)
    if (!Array.isArray(material.representations) || material.representations.length === 0) issue(`${path}.representations`, 'must contain at least one representation')
    else {
      const representationIds = new Set()
      for (const [repIndex, representation] of material.representations.entries()) {
        const repPath = `${path}.representations[${repIndex}]`
        if (!isRecord(representation)) { issue(repPath, 'must be an object'); continue }
        if (!nonEmpty(representation.id)) issue(`${repPath}.id`, 'must be a non-empty string')
        else if (representationIds.has(representation.id)) issue(`${repPath}.id`, 'must be unique within material')
        else representationIds.add(representation.id)
        if (!nonEmpty(representation.media_type)) issue(`${repPath}.media_type`, 'must be a non-empty string')
        if (!nonEmpty(representation.role)) issue(`${repPath}.role`, 'must be a non-empty string')
        validateSource(representation.source_ref, `${repPath}.source_ref`)
      }
    }
    if (material.relationship !== undefined && !requiredRecord(material.relationship, `${path}.relationship`, ['from', 'predicate', 'to'])) {}
    if (material.view !== undefined) {
      if (!isRecord(material.view) || !Array.isArray(material.view.members)) issue(`${path}.view`, 'must contain members array')
      else material.view.members.forEach((member, memberIndex) => {
        if (!isRecord(member) || !nonEmpty(member.material_id)) issue(`${path}.view.members[${memberIndex}]`, 'must contain material_id')
      })
    }
    if (material.formal_domain !== undefined) {
      const domain = material.formal_domain
      const domainPath = `${path}.formal_domain`
      if (!isRecord(domain)) { issue(domainPath, 'must be an object'); continue }
      validateSource(domain.product_ref, `${domainPath}.product_ref`)
      if (!nonEmpty(domain.semantic_scope)) issue(`${domainPath}.semantic_scope`, 'must be a non-empty string')
      if (!Array.isArray(domain.formal_artifact_kinds) || domain.formal_artifact_kinds.length === 0 || domain.formal_artifact_kinds.some((kind) => !nonEmpty(kind))) issue(`${domainPath}.formal_artifact_kinds`, 'must be a non-empty array of non-empty strings')
      if (!Array.isArray(domain.authority_scopes) || domain.authority_scopes.length === 0) issue(`${domainPath}.authority_scopes`, 'must be a non-empty array')
      else domain.authority_scopes.forEach((scope, scopeIndex) => validateAuthority(scope, `${domainPath}.authority_scopes[${scopeIndex}]`))
      validateVersion(domain.rule_set_ref, `${domainPath}.rule_set_ref`)
      if (!isRecord(domain.responsibility_model) || !Array.isArray(domain.responsibility_model.required_roles) || domain.responsibility_model.required_roles.length === 0 || domain.responsibility_model.required_roles.some((role) => !nonEmpty(role))) issue(`${domainPath}.responsibility_model.required_roles`, 'must be a non-empty array of non-empty strings')
      if (!isRecord(domain.index_policy) || !nonEmpty(domain.index_policy.default_visibility)) issue(`${domainPath}.index_policy.default_visibility`, 'must be a non-empty string')
      if (!isRecord(domain.index_policy) || !nonEmpty(domain.index_policy.history_visibility)) issue(`${domainPath}.index_policy.history_visibility`, 'must be a non-empty string')
      if (!isRecord(domain.lifecycle) || !Array.isArray(domain.lifecycle.operations) || domain.lifecycle.operations.length === 0 || domain.lifecycle.operations.some((operation) => !nonEmpty(operation))) issue(`${domainPath}.lifecycle.operations`, 'must be a non-empty array of non-empty strings')
      validateSource(domain.adapter_ref, `${domainPath}.adapter_ref`)
      if (domain.extensions !== undefined && !isRecord(domain.extensions)) issue(`${domainPath}.extensions`, 'must be an object')
    }
    if (material.formalization !== undefined) {
      const formalization = material.formalization
      const formalizationPath = `${path}.formalization`
      if (!isRecord(formalization)) { issue(formalizationPath, 'must be an object'); continue }
      if (!enumValue(formalization.operation, ['enter', 'revise', 'supersede', 'withdraw'])) issue(`${formalizationPath}.operation`, 'must be enter, revise, supersede or withdraw')
      if (!enumValue(formalization.outcome, ['succeeded', 'rejected'])) issue(`${formalizationPath}.outcome`, 'must be succeeded or rejected')
      validateVersion(formalization.domain_ref, `${formalizationPath}.domain_ref`)
      validateVersion(formalization.input_material, `${formalizationPath}.input_material`)
      if (!Array.isArray(formalization.artifacts) || formalization.artifacts.length === 0) issue(`${formalizationPath}.artifacts`, 'must be a non-empty array')
      else formalization.artifacts.forEach((artifact, artifactIndex) => {
        const artifactPath = `${formalizationPath}.artifacts[${artifactIndex}]`
        if (!isRecord(artifact) || !nonEmpty(artifact.kind)) issue(`${artifactPath}.kind`, 'must be a non-empty string')
        if (!isRecord(artifact) || !nonEmpty(artifact.ref)) issue(`${artifactPath}.ref`, 'must be a non-empty string')
        if (isRecord(artifact)) validateRevision(artifact.revision_ref, `${artifactPath}.revision_ref`)
      })
      if (!Array.isArray(formalization.sources) || formalization.sources.length === 0) issue(`${formalizationPath}.sources`, 'must be a non-empty array')
      else formalization.sources.forEach((source, sourceIndex) => validateSource(source, `${formalizationPath}.sources[${sourceIndex}]`))
      if (!isRecord(formalization.responsibility)) issue(`${formalizationPath}.responsibility`, 'must be an object')
      else ['execution', 'content', 'maintenance'].forEach((role) => validateActor(formalization.responsibility[role], `${formalizationPath}.responsibility.${role}`))
      if (formalization.base_revision_ref !== undefined) validateRevision(formalization.base_revision_ref, `${formalizationPath}.base_revision_ref`)
      if (!Array.isArray(formalization.rule_results) || formalization.rule_results.length === 0) issue(`${formalizationPath}.rule_results`, 'must be a non-empty array')
      else formalization.rule_results.forEach((result, resultIndex) => {
        const resultPath = `${formalizationPath}.rule_results[${resultIndex}]`
        if (!isRecord(result) || !nonEmpty(result.rule_id)) issue(`${resultPath}.rule_id`, 'must be a non-empty string')
        if (!isRecord(result) || !enumValue(result.status, ['passed', 'failed', 'waived'])) issue(`${resultPath}.status`, 'must be passed, failed or waived')
        if (!isRecord(result) || !Array.isArray(result.evidence) || result.evidence.length === 0) issue(`${resultPath}.evidence`, 'must be a non-empty array')
        else result.evidence.forEach((evidence, evidenceIndex) => validateSource(evidence, `${resultPath}.evidence[${evidenceIndex}]`))
        if (formalization.outcome === 'succeeded' && result?.status === 'failed') issue(`${resultPath}.status`, 'succeeded formalization cannot contain a failed rule')
      })
      const effect = formalization.authority_effect
      if (!isRecord(effect)) issue(`${formalizationPath}.authority_effect`, 'must be an object')
      else {
        if (!enumValue(effect.action, ['establish', 'replace', 'withdraw', 'none'])) issue(`${formalizationPath}.authority_effect.action`, 'must be establish, replace, withdraw or none')
        if (!Array.isArray(effect.scopes) || effect.scopes.length === 0) issue(`${formalizationPath}.authority_effect.scopes`, 'must be a non-empty array')
        else effect.scopes.forEach((scope, scopeIndex) => validateAuthority(scope, `${formalizationPath}.authority_effect.scopes[${scopeIndex}]`))
        if (effect.replaces !== undefined) {
          if (!Array.isArray(effect.replaces)) issue(`${formalizationPath}.authority_effect.replaces`, 'must be an array')
          else effect.replaces.forEach((replacement, replacementIndex) => validateVersion(replacement, `${formalizationPath}.authority_effect.replaces[${replacementIndex}]`))
        }
      }
      if (!isoDateTime(formalization.occurred_at)) issue(`${formalizationPath}.occurred_at`, 'must be an ISO-8601 date-time string')
      if (!nonEmpty(formalization.idempotency_key)) issue(`${formalizationPath}.idempotency_key`, 'must be a non-empty string')
      if (formalization.extensions !== undefined && !isRecord(formalization.extensions)) issue(`${formalizationPath}.extensions`, 'must be an object')
    }
  }
  for (const [index, material] of input.materials.entries()) {
    if (!isRecord(material)) continue
    if (material.relationship) {
      if (!ids.has(material.relationship.from)) issue(`materials[${index}].relationship.from`, `source material is absent: ${material.relationship.from}`)
      if (!ids.has(material.relationship.to)) issue(`materials[${index}].relationship.to`, `target material is absent: ${material.relationship.to}`)
    }
    if (material.view?.members) for (const [memberIndex, member] of material.view.members.entries()) if (isRecord(member) && nonEmpty(member.material_id) && !ids.has(member.material_id)) issue(`materials[${index}].view.members[${memberIndex}].material_id`, 'member material is absent')
  }
  if (input.active_view_id && !ids.has(input.active_view_id)) issue('active_view_id', 'active view is absent')
  return { ok: issues.length === 0, issues }
}

export function assertMaterialGraph(input) {
  const result = validateMaterialGraph(input)
  if (!result.ok) throw new TypeError(`Invalid Material Graph: ${result.issues.slice(0, 8).map((item) => `${item.path}: ${item.message}`).join('; ')}`)
  return input
}

export function materialMap(graph) { return new Map(graph.materials.map((material) => [material.id, material])) }

export function resolveView(graph, viewId, options = {}) {
  assertMaterialGraph(graph)
  const byId = materialMap(graph); const view = byId.get(viewId)
  if (!view?.view) throw new TypeError(`${viewId} is not a View Material`)
  const mode = options.mode ?? 'expanded'; const maxDepth = options.max_depth ?? 4; const maxItems = options.max_items ?? 200
  const items = []; const diagnostics = []
  const visit = (owner, member, depth, trail) => {
    if (items.length >= maxItems) { diagnostics.push({ kind: 'truncated', at: owner.id, detail: `max_items=${maxItems}` }); return }
    const material = byId.get(member.material_id)
    if (!material) { diagnostics.push({ kind: 'missing', at: owner.id, detail: member.material_id }); return }
    if (mode === 'expanded' && material.view) {
      if (trail.has(material.id)) { diagnostics.push({ kind: 'cycle', at: owner.id, detail: material.id }); return }
      if (depth >= maxDepth) { diagnostics.push({ kind: 'truncated', at: material.id, detail: `max_depth=${maxDepth}` }); return }
      const nextTrail = new Set(trail); nextTrail.add(material.id)
      for (const nested of material.view.members) visit(material, nested, depth + 1, nextTrail)
    } else items.push({ material, member, depth, owner_view_id: owner.id })
  }
  for (const member of view.view.members) visit(view, member, 0, new Set([view.id]))
  const itemIds = new Set(items.map((item) => item.material.id))
  return { contract_version: graph.contract_version, graph_id: graph.graph_id, view, mode, items, relationships: graph.materials.filter((material) => material.relationship && itemIds.has(material.relationship.from) && itemIds.has(material.relationship.to)), diagnostics }
}

export function sliceGraphForView(graph, viewId) {
  assertMaterialGraph(graph)
  const byId = materialMap(graph); const keep = new Set([viewId]); const queue = [viewId]
  while (queue.length) { const current = byId.get(queue.shift()); if (!current?.view) continue; for (const member of current.view.members) { if (!keep.has(member.material_id)) queue.push(member.material_id); keep.add(member.material_id) } }
  for (const material of graph.materials) if (material.relationship && keep.has(material.relationship.from) && keep.has(material.relationship.to)) keep.add(material.id)
  return { ...graph, active_view_id: viewId, materials: graph.materials.filter((material) => keep.has(material.id)) }
}

/** Stable semantic manifest: excludes graph generation, inline representation content and view theme data. */
export function canonicalManifest(graph) {
  assertMaterialGraph(graph)
  const materials = graph.materials.map((material) => ({
    id: material.id, title: material.title, labels: [...material.labels].sort(), revision_ref: cloneSemantic(material.revision_ref),
    representations: material.representations.map(({ content, ...representation }) => cloneSemantic(representation)).sort((left, right) => stableCompare(left.id, right.id)),
    relationship: material.relationship ? cloneSemantic(material.relationship) : null,
    view: material.view ? { members: material.view.members.map(cloneSemantic), presentation: material.view.presentation ? Object.fromEntries(Object.entries(material.view.presentation).filter(([key]) => key !== 'theme').map(([key, value]) => [key, cloneSemantic(value)])) : null } : null,
    formal_domain: material.formal_domain ? cloneSemantic(material.formal_domain) : null,
    formalization: material.formalization ? cloneSemantic(material.formalization) : null,
    preset_ref: material.preset_ref ? cloneSemantic(material.preset_ref) : null,
  })).sort((left, right) => stableCompare(left.id, right.id))
  return { contract_version: graph.contract_version, graph_id: graph.graph_id, active_view_id: graph.active_view_id ?? null, materials }
}
