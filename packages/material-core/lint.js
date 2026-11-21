import { validateMaterialGraph } from './index.js'
import { inferFormatTag, normalizeFormatTag, validateMaterialRepresentationFormat } from './formats.js'
import {
  materialClassificationContextRef,
  materialClassificationRef,
  validateMaterialClassificationContextRef,
  validateMaterialClassificationRef,
} from './classification.js'

const isRecord = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const list = (value) => Array.isArray(value) ? value : value === undefined ? [] : [value]

function issue(path, code, message, severity = 'error') { return { path, code, message, severity } }

function rendererSupports(renderer, formatTag, mediaType) {
  const declaredTags = renderer?.formatTags ?? renderer?.acceptsFormatTags ?? renderer?.format_tag
  const tags = list(declaredTags)
    .map(normalizeFormatTag).filter(Boolean)
  // Once a renderer declares format tags, that is its authoritative capability
  // boundary. Do not let a shared MIME type accidentally make a raw JSON
  // renderer claim table/table-diff/tutorial support.
  if (declaredTags !== undefined) return tags.includes(formatTag)
  const mediaTypes = list(renderer?.acceptsFormats ?? renderer?.mediaTypes ?? renderer?.media_types)
  return mediaTypes.some((pattern) => {
    if (pattern === formatTag || pattern === mediaType || pattern === '*/*') return true
    if (typeof pattern !== 'string' || !pattern.includes('*') || typeof mediaType !== 'string') return false
    const escaped = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replaceAll('\\*', '.*')
    return new RegExp(`^${escaped}$`, 'i').test(mediaType)
  })
}

function lintRepresentation(representation, path, options) {
  const problems = validateMaterialRepresentationFormat(representation, path, { requireFormatTag: options.requireFormatTag })
  const tag = normalizeFormatTag(representation?.format_tag ?? representation?.formatTag)
  if (tag && Array.isArray(options.renderers) && options.renderers.length > 0) {
    const supported = options.renderers.some((renderer) => !renderer?.fallback && rendererSupports(renderer, tag, representation.media_type))
    if (!supported) problems.push(issue(`${path}.format_tag`, 'renderer.unsupported-format', `no registered frontend renderer accepts format_tag=${tag}`, 'error'))
  }
  if (!tag && options.requireFormatTag === false && inferFormatTag(representation)) {
    problems.push(issue(`${path}.format_tag`, 'format-tag.inferred', `legacy representation would infer format_tag=${inferFormatTag(representation)}`, 'warning'))
  }
  return problems
}

function legacyMaterialKeys(material, path, options) {
  const problems = []
  const severity = options.allowLegacyMaterialKind === true || options.requireFormatTag === false ? 'warning' : 'error'
  for (const key of ['material_type', 'material_kind', 'material_type_id']) {
    if (Object.prototype.hasOwnProperty.call(material, key)) problems.push(issue(`${path}.${key}`, 'protocol.legacy-material-kind', `${key} is retired; use preset_ref/classification_ref`, severity))
    if (isRecord(material.metadata) && Object.prototype.hasOwnProperty.call(material.metadata, key)) problems.push(issue(`${path}.metadata.${key}`, 'protocol.legacy-material-kind', `${key} is retired; use preset_ref/classification_ref`, severity))
  }
  return problems
}

function workbenchLinkProblems(value, path) {
  if (!Array.isArray(value)) return value === undefined ? [] : [issue(path, 'workbench-links.shape', 'workbench_links must be an array')]
  const problems = []
  const ids = new Set()
  value.forEach((link, index) => {
    const itemPath = `${path}[${index}]`
    if (!isRecord(link)) { problems.push(issue(itemPath, 'workbench-links.shape', 'workbench link must be an object')); return }
    if (typeof link.id !== 'string' || !link.id.trim()) problems.push(issue(`${itemPath}.id`, 'workbench-links.id', 'id must be a non-empty string'))
    else if (ids.has(link.id)) problems.push(issue(`${itemPath}.id`, 'workbench-links.duplicate', `duplicate workbench link id ${link.id}`))
    else ids.add(link.id)
    if (typeof link.label !== 'string' || !link.label.trim()) problems.push(issue(`${itemPath}.label`, 'workbench-links.label', 'label must be a non-empty string'))
    if (typeof link.href !== 'string' || !link.href.trim()) problems.push(issue(`${itemPath}.href`, 'workbench-links.href', 'href must be a non-empty URL or relative path'))
    else if (!/^(?:https?:\/\/|\/|\.\.?(?:\/|$))/.test(link.href.trim())) problems.push(issue(`${itemPath}.href`, 'workbench-links.href', 'href must use http(s) or a relative path'))
    if (link.query !== undefined && (!isRecord(link.query)
      || Object.values(link.query).some((item) => !['string', 'number', 'boolean'].includes(typeof item)))) {
      problems.push(issue(`${itemPath}.query`, 'workbench-links.query', 'query values must be strings, numbers or booleans'))
    }
    if (link.display !== undefined) {
      if (!isRecord(link.display)) problems.push(issue(`${itemPath}.display`, 'workbench-links.display', 'display must be an object'))
      else {
        if (link.display.placement !== undefined && !['toolbar', 'menu', 'both'].includes(link.display.placement)) problems.push(issue(`${itemPath}.display.placement`, 'workbench-links.display', 'placement must be toolbar, menu or both'))
        if (link.display.target !== undefined && !['same', 'new'].includes(link.display.target)) problems.push(issue(`${itemPath}.display.target`, 'workbench-links.display', 'target must be same or new'))
        if (link.display.icon !== undefined && !['map', 'material', 'external'].includes(link.display.icon)) problems.push(issue(`${itemPath}.display.icon`, 'workbench-links.display', 'icon is not registered'))
        if (link.display.tone !== undefined && !['default', 'primary'].includes(link.display.tone)) problems.push(issue(`${itemPath}.display.tone`, 'workbench-links.display', 'tone must be default or primary'))
      }
    }
  })
  return problems
}

function canvasBehaviorProblems(value, path = 'behavior') {
  if (value === undefined) return []
  if (!isRecord(value)) return [issue(path, 'canvas.behavior-shape', 'behavior must be an object')]
  const problems = [...workbenchLinkProblems(value.workbenchLinks, `${path}.workbenchLinks`)]
  if (value.performance !== undefined) {
    if (!isRecord(value.performance)) problems.push(issue(`${path}.performance`, 'canvas.performance-shape', 'performance must be an object'))
    else {
      const performance = value.performance
      if (performance.virtualization !== undefined && !['auto', 'always', 'never'].includes(performance.virtualization)) problems.push(issue(`${path}.performance.virtualization`, 'canvas.performance-value', 'virtualization must be auto, always or never'))
      if (performance.defaultRenderMode !== undefined && !['auto', 'overview', 'detail'].includes(performance.defaultRenderMode)) problems.push(issue(`${path}.performance.defaultRenderMode`, 'canvas.performance-value', 'defaultRenderMode must be auto, overview or detail'))
      if (performance.virtualizationThreshold !== undefined && (!Number.isInteger(performance.virtualizationThreshold) || performance.virtualizationThreshold < 20)) problems.push(issue(`${path}.performance.virtualizationThreshold`, 'canvas.performance-value', 'virtualizationThreshold must be an integer >= 20'))
      for (const key of ['overviewMaxZoom', 'detailMinPixels']) if (performance[key] !== undefined && (typeof performance[key] !== 'number' || !Number.isFinite(performance[key]) || performance[key] <= 0)) problems.push(issue(`${path}.performance.${key}`, 'canvas.performance-value', `${key} must be a positive finite number`))
    }
  }
  if (value.checks !== undefined) {
    if (!Array.isArray(value.checks)) problems.push(issue(`${path}.checks`, 'canvas.checks-shape', 'checks must be an array'))
    else value.checks.forEach((check, index) => {
      if (!isRecord(check) || typeof check.checker !== 'string' || !check.checker.trim()) problems.push(issue(`${path}.checks[${index}]`, 'canvas.check-shape', 'check must declare a checker id'))
      else if (check.severity !== undefined && !['error', 'warning'].includes(check.severity)) problems.push(issue(`${path}.checks[${index}].severity`, 'canvas.check-severity', 'severity must be error or warning'))
    })
  }
  if (value.folding !== undefined && !isRecord(value.folding)) problems.push(issue(`${path}.folding`, 'canvas.folding-shape', 'folding must be an object'))
  return problems
}

function classificationProblems(material, path, options, requireClassification) {
  const problems = []
  const severity = requireClassification ? 'error' : 'warning'
  const metadata = isRecord(material?.metadata) ? material.metadata : null
  const rootRef = material?.classification_ref
  const metadataRef = metadata?.classification_ref
  const rootContext = material?.classification_context_ref
  const metadataContext = metadata?.classification_context_ref
  const ref = materialClassificationRef(material)
  const context = materialClassificationContextRef(material)
  const refPath = rootRef !== undefined ? `${path}.classification_ref` : `${path}.metadata.classification_ref`
  const contextPath = rootContext !== undefined ? `${path}.classification_context_ref` : `${path}.metadata.classification_context_ref`

  if (rootRef !== undefined && metadataRef !== undefined && JSON.stringify(rootRef) !== JSON.stringify(metadataRef)) {
    problems.push(issue(`${path}.classification_ref`, 'classification.duplicate-mismatch', 'top-level and metadata classification_ref must be identical', severity))
  }
  if (rootContext !== undefined && metadataContext !== undefined && JSON.stringify(rootContext) !== JSON.stringify(metadataContext)) {
    problems.push(issue(`${path}.classification_context_ref`, 'classification.context-duplicate-mismatch', 'top-level and metadata classification_context_ref must be identical', severity))
  }
  if (!ref) {
    problems.push(issue(`${path}.metadata.classification_ref`, 'classification.missing', 'formal classification_ref is required; run omni material profile show/classify first', severity))
  } else {
    problems.push(...validateMaterialClassificationRef(ref, refPath).map((item) => ({ ...item, severity })))
  }
  if (!context) {
    problems.push(issue(`${path}.metadata.classification_context_ref`, 'classification.context-missing', 'formal classification_context_ref is required; copy the context pointer emitted by omni material profile show/classify', severity))
  } else {
    problems.push(...validateMaterialClassificationContextRef(context, contextPath).map((item) => ({ ...item, severity })))
  }
  if (ref && context && (ref.profile_id !== context.profile_id || ref.profile_revision !== context.profile_revision)) {
    problems.push(issue(`${path}.metadata.classification_context_ref`, 'classification.context-mismatch', 'classification_context_ref profile_id/profile_revision must match classification_ref', severity))
  }
  const expected = options.classificationProfile
  if (expected && ref && context) {
    for (const key of ['profile_id', 'profile_revision'] ) {
      if (expected[key] !== undefined && (ref[key] !== expected[key] || context[key] !== expected[key])) {
        problems.push(issue(`${path}.metadata.classification_ref.${key}`, 'classification.profile-mismatch', `${key} does not match the registered classification profile`, severity))
      }
    }
    if (expected.project_id !== undefined && context.project_id !== expected.project_id) {
      problems.push(issue(`${path}.metadata.classification_context_ref.project_id`, 'classification.project-mismatch', 'project_id does not match the registered classification profile', severity))
    }
    if (expected.profile_ref !== undefined && context.profile_ref !== expected.profile_ref) {
      problems.push(issue(`${path}.metadata.classification_context_ref.profile_ref`, 'classification.source-mismatch', 'profile_ref does not match the registered classification profile', severity))
    }
  }
  if (ref && typeof options.resolveClassificationRef === 'function') {
    const result = options.resolveClassificationRef(ref)
    if (result !== true) problems.push(issue(`${path}.metadata.classification_ref`, 'classification.unresolved', typeof result === 'string' ? result : 'classification path cannot be resolved in the registered profile', severity))
  } else if (ref && requireClassification && options.requireClassificationResolution === true) {
    problems.push(issue(`${path}.metadata.classification_ref`, 'classification.unverified', 'strict lint requires a host classification resolver to verify the selected tree path', 'error'))
  }
  return problems
}

/**
 * General Material lint. Structural validation remains intentionally separate;
 * this pass checks the cross-package format/frontend protocol and legacy lanes.
 */
export function lintMaterialGraph(graph, options = {}) {
  const requireFormatTag = options.requireFormatTag ?? (graph?.contract_version === '0.3.0')
  // Classification is a write gate independent of the historical contract
  // version.  `--compat` (requireFormatTag:false) is the explicit readback
  // escape hatch; otherwise a caller cannot downgrade to 0.2 to skip it.
  const requireClassification = options.requireClassification ?? options.requireFormatTag !== false
  const structural = validateMaterialGraph(graph)
  const issues = structural.issues.map((item) => issue(item.path, 'schema.structural', item.message))
  if (!isRecord(graph) || !Array.isArray(graph?.materials)) return { ok: false, issues }
  graph.materials.forEach((material, index) => {
    if (!isRecord(material)) return
    const path = `materials[${index}]`
    issues.push(...legacyMaterialKeys(material, path, { ...options, requireFormatTag }))
    issues.push(...workbenchLinkProblems(material.workbench_links, `${path}.workbench_links`))
    issues.push(...classificationProblems(material, path, options, requireClassification))
    if (Array.isArray(material.representations)) {
      const primaryCount = material.representations.filter((representation) => representation?.role === 'primary').length
      if (primaryCount !== 1) issues.push(issue(`${path}.representations`, 'representation.primary-count', 'exactly one primary representation is required'))
      material.representations.forEach((representation, repIndex) => {
        if (isRecord(representation)) issues.push(...lintRepresentation(representation, `${path}.representations[${repIndex}]`, { ...options, requireFormatTag }))
      })
    }
    if (material.classification_ref && typeof options.resolveClassificationRef === 'function') {
      const result = options.resolveClassificationRef(material.classification_ref)
      if (result !== true) issues.push(issue(`${path}.classification_ref`, 'classification.unresolved', typeof result === 'string' ? result : 'classification reference cannot be resolved'))
    }
    if (material.preset_ref && typeof options.resolvePresetRef === 'function') {
      const result = options.resolvePresetRef(material.preset_ref)
      if (result !== true) issues.push(issue(`${path}.preset_ref`, 'preset.unresolved', typeof result === 'string' ? result : 'preset reference cannot be resolved'))
    }
  })
  return { ok: issues.every((item) => item.severity !== 'error'), issues }
}

function canvasSliceProblems(slice, path) {
  const problems = []
  if (!isRecord(slice)) return [issue(path, 'canvas.slice-shape', 'slice must be an object')]
  if (!['element', 'region', 'range', 'step'].includes(slice.kind)) problems.push(issue(`${path}.kind`, 'canvas.slice-kind', 'slice kind must be element, region, range or step'))
  if (typeof slice.ref !== 'string' || !slice.ref.trim()) problems.push(issue(`${path}.ref`, 'canvas.slice-ref', 'slice ref must be a non-empty string'))
  const needsBox = slice.kind === 'element' || slice.kind === 'region'
  if (needsBox !== Boolean(slice.box)) problems.push(issue(`${path}.box`, 'canvas.slice-box', needsBox ? `${slice.kind} slice requires normalized box` : `${slice.kind} slice must not contain box`))
  if (slice.box) {
    for (const key of ['x', 'y', 'width', 'height']) if (!Number.isFinite(slice.box[key])) problems.push(issue(`${path}.box.${key}`, 'canvas.slice-box-number', 'box coordinates must be finite numbers'))
    if (Number.isFinite(slice.box.width) && slice.box.width <= 0) problems.push(issue(`${path}.box.width`, 'canvas.slice-box-size', 'box width must be > 0'))
    if (Number.isFinite(slice.box.height) && slice.box.height <= 0) problems.push(issue(`${path}.box.height`, 'canvas.slice-box-size', 'box height must be > 0'))
  }
  return problems
}

function cardMaterialBinding(card) {
  const material = isRecord(card?.material) ? card.material : null
  return {
    material,
    embeddedId: typeof material?.id === 'string' ? material.id : null,
    materialRef: typeof card?.material_ref === 'string' ? card.material_ref : typeof material?.material_ref === 'string' ? material.material_ref : null,
    representationId: typeof card?.representation_id === 'string' ? card.representation_id : typeof material?.representation_id === 'string' ? material.representation_id : null,
    representations: Array.isArray(material?.representations) ? material.representations : [],
  }
}

/** Canvas lint consumes the projection shape and never invents a second format taxonomy. */
export function lintCanvasDocument(document, options = {}) {
  const requireClassification = options.requireClassification ?? options.requireFormatTag !== false
  const issues = []
  if (!isRecord(document)) return { ok: false, issues: [issue('$', 'canvas.document-shape', 'Canvas document must be an object')] }
  if (document.version !== 1) issues.push(issue('version', 'canvas.version', 'Canvas version must be 1'))
  if (!Array.isArray(document.cards)) issues.push(issue('cards', 'canvas.cards', 'cards must be an array'))
  if (!Array.isArray(document.relations)) issues.push(issue('relations', 'canvas.relations', 'relations must be an array'))
  issues.push(...canvasBehaviorProblems(document.behavior))
  const cards = Array.isArray(document.cards) ? document.cards : []
  const ids = new Set()
  cards.forEach((card, index) => {
    const path = `cards[${index}]`
    if (!isRecord(card)) { issues.push(issue(path, 'canvas.card-shape', 'card must be an object')); return }
    if (typeof card.id !== 'string' || !card.id.trim()) issues.push(issue(`${path}.id`, 'canvas.card-id', 'card id must be a non-empty string'))
    else if (ids.has(card.id)) issues.push(issue(`${path}.id`, 'canvas.card-duplicate', `duplicate card id ${card.id}`))
    else ids.add(card.id)
    if (card.kind === 'material') {
      const binding = cardMaterialBinding(card)
      const materialIsEmbedded = Boolean(binding.material && (binding.representations.length > 0 || materialClassificationRef(binding.material) || binding.material.preset_ref))
      if (materialIsEmbedded) {
        issues.push(...legacyMaterialKeys(binding.material, `${path}.material`, { ...options, requireFormatTag: options.requireFormatTag ?? true }))
        issues.push(...classificationProblems(binding.material, `${path}.material`, options, requireClassification))
        issues.push(...workbenchLinkProblems(binding.material.workbench_links, `${path}.material.workbench_links`))
      }
      if (!binding.materialRef && binding.representations.length === 0) issues.push(issue(`${path}.material`, 'canvas.material-ref', 'material card must carry material_ref or an embedded Material representation'))
      if (binding.materialRef && !binding.representationId) issues.push(issue(`${path}.representation_id`, 'canvas.representation-ref', 'material card must name representation_id'))
      const bindingTargetsEmbeddedMaterial = !binding.materialRef || !binding.embeddedId || binding.materialRef === binding.embeddedId
      if (bindingTargetsEmbeddedMaterial && binding.representations.length > 0 && binding.representationId && !binding.representations.some((item) => item?.id === binding.representationId)) issues.push(issue(`${path}.representation_id`, 'canvas.representation-missing', `representation ${binding.representationId} is absent from material`))
      binding.representations.forEach((representation, repIndex) => {
        if (isRecord(representation)) issues.push(...lintRepresentation(representation, `${path}.material.representations[${repIndex}]`, { ...options, requireFormatTag: options.requireFormatTag ?? true }))
      })
      if (typeof card.adapter === 'string' && card.adapter !== 'auto') issues.push(issue(`${path}.adapter`, 'protocol.legacy-adapter', 'adapter is a transport compatibility field; format comes from Material representation.format_tag', 'warning'))
      const slices = Array.isArray(card.slices) ? card.slices : Array.isArray(binding.material?.slices) ? binding.material.slices : []
      slices.forEach((slice, sliceIndex) => issues.push(...canvasSliceProblems(slice, `${path}.slices[${sliceIndex}]`)))
    }
    if (card.comment?.subject) issues.push(...canvasSliceProblems(card.comment.subject, `${path}.comment.subject`))
    if (card.fold !== undefined) {
      if (!isRecord(card.fold)) issues.push(issue(`${path}.fold`, 'canvas.fold-shape', 'fold must be an object'))
      else if (card.fold.memberIds !== undefined && (!Array.isArray(card.fold.memberIds)
        || card.fold.memberIds.some((id) => typeof id !== 'string' || !id.trim()))) issues.push(issue(`${path}.fold.memberIds`, 'canvas.fold-members', 'memberIds must contain non-empty card ids'))
    }
  })
  const relations = Array.isArray(document.relations) ? document.relations : []
  const relationIds = new Set()
  relations.forEach((relation, index) => {
    const path = `relations[${index}]`
    if (!isRecord(relation)) { issues.push(issue(path, 'canvas.relation-shape', 'relation must be an object')); return }
    if (typeof relation.id !== 'string' || !relation.id.trim()) issues.push(issue(`${path}.id`, 'canvas.relation-id', 'relation id must be a non-empty string'))
    else if (relationIds.has(relation.id)) issues.push(issue(`${path}.id`, 'canvas.relation-duplicate', `duplicate relation id ${relation.id}`))
    else relationIds.add(relation.id)
    if (!ids.has(relation.source)) issues.push(issue(`${path}.source`, 'canvas.relation-endpoint', 'source card is absent'))
    if (!ids.has(relation.target)) issues.push(issue(`${path}.target`, 'canvas.relation-endpoint', 'target card is absent'))
    if (relation.kind === 'transitions-to' && (typeof relation.trigger !== 'string' || !relation.trigger.trim())) issues.push(issue(`${path}.trigger`, 'canvas.transition-trigger', 'transitions-to requires trigger'))
    if (relation.sourceSlice) issues.push(...canvasSliceProblems(relation.sourceSlice, `${path}.sourceSlice`))
    if (relation.targetSlice) issues.push(...canvasSliceProblems(relation.targetSlice, `${path}.targetSlice`))
    if (Array.isArray(relation.routePoints) && relation.routePoints.length > 0) issues.push(issue(`${path}.routePoints`, 'canvas.intermediate-geometry', 'intermediate route points are retired; relations use endpoint-derived Bézier geometry'))
    if (relation.curveControl !== undefined && relation.curveControl !== null) issues.push(issue(`${path}.curveControl`, 'canvas.intermediate-geometry', 'manual curve controls are retired; relations use endpoint-derived Bézier geometry'))
    if (relation.path !== undefined && relation.path !== 'curve') issues.push(issue(`${path}.path`, 'canvas.relation-path', 'Canvas relations use the shared endpoint-derived Bézier path'))
  })
  return { ok: issues.every((item) => item.severity !== 'error'), issues }
}

export const materialLintCodes = Object.freeze({
  legacyKind: 'protocol.legacy-material-kind',
  missingFormat: 'format-tag.missing',
  unsupportedFormat: 'renderer.unsupported-format',
  legacyAdapter: 'protocol.legacy-adapter',
  missingClassification: 'classification.missing',
  missingClassificationContext: 'classification.context-missing',
  unresolvedClassification: 'classification.unresolved',
})
