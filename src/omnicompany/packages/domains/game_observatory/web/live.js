const $ = (selector) => document.querySelector(selector)
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]))

const state = {
  environmentId: new URLSearchParams(location.search).get('environment_id') || '',
  sessionId: new URLSearchParams(location.search).get('session_id') || '',
  frameUrl: '',
  streamKey: '',
  streamMode: 'hls',
  mediaStream: null,
  loading: false,
  canInstruct: false,
}

function formatNumber(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return new Intl.NumberFormat('zh-CN', {
    notation: number >= 100000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(number)
}

function formatDuration(value) {
  let seconds = Math.max(0, Number(value) || 0)
  const hours = Math.floor(seconds / 3600)
  seconds -= hours * 3600
  const minutes = Math.floor(seconds / 60)
  if (!hours && !minutes) return `${Math.round(seconds)}秒`
  return `${hours ? `${hours}时 ` : ''}${minutes}分`
}

function formatDate(value) {
  if (!value) return '未观测'
  const date = new Date(value)
  return Number.isNaN(date.valueOf())
    ? String(value)
    : date.toLocaleString('zh-CN', { hour12: false })
}

function timestamp(value) {
  const parsed = new Date(value || 0).valueOf()
  return Number.isFinite(parsed) ? parsed : 0
}

function statusLabel(value) {
  return ({
    active: '运行中', suspended: '已暂停', closed: '已结束', failed: '失败',
    succeeded: '完成', timed_out: '超时', approved: '等待下一轮',
    rejected: '未采用', delivered: '已送入 Agent 下一轮', passed: '成功',
    candidate: '待确认', queued: '排队中', blocked: '受阻',
  })[value] || value || '未知'
}

function cleanTarget(value) {
  return String(value || '')
    .replace(/\s*\/\s*step\.\d+\.action\s*$/i, '')
    .replace(/^待裁决：/, '')
    .replace(/（操作后）$/, '')
    .trim()
}

function actionDescription(item) {
  const action = item?.action || {}
  const target = cleanTarget(item?.target_name)
  switch (action.type) {
    case 'tap':
      return target ? `点击「${target}」` : `点击画面坐标（${action.x ?? '—'}, ${action.y ?? '—'}）`
    case 'swipe':
      return target ? `在「${target}」滑动` : '滑动游戏画面'
    case 'back':
      return '返回上一层游戏界面'
    case 'key':
    case 'keyevent':
      return action.keycode === 4 ? '返回上一层游戏界面' : `触发游戏按键 ${action.keycode || ''}`.trim()
    case 'text':
      return target ? `在「${target}」输入游戏文本` : '输入游戏内文本'
    case 'launch':
      return `启动或切回游戏${action.package ? `（${action.package}）` : ''}`
    case 'wait':
      return target && !target.includes('只读观察') ? `等待「${target}」状态稳定` : '观察画面并等待状态稳定'
    default:
      return target || action.type || '观察当前游戏画面'
  }
}

function aggregateActions(operations) {
  const ordered = [...(operations || [])].sort(
    (left, right) => timestamp(left.started_at) - timestamp(right.started_at),
  )
  const grouped = []
  ordered.forEach((item) => {
    const text = actionDescription(item)
    const previous = grouped.at(-1)
    if (previous?.text === text) previous.count += 1
    else grouped.push({ text, count: 1, failed: Boolean(item.error) })
  })
  const meaningful = grouped.filter((item) => !item.text.startsWith('观察画面'))
  return (meaningful.length ? meaningful : grouped).slice(0, 8)
}

function operationsForTurns(turns, operations) {
  const assignments = new Map(turns.map((turn) => [turn.id, []]))
  const unmatched = []
  ;(operations || []).forEach((operation) => {
    const at = timestamp(operation.started_at || operation.ended_at)
    const turn = turns.find((candidate) => {
      const start = timestamp(candidate.started_at) - 5000
      const end = timestamp(candidate.completed_at || candidate.started_at) + 15000
      return at >= start && at <= end
    })
    if (turn) assignments.get(turn.id).push(operation)
    else unmatched.push(operation)
  })
  return { assignments, unmatched }
}

function renderJournal(agent, operations) {
  const turns = [...(agent?.turns || [])].reverse()
  const { assignments, unmatched } = operationsForTurns(turns, operations)
  const cards = turns.map((turn, index) => {
    const actions = aggregateActions(assignments.get(turn.id))
    const result = turn.decision_summary
      || (turn.status === 'succeeded'
        ? '本轮完成，但 Agent 没有提交可公开的结果摘要。'
        : `本轮没有形成有效结果，状态为“${statusLabel(turn.status)}”。`)
    const actionHtml = actions.length
      ? `<div class="action-highlights">${actions.map((item, actionIndex) => `
          <div class="action-line"><i>${actionIndex + 1}</i><span>${esc(item.text)}${item.count > 1 ? ` ×${item.count}` : ''}</span></div>
        `).join('')}</div>`
      : '<div class="action-highlights"><div class="action-line"><i>—</i><span>本轮没有匹配到规范化游戏操作回执。</span></div></div>'
    return `
      <article class="turn ${index === 0 && agent?.status === 'active' ? 'is-live' : ''}">
        <div class="turn-head"><span>第 ${esc(turn.sequence)} 轮 · ${esc(statusLabel(turn.status))}</span><span>${esc(formatDate(turn.completed_at || turn.started_at))}</span></div>
        <p class="turn-result">${esc(result)}</p>
        ${actionHtml}
        <div class="pill-list">
          <span class="pill">${esc(turn.actions_executed ?? actions.length)} 个游戏行为</span>
          <span class="pill">${esc(formatNumber(turn.usage?.input_tokens))} 输入 tokens</span>
          <span class="pill">${esc(formatNumber(turn.usage?.output_tokens))} 输出 tokens</span>
          <span class="pill">${esc(formatDuration(turn.duration_seconds))}</span>
        </div>
        ${turn.agent_output ? `<details class="turn-output"><summary>打开本轮 Agent 最终输出</summary><pre>${esc(turn.agent_output)}</pre></details>` : ''}
      </article>`
  })
  const orphanActions = aggregateActions(unmatched)
  if (orphanActions.length) {
    cards.push(`
      <article class="turn">
        <div class="turn-head"><span>较早的设施操作回执</span><span>未匹配到当前展示的 Agent 轮次</span></div>
        <div class="action-highlights">${orphanActions.map((item, index) => `
          <div class="action-line"><i>${index + 1}</i><span>${esc(item.text)}${item.count > 1 ? ` ×${item.count}` : ''}</span></div>
        `).join('')}</div>
      </article>`)
  }
  $('[data-turns]').innerHTML = cards.join('') || '<p class="muted">尚无 Agent 连续记录。</p>'
  $('[data-journal-count]').textContent = `${turns.length} 轮 · ${operations?.length || 0} 条操作回执`
}

function renderAgent(agent, operations) {
  const status = statusLabel(agent?.status)
  const model = agent?.resolved_model_id || agent?.model_selector || '—'
  $('[data-agent-chip]').textContent = status
  $('[data-agent-state]').textContent = `AGENT — ${status}`
  $('[data-stage-agent]').textContent = status
  $('[data-stage-model]').textContent = model
  $('[data-stage-runtime]').textContent = formatDuration(agent?.provider_runtime_seconds)
  $('[data-stage-actions]').textContent = `${formatNumber(agent?.semantic_action_count)} 次`
  $('[data-agent-identity]').innerHTML = agent ? [
    ['框架', agent.framework],
    ['Provider', agent.provider],
    ['模型', model],
    ['推理强度', agent.actual_effort || agent.requested_effort],
    ['当前阶段', agent.phase_id],
    ['会话', agent.session_id],
  ].map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value || '—')}</dd>`).join('')
    : '<dt>状态</dt><dd>没有匹配的外部 Agent 会话</dd>'

  const usage = agent?.usage || {}
  const cost = agent?.cost?.reported_usd
  $('[data-agent-stats]').innerHTML = [
    [formatNumber(usage.input_tokens), '输入 tokens'],
    [formatNumber(usage.output_tokens), '输出 tokens'],
    [formatNumber(usage.reasoning_tokens), '推理 tokens（计量）'],
    [cost == null ? '未上报' : `$${Number(cost).toFixed(4)}`, 'Provider 回执费用'],
    [formatDuration(agent?.provider_runtime_seconds), 'Provider 运行时长'],
    [formatNumber(agent?.semantic_action_count), '语义游戏行为'],
  ].map(([value, label]) => `<div class="stat"><b>${esc(value)}</b><small>${esc(label)}</small></div>`).join('')
  renderJournal(agent, operations)
}

function metricKey(metric) {
  return metric?.definition?.metric_key
    || metric?.delta?.metric_key
    || metric?.metric_key
    || metric?.definition_id
    || metric?.metric_id
    || metric?.id
}

function metricLabel(metric, fallback) {
  return metric?.definition?.label || metric?.label || fallback || metricKey(metric)
}

function metricValue(metric) {
  const candidates = [
    metric?.after?.value, metric?.after_observation?.value, metric?.delta?.after,
    metric?.after_value, metric?.value,
  ]
  return candidates.find((value) => value !== undefined && value !== null)
}

function metricObservedAt(metric) {
  return metric?.after?.observed_at
    || metric?.after_observation?.observed_at
    || metric?.created_at
    || metric?.observed_at
}

function metricDirectEvidence(metric) {
  const source = metric?.after_observation?.source
  if (source?.method !== 'screenshot_ocr') return null
  return {
    confidence: Number(source.confidence),
    rawText: source.raw_text,
    artifactId: source.frame_artifact_id,
  }
}

function renderAccount(account) {
  $('[data-observed-at]').textContent = `最后观测 ${formatDate(account?.last_observed_at)}`
  const current = account?.current_state
  $('[data-account-state]').innerHTML = current
    ? `<strong>最后识别界面：${esc(cleanTarget(current.title || current.id))}</strong>
       <span>${esc(current.description || '')} · ${esc(statusLabel(current.status))} · v${esc(current.version)}</span>`
    : '<strong>尚无已确认的账号界面状态</strong><span>直播页不会用旧文档读数冒充当前观测。</span>'

  const derivations = account?.metric_derivations || []
  const byKey = new Map()
  derivations.forEach((metric) => byKey.set(metricKey(metric), metric))
  const catalog = account?.key_metric_catalog || []
  $('[data-account-metrics]').innerHTML = catalog.map((definition) => {
    const metric = byKey.get(definition.metric_key)
    const value = metricValue(metric)
    const direct = metricDirectEvidence(metric)
    if (value === undefined || !direct || direct.confidence < 0.95) {
      return `<div class="key-metric unobserved"><small>${esc(definition.label)}</small><strong>尚未精准观测</strong><span>等待 canonical 局部截图 OCR</span></div>`
    }
    const href = `/api/game-observatory/internal/artifacts/${encodeURIComponent(direct.artifactId)}`
    return `<a class="key-metric" href="${esc(href)}" target="_blank" rel="noopener">
      <small>${esc(metricLabel(metric, definition.label))} · 直接截图</small>
      <strong>${esc(formatNumber(value))}</strong>
      <span>OCR ${esc((direct.confidence * 100).toFixed(1))}% · ${esc(formatDate(metricObservedAt(metric)))} · 打开原图</span>
    </a>`
  }).join('')

  const identity = account?.identity || {}
  const budget = account?.budget || {}
  const details = [
    ['账号', identity.account_scope_id],
    ['服务器', identity.server_scope_id],
    ['世界', identity.world_scope_id],
    ['渠道', identity.channel],
    ['版本', identity.build_scope_id],
    ['剩余动作预算', budget.actions_remaining],
    ['剩余时间预算', budget.seconds_remaining == null ? '—' : formatDuration(budget.seconds_remaining)],
    ['数据来源', budget.source],
  ]
  $('[data-account-details]').innerHTML = details.map(([label, value]) => `
    <div class="account-detail"><small>${esc(label)}</small><b>${esc(value ?? '—')}</b></div>
  `).join('')

  const tasks = account?.tasks || []
  $('[data-account-tasks]').innerHTML = tasks.slice(0, 12).map((task) => `
    <div class="task"><b>${esc(task.title || task.id)}</b>
    <span>${esc(statusLabel(task.status))} · ${esc(task.reason || task.description || '')}</span></div>
  `).join('') || '<p class="muted">当前没有账号任务。</p>'
}

function routeAction(edge, destination) {
  const action = edge?.action || {}
  switch (action.type) {
    case 'tap':
      return destination ? `点击进入「${destination}」` : `点击（${action.x ?? '—'}, ${action.y ?? '—'}）`
    case 'swipe': return '滑动画面'
    case 'back': return '返回上一层'
    case 'wait': return '等待界面稳定'
    default: return action.type || '执行游戏操作'
  }
}

function renderBehavior(model) {
  const nodes = (model?.nodes || []).filter(Boolean)
  const edges = (model?.edges || []).filter(Boolean)
  const nodeMap = new Map(nodes.map((node) => [node.id, cleanTarget(node.title || node.id)]))
  const current = nodeMap.get(model?.current_state_id) || '尚未确认当前界面'
  const verifiedCount = edges.filter((edge) => String(edge.outcome || edge.status || '').includes('verified')).length
  $('[data-behavior-kind]').textContent = model?.available ? `${nodes.length} 个界面 · ${edges.length} 条路线` : '尚无路线'
  $('[data-behavior-explanation]').textContent = model?.explanation || ''
  $('[data-behavior-summary]').innerHTML = `
    <span class="summary-chip current">AI 当前认为：${esc(current)}</span>
    <span class="summary-chip">已认识 ${esc(nodes.length)} 个游戏界面</span>
    <span class="summary-chip">积累 ${esc(edges.length)} 条操作路线</span>
    <span class="summary-chip">${esc(verifiedCount)} 条已明确标记验证</span>`

  const routes = [...edges].reverse().slice(0, 14)
  $('[data-routes]').innerHTML = routes.length ? routes.map((edge) => {
    const from = nodeMap.get(edge.from_state_id || edge.from_id) || '未命名界面'
    const to = nodeMap.get(edge.to_state_id || edge.to_id) || '未命名界面'
    const outcome = statusLabel(edge.outcome || edge.status || '已有证据')
    return `<div class="route-card">
      <div class="route-node">从：${esc(from)}</div>
      <div class="route-action">${esc(routeAction(edge, to))}</div>
      <div class="route-node">到：${esc(to)}</div>
      <div class="route-outcome">${esc(outcome)}</div>
    </div>`
  }).join('') : '<p class="muted">设施还没有积累可读的游戏路线。</p>'

  $('[data-frontier]').innerHTML = `<div class="pill-list">${(model?.frontier || []).slice(0, 10).map(
    (item) => `<span class="pill">${esc(cleanTarget(item.title || item.id))}</span>`,
  ).join('') || '<span class="muted">没有开放目标</span>'}</div>`
  $('[data-skills]').innerHTML = `<div class="pill-list">${(model?.skills || []).slice(0, 10).map(
    (item) => `<span class="pill">${esc(cleanTarget(item.name || item.title || item.skill_id || item.id))}</span>`,
  ).join('') || '<span class="muted">尚无已沉淀路线</span>'}</div>`
}

function renderQueue(items) {
  const queue = $('[data-instruction-queue]')
  queue.innerHTML = (items || []).length ? items.slice(0, 30).map((item) => `
    <article class="chat-message ${esc(item.status)}">
      <span class="chat-avatar">${esc((item.display_name || '观').slice(0, 1))}</span>
      <div class="chat-bubble"><b>${esc(item.display_name)}<span>${esc(statusLabel(item.status))}</span></b>
      <p>${esc(item.raw_instruction)}</p></div>
    </article>
  `).join('') : '<p class="muted">还没有观众消息。第一条合规游戏指示会在完整轮次边界交付。</p>'
}

function streamUrl(media, mode) {
  if (!media?.path) return ''
  return `/game-observatory/live-media/${mode}/${encodeURIComponent(media.path)}/`
    + '?autoplay=true&muted=true&controls=false&playsinline=true&disablepictureinpicture=true'
}

function showMediaStream(room, mode = state.streamMode) {
  const media = room?.media_stream || state.mediaStream
  if (!media) return showLegacyFrame(room?.stream_url || state.frameUrl)
  state.mediaStream = media
  state.streamMode = mode
  const key = `${media.path}:${mode}:${location.hostname}`
  const player = $('[data-live-video]')
  const frame = $('[data-live-frame]')
  if (state.streamKey !== key) {
    state.streamKey = key
    player.src = streamUrl(media, mode)
  }
  player.hidden = false
  frame.hidden = true
  $('[data-frame-placeholder]').hidden = true
  document.querySelectorAll('[data-stream-mode]').forEach((button) => {
    button.classList.toggle('active', button.dataset.streamMode === mode)
  })
}

function showLegacyFrame(url) {
  if (!url) return
  state.frameUrl = url
  const frame = $('[data-live-frame]')
  const player = $('[data-live-video]')
  player.hidden = true
  frame.hidden = false
  if (!frame.src) frame.src = `${url}&interval_seconds=1`
  frame.onload = () => { $('[data-frame-placeholder]').hidden = true }
  frame.onerror = () => {
    $('[data-frame-placeholder]').hidden = false
    $('[data-frame-placeholder]').textContent = '设备画面暂时不可用'
  }
}

async function loadStreamStatus() {
  if (!state.environmentId) return
  const query = new URLSearchParams({ environment_id: state.environmentId })
  try {
    const response = await fetch(`/api/game-observatory/ai-player/live/stream/status?${query}`, { cache: 'no-store' })
    if (!response.ok) return
    const status = await response.json()
    const media = status.distribution
    if (media?.ready) {
      const fps = Number(media.target_fps || 60)
      const viewers = Number(media.active_viewers || 0)
      const mbps = Number(media.estimated_total_mbps || 0)
      const protocol = state.streamMode === 'hls' ? 'HLS' : 'WebRTC'
      $('[data-frame-note]').textContent = `OBS / ${protocol} · ${media.target_resolution} · ${fps.toFixed(0)} FPS`
      $('[data-stream-health]').textContent = `${viewers} 位观看 · ${media.video_codec} · 约 ${mbps.toFixed(1)} Mbps`
      showMediaStream(null, state.streamMode)
    } else {
      $('[data-frame-note]').textContent = '视频流离线 · 已回退到低带宽截图（720p / 1 FPS）'
      $('[data-stream-health]').textContent = '截图故障回退 · 非正常直播模式'
      showLegacyFrame(state.frameUrl)
    }
  } catch (error) {
    console.debug('stream telemetry unavailable', error)
  }
}

async function loadRoom() {
  if (state.loading) return
  state.loading = true
  const query = new URLSearchParams()
  if (state.environmentId) query.set('environment_id', state.environmentId)
  if (state.sessionId) query.set('session_id', state.sessionId)
  try {
    const response = await fetch(`/api/game-observatory/ai-player/live?${query}`, { cache: 'no-store' })
    if (!response.ok) throw new Error(await response.text())
    const payload = await response.json()
    state.environmentId = payload.room.environment_id
    state.sessionId = payload.room.session_id || ''
    $('[data-room-status]').textContent = payload.agent?.status === 'active'
      ? 'Agent 正在玩 · 设施在线'
      : `Agent ${statusLabel(payload.agent?.status)} · 设施在线`
    $('[data-game-title]').textContent = payload.room.game_id || '当前游戏画面'
    $('[data-generated-at]').textContent = formatDate(payload.generated_at)
    state.frameUrl = payload.room.stream_url || state.frameUrl
    showMediaStream(payload.room)
    renderAgent(payload.agent, payload.operations)
    renderAccount(payload.account)
    renderBehavior(payload.behavior_model)
    renderQueue(payload.instructions)
    await loadStreamStatus()
  } catch (error) {
    $('[data-room-status]').textContent = '直播设施读取失败'
    console.error(error)
  } finally {
    state.loading = false
  }
}

async function loadAccessPolicy() {
  const form = $('[data-instruction-form]')
  const output = $('[data-submit-result]')
  try {
    const response = await fetch('/api/lan-access/me', { cache: 'no-store' })
    const payload = response.ok ? await response.json() : null
    state.canInstruct = Boolean(payload?.allowed)
  } catch (_error) {
    state.canInstruct = false
  }
  if (state.canInstruct) return
  form.querySelectorAll('input, textarea, button').forEach((element) => {
    element.disabled = true
  })
  output.textContent = '公开旁观模式：直播可直接观看，发送 AI 指示需要受信设备。'
}

$('[data-instruction-form]').addEventListener('submit', async (event) => {
  event.preventDefault()
  if (!state.canInstruct) return
  const form = event.currentTarget
  const button = form.querySelector('button')
  const output = $('[data-submit-result]')
  const data = new FormData(form)
  const displayName = String(data.get('display_name') || '').trim()
  const instruction = String(data.get('instruction') || '').trim()
  if (!instruction) return
  localStorage.setItem('gameObservatoryLiveDisplayName', displayName)
  button.disabled = true
  output.textContent = '正在接收指示…'
  try {
    const response = await fetch('/api/game-observatory/ai-player/live/instructions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        environment_id: state.environmentId,
        session_id: state.sessionId || null,
        display_name: displayName || '局域网观众',
        instruction,
      }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '提交失败')
    const item = payload.instruction
    output.textContent = `${statusLabel(item.status)}：${item.review_reason}`
    if (item.status !== 'rejected') form.elements.instruction.value = ''
    await loadRoom()
  } catch (error) {
    output.textContent = String(error.message || error)
  } finally {
    button.disabled = false
  }
})

const savedName = localStorage.getItem('gameObservatoryLiveDisplayName')
if (savedName) $('[data-instruction-form]').elements.display_name.value = savedName
document.querySelectorAll('[data-stream-mode]').forEach((button) => {
  button.addEventListener('click', () => showMediaStream(null, button.dataset.streamMode))
})
loadAccessPolicy()
loadRoom()
setInterval(loadRoom, 1500)

