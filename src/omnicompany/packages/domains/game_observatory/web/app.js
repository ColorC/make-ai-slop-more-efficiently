const state = { reports: [], games: [], tags: [], searchResults: [], activeTag: '', query: '', health: null, currentGame: null, currentReport: null }

const $ = (selector) => document.querySelector(selector)
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[c]))

function publicUrl(source) {
  if (!source.public || !/^https?:\/\//.test(source.url || '')) return ''
  return source.url
}

const feedbackSourceLabels = {
  player_comment: '玩家具体评论', player_discussion: '玩家讨论', media_score: '媒体评分',
  media_article: '媒体文章', media_review: '媒体评论', objective_data: '客观数据', estimated_data: '预估数据',
}

function sourceContext(source) {
  const identity = [source.platform, source.author || source.account, source.published_at, source.locator].filter(Boolean)
  const rating = source.rating ? `${source.rating.value} / ${source.rating.scale_max}${source.rating.rating_count != null ? ` · ${source.rating.rating_count} 份评分` : ''}` : ''
  const estimate = source.estimation_method
    ? `${source.estimation_method.method} · 依据：${(source.estimation_method.basis || []).join('、')}${source.estimation_method.range_low != null ? ` · 区间 ${source.estimation_method.range_low}–${source.estimation_method.range_high} ${source.estimation_method.unit || ''}` : ''}`
    : ''
  const dataScope = source.data_scope && Object.keys(source.data_scope).length ? JSON.stringify(source.data_scope) : ''
  const engagement = source.engagement && Object.keys(source.engagement).length
    ? Object.entries(source.engagement).map(([key, value]) => `${key} ${value}`).join(' · ')
    : ''
  const details = [
    source.source_type ? feedbackSourceLabels[source.source_type] || source.source_type : '',
    ...identity,
    rating ? `评分 ${rating}` : '',
    engagement ? `互动 ${engagement}` : '',
    dataScope ? `数据范围 ${dataScope}` : '',
    estimate ? `估算方法 ${estimate}` : '',
    source.content_type ? `${source.content_type} · ${source.content_bytes ?? 0} bytes` : '',
    source.content_sha256 ? `快照 ${source.content_sha256.slice(0, 12)}` : '',
  ].filter(Boolean)
  const url = publicUrl(source)
  return `${details.length ? `<small class="source-context">${details.map(esc).join(' · ')}</small>` : ''}${url ? `<code class="source-url">${esc(url)}${source.resolved_url && source.resolved_url !== url ? ` → ${esc(source.resolved_url)}` : ''}</code>` : ''}`
}

async function loadHealth() {
  try {
    const response = await fetch('/api/game-observatory/health')
    if (!response.ok) throw new Error(`health ${response.status}`)
    state.health = await response.json()
  } catch (error) {
    console.error(error)
  }
}

async function loadCatalog() {
  const params = new URLSearchParams()
  params.set('view', 'plays-v1')
  if (state.activeTag) params.set('tag', state.activeTag)
  const response = await fetch(`/api/game-observatory/catalog?${params}`)
  if (!response.ok) throw new Error(`catalog ${response.status}`)
  const data = await response.json()
  state.reports = data.reports || []
  state.games = groupGames(state.reports)
  state.tags = data.tags || []
  const normalizedQuery = state.query.trim().toLocaleLowerCase()
  state.searchResults = normalizedQuery
    ? state.reports.filter((report) => JSON.stringify([
      report.game?.localized_title,
      report.play?.title,
      report.summary,
      report.reader?.player_goal,
      report.reader?.steps,
      report.reader?.concepts,
      report.reader?.rules,
      reportPlayTags(report),
    ]).toLocaleLowerCase().includes(normalizedQuery))
    : []
  renderCatalog()
}

function reportGameId(report) {
  return report.game_id || report.scope?.game_id || String(report.game_title || 'untitled-game').toLocaleLowerCase().replace(/[^a-z0-9]+/g, '-')
}

function reportGameSlug(report) {
  return report.game?.slug || reportGameId(report)
}

function reportPlayTags(report) {
  return report.play?.tags?.length ? report.play.tags : (report.tags || [])
}

function reportPlayTagMarkup(report, limit = null) {
  const detailByTag = new Map((report.play?.tag_details || report.play_tag_details || []).map((item) => [item.tag, item]))
  const tags = limit === null ? reportPlayTags(report) : reportPlayTags(report).slice(0, limit)
  return tags.map((tag) => {
    const detail = detailByTag.get(tag)
    const explanation = detail ? `${detail.facet}：${detail.description}` : ''
    return `<span${explanation ? ` title="${esc(explanation)}"` : ''}>${esc(tag)}</span>`
  }).join('')
}

function groupGames(reports) {
  const grouped = new Map()
  reports.forEach((report) => {
    const id = reportGameId(report)
    const game = grouped.get(id) || { id, slug: reportGameSlug(report), title: report.game_title, localizedTitle: report.game?.localized_title || '', summary: report.game?.summary || '', reports: [], tags: new Set(report.game?.tags || []) }
    game.reports.push(report)
    ;(report.game?.tags || []).forEach((tag) => game.tags.add(tag))
    grouped.set(id, game)
  })
  return [...grouped.values()].map((game) => ({ ...game, tags: [...game.tags] }))
}

function renderStats() {
  const reports = state.reports
  const plays = reports.filter((item) => item.content_kind !== 'journey')
  const journeys = reports.filter((item) => item.content_kind === 'journey')
  $('[data-stats]').innerHTML = [
    ['游戏', state.games.length], ['游戏系统', plays.length], ['入门流程', journeys.length],
  ].map(([label, value]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join('')
}

function gameCard(game) {
  const lead = game.reports[0]
  const plays = game.reports.filter((item) => item.content_kind !== 'journey')
  const journeys = game.reports.filter((item) => item.content_kind === 'journey')
  const cover = lead?.artifacts?.find((item) => item.id === lead.cover_artifact_id)
  const coverHtml = cover ? `<img class="card-cover" loading="lazy" decoding="async" src="${esc(cover.url || `/api/game-observatory/artifacts/${encodeURIComponent(cover.id)}`)}" alt="${esc(game.localizedTitle || game.title)} 游戏画面">` : ''
  return `<article class="report-card game-catalog-card ${cover ? 'has-cover' : ''}" role="button" tabindex="0" aria-label="打开游戏 ${esc(game.localizedTitle || game.title)}" data-game="${esc(game.slug)}">
    ${coverHtml}<span class="card-game">游戏</span><h3>${esc(game.localizedTitle || game.title)}</h3><p>${esc(game.summary || `${plays.length} 个游戏系统${journeys.length ? ` · ${journeys.length} 条入门流程` : ''}`)}</p><div class="card-bottom"><div class="mini-tags">${game.tags.slice(0, 6).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div><span class="card-arrow">→</span></div>
  </article>`
}

function renderTags() {
  $('[data-tags]').innerHTML = state.tags.slice(0, 24).map((item) => (
    `<button class="tag ${state.activeTag === item.tag ? 'is-active' : ''}" data-tag="${esc(item.tag)}">${esc(item.tag)} · ${item.count}</button>`
  )).join('')
}

function reportCard(report) {
  const cover = (report.artifacts || []).find((item) => item.id === report.cover_artifact_id)
  const title = report.play?.title || report.design_spec?.title || report.system_title
  const coverHtml = cover ? `<img class="card-cover" loading="lazy" decoding="async" src="${esc(cover.url || `/api/game-observatory/artifacts/${encodeURIComponent(cover.id)}`)}" alt="${esc(title)} 真实游戏画面">` : ''
  const destination = report.public_path ? `data-public-path="${esc(report.public_path)}"` : `data-report="${esc(report.slug)}"`
  const isJourney = report.content_kind === 'journey'
  const cardScope = report.public_path
    ? `${report.game?.localized_title || report.game_title} · ${isJourney ? '入门流程' : '游戏系统'}`
    : `${report.game_title} · ${report.scope.version}`
  return `<article class="report-card ${cover ? 'has-cover' : ''}" role="button" tabindex="0" aria-label="打开 ${esc(title)} 系统文章" ${destination}>
    ${coverHtml}
    <span class="card-game">${esc(cardScope)}</span>
    <h3>${esc(title)}</h3>
    <p>${esc(report.summary)}</p>
    <div class="card-bottom"><div class="mini-tags">${reportPlayTagMarkup(report, 5)}</div><span class="card-arrow">↗</span></div>
  </article>`
}

function renderCatalog() {
  renderStats()
  renderTags()
  const grid = $('[data-report-grid]')
  grid.classList.toggle('is-search-results', Boolean(state.query))
  if (state.query) {
    $('[data-result-count]').textContent = `${state.searchResults.length} 个系统结果`
    grid.innerHTML = state.searchResults.length
      ? state.searchResults.map(reportCard).join('')
      : '<div class="empty">没有找到相关系统，可以换一个玩家目标、概念或规则。</div>'
    return
  }
  const playCount = state.reports.filter((item) => item.content_kind !== 'journey').length
  const journeyCount = state.reports.filter((item) => item.content_kind === 'journey').length
  $('[data-result-count]').textContent = `${state.games.length} 个游戏 · ${playCount} 个游戏系统${journeyCount ? ` · ${journeyCount} 条入门流程` : ''}`
  grid.innerHTML = state.games.length
    ? state.games.map(gameCard).join('')
    : '<div class="empty">当前还没有可浏览的游戏系统。</div>'
}

function gameView(game) {
  const journeys = game.reports.filter((item) => item.content_kind === 'journey')
  const plays = game.reports.filter((item) => item.content_kind !== 'journey')
  return `<div class="report-topbar"><button class="back" data-home>← 返回游戏</button><div class="scope-line">${plays.length} 个游戏系统${journeys.length ? ` · ${journeys.length} 条入门流程` : ''}</div></div>
  <header class="game-public-hero"><p class="eyebrow">游戏系统目录</p><h1>${esc(game.localizedTitle || game.title)}</h1><p>${esc(game.summary || '从玩家目标出发，理解系统、规则、关键概念和相互关系。')}</p><div class="mini-tags">${game.tags.map((tag) => `<span>${esc(tag)}</span>`).join('')}</div></header>
  ${journeys.length ? `<section class="catalog"><div class="section-head"><div><p class="eyebrow">入门路线</p><h2>先从完整流程认识游戏</h2></div><span>${journeys.length} 条</span></div><div class="report-grid">${journeys.map(reportCard).join('')}</div></section>` : ''}
  <section class="catalog"><div class="section-head"><div><p class="eyebrow">游戏系统</p><h2>按系统深入理解玩法</h2></div><span>${plays.length} 个</span></div><div class="report-grid">${plays.length ? plays.map(reportCard).join('') : '<div class="empty">当前还没有可阅读的系统文章。</div>'}</div></section>`
}

function openGame(slug, push = true) {
  const game = state.games.find((item) => item.slug === slug || item.id === slug)
  if (!game) throw new Error(`game not found: ${slug}`)
  state.currentGame = game
  state.currentReport = null
  $('[data-catalog-shell]').hidden = true
  $('[data-report-view]').hidden = true
  $('.hero').hidden = true
  const view = $('[data-game-view]')
  view.hidden = false
  view.innerHTML = gameView(game)
  if (push) history.pushState({ game: game.slug }, '', `/game-observatory/game/${encodeURIComponent(game.slug)}`)
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function sourceName(report, id) {
  return report.sources.find((item) => item.id === id)?.title || id
}

function collaborationPanel(report) {
  const objects = [
    [report.id, '整份设计案'],
    ...report.surfaces.flatMap((surface) => [
      [surface.id, `页面 · ${surface.title}`],
      ...surface.elements.map((item) => [item.id, `UI · ${item.label || item.role}`]),
    ]),
    ...report.flow.map((item) => [item.id, `流程 · ${item.title}`]),
    ...report.mechanisms.map((item) => [item.id, `机制 · ${item.title}`]),
    ...report.resources.map((item) => [item.id, `资源 · ${item.resource}`]),
    ...(report.compiled?.object_index || []).map((item) => [item.object_id, `${item.object_type} · ${item.label}`]),
  ]
  const options = objects.map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('')
  const sample = JSON.stringify([{
    op: 'replace', target_kind: 'flow', target_id: report.flow[0]?.id || '', field: 'description', value: report.flow[0]?.description || '',
  }], null, 2)
  return `<details class="collaboration" data-collaboration>
    <summary>作者 / 审核协作</summary>
    <div class="collab-auth"><label>访问令牌<input type="password" data-collab-token value="${esc(localStorage.getItem('gameObservatoryToken') || '')}" autocomplete="off"></label><button data-collab-load>连接协作面</button><span data-collab-status>尚未连接</span></div>
    <div class="collab-grid">
      <form data-annotation-form><h3>添加对象批注</h3><label>对象<select name="object_id">${options}</select></label><label>作者<input name="author" required></label><label>批注<textarea name="body" required></textarea></label><button type="submit">保存批注</button></form>
      <form data-patch-form><h3>提交语义 Patch</h3><label>作者<input name="author" required></label><label>说明<input name="note" required></label><label>Operations JSON<textarea name="operations" class="code-input" required>${esc(sample)}</textarea></label><button type="submit">提交审核</button></form>
    </div>
    <div class="collab-results"><section><h3>批注</h3><div data-annotation-list>连接后载入</div></section><section><h3>Patches</h3><div data-patch-list>连接后载入</div></section></div>
  </details>`
}

function collabHeaders() {
  const token = $('[data-collab-token]')?.value.trim() || localStorage.getItem('gameObservatoryToken') || ''
  if (token) localStorage.setItem('gameObservatoryToken', token)
  return { 'Content-Type': 'application/json', 'X-Game-Observatory-Token': token }
}

async function collabFetch(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { ...collabHeaders(), ...(options.headers || {}) } })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `HTTP ${response.status}`)
  }
  return response.json()
}

async function loadCollaboration() {
  const report = state.currentReport
  if (!report) return
  const [annotations, patches] = await Promise.all([
    collabFetch(`/api/game-observatory/reports/${encodeURIComponent(report.id)}/annotations`),
    collabFetch(`/api/game-observatory/reports/${encodeURIComponent(report.id)}/patches`),
  ])
  $('[data-collab-status]').textContent = '协作面已连接'
  $('[data-annotation-list]').innerHTML = annotations.annotations.length ? annotations.annotations.map((item) => (
    `<article class="collab-item"><strong>${esc(item.kind)} · ${esc(item.object_id)}</strong><p>${esc(item.body)}</p><small>${esc(item.author)} · ${esc(item.status)}</small></article>`
  )).join('') : '<p>暂无批注。</p>'
  $('[data-patch-list]').innerHTML = patches.patches.length ? patches.patches.map((item) => (
    `<article class="collab-item"><strong>${esc(item.status)} · revision ${item.base_revision}</strong><p>${esc(item.note)}</p><small>${esc(item.author)}</small>${item.status === 'proposed' ? `<div><button data-patch-apply="${esc(item.id)}">应用</button><button data-patch-reject="${esc(item.id)}">拒绝</button></div>` : ''}</article>`
  )).join('') : '<p>暂无 Patch。</p>'
}

function diffValue(value) {
  if (value === null || value === undefined) return '∅'
  const raw = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return raw.length > 480 ? `${raw.slice(0, 480)}…` : raw
}

async function showRevisionDiff(button) {
  const params = new URLSearchParams({
    from_revision: button.dataset.from,
    to_revision: button.dataset.to,
  })
  const response = await fetch(`/api/game-observatory/reports/${encodeURIComponent(button.dataset.diff)}/diff?${params}`)
  if (!response.ok) throw new Error(`revision diff ${response.status}`)
  const payload = await response.json()
  const panel = $('[data-diff-panel]')
  panel.hidden = false
  panel.innerHTML = payload.changes.length
    ? `<h3>Revision ${payload.from_revision} → ${payload.to_revision}</h3>${payload.changes.map((item) => (
      `<article class="diff-row"><strong>${esc(item.op)} · ${esc(item.path)}</strong><div><pre>${esc(diffValue(item.before))}</pre><span>→</span><pre>${esc(diffValue(item.after))}</pre></div></article>`
    )).join('')}`
    : `<p>Revision ${payload.from_revision} 与 ${payload.to_revision} 的语义内容一致。</p>`
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

function legacyReportView(report) {
  const revisions = report.revisions || []
  const sourceItems = report.sources.map((source) => {
    if (source.status === 'retracted') {
      return `<li><span>来源已撤回</span><small>${esc(source.kind)} · ${esc(source.version_context || 'version unknown')} · 历史 tombstone 保留</small></li>`
    }
    const url = publicUrl(source)
    const title = url ? `<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(source.title)}</a>` : esc(source.title)
    return `<li>${title}<small>${esc(source.kind)} · ${esc(source.version_context || 'version unknown')}${source.public ? '' : ' · 内部定位未公开'}</small>${sourceContext(source)}</li>`
  }).join('')
  const activeSourceCount = report.sources.filter((source) => source.status === 'active').length
  const flow = report.flow.map((node, index) => `<div class="flow-node">
    <span class="flow-index">${String(index + 1).padStart(2, '0')}</span><div><h3>${esc(node.title)}</h3><p>${esc(node.description)}</p>${node.action ? `<span class="flow-action">ACTION · ${esc(node.action)}</span>` : ''}</div>
  </div>`).join('')
  const surfaces = report.surfaces.map((surface) => {
    const artifact = surface.artifact_ids.map((id) => report.artifacts.find((item) => item.id === id)).find(Boolean)
    const visual = artifact
      ? `<div class="surface-visual"><img loading="lazy" decoding="async" src="/api/game-observatory/artifacts/${encodeURIComponent(artifact.id)}" alt="${esc(surface.title)} 运行证据">${surface.elements.filter((item) => item.bounds).map((item) => `<span class="surface-box" style="--x:${item.bounds.x * 100}%;--y:${item.bounds.y * 100}%;--w:${item.bounds.width * 100}%;--h:${item.bounds.height * 100}%" title="${esc(item.label || item.role)}"></span>`).join('')}</div>`
      : '<div class="surface-visual is-semantic"><span>SEMANTIC LAYOUT</span><strong>来源约束的页面结构</strong></div>'
    const elements = surface.elements.map((item) => `<li><span>${esc(item.role)}</span><strong>${esc(item.label || item.text || item.id)}</strong>${item.actions.length ? `<small>${item.actions.map((action) => esc(action)).join(' · ')}</small>` : ''}</li>`).join('')
    return `<article class="surface-card" id="${esc(surface.id)}">${visual}<div class="surface-copy"><span class="surface-kind">${esc(surface.kind)}</span><h3>${esc(surface.title)}</h3><p>${esc(surface.description || '')}</p><ul>${elements}</ul></div></article>`
  }).join('')
  const surfaceSection = report.surfaces.length ? `<section class="report-section"><p class="eyebrow">PAGE &amp; UI</p><h2>页面与 UI</h2><p class="section-intro">画面证据与语义元素使用同一 Surface ID；没有公开截图的页面明确标为来源约束布局。</p><div class="surface-grid">${surfaces}</div></section>` : ''
  const mechanisms = report.mechanisms.map((item) => `<article class="mechanism"><h3>${esc(item.title)}</h3><p>${esc(item.description)}</p>${item.code ? `<code>${esc(item.code)}</code>` : ''}</article>`).join('')
  const resources = report.resources.map((item) => `<article class="resource"><strong>${esc(item.resource)}<b>${esc(item.role)}</b></strong><p>${esc(item.description)}</p></article>`).join('')
  const resourceSection = report.resources.length ? `<section class="report-section"><p class="eyebrow">RESOURCE SYSTEM</p><h2>资源关系</h2>${resources}</section>` : ''
  const voices = report.player_voices.map((voice) => `<blockquote class="voice"><p>${esc(voice.summary)}</p>${voice.quote ? `<q>${esc(voice.quote)}</q>` : ''}<footer>${esc(voice.theme)} · ${esc(sourceName(report, voice.source_id))} · ${esc(voice.version_context)}</footer></blockquote>`).join('') || '<p>当前没有进入公开投影的玩家声音。</p>'
  const task = report.benchmark_task ? `<div class="benchmark-note"><strong>${esc(report.benchmark_task.title)}</strong><span>${esc(report.benchmark_task.goal)}</span></div>` : ''
  const cover = report.artifacts.find((item) => item.id === report.cover_artifact_id)
  const coverFigure = cover ? `<figure class="report-cover"><img src="/api/game-observatory/artifacts/${encodeURIComponent(cover.id)}" alt="${esc(report.system_title)} evidence"><figcaption>真实运行证据 · ${esc(report.scope.device)}</figcaption></figure>` : ''
  const revisionList = revisions.length ? `<section class="report-section"><p class="eyebrow">VERSION HISTORY</p><h2>修订历史</h2><ul class="source-list">${revisions.map((item, index) => {
    const older = revisions[index + 1]
    const diffButton = older ? `<button class="revision-diff" data-diff="${esc(report.slug)}" data-from="${older.revision}" data-to="${item.revision}">与上一版比较</button>` : ''
    return `<li>Revision ${item.revision}<small>${esc(item.created_at.slice(0, 19))} · ${esc(item.sha256.slice(0, 12))}</small>${diffButton}</li>`
  }).join('')}</ul><div class="diff-panel" data-diff-panel hidden></div></section>` : ''
  return `<div class="report-topbar"><button class="back" data-back>← 返回所属游戏的玩法</button><a class="semantic-link" href="/game-observatory/reports/${encodeURIComponent(report.slug)}">语义 HTML ↗</a><div class="scope-line">${esc(report.scope.platform)} / ${esc(report.scope.version)}<br>${esc(report.scope.region)} · ${esc(report.scope.captured_at.slice(0, 10))}</div></div>
  <section class="report-hero"><div><p class="eyebrow">${esc(report.game_title)} · ${esc(report.system_id)}</p><h1>${esc(report.system_title)}</h1><p class="report-summary">${esc(report.summary)}</p></div>
    <div class="report-metrics"><div><strong>${report.flow.length}</strong><span>flow nodes</span></div><div><strong>${report.mechanisms.length}</strong><span>mechanisms</span></div><div><strong>${activeSourceCount}</strong><span>active sources</span></div><div><strong>${report.player_voices.length}</strong><span>voices</span></div></div>
  </section>
  <div class="report-body"><div>
    ${coverFigure}
    ${surfaceSection}
    <section class="report-section"><p class="eyebrow">PLAYER JOURNEY</p><h2>玩家旅程</h2><div class="flow">${flow}</div></section>
    <section class="report-section"><p class="eyebrow">FORMAL DESCRIPTION</p><h2>机制表达</h2>${mechanisms}</section>
    ${resourceSection}
  </div><aside>
    <section class="report-section"><p class="eyebrow">PLAYER VOICE</p><h2>玩家声音</h2>${voices}</section>
    <section class="report-section"><p class="eyebrow">OBSERVATIONS</p><h2>观察与解释</h2><ul class="analysis-list">${report.observations.map((item) => `<li>${esc(item.statement)}</li>`).join('')}</ul></section>
    <section class="report-section"><p class="eyebrow">INTERPRETATION</p><ul class="analysis-list">${report.interpretations.map((item) => `<li>${esc(item.statement)}</li>`).join('')}</ul></section>
    ${task}
    ${revisionList}
    <section class="report-section"><p class="eyebrow">PROVENANCE</p><h2>来源</h2><ul class="source-list">${sourceItems}</ul></section>
    ${collaborationPanel(report)}
  </aside></div>`
}

function artifactFor(report, id) {
  return report.artifacts.find((item) => item.id === id)
}

function artifactFigure(report, artifact, label) {
  if (!artifact) return ''
  return `<figure class="design-visual" data-artifact-id="${esc(artifact.id)}"><img loading="lazy" decoding="async" src="/api/game-observatory/artifacts/${encodeURIComponent(artifact.id)}" alt="${esc(label)}"><figcaption><b>${esc(label)}</b><span>${esc(artifact.kind)} · ${esc(artifact.id)}</span></figcaption></figure>`
}

function evidenceRefs(report, sourceIds = [], artifactIds = []) {
  const sourceLinks = sourceIds.map((id) => {
    const source = report.sources.find((item) => item.id === id)
    return `<a href="#source-${esc(id)}">来源 · ${esc(source?.title || id)}</a>`
  })
  const artifactLinks = artifactIds.map((id) => `<a href="#artifact-${esc(id)}">证据 · ${esc(id)}</a>`)
  return sourceLinks.length + artifactLinks.length ? `<div class="evidence-refs">${[...sourceLinks, ...artifactLinks].join('')}</div>` : ''
}

function statementCards(report, statements) {
  return statements.map((item) => `<article class="spec-card" id="${esc(item.id)}"><span>${esc(item.kind)}</span><h3>${esc(item.title)}</h3><p>${esc(item.statement)}</p>${evidenceRefs(report, item.source_ids, item.artifact_ids)}</article>`).join('')
}

function coverageNote(spec, section) {
  const value = spec.section_coverage.find((item) => item.section === section)
  return value ? `<div class="coverage-note is-${esc(value.status)}"><b>${esc(value.status)}</b><span>${esc(value.rationale)}</span></div>` : ''
}

function designReportView(report) {
  const spec = report.design_spec
  const sourceItems = report.sources.map((source) => {
    const url = publicUrl(source)
    const title = url ? `<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(source.title)}</a>` : `<b>${esc(source.title)}</b>`
    return `<li id="source-${esc(source.id)}">${title}<small>${esc(source.kind)} · ${esc(source.version_context || 'version unknown')}${source.public ? '' : ' · 内部定位未公开'}</small>${sourceContext(source)}<span>${esc(source.note || '')}</span></li>`
  }).join('')
  const artifactItems = report.artifacts.map((artifact) => `<li id="artifact-${esc(artifact.id)}"><b>${esc(artifact.id)}</b><small>${esc(artifact.kind)} · ${esc(artifact.media_type || 'application/octet-stream')} · run ${esc(artifact.run_id || 'unassigned')}</small><span>${esc(artifact.sha256 || '')}</span></li>`).join('')
  const chapters = [
    ['spec-overview', '概述'], ['spec-core-loop', '核心循环'], ['spec-surfaces', '页面设计'],
    ['spec-interaction', '交互状态'], ['spec-rules', '规则资源'], ['spec-progression', '成长数值'],
    ['spec-feedback', '反馈教学'], ['spec-failure', '失败依赖'], ['spec-voices', '玩家反馈'], ['spec-sources', '来源'],
  ]
  const chapterNav = chapters.map(([id, label]) => `<a href="#${id}">${label}</a>`).join('')
  const objectCounts = Object.entries(report.compiled?.object_counts || {}).map(([type, count]) => `<li><span>${esc(type)}</span><b>${count}</b></li>`).join('')
  const cover = artifactFor(report, report.cover_artifact_id)

  const coverage = spec.section_coverage.map((item) => `<li class="is-${esc(item.status)}"><span>${esc(item.section)}</span><b>${esc(item.status)}</b><small>${esc(item.rationale)}</small></li>`).join('')
  const coreSteps = spec.core_loop.steps.map((step, index) => `<li id="${esc(step.id)}"><span>${String(index + 1).padStart(2, '0')}</span><div><h3>${esc(step.title)}</h3><dl><dt>玩家动作</dt><dd>${esc(step.player_action)}</dd><dt>系统响应</dt><dd>${esc(step.system_response)}</dd><dt>状态</dt><dd>${esc(step.state_before)} → ${esc(step.state_after)}</dd></dl>${evidenceRefs(report, step.source_ids, step.artifact_ids)}</div></li>`).join('')
  const observedFlow = report.flow.map((node, index) => `<li id="${esc(node.id)}"><b>${String(index + 1).padStart(2, '0')} · ${esc(node.title)}</b><span>${esc(node.description)}</span>${evidenceRefs(report, node.source_ids, node.artifact_ids)}</li>`).join('')

  const surfaceById = new Map(report.surfaces.map((item) => [item.id, item]))
  const surfaces = spec.information_architecture.surface_ids.map((surfaceId) => {
    const surface = surfaceById.get(surfaceId)
    const layout = spec.layout_specs.find((item) => item.surface_id === surfaceId)
    const screenshot = surface.artifact_ids.map((id) => artifactFor(report, id)).find(Boolean)
    const derivedSpec = spec.design_artifacts.find((item) => item.surface_ids.includes(surfaceId) && ['annotated_plate', 'layout_spec', 'wireframe'].includes(item.kind))
    const derived = derivedSpec ? artifactFor(report, derivedSpec.artifact_id) : null
    const elementById = new Map(surface.elements.map((item) => [item.id, item]))
    const overlays = layout.elements.map((item) => {
      const target = elementById.get(item.ui_element_id)
      return `<button class="layout-box" style="--x:${item.bounds.x * 100}%;--y:${item.bounds.y * 100}%;--w:${item.bounds.width * 100}%;--h:${item.bounds.height * 100}%" title="${esc(target?.label || target?.role || item.ui_element_id)}" data-object-link="${esc(item.id)}"></button>`
    }).join('')
    const layoutRows = layout.elements.map((item) => {
      const target = elementById.get(item.ui_element_id)
      return `<tr id="${esc(item.id)}"><td>${esc(target?.label || target?.role || item.ui_element_id)}</td><td><code>${item.bounds.x.toFixed(3)}, ${item.bounds.y.toFixed(3)}, ${item.bounds.width.toFixed(3)}, ${item.bounds.height.toFixed(3)}</code></td><td>${esc(item.anchors.join(' / ') || '—')}</td></tr>`
    }).join('')
    return `<article class="surface-explorer" id="${esc(surface.id)}" data-surface-card data-mode="evidence"><header><div><span>${esc(surface.kind)}</span><h3>${esc(surface.title)}</h3><p>${esc(surface.description || '')}</p></div><div class="surface-modes" role="group" aria-label="${esc(surface.title)} 视图"><button class="is-active" data-surface-mode="evidence">真实画面</button><button data-surface-mode="reconstruction">反推设计稿</button><button data-surface-mode="layout">布局数据</button></div></header><div class="surface-panel" data-surface-panel="evidence">${artifactFigure(report, screenshot, `${surface.title} · 真实游戏画面`)}</div><div class="surface-panel" data-surface-panel="reconstruction">${artifactFigure(report, derived, `${surface.title} · 反推设计稿`)}${derivedSpec ? `<div class="derivation"><b>${esc(derivedSpec.generation_method)}</b><span>由 ${derivedSpec.derived_from_artifact_ids.map((id) => esc(id)).join('、')} 生成 · ${esc(derivedSpec.review_status)}</span></div>` : ''}</div><div class="surface-panel" data-surface-panel="layout"><div class="layout-stage">${screenshot ? `<img loading="lazy" src="/api/game-observatory/artifacts/${encodeURIComponent(screenshot.id)}" alt="${esc(surface.title)} 布局坐标底图">` : ''}${overlays}</div><table><thead><tr><th>元素</th><th>x, y, w, h</th><th>锚点</th></tr></thead><tbody>${layoutRows}</tbody></table>${evidenceRefs(report, layout.source_ids, layout.artifact_ids)}</div></article>`
  }).join('')
  const navigation = spec.information_architecture.edges.map((edge) => `<tr id="${esc(edge.id)}"><td><a href="#${esc(edge.from_surface_id)}">${esc(surfaceById.get(edge.from_surface_id)?.title)}</a></td><td>${esc(edge.trigger)}</td><td><a href="#${esc(edge.to_surface_id)}">${esc(surfaceById.get(edge.to_surface_id)?.title)}</a></td><td>${esc(edge.condition || '始终')}</td></tr>`).join('')

  const interactions = spec.interaction_specs.map((item) => `<article class="interaction-spec" id="${esc(item.id)}"><header><span>InteractionSpec</span><h3>${esc(item.title)}</h3><p><b>触发：</b>${esc(item.trigger)}</p></header><ol>${item.steps.map((step) => `<li id="${esc(step.id)}"><b>${step.order} · ${esc(step.actor)}</b><span>${esc(step.action)}</span><small>${esc(step.response || '')}</small><div>${step.surface_id ? `<a href="#${esc(step.surface_id)}">页面</a>` : ''}${step.ui_element_id ? `<a href="#${esc(step.ui_element_id)}">控件</a>` : ''}${step.flow_node_id ? `<a href="#${esc(step.flow_node_id)}">轨迹</a>` : ''}</div>${evidenceRefs(report, step.source_ids, step.artifact_ids)}</li>`).join('')}</ol><footer><b>完成后</b><span>${esc(item.postconditions.join('；'))}</span></footer>${evidenceRefs(report, item.source_ids, item.artifact_ids)}</article>`).join('')
  const stateMatrices = spec.state_matrices.map((matrix) => `<article class="matrix" id="${esc(matrix.id)}"><h3>${esc(matrix.title)}</h3><p>对象 · <a href="#${esc(matrix.subject_id)}">${esc(matrix.subject_id)}</a></p><table><thead><tr><th>状态</th><th>条件</th><th>可见 / 可用</th><th>反馈</th><th>下一状态</th></tr></thead><tbody>${matrix.cases.map((item) => `<tr id="${esc(item.id)}"><td>${esc(item.state)}</td><td>${esc(item.condition)}</td><td>${esc(item.visible)} / ${esc(item.enabled)}</td><td>${esc(item.feedback.join('；'))}</td><td>${esc(item.next_state || '—')}</td></tr>`).join('')}</tbody></table></article>`).join('')

  const mechanisms = report.mechanisms.map((item) => `<article class="mechanism" id="${esc(item.id)}"><h3>${esc(item.title)}</h3><p>${esc(item.description)}</p>${item.code ? `<pre><code>${esc(item.code)}</code></pre>` : ''}${evidenceRefs(report, item.source_ids, item.artifact_ids)}</article>`).join('')
  const resources = report.resources.map((item) => `<tr id="${esc(item.id)}"><td>${esc(item.resource)}</td><td>${esc(item.role)}</td><td>${esc(item.description)}</td></tr>`).join('')
  const progressions = spec.progression_specs.map((item) => `<article class="spec-card" id="${esc(item.id)}"><span>ProgressionSpec</span><h3>${esc(item.title)}</h3>${item.axes.map((axis) => `<dl id="${esc(axis.id)}"><dt>${esc(axis.name)}</dt><dd>${esc(axis.stages.join(' → '))} · ${esc(axis.unit)}</dd><dt>门槛</dt><dd>${esc(axis.gates.join('；') || '无')}</dd></dl>`).join('')}${evidenceRefs(report, item.source_ids, item.artifact_ids)}</article>`).join('')
  const balances = spec.balance_specs.map((item) => `<article class="balance-spec" id="${esc(item.id)}"><span>BalanceSpec</span><h3>${esc(item.title)}</h3><p>${esc(item.target_experience)}</p><table><thead><tr><th>参数</th><th>值 / 范围</th><th>调节作用</th></tr></thead><tbody>${item.parameters.map((parameter) => `<tr id="${esc(parameter.id)}"><td>${esc(parameter.name)}</td><td>${esc(parameter.value_or_range)} ${esc(parameter.unit || '')}</td><td>${esc(parameter.tuning_role)}</td></tr>`).join('')}</tbody></table></article>`).join('')
  const feedback = spec.feedback_specs.map((item) => `<article class="spec-card" id="${esc(item.id)}"><span>${esc(item.channels.join(' / '))}</span><h3>${esc(item.title)}</h3><p><b>${esc(item.trigger)}</b> · ${esc(item.timing)}</p><dl><dt>成功</dt><dd>${esc(item.success_behavior)}</dd><dt>失败</dt><dd>${esc(item.failure_behavior)}</dd></dl>${evidenceRefs(report, item.source_ids, item.artifact_ids)}</article>`).join('')
  const tutorials = spec.tutorial_specs.map((item) => `<article class="tutorial-spec" id="${esc(item.id)}"><h3>${esc(item.title)}</h3><ol>${item.steps.map((step) => `<li id="${esc(step.id)}"><b>${esc(step.trigger)}</b><span>${esc(step.instruction)}</span><small>完成：${esc(step.completion_condition)} · 恢复：${esc(step.recovery)}</small></li>`).join('')}</ol></article>`).join('')
  const failures = spec.failure_recovery_specs.map((item) => `<article class="spec-card" id="${esc(item.id)}"><span>FailureRecoverySpec</span><h3>${esc(item.title)}</h3><dl><dt>失败条件</dt><dd>${esc(item.failure_condition)}</dd><dt>可见反馈</dt><dd>${esc(item.visible_behavior)}</dd><dt>保留状态</dt><dd>${esc(item.retained_state)}</dd><dt>恢复动作</dt><dd>${esc(item.recovery_action)}</dd></dl>${evidenceRefs(report, item.source_ids, item.artifact_ids)}</article>`).join('')
  const dependencies = spec.dependency_specs.map((item) => `<article class="spec-card" id="${esc(item.id)}"><span>${esc(item.direction)} → ${esc(item.target_system_id)}</span><h3>${esc(item.title)}</h3><p>${esc(item.dependency)}</p>${evidenceRefs(report, item.source_ids, item.artifact_ids)}</article>`).join('')
  const voices = report.player_voices.map((voice) => `<blockquote class="voice" id="${esc(voice.id)}"><p>${esc(voice.summary)}</p>${voice.quote ? `<q>${esc(voice.quote)}</q>` : ''}<div class="voice-targets">${voice.target_object_ids.map((id) => `<a href="#${esc(id)}">关联 · ${esc(id)}</a>`).join('')}</div><footer>${esc(voice.theme)} · <a href="#source-${esc(voice.source_id)}">${esc(sourceName(report, voice.source_id))}</a> · ${esc(voice.version_context)}</footer></blockquote>`).join('')
  const communityFeedback = (report.community_feedback || []).map((item) => {
    const source = item.source
    const title = publicUrl(source) ? `<a href="${esc(source.url)}" target="_blank" rel="noreferrer">${esc(item.title)}</a>` : esc(item.title)
    return `<article class="community-feedback" id="${esc(item.id)}"><span>${esc(feedbackSourceLabels[item.source_type] || item.source_type)}</span><h3>${title}</h3><p>${esc(item.summary)}</p><div class="voice-targets">${(item.target_object_ids || []).map((id) => `<a href="#${esc(id)}">关联 · ${esc(id)}</a>`).join('')}</div>${sourceContext(source)}</article>`
  }).join('')
  const revisionList = (report.revisions || []).map((item, index, list) => { const older = list[index + 1]; return `<li>Revision ${item.revision}<small>${esc(item.created_at.slice(0, 19))} · ${esc(item.sha256.slice(0, 12))}</small>${older ? `<button class="revision-diff" data-diff="${esc(report.slug)}" data-from="${older.revision}" data-to="${item.revision}">与上一版比较</button>` : ''}</li>` }).join('')
  const screenTags = (report.screen_tags || []).map((binding) => `<article><strong>${esc(surfaceById.get(binding.surface_id)?.title || binding.surface_id)}</strong><div class="mini-tags">${binding.tags.map((tag) => `<span>${esc(tag)}</span>`).join('')}</div></article>`).join('')
  const playRecords = (report.play_records || []).map((record) => {
    const sourceLinks = (record.source_ids || []).map((id) => `<a href="#source-${esc(id)}">${esc(sourceName(report, id))}</a>`).join(' · ')
    const artifactLinks = (record.artifact_ids || []).map((id) => `<a href="#artifact-${esc(id)}">录屏或画面证据</a>`).join(' · ')
    return `<article><span>${record.source_type === 'ai_player_live_run' ? 'AI 玩家实机运行' : '人类录屏'}</span><h3>${esc(record.title)}</h3><p>${esc(record.platform)} · ${esc(record.captured_at)}${record.operator ? ` · ${esc(record.operator)}` : ''}</p>${record.note ? `<p>${esc(record.note)}</p>` : ''}<div>${sourceLinks}${sourceLinks && artifactLinks ? ' · ' : ''}${artifactLinks}</div></article>`
  }).join('')
  const demos = (report.demo_reproductions || []).map((demo) => `<article><span>DEMO</span><h3>${esc(demo.title)}</h3><p>${esc(demo.description)}</p><div class="mini-tags">${(demo.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div><a href="${esc(demo.url)}">打开 Demo</a></article>`).join('')
  const playSupplement = `<section class="play-supplement" id="play-screen-tags"><p class="eyebrow">SCREEN TAGS</p><h2>界面标签</h2><div class="public-screen-tags">${screenTags || '<p>当前玩法没有已发布的界面标签。</p>'}</div></section>
  <section class="play-supplement" id="play-records"><p class="eyebrow">PLAY RECORDS</p><h2>玩法游玩记录</h2><div class="public-play-records">${playRecords || '<p>当前玩法没有已发布的游玩记录。</p>'}</div></section>
  <section class="play-supplement" id="play-tags"><p class="eyebrow">PLAY TAGS</p><h2>玩法标签</h2><p>用于跨游戏比较的稳定概念；悬停可查看类别与定义。</p><div class="mini-tags is-large">${reportPlayTagMarkup(report)}</div></section>
  <section class="play-supplement" id="play-demos"><p class="eyebrow">DEMO REPRODUCTION</p><h2>玩法 Demo 复现</h2><div class="public-demos">${demos || '<p>当前玩法没有已发布的 Demo 复现。</p>'}</div></section>`

  return `<div class="report-topbar"><button class="back" data-back>← 返回所属游戏的玩法</button><a class="semantic-link" href="/game-observatory/reports/${encodeURIComponent(report.slug)}">完整语义版 ↗</a><div class="scope-line">${esc(report.scope.platform)} / ${esc(report.scope.version)}<br>${esc(report.scope.region)} · ${esc(report.scope.captured_at.slice(0, 10))}</div></div>
  <section class="design-hero"><div><p class="eyebrow">${esc(report.game_title)} · REVERSE-ENGINEERED DESIGN SPEC</p><h1>${esc(spec.title)}</h1><p>${esc(report.summary)}</p><div class="contract-line"><span>${esc(report.contract_version)}</span><span>${esc(report.migration_status)}</span><span>${report.compiled.object_index.length} objects</span></div></div>${artifactFigure(report, cover, `${spec.title} · 真实运行证据`)}</section>
  <nav class="public-play-nav" aria-label="玩法内容"><a href="#play-design">玩法设计文档</a><a href="#spec-surfaces">界面与交互路线</a><a href="#play-screen-tags">界面标签</a><a href="#play-records">游玩记录</a><a href="#spec-voices">社群反馈</a><a href="#play-tags">玩法标签</a><a href="#play-demos">Demo 复现</a></nav>
  <div class="design-reader" id="play-design"><aside class="design-rail"><b>设计案章节</b><nav>${chapterNav}</nav><details><summary>对象索引</summary><ul>${objectCounts}</ul></details></aside><div class="design-document">
    <section class="design-chapter" id="spec-overview"><header><span>01</span><div><p class="eyebrow">COVERAGE &amp; OVERVIEW</p><h2>系统概述与完整度</h2></div></header><ul class="coverage-grid">${coverage}</ul><div class="spec-card-grid">${statementCards(report, [...spec.overview, ...spec.player_goals, ...spec.entry_and_unlock])}</div></section>
    <section class="design-chapter" id="spec-core-loop"><header><span>02</span><div><p class="eyebrow">CORE LOOP</p><h2>${esc(spec.core_loop.title)}</h2><p>${esc(spec.core_loop.player_goal)}</p></div></header><img class="generated-diagram" src="${esc(report.compiled.diagrams.core_loop)}" alt="${esc(spec.core_loop.title)} 核心循环图"><ol class="core-loop-steps">${coreSteps}</ol><details class="observed-trace"><summary>展开真实观察轨迹与设计步骤映射</summary><ol>${observedFlow}</ol></details></section>
    <section class="design-chapter" id="spec-surfaces"><header><span>03</span><div><p class="eyebrow">INFORMATION ARCHITECTURE &amp; SURFACES</p><h2>信息架构与页面设计</h2></div></header><img class="generated-diagram" src="${esc(report.compiled.diagrams.navigation)}" alt="页面导航关系图"><table><thead><tr><th>从</th><th>触发</th><th>到</th><th>条件</th></tr></thead><tbody>${navigation}</tbody></table><div class="surface-explorers">${surfaces}</div></section>
    <section class="design-chapter" id="spec-interaction"><header><span>04</span><div><p class="eyebrow">INTERACTION &amp; STATE</p><h2>交互、分支与状态</h2></div></header><img class="generated-diagram" src="${esc(report.compiled.diagrams.interaction)}" alt="玩家操作与系统响应图"><div class="interaction-grid">${interactions}</div>${stateMatrices}</section>
    <section class="design-chapter" id="spec-rules"><header><span>05</span><div><p class="eyebrow">RULES &amp; ECONOMY</p><h2>机制、规则与资源关系</h2></div></header><div class="spec-card-grid">${mechanisms}</div><table><thead><tr><th>资源</th><th>角色</th><th>关系说明</th></tr></thead><tbody>${resources}</tbody></table>${coverageNote(spec, 'resources_economy')}</section>
    <section class="design-chapter" id="spec-progression"><header><span>06</span><div><p class="eyebrow">PROGRESSION &amp; BALANCE</p><h2>成长轴与数值参数</h2></div></header><div class="spec-card-grid">${progressions}${balances}</div>${coverageNote(spec, 'progression_balance')}</section>
    <section class="design-chapter" id="spec-feedback"><header><span>07</span><div><p class="eyebrow">FEEDBACK &amp; TUTORIAL</p><h2>反馈规范与教学</h2></div></header><div class="spec-card-grid">${feedback}${tutorials}</div>${coverageNote(spec, 'tutorial')}</section>
    <section class="design-chapter" id="spec-failure"><header><span>08</span><div><p class="eyebrow">FAILURE &amp; DEPENDENCIES</p><h2>失败恢复与系统依赖</h2></div></header><div class="spec-card-grid">${failures}${dependencies}</div></section>
    <section class="design-chapter" id="spec-voices"><header><span>09</span><div><p class="eyebrow">COMMUNITY, MEDIA &amp; DATA</p><h2>玩法社群与媒体反馈</h2></div></header><div class="community-feedback-list">${communityFeedback || '<p>当前没有已发布的真实平台反馈。</p>'}</div>${voices ? `<h3 class="feedback-subheading">玩家声音摘录</h3>${voices}` : ''}</section>
    <section class="design-chapter" id="spec-sources"><header><span>10</span><div><p class="eyebrow">VERSION &amp; PROVENANCE</p><h2>版本边界、来源与修订</h2></div></header><div class="spec-card-grid">${statementCards(report, [...spec.version_notes, ...spec.monetization_specs])}</div><ul class="source-list">${sourceItems}</ul><h3>证据资产</h3><ul class="source-list">${artifactItems}</ul>${revisionList ? `<h3>设计案修订</h3><ul class="source-list">${revisionList}</ul><div class="diff-panel" data-diff-panel hidden></div>` : ''}${collaborationPanel(report)}</section>
  </div></div>${playSupplement}`
}

function reportView(report) {
  if (!report.design_spec) throw new Error('published item is missing ReverseEngineeredGameDesignSpec')
  return designReportView(report)
}

async function openReport(slug, push = true) {
  const response = await fetch(`/api/game-observatory/reports/${encodeURIComponent(slug)}`)
  if (!response.ok) throw new Error(`report ${response.status}`)
  const report = await response.json()
  state.currentReport = report
  state.currentGame = state.games.find((item) => item.id === reportGameId(report)) || null
  $('[data-catalog-shell]').hidden = true
  $('[data-game-view]').hidden = true
  $('.hero').hidden = true
  const view = $('[data-report-view]')
  view.hidden = false
  view.innerHTML = reportView(report)
  if (push) history.pushState({ play: slug }, '', `/game-observatory/play/${encodeURIComponent(slug)}`)
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function showHome(push = true) {
  $('[data-report-view]').hidden = true
  $('[data-game-view]').hidden = true
  $('[data-catalog-shell]').hidden = false
  $('.hero').hidden = false
  if (push) history.pushState({}, '', '/game-observatory/')
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-game],[data-report],[data-public-path],[data-tag],[data-home],[data-back],[data-clear],[data-scroll],[data-diff]')
  if (!target) return
  if (target.matches('[data-game]')) openGame(target.dataset.game)
  else if (target.matches('[data-public-path]')) window.location.href = target.dataset.publicPath
  else if (target.matches('[data-report]')) openReport(target.dataset.report).catch(console.error)
  else if (target.matches('[data-diff]')) showRevisionDiff(target).catch(console.error)
  else if (target.matches('[data-tag]')) { state.activeTag = target.dataset.tag; loadCatalog().catch(console.error) }
  else if (target.matches('[data-clear]')) { state.activeTag = ''; state.query = ''; $('[data-search]').value = ''; loadCatalog().catch(console.error) }
  else if (target.matches('[data-scroll]')) document.getElementById(target.dataset.scroll)?.scrollIntoView()
  else if (target.matches('[data-back]') && state.currentGame) openGame(state.currentGame.slug)
  else showHome()
})

document.addEventListener('click', (event) => {
  const load = event.target.closest('[data-collab-load]')
  const apply = event.target.closest('[data-patch-apply]')
  const reject = event.target.closest('[data-patch-reject]')
  if (load) loadCollaboration().catch((error) => { $('[data-collab-status]').textContent = error.message })
  if (apply) collabFetch(`/api/game-observatory/patches/${encodeURIComponent(apply.dataset.patchApply)}/apply`, {
    method: 'POST', body: JSON.stringify({ reviewer: 'web-reviewer' }),
  }).then(() => openReport(state.currentReport.slug, false)).catch((error) => { $('[data-collab-status]').textContent = error.message })
  if (reject) {
    const reason = window.prompt('拒绝原因')
    if (reason) collabFetch(`/api/game-observatory/patches/${encodeURIComponent(reject.dataset.patchReject)}/reject`, {
      method: 'POST', body: JSON.stringify({ reviewer: 'web-reviewer', reason }),
    }).then(loadCollaboration).catch((error) => { $('[data-collab-status]').textContent = error.message })
  }
})

document.addEventListener('click', (event) => {
  const mode = event.target.closest('[data-surface-mode]')
  if (mode) {
    const card = mode.closest('[data-surface-card]')
    card.dataset.mode = mode.dataset.surfaceMode
    card.querySelectorAll('[data-surface-mode]').forEach((button) => {
      const active = button === mode
      button.classList.toggle('is-active', active)
      button.setAttribute('aria-pressed', String(active))
    })
  }
  const link = event.target.closest('[data-object-link]')
  if (link) {
    const target = document.getElementById(link.dataset.objectLink)
    if (target) {
      target.classList.add('is-targeted')
      target.scrollIntoView({ behavior: 'smooth', block: 'center' })
      window.setTimeout(() => target.classList.remove('is-targeted'), 1800)
    }
  }
})

document.addEventListener('submit', (event) => {
  if (event.target.matches('[data-annotation-form]')) {
    event.preventDefault()
    const data = new FormData(event.target)
    collabFetch(`/api/game-observatory/reports/${encodeURIComponent(state.currentReport.id)}/annotations`, {
      method: 'POST', body: JSON.stringify({ object_id: data.get('object_id'), author: data.get('author'), body: data.get('body'), kind: 'comment', source_ids: [] }),
    }).then(loadCollaboration).catch((error) => { $('[data-collab-status]').textContent = error.message })
  }
  if (event.target.matches('[data-patch-form]')) {
    event.preventDefault()
    const data = new FormData(event.target)
    let operations
    try { operations = JSON.parse(data.get('operations')) } catch (error) { $('[data-collab-status]').textContent = `JSON 错误：${error.message}`; return }
    collabFetch(`/api/game-observatory/reports/${encodeURIComponent(state.currentReport.id)}/patches`, {
      method: 'POST', body: JSON.stringify({ base_revision: state.currentReport.revisions[0].revision, author: data.get('author'), note: data.get('note'), operations }),
    }).then(loadCollaboration).catch((error) => { $('[data-collab-status]').textContent = error.message })
  }
})

document.addEventListener('keydown', (event) => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-game],[data-report],[data-public-path]')) event.target.click()
})

let searchTimer
$('[data-search]').addEventListener('input', (event) => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { state.query = event.target.value.trim(); loadCatalog().catch(console.error) }, 180)
})

window.addEventListener('popstate', () => {
  const playMatch = location.pathname.match(/\/game-observatory\/(?:play|report)\/([^/]+)/)
  const gameMatch = location.pathname.match(/\/game-observatory\/game\/([^/]+)/)
  if (playMatch) openReport(decodeURIComponent(playMatch[1]), false).catch(console.error)
  else if (gameMatch) openGame(decodeURIComponent(gameMatch[1]), false)
  else showHome(false)
})

Promise.all([loadCatalog(), loadHealth()]).then(() => {
  const playMatch = location.pathname.match(/\/game-observatory\/(?:play|report)\/([^/]+)/)
  const gameMatch = location.pathname.match(/\/game-observatory\/game\/([^/]+)/)
  if (playMatch) openReport(decodeURIComponent(playMatch[1]), false).catch(console.error)
  else if (gameMatch) openGame(decodeURIComponent(gameMatch[1]), false)
}).catch((error) => {
  console.error(error)
  $('[data-report-grid]').innerHTML = `<div class="empty">设施暂时无法读取：${esc(error.message)}</div>`
})
