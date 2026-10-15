const primaryRepresentation = (material) =>
  material.representations?.find((item) => item.role === 'primary')
  ?? material.representations?.[0]
  ?? null

/**
 * 语义三元组：从材料上读出 (flow_role, artifact_kind, format)。
 * 两轴取自 material 顶层或 metadata（0.3.0 起为顶层字段，0.2.0 图仍写在 metadata）。
 */
function semanticsOf(material, representation) {
  const meta = material?.metadata ?? {}
  return {
    flowRole: material?.flow_role ?? meta.flow_role ?? null,
    artifactKind: material?.artifact_kind ?? meta.artifact_kind ?? null,
    // format_tag is the frontend contract; media_type remains transport metadata.
    format: representation?.format_tag ?? representation?.formatTag ?? representation?.media_type ?? null,
  }
}

/** 这个材料是否声明了语义。声明了就不允许在"无人接受"时静默退成 generic。 */
export function declaresSemantics(material) {
  const { flowRole, artifactKind } = semanticsOf(material, null)
  return Boolean(flowRole || artifactKind)
}

/** 给人看的语义描述，用于把"无人接受"这件事讲清楚，而不是变成一个安静的朴素渲染。 */
export function describeSemantics(material, representation) {
  const { flowRole, artifactKind, format } = semanticsOf(material, representation)
  return { flow_role: flowRole, artifact_kind: artifactKind, format }
}

/**
 * 消费者侧能力匹配：plugin 声明 acceptsFlowRoles / acceptsArtifactKinds / acceptsFormats。
 * 规则：**凡是 plugin 声明了的维度都必须命中**；未声明的维度视为不约束。
 * 三个维度都未声明的 plugin 不参与语义匹配（它只能靠 hint / mediaType / label 命中）。
 */
export function semanticCandidates(material, representation, plugins = []) {
  const { flowRole, artifactKind, format } = semanticsOf(material, representation)
  if (!flowRole && !artifactKind) return []
  const accepts = (list, value) => !Array.isArray(list) || list.length === 0
    ? null
    : Boolean(value) && list.includes(value)
  return plugins.filter((plugin) => {
    const checks = [
      accepts(plugin.acceptsFlowRoles, flowRole),
      accepts(plugin.acceptsArtifactKinds, artifactKind),
      accepts(plugin.acceptsFormatTags ?? plugin.acceptsFormats, format),
    ]
    if (checks.every((value) => value === null)) return false
    return checks.every((value) => value === null || value === true)
  })
}

/** Identity + body when no display plugin is loaded. Never invents write-back. */
export function genericDisplay(material) {
  const representation = primaryRepresentation(material)
  return {
    kind: 'generic',
    member_id: material.id,
    title: material.title,
    body: typeof representation?.content === 'string'
      ? representation.content
      : (material.summary ?? ''),
    media_type: representation?.media_type ?? 'text/plain',
    writable: false,
  }
}

export function resolveDisplayPlugin(material, plugins = []) {
  const context = arguments[2] ?? {}
  const representation = primaryRepresentation(material)
  const hint = representation?.renderer_hint
  const media = representation?.media_type
  const overrideId = (value) => {
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (!value || typeof value !== 'object') return null
    const id = value.pluginId ?? value.plugin_id
    return typeof id === 'string' && id.trim() ? id.trim() : null
  }
  const byId = (value) => {
    const id = overrideId(value)
    return id ? plugins.find((item) => item.id === id) ?? null : null
  }
  // 0.3.0 能力声明反转：先按**语义**匹配（消费者声明 accepts_*），renderer_hint 退为
  // "多个语义候选时的择一 override"，不再是主路径、也不得让不匹配的 handler 生效。
  // 这是"语义相同、格式不同的内容进同一条管线"的实现位（REQ-MATERIAL-CORE-0.3.0 §3.3）。
  const semantic = semanticCandidates(material, representation, plugins)
  const hintPick = hint ? semantic.find((item) => Array.isArray(item.hints) && item.hints.includes(hint)) : null
  const legacyLanes = !declaresSemantics(material)

  const candidates = [
    ['member-override', byId(context.memberOverride ?? context.member?.display)],
    ['group-override', byId(context.groupOverride ?? context.group?.display)],
    ['composition-override', byId(context.compositionOverride ?? context.composition?.display)],
    // 语义命中且被 hint 选中 → 这是 hint 唯一的合法用途
    ['semantic+hint', semantic.length > 1 ? hintPick : null],
    ['semantic', semantic[0] ?? null],
    // 以下三条是 0.2.0 的旧通道，**只对未声明语义的老材料开放**。
    // 一旦材料声明了语义，就必须走语义匹配：否则一个声明"我只接受 play-path"的 handler
    // 能靠 hint 抢到 real-screen 的材料，等于 hint 又变回主路径。
    ['renderer-hint', legacyLanes && plugins.find((item) => hint && Array.isArray(item.hints) && item.hints.includes(hint))],
    ['media-type', legacyLanes && plugins.find((item) => media && Array.isArray(item.mediaTypes) && item.mediaTypes.includes(media))],
    ['material-label', legacyLanes && plugins.find((item) => Array.isArray(item.labels)
      && item.labels.some((label) => material.labels?.includes(label)))],
  ]
  const matched = candidates.find(([, plugin]) => plugin)
  if (!matched) {
    // 声明了语义却没有任何 handler 接受它 —— 这是配置缺口，必须显性化。
    // 0.2.0 在这里静默退成 generic，于是"没人能渲染"和"渲染得很朴素"长得一模一样。
    if (declaresSemantics(material)) {
      return {
        ...genericDisplay(material),
        resolution: 'unhandled-semantics',
        unhandled: describeSemantics(material, representation),
      }
    }
    return { ...genericDisplay(material), resolution: 'generic-fallback' }
  }
  const [resolution, plugin] = matched
  return {
    kind: 'plugin',
    plugin_id: plugin.id,
    resolution,
    member_id: material.id,
    title: material.title,
    body: genericDisplay(material).body,
    media_type: media ?? 'application/octet-stream',
    writable: false,
  }
}

/** Built-in process plugins. Hosts may add more; View members stay independent of provider. */
export const MATERIAL_PROCESS_PLUGINS = [
  { id: 'material-wiki-io', provider: 'material-wiki', can_write: true },
  { id: 'git-io', provider: 'git', can_write: true },
  { id: 'p4-io', provider: 'p4', can_write: true },
  { id: 'material-note-io', provider: 'material-note', can_write: true },
]

export function resolveProcessPlugin(revisionRef, plugins = MATERIAL_PROCESS_PLUGINS) {
  const provider = revisionRef?.provider
  const plugin = plugins.find((item) => item.provider === provider || (Array.isArray(item.providers) && item.providers.includes(provider)))
  if (!plugin) {
    return { kind: 'generic', provider: provider ?? '', can_write: false }
  }
  return { kind: 'plugin', plugin_id: plugin.id, provider, can_write: Boolean(plugin.can_write) }
}

/** View membership must not depend on which process plugin stores the revision. */
export function viewMembersForProcess(_view, members) {
  return members.map((member) => ({
    material_id: member.material_id,
    order: member.order,
    group: member.group,
  }))
}

/**
 * Third plugin class (R3): external-platform sync. Local state stays the truth;
 * external platforms are projections. Bot-only identity, idempotent writes and
 * readback are inherited red lines (WindowsWorkspace AGENTS.md).
 */
export const MATERIAL_SYNC_PLUGINS = [
  { id: 'notify-omni', direction: 'push', events: ['gate.passed', 'run.finished'], can_write: true },
  { id: 'notify-lark', direction: 'push', events: ['gate.passed', 'run.finished'], can_write: true },
  { id: 'sync-feishu-project', direction: 'both', events: ['material.completed', 'gate.passed', 'run.finished'], can_write: true },
  { id: 'sync-meego', direction: 'both', events: ['run.finished'], can_write: true },
]

export function resolveSyncPlugin(material, plugins = MATERIAL_SYNC_PLUGINS) {
  let bindings = []
  try {
    const value = material?.metadata?.sync
    if (Array.isArray(value)) bindings = value
  } catch { bindings = [] }
  const matched = []
  for (const binding of bindings) {
    const plugin = plugins.find((item) => item.id === binding.plugin)
    if (plugin) matched.push({ plugin, binding })
  }
  return matched
}
