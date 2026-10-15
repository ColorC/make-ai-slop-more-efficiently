/**
 * kinds.js — 语义类型注册表的唯一裁决入口（core 0.3.0）
 *
 * 权威链：core 契约 > 域声明（只能在 core 形状内选值/注册值）> provider / 代码（只消费不定义）。
 * **未声明即拒绝，不做兜底** —— 这是 0.2.0 与 0.3.0 的核心差别：0.2.0 在沉默处退化，
 * 于是代码字面量（real-screen 之类）与域声明（formal_artifact_kinds）并存成两套词汇。
 *
 * 因此本模块只导出「查询 + 校验」，不导出任何可让调用方绕过校验的松散判断。
 * core 内不出现任何项目专属值或项目专属 id（判据：core 目录下 grep 项目前缀应为空）。
 */
import { createRequire } from 'node:module'

const require_ = createRequire(import.meta.url)
/** @type {any} */
const REGISTRY = require_('./contract/kinds.json')

export const KIND_REGISTRY = Object.freeze(REGISTRY)
export const KIND_REGISTRY_VERSION = REGISTRY.registry_version

/** 流程角色是封闭值集（跨项目通用）。 */
export const FLOW_ROLES = Object.freeze(Object.keys(REGISTRY.axes.flow_role.values))
export const CONTENT_CLASSES = Object.freeze([...REGISTRY.axes.artifact_kind.content_class_enum])
export const VISIBILITY_VALUES = Object.freeze([...REGISTRY.visibility.values])
export const AUTHORITY_VALUES = Object.freeze([...REGISTRY.authority.values])

const flowRole = (role) => REGISTRY.axes.flow_role.values[role] ?? null

/** 取某个流程角色的声明；未声明返回 null（调用方必须当作拒绝，不要当作"随便"）。 */
export function flowRoleSpec(role) {
  return flowRole(role)
}

/** 该流程角色要求哪些语义断言。 */
export function requiredAssertions(role) {
  const spec = flowRole(role)
  return spec ? Object.freeze([...spec.requires]) : null
}

/**
 * 校验一个 artifact_kind 的**注册**（域文件侧）。
 * 域只能注册符合 value_pattern 且声明了 content_class / project_scope 的值。
 */
export function validateArtifactKindRegistration(entry) {
  const problems = []
  const axis = REGISTRY.axes.artifact_kind
  const value = typeof entry?.kind === 'string' ? entry.kind : ''
  if (!new RegExp(axis.value_pattern).test(value)) {
    problems.push(`artifact_kind "${value}" 不符合 value_pattern ${axis.value_pattern}`)
  }
  for (const field of axis.registration_requires) {
    if (!entry?.[field]) problems.push(`artifact_kind "${value}" 缺少注册必填项 ${field}`)
  }
  if (entry?.content_class && !CONTENT_CLASSES.includes(entry.content_class)) {
    problems.push(`content_class "${entry.content_class}" 不在 core 声明的枚举内：${CONTENT_CLASSES.join(' / ')}`)
  }
  return problems
}

/**
 * 校验 (flow_role, artifact_kind) 组合是否合法。
 * 合法性由 core 判定：flow_role 声明它接受哪些 content_class，artifact_kind 注册时声明自己的
 * content_class —— 于是 core 不需要知道任何项目专属值就能裁决组合。
 */
export function validateKindPair({ flow_role: role, artifact_kind: kind, content_class: contentClass }) {
  const problems = []
  const spec = flowRole(role)
  if (!spec) {
    problems.push(`flow_role "${role}" 未在 core 注册表声明；flow_role 是封闭值集（${FLOW_ROLES.join(' / ')}），不允许新增`)
    return problems
  }
  if (!kind) {
    problems.push(`flow_role "${role}" 必须同时声明 artifact_kind（两轴正交，缺一不可）`)
    return problems
  }
  if (!contentClass) {
    problems.push(`artifact_kind "${kind}" 未提供 content_class，无法裁决与 flow_role "${role}" 的组合`)
    return problems
  }
  if (!spec.content_classes.includes(contentClass)) {
    problems.push(
      `组合非法：flow_role "${role}" 只接受 content_class ${spec.content_classes.join(' / ')}，`
      + `而 artifact_kind "${kind}" 是 "${contentClass}"`,
    )
  }
  return problems
}

/** 命名规则：id 必须 ASCII 且符合字符集；display_name 必须含中日韩汉字；禁用词不得出现在面向用户文案。 */
export function validateNaming({ id, display_name: displayName, summary } = {}) {
  const problems = []
  const naming = REGISTRY.naming
  if (typeof id !== 'string' || !new RegExp(naming.id_charset).test(id)) {
    problems.push(`id "${id}" 不符合 id_charset ${naming.id_charset}`)
  } else if (naming.id_must_be_ascii && /[^\x00-\x7F]/.test(id)) {
    problems.push(`id "${id}" 必须为 ASCII（显示名用中文，id 用英文）`)
  }
  if (naming.display_name_requires_cjk) {
    const cjk = typeof displayName === 'string' ? (displayName.match(/[一-鿿]/g) ?? []).length : 0
    // 只要求"有实义中文描述"，不禁止名字里出现业务英文标识（如"系统面板玩法文档：Setting / Mail"
    // 是业主接受过的形态）。因此判据是中文字数下限，而不是"不含 ASCII"。
    // 已知局限：`SomeView · 界面` 这类以英文标识为主体、中文只作后缀的写法本检查拦不住，
    // 实践中由 reserved_words 与人工审读兜住，不要在这里做启发式猜测。
    if (cjk < naming.display_name_min_cjk) {
      problems.push(
        `display_name "${displayName}" 中文字符不足 ${naming.display_name_min_cjk} 个`
        + `（业主裁定：起名一律全中文，id 用英文）`,
      )
    }
  }
  for (const word of naming.reserved_words) {
    for (const [field, text] of [['display_name', displayName], ['summary', summary]]) {
      if (typeof text === 'string' && text.includes(word)) {
        problems.push(`${field} 含内部实现术语 "${word}"，不得出现在面向用户的文案里`)
      }
    }
  }
  return problems
}

/** 可见性与"禁止新增顶层"。默认 hidden；指向 home 的顶层需要显式人工批准位。 */
export function validateVisibility({ visibility, navigation } = {}, { homeViewId } = {}) {
  const problems = []
  const policy = REGISTRY.visibility
  const value = visibility ?? policy.default
  if (!policy.values.includes(value)) {
    problems.push(`visibility "${value}" 不在枚举内：${policy.values.join(' / ')}`)
  }
  // 宿主的 home view id 由调用方传入；core 不内置项目专属 id。未传则跳过顶层检查
  // （宿主没有声明 home，就不存在"新增顶层"这件事）。
  const parent = navigation?.parent_view_id
  if (homeViewId && parent && parent === homeViewId) {
    const approvedBy = navigation?.top_level_approved_by
    if (!approvedBy) {
      problems.push(
        `拒绝新增顶层：parent_view_id 指向 ${parent} 需要显式人工批准位 `
        + `${policy.top_level_policy.requires_manual_approval_field}`,
      )
    }
  }
  return problems
}

/** 投影类只读：authority=projection 的材料不得声明可写。 */
export function validateAuthority({ authority, writable } = {}) {
  const problems = []
  const value = authority ?? REGISTRY.authority.default
  if (!REGISTRY.authority.values.includes(value)) {
    problems.push(`authority "${value}" 不在枚举内：${REGISTRY.authority.values.join(' / ')}`)
  }
  if (value === 'projection' && REGISTRY.authority.projection_is_read_only && writable) {
    problems.push('authority=projection 的材料禁止写回（规范出现两份等于没有规范）')
  }
  return problems
}

/** 机采/可重生产物：身份绑生成器与输入指纹，不绑文件字节。 */
export function validateDerivation(derivation) {
  if (!derivation) return []
  const problems = []
  for (const field of REGISTRY.derivation.required_fields) {
    if (!derivation[field]) problems.push(`derivation 缺少必填项 ${field}`)
  }
  return problems
}

/**
 * 单个材料的语义校验总入口。返回 problems 数组；**非空即拒绝注册**，调用方不得降级为警告。
 * `resolveContentClass` 由宿主提供：给定 artifact_kind 返回其注册时声明的 content_class
 * （core 不内置项目专属值，所以必须由域注册表回答）。
 */
export function validateMaterialSemantics(material, { resolveContentClass, homeViewId } = {}) {
  const problems = []
  const meta = material?.metadata ?? {}
  const role = meta.flow_role
  const kind = meta.artifact_kind

  if (role || kind) {
    const contentClass = kind && typeof resolveContentClass === 'function' ? resolveContentClass(kind) : undefined
    problems.push(...validateKindPair({ flow_role: role, artifact_kind: kind, content_class: contentClass }))
    for (const assertion of requiredAssertions(role) ?? []) {
      if (assertion === 'evidence') {
        const ok = (material?.representations ?? []).some((rep) => rep?.source_ref)
        if (!ok) problems.push(`flow_role "${role}" 要求 evidence：至少一个 representation 带可打开的 source_ref`)
      }
      if (assertion === 'revision') {
        const rev = material?.revision_ref
        if (!rev?.provider || !rev?.object || !rev?.revision) {
          problems.push(`flow_role "${role}" 要求 revision：revision_ref 必须 provider/object/revision 三段齐备`)
        }
      }
      if (assertion === 'identity_fields') {
        if (!meta.identity && !meta.interface_id) {
          problems.push(`flow_role "${role}" 要求 identity_fields：metadata 需给出可唯一确定被描述对象的身份字段`)
        }
      }
    }
  }

  problems.push(...validateNaming({
    id: material?.id,
    display_name: material?.display_name ?? material?.title,
    summary: material?.summary,
  }))
  problems.push(...validateVisibility({ visibility: meta.visibility, navigation: meta.navigation }, { homeViewId }))
  problems.push(...validateAuthority({ authority: meta.authority, writable: meta.writable }))
  problems.push(...validateDerivation(meta.derivation))
  return problems
}
