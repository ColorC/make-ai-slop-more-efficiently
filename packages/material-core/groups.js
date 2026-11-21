/**
 * groups.js — material 组与组视图（core 0.3.0 §3.4）
 *
 * 业主的判断是本文件的设计前提：**组视图之间"本质上是一样的"，大部分字段完全挪动，
 * 只不过用自定义字段去表现空间关联或者顺序关联。**
 *
 * 所以这里**不设计两套结构**：
 *   - **组（group）** 持有身份、语义约束、以及**唯一的成员集**；
 *   - **组视图（group_view）** 只声明"关系怎么表达"（spatial=空间 / sequential=顺序），
 *     外加该模式下的自定义字段（坐标/尺寸 或 次序）。
 *
 * 由此得到一条可校验的不变量：**视图不得引入组里没有的成员**。
 * 当前 4 个玩法簇画布与 23 条线性流程条正是同一组的两种视图被做成了两套硬编码产物；
 * 统一后业务只声明一次成员，画布与流程条各自只贴自己的关系字段。
 *
 * core 内不出现任何项目专属值或 id。
 */

/** 关系表达模式。封闭值集：新增模式必须先改 core，不能由宿主自造。 */
export const GROUP_VIEW_RELATIONS = Object.freeze(['spatial', 'sequential'])

/** 每种关系模式允许的**视图局部**成员字段。视图只能加这些，不能改成员本身。 */
const RELATION_MEMBER_FIELDS = Object.freeze({
  spatial: Object.freeze(['x', 'y', 'width', 'height', 'edges']),
  sequential: Object.freeze(['order', 'branch']),
})

export function relationMemberFields(relation) {
  return RELATION_MEMBER_FIELDS[relation] ?? null
}

const asArray = (value) => (Array.isArray(value) ? value : [])
const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0

/**
 * 成员键。**不能只用 material_id**：一个组里常有同一张材料的多个 representation
 * （例如同一张界面卡的多个阶段截图），这正是契约 view_member 有 representation_id 的原因。
 * 第一版按 material_id 判唯一，被真实数据当场打回（GetLike 8 个阶段同属一张卡）。
 */
export function memberKey(member) {
  const id = typeof member === 'string' ? member : member?.material_id
  const rep = typeof member === 'string' ? null : member?.representation_id
  return rep ? `${id}#${rep}` : String(id)
}

/**
 * 解析组的成员集。两种来源：显式清单，或选择器（由宿主提供 resolveSelector 求值）。
 * **同一 material 可属多组**，因此这里不做全局唯一性假设。
 */
export function resolveGroupMembers(group, { resolveSelector } = {}) {
  if (Array.isArray(group?.members)) {
    return group.members.map((member) => (typeof member === 'string' ? { material_id: member } : member))
  }
  if (group?.selector && typeof resolveSelector === 'function') {
    return asArray(resolveSelector(group.selector)).map((member) => (
      typeof member === 'string' ? { material_id: member } : member
    ))
  }
  return []
}

/** 组自身的结构与语义约束校验。 */
export function validateGroup(group, { resolveSelector, resolveMaterialSemantics } = {}) {
  const problems = []
  if (!nonEmpty(group?.id)) problems.push('group.id 必须为非空字符串（英文 id）')
  if (!nonEmpty(group?.display_name)) problems.push('group.display_name 必须为非空字符串（中文显示名）')
  const hasList = Array.isArray(group?.members)
  const hasSelector = Boolean(group?.selector)
  if (!hasList && !hasSelector) problems.push('group 必须给出 members 显式清单或 selector 之一')
  if (hasList && hasSelector) problems.push('group 不得同时给出 members 与 selector（成员来源必须唯一）')

  const members = resolveGroupMembers(group, { resolveSelector })
  const seen = new Set()
  for (const member of members) {
    if (!nonEmpty(member?.material_id)) { problems.push('group 成员缺少 material_id'); continue }
    const key = memberKey(member)
    if (seen.has(key)) problems.push(`group 成员重复：${key}`)
    seen.add(key)
  }

  // 语义约束：声明了 member_kinds 就必须条条命中（未声明视为不约束）。
  const wantRoles = asArray(group?.member_kinds?.flow_roles)
  const wantKinds = asArray(group?.member_kinds?.artifact_kinds)
  if ((wantRoles.length || wantKinds.length) && typeof resolveMaterialSemantics === 'function') {
    for (const member of members) {
      const semantics = resolveMaterialSemantics(member.material_id) ?? {}
      if (wantRoles.length && !wantRoles.includes(semantics.flow_role)) {
        problems.push(
          `成员 ${member.material_id} 的 flow_role "${semantics.flow_role}" 不在组声明的 `
          + `${wantRoles.join(' / ')} 内`,
        )
      }
      if (wantKinds.length && !wantKinds.includes(semantics.artifact_kind)) {
        problems.push(
          `成员 ${member.material_id} 的 artifact_kind "${semantics.artifact_kind}" 不在组声明的 `
          + `${wantKinds.join(' / ')} 内`,
        )
      }
    }
  }
  return problems
}

/**
 * 组视图校验。核心不变量：**视图不得引入组里没有的成员**，且视图局部字段只能是该关系模式
 * 允许的那几个 —— 这就是"大部分字段完全挪动、只用自定义字段表达空间/顺序关联"的可执行形式。
 */
export function validateGroupView(view, group, { resolveSelector } = {}) {
  const problems = []
  if (!nonEmpty(view?.id)) problems.push('group_view.id 必须为非空字符串（英文 id）')
  if (!nonEmpty(view?.display_name)) problems.push('group_view.display_name 必须为非空字符串（中文显示名）')
  if (!GROUP_VIEW_RELATIONS.includes(view?.relation)) {
    problems.push(`group_view.relation 必须是 ${GROUP_VIEW_RELATIONS.join(' / ')} 之一（值集封闭）`)
    return problems
  }
  if (nonEmpty(view?.group_id) && nonEmpty(group?.id) && view.group_id !== group.id) {
    problems.push(`group_view.group_id "${view.group_id}" 与所绑定的组 "${group.id}" 不一致`)
  }

  // 视图局部字段按**成员键**索引（material_id 或 material_id#representation_id），
  // 否则同一张卡的多个阶段无法各自贴坐标/次序。
  const allowed = new Set(resolveGroupMembers(group, { resolveSelector }).map(memberKey))
  const fields = new Set(relationMemberFields(view.relation))

  for (const [key, local] of Object.entries(view?.members ?? {})) {
    if (!allowed.has(key)) {
      problems.push(
        `group_view 引入了组里没有的成员 ${key} —— 成员集只由组决定，视图只能贴关系字段`,
      )
      continue
    }
    for (const field of Object.keys(local ?? {})) {
      if (!fields.has(field)) {
        problems.push(
          `group_view 成员 ${key} 的字段 "${field}" 不属于 relation=${view.relation} 允许的 `
          + `${[...fields].join(' / ')}`,
        )
      }
    }
  }
  return problems
}

/**
 * 把组 + 视图投影成渲染端要的成员序列。
 * spatial 保留坐标；sequential 按 order 排序（缺 order 的排在后面，稳定不抖）。
 * **两种模式共用同一份成员**，差异只在这里体现 —— 不复制成员、不各存一套。
 */
export function projectGroupView(group, view, { resolveSelector } = {}) {
  const members = resolveGroupMembers(group, { resolveSelector })
  const local = view?.members ?? {}
  const projected = members.map((member) => ({
    ...member,
    view_local: local[memberKey(member)] ?? {},
  }))
  if (view?.relation === 'sequential') {
    return projected
      .map((member, index) => ({ member, index }))
      .sort((left, right) => {
        const lo = left.member.view_local.order
        const ro = right.member.view_local.order
        if (typeof lo === 'number' && typeof ro === 'number' && lo !== ro) return lo - ro
        if (typeof lo === 'number' && typeof ro !== 'number') return -1
        if (typeof lo !== 'number' && typeof ro === 'number') return 1
        return left.index - right.index
      })
      .map((entry) => entry.member)
  }
  return projected
}
