const workspace = document.querySelector('[data-testid="game-observatory-studio"]')

const state = {
  health: null,
  specs: [],
  runs: [],
  targets: [],
  leases: [],
  snapshots: [],
  voices: [],
  ledgers: [],
  currentRun: null,
  currentStep: null,
}

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[character]))

const compactDate = (value) => value ? String(value).replace('T', ' ').slice(0, 19) : '—'
const humanStatus = (value) => ({
  running: '记录中', passed: '已通过', failed: '失败', stopped: '已停止',
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
  document.querySelectorAll('[data-surface]').forEach((item) => {
    item.classList.toggle('is-active', item.dataset.surface === surface)
  })
}

function head(kicker, title, copy, phase = 'Gate 3 · 设施预览') {
  return `<header class="page-head">
    <div><p class="page-kicker">${esc(kicker)}</p><h1>${esc(title)}</h1><p>${esc(copy)}</p></div>
    <div class="phase-chip"><span>当前边界</span><strong>${esc(phase)}</strong></div>
  </header>`
}

function status(value) {
  return `<span class="status is-${esc(value)}">${esc(humanStatus(value))}</span>`
}

function runLabel(run) {
  const scope = run.scope_id || run.game_id || '未命名范围'
  return `${scope} · ${run.step_ids?.length || 0} 步`
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
  const [specPayload, runPayload, targetPayload, ledgerPayload] = await Promise.all([
    safeJson('/api/game-observatory/workspace/design-specs', { design_specs: [] }),
    safeJson('/api/game-observatory/evidence-runs?limit=100', { runs: [] }),
    safeJson('/api/game-observatory/targets', { targets: [] }),
    safeJson('/api/game-observatory/saturation-ledgers', { ledgers: [] }),
  ])
  state.specs = specPayload.design_specs || []
  state.runs = runPayload.runs || []
  state.targets = targetPayload.targets || []
  state.ledgers = ledgerPayload.ledgers || []
  const counts = state.health?.counts || {}
  const activeRun = preferredRun(state.runs)
  const passedLedger = state.ledgers.filter((item) => item.validation?.saturation_pass).length
  workspace.innerHTML = `${head(
    'FACILITY MAP',
    '当前设施可以独立走查',
    '部分游戏事实仍在采集。采集、证据、事实模型、覆盖审查、来源、反馈、理解与设备控制已经拥有彼此隔离的本地入口。',
  )}
  <section class="metric-grid" aria-label="设施计数">
    <article class="metric"><strong>${counts.artifacts || 0}</strong><span>证据 artifact</span><small>原始文件与 hash 入库</small></article>
    <article class="metric"><strong>${state.runs.length}</strong><span>近期游玩记录</span><small>${activeRun ? humanStatus(activeRun.status) : '暂无运行'}</small></article>
    <article class="metric"><strong>${state.specs.length}</strong><span>事实案工作对象</span><small>含草稿与历史对象</small></article>
    <article class="metric"><strong>${passedLedger}/${state.ledgers.length}</strong><span>有限域饱和账本</span><small>${state.ledgers.length ? '按真实证据验收' : '等待首份账本'}</small></article>
  </section>
  <section class="surface-grid">
    ${[
      ['FACT SPEC', '事实案工作区', '按 Screen、Element、Interaction、Mechanic 与 Resource 分开查看。', '/game-observatory/studio/spec'],
      ['EVIDENCE', '完整游玩轨迹', '逐步打开 Before、操作位置、After 与相邻视频。', '/game-observatory/studio/evidence'],
      ['SATURATION', '探索覆盖台', '查看候选、无变化、错误上下文、遗漏与饱和门状态。', '/game-observatory/studio/coverage'],
      ['PROVENANCE', '来源与 artifact', '保留游戏版本、来源快照、文件 hash 与证据反向链接。', '/game-observatory/studio/sources'],
      ['PLAYER VOICE', '玩家反馈', '社群原始来源与主题绑定拥有独立页面。', '/game-observatory/studio/feedback'],
      ['INTERPRETATION', '理解', '推测和判断单独呈现，不进入事实正文。', '/game-observatory/studio/interpretation'],
      ['AI PLAYER', 'AI 玩家控制台', '设备、租约、运行、画面、动作锁与紧急停止集中管理。', '/game-observatory/console'],
      ['PUBLIC READER', '公开资料库', '只接收完成发布门的事实案；当前旧样例将退出公开投影。', '/game-observatory/'],
    ].map(([kicker, title, copy, href]) => `<a class="surface-card" href="${href}"><span>${kicker}</span><h2>${title}</h2><p>${copy}</p></a>`).join('')}
  </section>`
}

function sectionCount(report, key) {
  const spec = report?.design_spec || {}
  const value = spec[key] ?? report?.[key]
  return Array.isArray(value) ? value.length : value ? 1 : 0
}

async function renderSpec() {
  const payload = await safeJson('/api/game-observatory/workspace/design-specs', { design_specs: [] })
  state.specs = payload.design_specs || []
  const requested = new URLSearchParams(location.search).get('report')
  const selected = state.specs.find((item) => item.report.id === requested) || state.specs[0]
  const report = selected?.report
  const options = state.specs.map((item) => `<option value="${esc(item.report.id)}" ${item === selected ? 'selected' : ''}>${esc(item.report.system_title)} · ${humanStatus(item.report.status)}</option>`).join('')
  workspace.innerHTML = `${head(
    'FACT AUTHORING',
    '事实案工作区',
    '这里检查事实对象和发布缺口。完整轨迹仍可先进入证据库，只有经过范围与来源审查的对象才会进入公开资料库。',
  )}
  <div class="toolbar"><label>工作对象<select data-spec-select>${options || '<option>暂无对象</option>'}</select></label></div>
  ${report ? `<div class="spec-board">
    <nav class="spec-outline" aria-label="事实案结构">
      <a href="#scope">系统边界</a><a href="#screens">界面规格</a><a href="#elements">元素规格</a>
      <a href="#interactions">交互规格</a><a href="#mechanics">机制规格</a><a href="#resources">资源规格</a>
      <a href="#publication">发布门</a>
    </nav>
    <div class="spec-canvas">
      <section class="spec-section" id="scope"><span>SYSTEM SCOPE</span><h2>${esc(report.system_title)}</h2><p>${esc(report.summary || '当前对象尚未补充范围摘要。')}</p><div class="contract-grid">
        <div class="contract-item"><strong>${esc(report.game_title)}</strong><small>${esc(report.scope?.platform)} · ${esc(report.scope?.version)} · ${esc(report.scope?.locale || '')}</small></div>
        <div class="contract-item"><strong>${humanStatus(report.status)}</strong><small>${esc(report.migration_status)} · revision ${selected.current_revision || 0}</small></div>
      </div></section>
      ${[
        ['screens', 'SCREEN SPEC', '界面规格', sectionCount(report, 'surfaces'), '完整画面、进入条件、区域、可见信息、状态与返回行为'],
        ['elements', 'UI ELEMENT', '元素规格', report.surfaces?.reduce((sum, item) => sum + (item.elements?.length || 0), 0) || 0, '原图坐标、显示条件、输入、即时反馈与目标状态'],
        ['interactions', 'INTERACTION', '交互规格', sectionCount(report, 'interactions') || sectionCount(report, 'flow'), '来源状态、准确位置、动作、反馈、转换、结果与撤销'],
        ['mechanics', 'MECHANIC', '机制规格', sectionCount(report, 'mechanisms'), '输入、条件、规则、状态变化、输出、上限与例外'],
        ['resources', 'RESOURCE', '资源规格', sectionCount(report, 'resources'), '显示位置、已验证来源、消耗、数量与不足表现'],
      ].map(([id, kicker, title, count, copy]) => `<section class="spec-section" id="${id}"><span>${kicker}</span><h2>${title} · ${count}</h2><p>${copy}</p></section>`).join('')}
      <section class="spec-section" id="publication"><span>PUBLICATION GATE</span><h2>发布门</h2>${selected.publication_issues?.length
        ? `<ul class="issue-list">${selected.publication_issues.map((issue) => `<li>${esc(issue)}</li>`).join('')}</ul>`
        : '<p>当前模型校验没有结构错误；内容事实仍需由有限域饱和和人工审阅决定。</p>'}</section>
    </div>
  </div>` : '<div class="empty-state"><strong>没有事实案工作对象</strong><span>先从证据轨迹提升经过审阅的事实对象。</span></div>'}`
  document.querySelector('[data-spec-select]')?.addEventListener('change', (event) => {
    location.href = `/game-observatory/studio/spec?report=${encodeURIComponent(event.target.value)}`
  })
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

function evidenceDetail(detail, step) {
  const run = detail.run
  const action = step.action || {}
  return `<div class="evidence-title"><div><h2>步骤 ${step.step_index} · ${esc(step.target_name || action.type)}</h2><p>${status(step.status)} · ${esc(action.type)} · ${compactDate(step.started_at)}</p></div></div>
  <div class="frame-pair">
    <article class="frame-card"><h3>BEFORE / 操作位置</h3><div class="frame-stage">${frameMarkup(step.before_frame_id, step, run, true)}</div></article>
    <article class="frame-card"><h3>AFTER / 到达状态</h3><div class="frame-stage">${frameMarkup(step.after_frame_id, step, run, false)}</div></article>
  </div>
  <div class="action-card"><span>动作 ${esc(action.type)}</span>${step.source_point ? `<span>起点 ${step.source_point.x}, ${step.source_point.y}</span>` : ''}${step.source_end_point ? `<span>终点 ${step.source_end_point.x}, ${step.source_end_point.y}</span>` : ''}<span>稳定帧 ${step.stability?.sampled_frames || 0}</span></div>
  ${step.video_artifact_id ? `<details class="tech"><summary>相邻视频</summary><video controls preload="metadata" style="display:block;width:100%;max-height:620px" src="/api/game-observatory/internal/artifacts/${encodeURIComponent(step.video_artifact_id)}"></video></details>` : ''}
  <details class="tech"><summary>技术详情</summary><pre>${esc(JSON.stringify(step, null, 2))}</pre></details>`
}

async function renderEvidence() {
  const payload = await safeJson('/api/game-observatory/evidence-runs?limit=100', { runs: [] })
  state.runs = payload.runs || []
  const selected = preferredRun(state.runs)
  const detail = selected ? await safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}`, null) : null
  state.currentRun = detail
  state.currentStep = detail?.steps?.[0] || null
  workspace.innerHTML = `${head(
    'EVIDENCE PLAYER',
    '完整游玩轨迹',
    '每个动作保留完整前帧、原图坐标、目标框、后帧与相邻视频。画面使用唯一源坐标系，叠层随图片内容框缩放。',
  )}
  <div class="split-layout">
    <aside class="side-panel"><div class="panel-head"><strong>证据运行</strong><small>${state.runs.length} 条近期记录</small></div><div class="run-list">${state.runs.map((run) => `<a class="run-row ${run.id === selected?.id ? 'is-active' : ''}" href="?run=${encodeURIComponent(run.id)}"><strong>${esc(runLabel(run))}</strong><span>${humanStatus(run.status)} · ${compactDate(run.started_at)}</span></a>`).join('')}</div></aside>
    <section class="content-panel">${detail ? `<div class="evidence-shell"><div class="step-list">${detail.steps.map((step, index) => `<button class="step-row ${index === 0 ? 'is-active' : ''}" data-step-index="${index}"><strong>${step.step_index}. ${esc(step.target_name || step.action.type)}</strong><span>${humanStatus(step.status)} · ${esc(step.action.type)}</span></button>`).join('')}</div><div class="evidence-detail" data-evidence-detail>${evidenceDetail(detail, detail.steps[0])}</div></div>` : '<div class="empty-state"><strong>没有证据运行</strong><span>新运行会出现在这里。</span></div>'}</section>
  </div>`
  document.querySelectorAll('[data-step-index]').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-step-index]').forEach((item) => item.classList.remove('is-active'))
      button.classList.add('is-active')
      const step = detail.steps[Number(button.dataset.stepIndex)]
      state.currentStep = step
      document.querySelector('[data-evidence-detail]').innerHTML = evidenceDetail(detail, step)
    })
  })
}

async function renderCoverage() {
  const [runPayload, ledgerPayload] = await Promise.all([
    safeJson('/api/game-observatory/evidence-runs?limit=100', { runs: [] }),
    safeJson('/api/game-observatory/saturation-ledgers', { ledgers: [] }),
  ])
  state.runs = runPayload.runs || []
  state.ledgers = ledgerPayload.ledgers || []
  const selected = preferredRun(state.runs)
  const detail = selected ? await safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}`, null) : null
  const steps = detail?.steps || []
  const actionTypes = [...new Set(steps.map((item) => item.action.type))]
  const failed = steps.filter((item) => item.status !== 'passed').length
  const named = steps.filter((item) => item.target_name).length
  const invalidContext = [75, 87, 88].filter((index) => steps.some((item) => item.step_index === index))
  const noChange = [86, 89].filter((index) => steps.some((item) => item.step_index === index))
  const ledger = state.ledgers[0]
  workspace.innerHTML = `${head(
    'FINITE DOMAIN',
    '探索覆盖台',
    '有限域账本把界面状态、可见候选、动作结果、回滚与证据逐项绑定。当前 AFK 运行保留为部分覆盖，饱和门仍关闭。',
  )}
  <section class="coverage-board">
    <article class="coverage-card"><h2>本轮证据</h2><strong>${steps.length}</strong><p>${named} 步具有人类可读目标；动作类型覆盖 ${actionTypes.map(esc).join('、') || '无'}。</p></article>
    <article class="coverage-card"><h2>人工裁定</h2><strong>${invalidContext.length}</strong><p>错误上下文步骤：${invalidContext.map((value) => `步骤 ${value}`).join('、') || '无'}。它们只留在内部轨迹。</p><ul class="issue-list">${noChange.map((value) => `<li>步骤 ${value} · 正确上下文中的无可辨变化</li>`).join('')}</ul></article>
    <article class="coverage-card"><h2>饱和门</h2><strong>${ledger?.validation?.saturation_pass ? '通过' : '未通过'}</strong><p>${ledger ? `${ledger.validation.unresolved_candidate_ids?.length || 0} 个未裁决候选，${ledger.validation.unreviewed_state_node_ids?.length || 0} 个未复查状态。` : '首份完整 ledger 尚未提交；探索未完成。'}</p></article>
  </section>
  <div class="card-list" style="margin-top:18px">
    <article class="record-card"><header><h2>有限域验收合同</h2>${status(ledger?.validation?.saturation_pass ? 'passed' : 'running')}</header><div class="contract-grid">
      <div class="contract-item"><strong>候选闭合</strong><small>每个可见安全候选拥有执行、禁止或延期裁定。</small></div>
      <div class="contract-item"><strong>状态递归</strong><small>新页面、弹窗与状态继续进入同一普查。</small></div>
      <div class="contract-item"><strong>独立复查</strong><small>两次干净起点复查为每个状态绑定独立证据。</small></div>
      <div class="contract-item"><strong>事实可回答</strong><small>机制、资源与状态变化能从事实对象和证据直接回答。</small></div>
    </div>${failed ? `<ul class="issue-list"><li>${failed} 个步骤状态未通过。</li></ul>` : ''}</article>
  </div>`
}

async function renderSources() {
  const [snapshotPayload, runPayload] = await Promise.all([
    safeJson('/api/game-observatory/source-snapshots', { snapshots: [] }),
    safeJson('/api/game-observatory/evidence-runs?limit=100', { runs: [] }),
  ])
  state.snapshots = snapshotPayload.snapshots || []
  state.runs = runPayload.runs || []
  const selected = preferredRun(state.runs)
  const detail = selected ? await safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(selected.id)}`, null) : null
  const artifactIds = [...new Set((detail?.steps || []).flatMap((step) => step.artifact_ids || []))]
  workspace.innerHTML = `${head(
    'PROVENANCE',
    '来源与 artifact',
    '来源快照、游戏运行和派生材料分开保存。公开事实回链来源；内部轨迹继续保留文件 hash 与运行语境。',
  )}
  <section class="metric-grid">
    <article class="metric"><strong>${state.snapshots.length}</strong><span>来源快照</span><small>内容 hash 可追踪</small></article>
    <article class="metric"><strong>${artifactIds.length}</strong><span>当前运行 artifact</span><small>${selected ? `${selected.step_ids?.length || 0} 个证据步` : '未选择运行'}</small></article>
    <article class="metric"><strong>${detail?.run?.build_scope_id ? 1 : 0}</strong><span>build 语境</span><small>${esc(detail?.run?.build_scope_id || '尚未绑定')}</small></article>
    <article class="metric"><strong>${detail?.run?.target_id ? 1 : 0}</strong><span>设备语境</span><small>内部可追溯</small></article>
  </section>
  <div class="card-list">${state.snapshots.slice(0, 40).map((snapshot) => `<article class="record-card"><header><h3>${esc(snapshot.metadata?.title || snapshot.source_id)}</h3>${status(snapshot.status)}</header><p>${esc(snapshot.excerpt || '该来源只保存定位和内容指纹。')}</p><div class="meta"><span>${compactDate(snapshot.captured_at)}</span><span>sha256 ${esc(snapshot.content_sha256?.slice(0, 16))}</span></div><details class="tech"><summary>来源详情</summary><pre>${esc(JSON.stringify(snapshot, null, 2))}</pre></details></article>`).join('') || '<div class="empty-state"><strong>没有来源快照</strong><span>来源采集后会在这里显示。</span></div>'}</div>`
}

async function renderFeedback() {
  const payload = await safeJson('/api/game-observatory/voice-records', { voices: [] })
  state.voices = payload.voices || []
  const active = state.voices.filter((item) => item.status === 'active' && item.voice?.review_status !== 'rejected')
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
  <div class="card-list">${active.map((record) => { const voice = record.voice; return `<article class="record-card"><header><h2>${esc(voice.theme)}</h2><span>${esc(voice.sentiment)}</span></header><p>${esc(voice.summary)}</p>${voice.quote ? `<p>“${esc(voice.quote)}”</p>` : ''}<div class="meta"><span>${esc(voice.version_context)}</span><span>${esc(voice.review_status)}</span>${(voice.tags || []).map((tag) => `<span>#${esc(tag)}</span>`).join('')}</div><details class="tech"><summary>来源绑定</summary><pre>${esc(JSON.stringify(record, null, 2))}</pre></details></article>` }).join('') || '<div class="empty-state"><strong>没有已审阅玩家反馈</strong><span>后续采集仍会保留原始来源。</span></div>'}</div>`
}

function interpretationItems(report) {
  const spec = report.design_spec || {}
  const values = spec.interpretations || report.interpretations || []
  return values.map((item) => typeof item === 'string' ? { statement: item } : item)
}

async function renderInterpretation() {
  const payload = await safeJson('/api/game-observatory/workspace/design-specs', { design_specs: [] })
  state.specs = payload.design_specs || []
  const entries = state.specs.flatMap((item) => interpretationItems(item.report).map((entry) => ({ report: item.report, entry })))
  workspace.innerHTML = `${head(
    'INTERPRETATION',
    '理解',
    '设计意图、体验判断、因果解释和商业化理解集中在这一层。每条陈述保留支持材料，并与事实正文保持独立。',
  )}
  <div class="card-list">${entries.map(({ report, entry }) => `<article class="record-card"><header><h2>${esc(report.system_title)}</h2><span>推测</span></header><p>${esc(entry.statement || entry.title || '')}</p><div class="meta"><span>${esc(report.game_title)}</span><span>${(entry.source_ids || []).length} 个来源</span><span>${(entry.artifact_ids || []).length} 个 artifact</span></div><details class="tech"><summary>支持材料</summary><pre>${esc(JSON.stringify(entry, null, 2))}</pre></details></article>`).join('') || '<div class="empty-state"><strong>当前没有理解条目</strong><span>事实案可以在没有理解条目的情况下完成。</span></div>'}</div>`
}

async function renderConsole() {
  const [targetPayload, leasePayload, runPayload] = await Promise.all([
    safeJson('/api/game-observatory/targets', { targets: [] }),
    safeJson('/api/game-observatory/leases', { leases: [] }),
    safeJson('/api/game-observatory/evidence-runs?limit=100', { runs: [] }),
  ])
  state.targets = targetPayload.targets || []
  state.leases = leasePayload.leases || []
  state.runs = runPayload.runs || []
  const activeRun = preferredRun(state.runs)
  const detail = activeRun ? await safeJson(`/api/game-observatory/evidence-runs/${encodeURIComponent(activeRun.id)}`, null) : null
  const lastStep = detail?.steps?.[detail.steps.length - 1]
  const selectedTarget = state.targets.find((target) => target.id === activeRun?.target_id) || state.targets[0]
  const activeLease = state.leases.find((lease) => lease.target_id === selectedTarget?.id && lease.status === 'active')
  workspace.innerHTML = `${head(
    'AI PLAYER CONSOLE',
    'AI 玩家控制台',
    '设备、独占租约、画面、证据运行和安全控制集中在本地控制面。公开资料库不会显示这些运行信息。',
    '本地控制面',
  )}
  <div class="console-grid">
    <aside>${state.targets.map((target) => `<article class="target-card"><header><strong>${esc(target.label || target.endpoint)}</strong>${status(target.status)}</header><p>${esc(target.kind)} · ${esc(target.endpoint)}</p><div class="meta"><span>${activeLease?.target_id === target.id ? `租约 ${humanStatus(activeLease.status)}` : '无活动租约'}</span></div></article>`).join('') || '<div class="empty-state"><strong>没有设备目标</strong></div>'}</aside>
    <section class="console-screen"><div class="evidence-title"><div><h2>${esc(selectedTarget?.label || '未选择设备')}</h2><p>${activeRun ? `${esc(runLabel(activeRun))} · ${humanStatus(activeRun.status)}` : '没有关联运行'}</p></div>${status(activeLease ? 'running' : 'stopped')}</div>
      <div class="frame-stage">${lastStep?.after_frame_id ? frameMarkup(lastStep.after_frame_id, lastStep, detail.run, false) : '<div class="empty-state"><strong>没有当前画面</strong><span>开始证据任务后会显示完整设备画面。</span></div>'}</div>
      <div class="command-dock"><button disabled>点击</button><button disabled>滑动</button><button disabled>多点手势</button><button disabled>返回</button></div>
      <p style="color:var(--muted);font-size:12px">${activeLease ? '当前设备已有活动租约；动作仍需绑定到证据运行。' : '设备租约已释放。操作控件保持锁定，取得租约并创建证据任务后才能执行。'}</p>
      <details class="tech"><summary>运行与安全状态</summary><pre>${esc(JSON.stringify({ target: selectedTarget, lease: activeLease || null, run: activeRun || null }, null, 2))}</pre></details>
    </section>
  </div>`
}

async function main() {
  const surface = surfaceName()
  markNavigation(surface)
  try {
    await loadCommon()
    const renderers = {
      overview: renderOverview,
      spec: renderSpec,
      evidence: renderEvidence,
      coverage: renderCoverage,
      sources: renderSources,
      feedback: renderFeedback,
      interpretation: renderInterpretation,
      console: renderConsole,
    }
    await (renderers[surface] || renderOverview)()
  } catch (error) {
    console.error(error)
    workspace.innerHTML = `<div class="error-panel"><strong>设施页面读取失败</strong><p>${esc(error.message)}</p></div>`
  }
}

main()