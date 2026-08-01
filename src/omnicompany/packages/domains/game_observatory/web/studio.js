const workspace = document.querySelector('[data-testid="game-observatory-studio"]')

const state = {
  health: null,
  specs: [],
  partialBundles: [],
  runs: [],
  targets: [],
  leases: [],
  snapshots: [],
  voices: [],
  ledgers: [],
  currentRun: null,
  currentStep: null,
}

function aiPlayerPhysicalReadinessStatus(gate) {
  if (!gate) return '等待启动基准'
  if (gate.physical_play_unlocked) return gate.status === 'bypassed' ? '测试环境已放行' : '启动基准已通过'
  return '实体动作已锁止'
}

function aiPlayerPhysicalReadinessReason(gate) {
  if (!gate) return '尚未取得实体操作启动基准的核验结果。'
  if (gate.status === 'ready') return 'AFK 已知界面基础操作基准已通过。'
  if (gate.status === 'bypassed') return '当前为显式测试环境，启动基准已跳过。'
  return 'AFK 已知界面基础操作基准尚未通过，正式游戏保持锁止。'
}

function aiPlayerIterationStatus(value) {
  return ({
    passed: '通过', failed: '未通过', insufficient_data: '样本不足', not_evaluated: '尚未评估',
    confirmed: '预期变化成立', rejected: '预检拒绝', no_effect: '没有效果',
    blocked_by_overlay: '被遮罩阻挡', wrong_target: '目标错误', unsettled: '画面未稳定',
  })[value] || aiPlayerStatus(value)
}

function aiPlayerIterationMetric(value) {
  return ({
    sample_count: '样本', executed_count: '已执行', policy_violation_count: '越界',
    invalid_target_execution_count: '无效目标下发', incomplete_evidence_count: '证据缺失',
    expected_change_match_rate: '预期变化命中率', token_telemetry_coverage_rate: 'token 计量覆盖',
    latency_telemetry_coverage_rate: '时延计量覆盖', skill_token_reduction_rate: '技能 token 降幅',
    skill_latency_reduction_rate: '技能时延降幅', no_effect_rate: '无效果率',
    repeated_target_rate: '重复目标率', spin_cluster_count: '空转动作簇',
    meaningful_action_rate: '有意义动作率', false_empty_task_queue_count: '误判无任务',
    recovery_success_rate: '恢复成功率', favorable_account_or_objective_metric_count: '正向指标',
    objective_completed_count: '完成目标', task_progress_action_count: '推进任务的动作',
    new_canonical_content_count: '新记录内容', information_gain_units: '信息增量',
    frontier_exhausted: '前沿已穷尽',
  })[value] || value
}

function aiPlayerSoftSignal(value) {
  return ({
    tutorial_comprehension: '理解引导', intent_coherence: '目标连贯',
    opportunity_awareness: '机会敏感', strategic_continuity: '经营连续',
    curiosity_quality: '好奇心质量', loop_avoidance: '避免空转',
    player_naturalness: '玩家行为自然度',
  })[value] || value
}

function aiPlayerDecisionMode(value) {
  return ({ new_state: '新界面判断', known_state: '已知界面', skill_replay: '技能重放', recovery: '中断恢复' })[value] || value
}

function aiPlayerMemoryKind(value) {
  return ({
    identity_environment: '身份与环境', working: '工作记忆', episodic: '游玩经历',
    semantic: '规则与事实', procedural: '操作方法', task: '任务记忆',
    failure_forbidden: '注意事项与纠错记录',
  })[value] || '其他记忆'
}

function aiPlayerGameName(value) {
  return ({
    'sanguo-mouding-tianxia': '三国：谋定天下', 'afk-journey': '剑与远征：启程',
    minecraft: 'Minecraft',
  })[value] || '未命名游戏'
}

function aiPlayerChannel(value) {
  return ({ bilibili: '哔哩哔哩', fixture: '测试环境', official: '官方渠道' })[value] || '未标注渠道'
}

function aiPlayerAction(value) {
  return ({ tap: '点击', swipe: '滑动', wait: '等待', back: '返回', pinch: '双指缩放', two_finger_swipe: '双指滑动' })[value] || '其他操作'
}

function aiPlayerExecutor(value) {
  return ({ normalized_actions: '标准设备动作', maa: 'MAA 自动化', airtest: 'Airtest 自动化', mineflayer: 'Minecraft 自动化', specialized_adapter: '专用适配器' })[value] || '未标注执行器'
}

function aiPlayerSkillLayer(value) {
  return ({ atomic: '原子技能', flow: '流程技能', strategy: '策略技能' })[value] || '未标注层级'
}

function aiPlayerSkillScope(value) {
  return ({ interaction: '单次交互', surface: '界面流程', gameplay: '玩法流程', cross_game: '跨游戏能力' })[value] || '未标注范围'
}

function aiPlayerSkillSafety(value) {
  return ({ read_only: '只读', reversible: '可恢复', progression: '会推进进度', social: '涉及社交', economic: '涉及游戏经济', restricted: '需要单独授权' })[value] || '未标注安全级别'
}

function aiPlayerBlockerKind(value) {
  return ({ task: '任务阻断', pending_action: '动作待确认' })[value] || '阻断'
}

function aiPlayerSource(value) {
  return ({
    user_goal: '用户目标', unknown_interaction: '未知交互', missing_transition: '缺失转移',
    stale_memory: '陈旧记忆', interface_family_gap: '界面族缺口', new_unlock: '新解锁',
    guide_update: '攻略更新', failed_skill: '技能失败', gameplay_candidate: '玩法候选',
    coverage_gap: '覆盖缺口',
  })[value] || value || '未分类'
}

function aiPlayerOutcome(value) {
  return ({
    verified_transition: '已验证转移', verified_state_change: '已验证变化',
    verified_no_change: '已验证无变化', failed: '失败', forbidden: '禁止', deferred: '待处理',
  })[value] || value || '未知'
}

function aiPlayerCountRows(values, labeler = (value) => value) {
  const entries = Object.entries(values || {})
  if (!entries.length) return '<span class="ai-empty-inline">暂无</span>'
  return entries.map(([key, count]) => `<span><b>${esc(labeler(key))}</b>${count}</span>`).join('')
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    const apiDetail = detail.detail
    const message = typeof apiDetail === 'string'
      ? apiDetail
      : (apiDetail?.message || detail.message || `${url} · HTTP ${response.status}`)
    throw new Error(message)
  }
  return response.json()
}

function showToast(message, kind = 'info') {
  document.querySelector('.studio-toast')?.remove()
  const toast = document.createElement('div')
  toast.className = `studio-toast is-${kind}`
  toast.textContent = message
  document.body.append(toast)
  setTimeout(() => toast.remove(), 4200)
}

const excludedWorkspaceReportIds = new Set([
  'report.afk-journey.hero-upgrade.v1',
])

function usableSpecs(values) {
  return (values || []).filter((item) => (
    item.report?.design_spec?.contract_version === 'reverse-engineered-game-design-spec.v0.3'
    && !excludedWorkspaceReportIds.has(item.report.id)
  ))
}

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[character]))

function playTagMarkup(tags, details = []) {
  const detailByTag = new Map((details || []).map((item) => [item.tag, item]))
  return (tags || []).map((tag) => {
    const detail = detailByTag.get(tag)
    const explanation = detail ? `${detail.facet}：${detail.description}` : ''
    return `<span${explanation ? ` title="${esc(explanation)}"` : ''}>${esc(tag)}</span>`
  }).join('')
}

const compactDate = (value) => value ? String(value).replace('T', ' ').slice(0, 19) : '—'
const humanStatus = (value) => ({
  running: '记录中', paused: '已暂停', passed: '已通过', failed: '失败', stopped: '已停止',
  idle: '无租约',
  partial: '部分有效', complete: '已完成', pending: '待处理',
  published: '公开', review: '待审', draft: '草稿', online: '在线', offline: '离线',
}[value] || value || '未知')

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `${url} · HTTP ${response.status}`)
  }
  return response.json()
}

async function safeJson(url, fallback) {
  try { return await getJson(url) } catch (error) {
    console.error(error)
    return fallback
  }
}

function surfaceName() {
  const path = location.pathname.replace(/\/$/, '')
  if (path.endsWith('/console')) return 'console'
  const match = path.match(/\/studio\/([^/]+)$/)
  return match?.[1] || 'overview'
}

function markNavigation(surface) {
  const navigationSurface = ['game', 'play', 'demo'].includes(surface) ? 'overview' : surface
  document.querySelectorAll('[data-surface]').forEach((item) => {
    item.classList.toggle('is-active', item.dataset.surface === navigationSurface)
  })
}

function head(kicker, title, copy, phase = 'Gate 3 · 设施预览') {
  return `<header class="page-head">
    <div><p class="page-kicker">${esc(kicker)}</p><h1>${esc(title)}</h1><p>${esc(copy)}</p></div>
    ${phase ? `<div class="phase-chip"><span>当前边界</span><strong>${esc(phase)}</strong></div>` : ''}
  </header>`
}

function status(value) {
  return `<span class="status is-${esc(value)}">${esc(humanStatus(value))}</span>`
}

function runLabel(run) {
  const scope = run.scope_id || run.game_id || '未命名范围'
  return `${scope} · ${run.step_count ?? run.step_ids?.length ?? 0} 步`
}

const verdictLabels = {
  valid: '人工确认',
  mislabelled: '原标签错误',
  invalid_context: '上下文错误',
  facility_failure: '设施失败',
  verified_no_change: '确认无变化',
  needs_review: '需要复核',
}

function evidenceRecorderStatus(step) {
  return step.status === 'passed' ? '证据已落盘' : `录制${humanStatus(step.status)}`
}

function effectiveTargetName(step, adjudication) {
  return adjudication?.corrected_target_name || step.target_name || step.action?.type
}

function adjudicationMarkup(adjudication) {
  if (!adjudication) return ''
  const stateTransition = [adjudication.actual_from_state, adjudication.actual_to_state]
    .filter(Boolean)
    .join(' → ')
  return `<aside class="adjudication is-${esc(adjudication.verdict)}">
    <strong>${esc(verdictLabels[adjudication.verdict] || adjudication.verdict)}</strong>
    ${stateTransition ? `<span>${esc(stateTransition)}</span>` : ''}
    ${adjudication.note ? `<p>${esc(adjudication.note)}</p>` : ''}
  </aside>`
}

function preferredRun(runs) {
  const requested = new URLSearchParams(location.search).get('run')
  return runs.find((item) => item.id === requested)
    || runs.find((item) => item.status === 'running' && item.game_id === 'afk-journey')
    || runs.find((item) => item.game_id === 'afk-journey')
    || runs[0]
}

async function loadCommon() {
  const health = await safeJson('/api/game-observatory/health', { counts: {}, content: {} })
  state.health = health
}

async function renderOverview() {
  const partialPayload = await safeJson(
    '/api/game-observatory/workspace/partial-fact-bundles',
    { bundles: [] },
  )
  state.partialBundles = partialPayload.bundles || []
  const games = new Map()
  state.partialBundles.forEach((bundle) => {
    const gameId = bundle.game_id || 'unknown-game'
    const game = games.get(gameId) || {
      id: gameId,
      slug: bundle.game_slug || gameId,
      title: bundle.game_title || gameId,
      localizedTitle: readerGameTitle(bundle),
      summary: readerGameSummary(bundle),
      tags: bundle.game_tags || [],
      entries: [],
      coverArtifactId: bundle.cover_artifact_id || '',
    }
    game.entries.push(bundle)
    if (!game.coverArtifactId && bundle.cover_artifact_id) game.coverArtifactId = bundle.cover_artifact_id
    games.set(gameId, game)
  })
  workspace.innerHTML = `${head(
    '游戏设计资料库',
    '先看系统，再进入具体机制与操作',
    '选择一款游戏，先认识真正的系统范围，再进入其中的机制、操作、界面和状态说明。',
    '',
  )}
  <form class="library-search" action="/game-observatory/studio/search"><label><span>搜索游戏设计资料</span><input type="search" name="q" placeholder="系统、机制、操作、界面或规则"></label><button class="button is-primary" type="submit">搜索</button></form>
  <section class="game-library">${[...games.values()].map((game) => {
    const published = game.entries.filter((entry) => entry.publication_ready)
    const positions = published.map((entry) => readerView(entry).position)
    const systemCount = new Set(positions
      .filter((position) => position.group?.kind === 'system')
      .map((position) => position.group.id)).size
    const journeyCount = positions.filter((position) => position.level === 'journey').length
    const loopCount = positions.filter((position) => position.level === 'play_loop').length
    const counts = [
      systemCount ? `${systemCount} 个系统` : '',
      journeyCount ? `${journeyCount} 条入门路线` : '',
      loopCount ? `${loopCount} 个玩法循环` : '',
    ].filter(Boolean).join(' · ') || `${published.length} 篇设计资料`
    return `<a class="game-card" href="/game-observatory/studio/game?game=${encodeURIComponent(game.slug)}">
    <div class="game-card-cover">${game.coverArtifactId ? `<img src="/api/game-observatory/artifacts/${encodeURIComponent(game.coverArtifactId)}" alt="${esc(game.localizedTitle || game.title)} 游戏画面" loading="eager" decoding="async">` : ''}</div>
    <div class="game-card-copy"><span>游戏</span><h2>${esc(game.localizedTitle || game.title)}</h2><p>${esc(game.summary)}</p><div class="tag-row">${game.tags.map((tag) => `<span>${esc(tag)}</span>`).join('')}</div><strong>${esc(counts)}</strong></div>
  </a>`
  }).join('') || '<div class="empty-state"><strong>当前还没有可浏览的游戏设计资料</strong></div>'}</section>`
}

async function partialWorkspacePayload() {
  const payload = await safeJson('/api/game-observatory/workspace/partial-fact-bundles', { bundles: [] })
  state.partialBundles = payload.bundles || []
  return state.partialBundles
}

function selectedPartialBundle(bundles, gameSlug, playSlug = '') {
  if (!playSlug) {
    return bundles.find((item) => (
      (item.game_slug === gameSlug || item.game_id === gameSlug)
      && readerVisible(item)
    ))
  }
  return bundles.find((item) => (
    (item.game_slug === gameSlug || item.game_id === gameSlug)
    && (item.play_slug === playSlug || item.play_id === playSlug)
  ))
}

function readerVisible(item) {
  return item?.publication_ready === true
    && (item?.reader_visibility || 'public') === 'public'
}

function readerView(summary) {
  const reader = summary?.reader || {}
  const position = reader.position || summary?.reader_position || {
    level: summary?.content_kind === 'journey' ? 'journey' : 'unclassified',
    label: summary?.content_kind === 'journey' ? '入门路线' : '待归类内容',
    order: 0,
    group: {},
  }
  return {
    title: reader.title || summary?.play_title || '游戏设计内容',
    summary: reader.summary || '介绍这项内容的玩家目标、使用流程、关键概念和主要规则。',
    player_goal: reader.player_goal || '',
    steps: reader.steps || [],
    concepts: reader.concepts || [],
    rules: reader.rules || [],
    connections: reader.connections || [],
    kind: reader.kind || position.label,
    position,
  }
}

function readerGroup(summary) {
  const reader = readerView(summary)
  return reader.position.group || {}
}

function readerGroupHref(summary) {
  const group = readerGroup(summary)
  if (!summary?.game_slug || !group.slug) return ''
  return `/game-observatory/studio/group?game=${encodeURIComponent(summary.game_slug)}&group=${encodeURIComponent(group.slug)}`
}

function readerGroupAnchor(group) {
  return `group-${String(group?.slug || group?.id || 'unclassified').replace(/[^a-z0-9_-]+/gi, '-')}`
}

function groupEntries(entries) {
  const groups = new Map()
  entries.forEach((entry) => {
    const position = readerView(entry).position
    const group = position.group || {}
    if (!group.id) return
    const bucket = groups.get(group.id) || { group, entries: [] }
    bucket.entries.push(entry)
    groups.set(group.id, bucket)
  })
  return [...groups.values()]
    .map((bucket) => ({
      ...bucket,
      entries: bucket.entries.sort((left, right) => {
        const leftPosition = readerView(left).position
        const rightPosition = readerView(right).position
        return (leftPosition.order || 0) - (rightPosition.order || 0)
          || readerTitle(left).localeCompare(readerTitle(right), 'zh-CN')
      }),
    }))
    .sort((left, right) => (
      (left.group.order || 0) - (right.group.order || 0)
      || String(left.group.title || '').localeCompare(String(right.group.title || ''), 'zh-CN')
    ))
}

function readerGoalLabel(level) {
  return {
    system: '这个系统帮助玩家解决什么',
    system_overview: '这篇总览帮助玩家理解什么',
    journey: '沿着这条路线要完成什么',
    play_loop: '这个玩法循环要解决什么',
    mechanism: '这项机制怎样影响玩家',
    operation: '玩家执行这个操作要得到什么',
    interface: '玩家来到这个界面要看懂什么',
    state_reference: '这些状态与规则决定什么',
  }[level] || '这篇内容帮助玩家理解什么'
}

function readerArticleBreadcrumb(summary) {
  const gameHref = `/game-observatory/studio/game?game=${encodeURIComponent(summary.game_slug)}`
  const group = readerGroup(summary)
  const groupHref = readerGroupHref(summary)
  const position = readerView(summary).position
  const groupCrumb = groupHref && position.level !== 'system'
    ? `<b>→</b><a href="${groupHref}">${esc(group.title)}</a>`
    : ''
  return `<nav class="content-breadcrumb"><a href="/game-observatory/studio/">游戏</a><b>→</b><a href="${gameHref}">${esc(readerGameTitle(summary))}</a>${groupCrumb}<b>→</b><span>${esc(readerTitle(summary))}</span></nav>`
}

function readerTitle(summary) {
  return readerView(summary).title
}

function readerGameTitle(summary) {
  return summary?.reader_game?.title || summary?.game_localized_title || summary?.game_title || '游戏'
}

function readerGameSummary(summary) {
  return summary?.reader_game?.summary || `查看《${readerGameTitle(summary)}》的系统、规则、界面和相关玩法。`
}

function readerFacingCopy(value, fallback = '') {
  const internalPattern = /\b(?:EvidenceRun|Agent|candidate|candidate_replay|learn|warm_route|partial|coverage)\b/i
  const cleanParts = String(value || '').split(/[；;]/).map((part) => part.trim())
    .filter((part) => part && !internalPattern.test(part))
  return cleanParts.join('；') || fallback
}

const readerDesignInternalPattern = /(?:\b(?:EvidenceRun|EvidenceStep|OperationMemory|Agent|Session|worker|provider|model|candidate|candidate_replay|learn|warm_route|route|partial|coverage|artifact|run_id|token|revision|passed|failed)\b|证据|待证|取证|采集过程|发布门|完整度|置信度|安全返回|首访|二访|三访|上下文复用|复用路线|已学路线|路线固化|本次会话|当前会话|本轮运行|内部记录|内部字段|内部\s*ID|当前实例|重跑)/i

function readerDesignCopy(value, fallback = '') {
  const cleanParts = String(value || '').replace(/(\d+)\s*→\s*(\d+)/g, '$1 变为 $2')
    .split(/[；;]/).map((part) => part.trim())
    .filter((part) => part && !readerDesignInternalPattern.test(part))
  return cleanParts.join('；') || fallback
}

function readerDesignItems(items) {
  return (items || []).map((item) => readerDesignCopy(item)).filter(Boolean)
}

function readerDesignParagraphs(items) {
  return readerDesignItems(items).map((item) => `<p>${esc(item)}</p>`).join('')
}

function readerDesignListParagraph(label, items) {
  const cleanItems = readerDesignItems(items)
  return cleanItems.length ? `${label}${cleanItems.join('；')}。` : ''
}

function readerStoryAnchor(value, index = 0) {
  const clean = String(value || `section-${index + 1}`).toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
  return `story-${clean || `section-${index + 1}`}`
}

function readerStoryConceptAnchor(concept, index = 0) {
  const raw = String(concept?.id || concept?.name || `concept-${index + 1}`)
  const clean = raw.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
  return `story-concept-${clean || index + 1}`
}

function readerStoryInlineMarkup(value, concepts = []) {
  const source = String(value || '')
  const candidates = concepts.map((concept, index) => ({
    name: readerDesignCopy(concept?.name),
    anchor: readerStoryConceptAnchor(concept, index),
  })).filter((concept) => concept.name.length >= 2).sort((a, b) => b.name.length - a.name.length)
  if (!candidates.length) return esc(source)
  let cursor = 0
  let markup = ''
  while (cursor < source.length) {
    const match = candidates.map((concept) => ({ ...concept, at: source.indexOf(concept.name, cursor) }))
      .filter((concept) => concept.at >= 0).sort((a, b) => a.at - b.at || b.name.length - a.name.length)[0]
    if (!match) { markup += esc(source.slice(cursor)); break }
    markup += esc(source.slice(cursor, match.at))
    markup += `<a class="reader-story-concept-link" href="#${esc(match.anchor)}">${esc(match.name)}</a>`
    cursor = match.at + match.name.length
  }
  return markup
}

function readerStoryParagraphs(items, concepts = []) {
  return readerDesignItems(items).map((item) => `<p>${readerStoryInlineMarkup(item, concepts)}</p>`).join('')
}

function readerStoryConceptsMarkup(concepts, summary) {
  const entries = (concepts || []).map((concept, index) => {
    const name = readerDesignCopy(concept?.name)
    const description = readerDesignCopy(concept?.description)
    const scope = readerDesignCopy(concept?.scope, '本系统概念')
    const details = readerDesignItems(concept?.details)
    const playSlug = String(concept?.play_slug || '').trim()
    if (!name || !description) return ''
    const href = playSlug ? `/game-observatory/studio/play?game=${encodeURIComponent(summary.game_slug)}&play=${encodeURIComponent(playSlug)}` : ''
    return `<article id="${esc(readerStoryConceptAnchor(concept, index))}"><span>${esc(scope)}</span><h3>${esc(name)}</h3><p>${esc(description)}</p>${readerDesignParagraphs(details)}${href ? `<a class="reader-story-concept-source" href="${href}">查看相关系统</a>` : ''}</article>`
  }).filter(Boolean)
  if (!entries.length) return ''
  const index = concepts.map((concept, conceptIndex) => {
    const name = readerDesignCopy(concept?.name)
    return name ? `<a href="#${esc(readerStoryConceptAnchor(concept, conceptIndex))}">${esc(name)}</a>` : ''
  }).filter(Boolean).join('')
  return `<section class="reader-story-concepts" id="${readerStoryAnchor('concepts')}"><header><span>概念</span><h2>先认识这些概念</h2></header><nav aria-label="概念索引">${index}</nav><div>${entries.join('')}</div></section>`
}

function readerStoryItemsMarkup(items, style = 'columns', concepts = [], loading = 'lazy') {
  const entries = (items || []).map((item) => {
    const title = readerDesignCopy(item?.title)
    const eyebrow = readerDesignCopy(item?.eyebrow)
    const paragraphs = readerDesignItems(item?.paragraphs?.length ? item.paragraphs : [item?.body])
    const artifactId = String(item?.artifact_id || '').trim()
    const caption = readerDesignCopy(item?.caption || title)
    if (!title && !paragraphs.length) return ''
    return `<article class="reader-story-item${artifactId ? ' has-image' : ''}">${artifactId ? `<figure>${partialArtifactImage(artifactId, title || '游戏画面', loading)}<figcaption>${esc(caption)}</figcaption></figure>` : ''}<div>${eyebrow ? `<span>${esc(eyebrow)}</span>` : ''}${title ? `<h3>${readerStoryInlineMarkup(title, concepts)}</h3>` : ''}${readerStoryParagraphs(paragraphs, concepts)}</div></article>`
  }).filter(Boolean)
  if (!entries.length) return ''
  const resolvedStyle = ['columns', 'stack', 'rail'].includes(style) ? style : 'columns'
  return `<div class="reader-story-items is-${resolvedStyle}">${entries.join('')}</div>`
}

function readerStoryFlowMarkup(items, concepts = []) {
  const entries = (items || []).map((item, index) => {
    const title = readerDesignCopy(typeof item === 'string' ? item : item?.title)
    const description = readerDesignCopy(typeof item === 'string' ? '' : item?.description)
    if (!title && !description) return ''
    return `<li><b>${String(index + 1).padStart(2, '0')}</b><div>${title ? `<strong>${readerStoryInlineMarkup(title, concepts)}</strong>` : ''}${description ? `<p>${readerStoryInlineMarkup(description, concepts)}</p>` : ''}</div></li>`
  }).filter(Boolean)
  return entries.length ? `<ol class="reader-story-flow">${entries.join('')}</ol>` : ''
}

function readerStoryTableMarkup(table, title = '', concepts = []) {
  const columns = readerDesignItems(table?.columns)
  const rows = (table?.rows || []).map((row) => (row || []).map((cell) => readerDesignCopy(cell))).filter((row) => row.some(Boolean))
  if (!columns.length || !rows.length) return ''
  return `<div class="reader-story-table-wrap" role="region" aria-label="${esc(title || '系统对照表')}" tabindex="0"><table><thead><tr>${columns.map((column) => `<th>${readerStoryInlineMarkup(column, concepts)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((_, index) => `<td>${readerStoryInlineMarkup(row[index] || '', concepts)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
}

function readerStoryRelatedMarkup(items, summary) {
  const entries = (items || []).map((item) => {
    const playSlug = String(item?.play_slug || '').trim()
    const section = String(item?.section || '').trim()
    const title = readerDesignCopy(item?.title)
    const description = readerDesignCopy(item?.description)
    if (!playSlug || !title) return ''
    const sectionQuery = section ? `&section=${encodeURIComponent(section)}` : ''
    return `<a href="/game-observatory/studio/play?game=${encodeURIComponent(summary.game_slug)}&play=${encodeURIComponent(playSlug)}${sectionQuery}"><strong>${esc(title)}</strong>${description ? `<span>${esc(description)}</span>` : ''}</a>`
  }).filter(Boolean)
  return entries.length ? `<aside class="reader-story-related"><span>就近展开</span><div>${entries.join('')}</div></aside>` : ''
}

const readerStoryInterfaceSectionIds = new Set(['interfaces-and-operations', 'observed-operation'])

function readerStorySectionMarkup(section, summary, index, concepts = [], options = {}) {
  const title = readerDesignCopy(section?.title)
  const eyebrow = readerDesignCopy(section?.eyebrow)
  const paragraphs = readerDesignItems(section?.paragraphs)
  const artifactId = String(section?.artifact_id || '').trim()
  const caption = readerDesignCopy(section?.caption || title)
  const layout = ['feature', 'wide', 'text'].includes(section?.layout) ? section.layout : (artifactId ? 'feature' : 'text')
  const inference = section?.inference === true
  const interfaceTab = options.interfaceTab === true
  const items = readerStoryItemsMarkup(section?.items, section?.items_style, concepts, interfaceTab ? 'eager' : 'lazy')
  const flow = readerStoryFlowMarkup(section?.flow, concepts)
  const table = readerStoryTableMarkup(section?.table, title, concepts)
  const related = readerStoryRelatedMarkup(section?.related, summary)
  if (!title && !paragraphs.length && !items && !flow && !table && !related) return ''
  const figure = artifactId ? `<figure class="reader-story-figure">${partialArtifactImage(artifactId, title || '系统画面', 'eager')}<figcaption>${esc(caption)}</figcaption></figure>` : ''
  return `<section class="reader-story-section is-${layout}${inference ? ' is-inference' : ''}${interfaceTab ? ' is-interface-tab-section' : ''}" id="${readerStoryAnchor(section?.id, index)}">${figure}<div class="reader-story-section-copy"><header>${eyebrow ? `<span>${esc(eyebrow)}</span>` : ''}${title ? `<h2>${esc(title)}</h2>` : ''}</header>${inference ? '<p class="reader-story-inference-note">以下内容是推测，等待更多材料验证。</p>' : ''}${readerStoryParagraphs(paragraphs, concepts)}${flow}${items}${table}${related}</div></section>`
}

function readerStoryMarkup(story, summary, options = {}) {
  const concepts = (story?.concepts || []).filter((concept) => readerDesignCopy(concept?.name) && readerDesignCopy(concept?.description))
  const excludedSectionIds = options.excludeSectionIds || new Set()
  const sections = (story?.sections || []).map((section, index) => ({ section, index }))
    .filter(({ section }) => !excludedSectionIds.has(String(section?.id || '').trim()))
    .map(({ section, index }) => ({ title: readerDesignCopy(section?.title), anchor: readerStoryAnchor(section?.id, index), markup: readerStorySectionMarkup(section, summary, index, concepts) }))
    .filter((section) => section.markup)
  if (!sections.length) return ''
  const eyebrow = readerDesignCopy(story?.eyebrow, '系统全貌')
  const title = readerDesignCopy(story?.title, readerTitle(summary))
  const summaryCopy = readerDesignCopy(story?.summary, readerView(summary).summary)
  const lead = readerDesignItems(story?.lead)
  const conceptsMarkup = readerStoryConceptsMarkup(concepts, summary)
  const navigationItems = [...(conceptsMarkup ? [{ anchor: readerStoryAnchor('concepts'), title: '概念' }] : []), ...sections]
  const navigation = navigationItems.length > 1 ? `<nav class="reader-story-nav" aria-label="本文内容"><span>本页</span>${navigationItems.map((section) => `<a href="#${section.anchor}">${esc(section.title || '继续阅读')}</a>`).join('')}</nav>` : ''
  return `<article class="reader-story"><header class="reader-story-opening"><span>${esc(eyebrow)}</span><h2>${esc(title)}</h2>${summaryCopy ? `<p>${readerStoryInlineMarkup(summaryCopy, concepts)}</p>` : ''}${readerStoryParagraphs(lead, concepts)}</header>${navigation}${conceptsMarkup}${sections.map((section) => section.markup).join('')}</article>`
}

function readerStoryInterfaceTabMarkup(story, summary) {
  const sections = (story?.sections || []).map((section, index) => ({ section, index }))
    .filter(({ section }) => readerStoryInterfaceSectionIds.has(String(section?.id || '').trim()))
    .map(({ section, index }) => ({ title: readerDesignCopy(section?.title), anchor: readerStoryAnchor(section?.id, index), markup: readerStorySectionMarkup(section, summary, index, [], { interfaceTab: true }) }))
    .filter((section) => section.markup)
  if (!sections.length) return ''
  const navigation = sections.length > 1 ? `<nav class="reader-story-nav" aria-label="界面与操作内容"><span>本页</span>${sections.map((section) => `<a href="#${section.anchor}">${esc(section.title || '继续阅读')}</a>`).join('')}</nav>` : ''
  return `<article class="reader-story reader-story-interface-tab">${navigation}${sections.map((section) => section.markup).join('')}</article>`
}

function readerRecordTitle(reader, index, total) {
  return `${reader.title}游玩记录${total > 1 ? ` ${index + 1}` : ''}`
}

function readerConceptMarkup(reader) {
  if (!reader.concepts.length) return ''
  const index = reader.concepts.map((concept) => `<a href="#${esc(concept.id)}">${esc(concept.name)}</a>`).join('')
  const cards = reader.concepts.map((concept) => `<article id="${esc(concept.id)}"><span>关键概念</span><h4>${esc(concept.name)}</h4><p>${esc(concept.description)}</p></article>`).join('')
  return `<nav class="reader-concept-index" aria-label="本文关键概念">${index}</nav><div class="reader-concept-grid">${cards}</div>`
}

function readerConnectionMarkup(reader, summary) {
  if (!reader.connections.length) return ''
  return `<div class="reader-connection-list">${reader.connections.map((connection) => `<a href="/game-observatory/studio/play?game=${encodeURIComponent(summary.game_slug)}&play=${encodeURIComponent(connection.play_slug)}"><strong>${esc(connection.title)}</strong><span>${esc(connection.relationship)}</span></a>`).join('')}</div>`
}

function partialBundleRunIds(bundle) {
  return new Set([...(bundle?.evidence_run_ids || []), ...(bundle?.evidence_run_id ? [bundle.evidence_run_id] : [])].filter(Boolean))
}

function scopedEvidenceRun(runs, bundle, requestedRunId = '') {
  if (!bundle) return null
  const runIds = partialBundleRunIds(bundle)
  const selectedRunId = requestedRunId || bundle.evidence_run_id || bundle.evidence_run_ids?.[0]
  if (!selectedRunId || !runIds.has(selectedRunId)) return null
  return runs.find((item) => item.id === selectedRunId) || null
}

function playSearchHref(summary) {
  if (!summary?.game_slug || !summary?.play_slug) return '/game-observatory/studio/search'
  return `/game-observatory/studio/search?game=${encodeURIComponent(summary.game_slug)}&play=${encodeURIComponent(summary.play_slug)}`
}

function scopeSearchNavigation(summary) {
  const link = document.querySelector('[data-surface="search"]')
  if (link) link.href = playSearchHref(summary)
}

function playContentHref(surface, summary, suffix = '') {
  const query = surface === 'evidence'
    ? `draft=${encodeURIComponent(summary.path)}&run=${encodeURIComponent(summary.evidence_run_id)}`
    : `draft=${encodeURIComponent(summary.path)}`
  return `/game-observatory/studio/${surface}?${query}${suffix}`
}

function playBreadcrumb(summary, current) {
  if (!summary?.game_slug || !summary?.play_slug) return ''
  const playUrl = `/game-observatory/studio/play?game=${encodeURIComponent(summary.game_slug)}&play=${encodeURIComponent(summary.play_slug)}`
  return `<nav class="content-breadcrumb"><a href="/game-observatory/studio/">游戏</a><b>→</b><a href="/game-observatory/studio/game?game=${encodeURIComponent(summary.game_slug)}">${esc(readerGameTitle(summary))}</a><b>→</b><a href="${playUrl}">${esc(readerTitle(summary))}</a><b>→</b><span>${esc(current)}</span></nav>`
}

function entryHref(entry) {
  return `/game-observatory/studio/play?game=${encodeURIComponent(entry.game_slug)}&play=${encodeURIComponent(entry.play_slug)}`
}

function featuredEntryCard(entry) {
  const reader = readerView(entry)
  return `<a class="play-card" href="${entryHref(entry)}"><div>${entry.cover_artifact_id ? `<img src="/api/game-observatory/artifacts/${encodeURIComponent(entry.cover_artifact_id)}" alt="${esc(reader.title)} 游戏画面" loading="eager" decoding="async">` : ''}</div><section><span>${esc(reader.kind)}</span><h3>${esc(reader.title)}</h3><p>${esc(reader.summary)}</p><div class="tag-row">${playTagMarkup(entry.play_tags, entry.play_tag_details)}</div></section></a>`
}

function systemEntryCard(entry) {
  const reader = readerView(entry)
  return `<a class="system-entry-card" href="${entryHref(entry)}"><div>${entry.cover_artifact_id ? `<img src="/api/game-observatory/artifacts/${encodeURIComponent(entry.cover_artifact_id)}" alt="${esc(reader.title)} 游戏画面" loading="lazy" decoding="async">` : ''}</div><section><span>${esc(reader.kind)}</span><h3>${esc(reader.title)}</h3><p>${esc(reader.summary)}</p></section></a>`
}

function contentGroupCard(bucket, gameSlug) {
  const { group, entries } = bucket
  const groupHref = `/game-observatory/studio/group?game=${encodeURIComponent(gameSlug)}&group=${encodeURIComponent(group.slug)}`
  const roleCounts = new Map()
  entries.forEach((entry) => {
    const label = readerView(entry).kind
    roleCounts.set(label, (roleCounts.get(label) || 0) + 1)
  })
  const roleSummary = [...roleCounts.entries()].map(([label, count]) => `${count} 篇${label}`).join(' · ')
  return `<article class="content-group-card" id="${readerGroupAnchor(group)}"><header><div><span>${group.kind === 'system' ? '系统' : '跨玩法索引'}</span><h2>${esc(group.title)}</h2><p>${esc(group.summary)}</p><small>${esc(roleSummary)}</small></div><a class="button" href="${groupHref}">${group.reader_story || group.story_play_slug ? (group.kind === 'system' ? '阅读系统全貌' : '阅读这组内容') : (group.kind === 'system' ? '打开系统目录' : '查看这组内容')}</a></header><div class="system-entry-grid">${entries.map(systemEntryCard).join('')}</div></article>`
}

async function renderGame() {
  const bundles = await partialWorkspacePayload()
  const gameSlug = new URLSearchParams(location.search).get('game') || bundles[0]?.game_slug
  const entries = bundles.filter((item) => item.game_slug === gameSlug || item.game_id === gameSlug)
  const publishedEntries = entries.filter(readerVisible)
  const journeys = publishedEntries.filter((item) => readerView(item).position.level === 'journey')
  const loops = publishedEntries.filter((item) => readerView(item).position.level === 'play_loop')
  const grouped = groupEntries(publishedEntries)
  const systemGroups = grouped.filter((bucket) => bucket.group.kind === 'system')
  const collections = grouped.filter((bucket) => bucket.group.kind === 'collection')
  const unclassified = publishedEntries.filter((item) => {
    const position = readerView(item).position
    return !position.group?.id && !['journey', 'play_loop'].includes(position.level)
  })
  const game = entries[0]
  if (!game) {
    workspace.innerHTML = `${head('GAME', '没有找到游戏', '该游戏当前没有玩法对象。', '本地资料库')}<a class="button" href="/game-observatory/studio/">返回游戏</a>`
    return
  }
  const gameDetailPaths = [...new Set(entries.map((entry) => entry.base_bundle_path || entry.path))]
  const gameDetails = await Promise.all(gameDetailPaths.map((path) => safeJson(
    `/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(path)}`,
    { bundle: {} },
  )))
  const gameFeedback = [...new Map(gameDetails.flatMap((item) => (
    item.bundle?.community_feedback || []
  )).filter((item) => item.content_scope === 'game').map((item) => [item.id, item])).values()]
  workspace.innerHTML = `<nav class="content-breadcrumb"><a href="/game-observatory/studio/">游戏</a><b>→</b><span>${esc(readerGameTitle(game))}</span></nav>
  <header class="game-detail-head"><span>游戏设计目录</span><h1>${esc(readerGameTitle(game))}</h1><p>${esc(readerGameSummary(game))}</p></header>
  <form class="library-search" action="/game-observatory/studio/search"><input type="hidden" name="game" value="${esc(game.game_slug)}"><label><span>搜索本游戏的设计资料</span><input type="search" name="q" placeholder="系统、机制、操作、界面或规则"></label><button class="button is-primary" type="submit">搜索</button></form>
  <div class="tag-row is-prominent">${(game.game_tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div>
  ${journeys.length ? `<section class="play-library"><div class="section-heading"><div><span>入门路线</span><h2>先从跨系统流程认识游戏</h2></div><small>${journeys.length} 条</small></div>${journeys.map(featuredEntryCard).join('')}</section>` : ''}
  ${loops.length ? `<section class="play-library"><div class="section-heading"><div><span>玩法循环</span><h2>按一个完整场景理解多项机制怎样协作</h2></div><small>${loops.length} 个</small></div>${loops.map(featuredEntryCard).join('')}</section>` : ''}
  <section class="content-group-library"><div class="section-heading"><div><span>系统目录</span><h2>先选系统，再阅读其中的机制与操作</h2></div><small>${systemGroups.length} 个系统 · ${systemGroups.reduce((count, bucket) => count + bucket.entries.length, 0)} 篇资料</small></div>${systemGroups.length ? systemGroups.map((bucket) => contentGroupCard(bucket, game.game_slug)).join('') : '<div class="empty-state"><strong>当前还没有完成系统归类</strong><span>未归类文章不会被计作游戏系统。</span></div>'}</section>
  ${collections.length ? `<section class="content-group-library is-collection"><div class="section-heading"><div><span>跨玩法入口</span><h2>这些页面连接多个系统，但本身不是系统</h2></div><small>${collections.reduce((count, bucket) => count + bucket.entries.length, 0)} 篇</small></div>${collections.map((bucket) => contentGroupCard(bucket, game.game_slug)).join('')}</section>` : ''}
  ${unclassified.length ? `<section class="play-library"><div class="section-heading"><div><span>待归类</span><h2>尚未确认对象层级的内容</h2></div><small>${unclassified.length} 篇</small></div>${unclassified.map(featuredEntryCard).join('')}</section>` : ''}
  ${gameFeedback.length ? `<section class="game-feedback-section" id="game-feedback"><header class="content-page-heading"><span>游戏级反馈与数据</span><h2>综合评价、平台数据与市场估算</h2><p>这些材料描述整个游戏或平台表现，不归因到某一个玩法。</p></header><div class="feedback-source-list has-images">${gameFeedback.map((item) => `<article>${feedbackPreviewMarkup(item)}<span>${esc(feedbackSourceTypeNames[item.source_type] || item.source_type)}</span><h3>${esc(item.title)}</h3><p>${esc(item.summary)}</p>${feedbackSourceMarkup(item)}</article>`).join('')}</div></section>` : ''}`
}

async function renderGroup() {
  const bundles = (await partialWorkspacePayload()).filter(readerVisible)
  const query = new URLSearchParams(location.search)
  const gameSlug = query.get('game') || bundles[0]?.game_slug
  const groupSlug = query.get('group') || ''
  const entries = bundles.filter((item) => item.game_slug === gameSlug || item.game_id === gameSlug)
  const bucket = groupEntries(entries).find((item) => item.group.slug === groupSlug || item.group.id === groupSlug)
  const game = entries[0]
  if (!game || !bucket) {
    workspace.innerHTML = `${head('目录', '没有找到这组内容', '请从游戏目录重新选择系统或内容集合。', '游戏设计资料库')}<a class="button" href="/game-observatory/studio/game?game=${encodeURIComponent(gameSlug || '')}">返回游戏目录</a>`
    return
  }
  const { group } = bucket
  const roleCounts = new Map()
  bucket.entries.forEach((entry) => {
    const label = readerView(entry).kind
    roleCounts.set(label, (roleCounts.get(label) || 0) + 1)
  })
  const roleChips = [...roleCounts.entries()].map(([label, count]) => `<span>${count} 篇${esc(label)}</span>`).join('')
  const storyEntry = bucket.entries.find((entry) => entry.play_slug === group.story_play_slug || entry.play_id === group.story_play_slug)
  const groupStory = group.reader_story
  const storyDetail = !groupStory && storyEntry ? await safeJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(storyEntry.path)}`, { bundle: {} }) : { bundle: {} }
  const storyMarkup = readerStoryMarkup(groupStory || storyDetail.bundle?.design_document?.reader_story, storyEntry || bucket.entries[0], { excludeSectionIds: readerStoryInterfaceSectionIds })
  const memberHeading = storyMarkup ? '继续拆看具体界面、机制与操作' : (group.kind === 'system' ? '机制、操作、界面与状态规则' : '连接多个玩法的入口与说明')
  workspace.innerHTML = `<nav class="content-breadcrumb"><a href="/game-observatory/studio/">游戏</a><b>→</b><a href="/game-observatory/studio/game?game=${encodeURIComponent(game.game_slug)}">${esc(readerGameTitle(game))}</a><b>→</b><span>${esc(group.title)}</span></nav><header class="group-detail-head"><span>${group.kind === 'system' ? '系统' : '跨玩法索引'}</span><h1>${esc(group.title)}</h1><p>${esc(group.summary)}</p><div class="group-role-counts">${roleChips}</div></header>${storyMarkup}<section class="group-member-section"><div class="section-heading"><div><span>${storyMarkup ? '延伸阅读' : (group.kind === 'system' ? '系统构成' : '内容构成')}</span><h2>${memberHeading}</h2></div><small>${bucket.entries.length} 篇</small></div><div class="system-entry-grid">${bucket.entries.map(systemEntryCard).join('')}</div></section>`
}

const playRecordTypeNames = {
  ai_player_live_run: 'AI 玩家实机运行',
  human_screen_recording: '人类录屏',
}

const feedbackSourceTypeNames = {
  player_comment: '玩家具体评论',
  player_discussion: '玩家讨论',
  media_score: '媒体评分',
  media_article: '媒体文章',
  media_review: '媒体评论',
  objective_data: '客观数据',
  estimated_data: '预估数据',
}

const sourceMetricNames = {
  helpful_count: '有帮助',
  comment_count: '评论',
  reply_count: '回复',
  captured_on: '快照日期',
  platform: '平台范围',
  rating_value: '评分',
  rating_scale: '评分上限',
  rating_count_approx: '评价数（约）',
  header_rating_count_approx: '应用页头评价数（约）',
  phone_rating_count_approx: 'Phone 评价数（约）',
  download_band: '下载量区间',
  review_count_approx: '评论数（约）',
  review_count: '评论数',
  positive_percent_approx: '好评率（约）',
  positive_percent: '好评率',
  steamdb_rating_percent: 'SteamDB Rating',
  all_time_concurrent_peak: '历史同时在线峰值',
  peak_date: '峰值日期',
  through_date: '统计截止日',
  platform_scope: '平台口径',
  metric: '指标',
  value: '数值',
  currency: '币种',
}

function readableSourceMetrics(value) {
  if (!value || typeof value !== 'object') return ''
  return Object.entries(value).map(([key, item]) => `${sourceMetricNames[key] || key} ${item}`).join(' · ')
}

function screenTagMap(bundle) {
  return new Map((bundle.screen_tags || []).map((item) => [item.screen_state_id, item.tags || []]))
}

function feedbackSourceMarkup(item) {
  const source = item.source || {}
  if (!source.url || !source.platform) return ''
  const rating = source.rating ? `${source.rating.value} / ${source.rating.scale_max}${source.rating.rating_count != null ? ` · ${source.rating.rating_count} 份评分` : ''}` : ''
  const engagement = readableSourceMetrics(source.engagement)
  const dataScope = readableSourceMetrics(source.data_scope)
  const method = source.estimation_method ? [
    source.estimation_method.method,
    (source.estimation_method.basis || []).length ? `依据：${source.estimation_method.basis.join('、')}` : '',
    (source.estimation_method.assumptions || []).length ? `假设：${source.estimation_method.assumptions.join('、')}` : '',
    source.estimation_method.range_low != null ? `范围：${source.estimation_method.range_low}–${source.estimation_method.range_high} ${source.estimation_method.unit || ''}` : '',
  ].filter(Boolean).join('；') : ''
  const details = [
    ['原页面标题', source.title],
    ['原文定位', source.locator],
    ['语言', source.locale],
    ['版本语境', source.version_context],
    ['抓取时间', source.captured_at],
    ['评分', rating],
    ['互动数据', engagement],
    ['数据口径', dataScope],
    ['估算方法', method],
    ['来源说明', source.note],
  ].filter(([, value]) => value)
  return `<a class="source-binding" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer"><strong>${esc(source.platform)}</strong><span>${esc(source.author || source.account || '未登记作者')} · ${esc(source.published_at || '未登记发布时间')}</span>${details.length ? `<dl>${details.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>` : ''}<small>${esc(source.url)}</small></a>`
}

function strictPlayBundleView(bundle) {
  const partition = bundle.content_partition || {}
  const strictSurfaceIds = new Set(partition.strict_surface_ids || (bundle.screen_states || []).map((item) => item.id))
  const hasStrictMechanicIds = Object.prototype.hasOwnProperty.call(partition, 'strict_mechanic_ids')
  const strictMechanicIds = new Set(partition.strict_mechanic_ids || [])
  const strictRecordIds = new Set(partition.strict_play_record_ids || (bundle.play_records || []).map((item) => item.id))
  const hasStrictGapIds = Object.prototype.hasOwnProperty.call(partition, 'strict_evidence_gap_ids')
  const strictGapIds = new Set(partition.strict_evidence_gap_ids || [])
  const screenStates = (bundle.screen_states || []).filter((item) => strictSurfaceIds.has(item.id))
  const screenArtifactIds = new Set(screenStates.flatMap((item) => item.artifact_ids || []))
  const interactions = (bundle.interactions || []).filter((item) => (
    strictSurfaceIds.has(item.from_state_id) && strictSurfaceIds.has(item.to_state_id)
  ))
  const interactionIds = new Set(interactions.map((item) => item.id))
  const transitions = (bundle.state_transitions || []).filter((item) => (
    strictSurfaceIds.has(item.from_state_id) && strictSurfaceIds.has(item.to_state_id)
  )).map((item) => ({
    ...item,
    via_interaction_ids: (item.via_interaction_ids || []).filter((id) => interactionIds.has(id)),
  }))
  const mechanics = (bundle.visible_mechanics || []).filter((item) => (
    hasStrictMechanicIds
      ? strictMechanicIds.has(item.id)
      : (item.screen_state_ids || []).some((id) => strictSurfaceIds.has(id))
  )).map((item) => ({
    ...item,
    screen_state_ids: (item.screen_state_ids || []).filter((id) => strictSurfaceIds.has(id)),
  }))
  return {
    ...bundle,
    screen_states: screenStates,
    screen_tags: (bundle.screen_tags || []).filter((item) => strictSurfaceIds.has(item.screen_state_id)),
    ui_elements: (bundle.ui_elements || []).filter((item) => (
      (item.screen_state_ids || []).some((id) => strictSurfaceIds.has(id))
    )).map((item) => ({
      ...item,
      screen_state_ids: (item.screen_state_ids || []).filter((id) => strictSurfaceIds.has(id)),
    })),
    interactions,
    state_transitions: transitions,
    visible_mechanics: mechanics,
    resource_displays: (bundle.resource_displays || []).filter((item) => strictSurfaceIds.has(item.screen_state_id)),
    screen_families: (bundle.screen_families || []).filter((item) => (
      (item.screen_state_ids || []).some((id) => strictSurfaceIds.has(id))
    )).map((item) => {
      const familySurfaceIds = (item.screen_state_ids || []).filter((id) => strictSurfaceIds.has(id))
      const familyArtifacts = (item.gallery_artifact_ids || []).filter((id) => screenArtifactIds.has(id))
      const representative = screenArtifactIds.has(item.representative_artifact_id)
        ? item.representative_artifact_id
        : screenStates.find((screen) => familySurfaceIds.includes(screen.id))?.artifact_ids?.[0] || ''
      return {
        ...item,
        screen_state_ids: familySurfaceIds,
        representative_artifact_id: representative,
        gallery_artifact_ids: [...new Set([representative, ...familyArtifacts].filter(Boolean))],
      }
    }),
    play_records: (bundle.play_records || []).filter((item) => strictRecordIds.has(item.id)),
    evidence_gaps: (bundle.evidence_gaps || []).filter((item) => (
      !hasStrictGapIds || strictGapIds.has(item.id)
    )),
    demo_reproductions: (bundle.demo_reproductions || []).filter((item) => (
      (item.covered_surface_ids || []).every((id) => strictSurfaceIds.has(id))
      && (item.covered_interaction_ids || []).every((id) => interactionIds.has(id))
    )),
    community_feedback: (bundle.community_feedback || []).filter((item) => item.content_scope !== 'game'),
    play_connections: bundle.play_connections || [],
  }
}

const playSections = [
  ['design', '内容说明'],
  ['interfaces', '界面与操作'],
  ['screen-tags', '界面标签'],
  ['records', '游玩记录'],
  ['feedback', '社群反馈'],
  ['tags', '内容标签'],
  ['demo', '交互演示'],
]

function playSectionHref(summary, section) {
  return `/game-observatory/studio/play?game=${encodeURIComponent(summary.game_slug)}&play=${encodeURIComponent(summary.play_slug)}&section=${encodeURIComponent(section)}`
}

function artifactGalleryMarkup(artifactIds, label) {
  const values = [...new Set((artifactIds || []).filter((id) => id && !id.startsWith('art.video.')))]
  if (!values.length) return ''
  return `<div class="play-image-gallery">${values.map((artifactId, index) => `<button type="button" data-gallery-image="${esc(artifactId)}" aria-label="查看${esc(label)}第 ${index + 1} 张图">${partialArtifactImage(artifactId, `${label} · ${index + 1}`)}</button>`).join('')}</div>`
}

function screenFamilyMarkup(family, screensById, tagsByScreen) {
  const screens = (family.screen_state_ids || []).map((id) => screensById.get(id)).filter(Boolean)
  const tags = [...new Set(screens.flatMap((screen) => tagsByScreen.get(screen.id) || []))]
  const title = readerFacingCopy(family.title, '游戏界面')
  const summary = readerFacingCopy(family.summary, '展示该系统中的主要信息和可用操作。')
  return `<article class="screen-family-card" id="${esc(family.id)}">
    <div class="screen-family-lead">${partialArtifactImage(family.representative_artifact_id, title, 'eager')}</div>
    <div class="screen-family-copy"><span>${screens.length} 个界面状态</span><h3>${esc(title)}</h3><p>${esc(summary)}</p><div class="tag-row">${tags.map((tag) => `<span>${esc(tag)}</span>`).join('')}</div><details><summary>查看包含的界面</summary><ul>${screens.map((screen) => `<li>${esc(screen.name)}</li>`).join('')}</ul></details></div>
    ${artifactGalleryMarkup(family.gallery_artifact_ids, title)}
  </article>`
}

function screenFamilyDesignMarkup(family, screensById, tagsByScreen) {
  const information = family.information_blocks || []
  const rules = family.interaction_rules || []
  return `<section class="screen-family-spec">
    ${screenFamilyMarkup(family, screensById, tagsByScreen)}
    <div class="screen-family-contract"><div><h4>界面提供的信息</h4>${simpleList(information)}</div><div><h4>玩家操作与系统响应</h4><div class="interaction-rule-table">${rules.map((rule) => `<article><strong>${esc(rule.action)}</strong><p>${esc(rule.response)}</p><span>到达：${esc(rule.destination)}</span></article>`).join('')}</div></div></div>
  </section>`
}

const designSourceTypeNames = {
  live_observation: '实机观察',
  internal_design: '内部设计资料',
  internal_config: '内部配置',
  internal_code: '内部代码',
  official_public: '官方公开资料',
  public_research: '公开资料汇编',
}

function mechanismModuleMarkup(module) {
  const copy = module.reader_copy || {}
  const conceptParagraphs = readerDesignItems(
    copy.concept_paragraphs?.length
      ? copy.concept_paragraphs
      : [module.definition, module.player_result]
  )
  const playerFlowParagraphs = readerDesignItems(
    copy.player_flow_paragraphs?.length
      ? copy.player_flow_paragraphs
      : [
        readerDesignListParagraph('开始前，玩家需要具备或选择：', module.inputs),
        readerDesignListParagraph('操作完成后，会更新：', module.outputs),
      ]
  )
  const stateParagraphs = readerDesignItems(
    copy.state_and_limits_paragraphs?.length
      ? copy.state_and_limits_paragraphs
      : [
        readerDesignListParagraph('常见状态包括：', module.states),
        readerDesignListParagraph('需要留意的限制：', module.boundaries),
      ]
  )
  const systemLinkParagraphs = readerDesignItems(
    copy.system_link_paragraphs?.length
      ? copy.system_link_paragraphs
      : [readerDesignListParagraph('它还会影响或依赖：', module.dependencies)]
  )
  const rules = (module.rules || [])
    .map((rule) => ({
      title: readerDesignCopy(rule.title),
      statement: readerDesignCopy(rule.statement),
    }))
    .filter((rule) => rule.title && rule.statement)
  const ruleIntro = readerDesignCopy(copy.rule_intro)
  const figure = module.artifact_id
    ? `<figure class="mechanism-chapter-figure">${partialArtifactImage(module.artifact_id, `${readerDesignCopy(module.title, '机制')}对应游戏画面`, 'eager')}<figcaption>${esc(readerDesignCopy(copy.image_caption || module.title, '对应游戏画面'))}</figcaption></figure>`
    : ''
  const sections = [
    conceptParagraphs.length ? `<section><h4>这个机制是什么</h4>${readerDesignParagraphs(conceptParagraphs)}</section>` : '',
    playerFlowParagraphs.length ? `<section><h4>玩家怎样使用</h4>${readerDesignParagraphs(playerFlowParagraphs)}</section>` : '',
    (ruleIntro || rules.length) ? `<section><h4>规则如何运转</h4>${ruleIntro ? `<p>${esc(ruleIntro)}</p>` : ''}<ol class="mechanism-prose-rules">${rules.map((rule) => `<li><p><strong>${esc(rule.title)}。</strong>${esc(rule.statement)}</p></li>`).join('')}</ol></section>` : '',
    stateParagraphs.length ? `<section><h4>条件变化与结果</h4>${readerDesignParagraphs(stateParagraphs)}</section>` : '',
    systemLinkParagraphs.length ? `<section><h4>与其他系统的关系</h4>${readerDesignParagraphs(systemLinkParagraphs)}</section>` : '',
  ].filter(Boolean).join('')
  if (!sections) return ''
  return `<article class="mechanism-chapter${figure ? ' has-figure' : ''}" id="${esc(module.id)}">
    ${figure}
    <div class="mechanism-chapter-copy">
      <header><span>核心机制</span><h3>${esc(readerDesignCopy(module.title, '未命名机制'))}</h3></header>
      ${sections}
    </div>
  </article>`
}

function progressionAxesMarkup(items) {
  const entries = (items || []).map((item) => ({
    name: readerDesignCopy(item.name),
    description: readerDesignCopy(item.description),
  })).filter((item) => item.name && item.description)
  if (!entries.length) return ''
  return `<div class="design-prose-list">${entries.map((item) => `<article><h4>${esc(item.name)}</h4><p>${esc(item.description)}</p></article>`).join('')}</div>`
}

function resourceLoopMarkup(items) {
  const entries = (items || []).map((item) => ({
    resource: readerDesignCopy(item.resource),
    description: readerDesignCopy(item.description),
  })).filter((item) => item.resource && item.description)
  if (!entries.length) return ''
  return `<div class="design-prose-list">${entries.map((item) => `<article><h4>${esc(item.resource)}</h4><p>${esc(item.description)}</p></article>`).join('')}</div>`
}

function mechanismMapMarkup(items) {
  const entries = (items || []).map((item) => ({
    from: readerDesignCopy(item.from),
    relation: readerDesignCopy(item.relation),
    to: readerDesignCopy(item.to),
  })).filter((item) => item.from && item.relation && item.to)
  if (!entries.length) return ''
  return `<div class="reader-mechanism-map">${entries.map((item) => `<article><strong>${esc(item.from)}</strong><span>${esc(item.relation)}</span><strong>${esc(item.to)}</strong></article>`).join('')}</div>`
}

function readerStateMatrixMarkup(items) {
  const entries = (items || []).map((item) => ({
    subject: readerDesignCopy(item.subject),
    condition: readerDesignCopy(item.condition),
    action: readerDesignCopy(item.available_action),
    result: readerDesignCopy(item.result),
  })).filter((item) => item.subject && item.condition && item.result)
  if (!entries.length) return ''
  return `<div class="reader-state-matrix">${entries.map((item) => `<article><h4>${esc(item.subject)}</h4><p><b>条件</b>${esc(item.condition)}</p>${item.action ? `<p><b>玩家可以</b>${esc(item.action)}</p>` : ''}<p><b>结果</b>${esc(item.result)}</p></article>`).join('')}</div>`
}

function mechanismSourcesMarkup(items) {
  if (!(items || []).length) return ''
  return `<ol class="mechanism-bibliography">${items.map((source) => `<li id="${esc(source.id)}"><p><strong>${esc(source.title)}</strong><span>${esc(designSourceTypeNames[source.type] || source.type)}${source.version_context ? ` · ${esc(source.version_context)}` : ''}</span><small>${esc(source.locator)}</small></p></li>`).join('')}</ol>`
}

function recordGalleryMarkup(record, resolved = {}, displayTitle = '游玩记录', publicReady = false) {
  const artifacts = (resolved.artifacts || []).filter((artifact) => (
    String(artifact.media_type || '').startsWith('image/')
  ))
  if (!artifacts.length) return ''
  return `<div class="record-image-strip">${artifacts.map((artifact, index) => {
    const href = publicReady && String(artifact.media_type || '').startsWith('image/')
      ? `/api/game-observatory/artifacts/${encodeURIComponent(artifact.id)}`
      : artifact.href
    return `<a href="${esc(href)}"><img src="${esc(href)}" alt="${esc(displayTitle)}第 ${index + 1} 张画面" loading="lazy" decoding="async"><span>${esc(displayTitle)} · 画面 ${index + 1}</span></a>`
  }).join('')}</div>`
}

function feedbackPreviewMarkup(item) {
  if (item.preview_artifact_id) {
    const href = `/api/game-observatory/artifacts/${encodeURIComponent(item.preview_artifact_id)}`
    return `<a class="feedback-preview-image" href="${href}" target="_blank" rel="noopener noreferrer">${partialArtifactImage(item.preview_artifact_id, `${item.source?.platform || ''}来源截图`)}<span>查看完整来源截图</span></a>`
  }
  const source = item.source || {}
  return `<div class="feedback-source-preview"><b>${esc(source.platform || '来源平台')}</b><strong>${esc(source.author || source.account || item.title)}</strong><span>${esc(source.published_at || source.captured_at || '时间未登记')}</span><small>${esc(source.locator || '打开原页面查看来源上下文')}</small></div>`
}

function bindPlayGallery() {
  document.querySelectorAll('[data-gallery-image]').forEach((button) => button.addEventListener('click', () => {
    const image = button.querySelector('img')
    const lead = button.closest('.screen-family-card')?.querySelector('.screen-family-lead img')
    if (image && lead) {
      lead.src = image.src
      lead.alt = image.alt
    }
    if (image) openImageViewer(image)
  }))
}

function openImageViewer(image) {
  const dialog = document.querySelector('[data-image-viewer]')
  const viewerImage = dialog?.querySelector('[data-image-viewer-image]')
  const caption = dialog?.querySelector('[data-image-viewer-caption]')
  const source = dialog?.querySelector('[data-image-viewer-source]')
  if (!dialog || !viewerImage || !caption || !source || !image?.currentSrc) return
  const figureCaption = image.closest('figure')?.querySelector('figcaption')?.textContent?.trim()
  viewerImage.src = image.currentSrc
  viewerImage.alt = image.alt || '游戏界面原图'
  caption.textContent = figureCaption || image.alt || '游戏界面原图'
  source.href = image.currentSrc
  if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal()
}

function bindImageViewer() {
  document.querySelectorAll('#workspace img').forEach((image) => {
    image.classList.add('zoomable-game-image')
    if (image.closest('[data-gallery-image]')) return
    image.tabIndex = 0
    image.setAttribute('role', 'button')
    image.setAttribute('aria-label', `${image.alt || '游戏画面'}，点击查看大图`)
    const open = (event) => {
      event.preventDefault()
      event.stopPropagation()
      openImageViewer(image)
    }
    image.addEventListener('click', open)
    image.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return
      open(event)
    })
  })
  const dialog = document.querySelector('[data-image-viewer]')
  if (dialog && !dialog.dataset.bound) {
    dialog.dataset.bound = 'true'
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close()
    })
  }
}

function playRecordOriginMarkup(record, resolved = {}) {
  const sourceEntries = (resolved.sources || []).map((source) => {
    const title = source.title || source.platform || source.locator || '已登记录屏来源'
    const context = [source.platform, source.author || source.account, source.locator].filter(Boolean).join(' · ')
    const url = String(source.url || '')
    const copy = `<strong>${esc(title)}</strong>${context ? `<span>${esc(context)}</span>` : ''}${url ? '<small>打开来源</small>' : ''}`
    return /^(https?:\/\/|file:\/\/|\/)/i.test(url)
      ? `<a class="source-binding" href="${esc(url)}"${/^https?:\/\//i.test(url) ? ' target="_blank" rel="noopener noreferrer"' : ''}>${copy}</a>`
      : `<div class="source-binding">${copy}</div>`
  })
  const artifactKindNames = { screenshot: '截图', video: '视频', ui_tree: '界面结构', trace: '操作轨迹' }
  const artifactEntries = (resolved.artifacts || []).map((artifact) => {
    const title = artifact.title || artifact.file_name || '录屏文件'
    const context = artifactKindNames[artifact.kind] || artifact.kind || '证据文件'
    return `<a class="source-binding" href="${esc(artifact.href)}"><strong>${esc(title)}</strong><span>${esc(context)}</span><small>打开原始文件</small></a>`
  })
  if (!sourceEntries.length && !artifactEntries.length) return ''
  return `<div class="play-record-origin-list" aria-label="${esc(record.title)}的具体来源">${sourceEntries.join('')}${artifactEntries.join('')}</div>`
}

function playRecordActionsMarkup(record, resolved = {}) {
  const run = resolved.run || {}
  const origins = playRecordOriginMarkup(record, resolved)
  const originDetails = origins ? `<details class="play-record-technical"><summary>来源与文件详情</summary>${origins}</details>` : ''
  if (!record.evidence_run_id) return originDetails
  const runLabel = Number(run.step_count || 0) > 0 ? '打开逐步实录' : '打开运行记录'
  const draft = state.currentPartialBundlePath
    ? `draft=${encodeURIComponent(state.currentPartialBundlePath)}&`
    : ''
  const evidenceHref = run.href || `/game-observatory/studio/evidence?${draft}run=${encodeURIComponent(record.evidence_run_id)}`
  const sourcesHref = run.sources_href || `/game-observatory/studio/sources?run=${encodeURIComponent(record.evidence_run_id)}`
  return `<div class="play-action-row"><a class="button is-primary" href="${esc(evidenceHref)}">${runLabel}</a><a class="button" href="${esc(sourcesHref)}">查看原始文件</a></div>${originDetails}`
}

async function renderPlay() {
  const bundles = await partialWorkspacePayload()
  const query = new URLSearchParams(location.search)
  const summary = selectedPartialBundle(bundles, query.get('game') || bundles[0]?.game_slug, query.get('play') || '')
  if (!summary) {
    workspace.innerHTML = `${head('玩法', '没有找到玩法', '该玩法当前没有内容对象。', '本地资料库')}<a class="button" href="/game-observatory/studio/">返回游戏</a>`
    return
  }
  const reader = readerView(summary)
  const position = reader.position
  const parentGroup = position.group || {}
  scopeSearchNavigation(summary)
  const detail = await getJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(summary.path)}`)
  const rawBundle = detail.bundle
  const bundle = strictPlayBundleView(rawBundle)
  const tagsByScreen = screenTagMap(bundle)
  const screenNames = new Map((bundle.screen_states || []).map((item) => [item.id, item.name]))
  const screensById = new Map((bundle.screen_states || []).map((item) => [item.id, item]))
  const elementNames = new Map((bundle.ui_elements || []).map((item) => [item.id, item.name]))
  const evidenceStepRefs = new Map(Object.entries(detail.evidence_step_refs || {}))
  const records = bundle.play_records || []
  const playRecordRefs = detail.play_record_refs || {}
  const feedback = (bundle.community_feedback || []).filter((item) => item.source?.url && item.source?.platform)
  const demos = bundle.demo_reproductions || []
  const families = bundle.screen_families || []
  const activeSection = playSections.some(([id]) => id === query.get('section')) ? query.get('section') : 'design'
  const coverArtifactId = bundle.screen_states?.[0]?.artifact_ids?.find((id) => !id.startsWith('art.video.')) || summary.cover_artifact_id
  const counts = {
    screens: bundle.screen_states?.length || 0,
    interactions: bundle.interactions?.length || 0,
    transitions: bundle.state_transitions?.length || 0,
    mechanics: bundle.visible_mechanics?.length || 0,
    resources: bundle.resource_displays?.length || 0,
  }
  const design = rawBundle.design_document || {}
  const systemDefinition = design.system_definition || {}
  const storedOverviewParagraphs = readerDesignItems(
    systemDefinition.reader_intro?.length
      ? systemDefinition.reader_intro
      : [systemDefinition.definition]
  )
  const overviewParagraphs = storedOverviewParagraphs.length ? storedOverviewParagraphs : [reader.summary]
  const relationshipParagraphs = readerDesignItems(systemDefinition.relationship_overview)
  const designModules = (design.mechanism_modules || []).map((module) => mechanismModuleMarkup(module)).filter(Boolean)
  const mechanismMap = mechanismMapMarkup(design.mechanism_map)
  const progressionAxes = progressionAxesMarkup(design.progression_axes)
  const resourceLoops = resourceLoopMarkup(design.resource_loop)
  const stateMatrix = readerStateMatrixMarkup(design.state_matrix)
  const positionPanel = parentGroup.id && position.level !== 'system'
    ? `<aside class="reader-position-note"><div><span>${parentGroup.kind === 'system' ? '所属系统' : '所属内容集合'}</span><strong>${esc(parentGroup.title)}</strong><p>本文在其中承担“${esc(reader.kind)}”的说明。</p></div><a href="${readerGroupHref(summary)}">${parentGroup.kind === 'system' ? '查看完整系统目录' : '查看同组内容'}</a></aside>`
    : ''
  const storyMarkup = readerStoryMarkup(design.reader_story, summary, {
    excludeSectionIds: readerStoryInterfaceSectionIds,
  })
  const interfaceStoryMarkup = readerStoryInterfaceTabMarkup(design.reader_story, summary)

  const designPanel = `<section class="play-tab-panel play-design-document" data-play-section="design">
    ${storyMarkup ? `${positionPanel}${storyMarkup}` : `
      <header class="content-page-heading"><span>${esc(reader.kind)}</span><h2>${esc(reader.title)}</h2><p>${esc(reader.summary)}</p></header>
      ${positionPanel}
      <div class="reader-system-intro">
        <figure>${partialArtifactImage(coverArtifactId, `${reader.title}主要界面`, 'eager')}<figcaption>${esc(reader.title)}的游戏界面</figcaption></figure>
        <section><span>${esc(readerGoalLabel(position.level))}</span><h3>${esc(reader.player_goal)}</h3></section>
      </div>
      <section class="reader-system-chapter"><header><span>01</span><div><h3>玩家如何使用</h3><p>从当前条件到得到结果的一次完整过程。</p></div></header><ol class="reader-step-list">${reader.steps.map((step) => `<li><span>${esc(step)}</span></li>`).join('')}</ol></section>
      <section class="design-prose-chapter"><h3>玩法概述</h3>${readerDesignParagraphs(overviewParagraphs)}</section>
      ${(relationshipParagraphs.length || mechanismMap) ? `<section class="design-prose-chapter"><h3>整体运作方式</h3>${readerDesignParagraphs(relationshipParagraphs)}${mechanismMap}</section>` : ''}
      ${designModules.length ? `<section class="design-prose-chapter"><h3>具体机制</h3><div class="mechanism-chapter-list">${designModules.join('')}</div></section>` : `<section class="reader-system-chapter"><header><span>02</span><div><h3>关键规则</h3><p>决定玩家选择和结果的主要规则。</p></div></header><div class="reader-rule-grid">${reader.rules.map((rule) => `<article><h4>${esc(rule.title)}</h4><p>${esc(rule.description)}</p></article>`).join('')}</div></section>`}
      ${progressionAxes ? `<section class="design-prose-chapter"><h3>进度怎样推进</h3>${progressionAxes}</section>` : ''}
      ${resourceLoops ? `<section class="design-prose-chapter"><h3>资源从哪里来、用到哪里</h3>${resourceLoops}</section>` : ''}
      ${stateMatrix ? `<section class="design-prose-chapter"><h3>常见状态、门槛与结果</h3>${stateMatrix}</section>` : ''}
      ${reader.concepts.length ? `<section class="reader-system-chapter"><header><span>词条</span><div><h3>关键概念</h3><p>这些词条可以继续用于索引和跨系统查找。</p></div></header>${readerConceptMarkup(reader)}</section>` : ''}
      ${reader.connections.length ? `<section class="reader-system-chapter"><header><span>链接</span><div><h3>相关内容</h3><p>沿着这些链接继续阅读前置、后续和资源关系。</p></div></header>${readerConnectionMarkup(reader, summary)}</section>` : ''}
    `}
  </section>`

  const interfacesPanel = `<section class="play-tab-panel" data-play-section="interfaces"><header class="content-page-heading"><span>界面与操作</span><h2>玩家会看到哪些页面，能够做什么</h2><p>${counts.screens} 个界面状态按界面族归并；${counts.interactions} 个操作保留操作前画面、位置、动作、即时反馈和目标状态。</p></header>${interfaceStoryMarkup}<div class="screen-family-list is-compact">${families.map((family) => screenFamilyMarkup(family, screensById, tagsByScreen)).join('')}</div><div class="reader-interaction-list is-play-tab">${(bundle.interactions || []).map((item) => readerInteractionMarkup(item, bundle, screenNames, elementNames, evidenceStepRefs, rawBundle.evidence_run_id || '')).join('')}</div></section>`

  const tagsPanel = `<section class="play-tab-panel" data-play-section="screen-tags"><header class="content-page-heading"><span>界面标签</span><h2>用画面识别界面和状态</h2><p>每组标签与对应实机画面一起展示。</p></header><div class="screen-tag-list has-images">${(bundle.screen_tags || []).map((item) => { const screen = screensById.get(item.screen_state_id); const artifactId = screen?.artifact_ids?.find((id) => !id.startsWith('art.video.')); return `<article><div>${partialArtifactImage(artifactId, screen?.name || '界面画面')}</div><strong>${esc(screen?.name || item.screen_state_id)}</strong><div class="tag-row">${(item.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div></article>` }).join('')}</div></section>`

  const recordsPanel = `<section class="play-tab-panel" data-play-section="records"><header class="content-page-heading"><span>游玩记录</span><h2>“${esc(reader.title)}”的实际游玩片段</h2><p>这里单独保存实际游玩过程和原始画面，不混入正文说明。</p></header><div class="record-source-list has-images">${records.map((record, index) => { const resolved = playRecordRefs[record.id] || {}; const displayTitle = readerRecordTitle(reader, index, records.length); return `<article>${recordGalleryMarkup(record, resolved, displayTitle, summary.publication_ready === true)}<span>${esc(playRecordTypeNames[record.source_type] || record.source_type)}</span><h3>${esc(displayTitle)}</h3><p>${esc(record.platform || '')} · ${esc(record.captured_on || '')}</p><dl><div><dt>操作者</dt><dd>${esc(record.operator || '未登记')}</dd></div><div><dt>状态</dt><dd>${esc(humanStatus(record.status))}</dd></div></dl>${playRecordActionsMarkup(record, resolved)}</article>` }).join('') || '<div class="empty-state"><strong>当前没有游玩记录</strong></div>'}</div></section>`

  const feedbackPanel = `<section class="play-tab-panel" data-play-section="feedback"><header class="content-page-heading"><span>社群反馈</span><h2>玩家怎样讨论“${esc(reader.title)}”</h2><p>这里只收录直接讨论这项内容的玩家与媒体材料。</p></header>${feedback.length ? `<div class="feedback-source-list has-images">${feedback.map((item) => `<article>${feedbackPreviewMarkup(item)}<span>${esc(feedbackSourceTypeNames[item.source_type] || item.source_type)}</span><h3>${esc(item.title || item.summary)}</h3><p>${esc(item.summary || '')}</p><div class="tag-row">${(item.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div>${feedbackSourceMarkup(item)}</article>`).join('')}</div>` : '<div class="empty-state"><strong>当前还没有直接相关的玩家反馈</strong></div>'}</section>`

  const tagsOnlyPanel = `<section class="play-tab-panel" data-play-section="tags"><header class="content-page-heading"><span>玩法 tag</span><h2>用于跨游戏检索的玩法标签</h2></header><div class="tag-row is-large">${(summary.play_tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div></section>`

  const demoPanel = `<section class="play-tab-panel" data-play-section="demo"><header class="content-page-heading"><span>玩法 Demo 复现</span><h2>用可操作状态验证反推结果</h2></header>${demos.length ? `<div class="demo-list">${demos.map((demo) => `<article><span>${(demo.covered_surface_ids || []).length} 个界面 · ${(demo.covered_interaction_ids || []).length} 个交互</span><h3>${esc(demo.title)}</h3><p>${esc(demo.description || '')}</p><div class="tag-row">${(demo.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div><a class="button is-primary" href="${esc(demo.url)}">打开交互复现</a></article>`).join('')}</div>` : '<div class="empty-state"><strong>当前玩法尚无可审阅 Demo</strong></div>'}</section>`

  const panels = { design: designPanel, interfaces: interfacesPanel, 'screen-tags': tagsPanel, records: recordsPanel, feedback: feedbackPanel, tags: tagsOnlyPanel, demo: demoPanel }
  workspace.innerHTML = `${readerArticleBreadcrumb(summary)}
  <header class="play-identity"><div class="play-identity-cover">${partialArtifactImage(coverArtifactId, `${summary.play_title}游戏画面`, 'eager')}</div><div class="play-identity-copy"><span>玩法／业务</span><h1>${esc(summary.play_title)}</h1><p>${esc(bundle.scope?.coverage || '')}</p><div class="tag-row">${(summary.play_tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div><small>${counts.screens} 个玩法内界面状态 · ${counts.interactions} 个玩法内交互</small></div></header>
  <nav class="play-content-nav is-tabs" aria-label="玩法内容">${playSections.map(([id, label]) => `<a class="${id === activeSection ? 'is-active' : ''}" href="${playSectionHref(summary, id)}">${esc(label)}</a>`).join('')}</nav>
  ${panels[activeSection]}
  `
  bindPlayGallery()
}

function demoArtifactId(screen) {
  return [...(screen?.artifact_ids || [])].reverse().find((id) => !id.startsWith('art.video.')) || ''
}

function demoInputLabel(interaction, elementsById) {
  const input = interaction?.input || {}
  const target = elementsById.get(input.target)
  if (input.type === 'tap') return input.device_action || `点击${target?.name || input.target || '画面位置'}`
  if (input.type === 'swipe') {
    const direction = Number(input.to?.x) >= Number(input.from?.x) ? '向右' : '向左'
    return `${direction}滑动角色`
  }
  if (input.type === 'pinch') return input.direction === 'out' ? '双指放大角色' : '双指缩小角色'
  if (input.type === 'two_finger_swipe') return '双指向上滑动镜头'
  if (input.type === 'sequence' && input.actions?.length) return input.actions.join(' → ')
  if (input.type === 'open') return `打开${target?.name || input.target || '目标界面'}`
  if (input.type === 'continue') return input.condition ? `继续至${input.condition}` : '继续推进'
  if (input.type === 'back') return '返回上一级画面'
  if (input.type === 'wait') return '等待画面变化'
  return interaction?.immediate_feedback || input.type || '执行交互'
}

function demoTapBounds(interaction, elementsById, bundle) {
  const input = interaction?.input || {}
  if (input.type !== 'tap') return null
  const targetBounds = elementsById.get(input.target)?.bounds
  if (targetBounds) return targetBounds
  const point = input.point
  if (!point) return null
  return { x: point.x - 48, y: point.y - 48, width: 96, height: 96 }
}

async function renderDemo() {
  const query = new URLSearchParams(location.search)
  const bundles = await partialWorkspacePayload()
  const selected = bundles.find((item) => item.path === query.get('draft')) || bundles[0]
  if (!selected) {
    workspace.innerHTML = `${head('DEMO', '没有找到玩法 Demo', '该玩法当前没有可复现内容。', '本地资料库')}`
    return
  }
  const detail = await getJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(selected.path)}`)
  state.currentPartialBundlePath = detail.path
  const bundle = detail.bundle
  state.currentPartialBundlePath = detail.path
  state.currentPartialEvidenceRunId = bundle.evidence_run_id || ''
  state.partialEvidenceStepRefs = new Map(Object.entries(detail.evidence_step_refs || {}))
  const demo = (bundle.demo_reproductions || []).find((item) => item.id === query.get('demo')) || bundle.demo_reproductions?.[0]
  if (!demo) {
    workspace.innerHTML = `${playBreadcrumb(detail.summary, 'Demo 复现')}${head('DEMO', '当前玩法没有 Demo', '返回玩法页查看已有内容。', '本地资料库')}`
    return
  }
  const screensById = new Map((bundle.screen_states || []).map((item) => [item.id, item]))
  const elementsById = new Map((bundle.ui_elements || []).map((item) => [item.id, item]))
  const interactions = (bundle.interactions || []).filter((item) => (demo.covered_interaction_ids || []).includes(item.id))
  const initialStateId = (demo.covered_surface_ids || [])[0]
  const initialScreen = screensById.get(initialStateId)
  const demoViewportWidth = Number(bundle.platform?.viewport?.width) || 9
  const demoViewportHeight = Number(bundle.platform?.viewport?.height) || 16
  const demoDeviceMaximumWidth = demoViewportWidth > demoViewportHeight ? 760 : 470
  let currentStateId = initialStateId
  let history = [{ surfaceId: initialStateId, action: '起始画面' }]

  const evidenceLink = (interaction) => {
    const stepId = interaction?.evidence_step_ids?.[0]
    const ref = state.partialEvidenceStepRefs.get(stepId)
    if (!stepId || !ref?.evidence_run_id) return ''
    return `/game-observatory/studio/evidence?draft=${encodeURIComponent(detail.path)}&run=${encodeURIComponent(ref.evidence_run_id)}&step=${encodeURIComponent(stepId)}`
  }

  const draw = () => {
    const screen = screensById.get(currentStateId)
    const available = interactions.filter((item) => item.from_state_id === currentStateId)
    const artifactId = demoArtifactId(screen)
    const tapZones = available.map((interaction) => {
      const bounds = demoTapBounds(interaction, elementsById, bundle)
      if (!bounds) return ''
      return `<button class="demo-hotspot" type="button" data-demo-interaction="${esc(interaction.id)}" style="${partialBoundsStyle(bounds, bundle)}"><span>${esc(demoInputLabel(interaction, elementsById))}</span></button>`
    }).join('')
    const gestureHints = []
    if (tapZones) gestureHints.push('可直接点击叠在画面上的操作框')
    if (available.some((item) => item.input?.type === 'swipe')) gestureHints.push('可在画面上按对应方向拖动')
    if (available.some((item) => item.input?.type === 'pinch')) gestureHints.push('可双击画面模拟双指放大，其他双指动作从右侧选择')
    if (!gestureHints.length) gestureHints.push('从右侧“当前可做”选择该界面的已复现操作')
    workspace.innerHTML = `${playBreadcrumb(detail.summary, 'Demo 复现')}
      <header class="demo-hero"><div><span>INTERACTIVE REPRODUCTION</span><h1>${esc(demo.title)}</h1><p>${esc(demo.description)}</p></div><a class="button" href="/game-observatory/studio/play?game=${encodeURIComponent(detail.summary.game_slug)}&play=${encodeURIComponent(detail.summary.play_slug)}#play-demo">返回玩法</a></header>
      <div class="demo-workbench">
        <section class="demo-device-column">
          <div class="demo-device" style="width:min(100%, ${demoDeviceMaximumWidth}px)"><div class="demo-screen-stage" data-demo-stage style="aspect-ratio:${demoViewportWidth} / ${demoViewportHeight}">${partialArtifactImage(artifactId, screen?.name || demo.title, 'eager')}${tapZones}<span class="demo-screen-name">${esc(screen?.name || currentStateId)}</span></div></div>
          <p class="demo-gesture-hint">${esc(gestureHints.join('；'))}。</p>
        </section>
        <aside class="demo-state-panel">
          <span>CURRENT INTERFACE</span><h2>${esc(screen?.name || currentStateId)}</h2>
          <ul>${(screen?.visible_facts || []).map((fact) => `<li>${esc(fact)}</li>`).join('')}</ul>
          <div class="demo-actions"><strong>当前可做</strong>${available.map((interaction) => { const label = demoInputLabel(interaction, elementsById); const feedback = interaction.immediate_feedback || ''; return `<article><button type="button" data-demo-interaction="${esc(interaction.id)}"><b>${esc(label)}</b>${feedback && feedback !== label ? `<span>${esc(feedback)}</span>` : ''}</button>${evidenceLink(interaction) ? `<a href="${evidenceLink(interaction)}">查看对应实机步骤</a>` : ''}</article>` }).join('') || '<p>该复现分支已到末端。</p>'}</div>
          <button class="button" type="button" data-demo-restart>重新从“${esc(initialScreen?.name || '起始画面')}”开始</button>
        </aside>
      </div>
      <section class="demo-history"><header><span>INTERACTION ROUTE</span><h2>本次交互路线</h2></header><div class="demo-history-track">${history.map((entry, index) => { const item = screensById.get(entry.surfaceId); return `<article class="${index === history.length - 1 ? 'is-current' : ''}"><div>${partialArtifactImage(demoArtifactId(item), item?.name || entry.surfaceId)}</div><span>${String(index + 1).padStart(2, '0')}</span><strong>${esc(item?.name || entry.surfaceId)}</strong><small>${esc(entry.action)}</small></article>` }).join('')}</div></section>`

    const trigger = (interactionId) => {
      const interaction = available.find((item) => item.id === interactionId)
      if (!interaction?.to_state_id) return
      currentStateId = interaction.to_state_id
      history.push({ surfaceId: currentStateId, action: demoInputLabel(interaction, elementsById) })
      draw()
    }
    document.querySelectorAll('[data-demo-interaction]').forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation()
      trigger(button.dataset.demoInteraction)
    }))
    document.querySelector('[data-demo-restart]')?.addEventListener('click', () => {
      currentStateId = initialStateId
      history = [{ surfaceId: initialStateId, action: '起始画面' }]
      draw()
    })
    const stage = document.querySelector('[data-demo-stage]')
    let pointerStart = null
    stage?.addEventListener('pointerdown', (event) => {
      if (event.target.closest('[data-demo-interaction]')) return
      pointerStart = { x: event.clientX, y: event.clientY }
    })
    stage?.addEventListener('pointerup', (event) => {
      if (!pointerStart || event.target.closest('[data-demo-interaction]')) return
      const deltaX = event.clientX - pointerStart.x
      pointerStart = null
      const swipe = available.find((item) => item.input?.type === 'swipe' && Math.sign(Number(item.input.to?.x) - Number(item.input.from?.x)) === Math.sign(deltaX))
      if (Math.abs(deltaX) > 40 && swipe) trigger(swipe.id)
    })
    stage?.addEventListener('dblclick', () => {
      const pinch = available.find((item) => item.input?.type === 'pinch' && item.input?.direction === 'out')
      if (pinch) trigger(pinch.id)
    })
  }
  draw()
}

async function renderSearch() {
  const query = new URLSearchParams(location.search)
  const gameFilter = query.get('game') || ''
  const playFilter = query.get('play') || ''
  const tagFilter = query.get('tag') || ''
  const term = (query.get('q') || '').trim()
  const normalizedTerm = term.toLocaleLowerCase()
  const bundles = (await partialWorkspacePayload()).filter(readerVisible)
  if (gameFilter && playFilter) {
    scopeSearchNavigation({ game_slug: gameFilter, play_slug: playFilter })
  }
  const matches = bundles.filter((item) => {
    if (gameFilter && ![item.game_id, item.game_slug].includes(gameFilter)) return false
    if (playFilter && ![item.play_id, item.play_slug].includes(playFilter)) return false
    if (tagFilter && !(item.play_tags || []).includes(tagFilter)) return false
    if (!normalizedTerm) return true
    const reader = readerView(item)
    const group = reader.position.group || {}
    const searchable = [
      readerGameTitle(item),
      group.title,
      group.summary,
      reader.title,
      reader.summary,
      reader.player_goal,
      reader.steps,
      reader.concepts,
      reader.rules,
      item.play_tags || [],
    ]
    return JSON.stringify(searchable).toLocaleLowerCase().includes(normalizedTerm)
  })
  const games = [...new Map(bundles.map((item) => [
    item.game_slug,
    { id: item.game_id, slug: item.game_slug, title: readerGameTitle(item) },
  ])).values()].sort((left, right) => left.title.localeCompare(right.title, 'zh-CN'))
  const plays = bundles
    .filter((item) => !gameFilter || [item.game_id, item.game_slug].includes(gameFilter))
    .sort((left, right) => readerTitle(left).localeCompare(readerTitle(right), 'zh-CN'))
  const tags = [...new Set(bundles.flatMap((item) => item.play_tags || []))].sort((left, right) => left.localeCompare(right, 'zh-CN'))
  const scopeLabel = playFilter ? '搜索本文' : gameFilter ? '搜索本游戏' : '搜索全部内容'
  const scopeCopy = playFilter
    ? '在当前文章的说明、玩家目标、关键概念和规则中查找。'
    : gameFilter
      ? '查找本游戏已经整理的系统、机制、操作、界面和规则。'
      : '跨游戏查找系统、机制、操作、玩家目标和规则。'
  const options = (values, selected, emptyLabel) => `<option value="">${esc(emptyLabel)}</option>${values.map((value) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(value)}</option>`).join('')}`
  const gameOptions = `<option value="">全部游戏</option>${games.map((game) => `<option value="${esc(game.slug || game.id)}"${[game.id, game.slug].includes(gameFilter) ? ' selected' : ''}>${esc(game.title)}</option>`).join('')}`
  const playOptions = `<option value="">全部文章</option>${plays.map((item) => `<option value="${esc(item.play_slug || item.play_id)}"${[item.play_id, item.play_slug].includes(playFilter) ? ' selected' : ''}>${esc(readerGameTitle(item))} · ${esc(readerView(item).kind)} · ${esc(readerTitle(item))}</option>`).join('')}`
  workspace.innerHTML = `<header class="game-detail-head"><span>搜索资料库</span><h1>搜索</h1><p>${esc(scopeCopy)}</p></header>
  <form class="library-search search-filter-form" action="/game-observatory/studio/search"><label class="search-query"><span>${scopeLabel}</span><input type="search" name="q" value="${esc(term)}" placeholder="输入系统、机制、操作、界面或规则"></label><label><span>游戏</span><select name="game">${gameOptions}</select></label><label><span>文章</span><select name="play">${playOptions}</select></label><label><span>标签</span><select name="tag">${options(tags, tagFilter, '全部标签')}</select></label><button class="button is-primary" type="submit">搜索</button></form>
  <div class="search-summary"><strong>${matches.length}</strong><span>${term ? `篇内容包含“${esc(term)}”` : (gameFilter || playFilter || tagFilter) ? '篇筛选结果' : '篇可阅读内容'}</span></div>
  <section class="search-results">${matches.map((item) => { const reader = readerView(item); const href = `/game-observatory/studio/play?game=${encodeURIComponent(item.game_slug)}&play=${encodeURIComponent(item.play_slug)}`; return `<a href="${href}">${item.cover_artifact_id ? `<div class="search-result-image">${partialArtifactImage(item.cover_artifact_id, reader.title)}</div>` : ''}<span>${esc(readerGameTitle(item))} · ${esc(reader.kind)}</span><h2>${esc(reader.title)}</h2><p>${esc(reader.summary)}</p><div class="tag-row">${playTagMarkup(item.play_tags, item.play_tag_details)}</div></a>` }).join('') || '<div class="empty-state"><strong>没有找到相关内容</strong><span>可以换一个关键词、游戏、内容类型或标签。</span></div>'}</section>`
  document.querySelector('.search-filter-form select[name="game"]')?.addEventListener('change', () => {
    const playSelect = document.querySelector('.search-filter-form select[name="play"]')
    if (playSelect) playSelect.value = ''
  })
}

function sectionCount(report, key) {
  const spec = report?.design_spec || {}
  const value = spec[key] ?? report?.[key]
  return Array.isArray(value) ? value.length : value ? 1 : 0
}

function referenceChips(record) {
  const sources = record?.source_ids || []
  const artifacts = record?.artifact_ids || []
  const run = record?.run_id ? [record.run_id] : []
  const values = [
    ...sources.map((value) => ['来源', value]),
    ...artifacts.map((value) => ['证据', value]),
    ...run.map((value) => ['运行', value]),
  ]
  if (!values.length) return ''
  return `<div class="reference-chips">${values.map(([kind, value]) => `<span title="${esc(value)}">${kind} · ${esc(value.split('.').slice(-2).join('.'))}</span>`).join('')}</div>`
}

function simpleList(values) {
  return values?.length ? `<ul class="plain-list">${values.map((value) => `<li>${esc(value)}</li>`).join('')}</ul>` : ''
}

function factStatements(statements) {
  const facts = (statements || []).filter((item) => item.kind !== 'analyst_interpretation')
  if (!facts.length) return '<p class="muted-copy">当前对象没有可进入事实区的条目。</p>'
  return facts.map((item) => `<article class="fact-statement"><strong>${esc(item.title)}</strong><p>${esc(item.statement)}</p>${referenceChips(item)}</article>`).join('')
}

function normalizedBounds(bounds) {
  if (!bounds) return ''
  const values = [bounds.x, bounds.y, bounds.width, bounds.height]
  if (values.some((value) => typeof value !== 'number')) return ''
  const scale = values.every((value) => value <= 1) ? 100 : 1
  return `left:${bounds.x * scale}%;top:${bounds.y * scale}%;width:${bounds.width * scale}%;height:${bounds.height * scale}%`
}

function surfaceMarkup(surface) {
  const elements = surface.elements || []
  const bounded = elements.filter((item) => normalizedBounds(item.bounds))
  const artifactId = surface.artifact_ids?.[0]
  return `<article class="surface-spec" id="${esc(surface.id)}">
    <header><div><span>${esc(surface.kind || 'surface')}</span><h3>${esc(surface.title)}</h3></div><small>${elements.length} 个元素</small></header>
    <p>${esc(surface.description || '')}</p>
    <div class="surface-inspector">
      <div class="surface-plate">${artifactId ? `<img src="/api/game-observatory/artifacts/${encodeURIComponent(artifactId)}" alt="${esc(surface.title)}完整画面" loading="lazy"><div class="surface-overlay">${bounded.map((element, index) => `<span class="surface-box" data-element-box="${esc(element.id)}" style="${normalizedBounds(element.bounds)}"><b>${index + 1}</b></span>`).join('')}</div>` : '<div class="empty-state"><strong>暂无画面</strong><span>结构条目保留，等待对应证据 artifact。</span></div>'}</div>
      <div class="element-ledger">${elements.map((element, index) => `<div class="element-row" data-element-row="${esc(element.id)}"><b>${index + 1}</b><div><strong>${esc(element.label || element.text || element.role)}</strong><span>${esc(element.role)}${element.actions?.length ? ` · ${element.actions.map(esc).join(' / ')}` : ''}</span></div></div>`).join('') || '<p class="muted-copy">尚未登记界面元素。</p>'}</div>
    </div>
    ${referenceChips(surface)}
  </article>`
}

function coreLoopMarkup(coreLoop) {
  if (!coreLoop) return '<p class="muted-copy">核心循环尚未形成结构化对象。</p>'
  return `<article class="loop-spec"><header><h3>${esc(coreLoop.title)}</h3><span>${esc(coreLoop.cadence || '')}</span></header>
    <div class="condition-grid"><div><strong>进入条件</strong>${simpleList(coreLoop.entry_conditions)}</div><div><strong>完成条件</strong>${simpleList(coreLoop.exit_conditions)}</div></div>
    <ol class="loop-steps">${coreLoop.steps.map((step) => `<li><span>${esc(step.title)}</span><div><strong>${esc(step.player_action)}</strong><p>${esc(step.system_response)}</p><small>${esc(step.state_before)} → ${esc(step.state_after)}</small>${referenceChips(step)}</div></li>`).join('')}</ol>
  </article>`
}

function architectureMarkup(architecture, surfaces) {
  if (!architecture) return '<p class="muted-copy">界面关系尚未登记。</p>'
  const names = new Map((surfaces || []).map((item) => [item.id, item.title]))
  return `<div class="edge-list">${architecture.edges.map((edge) => `<article class="edge-card"><span>${esc(names.get(edge.from_surface_id) || edge.from_surface_id)}</span><strong>${esc(edge.trigger)}</strong><span>${esc(names.get(edge.to_surface_id) || edge.to_surface_id)}</span>${edge.condition ? `<small>${esc(edge.condition)}</small>` : ''}</article>`).join('') || '<p class="muted-copy">当前只有根界面，尚无跨界面转换。</p>'}</div>${simpleList(architecture.notes)}`
}

function interactionMarkup(interactions, fallbackFlow) {
  if (interactions?.length) return interactions.map((interaction) => `<article class="interaction-spec"><header><div><span>触发 · ${esc(interaction.trigger)}</span><h3>${esc(interaction.title)}</h3></div></header><div class="interaction-steps">${interaction.steps.map((step) => `<div class="interaction-step"><b>${step.order}</b><div><small>${step.actor === 'player' ? '玩家' : '系统'}</small><strong>${esc(step.action)}</strong><p>${esc(step.response || '')}</p></div></div>`).join('')}</div><div class="condition-grid"><div><strong>前置条件</strong>${simpleList(interaction.preconditions)}</div><div><strong>完成状态</strong>${simpleList(interaction.postconditions)}</div></div>${interaction.branches?.length ? `<details class="tech"><summary>分支与例外</summary>${simpleList(interaction.branches)}</details>` : ''}${referenceChips(interaction)}</article>`).join('')
  return (fallbackFlow || []).map((node) => `<article class="flow-card"><div><small>之前</small><strong>${esc(node.state_before)}</strong></div><div><small>玩家操作</small><strong>${esc(node.action)}</strong><p>${esc(node.description)}</p></div><div><small>之后</small><strong>${esc(node.state_after)}</strong></div>${referenceChips(node)}</article>`).join('') || '<p class="muted-copy">交互步骤尚未登记。</p>'
}

function stateMatrixMarkup(matrices) {
  return (matrices || []).map((matrix) => `<article class="matrix-spec"><header><h3>${esc(matrix.title)}</h3><span>${matrix.dimensions.map(esc).join(' · ')}</span></header><div class="matrix-cases">${matrix.cases.map((item) => `<div><strong>${esc(item.state)}</strong><span>${esc(item.condition)}</span><p>${esc(item.content || '')}</p><small>${item.next_state ? `下一状态 · ${esc(item.next_state)}` : '终止状态'}</small></div>`).join('')}</div></article>`).join('') || '<p class="muted-copy">状态分支尚未登记。</p>'
}

function mechanismMarkup(mechanisms) {
  return (mechanisms || []).map((item) => `<article class="mechanism-card"><header><div><span>${esc(item.representation)}</span><h3>${esc(item.title)}</h3></div></header><p>${esc(item.description)}</p>${item.code ? `<pre>${esc(item.code)}</pre>` : ''}${referenceChips(item)}</article>`).join('') || '<p class="muted-copy">机制规则尚未登记。</p>'
}

function resourceMarkup(resources) {
  return (resources || []).map((item) => `<article class="resource-card"><span>${esc(item.role)}</span><h3>${esc(item.resource)}</h3><p>${esc(item.description)}</p>${referenceChips(item)}</article>`).join('') || '<p class="muted-copy">资源关系尚未登记。</p>'
}

function feedbackMarkup(items) {
  return (items || []).map((item) => `<article class="feedback-card"><header><h3>${esc(item.title)}</h3><span>${item.channels.map(esc).join(' · ')}</span></header><p><strong>触发：</strong>${esc(item.trigger)}</p><div class="condition-grid"><div><strong>成功反馈</strong><p>${esc(item.success_behavior)}</p></div><div><strong>失败反馈</strong><p>${esc(item.failure_behavior)}</p></div></div><small>${esc(item.timing)}</small>${referenceChips(item)}</article>`).join('') || '<p class="muted-copy">反馈规格尚未登记。</p>'
}

function failureMarkup(items) {
  return (items || []).map((item) => `<article class="failure-card"><h3>${esc(item.title)}</h3><div class="failure-grid"><div><small>发生条件</small><p>${esc(item.failure_condition)}</p></div><div><small>玩家看到</small><p>${esc(item.visible_behavior)}</p></div><div><small>保留内容</small><p>${esc(item.retained_state)}</p></div><div><small>恢复操作</small><p>${esc(item.recovery_action)}</p></div></div>${referenceChips(item)}</article>`).join('') || '<p class="muted-copy">失败与恢复尚未登记。</p>'
}

function bindElementHighlights() {
  document.querySelectorAll('[data-element-row]').forEach((row) => {
    const box = document.querySelector(`[data-element-box="${CSS.escape(row.dataset.elementRow)}"]`)
    if (!box) return
    row.addEventListener('mouseenter', () => box.classList.add('is-active'))
    row.addEventListener('mouseleave', () => box.classList.remove('is-active'))
  })
}

function partialEvidenceAccess(record) {
  const evidenceStepIds = record?.evidence_step_ids || []
  if (!evidenceStepIds.length) return ''
  const links = evidenceStepIds.map((stepId, index) => {
      const ref = state.partialEvidenceStepRefs.get(stepId)
      const runId = ref?.evidence_run_id || state.currentPartialEvidenceRunId
      if (!runId) return ''
      const draft = state.currentPartialBundlePath
        ? `draft=${encodeURIComponent(state.currentPartialBundlePath)}&`
        : ''
      return `<a href="/game-observatory/studio/evidence?${draft}run=${encodeURIComponent(runId)}&step=${encodeURIComponent(stepId)}">查看实录${evidenceStepIds.length > 1 ? ` ${index + 1}` : ''}</a>`
    }).filter(Boolean).join('')
  return links ? `<div class="partial-evidence-links" aria-label="事实依据"><span>事实依据</span>${links}</div>` : ''
}

function partialArtifactImage(artifactId, alt, loading = 'lazy') {
  if (!artifactId) return '<div class="empty-state"><strong>暂无证据画面</strong></div>'
  return `<img src="/api/game-observatory/artifacts/${encodeURIComponent(artifactId)}" alt="${esc(alt)}" loading="${loading}" decoding="async">`
}

function partialBoundsStyle(bounds, bundle) {
  const viewport = bundle?.platform?.viewport || {}
  const width = Number(viewport.width)
  const height = Number(viewport.height)
  if (!bounds || !width || !height) return ''
  const values = [bounds.x, bounds.y, bounds.width, bounds.height].map(Number)
  if (values.some((value) => !Number.isFinite(value))) return ''
  return `left:${values[0] / width * 100}%;top:${values[1] / height * 100}%;width:${values[2] / width * 100}%;height:${values[3] / height * 100}%`
}

function partialViewportFrameStyle(bundle) {
  const viewport = bundle?.platform?.viewport || {}
  const width = Number(viewport.width)
  const height = Number(viewport.height)
  if (!width || !height) return ''
  return `aspect-ratio:${width} / ${height};width:min(100%, ${Math.round(360 * width / height)}px)`
}

function readerActionOverlay(interaction, bundle) {
  const input = interaction?.input || {}
  const viewport = bundle?.platform?.viewport || {}
  const width = Number(viewport.width)
  const height = Number(viewport.height)
  if (!width || !height) return ''
  const finitePoint = (point) => point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))
  const shapes = []
  const targetIds = input.targets || (input.target ? [input.target] : [])
  const elements = new Map((bundle.ui_elements || []).map((item) => [item.id, item]))
  targetIds.forEach((targetId) => {
    const bounds = elements.get(targetId)?.bounds
    if (!bounds) return
    const values = [bounds.x, bounds.y, bounds.width, bounds.height].map(Number)
    if (values.some((value) => !Number.isFinite(value))) return
    shapes.push(`<rect class="reader-action-target" x="${values[0]}" y="${values[1]}" width="${values[2]}" height="${values[3]}" rx="18" />`)
  })
  if (finitePoint(input.point)) {
    shapes.push(`<circle class="reader-action-tap-ring" cx="${Number(input.point.x)}" cy="${Number(input.point.y)}" r="34" /><circle class="reader-action-tap" cx="${Number(input.point.x)}" cy="${Number(input.point.y)}" r="10" />`)
  }
  if (finitePoint(input.from) && finitePoint(input.to)) {
    const fromX = Number(input.from.x)
    const fromY = Number(input.from.y)
    const toX = Number(input.to.x)
    const toY = Number(input.to.y)
    shapes.push(`<line class="reader-action-path" x1="${fromX}" y1="${fromY}" x2="${toX}" y2="${toY}" /><circle class="reader-action-start" cx="${fromX}" cy="${fromY}" r="13" /><circle class="reader-action-end" cx="${toX}" cy="${toY}" r="22" />`)
    if (input.type === 'two_finger_swipe' && finitePoint(input.second_finger_offset)) {
      const offsetX = Number(input.second_finger_offset.x)
      const offsetY = Number(input.second_finger_offset.y)
      shapes.push(`<line class="reader-action-path is-secondary" x1="${fromX + offsetX}" y1="${fromY + offsetY}" x2="${toX + offsetX}" y2="${toY + offsetY}" /><circle class="reader-action-start" cx="${fromX + offsetX}" cy="${fromY + offsetY}" r="13" /><circle class="reader-action-end" cx="${toX + offsetX}" cy="${toY + offsetY}" r="22" />`)
    }
  }
  if (finitePoint(input.center)) {
    shapes.push(`<circle class="reader-action-center" cx="${Number(input.center.x)}" cy="${Number(input.center.y)}" r="42" />`)
  }
  if (!shapes.length) return ''
  return `<svg class="reader-action-overlay" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="本次操作在原始画面中的位置">${shapes.join('')}</svg>`
}

function partialScreenMarkup(screen, bundle, elementNames) {
  const elements = (bundle.ui_elements || []).filter((item) => (
    item.screen_state_ids || []
  ).includes(screen.id))
  const bounded = elements.filter((item) => partialBoundsStyle(item.bounds, bundle))
  const artifactId = screen.artifact_ids?.find((value) => !value.startsWith('art.video.'))
  return `<article class="partial-screen-card" id="${esc(screen.id)}" data-partial-screen="${esc(screen.id)}">
    <header><div><span>SCREEN</span><h3>${esc(screen.name)}</h3></div><small>${elements.length} 个已登记元素</small></header>
    <div class="partial-screen-layout">
      <div class="partial-screen-visual">
        ${partialArtifactImage(artifactId, `${screen.name}证据画面`)}
        <div class="partial-screen-overlay">${bounded.map((element, index) => `<span class="partial-bounds" data-partial-box="${esc(element.id)}" style="${partialBoundsStyle(element.bounds, bundle)}"><b>${index + 1}</b></span>`).join('')}</div>
      </div>
      <div class="partial-screen-copy">
        <h4>画面中可见</h4>${simpleList(screen.visible_facts)}
        <div class="partial-element-list">${elements.map((element, index) => `<div class="partial-element-row" data-partial-element="${esc(element.id)}"><b>${index + 1}</b><div><strong>${esc(elementNames.get(element.id) || element.name)}</strong><span>${esc(element.role || '')}</span></div></div>`).join('') || '<p class="muted-copy">当前画面没有已量测元素。</p>'}</div>
      </div>
    </div>
    ${partialEvidenceAccess(screen)}
  </article>`
}

function partialElementMarkup(element, screenNames) {
  const screens = (element.screen_state_ids || []).map((id) => screenNames.get(id) || '未命名界面')
  return `<article class="partial-record-card" id="${esc(element.id)}"><span>界面元素</span><h3>${esc(element.name)}</h3><p>${esc(element.role || '')}</p>
    <small>${screens.map(esc).join(' · ') || '尚未关联界面'}</small>
    ${element.bounds ? `<div class="coordinate-readout">位置 ${element.bounds.x}, ${element.bounds.y} · 大小 ${element.bounds.width} × ${element.bounds.height}</div>` : ''}
    ${partialEvidenceAccess(element)}
  </article>`
}

const partialActionNames = {
  tap: '点击', swipe: '滑动', pinch: '双指缩放', two_finger_swipe: '双指平移',
  back: '返回', wait: '等待', reset: '重置',
}

function partialInteractionMarkup(interaction, screenNames, elementNames) {
  const input = interaction.input || {}
  const targetIds = input.targets || (input.target ? [input.target] : [])
  const targets = targetIds.map((id) => elementNames.get(id) || '界面指定位置')
  const visualArtifacts = (interaction.artifact_ids || []).filter((id) => !id.startsWith('art.video.')).slice(0, 2)
  return `<article class="partial-interaction-card" id="${esc(interaction.id)}">
    <div class="partial-interaction-route"><span>${esc(screenNames.get(interaction.from_state_id) || '起始画面')}</span><b>${esc(partialActionNames[input.type] || input.type || '操作')}</b><span>${esc(screenNames.get(interaction.to_state_id) || '到达画面')}</span></div>
    <div class="partial-action-copy"><small>玩家操作</small><h3>${esc(partialActionNames[input.type] || input.type || '操作')}${targets.length ? ` · ${targets.map(esc).join(' / ')}` : ''}</h3><small>画面反馈</small><p>${esc(interaction.immediate_feedback || '当前证据没有登记可见反馈。')}</p></div>
    ${visualArtifacts.length ? `<div class="partial-interaction-images">${visualArtifacts.map((id, index) => partialArtifactImage(id, `${index === 0 ? '操作前后证据' : '到达状态证据'}`)).join('')}</div>` : ''}
    ${partialEvidenceAccess(interaction)}
  </article>`
}

function partialTransitionMarkup(transition, screenNames, interactionNames) {
  const via = (transition.via_interaction_ids || []).map((id) => interactionNames.get(id) || '已登记交互')
  return `<article class="partial-transition-card" id="${esc(transition.id)}"><span>${esc(screenNames.get(transition.from_state_id) || '起始状态')}</span><div><small>${via.map(esc).join(' / ')}</small><b>→</b></div><span>${esc(screenNames.get(transition.to_state_id) || '到达状态')}</span>${partialEvidenceAccess(transition)}</article>`
}

function partialMechanicMarkup(mechanic, screenNames) {
  const screens = (mechanic.screen_state_ids || []).map((id) => screenNames.get(id) || '未命名界面')
  const artifactId = mechanic.artifact_ids?.find((id) => !id.startsWith('art.video.'))
  return `<article class="partial-mechanic-card" id="${esc(mechanic.id)}"><div>${partialArtifactImage(artifactId, '机制证据画面')}</div><div><span>${screens.map(esc).join(' · ')}</span><p>${esc(mechanic.observed_rule)}</p>${partialEvidenceAccess(mechanic)}</div></article>`
}

function partialResourceMarkup(resource, bundle, screenNames) {
  const artifactId = resource.artifact_ids?.find((id) => !id.startsWith('art.video.'))
  const bounds = partialBoundsStyle(resource.bounds, bundle)
  const displayedValue = Array.isArray(resource.displayed_value)
    ? resource.displayed_value.join(' · ')
    : resource.displayed_value
  return `<article class="partial-resource-card" id="${esc(resource.id)}"><div class="partial-resource-visual">${partialArtifactImage(artifactId, `${resource.label}证据画面`)}${bounds ? `<span class="partial-bounds is-resource" style="${bounds}"></span>` : ''}</div><div><span>${esc(screenNames.get(resource.screen_state_id) || '界面数值')}</span><h3>${esc(resource.label)}</h3><strong>${esc(displayedValue)}</strong>${partialEvidenceAccess(resource)}</div></article>`
}

function partialGapMarkup(gap) {
  const artifactId = gap.artifact_ids?.find((id) => !id.startsWith('art.video.'))
  return `<article class="partial-gap-card" id="${esc(gap.id)}"><div>${partialArtifactImage(artifactId, `${gap.subject}现有证据`)}</div><div><span>尚未确认</span><h3>${esc(gap.subject)}</h3><p>${esc(gap.reason)}</p><small>仍需补充</small><p>${esc(gap.required_evidence)}</p>${partialEvidenceAccess(gap)}</div></article>`
}

function partialReferenceValues(value, key, result = new Set()) {
  if (Array.isArray(value)) {
    value.forEach((item) => partialReferenceValues(item, key, result))
  } else if (value && typeof value === 'object') {
    Object.entries(value).forEach(([name, item]) => {
      if (name === key && Array.isArray(item)) item.forEach((id) => result.add(id))
      else partialReferenceValues(item, key, result)
    })
  }
  return result
}

function partialObjectCollections(bundle) {
  return [
    ['界面', bundle.screen_states || [], (item) => item.name],
    ['界面元素', bundle.ui_elements || [], (item) => item.name],
    ['交互', bundle.interactions || [], (item) => item.immediate_feedback || item.id],
    ['状态转换', bundle.state_transitions || [], (item) => item.id],
    ['可见机制', bundle.visible_mechanics || [], (item) => item.observed_rule],
    ['资源显示', bundle.resource_displays || [], (item) => item.label],
    ['证据缺口', bundle.evidence_gaps || [], (item) => item.subject],
  ]
}

function partialEvidenceReferenceIndex(detail) {
  const result = new Map()
  const bundle = detail?.bundle || {}
  partialObjectCollections(bundle).forEach(([kind, values, labelFor]) => {
    values.forEach((item) => {
      if (!item.id) return
      ;(item.evidence_step_ids || []).forEach((stepId) => {
        const entries = result.get(stepId) || []
        entries.push({
          kind,
          label: labelFor(item) || item.id,
          href: `/game-observatory/studio/spec?draft=${encodeURIComponent(detail.path)}#${encodeURIComponent(item.id)}`,
        })
        result.set(stepId, entries)
      })
    })
  })
  return result
}

function specSelectionOptions(partialBundles, specs, selectedKind, selectedValue) {
  const partialOptions = partialBundles.map((item) => `<option value="draft:${esc(item.path)}" ${selectedKind === 'draft' && item.path === selectedValue ? 'selected' : ''}>${esc(item.subject)} · 局部草稿</option>`).join('')
  const strictOptions = specs.map((item) => `<option value="report:${esc(item.report.id)}" ${selectedKind === 'report' && item.report.id === selectedValue ? 'selected' : ''}>${esc(item.report.system_title)} · ${humanStatus(item.report.status)}</option>`).join('')
  return `${partialOptions ? `<optgroup label="局部事实草稿">${partialOptions}</optgroup>` : ''}${strictOptions ? `<optgroup label="结构化事实案">${strictOptions}</optgroup>` : ''}`
}

function bindSpecSelection() {
  document.querySelector('[data-spec-select]')?.addEventListener('change', (event) => {
    const value = event.target.value
    const separator = value.indexOf(':')
    const kind = value.slice(0, separator)
    const id = value.slice(separator + 1)
    location.href = kind === 'draft'
      ? `/game-observatory/studio/spec?draft=${encodeURIComponent(id)}`
      : `/game-observatory/studio/spec?report=${encodeURIComponent(id)}`
  })
}

function bindPartialHighlights() {
  document.querySelectorAll('[data-partial-screen]').forEach((screen) => {
    screen.querySelectorAll('[data-partial-element]').forEach((row) => {
      const box = screen.querySelector(`[data-partial-box="${CSS.escape(row.dataset.partialElement)}"]`)
      if (!box) return
      row.addEventListener('mouseenter', () => box.classList.add('is-active'))
      row.addEventListener('mouseleave', () => box.classList.remove('is-active'))
    })
  })
}

function renderPartialFactBundle(detail, allSpecs) {
  const bundle = strictPlayBundleView(detail.bundle)
  const summary = detail.summary
  const screenNames = new Map((bundle.screen_states || []).map((item) => [item.id, item.name]))
  const elementNames = new Map((bundle.ui_elements || []).map((item) => [item.id, item.name]))
  const interactionNames = new Map((bundle.interactions || []).map((item) => {
    const input = item.input || {}
    return [item.id, `${partialActionNames[input.type] || input.type || '操作'} · ${item.immediate_feedback || ''}`]
  }))
  const options = specSelectionOptions(state.partialBundles, allSpecs, 'draft', detail.path)
  const counts = {
    screen_states: bundle.screen_states?.length || 0,
    ui_elements: bundle.ui_elements?.length || 0,
    interactions: bundle.interactions?.length || 0,
    state_transitions: bundle.state_transitions?.length || 0,
    visible_mechanics: bundle.visible_mechanics?.length || 0,
    resource_displays: bundle.resource_displays?.length || 0,
  }
  const viewport = bundle.platform?.viewport || {}
  const buildScope = bundle.build?.build_scope_id || ''
  const buildParts = buildScope.match(/(\d+\.\d+\.\d+).*versionCode-(\d+)/i)
  const buildLabel = buildParts
    ? `版本 ${buildParts[1]} · Android build ${buildParts[2]}`
    : buildScope || '版本未登记'
  const playUrl = `/game-observatory/studio/play?game=${encodeURIComponent(summary.game_slug)}&play=${encodeURIComponent(summary.play_slug)}`
  workspace.innerHTML = `<nav class="content-breadcrumb"><a href="/game-observatory/studio/">游戏</a><b>→</b><a href="/game-observatory/studio/game?game=${encodeURIComponent(summary.game_slug)}">${esc(summary.game_title)}</a><b>→</b><a href="${playUrl}">${esc(summary.play_title)}</a><b>→</b><span>玩法设计文档</span></nav>
  ${head(
    'PLAY DESIGN DOCUMENT',
    `${summary.play_title} · 玩法设计文档`,
    bundle.scope?.subject || summary.subject,
    summary.game_title,
  )}
  <div class="toolbar"><label>工作对象<select data-spec-select>${options}</select></label><a class="button" href="${playUrl}">返回玩法</a><a class="button" href="/game-observatory/studio/reader?draft=${encodeURIComponent(detail.path)}">打开界面与交互路线</a></div>
  <div class="spec-board">
    <nav class="spec-outline" aria-label="局部事实案结构">
      <a href="#scope">系统范围</a><a href="#screens">界面状态 · ${counts.screen_states || 0}</a><a href="#ui-elements">界面元素 · ${counts.ui_elements || 0}</a>
      <a href="#interactions">交互 · ${counts.interactions || 0}</a><a href="#transitions">状态转换 · ${counts.state_transitions || 0}</a>
      <a href="#mechanics">可见机制 · ${counts.visible_mechanics || 0}</a><a href="#resources">资源显示 · ${counts.resource_displays || 0}</a>
    </nav>
    <div class="spec-canvas partial-fact-canvas">
      <section class="spec-section" id="scope"><span>SYSTEM SCOPE</span><h2>${esc(bundle.scope?.subject || summary.subject)}</h2><p>${esc(bundle.scope?.coverage || '')}</p>
        <div class="contract-grid"><div class="contract-item"><strong>${esc(summary.game_title)}</strong><small>${esc(bundle.platform?.kind || '')} · ${esc(bundle.language?.observed_ui_language || '')} · ${viewport.width || '?'} × ${viewport.height || '?'}</small></div><div class="contract-item"><strong>${esc(buildLabel)}</strong><small>观测日期 ${esc(bundle.build?.observed_on || '未登记')}</small></div></div>
      </section>
      <section class="spec-section" id="screens"><span>SCREEN</span><h2>界面状态 · ${counts.screen_states || 0}</h2><div class="partial-screen-list">${(bundle.screen_states || []).map((item) => partialScreenMarkup(item, bundle, elementNames)).join('')}</div></section>
      <section class="spec-section" id="ui-elements"><span>UI ELEMENT</span><h2>界面元素 · ${counts.ui_elements || 0}</h2><div class="partial-record-grid">${(bundle.ui_elements || []).map((item) => partialElementMarkup(item, screenNames)).join('')}</div></section>
      <section class="spec-section" id="interactions"><span>INTERACTION</span><h2>玩家操作与画面响应 · ${counts.interactions || 0}</h2><div class="partial-interaction-list">${(bundle.interactions || []).map((item) => partialInteractionMarkup(item, screenNames, elementNames)).join('')}</div></section>
      <section class="spec-section" id="transitions"><span>STATE TRANSITION</span><h2>界面状态转换 · ${counts.state_transitions || 0}</h2><div class="partial-transition-list">${(bundle.state_transitions || []).map((item) => partialTransitionMarkup(item, screenNames, interactionNames)).join('')}</div></section>
      <section class="spec-section" id="mechanics"><span>VISIBLE MECHANIC</span><h2>可见机制 · ${counts.visible_mechanics || 0}</h2><div class="partial-mechanic-list">${(bundle.visible_mechanics || []).map((item) => partialMechanicMarkup(item, screenNames)).join('')}</div></section>
      <section class="spec-section" id="resources"><span>RESOURCE DISPLAY</span><h2>界面中的资源与数值 · ${counts.resource_displays || 0}</h2><div class="partial-resource-list">${(bundle.resource_displays || []).map((item) => partialResourceMarkup(item, bundle, screenNames)).join('')}</div></section>
    </div>
  </div></details>`

  document.querySelector('[data-ai-environment]')?.addEventListener('change', (event) => {
    const next = new URL(location.href)
    next.searchParams.set('environment', event.currentTarget.value)
    location.href = next.toString()
  })
  bindSpecSelection()
  bindPartialHighlights()
}

function readerEvidenceLinks(record, evidenceStepRefs, fallbackRunId = '') {
  const stepIds = record?.evidence_step_ids || []
  if (!stepIds.length) return ''
  return `<details class="reader-source-drawer"><summary>查看实录来源</summary><div>${stepIds.map((stepId) => {
    const ref = evidenceStepRefs.get(stepId)
    const runId = ref?.evidence_run_id || fallbackRunId
    if (!runId) return ''
    const index = ref?.step_index
    const draft = state.currentPartialBundlePath
      ? `draft=${encodeURIComponent(state.currentPartialBundlePath)}&`
      : ''
    return `<a href="/game-observatory/studio/evidence?${draft}run=${encodeURIComponent(runId)}&step=${encodeURIComponent(stepId)}">${index ? `游玩步骤 ${index}` : '对应游玩步骤'}</a>`
  }).join('')}</div></details>`
}

function readerScreenMarkup(screen, bundle, elementNames, evidenceStepRefs, fallbackRunId) {
  const elements = (bundle.ui_elements || []).filter((item) => (
    item.screen_state_ids || []
  ).includes(screen.id))
  const bounded = elements.filter((item) => partialBoundsStyle(item.bounds, bundle))
  const artifactId = screen.artifact_ids?.find((value) => !value.startsWith('art.video.'))
  return `<article class="reader-screen" id="reader-${esc(screen.id)}">
    <div class="reader-screen-visual">${partialArtifactImage(artifactId, `${screen.name}完整游戏画面`)}<div class="partial-screen-overlay">${bounded.map((element, index) => `<span class="partial-bounds" data-reader-box="${esc(element.id)}" style="${partialBoundsStyle(element.bounds, bundle)}"><b>${index + 1}</b></span>`).join('')}</div></div>
    <div class="reader-screen-copy"><span>界面</span><h3>${esc(screen.name)}</h3>${simpleList(screen.visible_facts)}<div class="reader-element-list">${elements.map((element, index) => `<div data-reader-element="${esc(element.id)}"><b>${index + 1}</b><p><strong>${esc(elementNames.get(element.id) || element.name)}</strong><small>${esc(element.role || '')}</small></p></div>`).join('')}</div>${readerEvidenceLinks(screen, evidenceStepRefs, fallbackRunId)}</div>
  </article>`
}

function readerInteractionMarkup(interaction, bundle, screenNames, elementNames, evidenceStepRefs, fallbackRunId) {
  const input = interaction.input || {}
  const targetIds = input.targets || (input.target ? [input.target] : [])
  const targets = targetIds.map((id) => elementNames.get(id) || '界面指定位置')
  const images = (interaction.artifact_ids || []).filter((id) => !id.startsWith('art.video.')).slice(0, 2)
  const actionOverlay = readerActionOverlay(interaction, bundle)
  return `<article class="reader-interaction" id="reader-${esc(interaction.id)}">
    <div class="reader-route"><span>${esc(screenNames.get(interaction.from_state_id) || '起始画面')}</span><b>${esc(partialActionNames[input.type] || input.type || '操作')}${targets.length ? ` · ${targets.map(esc).join(' / ')}` : ''}</b><span>${esc(screenNames.get(interaction.to_state_id) || '到达画面')}</span></div>
    ${images.length ? `<div class="reader-interaction-images">${images.map((id, index) => `<figure><div class="reader-interaction-visual" style="${partialViewportFrameStyle(bundle)}">${partialArtifactImage(id, index === 0 ? '操作前游戏画面' : '操作后游戏画面')}${index === 0 ? actionOverlay : ''}</div><figcaption>${index === 0 ? '操作前 · 已标出操作位置' : '操作后'}</figcaption></figure>`).join('')}</div>` : ''}
    <div class="reader-feedback"><span>操作结果</span><p>${esc(readerFacingCopy(interaction.immediate_feedback, '操作后页面状态已更新。'))}</p></div>
  </article>`
}

function bindReaderHighlights() {
  document.querySelectorAll('.reader-screen').forEach((screen) => {
    screen.querySelectorAll('[data-reader-element]').forEach((row) => {
      const box = screen.querySelector(`[data-reader-box="${CSS.escape(row.dataset.readerElement)}"]`)
      if (!box) return
      row.addEventListener('mouseenter', () => box.classList.add('is-active'))
      row.addEventListener('mouseleave', () => box.classList.remove('is-active'))
    })
  })
}

async function renderReaderPreview() {
  const partialPayload = await safeJson('/api/game-observatory/workspace/partial-fact-bundles', { bundles: [] })
  state.partialBundles = partialPayload.bundles || []
  const requested = new URLSearchParams(location.search).get('draft')
  const selected = requested
    ? state.partialBundles.find((item) => item.path === requested)
    : state.partialBundles[0]
  if (!selected) {
    workspace.innerHTML = `${head('READER PREVIEW', '读者视图预览', '当前没有可供预览的局部事实草稿。', '内部预览')}<div class="empty-state"><strong>没有找到玩法预览</strong><span>指定玩法不存在或已经移除；未回退到其他游戏或玩法。</span></div>`
    return
  }
  scopeSearchNavigation(selected)
  const detail = await getJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(selected.path)}`)
  state.currentPartialBundlePath = detail.path
  const rawBundle = detail.bundle
  const bundle = strictPlayBundleView(rawBundle)
  const runId = bundle.evidence_run_id || ''
  const evidenceStepRefs = new Map(Object.entries(detail.evidence_step_refs || {}))
  const screenNames = new Map((bundle.screen_states || []).map((item) => [item.id, item.name]))
  const elementNames = new Map((bundle.ui_elements || []).map((item) => [item.id, item.name]))
  const counts = {
    screen_states: bundle.screen_states?.length || 0,
    interactions: bundle.interactions?.length || 0,
    visible_mechanics: bundle.visible_mechanics?.length || 0,
    resource_displays: bundle.resource_displays?.length || 0,
  }
  const playUrl = `/game-observatory/studio/play?game=${encodeURIComponent(selected.game_slug)}&play=${encodeURIComponent(selected.play_slug)}`
  workspace.innerHTML = `<nav class="content-breadcrumb"><a href="/game-observatory/studio/">游戏</a><b>→</b><a href="/game-observatory/studio/game?game=${encodeURIComponent(selected.game_slug)}">${esc(selected.game_title)}</a><b>→</b><a href="${playUrl}">${esc(selected.play_title)}</a><b>→</b><span>界面与交互路线</span></nav>
  <div class="reader-preview-boundary"><div><span>${selected.publication_ready ? '玩法界面与交互路线' : '内部读者视图预览'}</span><strong>${selected.publication_ready ? '本地资料库已发布' : '局部事实草稿 · 不在公开目录'}</strong></div><a class="button" href="${playUrl}">返回玩法</a></div>
  <header class="reader-preview-hero"><span>${esc(selected.game_title)} · PLAY / BUSINESS</span><h1>${esc(selected.play_title)}</h1><p><strong>${esc(bundle.scope?.subject || selected.subject)}</strong><br>${esc(bundle.scope?.coverage || '')}</p><div><span>${counts.screen_states || 0} 个界面状态</span><span>${counts.interactions || 0} 个可见交互</span><span>${counts.visible_mechanics || 0} 条可见机制</span><span>${counts.resource_displays || 0} 处资源显示</span></div></header>
  <nav class="reader-preview-nav" aria-label="读者预览目录"><a href="#reader-screens">界面</a><a href="#reader-interactions">交互</a><a href="#reader-transitions">状态转换</a><a href="#reader-mechanics">机制</a><a href="#reader-resources">资源</a></nav>
  <section class="reader-preview-section" id="reader-screens"><header><span>SCREEN & UI</span><h2>玩家看到的界面与信息</h2></header><div class="reader-screen-list">${(bundle.screen_states || []).map((item) => readerScreenMarkup(item, bundle, elementNames, evidenceStepRefs, runId)).join('')}</div></section>
  <section class="reader-preview-section" id="reader-interactions"><header><span>INTERACTION</span><h2>玩家操作与画面响应</h2></header><div class="reader-interaction-list">${(bundle.interactions || []).map((item) => readerInteractionMarkup(item, bundle, screenNames, elementNames, evidenceStepRefs, runId)).join('')}</div></section>
  <section class="reader-preview-section" id="reader-transitions"><header><span>STATE TRANSITION</span><h2>界面状态如何连接</h2></header><div class="reader-transition-list">${(bundle.state_transitions || []).map((transition) => `<article><span>${esc(screenNames.get(transition.from_state_id) || '起始状态')}</span><b>→</b><span>${esc(screenNames.get(transition.to_state_id) || '到达状态')}</span>${readerEvidenceLinks(transition, evidenceStepRefs, runId)}</article>`).join('')}</div></section>
  <section class="reader-preview-section" id="reader-mechanics"><header><span>VISIBLE MECHANIC</span><h2>从画面与操作可确认的机制</h2></header><div class="reader-rule-grid">${(bundle.visible_mechanics || []).map((mechanic) => `<article><span>${(mechanic.screen_state_ids || []).map((id) => esc(screenNames.get(id) || '相关界面')).join(' · ')}</span><p>${esc(mechanic.observed_rule)}</p>${readerEvidenceLinks(mechanic, evidenceStepRefs, runId)}</article>`).join('')}</div></section>
  <section class="reader-preview-section" id="reader-resources"><header><span>RESOURCE DISPLAY</span><h2>界面中的资源与数值</h2></header><div class="reader-resource-grid">${(bundle.resource_displays || []).map((resource) => { const artifactId = resource.artifact_ids?.find((id) => !id.startsWith('art.video.')); const value = Array.isArray(resource.displayed_value) ? resource.displayed_value.join(' · ') : resource.displayed_value; return `<article><div>${partialArtifactImage(artifactId, `${resource.label}所在游戏画面`)}</div><span>${esc(screenNames.get(resource.screen_state_id) || '相关界面')}</span><h3>${esc(resource.label)}</h3><strong>${esc(value)}</strong>${readerEvidenceLinks(resource, evidenceStepRefs, runId)}</article>` }).join('')}</div></section>`
  bindReaderHighlights()
}

async function renderSpec() {
  const [payload, partialPayload] = await Promise.all([
    safeJson('/api/game-observatory/workspace/design-specs', { design_specs: [] }),
    safeJson('/api/game-observatory/workspace/partial-fact-bundles', { bundles: [], errors: [] }),
  ])
  state.specs = usableSpecs(payload.design_specs)
  state.partialBundles = partialPayload.bundles || []
  const parameters = new URLSearchParams(location.search)
  const requested = parameters.get('report')
  const requestedDraft = parameters.get('draft')
  const selectedDraft = requested ? null : (
    requestedDraft
      ? state.partialBundles.find((item) => item.path === requestedDraft)
      : state.partialBundles[0]
  )
  if (requestedDraft && !selectedDraft) {
    workspace.innerHTML = `${head('ARTICLE PREVIEW', '没有找到指定内容页', '指定玩法不存在或已经移除；未回退到其他游戏或玩法。', '内容边界')}<div class="empty-state"><strong>内容页范围无效</strong></div>`
    return
  }
  if (selectedDraft) {
    const detail = await getJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(selectedDraft.path)}`)
    state.currentPartialBundlePath = detail.path
    state.currentPartialEvidenceRunId = detail.bundle?.evidence_run_id || ''
    state.partialEvidenceStepRefs = new Map(Object.entries(detail.evidence_step_refs || {}))
    renderPartialFactBundle(detail, state.specs)
    return
  }
  state.currentPartialBundlePath = ''
  state.currentPartialEvidenceRunId = ''
  state.partialEvidenceStepRefs = new Map()
  const selected = state.specs.find((item) => item.report.id === requested) || state.specs[0]
  const report = selected?.report
  const spec = report?.design_spec || {}
  const options = specSelectionOptions(state.partialBundles, state.specs, 'report', report?.id)
  workspace.innerHTML = `${playBreadcrumb(partialDetail?.summary, '游玩记录')}${head(
    'FACT AUTHORING',
    '事实案工作区',
    '这里检查事实对象和发布缺口。完整轨迹仍可先进入证据库，只有经过范围与来源审查的对象才会进入公开资料库。',
  )}
  <div class="toolbar"><label>工作对象<select data-spec-select>${options || '<option>暂无对象</option>'}</select></label></div>
  ${report ? `<div class="spec-board">
    <nav class="spec-outline" aria-label="事实案结构">
      <a href="#scope">系统边界</a><a href="#entry">入口条件</a><a href="#loop">核心循环</a>
      <a href="#architecture">界面关系</a><a href="#screens">界面与元素</a><a href="#interactions">交互步骤</a>
      <a href="#states">状态分支</a><a href="#mechanics">机制规则</a><a href="#resources">资源关系</a>
      <a href="#feedback">反馈规格</a><a href="#recovery">失败与恢复</a><a href="#publication">发布门</a>
    </nav>
    <div class="spec-canvas">
      <section class="spec-section" id="scope"><span>SYSTEM SCOPE</span><h2>${esc(report.system_title)}</h2><div class="contract-grid">
        <div class="contract-item"><strong>${esc(report.game_title)}</strong><small>${esc(report.scope?.platform)} · ${esc(report.scope?.version)} · ${esc(report.scope?.locale || '')}</small></div>
        <div class="contract-item"><strong>${humanStatus(report.status)}</strong><small>${esc(report.migration_status)} · revision ${selected.current_revision || 0}</small></div>
      </div>${factStatements(spec.version_notes)}</section>
      <section class="spec-section" id="entry"><span>ENTRY & UNLOCK</span><h2>入口条件</h2>${factStatements(spec.entry_and_unlock)}</section>
      <section class="spec-section" id="loop"><span>CORE LOOP</span><h2>玩家操作与系统响应</h2>${coreLoopMarkup(spec.core_loop)}</section>
      <section class="spec-section" id="architecture"><span>INFORMATION ARCHITECTURE</span><h2>界面关系</h2>${architectureMarkup(spec.information_architecture, report.surfaces)}</section>
      <section class="spec-section" id="screens"><span>SCREEN & ELEMENT</span><h2>界面与元素 · ${sectionCount(report, 'surfaces')}</h2><div class="surface-list">${(report.surfaces || []).map(surfaceMarkup).join('') || '<p class="muted-copy">界面规格尚未登记。</p>'}</div></section>
      <section class="spec-section" id="interactions"><span>INTERACTION</span><h2>交互步骤</h2>${interactionMarkup(spec.interaction_specs, report.flow)}</section>
      <section class="spec-section" id="states"><span>STATE BRANCHES</span><h2>状态分支</h2>${stateMatrixMarkup(spec.state_matrices)}</section>
      <section class="spec-section" id="mechanics"><span>MECHANIC</span><h2>机制规则 · ${sectionCount(report, 'mechanisms')}</h2><div class="mechanism-grid">${mechanismMarkup(report.mechanisms)}</div></section>
      <section class="spec-section" id="resources"><span>RESOURCE</span><h2>资源关系 · ${sectionCount(report, 'resources')}</h2><div class="resource-grid">${resourceMarkup(report.resources)}</div></section>
      <section class="spec-section" id="feedback"><span>FEEDBACK</span><h2>反馈规格</h2>${feedbackMarkup(spec.feedback_specs)}</section>
      <section class="spec-section" id="recovery"><span>FAILURE & RECOVERY</span><h2>失败与恢复</h2>${failureMarkup(spec.failure_recovery_specs)}</section>
      <section class="spec-section" id="publication"><span>PUBLICATION GATE</span><h2>发布门</h2>${selected.publication_issues?.length
        ? `<ul class="issue-list">${selected.publication_issues.map((issue) => `<li>${esc(issue)}</li>`).join('')}</ul>`
        : '<p>当前模型校验没有结构错误；内容事实仍需由有限域饱和和人工审阅决定。</p>'}</section>
    </div>
  </div>` : '<div class="empty-state"><strong>没有事实案工作对象</strong><span>先从证据轨迹提升经过审阅的事实对象。</span></div>'}`
  bindSpecSelection()
  bindElementHighlights()
}

function pathMarkup(step, run, second = false) {
  const action = step.action || {}
  if (action.x == null || action.y == null || action.x2 == null || action.y2 == null) return ''
  const offsetX = second ? action.two_finger_offset_x || 0 : 0
  const offsetY = second ? action.two_finger_offset_y || 0 : 0
  const x1 = action.x + offsetX
  const y1 = action.y + offsetY
  const x2 = action.x2 + offsetX
  const y2 = action.y2 + offsetY
  const dx = x2 - x1
  const dy = y2 - y1
  const width = Math.sqrt(dx * dx + dy * dy) / run.viewport_width * 100
  const angle = Math.atan2(dy, dx) * 180 / Math.PI
  return `<span class="action-path" style="left:${x1 / run.viewport_width * 100}%;top:${y1 / run.viewport_height * 100}%;width:${width}%;transform:rotate(${angle}deg)"></span>`
}

function overlayMarkup(step, run) {
  const point = step.source_point
  const bounds = step.target_bounds
  const action = step.action || {}
  const box = bounds ? `<span class="target-box" style="left:${bounds.x / run.viewport_width * 100}%;top:${bounds.y / run.viewport_height * 100}%;width:${bounds.width / run.viewport_width * 100}%;height:${bounds.height / run.viewport_height * 100}%"></span>` : ''
  const dot = point ? `<span class="action-point" style="left:${point.x / run.viewport_width * 100}%;top:${point.y / run.viewport_height * 100}%"></span>` : ''
  const firstPath = ['swipe', 'two_finger_swipe'].includes(action.type) ? pathMarkup(step, run) : ''
  const secondPath = action.type === 'two_finger_swipe' ? pathMarkup(step, run, true) : ''
  return `${box}${firstPath}${secondPath}${dot}`
}

function frameMarkup(artifactId, step, run, withOverlay) {
  if (!artifactId) return '<div class="empty-state"><strong>没有画面</strong><span>该证据步缺少对应 artifact。</span></div>'
  const maximumWidth = Math.max(240, Math.round(740 * run.viewport_width / run.viewport_height))
  return `<div class="frame-content" style="--frame-ratio:${run.viewport_width} / ${run.viewport_height};--frame-max-width:${maximumWidth}px">
    <img src="/api/game-observatory/internal/artifacts/${encodeURIComponent(artifactId)}" alt="完整游戏画面" loading="eager" decoding="async">
    ${withOverlay ? `<div class="frame-overlay">${overlayMarkup(step, run)}</div>` : ''}
  </div>`
}

function evidenceDetail(detail, step, adjudication, factReferences = []) {
  const run = detail.run
  const action = step.action || {}
  return `<div class="evidence-title"><div><h2>步骤 ${step.step_index} · ${esc(effectiveTargetName(step, adjudication))}</h2><p>${esc(evidenceRecorderStatus(step))} · ${esc(action.type)} · ${compactDate(step.started_at)}</p></div></div>
  ${adjudicationMarkup(adjudication)}
  ${factReferences.length ? `<aside class="evidence-fact-links"><strong>引用本步骤的事实对象</strong>${factReferences.map((item) => `<a href="${item.href}"><span>${esc(item.kind)}</span>${esc(item.label)}</a>`).join('')}</aside>` : '<aside class="evidence-fact-links is-empty"><strong>尚无事实对象引用本步骤</strong><span>证据可以先于事实对象存在。</span></aside>'}
  <div class="frame-pair">
    <article class="frame-card"><h3>BEFORE / 操作位置</h3><div class="frame-stage">${frameMarkup(step.before_frame_id, step, run, true)}</div></article>
    <article class="frame-card"><h3>AFTER / 到达状态</h3><div class="frame-stage">${frameMarkup(step.after_frame_id, step, run, false)}</div></article>
  </div>
  <div class="action-card"><span>动作 ${esc(action.type)}</span>${step.source_point ? `<span>起点 ${step.source_point.x}, ${step.source_point.y}</span>` : ''}${step.source_end_point ? `<span>终点 ${step.source_end_point.x}, ${step.source_end_point.y}</span>` : ''}<span>稳定帧 ${step.stability?.sampled_frames || 0}</span></div>
  ${step.video_artifact_id ? `<details class="tech"><summary>相邻视频</summary><video controls preload="metadata" style="display:block;width:100%;max-height:620px" src="/api/game-observatory/internal/artifacts/${encodeURIComponent(step.video_artifact_id)}"></video></details>` : ''}
  <details class="tech"><summary>技术详情</summary><pre>${esc(JSON.stringify(step, null, 2))}</pre></details>`
}

function evidenceOriginMarkup(detail, partialDetail) {
  const run = detail?.run
  if (!run) return ''
  const recordRef = Object.values(partialDetail?.play_record_refs || {}).find((item) => item?.run?.id === run.id)
  const source = recordRef?.sources?.[0] || {}
  const playRecord = (partialDetail?.bundle?.play_records || []).find((item) => item.evidence_run_id === run.id)
  const title = playRecord?.title || source.title?.replace(/\s*·\s*EvidenceRun$/, '') || `${run.game_id || '游戏'}实机游玩记录`
  const platform = source.platform || run.adapter || '已登记运行环境'
  const author = source.author || run.environment?.created_by || '已登记操作主体'
  const capturedAt = source.published_at || compactDate(run.started_at)
  const stepCount = recordRef?.run?.step_count ?? run.step_ids?.length ?? 0
  const artifactCount = recordRef?.run?.artifact_count ?? run.artifact_ids?.length ?? 0
  const sourcesHref = recordRef?.run?.sources_href || `/game-observatory/studio/sources?run=${encodeURIComponent(run.id)}`
  return `<section class="evidence-origin"><div><span>本次游玩记录来源</span><h2>${esc(title)}</h2><p>${esc(platform)} · ${esc(author)} · ${esc(capturedAt)}</p></div><dl><div><dt>运行状态</dt><dd>${esc(humanStatus(run.status))}</dd></div><div><dt>画面与动作</dt><dd>${stepCount} 个步骤 · ${artifactCount} 个原始文件</dd></div></dl><a class="button" href="${esc(sourcesHref)}">查看原始文件</a></section>`
}

function evidenceRunDisplayTitle(run, partialDetail) {
  const playRecord = (partialDetail?.bundle?.play_records || []).find((item) => item.evidence_run_id === run.id)
  return playRecord?.title || run.game_id || '游玩记录'
}

async function renderEvidence() {
  const [payload, partialPayload] = await Promise.all([
    // 控制台只需近期运行；完整历史仍在专门的证据浏览页。限制载荷可避免
    // 设备记录积累后，首屏被数百 KB 的运行清单长期阻塞。
    safeJson('/api/game-observatory/evidence-runs?limit=30', { runs: [] }),
    safeJson('/api/game-observatory/workspace/partial-fact-bundles', { bundles: [] }),
  ])
  state.runs = payload.runs || []
  state.partialBundles = partialPayload.bundles || []
  const parameters = new URLSearchParams(location.search)
  const requestedDraft = parameters.get('draft') || ''
  const requestedRunId = parameters.get('run') || ''
  const requestedBundle = requestedDraft
    ? state.partialBundles.find((item) => item.path === requestedDraft)
    : null
  if (requestedDraft && !requestedBundle) {
    workspace.innerHTML = `${head('EVIDENCE PLAYER', '没有找到指定内容页', '证据链接携带的玩法草稿不存在；未回退到其他玩法。', '证据闭合')}<div class="empty-state"><strong>证据范围无效</strong></div>`
    return
  }
  if (requestedBundle && requestedRunId && !partialBundleRunIds(requestedBundle).has(requestedRunId)) {
    workspace.innerHTML = `${playBreadcrumb(requestedBundle, '游玩记录')}${head('EVIDENCE PLAYER', '该运行不属于指定内容页', '证据范围校验失败；未回退到共享运行的其他玩法。', '证据闭合')}<div class="empty-state"><strong>证据不在当前系统边界内</strong></div>`
    return
  }
  const scopedRunId = requestedRunId
    || requestedBundle?.evidence_run_id
    || requestedBundle?.evidence_run_ids?.[0]
    || ''
  let selectedCandidate = requestedBundle
    ? scopedEvidenceRun(state.runs, requestedBundle, scopedRunId)
    : preferredRun(state.runs)
  let prefetchedDetail = null
  if (requestedBundle && scopedRunId && !selectedCandidate) {
    prefetchedDetail = await safeJson(
      `/api/game-observatory/evidence-runs/${encodeURIComponent(scopedRunId)}`,
      null,
    )
    selectedCandidate = prefetchedDetail?.run || null
  }
  const matchingBundle = requestedBundle || state.partialBundles.find((item) => (
    partialBundleRunIds(item).has(selectedCandidate?.id)
  ))
  const runPool = selectedCandidate && !state.runs.some((item) => item.id === selectedCandidate.id)
    ? [selectedCandidate, ...state.runs]
    : state.runs
  const visibleRuns = matchingBundle
    ? runPool.filter((item) => partialBundleRunIds(matchingBundle).has(item.id))
    : (selectedCandidate ? [selectedCandidate] : [])
  const selected = visibleRuns.find((item) => item.id === selectedCandidate?.id) || visibleRuns[0]
  const [detail, adjudicationPayload, partialDetail] = selected ? await Promise.all([
    prefetchedDetail || safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}`, null),
    safeJson(
      `/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}/adjudications`,
      { adjudications: { items: [] } },
    ),
    matchingBundle
      ? safeJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(matchingBundle.path)}`, null)
      : null,
  ]) : [null, { adjudications: { items: [] } }, null]
  const adjudicationByStep = new Map(
    (adjudicationPayload.adjudications?.items || []).map((item) => [item.step_id, item]),
  )
  const factReferencesByStep = partialEvidenceReferenceIndex(partialDetail)
  const requestedStepId = parameters.get('step')
  const selectedStepIndex = Math.max(0, detail?.steps?.findIndex((step) => step.id === requestedStepId) ?? 0)
  state.currentRun = detail
  state.currentStep = detail?.steps?.[selectedStepIndex] || null
  workspace.innerHTML = `${head(
    'EVIDENCE PLAYER',
    '完整游玩轨迹',
    '每个动作保留完整前帧、原图坐标、目标框、后帧与相邻视频。画面使用唯一源坐标系，叠层随图片内容框缩放。',
  )}
  <div class="split-layout">
    <aside class="side-panel"><div class="panel-head"><strong>证据运行</strong><small>${state.runs.length} 条近期记录</small></div><div class="run-list">${state.runs.map((run) => `<a class="run-row ${run.id === selected?.id ? 'is-active' : ''}" href="?run=${encodeURIComponent(run.id)}"><strong>${esc(runLabel(run))}</strong><span>${humanStatus(run.status)} · ${compactDate(run.started_at)}</span></a>`).join('')}</div></aside>
    <section class="content-panel">${detail?.steps?.length ? `<div class="evidence-shell"><div class="step-list">${detail.steps.map((step, index) => {
      const adjudication = adjudicationByStep.get(step.id)
      return `<button class="step-row ${index === selectedStepIndex ? 'is-active' : ''} ${adjudication ? `has-adjudication is-${esc(adjudication.verdict)}` : ''}" data-step-index="${index}"><strong>${step.step_index}. ${esc(effectiveTargetName(step, adjudication))}</strong><span>${adjudication ? esc(verdictLabels[adjudication.verdict] || adjudication.verdict) : esc(evidenceRecorderStatus(step))} · ${esc(step.action.type)}</span></button>`
    }).join('')}</div><div class="evidence-detail" data-evidence-detail>${evidenceDetail(detail, detail.steps[selectedStepIndex], adjudicationByStep.get(detail.steps[selectedStepIndex].id), factReferencesByStep.get(detail.steps[selectedStepIndex].id) || [])}</div></div>` : '<div class="empty-state"><strong>没有证据步骤</strong><span>新证据步会在这里按顺序显示。</span></div>'}</section>
  </div>`
  document.querySelectorAll('[data-step-index]').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-step-index]').forEach((item) => item.classList.remove('is-active'))
      button.classList.add('is-active')
      const step = detail.steps[Number(button.dataset.stepIndex)]
      state.currentStep = step
      document.querySelector('[data-evidence-detail]').innerHTML = evidenceDetail(
        detail,
        step,
        adjudicationByStep.get(step.id),
        factReferencesByStep.get(step.id) || [],
      )
      const url = new URL(location.href)
      url.searchParams.set('run', detail.run.id)
      url.searchParams.set('step', step.id)
      history.replaceState(null, '', url)
    })
  })
}

async function renderCoverage() {
  const [runPayload, ledgerPayload, partialPayload] = await Promise.all([
    safeJson('/api/game-observatory/evidence-runs?limit=100', { runs: [] }),
    safeJson('/api/game-observatory/saturation-ledgers', { ledgers: [] }),
    safeJson('/api/game-observatory/workspace/partial-fact-bundles', { bundles: [] }),
  ])
  state.runs = runPayload.runs || []
  state.ledgers = ledgerPayload.ledgers || []
  state.partialBundles = partialPayload.bundles || []
  const params = new URLSearchParams(location.search)
  const requestedRunId = params.get('run')
  const requestedDraft = params.get('draft')
  const matchingBundle = requestedDraft
    ? state.partialBundles.find((item) => item.path === requestedDraft)
    : state.partialBundles.find((item) => (item.evidence_run_ids || [item.evidence_run_id]).includes(requestedRunId))
  const bundleRunId = matchingBundle?.evidence_run_id || matchingBundle?.evidence_run_ids?.[0]
  const selected = state.runs.find((item) => item.id === (requestedRunId || bundleRunId))
    || (!requestedDraft ? preferredRun(state.runs) : null)
  const partialDetail = matchingBundle
    ? await safeJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(matchingBundle.path)}`, null)
    : null
  const detail = selected
    ? await safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}`, null)
    : null
  const adjudicationPayload = selected ? await safeJson(
    `/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}/adjudications`,
    { adjudications: { items: [] } },
  ) : { adjudications: { items: [] } }
  const steps = detail?.steps || []
  const adjudications = adjudicationPayload.adjudications?.items || []
  const actionTypes = [...new Set(steps.map((item) => item.action.type))]
  const failed = steps.filter((item) => item.status !== 'passed').length
  const named = steps.filter((item) => item.target_name).length
  const invalidContext = adjudications.filter((item) => item.verdict === 'invalid_context')
  const mislabeled = adjudications.filter((item) => item.verdict === 'mislabelled')
  const facilityFailure = adjudications.filter((item) => item.verdict === 'facility_failure')
  const noChange = adjudications.filter((item) => item.verdict === 'verified_no_change')
  const coverageGameId = partialDetail?.summary?.game_id || selected?.game_id
  const ledger = state.ledgers.find((item) => item.ledger?.scope?.game_id === coverageGameId)
  workspace.innerHTML = `${head(
    'FINITE DOMAIN',
    '探索覆盖台',
    '有限域账本把界面状态、可见候选、动作结果、回滚与证据逐项绑定。当前 AFK 运行保留为部分覆盖，饱和门仍关闭。',
  )}
  <section class="coverage-board">
    <article class="coverage-card"><h2>本轮证据</h2><strong>${steps.length}</strong><p>${named} 步具有人类可读目标；动作类型覆盖 ${actionTypes.map(esc).join('、') || '无'}。</p></article>
    <article class="coverage-card"><h2>人工裁定</h2><strong>${invalidContext.length + mislabeled.length}</strong><p>错误上下文：${invalidContext.map((item) => `步骤 ${item.step_index}`).join('、') || '无'}；标签与实际动作不符：${mislabeled.map((item) => `步骤 ${item.step_index}`).join('、') || '无'}。</p><ul class="issue-list">${facilityFailure.map((item) => `<li>步骤 ${item.step_index} · ${esc(item.note)}</li>`).join('')}${noChange.map((item) => `<li>步骤 ${item.step_index} · ${esc(item.note)}</li>`).join('')}</ul></article>
    <article class="coverage-card"><h2>饱和门</h2><strong>${ledger?.validation?.saturation_pass ? '通过' : '未通过'}</strong><p>${ledger ? `${ledger.validation.unresolved_candidate_ids?.length || 0} 个未裁决候选，${ledger.validation.unreviewed_state_node_ids?.length || 0} 个未复查状态。` : '首份完整 ledger 尚未提交；探索未完成。'}</p></article>
  </section>
  <div class="card-list" style="margin-top:18px">
    <article class="record-card"><header><h2>有限域验收合同</h2>${status(ledger?.validation?.saturation_pass ? 'passed' : 'running')}</header><div class="contract-grid">
      <div class="contract-item"><strong>候选闭合</strong><small>每个可见安全候选拥有执行、禁止或延期裁定。</small></div>
      <div class="contract-item"><strong>状态递归</strong><small>新页面、弹窗与状态继续进入同一普查。</small></div>
      <div class="contract-item"><strong>独立复查</strong><small>两次干净起点复查为每个状态绑定独立证据。</small></div>
      <div class="contract-item"><strong>事实可回答</strong><small>机制、资源与状态变化能从事实对象和证据直接回答。</small></div>
    </div>${failed ? `<ul class="issue-list"><li>${failed} 个步骤状态未通过。</li></ul>` : ''}</article>
    ${evidenceGaps.length ? `<section class="coverage-gaps"><header><span>EVIDENCE GAPS</span><h2>待补证问题 · ${evidenceGaps.length}</h2><p>这些条目只进入探索覆盖台，不进入玩法设计文档正文。</p></header><div class="partial-gap-list">${evidenceGaps.map(partialGapMarkup).join('')}</div></section>` : ''}
  </div>`
}

function sourceSnapshotMarkup(snapshot, sourceRef) {
  const title = sourceRef?.title || snapshot.metadata?.title || '已登记来源'
  const locator = sourceRef?.locator || snapshot.excerpt || '该来源保存了内容定位和指纹。'
  const kind = sourceRef?.kind || snapshot.metadata?.kind || '来源'
  const version = sourceRef?.version_context || ''
  return `<article class="record-card"><header><h3>${esc(title)}</h3>${status(snapshot.status)}</header><p>${esc(locator)}</p><div class="meta"><span>${esc(kind)}</span><span>${compactDate(snapshot.captured_at)}</span>${version ? `<span>${esc(version)}</span>` : ''}</div><details class="tech"><summary>来源详情</summary><pre>${esc(JSON.stringify({ source: sourceRef || null, snapshot }, null, 2))}</pre></details></article>`
}

async function renderSources() {
  const [snapshotPayload, runPayload, specPayload, partialPayload] = await Promise.all([
    safeJson('/api/game-observatory/source-snapshots', { snapshots: [] }),
    safeJson('/api/game-observatory/evidence-runs?limit=100', { runs: [] }),
    safeJson('/api/game-observatory/workspace/design-specs', { design_specs: [] }),
    safeJson('/api/game-observatory/workspace/partial-fact-bundles', { bundles: [] }),
  ])
  state.specs = usableSpecs(specPayload.design_specs)
  state.partialBundles = partialPayload.bundles || []
  state.runs = runPayload.runs || []
  const params = new URLSearchParams(location.search)
  const requestedRunId = params.get('run')
  const requestedDraft = params.get('draft')
  const matchingBundle = requestedDraft
    ? state.partialBundles.find((item) => item.path === requestedDraft)
    : state.partialBundles.find((item) => (item.evidence_run_ids || [item.evidence_run_id]).includes(requestedRunId))
  const bundleRunId = matchingBundle?.evidence_run_id || matchingBundle?.evidence_run_ids?.[0]
  const selected = state.runs.find((item) => item.id === (requestedRunId || bundleRunId))
    || (!requestedDraft ? preferredRun(state.runs) : null)
  const partialDetail = matchingBundle
    ? await safeJson(`/api/game-observatory/workspace/partial-fact-bundle?path=${encodeURIComponent(matchingBundle.path)}`, null)
    : null
  const detail = selected
    ? await safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}`, null)
    : null
  const scopedRunIds = new Set([
    ...(matchingBundle?.evidence_run_ids || []),
    ...(matchingBundle?.evidence_run_id ? [matchingBundle.evidence_run_id] : []),
  ])
  const availableRuns = matchingBundle
    ? state.runs.filter((run) => scopedRunIds.has(run.id))
    : state.runs
  const sourceIds = new Set(matchingBundle
    ? partialReferenceValues(partialDetail?.bundle || {}, 'source_ids')
    : state.specs.flatMap((item) => item.report.source_ids || []))
  const sourceRefById = new Map((partialDetail?.bundle?.source_refs || []).map((item) => [item.id, item]))
  state.snapshots = (snapshotPayload.snapshots || []).filter((snapshot) => sourceIds.has(snapshot.source_id))
  const steps = detail?.steps || []
  const artifactIds = [...new Set((detail?.steps || []).flatMap((step) => step.artifact_ids || []))]
  const frameArtifacts = []
  const seenFrames = new Set()
  steps.forEach((step) => {
    ;[
      ['操作前', step.before_frame_id],
      ['操作后', step.after_frame_id],
    ].forEach(([stage, artifactId]) => {
      if (!artifactId || seenFrames.has(artifactId)) return
      seenFrames.add(artifactId)
      frameArtifacts.push({ artifactId, stage, step })
    })
  })
  const videos = steps.filter((step) => step.video_artifact_id)
  const evidenceDraft = matchingBundle?.path
    ? `draft=${encodeURIComponent(matchingBundle.path)}&`
    : ''
  workspace.innerHTML = `${head(
    'PROVENANCE',
    '来源与 artifact',
    '来源快照、游戏运行和派生材料分开保存。公开事实回链来源；内部轨迹继续保留文件 hash 与运行语境。',
  )}
  <div class="toolbar">${availableRuns.length ? `<label>证据运行<select data-source-run-select>${availableRuns.map((run) => `<option value="${esc(run.id)}" ${run.id === selected?.id ? 'selected' : ''}>${esc(runLabel(run))} · ${humanStatus(run.status)}</option>`).join('')}</select></label>` : '<span class="muted-copy">该玩法尚未建立新格式的证据运行</span>'}${matchingBundle ? `<a class="button" href="/game-observatory/studio/spec?draft=${encodeURIComponent(matchingBundle.path)}">打开引用这些文件的事实草稿</a>` : ''}</div>
  <section class="metric-grid">
    <article class="metric"><strong>${state.snapshots.length}</strong><span>来源快照</span><small>内容 hash 可追踪</small></article>
    <article class="metric"><strong>${artifactIds.length}</strong><span>当前运行 artifact</span><small>${selected ? `${selected.step_count ?? detail?.steps?.length ?? 0} 个证据步` : '未选择运行'}</small></article>
    <article class="metric"><strong>${detail?.run?.build_scope_id ? 1 : 0}</strong><span>build 语境</span><small>${esc(detail?.run?.build_scope_id || '尚未绑定')}</small></article>
    <article class="metric"><strong>${detail?.run?.target_id ? 1 : 0}</strong><span>设备语境</span><small>内部可追溯</small></article>
  </section>
  <section class="source-section"><div class="section-heading"><div><span>CAPTURED FRAMES</span><h2>当前运行的画面文件</h2></div><small>${frameArtifacts.length} 张唯一前后帧</small></div>
    <div class="artifact-gallery">${frameArtifacts.slice(-24).map(({ artifactId, stage, step }) => `<a class="artifact-card" href="/game-observatory/studio/evidence?${evidenceDraft}run=${encodeURIComponent(selected.id)}&step=${encodeURIComponent(step.id)}"><img src="/api/game-observatory/internal/artifacts/${encodeURIComponent(artifactId)}" alt="步骤 ${step.step_index} ${stage}完整画面" loading="lazy" decoding="async"><span>步骤 ${step.step_index} · ${stage}</span><strong>${esc(step.target_name || step.action?.type || '未命名动作')}</strong></a>`).join('') || '<div class="empty-state"><strong>当前运行没有画面文件</strong></div>'}</div>
    ${frameArtifacts.length > 24 ? `<p class="muted-copy">显示最近 24 张；全部 ${frameArtifacts.length} 张仍保存在证据运行中。</p>` : ''}
  </section>
  <section class="source-section"><div class="section-heading"><div><span>ADJACENT VIDEO</span><h2>相邻视频</h2></div><small>${videos.length} 段</small></div>
    <div class="video-gallery">${videos.slice(-8).map((step) => `<article><video controls preload="none" src="/api/game-observatory/internal/artifacts/${encodeURIComponent(step.video_artifact_id)}"></video><a href="/game-observatory/studio/evidence?${evidenceDraft}run=${encodeURIComponent(selected.id)}&step=${encodeURIComponent(step.id)}">步骤 ${step.step_index} · ${esc(step.target_name || step.action?.type || '动作')}</a></article>`).join('') || '<div class="empty-state"><strong>当前运行没有相邻视频</strong></div>'}</div>
  </section>
  <section class="source-section"><div class="section-heading"><div><span>EXTERNAL SOURCES</span><h2>当前有效事实对象的来源快照</h2></div><small>${state.snapshots.length} 份</small></div>
    <div class="card-list">${state.snapshots.slice(0, 40).map((snapshot) => sourceSnapshotMarkup(snapshot, sourceRefById.get(snapshot.source_id))).join('') || '<div class="empty-state"><strong>当前事实草稿尚未绑定外部来源</strong><span>旧样例来源不会混入这次设施预览；本轮设备画面与运行语境已经单独列出。</span></div>'}</div>
  </section>
  <details class="tech"><summary>运行语境与全部 artifact 标识</summary><pre>${esc(JSON.stringify({ run: detail?.run || null, artifact_ids: artifactIds }, null, 2))}</pre></details>`
  document.querySelector('[data-source-run-select]')?.addEventListener('change', (event) => {
    location.href = `/game-observatory/studio/sources?run=${encodeURIComponent(event.target.value)}`
  })
}

async function renderFeedback() {
  const [payload, specPayload] = await Promise.all([
    safeJson('/api/game-observatory/voice-records', { voices: [] }),
    safeJson('/api/game-observatory/workspace/design-specs', { design_specs: [] }),
  ])
  state.voices = payload.voices || []
  state.specs = usableSpecs(specPayload.design_specs)
  const activeReportIds = new Set(state.specs.map((item) => item.report.id))
  const active = state.voices.filter((item) => (
    activeReportIds.has(item.report_id)
    && item.status === 'active'
    && item.voice?.review_status !== 'rejected'
  ))
  const isolated = state.voices.filter((item) => item.status === 'active' && !activeReportIds.has(item.report_id))
  workspace.innerHTML = `${head(
    'PLAYER VOICE',
    '玩家反馈',
    '玩家原帖、版本语境、主题与对应游戏对象在这一页组织。事实案只引用已经审阅的反馈，不吸收其中的推测。',
  )}
  <section class="metric-grid">
    <article class="metric"><strong>${active.length}</strong><span>有效反馈</span><small>保留原始来源</small></article>
    <article class="metric"><strong>${new Set(active.map((item) => item.voice.theme)).size}</strong><span>主题</span><small>可绑定系统对象</small></article>
    <article class="metric"><strong>${active.filter((item) => item.voice.review_status === 'reviewed').length}</strong><span>已审阅</span><small>拒绝项不进入公开页</small></article>
    <article class="metric"><strong>${new Set(active.map((item) => item.voice.version_context)).size}</strong><span>版本语境</span><small>避免跨版本混合</small></article>
  </section>
  <div class="card-list">${active.map((record) => { const voice = record.voice; return `<article class="record-card"><header><h2>${esc(voice.theme)}</h2><span>${esc(voice.sentiment)}</span></header><p>${esc(voice.summary)}</p>${voice.quote ? `<p>“${esc(voice.quote)}”</p>` : ''}<div class="meta"><span>${esc(voice.version_context)}</span><span>${esc(voice.review_status)}</span>${(voice.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div><details class="tech"><summary>来源绑定</summary><pre>${esc(JSON.stringify(record, null, 2))}</pre></details></article>` }).join('') || `<div class="empty-state"><strong>当前事实草稿尚未绑定玩家反馈</strong><span>${isolated.length} 条旧样例反馈已隔离，不会出现在本轮事实草稿或公开资料库。</span></div>`}</div>`
}

function interpretationItems(report) {
  const spec = report.design_spec || {}
  const values = spec.interpretations || report.interpretations || []
  return values.map((item) => typeof item === 'string' ? { statement: item } : item)
}

async function renderInterpretation() {
  const payload = await safeJson('/api/game-observatory/workspace/design-specs', { design_specs: [] })
  state.specs = usableSpecs(payload.design_specs)
  const entries = state.specs.flatMap((item) => interpretationItems(item.report).map((entry) => ({ report: item.report, entry })))
  workspace.innerHTML = `${head(
    'INTERPRETATION',
    '理解',
    '设计意图、体验判断与因果解释集中在这一层。每条陈述保留支持材料，并与事实正文保持独立。',
  )}
  <div class="card-list">${entries.map(({ report, entry }) => `<article class="record-card"><header><h2>${esc(report.system_title)}</h2><span>推测</span></header><p>${esc(entry.statement || entry.title || '')}</p><div class="meta"><span>${esc(report.game_title)}</span><span>${(entry.source_ids || []).length} 个来源</span><span>${(entry.artifact_ids || []).length} 个 artifact</span></div><details class="tech"><summary>支持材料</summary><pre>${esc(JSON.stringify(entry, null, 2))}</pre></details></article>`).join('') || '<div class="empty-state"><strong>当前玩法没有理解条目</strong><span>事实案可以在没有理解条目的情况下完成；其他玩法的推测不会出现在这里。</span></div>'}</div>`
}

function aiPlayerStatus(value) {
  return ({
    queued: '排队中', active: '执行中', cooldown: '冷却中', blocked: '已阻断',
    completed: '已完成', failed: '失败', invalidated: '已失效', superseded: '已替代',
    candidate: '候选', validated: '已验证', preferred: '首选', degraded: '已降级',
    accepted: '已确认', current: '当前', passed: '已通过', running: '进行中', paused: '已暂停',
    scope_review: '边界复核中', closed: '边界已闭合', draft: '草稿', authorized: '已授权',
    cancelled: '已取消', sent: '已发送', unverified: '未验证', stale: '可能过时',
    contradicted: '已被实机反驳',
  })[value] || value || '未知'
}

function aiPlayerPayloadSummary(payload) {
  if (!payload || typeof payload !== 'object') return String(payload || '未记录摘要')
  const preferred = ['title', 'summary', 'description', 'fact', 'statement', 'note', 'name']
    .map((key) => payload[key])
    .find((value) => typeof value === 'string' && value.trim())
  if (preferred) return aiPlayerHumanText(preferred)
  const scalar = Object.entries(payload)
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 3)
    .map(([, value]) => aiPlayerHumanText(value))
    .join('；')
  return scalar || '结构化记忆'
}

function aiPlayerHumanText(value) {
  const text = String(value ?? '').trim()
  // 记忆里的英文前缀是机器分类键，不属于读者可见内容。
  const withoutMachinePrefix = text.replace(/^(?:[A-Za-z][A-Za-z0-9_.-]*\s*[:：]\s*)+/, '').trim()
  return (withoutMachinePrefix || text).replace(/\bunknown\b/gi, '待确认')
}

function aiPlayerEvidenceImage(artifact, label) {
  if (!artifact?.is_visual || !artifact.href) return ''
  return `<figure class="ai-evidence-thumb"><a href="${esc(artifact.original_href || artifact.href)}" target="_blank" rel="noopener"><img src="${esc(artifact.href)}" alt="${esc(label)}" loading="lazy" decoding="async" width="360" height="240"></a><figcaption>${esc(label)}</figcaption></figure>`
}

function aiPlayerEvidenceCard(step) {
  return `<article class="ai-evidence-card"><header><strong>步骤 ${step.step_index} · ${esc(aiPlayerHumanText(step.target_name || aiPlayerAction(step.action?.type)))}</strong><span>${esc(compactDate(step.ended_at || step.started_at))}</span></header>${step.corrections?.length ? `<aside class="ai-evidence-correction"><strong>原始记录纠错</strong>${step.corrections.map((item) => `<p>${esc(item.note)}</p>`).join('')}</aside>` : ''}<div class="ai-evidence-pair">${aiPlayerEvidenceImage(step.before, '操作前')}${aiPlayerEvidenceImage(step.after, '操作后')}</div><div class="ai-row-meta">${step.source_point ? `<span>位置 ${step.source_point.x}, ${step.source_point.y}</span>` : ''}<span>${esc(aiPlayerStatus(step.status))}</span></div><details class="ai-evidence-meta"><summary>证据元数据</summary><pre>${esc(JSON.stringify({ step_id: step.id, run_id: step.run_id, target_name: step.target_name, action: step.action, before_artifact_id: step.before?.id, after_artifact_id: step.after?.id, corrections: step.corrections }, null, 2))}</pre></details></article>`
}

function aiPlayerEvidenceMarkup(payload, visibleCount = 4) {
  const steps = payload.evidence?.steps || []
  const visible = steps.slice(0, visibleCount)
  const remaining = Math.max(0, steps.length - visible.length)
  const cards = visible.map(aiPlayerEvidenceCard).join('') || '<p class="ai-empty">当前状态引用中没有证据步骤。</p>'
  return `${cards}${remaining ? `<button class="button ai-evidence-more" data-ai-evidence-more data-visible-count="${visible.length}">继续显示较早证据（余 ${remaining} 步）</button>` : ''}`
}

function aiPlayerSessionState(value) {
  return ({ created: '待启动', running: '运行中', paused: '已暂停', safe_stopped: '已安全停止', completed: '已完成' })[value] || value || '未知'
}

function aiPlayerSessionObjective(value) {
  const text = String(value ?? '').trim()
  const questionMarks = (text.match(/\?/g) || []).length
  const hasReadableChinese = /[\u3400-\u9fff]/.test(text)
  if (!text || /^[?\s\uFFFD]+$/.test(text) || (questionMarks >= 4 && !hasReadableChinese)) return '会话目标原文编码损坏（原始记录已保留）'
  return aiPlayerHumanText(text)
}

function aiPlayerIdentityPlace(identity) {
  const notes = (identity?.evidence_refs || []).map((ref) => String(ref.note || '')).join(' ')
  const evidenceMatch = notes.match(/(\d+)服\s+([^\s，,。；;]+)/)
  const serverId = String(identity?.server_scope_id || '')
  const serverMatch = serverId.match(/(?:^|\.)(\d+)$/)
  return {
    server: evidenceMatch?.[1] ? `${evidenceMatch[1]}服` : serverMatch?.[1] ? `${serverMatch[1]}服` : serverId || '待确认',
    world: evidenceMatch?.[2] || identity?.world_scope_id || '待确认',
  }
}

function aiPlayerDailyDuty(value) {
  return ({
    post_login_coverage_audit: '登录后覆盖审计',
    current_goal_update: '当前目标更新',
    guide_freshness_check: '攻略新鲜度检查',
    reachable_business_progress: '可达业务推进',
    end_of_day_memory_consolidation: '日终记忆整理',
    next_day_task_generation: '下一日任务生成',
  })[value] || value || '等待新一日开始'
}

function aiPlayerDailyStatus(value) {
  return ({
    not_started: '尚未开始',
    in_progress: '进行中',
    interrupted: '已中断',
    ready_to_seal: '可以封账',
    sealed: '当日已封账',
    completed: '七日已完成',
    blocked: '已阻断',
  })[value] || value || '尚未开始'
}

function aiPlayerDailyContinuityMarkup(payload) {
  const continuity = payload.daily_continuity
  if (!continuity) return ''
  const runs = continuity.runs || []
  const latest = continuity.latest
  if (!latest) {
    return `<section class="ai-panel ai-daily-continuity"><header><h2>连续经营日账</h2><span>0 个连续批次</span></header><p class="ai-empty">尚未建立真实 Day 1。设施不会把既有游玩记录补写成自然日日账。</p></section>`
  }
  const schedule = latest.schedule || {}
  const assessment = latest.assessment || {}
  return `<section class="ai-panel ai-daily-continuity"><header><h2>连续经营日账</h2><span>${runs.length} 个连续批次</span></header><div class="ai-coverage-summary"><div><strong>${assessment.recorded_natural_days ?? 0}/7</strong><span>已记录自然日</span></div><div><strong>${assessment.sealed_natural_days ?? 0}/7</strong><span>已封账自然日</span></div><div><strong>Day ${schedule.day_index ?? 1}</strong><span>${esc(aiPlayerDailyStatus(schedule.status))}</span></div><div><strong>${schedule.expected_version ?? 0}</strong><span>下一写入版本</span></div></div><article class="ai-row"><header><strong>${esc(aiPlayerDailyDuty(schedule.next_duty))}</strong><span>${esc(schedule.natural_day || '')}</span></header><p>${esc((schedule.reasons || assessment.reasons || []).join('；') || '等待当日真实证据。')}</p></article><p class="console-boundary">本设施只验证并封存真实自然日，不自行补造日期，也不单独签发 G-12。</p></section>`
}

function aiPlayerPathReuseMarkup(payload) {
  const health = payload.path_reuse_health || {}
  const paths = health.repeated_paths || []
  const statusLabel = ({
    healthy: '固定复用正常',
    attention: '有路径需要复查',
    learning: '正在积累首轮路径',
  })[health.status] || '等待路径样本'
  const trendLabel = ({ faster: '复访更快', stable: '耗时稳定', slower: '复访变慢' })
  const pathCards = paths.slice(0, 12).map((path) => {
    const change = Number(path.change_rate || 0)
    const changeLabel = `${change > 0 ? '+' : ''}${(change * 100).toFixed(0)}%`
    return `<article class="ai-row is-path-${esc(path.trend)}"><header><strong>${esc(path.title)}</strong><span class="ai-chip is-${path.trend === 'slower' ? 'failed' : 'passed'}">${esc(trendLabel[path.trend] || path.trend)}</span></header><div class="ai-row-meta"><span>成功复用 ${path.replay_count} 次</span><span>首次固定执行 ${Math.round(Number(path.first_latency_ms))}ms</span><span>最近执行 ${Math.round(Number(path.latest_latency_ms))}ms</span><span>变化 ${esc(changeLabel)}</span></div><details><summary>查看路径标识与计量</summary><pre>${esc(JSON.stringify(path, null, 2))}</pre></details></article>`
  }).join('')
  const zeroModel = `${health.zero_model_replay_count || 0}/${health.successful_replay_count || 0}`
  const baselineSpeedup = health.median_baseline_speedup_ratio == null ? '—' : `${Number(health.median_baseline_speedup_ratio).toFixed(2)}×`
  const latestLatency = health.median_latest_latency_ms == null ? '—' : `${Math.round(Number(health.median_latest_latency_ms))}ms`
  return `<section class="ai-panel ai-path-reuse" data-ai-path-reuse><header><h2>已学路径复用</h2><span>${esc(statusLabel)} · 未知界面才进入语义探索</span></header><div class="ai-coverage-summary"><div><strong>${health.production_route_arc_count || 0}</strong><span>可执行路径弧</span></div><div><strong>${health.known_source_state_count || 0}</strong><span>已知起点</span></div><div><strong>${health.successful_replay_count || 0}</strong><span>成功固定执行</span></div><div><strong>${esc(zeroModel)}</strong><span>零模型 token</span></div><div><strong>${health.repeated_skill_version_count || 0}</strong><span>有复访样本的路径</span></div><div><strong>${esc(latestLatency)}</strong><span>最近中位耗时</span></div><div><strong>${esc(baselineSpeedup)}</strong><span>相对语义基线提速</span></div><div><strong>${health.latest_decisive_failure_skill_count || 0}</strong><span>最近失败待恢复</span></div></div><p class="console-boundary">固定路径逐步核对来源状态与终态；守卫失败后停止该路径，并把未知缺口交回语义层。</p><details ${paths.length ? 'open' : ''}><summary>重复路径的实际速度变化</summary><div class="ai-list">${pathCards || '<p class="ai-empty">路径首次成功后，第二次固定执行会出现在这里。</p>'}</div></details></section>`
}

function aiPlayerIterationDirective(value) {
  return ({
    continue: '继续当前策略',
    shadow_only: '只做影子裁决，暂不操作设备',
    pause_physical_and_repair_perception_executor: '暂停实体操作，修复感知与执行',
    revise_planner_and_task_policy: '调整任务选择与防空转策略',
    refresh_guides_and_reprioritize_objectives: '刷新攻略并重排经营目标',
    expand_discovery_frontier: '扩展未记录内容的探索前沿',
  })[value] || value || '等待形成评估'
}

function aiPlayerIterationTierName(value) {
  return ({
    1: '操作正确、快速、省 token',
    2: '行为有意义、不空转',
    3: '经营账号并提升游戏指标',
    4: '理解尚未记录的内容',
  })[value] || `层级 ${value}`
}

function aiPlayerIterationMarkup(payload) {
  const monitoring = payload.iteration_monitoring || {}
  const latest = monitoring.latest_assessment
  const samples = monitoring.recent_samples || []
  if (!latest) {
    return `<section class="ai-panel ai-iteration-panel"><header><h2>持续迭代监控</h2><span>${samples.length} 条动作样本</span></header><p class="ai-empty">尚未形成完整评估窗口。基础交互门未通过时，实体操作保持暂停；被预检拒绝的候选也会作为零设备调用样本保留。</p></section>`
  }
  const tierCards = (latest.tiers || []).map((tier) => {
    const metrics = Object.entries(tier.metrics || {}).map(([key, value]) => `<span><b>${esc(aiPlayerIterationMetric(key))}</b> ${value == null ? '未计量' : esc(value)}</span>`).join('')
    return `<article class="ai-row is-${esc(tier.status)}"><header><strong>${esc(aiPlayerIterationTierName(tier.tier))}</strong><span class="ai-chip is-${esc(tier.status)}">${esc(aiPlayerIterationStatus(tier.status))}</span></header><div class="ai-row-meta">${metrics}</div>${(tier.reasons || []).length ? `<ul>${tier.reasons.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>` : ''}</article>`
  }).join('')
  const softSignals = Object.entries(latest.soft_signal_averages || {}).map(([key, value]) => `<span>${esc(aiPlayerSoftSignal(key))} ${Number(value).toFixed(1)}/5</span>`).join('')
  const sampleCards = samples.slice(0, 10).map((sample) => `<article class="ai-row"><header><strong>${esc(sample.expected_change)}</strong><span>${esc(aiPlayerIterationStatus(sample.outcome))}</span></header><div class="ai-row-meta"><span>${esc(aiPlayerDecisionMode(sample.decision_mode))}</span><span>token ${sample.model_input_tokens == null ? '未计量' : sample.model_input_tokens}</span><span>时延 ${sample.decision_latency_ms == null ? '未计量' : `${sample.decision_latency_ms}ms`}</span><span>新内容 ${Number(sample.new_state_count || 0) + Number(sample.new_transition_count || 0) + Number(sample.new_interface_count || 0) + Number(sample.new_gameplay_count || 0) + Number(sample.new_rule_count || 0)}</span></div></article>`).join('')
  return `<section class="ai-panel ai-iteration-panel"><header><h2>持续迭代监控</h2><span>策略第 1 版 · ${samples.length} 条近期样本</span></header><article class="ai-session-current"><header><div><span>${esc(aiPlayerIterationStatus(latest.overall_status))}</span><strong>${esc(aiPlayerIterationDirective(latest.directive))}</strong></div><small>连续通过 ${latest.highest_contiguous_passed_tier}/4 层</small></header>${softSignals ? `<div class="ai-row-meta">${softSignals}</div>` : ''}${(latest.soft_review_reasons || []).length ? `<aside>${latest.soft_review_reasons.map(esc).join('；')}</aside>` : ''}</article><div class="ai-list">${tierCards}</div><details><summary>最近动作样本</summary><div class="ai-list">${sampleCards || '<p class="ai-empty">没有近期动作样本。</p>'}</div></details><p class="console-boundary">软指标用于触发复盘和策略调整，不能让任何硬层级通过。</p></section>`
}

function aiPlayerSessionMarkup(sessionPayload, payload) {
  const sessions = sessionPayload?.sessions || []
  const current = sessions.find((item) => ['running', 'paused', 'created'].includes(item.state)) || sessions[0]
  const commandButtons = current ? ({
    created: [['start', '启动会话'], ['safe-stop', '安全停止']],
    running: [['pause', '暂停会话'], ['complete', '完成会话'], ['safe-stop', '安全停止']],
    paused: [['resume', '继续会话'], ['complete', '完成会话'], ['safe-stop', '安全停止']],
  })[current.state] || [] : []
  const budget = current ? `<div class="ai-session-budgets"><span>动作 <b>${current.remaining_action_budget}/${current.action_budget}</b></span><span>token <b>${current.remaining_token_budget ?? '未限制'}</b></span><span>时间 <b>${Math.round(current.remaining_time_seconds)} 秒</b></span><span>版本 <b>${current.version}</b></span></div>` : ''
  const currentCard = current ? `<article class="ai-session-current"><header><div><span>${esc(aiPlayerSessionState(current.state))}</span><strong>${esc(aiPlayerSessionObjective(current.objective))}</strong></div><small>更新于 ${esc(compactDate(current.updated_at))}</small></header>${budget}<div class="console-toolbar">${commandButtons.map(([operation, label]) => `<button class="button ${operation === 'safe-stop' ? 'is-danger' : operation === 'start' || operation === 'resume' ? 'is-primary' : ''}" data-ai-session-command="${operation}" data-session-id="${esc(current.id)}" data-session-version="${current.version}">${label}</button>`).join('')}</div><details class="ai-evidence-meta"><summary>会话元数据</summary><pre>${esc(JSON.stringify(current, null, 2))}</pre></details></article>` : '<p class="ai-empty">当前环境还没有持久会话。</p>'
  const canCreate = !current || ['safe_stopped', 'completed'].includes(current.state)
  const defaultObjective = payload.frontier?.find((task) => task.status === 'active')?.title || '继续当前游戏探索任务'
  const createForm = canCreate ? `<details class="ai-session-create" ${current ? '' : 'open'}><summary>创建新会话</summary><form data-ai-session-create><label>目标<input name="objective" value="${esc(defaultObjective)}" required></label><label>动作预算<input name="action_budget" type="number" min="1" value="50" required></label><label>token 预算<input name="token_budget" type="number" min="1" placeholder="可留空"></label><label>时间预算（秒）<input name="time_budget_seconds" type="number" min="60" value="3600" required></label><button class="button is-primary" type="submit">创建会话</button></form></details>` : ''
  return `<section class="ai-panel ai-session-panel"><header><h2>AI 会话控制</h2><span>${sessions.length} 个持久会话</span></header>${currentCard}${createForm}<p class="console-boundary">会话状态与设备租约分别受控；安全停止会禁止该会话继续执行。</p></section>`
}

function aiPlayerGuideStatus(value) {
  return ({ unverified: '未验证', current: '适用性已确认', stale: '可能过时', contradicted: '已被实机证据反驳' })[value] || value || '状态未标明'
}

function aiPlayerGuideUsage(value) {
  return value === 'discovery_only' ? '仅用于发现' : '只读参考'
}

function aiPlayerGuideMarkup(payload) {
  const guides = payload.guide_knowledge || []
  const cards = guides.map((guide) => {
    const published = guide.published_at || guide.updated_at || '日期未标明'
    const sourceIds = (guide.source_ids || []).join('、') || '未登记来源编号'
    return `<article class="ai-guide-card"><header><strong>${esc(guide.title)}</strong><div><span class="ai-chip is-unverified">${esc(aiPlayerGuideStatus(guide.status))}</span><span class="ai-chip is-discovery">${esc(aiPlayerGuideUsage(guide.usage_mode))}</span></div></header><dl><div><dt>来源</dt><dd>${esc(guide.platform)} · ${esc(guide.author)}</dd></div><div><dt>发布</dt><dd>${esc(published)}</dd></div></dl>${aiPlayerTriggeringTasks(payload, guide.triggering_task_ids)}<a href="${esc(guide.url)}" target="_blank" rel="noopener">打开原始页面</a><p class="ai-guide-gap"><b>适用性缺口</b>${esc(guide.missing_applicability_reason || '尚未完成当前环境适用性核对。')}</p><details><summary>来源定位</summary><p>${esc(sourceIds)}</p><ul>${(guide.locators || []).map((item) => `<li>${esc(item)}</li>`).join('')}</ul></details></article>`
  }).join('')
  return `<section class="ai-panel ai-guide-panel"><header><h2>公开攻略候选</h2><span>${guides.length} 条 · 当前环境</span></header><p class="ai-guide-boundary">这些资料保持只读，只用于提示值得查看的系统与界面；未验证内容不会成为实机事实，也不会直接生成动作指令。</p><div class="ai-guide-list">${cards || '<p class="ai-empty">当前环境没有已落账的攻略候选。</p>'}</div></section>`
}

function aiPlayerTaskTitle(payload, taskId) {
  return (payload.tasks || []).find((task) => task.id === taskId)?.title || '触发任务已归档'
}

function aiPlayerTriggeringTasks(payload, taskIds) {
  if (!(taskIds || []).length) return ''
  return `<div class="ai-row-meta"><b>触发任务</b>${taskIds.map((taskId) => `<span title="${esc(taskId)}">${esc(aiPlayerHumanText(aiPlayerTaskTitle(payload, taskId)))}</span>`).join('')}</div>`
}

function aiPlayerStateNames(payload, stateIds) {
  const names = Object.fromEntries((payload.states || []).map((state) => [state.id, state.title]))
  return (stateIds || []).map((stateId) => names[stateId] || '已归档状态').join('、') || '未登记'
}

function aiPlayerAccountAction(value) {
  return ({
    virtual_resource_use: '使用游戏内资源', recruitment: '招募', character_growth: '角色成长',
    inventory_and_equipment: '背包与装备', construction_and_research: '建设与研究', route_choice: '路线选择',
    map_actions: '地图行动', combat: '战斗', tasks_and_events: '任务与活动', in_game_mail: '游戏内邮件',
    alliance_join_leave: '加入或退出同盟', alliance_collaboration: '同盟协作',
    normal_in_game_communication: '正常游戏内交流', native_game_automation: '游戏自动化',
    real_money_payment: '真实货币支付', external_personal_identity_submission: '提交外部个人身份信息',
  })[value] || aiPlayerHumanText(value)
}

function aiPlayerGameplayMarkup(payload) {
  const candidates = payload.gameplay_candidates || []
  const cards = candidates.map((candidate) => {
    const detailHref = `/api/game-observatory/ai-player/gameplay-candidates/${encodeURIComponent(candidate.id)}?environment_id=${encodeURIComponent(payload.identity.id)}&version=${candidate.version}`
    return `<article class="ai-row"><header><strong>${esc(candidate.title)}</strong><span class="ai-chip is-${esc(candidate.status)}">${esc(aiPlayerStatus(candidate.status))}</span></header><p>${esc(candidate.boundary_summary)}</p>${aiPlayerTriggeringTasks(payload, candidate.triggering_task_ids)}<dl><dt>进入后看到</dt><dd>${esc(aiPlayerStateNames(payload, candidate.main_state_ids))}</dd><dt>进入与离开</dt><dd>${esc(aiPlayerStateNames(payload, candidate.entry_state_ids))} → ${esc(aiPlayerStateNames(payload, candidate.exit_state_ids))}</dd><dt>规则线索</dt><dd>${(candidate.rule_clues || []).map((item) => `<span>${esc(item)}</span>`).join('')}</dd><dt>资源与进度</dt><dd>${(candidate.resource_or_progression_clues || []).map((item) => `<span>${esc(item)}</span>`).join('')}</dd></dl><a href="${esc(detailHref)}" target="_blank" rel="noopener">查看完整玩法候选记录</a></article>`
  }).join('')
  return `<section class="ai-panel"><header><h2>自动发现的玩法</h2><span>${candidates.length} 个</span></header><div class="ai-list">${cards || '<p class="ai-empty">当前环境还没有形成玩法边界候选。</p>'}</div></section>`
}

function aiPlayerAccountPolicyMarkup(payload) {
  const policy = payload.account_policy
  if (!policy) return `<section class="ai-panel"><header><h2>纯 AI 账号行为边界</h2><span>尚未登记</span></header><p class="ai-empty">当前环境没有账号行为策略。</p></section>`
  const detailHref = `/api/game-observatory/ai-player/account-policies/${encodeURIComponent(policy.id)}?environment_id=${encodeURIComponent(payload.identity.id)}&version=${policy.version}`
  return `<section class="ai-panel"><header><h2>纯 AI 账号行为边界</h2><span>版本 ${policy.version}</span></header><article class="ai-row"><header><strong>${esc(policy.ai_identity_label)}</strong><span class="ai-chip is-current">游戏内自主行动</span></header><p>AI 可以自行完成正常游戏行为；真实货币支付和提交外部个人身份信息，每一次都需要单独授权。</p><div class="ai-row-meta"><span>禁止冒充人类</span><span>不读取私人身份信息</span></div><details><summary>查看可自主完成的游戏行为</summary><ul>${(policy.autonomous_actions || []).map((item) => `<li>${esc(aiPlayerAccountAction(item))}</li>`).join('')}</ul><strong>需要单独授权</strong><ul>${(policy.explicit_authorization_actions || []).map((item) => `<li>${esc(aiPlayerAccountAction(item))}</li>`).join('')}</ul></details><a href="${esc(detailHref)}" target="_blank" rel="noopener">查看完整策略记录</a></article></section>`
}

function aiPlayerSpeechMarkup(payload) {
  const intents = payload.speech_intents || []
  const events = payload.speech_events || []
  const cards = intents.map((intent) => {
    const related = events.filter((event) => event.speech_intent_id === intent.id && event.speech_intent_version === intent.version)
    const detailHref = `/api/game-observatory/ai-player/speech-intents/${encodeURIComponent(intent.id)}?environment_id=${encodeURIComponent(payload.identity.id)}&version=${intent.version}`
    return `<article class="ai-row"><header><strong>${esc(intent.purpose)}</strong><span class="ai-chip is-${esc(intent.status)}">${esc(aiPlayerStatus(intent.status))}</span></header><blockquote>${esc(intent.message_text)}</blockquote><div class="ai-row-meta"><span>${esc(intent.channel)}</span><span>接收方：${esc((intent.recipients || []).join('、'))}</span><span>${intent.policy_disposition === 'autonomous' ? '账号策略允许' : '等待单独授权'}</span></div>${aiPlayerTriggeringTasks(payload, [intent.triggering_task_id])}${related.map((event) => `<aside><b>${esc(aiPlayerStatus(event.status))}</b>：${esc(event.system_response)}</aside>`).join('')}<a href="${esc(detailHref)}" target="_blank" rel="noopener">查看发言意图与版本</a></article>`
  }).join('')
  return `<section class="ai-panel"><header><h2>游戏内发言</h2><span>${intents.length} 个意图 · ${events.length} 个结果</span></header><div class="ai-list">${cards || '<p class="ai-empty">当前环境没有发言意图或结果。</p>'}</div></section>`
}

function aiPlayerStateMarkup(payload, sessionPayload = { sessions: [] }) {
  if (!payload?.selection) {
    return `<section class="ai-player-state" data-ai-player-state><div class="empty-state"><strong>还没有 AI 玩家持久状态</strong><span>环境首次落账后，身份、记忆、任务与证据会出现在这里。</span></div></section>`
  }
  const identity = payload.identity
  const identityPlace = aiPlayerIdentityPlace(identity)
  const budget = payload.budget || {}
  const environmentOptions = payload.environment_options || []
  const lineage = (payload.lineage || []).map((item) => `<span class="${item.status === 'current' ? 'is-current' : ''}">${esc(item.label)}</span>`).join('<b>→</b>')
  const currentStateSuffix = payload.current_state_basis === 'latest_active_assignment' ? '（候选）' : ''
  const budgetSource = ({ durable_session: '持久会话', session_capsule: '会话胶囊', open_task_total: '开放任务合计' })[budget.source] || '尚未落账'
  const taskCards = (payload.frontier || []).map((task) => `<article class="ai-row"><header><strong>${esc(aiPlayerHumanText(task.title))}</strong><span class="ai-chip is-${esc(task.status)}">${esc(aiPlayerStatus(task.status))}</span></header><p>${esc(aiPlayerHumanText(task.reason))}</p><div class="ai-row-meta"><span>动作 ${task.action_budget}</span><span>尝试 ${task.attempt_count}/${task.max_attempts}</span><span>价值 ${Number(task.value_score || 0).toFixed(1)}</span></div>${task.blocked_reason ? `<aside>${esc(aiPlayerHumanText(task.blocked_reason))}；恢复条件：${esc(aiPlayerHumanText(task.reactivation_condition))}</aside>` : ''}</article>`).join('')
  const ordinaryMemories = (payload.memories || []).filter((memory) => memory.kind !== 'failure_forbidden')
  const memoryCards = ordinaryMemories.slice().reverse().slice(0, 12).map((memory) => `<article class="ai-row"><header><strong>${esc(aiPlayerMemoryKind(memory.kind))} · ${esc(aiPlayerPayloadSummary(memory.payload))}</strong><span>${esc(aiPlayerStatus(memory.status))}</span></header><details><summary>查看记忆原始结构</summary><pre>${esc(JSON.stringify({ subject_id: memory.subject_id, kind: memory.kind, status: memory.status, payload: memory.payload, evidence_refs: memory.evidence_refs }, null, 2))}</pre></details></article>`).join('')
  const skillCards = (payload.skills || []).map((skill) => {
    const validation = skill.latest_validation
    const validationSummary = validation
      ? `${aiPlayerStatus(validation.status)} · ${validation.successful_run_count}/${validation.total_run_count} 次正确 · token 降幅 ${(Number(validation.token_reduction_rate || 0) * 100).toFixed(0)}% · 时延降幅 ${(Number(validation.latency_reduction_rate || 0) * 100).toFixed(0)}%`
      : '尚无独立验收结论'
    const detailHref = `/api/game-observatory/ai-player/skills/${encodeURIComponent(skill.skill_id)}?environment_id=${encodeURIComponent(identity.id)}`
    return `<article class="ai-row ai-skill-card"><header><strong>${esc(skill.title)}</strong><span class="ai-chip is-${esc(skill.status)}">${esc(skill.level)} · ${esc(aiPlayerStatus(skill.status))}</span></header><p>${esc(skill.applicability)}</p><div class="ai-row-meta"><span>${esc(aiPlayerSkillLayer(skill.skill_layer))}</span><span>${esc(aiPlayerSkillScope(skill.scope))}</span><span>${esc(aiPlayerSkillSafety(skill.safety_level))}</span><span>${esc(aiPlayerExecutor(skill.executor_kind))}</span><span>版本 ${skill.version}</span><span>${skill.run_count || 0} 次运行</span></div><p class="ai-skill-validation">${esc(validationSummary)}</p><details><summary>查看前置、步骤、判定与适用域</summary><dl><dt>前置</dt><dd>${(skill.preconditions || []).map((item) => `<span>${esc(item)}</span>`).join('') || '未登记'}</dd><dt>步骤</dt><dd>${(skill.procedure_steps || []).map((item) => `<span>${esc(item)}</span>`).join('') || '未登记'}</dd><dt>成功</dt><dd>${(skill.success_checks || []).map((item) => `<span>${esc(item)}</span>`).join('') || '未登记'}</dd><dt>失败</dt><dd>${(skill.failure_checks || []).map((item) => `<span>${esc(item)}</span>`).join('') || '未登记'}</dd></dl><a href="${esc(detailHref)}" target="_blank" rel="noopener">打开技能版本、运行与验收原始数据</a></details></article>`
  }).join('')
  const capsuleCards = (payload.capsules || []).slice(0, 4).map((capsule) => `<article class="ai-row"><header><strong>会话胶囊 ${capsule.sequence}</strong><span>${esc(compactDate(capsule.created_at))}</span></header><p>${esc(capsule.stop_reason)}</p><div class="ai-row-meta"><span>活跃任务 ${capsule.active_task_ids?.length || 0}</span><span>待探索 ${capsule.pending_frontier_task_ids?.length || 0}</span><span>剩余动作 ${capsule.remaining_action_budget}</span><span>已知游戏影响 ${capsule.known_external_side_effects?.length || 0}</span></div>${capsule.known_external_side_effects?.length ? `<details><summary>查看已经发生的游戏影响</summary><ul>${capsule.known_external_side_effects.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></details>` : ''}</article>`).join('')
  const blockerCards = (payload.blockers || []).map((blocker) => `<article class="ai-row is-blocker"><header><strong>${esc(blocker.title)}</strong><span>${esc(aiPlayerBlockerKind(blocker.kind))}</span></header><p>${esc(typeof blocker.reason === 'string' ? blocker.reason : aiPlayerPayloadSummary(blocker.reason))}</p>${blocker.reactivation_condition ? `<aside>重新激活：${esc(blocker.reactivation_condition)}</aside>` : ''}</article>`).join('')
  const advisoryCards = (payload.advisories || []).map((advisory) => `<article class="ai-row is-advisory"><header><strong>${esc(aiPlayerMemoryKind(advisory.kind))}</strong><span>不计入阻断</span></header><p>${esc(aiPlayerPayloadSummary(advisory.payload))}</p><details><summary>查看纠错记录</summary><pre>${esc(JSON.stringify(advisory, null, 2))}</pre></details></article>`).join('')
  const stateTitles = Object.fromEntries((payload.state_map?.nodes || []).map((node) => [node.id, node.title]))
  const stateCards = (payload.state_map?.nodes || []).map((node) => `<article class="ai-state-node ${node.id === payload.state_map?.current_state_id ? 'is-current' : ''} is-${esc(node.status)}"><header><strong>${esc(node.title)}</strong><span class="ai-chip is-${esc(node.status)}">${esc(aiPlayerStatus(node.status))}</span></header><p>${esc(node.description)}</p>${node.visuals?.length ? `<div class="ai-node-visuals">${node.visuals.map((item) => aiPlayerEvidenceImage(item, node.title)).join('')}</div>` : ''}<div class="ai-row-meta"><span>${node.tags?.length || 0} 个标签</span></div><details><summary>查看状态元数据</summary><pre>${esc(JSON.stringify(node, null, 2))}</pre></details></article>`).join('')
  const edgeCards = (payload.state_map?.edges || []).map((edge) => `<article class="ai-map-edge is-${esc(edge.outcome)}"><div><strong>${esc(stateTitles[edge.from_state_id] || '未命名起点')}</strong><b>→</b><strong>${esc(stateTitles[edge.to_state_id] || '未确认去向')}</strong></div><p>${esc(aiPlayerOutcome(edge.outcome))} · ${esc(edge.observed_change)}</p>${edge.visuals?.length ? `<div class="ai-edge-visuals">${edge.visuals.map((item) => aiPlayerEvidenceImage(item, edge.observed_change)).join('')}</div>` : ''}<div class="ai-row-meta"><span>${esc(aiPlayerAction(edge.action?.type))}</span></div><details><summary>查看转移元数据</summary><pre>${esc(JSON.stringify(edge, null, 2))}</pre></details></article>`).join('')
  const coverage = payload.coverage || {}
  const gapCards = (payload.frontier || []).filter((task) => ['missing_transition', 'coverage_gap', 'interface_family_gap'].includes(task.source)).map((task) => `<article class="ai-coverage-gap is-${esc(task.status)}"><header><strong>${esc(aiPlayerSource(task.source))}</strong><span>${esc(aiPlayerStatus(task.status))}</span></header><p>${esc(aiPlayerHumanText(task.title))}：${esc(aiPlayerHumanText(task.reason))}</p>${task.blocked_reason ? `<aside>${esc(aiPlayerHumanText(task.blocked_reason))}；恢复条件：${esc(aiPlayerHumanText(task.reactivation_condition))}</aside>` : ''}</article>`).join('')
  const stepArtifactIds = new Set((payload.evidence?.steps || []).flatMap((step) => [step.before?.id, step.after?.id]).filter(Boolean))
  const directImages = (payload.evidence?.artifacts || []).filter((item) => item.is_visual && !stepArtifactIds.has(item.id)).map((item) => aiPlayerEvidenceImage(item, '补充证据')).join('')
  return `<section class="ai-player-state" data-ai-player-state>
    <header class="ai-state-head"><div><strong>当前持久状态</strong><span>${esc(aiPlayerGameName(identity.game_id))} · ${esc(aiPlayerChannel(identity.channel))} · ${identity.account_scope_id?.includes('prelogin') ? '登录前环境' : '纯 AI 账号'}</span></div><label>当前环境<select data-ai-environment>${environmentOptions.map((item) => `<option value="${esc(item.environment_id)}" ${item.environment_id === identity.id ? 'selected' : ''}>${esc(aiPlayerGameName(item.game_id))} · ${item.account_scope_id?.includes('prelogin') ? '登录前' : '当前账号'}</option>`).join('')}</select></label></header>
    <div class="ai-identity-strip"><div><span>环境谱系</span><div class="ai-lineage">${lineage}</div><details class="ai-identity-meta"><summary>环境技术信息</summary><pre>${esc(JSON.stringify(identity, null, 2))}</pre></details></div><dl><div><dt>游戏</dt><dd>${esc(aiPlayerGameName(identity.game_id))}</dd></div><div><dt>渠道</dt><dd>${esc(aiPlayerChannel(identity.channel))}</dd></div><div><dt>服务器 / 世界</dt><dd>${esc(identityPlace.server)} / ${esc(identityPlace.world)}</dd></div><div><dt>当前状态</dt><dd>${esc(payload.current_state?.title || '尚未落账')}${esc(currentStateSuffix)}</dd></div></dl></div>
    <div class="ai-metrics"><div><strong>${payload.frontier?.length || 0}</strong><span>持续任务与前沿</span></div><div><strong>${payload.memories?.length || 0}</strong><span>长期记忆</span></div><div><strong>${coverage.skill_version_count || 0}</strong><span>技能版本</span></div><div><strong>${payload.blockers?.length || 0}</strong><span>具名阻断</span></div><div><strong>${budget.actions_remaining ?? '—'}</strong><span>剩余动作 · ${esc(budgetSource)}</span></div><div><strong>${budget.tokens_remaining ?? '—'}</strong><span>剩余 token · ${esc(budgetSource)}</span></div></div>
    <div class="ai-state-grid">
      ${aiPlayerSessionMarkup(sessionPayload, payload)}
      ${aiPlayerDailyContinuityMarkup(payload)}
      ${aiPlayerPathReuseMarkup(payload)}
      ${aiPlayerIterationMarkup(payload)}
      <section class="ai-panel ai-map-panel"><header><h2>近期状态地图</h2><span>显示 ${payload.state_map?.nodes?.length || 0}/${coverage.semantic_state_latest_entity_count || 0} 个状态 · ${payload.state_map?.edges?.length || 0}/${Object.values(coverage.transitions_by_outcome || {}).reduce((sum, value) => sum + Number(value), 0)} 条转移</span></header><div class="ai-map-layout"><div class="ai-map-nodes">${stateCards || '<p class="ai-empty">当前环境尚无语义状态。</p>'}</div><div class="ai-map-edges">${edgeCards || '<p class="ai-empty">当前环境尚无状态转移。</p>'}</div></div></section>
      <section class="ai-panel ai-coverage-panel"><header><h2>覆盖视图</h2><span>只统计已落账对象</span></header><div class="ai-coverage-summary"><div><strong>${coverage.observed_count || 0}</strong><span>已观测</span></div><div><strong>${coverage.pending_adjudication_count || 0}</strong><span>待裁决状态</span></div><div><strong>${coverage.missing_transition_count || 0}</strong><span>缺失转移</span></div><div><strong>${coverage.blocked_frontier_count || 0}</strong><span>阻断前沿</span></div></div><div class="ai-coverage-groups"><dl><dt>前沿来源</dt><dd>${aiPlayerCountRows(coverage.tasks_by_source, aiPlayerSource)}</dd></dl><dl><dt>任务状态</dt><dd>${aiPlayerCountRows(coverage.tasks_by_status, aiPlayerStatus)}</dd></dl><dl><dt>状态成熟度</dt><dd>${aiPlayerCountRows(coverage.states_by_status, aiPlayerStatus)}</dd></dl><dl><dt>转移结果</dt><dd>${aiPlayerCountRows(coverage.transitions_by_outcome, aiPlayerOutcome)}</dd></dl></div><div class="ai-coverage-gaps">${gapCards || '<p class="ai-empty">当前没有落账的缺边或覆盖缺口任务。</p>'}</div></section>
      ${aiPlayerGameplayMarkup(payload)}
      ${aiPlayerAccountPolicyMarkup(payload)}
      ${aiPlayerSpeechMarkup(payload)}
      ${aiPlayerGuideMarkup(payload)}
      <section class="ai-panel"><header><h2>持续任务与探索前沿</h2><span>${payload.frontier?.length || 0} 项</span></header><div class="ai-list">${taskCards || '<p class="ai-empty">当前环境没有开放前沿。</p>'}</div></section>
      <section class="ai-panel"><header><h2>长期记忆</h2><span>${ordinaryMemories.length} 条</span></header><div class="ai-list">${memoryCards || '<p class="ai-empty">还没有长期记忆。</p>'}</div></section>
      <section class="ai-panel"><header><h2>注意事项与纠错记录</h2><span>${payload.advisories?.length || 0} 条</span></header><div class="ai-list">${advisoryCards || '<p class="ai-empty">当前没有注意事项。</p>'}</div></section>
      <section class="ai-panel"><header><h2>技能与分层自动化</h2><span>显示 ${payload.skills?.length || 0}/${coverage.skill_version_count || 0} 个</span></header><div class="ai-list">${skillCards || '<p class="ai-empty">尚无经过落账的技能版本。</p>'}</div></section>
      <section class="ai-panel"><header><h2>会话胶囊与预算</h2><span>${payload.capsules?.length || 0} 份</span></header><div class="ai-list">${capsuleCards || '<p class="ai-empty">尚无可恢复会话胶囊。</p>'}</div></section>
      <section class="ai-panel ${payload.blockers?.length ? 'has-blockers' : ''}"><header><h2>阻断与恢复条件</h2><span>${payload.blockers?.length || 0} 项</span></header><div class="ai-list">${blockerCards || '<p class="ai-empty">当前没有已落账阻断项。</p>'}</div></section>
      <section class="ai-panel ai-evidence-panel"><header><h2>最新证据</h2><span>操作前后图与点击位置 · 共 ${payload.evidence?.steps?.length || 0} 步</span></header><div class="ai-evidence-list" data-ai-evidence-list>${aiPlayerEvidenceMarkup(payload)}</div>${directImages ? `<details class="ai-direct-evidence"><summary>未包含在步骤中的补充图像</summary><div>${directImages}</div></details>` : ''}</section>
    </div>
  </section>`
}

function bindAiPlayerStateControls(aiPlayerPayload) {
  document.querySelector('[data-ai-environment]')?.addEventListener('change', (event) => {
    const next = new URL(location.href)
    next.searchParams.set('environment', event.currentTarget.value)
    location.href = next.toString()
  })
  document.querySelector('[data-ai-evidence-list]')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-ai-show-more]')
    if (!button) return
    const nextCount = Number(button.dataset.visibleCount || 0) + 4
    event.currentTarget.innerHTML = aiPlayerEvidenceMarkup(aiPlayerPayload, nextCount)
  })
}

async function renderConsole() {
  const query = new URLSearchParams(location.search)
  const requestedEnvironmentId = query.get('environment')
  const aiStateUrl = `/api/game-observatory/ai-player/console${requestedEnvironmentId ? `?environment_id=${encodeURIComponent(requestedEnvironmentId)}` : ''}`
  const aiPlayerPayload = await safeJson(aiStateUrl, { selection: null, environment_options: [] })
  const sessionPayload = aiPlayerPayload.identity?.id
    ? await safeJson(`/api/game-observatory/ai-player/sessions?environment_id=${encodeURIComponent(aiPlayerPayload.identity.id)}&limit=20`, { sessions: [] })
    : { sessions: [] }
  workspace.innerHTML = `<header class="console-compact-title"><strong>AI 玩家控制台</strong><span>持久状态、探索覆盖与证据</span></header>
  ${aiPlayerStateMarkup(aiPlayerPayload, sessionPayload)}
  <details class="console-device-tools"><summary>设备操作与证据录制</summary><div class="empty-state"><strong>正在读取设备设施</strong></div></details>`
  bindAiPlayerStateControls(aiPlayerPayload)

  const [targetPayload, leasePayload, runPayload] = await Promise.all([
    safeJson('/api/game-observatory/targets', { targets: [] }),
    safeJson('/api/game-observatory/leases', { leases: [] }),
    safeJson('/api/game-observatory/evidence-runs?limit=30', { runs: [] }),
  ])
  state.targets = targetPayload.targets || []
  state.leases = leasePayload.leases || []
  state.runs = runPayload.runs || []
  const requestedRunId = query.get('run')
  const requestedTargetId = query.get('target')
  const requestedRun = state.runs.find((run) => run.id === requestedRunId)
  const selectedTarget = state.targets.find((target) => target.id === requestedTargetId)
    || state.targets.find((target) => target.id === requestedRun?.target_id)
    || state.targets.find((target) => target.id === preferredRun(state.runs)?.target_id)
    || state.targets.find((target) => target.status === 'online')
    || state.targets[0]
  const targetRuns = state.runs.filter((run) => run.target_id === selectedTarget?.id)
  const activeRun = targetRuns.find((run) => run.id === requestedRunId)
    || targetRuns.find((run) => run.status === 'running')
    || targetRuns.find((run) => run.status === 'paused')
    || targetRuns[0]
  const [detail, controlPayload] = activeRun || selectedTarget ? await Promise.all([
    activeRun ? safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(activeRun.id)}`, null) : null,
    selectedTarget ? safeJson(`/api/game-observatory/gateway/controls/${encodeURIComponent(selectedTarget.id)}`, { control: null }) : { control: null },
  ]) : [null, { control: null }]
  const control = controlPayload?.control
  const lastStep = detail?.steps?.[detail.steps.length - 1]
  const activeLease = state.leases.find((lease) => lease.target_id === selectedTarget?.id && lease.status === 'active')
  const localHolder = 'game-observatory-local-console'
  const tokenKey = selectedTarget ? `game-observatory.lease-token.${selectedTarget.id}` : ''
  const leaseToken = tokenKey ? sessionStorage.getItem(tokenKey) : null
  const ownsLease = Boolean(leaseToken && activeLease?.holder === localHolder)
  const canRecord = Boolean(
    ownsLease
    && activeRun?.status === 'running'
    && activeRun.target_id === selectedTarget?.id
    && !control?.emergency_stopped
  )
  const canRerun = Boolean(
    ownsLease
    && selectedTarget?.status === 'online'
    && activeRun
    && ['passed', 'failed', 'stopped'].includes(activeRun.status)
    && !control?.emergency_stopped
  )
  const canPause = Boolean(ownsLease && activeRun?.status === 'running')
  const canResume = Boolean(
    ownsLease
    && selectedTarget?.status === 'online'
    && activeRun?.status === 'paused'
    && !control?.emergency_stopped
  )
  const canCreateRun = Boolean(
    ownsLease
    && (!activeRun || ['passed', 'failed', 'stopped'].includes(activeRun.status))
  )
  const viewportWidth = detail?.run?.viewport_width || 1080
  const viewportHeight = detail?.run?.viewport_height || 1920
  workspace.innerHTML = `<header class="console-compact-title"><strong>AI 玩家控制台</strong><span>持久状态、探索覆盖与证据</span></header>
  ${aiPlayerStateMarkup(aiPlayerPayload, sessionPayload)}
  <details class="console-device-tools"><summary>设备操作与证据录制</summary><div class="console-grid">
    <aside>${state.targets.map((target) => { const lease = state.leases.find((item) => item.target_id === target.id && item.status === 'active'); return `<a class="target-card ${target.id === selectedTarget?.id ? 'is-active' : ''}" href="?target=${encodeURIComponent(target.id)}"><header><strong>${esc(target.label || target.metadata?.endpoint || target.id)}</strong>${status(target.status)}</header><p>${esc(target.kind)} · ${esc(target.metadata?.endpoint || target.endpoint || '')}</p><div class="meta"><span>${lease ? `租约 · ${esc(lease.holder)}` : '无活动租约'}</span></div></a>` }).join('') || '<div class="empty-state"><strong>没有设备目标</strong></div>'}</aside>
    <section class="console-screen"><div class="evidence-title"><div><h2>${esc(selectedTarget?.label || '未选择设备')}</h2><p>${activeRun ? `${esc(runLabel(activeRun))} · ${humanStatus(activeRun.status)}` : '没有关联运行'}</p></div>${status(control?.emergency_stopped ? 'failed' : (activeLease ? 'running' : 'idle'))}</div>
      ${targetRuns.length ? `<label class="console-run-select">证据运行<select data-console-run-select><option value="">选择运行</option>${targetRuns.map((run) => `<option value="${esc(run.id)}" ${run.id === activeRun?.id ? 'selected' : ''}>${esc(runLabel(run))} · ${humanStatus(run.status)}</option>`).join('')}</select></label>` : ''}
      <div class="console-toolbar">
        <button class="button" data-console-refresh>刷新设备</button>
        <button class="button" data-console-acquire ${selectedTarget?.status !== 'online' || activeLease ? 'disabled' : ''}>取得租约</button>
        <button class="button" data-console-renew ${ownsLease ? '' : 'disabled'}>续租五分钟</button>
        <button class="button" data-console-release ${ownsLease ? '' : 'disabled'}>释放租约</button>
        <button class="button" data-console-pause ${canPause ? '' : 'disabled'}>暂停运行</button>
        <button class="button" data-console-resume ${canResume ? '' : 'disabled'}>继续运行</button>
        <button class="button is-primary" data-console-complete ${canRecord ? '' : 'disabled'}>完成运行</button>
        <button class="button" data-console-rerun ${canRerun ? '' : 'disabled'}>按此范围重跑</button>
        <button class="button is-danger" data-console-stop ${selectedTarget ? '' : 'disabled'}>紧急停止</button>
        ${control?.emergency_stopped ? '<button class="button" data-console-clear-stop>解除紧急停止</button>' : ''}
      </div>
      ${control?.emergency_stopped ? `<aside class="console-alert"><strong>设备动作已锁止</strong><span>${esc(control.emergency_reason || '未记录原因')} · ${esc(compactDate(control.emergency_stopped_at))}</span></aside>` : ''}
      <div class="frame-stage" data-console-frame data-viewport-width="${viewportWidth}" data-viewport-height="${viewportHeight}">${lastStep?.after_frame_id ? frameMarkup(lastStep.after_frame_id, lastStep, detail.run, false) : '<div class="empty-state"><strong>没有当前画面</strong><span>开始证据任务后会显示完整设备画面。</span></div>'}</div>
      <form class="console-action-form" data-console-action-form>
        <label>目标名称<input name="target_name" value="未命名可见目标" ${canRecord ? '' : 'disabled'}></label>
        <label>动作<select name="action_type" ${canRecord ? '' : 'disabled'}><option value="tap">点击</option><option value="swipe">单指滑动</option><option value="pinch">双指缩放</option><option value="two_finger_swipe">双指平移</option><option value="back">系统返回</option><option value="wait">等待</option></select></label>
        <label>X<input name="x" type="number" min="0" max="${viewportWidth - 1}" ${canRecord ? '' : 'disabled'}></label>
        <label>Y<input name="y" type="number" min="0" max="${viewportHeight - 1}" ${canRecord ? '' : 'disabled'}></label>
        <label>X2<input name="x2" type="number" min="0" max="${viewportWidth - 1}" ${canRecord ? '' : 'disabled'}></label>
        <label>Y2<input name="y2" type="number" min="0" max="${viewportHeight - 1}" ${canRecord ? '' : 'disabled'}></label>
        <label>框宽<input name="bounds_width" type="number" min="1" max="${viewportWidth}" value="80" ${canRecord ? '' : 'disabled'}></label>
        <label>框高<input name="bounds_height" type="number" min="1" max="${viewportHeight}" value="80" ${canRecord ? '' : 'disabled'}></label>
        <label>缩放方向<select name="pinch_direction" ${canRecord ? '' : 'disabled'}><option value="out">放大</option><option value="in">缩小</option></select></label>
        <label>等待秒数<input name="seconds" type="number" min="0.1" max="30" step="0.1" value="0.8" ${canRecord ? '' : 'disabled'}></label>
        <button class="button is-primary" type="submit" ${canRecord ? '' : 'disabled'}>记录并执行</button>
      </form>
      ${canCreateRun ? `<details class="tech"><summary>创建证据运行</summary><form class="console-run-form" data-console-run-form>
        <label>游戏 ID<input name="game_id" value="${esc(activeRun?.game_id || 'afk-journey')}"></label>
        <label>Build<input name="build_scope_id" value="${esc(activeRun?.build_scope_id || '')}"></label>
        <label>范围 ID<input name="scope_id" value="${esc(activeRun?.scope_id || 'local-console-exploration')}"></label>
        <label>宽<input name="viewport_width" type="number" min="1" value="${viewportWidth}"></label>
        <label>高<input name="viewport_height" type="number" min="1" value="${viewportHeight}"></label>
        <button class="button is-primary" type="submit">创建并绑定</button>
      </form></details>` : ''}
      <p class="console-boundary">${ownsLease ? (canRecord ? '本控制台持有租约。每个动作都会生成完整 EvidenceStep。' : '本控制台持有租约，但当前没有可追加的运行或设备已紧急停止。') : (activeLease ? `设备由 ${esc(activeLease.holder)} 独占；本控制台保持只读。` : '设备租约已释放。取得租约后，动作仍必须绑定到运行并保存完整证据。')}</p>
      <details class="tech"><summary>运行与安全状态</summary><pre>${esc(JSON.stringify({ target: selectedTarget, lease: activeLease || null, run: activeRun || null }, null, 2))}</pre></details>
    </section>
  </div>`

  document.querySelector('[data-console-run-select]')?.addEventListener('change', (event) => {
    const runId = event.currentTarget.value
    if (!runId) return
    location.href = `/game-observatory/console?target=${encodeURIComponent(selectedTarget.id)}&run=${encodeURIComponent(runId)}`
  })
  document.querySelector('[data-console-refresh]')?.addEventListener('click', async () => {
    try {
      await postJson('/api/game-observatory/gateway/refresh', {})
      showToast('设备列表已刷新')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-clear-stop]')?.addEventListener('click', async () => {
    try {
      await postJson('/api/game-observatory/gateway/emergency-stop/clear', {
        target_id: selectedTarget.id,
        actor: localHolder,
      })
      showToast('紧急停止已解除；仍需重新取得租约')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-acquire]')?.addEventListener('click', async () => {
    try {
      const payload = await postJson('/api/game-observatory/leases/acquire', {
        target_id: selectedTarget.id,
        holder: localHolder,
        ttl_seconds: 300,
      })
      sessionStorage.setItem(tokenKey, payload.lease.token)
      showToast('已取得五分钟独占租约')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-renew]')?.addEventListener('click', async () => {
    try {
      await postJson('/api/game-observatory/leases/renew', { token: leaseToken, ttl_seconds: 300 })
      showToast('独占租约已续期五分钟')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-release]')?.addEventListener('click', async () => {
    try {
      await postJson('/api/game-observatory/leases/release', { token: leaseToken, ttl_seconds: 300 })
      sessionStorage.removeItem(tokenKey)
      showToast('租约已释放')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-pause]')?.addEventListener('click', async () => {
    try {
      await postJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(activeRun.id)}/pause`, {
        lease_token: leaseToken,
      })
      showToast('运行已暂停；现有步骤与租约继续保留')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-resume]')?.addEventListener('click', async () => {
    try {
      await postJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(activeRun.id)}/resume`, {
        lease_token: leaseToken,
      })
      showToast('运行已继续，可以追加证据步骤')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-complete]')?.addEventListener('click', async () => {
    try {
      const payload = await postJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(activeRun.id)}/complete`, {
        lease_token: leaseToken,
      })
      showToast(payload.ok ? '运行已完成，manifest 可发布' : '运行已完成，manifest 仍有质量问题', payload.ok ? 'success' : 'error')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-rerun]')?.addEventListener('click', async () => {
    try {
      const payload = await postJson('/api/game-observatory/evidence-runs', {
        target_id: selectedTarget.id,
        lease_token: leaseToken,
        viewport_width: activeRun.viewport_width,
        viewport_height: activeRun.viewport_height,
        game_id: activeRun.game_id || null,
        build_scope_id: activeRun.build_scope_id || null,
        scope_id: activeRun.scope_id || null,
        environment: { created_by: 'game-observatory-local-console', rerun_of: activeRun.id },
      })
      showToast('同范围新运行已创建')
      location.href = `/game-observatory/console?target=${encodeURIComponent(selectedTarget.id)}&run=${encodeURIComponent(payload.run.id)}`
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-stop]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget
    if (button.dataset.confirmed !== 'true') {
      button.dataset.confirmed = 'true'
      button.textContent = '再次点击确认停止'
      button.classList.add('is-armed')
      showToast('五秒内再次点击才会停止设备动作', 'error')
      setTimeout(() => {
        if (!button.isConnected) return
        button.dataset.confirmed = 'false'
        button.textContent = '紧急停止'
        button.classList.remove('is-armed')
      }, 5000)
      return
    }
    try {
      await postJson('/api/game-observatory/gateway/emergency-stop', {
        target_id: selectedTarget.id,
        reason: 'Local console emergency stop',
        actor: localHolder,
      })
      sessionStorage.removeItem(tokenKey)
      showToast('设备动作已紧急停止', 'error')
      await renderConsole()
    } catch (error) { showToast(error.message, 'error') }
  })
  document.querySelector('[data-console-frame]')?.addEventListener('click', (event) => {
    if (!canRecord) return
    const image = event.currentTarget.querySelector('.frame-content')
    if (!image) return
    const rect = image.getBoundingClientRect()
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) return
    const x = Math.max(0, Math.min(viewportWidth - 1, Math.round((event.clientX - rect.left) / rect.width * viewportWidth)))
    const y = Math.max(0, Math.min(viewportHeight - 1, Math.round((event.clientY - rect.top) / rect.height * viewportHeight)))
    const form = document.querySelector('[data-console-action-form]')
    form.elements.x.value = x
    form.elements.y.value = y
  })
  document.querySelector('[data-console-action-form]')?.addEventListener('submit', async (event) => {
    event.preventDefault()
    if (!canRecord) return
    const form = event.currentTarget
    const type = form.elements.action_type.value
    const number = (name) => form.elements[name].value === '' ? null : Number(form.elements[name].value)
    const x = number('x')
    const y = number('y')
    const x2 = number('x2')
    const y2 = number('y2')
    if (['tap', 'swipe', 'pinch', 'two_finger_swipe'].includes(type) && (x == null || y == null)) {
      showToast('请在画面上选择动作起点', 'error')
      return
    }
    if (['swipe', 'two_finger_swipe'].includes(type) && (x2 == null || y2 == null)) {
      showToast('滑动动作需要 X2 与 Y2', 'error')
      return
    }
    const action = { type }
    if (['tap', 'swipe', 'pinch', 'two_finger_swipe'].includes(type)) Object.assign(action, { x, y })
    if (['swipe', 'two_finger_swipe'].includes(type)) Object.assign(action, { x2, y2, duration_ms: 450 })
    if (type === 'pinch') Object.assign(action, { pinch_direction: form.elements.pinch_direction.value, pinch_percent: 0.35, pinch_steps: 8 })
    if (type === 'two_finger_swipe') Object.assign(action, { two_finger_offset_x: 0, two_finger_offset_y: 80, two_finger_steps: 8 })
    if (type === 'wait') Object.assign(action, { seconds: Number(form.elements.seconds.value || 0.8) })
    const bounded = ['tap', 'swipe', 'pinch', 'two_finger_swipe'].includes(type) && x != null && y != null
    let width = Math.max(1, Math.min(viewportWidth, Number(form.elements.bounds_width.value || 80)))
    let height = Math.max(1, Math.min(viewportHeight, Number(form.elements.bounds_height.value || 80)))
    let boundsX = x == null ? 0 : x - Math.round(width / 2)
    let boundsY = y == null ? 0 : y - Math.round(height / 2)
    if (['swipe', 'two_finger_swipe'].includes(type)) {
      const offsetY = type === 'two_finger_swipe' ? 80 : 0
      const pointsX = [x, x2]
      const pointsY = [y, y2, y + offsetY, y2 + offsetY]
      const padding = 35
      boundsX = Math.max(0, Math.min(...pointsX) - padding)
      boundsY = Math.max(0, Math.min(...pointsY) - padding)
      width = Math.min(viewportWidth - boundsX, Math.max(...pointsX) - boundsX + padding)
      height = Math.min(viewportHeight - boundsY, Math.max(...pointsY) - boundsY + padding)
    }
    const bounds = bounded ? {
      x: Math.max(0, Math.min(viewportWidth - width, boundsX)),
      y: Math.max(0, Math.min(viewportHeight - height, boundsY)),
      width,
      height,
    } : null
    try {
      form.querySelector('button[type="submit"]').disabled = true
      const payload = await postJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(activeRun.id)}/steps`, {
        lease_token: leaseToken,
        action,
        target_name: form.elements.target_name.value || null,
        target_bounds: bounds,
        settle_timeout_seconds: 6,
        sample_interval_seconds: 0.25,
      })
      showToast(`EvidenceStep ${payload.step.step_index} 已保存`)
      await renderConsole()
    } catch (error) {
      showToast(error.message, 'error')
      form.querySelector('button[type="submit"]').disabled = false
    }
  })
  document.querySelector('[data-console-run-form]')?.addEventListener('submit', async (event) => {
    event.preventDefault()
    const form = event.currentTarget
    try {
      const payload = await postJson('/api/game-observatory/evidence-runs', {
        target_id: selectedTarget.id,
        lease_token: leaseToken,
        viewport_width: Number(form.elements.viewport_width.value),
        viewport_height: Number(form.elements.viewport_height.value),
        game_id: form.elements.game_id.value || null,
        build_scope_id: form.elements.build_scope_id.value || null,
        scope_id: form.elements.scope_id.value || null,
        environment: { created_by: 'game-observatory-local-console' },
      })
      showToast('新证据运行已创建')
      location.href = `/game-observatory/console?target=${encodeURIComponent(selectedTarget.id)}&run=${encodeURIComponent(payload.run.id)}`
    } catch (error) { showToast(error.message, 'error') }
  })
}

function scrollToRequestedHash() {
  if (!location.hash) return
  let targetId = location.hash.slice(1)
  try { targetId = decodeURIComponent(targetId) } catch (_error) { return }
  const scroll = () => document.getElementById(targetId)?.scrollIntoView({ block: 'start' })
  requestAnimationFrame(scroll)
  setTimeout(scroll, 350)
}

async function main() {
  const surface = surfaceName()
  markNavigation(surface)
  try {
    await loadCommon()
    const renderers = {
      overview: renderOverview,
      game: renderGame,
      group: renderGroup,
      play: renderPlay,
      demo: renderDemo,
      search: renderSearch,
      spec: renderSpec,
      evidence: renderEvidence,
      coverage: renderCoverage,
      sources: renderSources,
      feedback: renderFeedback,
      interpretation: renderInterpretation,
      reader: renderReaderPreview,
      console: renderConsole,
    }
    await (renderers[surface] || renderOverview)()
    bindImageViewer()
    scrollToRequestedHash()
  } catch (error) {
    console.error(error)
    workspace.innerHTML = `<div class="error-panel"><strong>设施页面读取失败</strong><p>${esc(error.message)}</p></div>`
  }
}

main()
