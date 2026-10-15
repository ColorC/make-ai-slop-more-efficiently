const isRecord = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
import { inferFormatTag } from './formats.js'
const token = (value) => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value)
const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0
const clone = (value) => structuredClone(value)
const canonical = (value) => JSON.stringify(value, (_key, item) => {
  if (!isRecord(item)) return item
  return Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]]))
})

function validateClassificationRef(value, path, issues) {
  if (!isRecord(value)) { issues.push(`${path} 必须是 classification_ref`); return }
  if (value.schema !== 'omni.material-classification-ref/v1') issues.push(`${path}.schema 不支持`)
  if (!token(value.profile_id)) issues.push(`${path}.profile_id 必须是稳定 token`)
  if (!token(value.profile_revision)) issues.push(`${path}.profile_revision 必须是稳定 token`)
  if (!Array.isArray(value.primary_path) || value.primary_path.length < 2 || value.primary_path.some((part) => !token(part))) {
    issues.push(`${path}.primary_path 必须是稳定 token 数组`)
  }
  if (!isRecord(value.facets)) issues.push(`${path}.facets 必须是对象`)
}

/** 项目自持的 preset profile；Core 只校验通用形状，不内置项目专属 preset。 */
export function validateMaterialPresetProfile(profile) {
  const issues = []
  if (!isRecord(profile)) return ['preset profile 必须是对象']
  if (profile.schema !== 'omni.material-preset-profile/v1') issues.push('schema 必须是 omni.material-preset-profile/v1')
  for (const field of ['profile_id', 'project_id', 'revision']) {
    if (!token(profile[field])) issues.push(`${field} 必须是稳定 token`)
  }
  if (!Array.isArray(profile.presets) || profile.presets.length === 0) return [...issues, 'presets 至少需要一项']
  const ids = new Set()
  const signatures = new Map()
  profile.presets.forEach((preset, index) => {
    const path = `presets[${index}]`
    if (!isRecord(preset)) { issues.push(`${path} 必须是对象`); return }
    if (!token(preset.id)) issues.push(`${path}.id 必须是稳定 token`)
    else if (ids.has(preset.id)) issues.push(`${path}.id 重复：${preset.id}`)
    else ids.add(preset.id)
    for (const field of ['label', 'definition', 'is_not']) {
      if (!nonEmpty(preset[field])) issues.push(`${path}.${field} 必填`)
    }
    if (!isRecord(preset.source_ref) || !nonEmpty(preset.source_ref.kind) || !nonEmpty(preset.source_ref.ref)) {
      issues.push(`${path}.source_ref 必须指向预设定义来源`)
    }
    validateClassificationRef(preset.classification_ref, `${path}.classification_ref`, issues)
    if (!isRecord(preset.representation_defaults)) issues.push(`${path}.representation_defaults 必须是对象`)
    else if (!nonEmpty(preset.representation_defaults.media_type)) issues.push(`${path}.representation_defaults.media_type 必填`)
    if (!isRecord(preset.display) || !nonEmpty(preset.display.badge_label)) issues.push(`${path}.display.badge_label 必填`)
    const signature = canonical({
      classification_ref: preset.classification_ref,
      representation_defaults: preset.representation_defaults,
      display: preset.display,
    })
    const duplicate = signatures.get(signature)
    if (duplicate) issues.push(`${path} 与 preset ${duplicate} 完全重复`)
    else if (token(preset.id)) signatures.set(signature, preset.id)
  })
  return issues
}

export function validateMaterialPresetRef(ref, profile) {
  const issues = validateMaterialPresetProfile(profile)
  if (issues.length) return issues
  if (!isRecord(ref)) return ['preset_ref 必须是对象']
  for (const field of ['profile_id', 'profile_revision', 'preset_id']) {
    if (!token(ref[field])) issues.push(`preset_ref.${field} 必须是稳定 token`)
  }
  if (ref.profile_id !== profile.profile_id) issues.push('preset_ref.profile_id 与 profile 不一致')
  if (ref.profile_revision !== profile.revision) issues.push('preset_ref.profile_revision 与 profile 不一致')
  if (!profile.presets.some((preset) => preset.id === ref.preset_id)) issues.push(`未定义 preset：${ref.preset_id}`)
  return issues
}

export function resolveMaterialPreset(ref, profile) {
  const issues = validateMaterialPresetRef(ref, profile)
  if (issues.length) throw new TypeError(`Invalid Material preset: ${issues.join('; ')}`)
  return clone(profile.presets.find((preset) => preset.id === ref.preset_id))
}

/**
 * 把 preset_ref 确定性展开到 Material。调用方只选 preset；分类、展示名与 representation
 * 缺省由 profile 带出。显式值与 preset 冲突时拒绝，不做隐式覆盖。
 */
export function applyMaterialPreset(material, profile) {
  if (!isRecord(material) || !isRecord(material.preset_ref)) throw new TypeError('Material.preset_ref 必填')
  const preset = resolveMaterialPreset(material.preset_ref, profile)
  const metadata = isRecord(material.metadata) ? clone(material.metadata) : {}
  if (metadata.classification_ref && canonical(metadata.classification_ref) !== canonical(preset.classification_ref)) {
    throw new TypeError(`Material ${material.id || ''} 的 classification_ref 与 preset ${preset.id} 冲突`)
  }
  const representations = Array.isArray(material.representations)
    ? material.representations.map((representation, index) => index === 0
      ? (() => {
        const merged = { ...clone(preset.representation_defaults), ...clone(representation) }
        if (!merged.format_tag) merged.format_tag = inferFormatTag(merged)
        return merged
      })()
      : clone(representation))
    : material.representations
  return {
    ...clone(material),
    preset_ref: clone(material.preset_ref),
    representations,
    metadata: {
      ...metadata,
      classification_ref: clone(preset.classification_ref),
      preset_label: preset.label,
      preset_display: clone(preset.display),
    },
  }
}
