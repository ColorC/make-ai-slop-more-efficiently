const state = { reports: [], tags: [], activeTag: '', query: '', health: null }

const $ = (selector) => document.querySelector(selector)
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[c]))

function publicUrl(source) {
  if (!source.public || !/^https?:\/\//.test(source.url || '')) return ''
  return source.url
}

async function loadHealth() {
  try {
    const response = await fetch('/api/game-observatory/health')
    if (!response.ok) throw new Error(`health ${response.status}`)
    state.health = await response.json()
    const online = (state.health.targets || []).filter((item) => item.status === 'online').length
    $('[data-live]').classList.add('is-live')
    $('[data-live] span').textContent = `${online} 个设备目标在线`
    $('[data-footer-status]').textContent = `SQLite canonical store · ${online} targets online`
  } catch (error) {
    $('[data-live] span').textContent = '设施离线'
    console.error(error)
  }
}

async function loadCatalog() {
  const params = new URLSearchParams()
  if (state.query) params.set('q', state.query)
  if (state.activeTag) params.set('tag', state.activeTag)
  const response = await fetch(`/api/game-observatory/catalog?${params}`)
  if (!response.ok) throw new Error(`catalog ${response.status}`)
  const data = await response.json()
  state.reports = data.reports || []
  state.tags = data.tags || []
  renderCatalog()
}

function renderStats() {
  const reports = state.reports
  const sourceCount = reports.reduce((sum, item) => sum + item.sources.length, 0)
  const flowCount = reports.reduce((sum, item) => sum + item.flow.length, 0)
  const voiceCount = reports.reduce((sum, item) => sum + item.player_voices.length, 0)
  $('[data-stats]').innerHTML = [
    ['档案', reports.length], ['流程节点', flowCount], ['来源', sourceCount], ['玩家声音', voiceCount],
  ].map(([label, value]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join('')
}

function renderTags() {
  $('[data-tags]').innerHTML = state.tags.slice(0, 24).map((item) => (
    `<button class="tag ${state.activeTag === item.tag ? 'is-active' : ''}" data-tag="${esc(item.tag)}">${esc(item.tag)} · ${item.count}</button>`
  )).join('')
}

function reportCard(report, index) {
  return `<article class="report-card" role="button" tabindex="0" data-report="${esc(report.slug)}" data-index="0${index + 1}">
    <span class="card-game">${esc(report.game_title)} · ${esc(report.scope.version)}</span>
    <h3>${esc(report.system_title)}</h3>
    <p>${esc(report.summary)}</p>
    <div class="card-bottom"><div class="mini-tags">${report.tags.slice(0, 5).map((tag) => `<span>#${esc(tag)}</span>`).join('')}</div><span class="card-arrow">↗</span></div>
  </article>`
}

function renderCatalog() {
  renderStats()
  renderTags()
  $('[data-result-count]').textContent = `${state.reports.length} 份公开档案`
  $('[data-report-grid]').innerHTML = state.reports.length
    ? state.reports.map(reportCard).join('')
    : '<div class="empty">没有符合当前筛选的档案。</div>'
}

function sourceName(report, id) {
  return report.sources.find((item) => item.id === id)?.title || id
}

function reportView(report) {
  const sourceItems = report.sources.map((source) => {
    const url = publicUrl(source)
    const title = url ? `<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(source.title)}</a>` : esc(source.title)
    return `<li>${title}<small>${esc(source.kind)} · ${esc(source.version_context || 'version unknown')}${source.public ? '' : ' · 内部定位未公开'}</small></li>`
  }).join('')
  const flow = report.flow.map((node, index) => `<div class="flow-node">
    <span class="flow-index">${String(index + 1).padStart(2, '0')}</span><div><h3>${esc(node.title)}</h3><p>${esc(node.description)}</p>${node.action ? `<span class="flow-action">ACTION · ${esc(node.action)}</span>` : ''}</div>
  </div>`).join('')
  const mechanisms = report.mechanisms.map((item) => `<article class="mechanism"><h3>${esc(item.title)}</h3><p>${esc(item.description)}</p>${item.code ? `<code>${esc(item.code)}</code>` : ''}</article>`).join('')
  const resources = report.resources.map((item) => `<article class="resource"><strong>${esc(item.resource)}<b>${esc(item.role)}</b></strong><p>${esc(item.description)}</p></article>`).join('')
  const voices = report.player_voices.map((voice) => `<blockquote class="voice"><p>${esc(voice.summary)}</p><footer>${esc(voice.theme)} · ${esc(sourceName(report, voice.source_id))} · ${esc(voice.version_context)}</footer></blockquote>`).join('') || '<p>当前没有进入公开投影的玩家声音。</p>'
  const task = report.benchmark_task ? `<div class="benchmark-note"><strong>${esc(report.benchmark_task.title)}</strong><span>${esc(report.benchmark_task.goal)}</span></div>` : ''
  return `<div class="report-topbar"><button class="back" data-back>← 返回所有档案</button><div class="scope-line">${esc(report.scope.platform)} / ${esc(report.scope.version)}<br>${esc(report.scope.region)} · ${esc(report.scope.captured_at.slice(0, 10))}</div></div>
  <section class="report-hero"><div><p class="eyebrow">${esc(report.game_title)} · ${esc(report.system_id)}</p><h1>${esc(report.system_title)}</h1><p class="report-summary">${esc(report.summary)}</p></div>
    <div class="report-metrics"><div><strong>${report.flow.length}</strong><span>flow nodes</span></div><div><strong>${report.mechanisms.length}</strong><span>mechanisms</span></div><div><strong>${report.sources.length}</strong><span>sources</span></div><div><strong>${report.player_voices.length}</strong><span>voices</span></div></div>
  </section>
  <div class="report-body"><div>
    <section class="report-section"><p class="eyebrow">PLAYER JOURNEY</p><h2>玩家旅程</h2><div class="flow">${flow}</div></section>
    <section class="report-section"><p class="eyebrow">FORMAL DESCRIPTION</p><h2>机制表达</h2>${mechanisms}</section>
    <section class="report-section"><p class="eyebrow">RESOURCE SYSTEM</p><h2>资源关系</h2>${resources}</section>
  </div><aside>
    <section class="report-section"><p class="eyebrow">PLAYER VOICE</p><h2>玩家声音</h2>${voices}</section>
    <section class="report-section"><p class="eyebrow">OBSERVATIONS</p><h2>观察与解释</h2><ul class="analysis-list">${report.observations.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></section>
    <section class="report-section"><p class="eyebrow">INTERPRETATION</p><ul class="analysis-list">${report.interpretations.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></section>
    ${task}
    <section class="report-section"><p class="eyebrow">PROVENANCE</p><h2>来源</h2><ul class="source-list">${sourceItems}</ul></section>
  </aside></div>`
}

async function openReport(slug, push = true) {
  const response = await fetch(`/api/game-observatory/reports/${encodeURIComponent(slug)}`)
  if (!response.ok) throw new Error(`report ${response.status}`)
  const report = await response.json()
  $('[data-catalog-shell]').hidden = true
  $('.hero').hidden = true
  const view = $('[data-report-view]')
  view.hidden = false
  view.innerHTML = reportView(report)
  if (push) history.pushState({ slug }, '', `/game-observatory/report/${encodeURIComponent(slug)}`)
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function showHome(push = true) {
  $('[data-report-view]').hidden = true
  $('[data-catalog-shell]').hidden = false
  $('.hero').hidden = false
  if (push) history.pushState({}, '', '/game-observatory/')
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-report],[data-tag],[data-home],[data-back],[data-clear],[data-scroll]')
  if (!target) return
  if (target.matches('[data-report]')) openReport(target.dataset.report).catch(console.error)
  else if (target.matches('[data-tag]')) { state.activeTag = target.dataset.tag; loadCatalog().catch(console.error) }
  else if (target.matches('[data-clear]')) { state.activeTag = ''; state.query = ''; $('[data-search]').value = ''; loadCatalog().catch(console.error) }
  else if (target.matches('[data-scroll]')) document.getElementById(target.dataset.scroll)?.scrollIntoView()
  else showHome()
})

document.addEventListener('keydown', (event) => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-report]')) event.target.click()
})

let searchTimer
$('[data-search]').addEventListener('input', (event) => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { state.query = event.target.value.trim(); loadCatalog().catch(console.error) }, 180)
})

window.addEventListener('popstate', () => {
  const match = location.pathname.match(/\/game-observatory\/report\/([^/]+)/)
  if (match) openReport(decodeURIComponent(match[1]), false).catch(console.error)
  else showHome(false)
})

Promise.all([loadCatalog(), loadHealth()]).then(() => {
  const match = location.pathname.match(/\/game-observatory\/report\/([^/]+)/)
  if (match) openReport(decodeURIComponent(match[1]), false).catch(console.error)
}).catch((error) => {
  console.error(error)
  $('[data-report-grid]').innerHTML = `<div class="empty">设施暂时无法读取：${esc(error.message)}</div>`
})