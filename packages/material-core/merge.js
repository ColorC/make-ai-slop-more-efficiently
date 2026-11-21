const isRecord = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const clone = (value) => structuredClone(value)
const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right)

function exactSource(source, index) {
  if (!isRecord(source) || typeof source.source_id !== 'string' || !source.source_id.trim()) {
    throw new TypeError(`sources[${index}].source_id 必填`)
  }
  const revision = source.revision_ref
  if (!isRecord(revision) || !['provider', 'object', 'revision'].every((key) => typeof revision[key] === 'string' && revision[key].trim())) {
    throw new TypeError(`sources[${index}].revision_ref 必须是精确版本引用`)
  }
  if (!isRecord(source.data)) throw new TypeError(`sources[${index}].data 必须是对象`)
  return clone(source)
}

function getPath(value, path) {
  return path.split('.').reduce((current, key) => isRecord(current) ? current[key] : undefined, value)
}

function setPath(target, path, value) {
  const parts = path.split('.')
  let cursor = target
  parts.slice(0, -1).forEach((part) => {
    if (!isRecord(cursor[part])) cursor[part] = {}
    cursor = cursor[part]
  })
  cursor[parts.at(-1)] = clone(value)
}

function mergeField(path, policy, sources) {
  const candidates = sources
    .map((source) => ({ source_id: source.source_id, value: getPath(source.data, path) }))
    .filter((candidate) => candidate.value !== undefined)
  if (candidates.length === 0) return { value: undefined, sources: [] }
  const strategy = policy?.strategy || 'reject-conflict'
  if (strategy === 'reject-conflict') {
    if (candidates.some((candidate) => !equal(candidate.value, candidates[0].value))) {
      throw new TypeError(`字段 ${path} 有冲突，必须声明合并策略`)
    }
    return { value: candidates[0].value, sources: candidates.map((candidate) => candidate.source_id) }
  }
  if (strategy === 'prefer-source') {
    const selected = candidates.find((candidate) => candidate.source_id === policy.source_id)
    if (!selected) throw new TypeError(`字段 ${path} 找不到优先来源 ${policy.source_id || ''}`)
    return { value: selected.value, sources: [selected.source_id] }
  }
  if (strategy === 'coalesce') return { value: candidates[0].value, sources: [candidates[0].source_id] }
  if (strategy === 'concat') {
    if (candidates.some((candidate) => !Array.isArray(candidate.value))) throw new TypeError(`字段 ${path} 的 concat 输入必须都是数组`)
    return { value: candidates.flatMap((candidate) => candidate.value), sources: candidates.map((candidate) => candidate.source_id) }
  }
  if (strategy === 'keyed-table') {
    const key = policy.key
    if (typeof key !== 'string' || !key) throw new TypeError(`字段 ${path} 的 keyed-table 必须声明 key`)
    if (candidates.some((candidate) => !Array.isArray(candidate.value))) throw new TypeError(`字段 ${path} 的 keyed-table 输入必须都是数组`)
    const rows = new Map()
    const rowSources = new Map()
    for (const candidate of candidates) {
      for (const row of candidate.value) {
        if (!isRecord(row) || row[key] === undefined) throw new TypeError(`字段 ${path} 的行缺少键 ${key}`)
        const id = String(row[key])
        if (rows.has(id) && !equal(rows.get(id), row) && policy.on_conflict !== 'last-wins') {
          throw new TypeError(`字段 ${path} 的键 ${id} 冲突`)
        }
        rows.set(id, clone(row))
        rowSources.set(id, candidate.source_id)
      }
    }
    return { value: [...rows.values()], sources: [...new Set(rowSources.values())], row_sources: Object.fromEntries(rowSources) }
  }
  throw new TypeError(`字段 ${path} 使用未知合并策略 ${strategy}`)
}

/** 按显式 recipe 合并精确版本 Material 数据；未声明的冲突一律拒绝。 */
export function mergeMaterialPayloads(inputSources, recipe) {
  if (!Array.isArray(inputSources) || inputSources.length < 2) throw new TypeError('合并至少需要两个来源')
  const sources = inputSources.map(exactSource)
  if (!isRecord(recipe) || !isRecord(recipe.fields)) throw new TypeError('merge recipe.fields 必填')
  if (new Set(sources.map((source) => source.source_id)).size !== sources.length) throw new TypeError('source_id 必须唯一')
  const data = {}
  const provenance = {}
  for (const [path, policy] of Object.entries(recipe.fields)) {
    if (!path.trim()) throw new TypeError('合并字段路径不能为空')
    const merged = mergeField(path, isRecord(policy) ? policy : {}, sources)
    if (merged.value === undefined) continue
    setPath(data, path, merged.value)
    provenance[path] = {
      strategy: policy?.strategy || 'reject-conflict',
      sources: merged.sources,
      ...(merged.row_sources ? { row_sources: merged.row_sources } : {}),
    }
  }
  return {
    data,
    provenance,
    source_revisions: Object.fromEntries(sources.map((source) => [source.source_id, clone(source.revision_ref)])),
    display: clone(isRecord(recipe.display) ? recipe.display : {}),
  }
}
