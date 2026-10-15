const MAIN_ENTITY_ID = 'main-board'

const NAVIGATION_TITLES = {
  'main-board': '餐厅棋盘',
  'restaurant-scene': '浪漫餐厅',
  'main-task-panel': '任务面板',
  'story-dialogue': '剧情对话',
  'decoration-choice': '装饰选择',
  'decoration-confirmation': '装饰确认',
  'energy-supply-panel': '体力补给',
  'warehouse-price-panel': '仓库价格',
  'daily-rewards': '每日奖励',
  'reward-panel': '奖励面板',
  'level-up-reward': '升级奖励',
  'board-story-prompt': '主界面剧情提示',
  'shop-daily-specials-and-hot-selling': '商店',
  'scene-transition-curtain': '场景切换',
  'story-cutscene': '剧情演出',
  'tutorial-overlay': '新手提示',
  'forced-shop-tutorial': '商店引导',
  'returning-player-gift': '回归礼物',
  'store-rating-prompt': '评价提示',
  'newbie-gift': '新手礼物',
  'newbie-gift-progress-modal': '新手礼物进度',
  'update-game-modal': '更新提示',
  'save-load-login': '登录提示',
  'spring-picnic-real-money-offer': '春日野餐优惠',
  'spring-picnic-real-money-offer-any-tier': '春日野餐优惠',
  'spring-picnic-real-money-offer-399': '春日野餐优惠',
  'intro-chest-real-money-offer': '开场宝箱优惠',
  'energy-depleted-guide': '体力不足提示',
  'energy-party-announcement': '体力活动提示',
  'energy-party-announcement-fullscreen': '体力活动',
  'rave-reviews-complete-panel': '好评奖励',
  'rave-reviews-event-intro': '好评活动',
}

const labels = {
  interactive: '点击后进入',
  automatic: '自动变化',
  'observed-gap': '过程未完整记录',
  success: '已到达',
  unknown: '结果待确认',
}

function text(value, fallback = '') {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback
}

function entityLabel(entity) {
  if (!entity) return '未识别界面'
  const catalogTitle = entity.catalog && text(entity.catalog.title)
  if (catalogTitle) return catalogTitle
  if (NAVIGATION_TITLES[entity.id]) return NAVIGATION_TITLES[entity.id]
  if (entity.provisional) return '未识别界面'
  const title = text(entity.title)
  return title && !/^(?:unknown|unrecognised|unrecognized|provisional)[:/]/i.test(title) && !/^[A-Za-z0-9_.:/-]+$/.test(title)
    ? title
    : '已识别界面'
}

function graphFromPayload(payload) {
  let candidate = payload
  for (let depth = 0; depth < 3 && candidate; depth += 1) {
    if (candidate.entities) return candidate
    candidate = candidate.graph || candidate.snapshot || candidate.data
  }
  return { entities: [], states: [], observations: [], elements: [], transitions: [] }
}

function mapIndexes(graph) {
  return {
    entities: new Map((graph.entities || []).map(item => [item.id, item])),
    states: new Map((graph.states || []).map(item => [item.id, item])),
    observations: new Map((graph.observations || []).map(item => [item.id || item.frame_id, item])),
    elements: new Map((graph.elements || []).map(item => [item.id, item])),
    transitions: graph.transitions || [],
  }
}

function representativeState(entity, indexes, graph) {
  const preferred = graph.view && graph.view.default_state_ids && graph.view.default_state_ids[entity.id]
  return indexes.states.get(preferred) || indexes.states.get((entity.state_ids || []).slice(-1)[0])
}

function firstObservation(state, indexes) {
  const observationId = state && (state.observation_ids || [])[0]
  return observationId ? indexes.observations.get(observationId) : null
}

function observationForState(state, indexes) {
  const ids = (state && state.observation_ids) || []
  return ids.length ? indexes.observations.get(ids[ids.length - 1]) : null
}

function friendlyTrigger(transition) {
  if (!transition) return '界面变化'
  return labels[transition.mode] || '界面变化'
}

function navigableTransitions(indexes) {
  return indexes.transitions.filter(transition => transition.mode === 'interactive' || transition.mode === 'observed-gap')
}

// Recorded triggers are the loop's intent names; the drawer reads them in Chinese.
const INTENT_WORDS = [
  ['popup:close:', '关闭弹窗'], ['popup:free-claim:', '领取奖励'], ['popup:tutorial-follow:', '按引导点击'],
  ['popup:story-advance:', '推进剧情'], ['popup:selection:', '选择'], ['popup:confirm:', '确认'], ['popup:wait:', '等待'],
  ['main-task:open-panel', '打开任务面板'], ['main-task:start', '开始主线任务'], ['main-task:story', '推进剧情'],
  ['main-task:return-to-board', '返回棋盘'], ['navigation:return-to-board', '返回棋盘'],
  ['navigation:main-board:energy-plus', '点体力加号'], ['navigation:main-board:shop', '进商店'],
  ['navigation:agent-handler', '接管探索'], ['navigation:', '界面跳转'], ['purchase:', '钻石买体力'],
  ['clear-cobweb:', '合成清蛛网'], ['merge:', '合成'], ['generate:', '生成'], ['select-generator', '选中生产器'],
  ['select-chest', '选中宝箱'], ['open-chest', '开宝箱'], ['select-reward-item', '选中奖励棋子'],
  ['collect-reward-item', '领奖励棋子'], ['select-energy-item', '选中体力棋子'], ['collect-energy-item', '领体力棋子'],
  ['claim-reward-queue', '领奖励队列'], ['deliver:', '交订单'], ['stash', '存仓库'], ['retrieve', '取仓库'],
]

function friendlyIntent(trigger) {
  const value = text(trigger)
  if (!value) return ''
  const match = INTENT_WORDS.find(([prefix]) => value.startsWith(prefix))
  return match ? match[1] : value
}

function interactiveTransitions(indexes) {
  return indexes.transitions.filter(transition => transition.mode === 'interactive')
}

function assetHref(path, assetUrl) {
  if (!path) return ''
  return typeof assetUrl === 'function' ? assetUrl(path) : path
}

function frameOf(observation) {
  return observation && observation.frame && typeof observation.frame === 'object'
    ? observation.frame
    : observation
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild)
}

function button(document, label, className, handler) {
  const node = document.createElement('button')
  node.type = 'button'
  node.className = className || ''
  node.textContent = label
  node.addEventListener('click', handler)
  return node
}

function renderScreenshot(document, host, observation, assetUrl) {
  clear(host)
  const frame = frameOf(observation)
  if (!frame || !frame.path) {
    host.textContent = '暂无画面'
    return
  }
  const image = document.createElement('img')
  image.className = 'play-map-screenshot'
  image.alt = '界面画面'
  image.loading = 'lazy'
  image.src = assetHref(frame.path, assetUrl)
  host.appendChild(image)
}

const FALLBACK_NODE_COLOR = '#365875'
const COLUMN_GAP = 230
const ROW_GAP = 92

// The catalog (interface_catalog.v1.json) groups surfaces; the page keeps the
// catalog's category order so the same category always lands in the same column.
function categoryIndex(graph) {
  return new Map((graph.categories || []).map((category, order) => [category.id, { ...category, order }]))
}

function entityCategory(entity, categories) {
  const id = entity && entity.catalog && entity.catalog.category
  return (id && categories.get(id)) || null
}

// One edge per ordered pair of surfaces.  The server folds the raw transition
// rows into summary_edges; without it (older payloads) the same fold is done here.
function surfaceEdges(graph, indexes) {
  if (Array.isArray(graph.summary_edges)) return graph.summary_edges
  const folded = new Map()
  interactiveTransitions(indexes).forEach(transition => {
    const key = `${transition.source_entity_id}->${transition.target_entity_id}`
    const edge = folded.get(key) || {
      id: `summary:${key}`,
      source_entity_id: transition.source_entity_id,
      target_entity_id: transition.target_entity_id,
      count: 0,
      interactive_count: 0,
      top_triggers: [],
    }
    edge.count += 1
    edge.interactive_count += 1
    folded.set(key, edge)
  })
  return Array.from(folded.values())
}

function edgeTransition(edge, indexes) {
  return indexes.transitions.find(item =>
    item.source_entity_id === edge.source_entity_id &&
    item.target_entity_id === edge.target_entity_id &&
    item.mode === 'interactive') || null
}

function presetPositions(graph, categories) {
  const columns = new Map()
  ;(graph.entities || []).forEach(entity => {
    const category = entityCategory(entity, categories)
    const column = category ? category.order : categories.size
    if (!columns.has(column)) columns.set(column, [])
    columns.get(column).push(entity)
  })
  const positions = new Map()
  Array.from(columns.keys()).sort((a, b) => a - b).forEach((column, columnIndex) => {
    columns.get(column)
      .sort((a, b) => (b.state_ids || []).length - (a.state_ids || []).length)
      .forEach((entity, row) => positions.set(entity.id, { x: columnIndex * COLUMN_GAP, y: row * ROW_GAP }))
  })
  return positions
}

function cyElements(graph, indexes) {
  const categories = categoryIndex(graph)
  if (categories.size) {
    const positions = presetPositions(graph, categories)
    const nodes = (graph.entities || []).map(entity => {
      const category = entityCategory(entity, categories)
      return {
        data: { id: entity.id, label: entityLabel(entity), color: (category && category.color) || FALLBACK_NODE_COLOR },
        position: positions.get(entity.id),
      }
    })
    const ids = new Set(nodes.map(node => node.data.id))
    const edges = surfaceEdges(graph, indexes)
      .filter(edge => edge.interactive_count > 0 && ids.has(edge.source_entity_id) && ids.has(edge.target_entity_id))
      .map(edge => ({
        data: {
          id: edge.id,
          source: edge.source_entity_id,
          target: edge.target_entity_id,
          label: `点击 ×${edge.interactive_count}`,
          width: Math.min(6, 1 + Math.log2(1 + edge.interactive_count) / 2),
        },
      }))
    return nodes.concat(edges)
  }
  const nodes = (graph.entities || []).map(entity => ({
    data: { id: entity.id, label: entityLabel(entity) },
  }))
  const edges = interactiveTransitions(indexes).map((transition, index) => ({
    data: {
      id: transition.id || `transition-${index}`,
      source: transition.source_entity_id,
      target: transition.target_entity_id,
      label: friendlyTrigger(transition),
    },
  })).filter(edge => nodes.some(node => node.data.id === edge.data.source) && nodes.some(node => node.data.id === edge.data.target))
  return nodes.concat(edges)
}

/**
 * Mount the interface exploration map.  When the host page supplies the
 * already-pinned Cytoscape runtime (`globalThis.cytoscape`), it is used for
 * graph layout and edge hit testing.  The semantic DOM view remains available
 * as a dependency-free fallback for the same API.
 */
export function mountMap(container, { fetchGraph, fetchObservations, assetUrl, onSeek } = {}) {
  if (!container || typeof container.ownerDocument?.createElement !== 'function') throw new TypeError('container is required')
  const document = container.ownerDocument
  const state = {
    graph: null, indexes: null, selected: null, query: '', cy: null, showAllEdges: false,
    observationOffset: 0, observationLimit: 24, observationEntity: null, observationPage: null,
    observationRequest: 0,
  }
  clear(container)
  container.classList.add('play-map')

  const toolbar = document.createElement('div')
  toolbar.className = 'play-map-toolbar'
  const title = document.createElement('h2')
  title.className = 'play-map-title'
  title.textContent = '界面地图'
  const search = document.createElement('input')
  search.type = 'search'
  search.className = 'play-map-search'
  search.placeholder = '查找界面'
  search.setAttribute('aria-label', '查找界面')
  const mainButton = button(document, '回到餐厅棋盘', 'play-map-main', () => select(MAIN_ENTITY_ID))
  const focusButton = button(document, '聚焦起点', 'play-map-focus', () => focusMain())
  const fitButton = button(document, '查看全图', 'play-map-fit', () => fitAll())
  // 186 click-driven surface pairs drawn at once read as a hairball, so the
  // overview shows the categorized surfaces and a selection reveals its links.
  const edgesButton = button(document, '显示全部连线', 'play-map-edges', () => {
    state.showAllEdges = !state.showAllEdges
    edgesButton.textContent = state.showAllEdges ? '只看选中界面的连线' : '显示全部连线'
    applyEdgeVisibility()
  })
  const zoomOut = button(document, '−', 'play-map-zoom', () => zoomBy(1 / 1.2))
  const zoomIn = button(document, '+', 'play-map-zoom', () => zoomBy(1.2))
  const closeButton = button(document, '关闭地图', 'play-map-close-map', () => {
    const next = `${location.pathname}${location.search}`
    history.replaceState(null, '', next)
    syncVisibility()
  })
  toolbar.append(title, search, mainButton, focusButton, fitButton, edgesButton, zoomOut, zoomIn, closeButton)
  container.appendChild(toolbar)

  const graphHost = document.createElement('div')
  graphHost.className = 'play-map-graph'
  graphHost.setAttribute('aria-label', '界面关系图')
  container.appendChild(graphHost)

  const detail = document.createElement('aside')
  detail.className = 'play-map-detail'
  detail.hidden = true
  container.appendChild(detail)

  function seek(payload) {
    if (typeof onSeek === 'function') onSeek(payload)
  }

  function transitionForObservation(observation) {
    const frame = frameOf(observation)
    if (!frame || !state.indexes) return null
    return state.indexes.transitions.find(item => {
      const evidence = item.evidence || {}
      return evidence.post_frame_id === (frame.id || frame.frame_id) ||
        (evidence.additional_post_frame_ids || []).includes(frame.id || frame.frame_id)
    }) || null
  }

  function replayObservation(observation) {
    const transition = transitionForObservation(observation)
    seek({ observation: frameOf(observation), transition })
  }

  function renderObservationLedger() {
    const existing = detail.querySelector('.play-map-observations')
    if (existing) existing.remove()
    const section = document.createElement('section')
    section.className = 'play-map-observations'
    const heading = document.createElement('h3')
    heading.textContent = '记录的画面'
    section.appendChild(heading)
    const page = state.observationPage
    if (!page) {
      const loading = document.createElement('p')
      loading.className = 'play-map-muted'
      loading.textContent = '正在加载画面记录…'
      section.appendChild(loading)
    } else if (!(page.items || []).length) {
      const empty = document.createElement('p')
      empty.className = 'play-map-muted'
      empty.textContent = page.error ? '画面记录暂时无法加载' : '暂无画面记录'
      section.appendChild(empty)
    } else {
      const cards = document.createElement('div')
      cards.className = 'play-map-observation-grid'
      page.items.forEach(item => {
        const frame = frameOf(item)
        if (!frame) return
        const card = document.createElement('article')
        card.className = 'play-map-observation'
        const thumb = button(document, '', 'play-map-observation-thumb', () => replayObservation(item))
        thumb.setAttribute('aria-label', `回放${entityLabel({ id: frame.screen_id, title: frame.screen_id })}画面`)
        renderScreenshot(document, thumb, frame, assetUrl)
        card.appendChild(thumb)
        const caption = document.createElement('p')
        caption.textContent = entityLabel({ id: frame.screen_id, title: frame.screen_id })
        card.appendChild(caption)
        cards.appendChild(card)
      })
      section.appendChild(cards)
    }
    if (page && page.total > state.observationLimit) {
      const pager = document.createElement('div')
      pager.className = 'play-map-pager'
      const previous = button(document, '上一页', 'play-map-page', () => loadObservationPage(state.observationOffset - state.observationLimit, state.observationEntity))
      previous.disabled = state.observationOffset <= 0
      const next = button(document, '下一页', 'play-map-page', () => loadObservationPage(state.observationOffset + state.observationLimit, state.observationEntity))
      next.disabled = state.observationOffset + state.observationLimit >= page.total
      const summary = document.createElement('span')
      const start = page.total ? state.observationOffset + 1 : 0
      const end = Math.min(state.observationOffset + state.observationLimit, page.total)
      summary.textContent = `${start}–${end} / ${page.total}`
      pager.append(previous, summary, next)
      section.appendChild(pager)
    }
    detail.appendChild(section)
  }

  function renderLatestObservation() {
    const loading = detail.querySelector('.play-map-loading')
    if (loading) loading.remove()
    const existing = detail.querySelector('.play-map-latest')
    if (existing) existing.remove()
    const page = state.observationPage
    const item = page && (page.items || []).length ? page.items[page.items.length - 1] : null
    const frame = frameOf(item)
    if (!frame) return
    const section = document.createElement('section')
    section.className = 'play-map-latest'
    const heading = document.createElement('h3')
    heading.textContent = '最近画面'
    section.appendChild(heading)
    const replay = button(document, '', 'play-map-latest-thumb', () => replayObservation(item))
    replay.setAttribute('aria-label', '回放最近画面')
    renderScreenshot(document, replay, frame, assetUrl)
    section.appendChild(replay)
    const caption = document.createElement('p')
    caption.className = 'play-map-muted'
    caption.textContent = '点击画面可回看'
    section.appendChild(caption)
    const ledger = detail.querySelector('.play-map-observations')
    detail.insertBefore(section, ledger || null)
  }

  async function loadObservationPage(offset, entityId = state.observationEntity, latest = false) {
    if (typeof fetchObservations !== 'function') return
    const request = ++state.observationRequest
    state.observationOffset = Math.max(0, offset)
    state.observationEntity = entityId || null
    state.observationPage = null
    renderObservationLedger()
    try {
      const page = await fetchObservations(state.observationOffset, state.observationLimit, state.observationEntity)
      if (request !== state.observationRequest) return
      if (latest && page && page.total > state.observationLimit && state.observationOffset === 0) {
        await loadObservationPage(page.total - state.observationLimit, state.observationEntity, false)
        return
      }
      state.observationPage = page || { items: [], total: 0 }
      renderLatestObservation()
      renderObservationLedger()
    } catch (_) {
      if (request !== state.observationRequest) return
      state.observationPage = { items: [], total: 0, error: true }
      renderLatestObservation()
      renderObservationLedger()
      const error = detail.querySelector('.play-map-observations .play-map-muted')
      if (error) error.textContent = '画面记录暂时无法加载'
    }
  }

  function showDetail(entityId) {
    const entity = state.indexes.entities.get(entityId)
    if (!entity) return
    state.selected = entityId
    detail.hidden = false
    clear(detail)
    const header = document.createElement('div')
    header.className = 'play-map-detail-header'
    header.append(
      document.createElement('h2'),
      button(document, '关闭', 'play-map-close', closeDetail),
    )
    header.firstChild.textContent = entityLabel(entity)
    detail.appendChild(header)

    const info = entity.catalog
    if (info) {
      const section = document.createElement('section')
      section.className = 'play-map-catalog'
      const category = entityCategory(entity, categoryIndex(state.graph))
      const tags = document.createElement('div')
      tags.className = 'play-map-catalog-tags'
      if (category) {
        const chip = document.createElement('span')
        chip.className = 'play-map-chip'
        chip.style.background = category.color
        chip.textContent = category.title
        tags.appendChild(chip)
      }
      if (text(info.activity)) {
        const activity = document.createElement('span')
        activity.className = 'play-map-chip play-map-chip-quiet'
        activity.textContent = `活动 · ${info.activity}`
        tags.appendChild(activity)
      }
      section.appendChild(tags)
      if (text(info.summary)) {
        const summary = document.createElement('p')
        summary.textContent = info.summary
        section.appendChild(summary)
      }
      if (text(info.handling)) {
        const heading = document.createElement('h3')
        heading.textContent = '自动游玩怎么处理'
        const handling = document.createElement('p')
        handling.textContent = info.handling
        section.append(heading, handling)
      }
      detail.appendChild(section)
    }

    const hint = document.createElement('p')
    hint.className = 'play-map-muted play-map-loading'
    hint.textContent = '正在查找最近画面…'
    detail.appendChild(hint)
    loadObservationPage(0, entityId, true)
    const related = navigableTransitions(state.indexes).filter(edge => edge.source_entity_id === entityId || edge.target_entity_id === entityId)
    const interactive = []
    const gaps = []
    const seen = new Set()
    related.forEach(transition => {
      const targetId = transition.source_entity_id === entityId ? transition.target_entity_id : transition.source_entity_id
      const key = `${targetId}|${transition.mode}`
      if (seen.has(key)) return
      seen.add(key)
      ;(transition.mode === 'observed-gap' ? gaps : interactive).push({ transition, targetId })
    })
    const pairs = Array.isArray(state.graph.summary_edges) ? state.graph.summary_edges : null
    if (pairs) {
      // Direction and frequency, from the server's one-edge-per-pair fold.
      const directions = [
        ['从这里去往', edge => edge.source_entity_id === entityId, edge => edge.target_entity_id],
        ['从哪里来', edge => edge.target_entity_id === entityId, edge => edge.source_entity_id],
      ]
      directions.forEach(([label, matches, otherId]) => {
        const edges = pairs
          .filter(edge => matches(edge) && edge.interactive_count > 0 && state.indexes.entities.has(otherId(edge)))
          .sort((a, b) => b.interactive_count - a.interactive_count)
        if (!edges.length) return
        const heading = document.createElement('h3')
        heading.textContent = `${label}（${edges.length}）`
        detail.appendChild(heading)
        const host = document.createElement('div')
        host.className = 'play-map-related'
        edges.forEach(edge => {
          const other = state.indexes.entities.get(otherId(edge))
          const trigger = (edge.top_triggers || [])[0]
          const hint = trigger && trigger.trigger ? ` · 常见触发：${friendlyIntent(trigger.trigger)}` : ''
          host.appendChild(button(document, `${entityLabel(other)} · 点击 ×${edge.interactive_count}${hint}`, 'play-map-edge', () => {
            select(other.id)
            const transition = edgeTransition(edge, state.indexes)
            if (transition) seek({ transition })
          }))
        })
        detail.appendChild(host)
      })
    }
    if (!pairs && interactive.length) {
      const heading = document.createElement('h3')
      heading.textContent = '相关界面'
      detail.appendChild(heading)
      const relatedHost = document.createElement('div')
      relatedHost.className = 'play-map-related'
      interactive.forEach(({ transition, targetId }) => {
        const target = state.indexes.entities.get(targetId)
        if (!target) return
        const edgeButton = button(document, `${entityLabel(target)} · ${friendlyTrigger(transition)}`, 'play-map-edge', () => {
          select(targetId)
          seek({ transition })
        })
        relatedHost.appendChild(edgeButton)
      })
      detail.appendChild(relatedHost)
    }
    if (gaps.length) {
      const review = document.createElement('details')
      review.className = 'play-map-gaps'
      const summary = document.createElement('summary')
      summary.textContent = '其他记录（过程未完整记录）'
      review.appendChild(summary)
      const gapHost = document.createElement('div')
      gapHost.className = 'play-map-related'
      gaps.forEach(({ transition, targetId }) => {
        const target = state.indexes.entities.get(targetId)
        if (!target) return
        const edgeButton = button(document, `${entityLabel(target)} · ${friendlyTrigger(transition)}`, 'play-map-edge', () => {
          select(targetId)
          seek({ transition })
        })
        gapHost.appendChild(edgeButton)
      })
      review.appendChild(gapHost)
      detail.appendChild(review)
    }
  }

  function closeDetail() {
    state.selected = null
    detail.hidden = true
    applyEdgeVisibility()
  }

  function select(entityId) {
    if (!state.indexes || !state.indexes.entities.has(entityId)) return
    showDetail(entityId)
    if (state.cy) state.cy.$id(entityId).select()
    applyEdgeVisibility()
  }

  function applyEdgeVisibility() {
    if (!state.cy) return
    state.cy.elements().removeClass('shown faded')
    if (state.showAllEdges) {
      state.cy.edges().addClass('shown')
      return
    }
    if (!state.selected) return
    const node = state.cy.$id(state.selected)
    if (!node.length) return
    const edges = node.connectedEdges()
    edges.addClass('shown')
    state.cy.nodes().difference(node.union(edges.connectedNodes())).addClass('faded')
  }

  function visibleEntities() {
    const query = state.query.toLocaleLowerCase()
    return (state.graph.entities || []).filter(entity => !query || entityLabel(entity).toLocaleLowerCase().includes(query))
  }

  function renderFallback() {
    clear(graphHost)
    const list = document.createElement('div')
    list.className = 'play-map-graph-list'
    const categories = categoryIndex(state.graph)
    let lastCategory
    visibleEntities()
      .slice()
      .sort((a, b) => {
        const left = entityCategory(a, categories)
        const right = entityCategory(b, categories)
        return (left ? left.order : categories.size) - (right ? right.order : categories.size)
      })
      .forEach(entity => {
        const category = entityCategory(entity, categories)
        if (categories.size && category && category.id !== lastCategory) {
          const heading = document.createElement('h3')
          heading.className = 'play-map-group'
          heading.textContent = category.title
          list.appendChild(heading)
          lastCategory = category.id
        }
        const item = button(document, entityLabel(entity), 'play-map-node', () => select(entity.id))
        item.dataset.entityId = entity.id
        list.appendChild(item)
      })
    interactiveTransitions(state.indexes).forEach(transition => {
      const source = state.indexes.entities.get(transition.source_entity_id)
      const target = state.indexes.entities.get(transition.target_entity_id)
      if (!source || !target || !visibleEntities().some(entity => entity.id === source.id)) return
      const edge = button(document, `${entityLabel(source)} → ${entityLabel(target)} · ${friendlyTrigger(transition)}`, 'play-map-edge', () => {
        select(target.id)
        seek({ transition })
      })
      list.appendChild(edge)
    })
    graphHost.appendChild(list)
  }

  function currentLayout() {
    return categoryIndex(state.graph || {}).size
      ? { name: 'preset', padding: 48 }
      : { name: 'grid', cols: 5, padding: 72, avoidOverlap: true, avoidOverlapPadding: 24, nodeDimensionsIncludeLabels: true }
  }

  function renderLegend() {
    const existing = container.querySelector('.play-map-legend')
    if (existing) existing.remove()
    const categories = categoryIndex(state.graph || {})
    if (!categories.size) return
    const legend = document.createElement('div')
    legend.className = 'play-map-legend'
    Array.from(categories.values()).forEach(category => {
      const count = (state.graph.entities || []).filter(entity => entity.catalog && entity.catalog.category === category.id).length
      if (!count) return
      const chip = document.createElement('span')
      chip.className = 'play-map-legend-item'
      chip.title = category.summary || ''
      const dot = document.createElement('i')
      dot.style.background = category.color
      chip.append(dot, document.createTextNode(`${category.title} ${count}`))
      legend.appendChild(chip)
    })
    container.insertBefore(legend, graphHost)
  }

  function renderGraph() {
    renderLegend()
    if (state.cy) {
      state.cy.destroy()
      state.cy = null
    }
    clear(graphHost)
    const cytoscape = document.defaultView && document.defaultView.cytoscape
    if (typeof cytoscape === 'function' && !state.query) {
      state.cy = cytoscape({
        container: graphHost,
        elements: cyElements(state.graph, state.indexes),
        style: [
          { selector: 'node', style: { label: 'data(label)', width: 156, height: 58, shape: 'round-rectangle', 'background-color': '#365875', color: '#e8edf3', 'text-wrap': 'wrap', 'text-max-width': 136, 'text-halign': 'center', 'text-valign': 'center', 'font-size': 12 } },
          { selector: 'node[color]', style: { 'background-color': 'data(color)' } },
          { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#eaaab0' } },
          { selector: 'edge', style: { label: '', width: 2, 'line-color': '#6b8eae', 'target-arrow-color': '#6b8eae', 'target-arrow-shape': 'triangle', color: '#c5d6e6', 'font-size': 12, 'text-background-color': '#171d24', 'text-background-opacity': .9, 'text-background-padding': 3 } },
          { selector: 'edge[width]', style: { display: 'none', width: 'data(width)', 'curve-style': 'bezier', opacity: .8 } },
          { selector: 'edge[width].shown', style: { display: 'element', label: 'data(label)', 'font-size': 10 } },
          { selector: 'node.faded', style: { opacity: .25 } },
          { selector: 'edge:selected', style: { label: 'data(label)', width: 3, 'line-color': '#eaaab0', 'target-arrow-color': '#eaaab0', opacity: 1 } },
        ],
        layout: currentLayout(),
      })
      state.cy.on('tap', 'node', event => select(event.target.id()))
      state.cy.on('tap', event => { if (event.target === state.cy) closeDetail() })
      state.cy.on('tap', 'edge', event => {
        const summary = surfaceEdges(state.graph, state.indexes).find(item => item.id === event.target.id())
        const transition = summary
          ? edgeTransition(summary, state.indexes)
          : state.indexes.transitions.find(item => item.id === event.target.id())
        const targetId = summary ? summary.target_entity_id : transition && transition.target_entity_id
        if (targetId) select(targetId)
        if (transition) seek({ transition })
      })
      applyEdgeVisibility()
      resizeGraph()
      return
    }
    renderFallback()
  }

  search.addEventListener('input', () => {
    state.query = search.value.trim()
    if (state.graph) renderGraph()
  })

  function resizeGraph() {
    if (!state.cy) return
    requestAnimationFrame(() => {
      if (!state.cy) return
      state.cy.resize()
      state.cy.layout(currentLayout()).run()
      fitAll()
    })
  }

  function focusMain() {
    if (!state.cy) return
    let focus = state.cy.$id(MAIN_ENTITY_ID)
    if (!focus.length) focus = state.cy.nodes().first()
    if (!focus.length) return
    focus = focus.union(focus.neighborhood())
    state.cy.fit(focus, 80)
    state.cy.center(focus)
  }

  function fitAll() {
    if (!state.cy) return
    state.cy.fit(state.cy.elements(), 48)
  }

  function zoomBy(factor) {
    if (!state.cy) return
    const next = Math.min(state.cy.maxZoom(), Math.max(state.cy.minZoom(), state.cy.zoom() * factor))
    state.cy.zoom(next)
  }

  function syncVisibility() {
    const open = location.hash === '#play-map'
    container.classList.toggle('is-open', open)
    if (open) resizeGraph()
  }
  window.addEventListener('hashchange', syncVisibility)
  syncVisibility()

  const onKeyDown = event => {
    if (event.key === 'Escape' && !detail.hidden) closeDetail()
  }
  document.addEventListener('keydown', onKeyDown)

  async function refresh() {
    if (typeof fetchGraph !== 'function') throw new TypeError('fetchGraph is required')
    const payload = await fetchGraph()
    state.graph = graphFromPayload(payload)
    state.indexes = mapIndexes(state.graph)
    renderGraph()
    if (state.selected && state.indexes.entities.has(state.selected)) showDetail(state.selected)
    return state.graph
  }

  function destroy() {
    if (state.cy) state.cy.destroy()
    document.removeEventListener('keydown', onKeyDown)
    window.removeEventListener('hashchange', syncVisibility)
    clear(container)
  }

  const api = { refresh, destroy, select, showEntity: select, closeDetail, loadObservationPage }
  if (typeof fetchGraph === 'function') refresh().catch(error => {
    graphHost.textContent = '界面地图暂时无法加载'
    container.dispatchEvent(new CustomEvent('play-map-error', { detail: error }))
  })
  return api
}

export default mountMap

// Pure helpers, exported for node tests only.
export { cyElements, entityLabel, friendlyIntent, surfaceEdges }
