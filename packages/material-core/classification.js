/**
 * Canonical pointers emitted by `omni material profile show/classify`.
 *
 * A Material stores pointers only; the project-owned profile remains the
 * authority and is loaded by the host resolver.  The context ref is the
 * small, copy-free proof envelope that the agent used the registered profile
 * before writing a classification_ref.
 */

const isRecord = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const token = (value) => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value)
const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0

export const MATERIAL_CLASSIFICATION_REF_SCHEMA = 'omni.material-classification-ref/v1'
export const MATERIAL_CLASSIFICATION_CONTEXT_REF_SCHEMA = 'omni.material-classification-context-ref/v1'

const problem = (path, code, message) => ({ path, code, severity: 'error', message })

function validatePath(value, path, minimum = 1) {
  if (!Array.isArray(value) || value.length < minimum || value.some((part) => !token(part))) {
    return [problem(path, 'classification.path', 'classification path must be a non-empty stable token array')]
  }
  return []
}

/** Validate only the wire shape; path existence is checked by the host resolver. */
export function validateMaterialClassificationRef(value, where = 'classification_ref') {
  const problems = []
  if (!isRecord(value)) return [problem(where, 'classification.shape', 'classification_ref must be an object')]
  if (value.schema !== MATERIAL_CLASSIFICATION_REF_SCHEMA) problems.push(problem(`${where}.schema`, 'classification.schema', `schema must be ${MATERIAL_CLASSIFICATION_REF_SCHEMA}`))
  if (!token(value.profile_id)) problems.push(problem(`${where}.profile_id`, 'classification.profile-id', 'profile_id must be a stable token'))
  if (!token(value.profile_revision)) problems.push(problem(`${where}.profile_revision`, 'classification.profile-revision', 'profile_revision must be a stable token'))
  problems.push(...validatePath(value.primary_path, `${where}.primary_path`, 2))
  if (!isRecord(value.facets)) problems.push(problem(`${where}.facets`, 'classification.facets', 'facets must be an object'))
  else {
    for (const [facetId, paths] of Object.entries(value.facets)) {
      if (!token(facetId)) problems.push(problem(`${where}.facets.${facetId}`, 'classification.facet-id', 'facet id must be a stable token'))
      if (!Array.isArray(paths) || paths.length === 0 || paths.some((path) => validatePath(path, `${where}.facets.${facetId}`).length > 0)) {
        problems.push(problem(`${where}.facets.${facetId}`, 'classification.facet-path', 'facet value must contain one or more stable token paths'))
      }
    }
  }
  return problems
}

/**
 * Validate the copy-free receipt produced by the official profile commands.
 * This is intentionally not a signature: a static lint can prove that the
 * Material carries a registered-profile pointer, while the host resolver
 * proves the pointed path exists in the current owner profile.
 */
export function validateMaterialClassificationContextRef(value, where = 'classification_context_ref') {
  const problems = []
  if (!isRecord(value)) return [problem(where, 'classification.context-shape', 'classification_context_ref must be an object')]
  if (value.schema !== MATERIAL_CLASSIFICATION_CONTEXT_REF_SCHEMA) problems.push(problem(`${where}.schema`, 'classification.context-schema', `schema must be ${MATERIAL_CLASSIFICATION_CONTEXT_REF_SCHEMA}`))
  if (!token(value.profile_id)) problems.push(problem(`${where}.profile_id`, 'classification.context-profile-id', 'profile_id must be a stable token'))
  if (!token(value.profile_revision)) problems.push(problem(`${where}.profile_revision`, 'classification.context-profile-revision', 'profile_revision must be a stable token'))
  if (!token(value.project_id)) problems.push(problem(`${where}.project_id`, 'classification.context-project-id', 'project_id must be a stable token'))
  if (!nonEmpty(value.profile_ref) || !/^(workspace|omnicompany|mount):\S+$/.test(value.profile_ref.trim())) problems.push(problem(`${where}.profile_ref`, 'classification.context-profile-ref', 'profile_ref must be a registered workspace/omnicompany/mount pointer'))
  if (!nonEmpty(value.read_via) || !['omni material profile show', 'omni material profile classify'].includes(value.read_via.trim())) {
    problems.push(problem(`${where}.read_via`, 'classification.context-read-via', 'read_via must be emitted by omni material profile show or classify'))
  }
  return problems
}

export function materialClassificationRef(material) {
  if (!isRecord(material)) return null
  const metadata = isRecord(material.metadata) ? material.metadata : null
  return material.classification_ref ?? metadata?.classification_ref ?? null
}

export function materialClassificationContextRef(material) {
  if (!isRecord(material)) return null
  const metadata = isRecord(material.metadata) ? material.metadata : null
  return material.classification_context_ref ?? metadata?.classification_context_ref ?? null
}
