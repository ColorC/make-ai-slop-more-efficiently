// 后端枚举保持稳定以兼容存储与 API；本文件是 Dashboard 唯一的人读中文映射。
const STATUS_ZH: Record<string, string> = {
  provisional: '初步判断',
  unknown: '尚无法判断',
  inconclusive: '证据不足，暂无法判断',
  pass: '达到当前标准',
  fail: '未达到当前标准',
  mixed: '有好有坏',
  improving: '工作质量在改善',
  regressing: '工作质量在退化',
  not_comparable: '还没有可比较的前后样本',
  available: '已读取',
  empty: '现有来源中未发现',
  unavailable: '暂时无法读取',
  stale: '来源可能过时',
  conflict: '证据互相冲突',
  excluded: '按边界不采集',
  active: '进行中',
  observed: '已观测',
  archived: '已归档',
  pending: '等待处理',
  accepted: '已接受',
  rejected: '未接受',
  blocked: '受阻',
  completed: '已完成',
  ok: '正常完成',
  partial: '部分完成',
  failed: '失败',
  deterministic_only: '仅做规则检查',
  cold_start: '首次评价，等待后续证据',
  pending_external_validation: '等待后续独立证据复核',
  possible_consumption_signal: '发现可能的使用线索',
  evidence_gap: '暂未关联到后续证据',
  met: '正确性门槛已满足',
  not_met: '正确性门槛未满足',
  better: '在可比尺度上更好',
  worse: '在可比尺度上更差',
  stable: '在可比尺度上基本稳定',
  comparable: '可比较',
  partially_comparable: '部分维度可比较',
  requires_normalization: '需要分层或归一化',
  connected: '已接通',
  discovered_only: '只发现入口，尚未解析结果',
  missing: '尚未接通',
  invalid: '档案无效',
  recheck_against_new_evidence: '有新证据，等待重新评价',
  no_new_snapshot: '没有新的不同快照',
}

const SOURCE_CATEGORY_ZH: Record<string, string> = {
  identity_and_direction: '项目身份与方向',
  execution: '实际执行',
  decision_basis: '工作依据与决策',
  engineering_change: '工程改动',
  verification: '验证材料',
  actual_outcome: '实际结果',
  human_intervention: '人工介入',
  efficiency_and_cost: '时间与资源成本',
  authority_and_freshness: '权威来源与新鲜度',
  downstream_evidence: '后续使用与结果',
}

const WORK_TYPE_ZH: Record<string, string> = {
  'agent-runtime-and-human-attention': 'Agent 运行与人工注意力',
  'device-and-product-delivery': '设备与产品交付',
  'game-product-and-engineering': '游戏产品与工程',
  'long-running-exploration': '长期探索',
  'software-and-content-production': '软件与内容生产',
  'subjective-content-and-product': '主观内容与产品',
  'versioned-product-orchestration': '版本化产品协作',
  'general-product': '一般产品工作',
  'game-product': '游戏产品工作',
  'game-production': '游戏生产工作',
  'agent-platform': 'Agent 平台工作',
  unspecified: '尚未分类的工作',
}

const SUMMARY_ZH: Record<string, string> = {
  'No independent later evidence has calibrated a prior assessment yet.': '尚无后续独立证据可以复核这次评价。',
  'New evidence created a revision, but the evaluator cannot validate its own prior judgment.': '新证据触发了评价修订，但评价器不能自行证明此前判断是否正确。',
  'A textual target match is only a lead; independent outcome evidence is still required.': '目前只有文字目标相似这一条线索；观测中心仍需从既有真源中寻找独立结果。',
  'No current evidence can link this candidate to later consumption or outcome.': '观测中心暂时无法从既有真源中把这条候选与后续使用或结果关联起来。',
  'Project-linked session metadata': '与项目关联的会话元数据',
  'Available provider/model/token-budget metadata': '可读取的模型来源、模型和 token 预算元数据',
  'Native session reference matched by cwd or project/plan id': '按工作目录或项目/计划身份关联到的原始会话引用',
  'Declared outcome criteria; declaration is not completion proof': '计划声明的结果标准；声明本身不是完成证据',
  'Relevant authority file discovered; actual task consumption is not proven': '发现相关手册或技能；尚不能证明本次任务实际使用了它',
  'Project-local Team discovered; actual task consumption is not proven': '发现项目内 Team；尚不能证明本次任务实际使用了它',
  'Repository change and recent commit state': '仓库改动与近期提交状态',
  'Recent commits are downstream artifacts, not proof of quality': '近期提交是后续产物，不单独证明工作质量',
  'Human or review-stage outcome signal': '人工或审阅阶段的结果信号',
  'Manual verdict, comment, or explicit patch signal': '人工裁定、评论或明确补丁信号',
  'Recent verification-like artifact': '近期可能用于验证的产物',
  'Registry authority and freshness signals': '项目登记信息的权威性与新鲜度信号',
  'Project index exists; content retained by digest only': '项目索引存在；正文只保留摘要指纹，不复制内容',
}

export function statusLabelZh(value?: string | null, fallback = '未记录'): string {
  if (!value) return fallback
  return STATUS_ZH[value] || '未识别状态'
}

export function sourceCategoryLabelZh(value: string): string {
  return SOURCE_CATEGORY_ZH[value] || '其他证据来源'
}

export function workTypeLabelZh(value?: string | null): string {
  if (!value) return '尚未分类的工作'
  return WORK_TYPE_ZH[value] || '其他工作类型'
}

export function selectionReasonLabelZh(value: string): string {
  const activeDays = value.match(/^active_days_7d=(\d+)$/)
  if (activeDays) return `近 7 天活跃 ${activeDays[1]} 天`
  const planCount = value.match(/^plan_count=(\d+)$/)
  if (planCount) return `关联计划 ${planCount[1]} 项`
  const activeWithin = value.match(/^active_within_(\d+)d$/)
  if (activeWithin) return `${activeWithin[1]} 天内活跃`
  const usedWithin = value.match(/^used_within_(\d+)d$/)
  if (usedWithin) return `${usedWithin[1]} 天内使用过`
  return ({
    configured_initial_service: '核心服务组合',
    configured_initial_service_outside_activity_window: '核心组合，但近期活动较少',
    project_registry_pinned: '项目已固定关注',
    index_stale_but_activity_preserved: '索引需刷新，但仍有近期活动',
    registered_project: '已登记项目',
  } as Record<string, string>)[value] || '由当前组合策略选入'
}

export function humanTextZh(value?: string | null): string {
  if (!value) return ''
  const deterministic = value.match(
    /^Queried (\d+) evidence categories; (\d+) available and (\d+) unresolved\. Improvement remains unproven until comparable later evidence exists\.$/,
  )
  if (deterministic) {
    return `观测中心查询了 ${deterministic[1]} 类既有证据，当前能读取 ${deterministic[2]} 类，另有 ${deterministic[3]} 类暂未提取或关联。在出现可比较的后续真实工作前，不能判断工作质量是否改善。`
  }
  if (value.startsWith('Project registry identity for ')) {
    return `${value.slice('Project registry identity for '.length)} 的项目登记身份`
  }
  return SUMMARY_ZH[value] || value
}

export function projectScopeNoteZh(projectId: string): string {
  return ({
    lofa: '这里观察的是 LOFA 手机客户端的交付工作，不是要求 LOFA 为观测新增真机任务或记录。',
    'agent-office': '这里观察的是近期“版本工厂重建”这条工作，不是对整个 Agent Office 的总评价。早期候选和当前候选范围不同，不能直接拼成改善趋势。',
  } as Record<string, string>)[projectId] || ''
}
