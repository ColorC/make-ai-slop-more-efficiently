const pageProbe = () => {
  const qs = (selector) => Array.from(document.querySelectorAll(selector))
  const visible = (element) => !element.closest('[hidden]') && element.getAttribute('aria-hidden') !== 'true'
  const name = (element) => {
    const labelled = element.getAttribute('aria-labelledby')
    const byId = labelled ? labelled.split(/\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ') : ''
    const wrapping = element.closest('label')?.textContent || ''
    const explicit = element.id ? document.querySelector(`label[for="${element.id}"]`)?.textContent || '' : ''
    return (element.getAttribute('aria-label') || element.getAttribute('title') || byId || explicit || wrapping || element.textContent || element.getAttribute('placeholder') || '').trim()
  }
  const controls = qs('button,a[href],input,select,textarea').filter(visible)
  const headings = qs('h1,h2,h3,h4,h5,h6').filter(visible).map((element) => ({
    level: Number(element.tagName.slice(1)),
    text: name(element),
  }))
  const headingOrderValid = headings.length > 0 && headings[0].level === 1 && headings.every((item, index) => (
    index === 0 || item.level <= headings[index - 1].level + 1
  ))
  const ids = qs('[id]').filter(visible).map((element) => element.id)
  return {
    lang: document.documentElement.getAttribute('lang'),
    title: document.title,
    url: location.href,
    landmarks: {
      main: qs('main').filter(visible).length,
      nav: qs('nav').filter(visible).length,
      header: qs('header').filter(visible).length,
      footer: qs('footer').filter(visible).length,
    },
    headings,
    heading_order_valid: headingOrderValid,
    controls_without_name: controls.filter((element) => !name(element)).map((element) => element.tagName),
    images_without_alt: qs('img').filter(visible).filter((element) => !element.hasAttribute('alt')).length,
    duplicate_ids: ids.filter((id, index) => ids.indexOf(id) !== index),
    positive_tabindex: qs('[tabindex]').filter(visible).filter((element) => Number(element.getAttribute('tabindex')) > 0).length,
    skip_target: Boolean(document.querySelector(document.querySelector('.skip-link')?.getAttribute('href') || '#missing')),
    game_cards: qs('[data-game]').filter(visible).length,
    play_cards: qs('[data-report]').filter(visible).length,
    has_play_design: document.body.textContent.includes('玩法设计文档'),
    has_interfaces_and_routes: document.body.textContent.includes('界面与交互路线'),
    has_screen_tags: document.body.textContent.includes('界面标签'),
    has_play_records: document.body.textContent.includes('玩法游玩记录'),
    has_community_feedback: document.body.textContent.includes('玩法社群与媒体反馈'),
    has_play_tags: document.body.textContent.includes('玩法标签'),
    has_demo_reproduction: document.body.textContent.includes('玩法 Demo 复现'),
  }
}

export async function collectPublicSiteBrowserEvidence(tab, baseUrl) {
  const homeUrl = `${baseUrl.replace(/\/$/, '')}/game-observatory/`
  const navigationSamples = []
  for (let index = 0; index < 5; index += 1) {
    const started = Date.now()
    await tab.goto(homeUrl)
    await tab.playwright.getByRole('heading', { name: '从游戏进入系统', exact: true }).waitFor({ state: 'visible' })
    navigationSamples.push(Date.now() - started)
  }
  const home = await tab.playwright.evaluate(pageProbe)
  let game = null
  let play = null
  const games = tab.playwright.locator('[data-game]')
  if (await games.count()) {
    await games.first().click()
    await tab.playwright.getByRole('heading', { name: '按系统深入理解玩法', exact: true }).waitFor({ state: 'visible' })
    game = await tab.playwright.evaluate(pageProbe)
    const plays = tab.playwright.locator('[data-report]')
    if (await plays.count()) {
      await plays.first().click()
      await tab.playwright.getByRole('heading', { name: '玩法设计文档', exact: true }).waitFor({ state: 'visible' })
      play = await tab.playwright.evaluate(pageProbe)
    }
  }
  return {
    schema: 'game-observatory.browser-quality-evidence.v2',
    captured_at: new Date().toISOString(),
    base_url: baseUrl,
    navigation_samples_ms: navigationSamples,
    home,
    game,
    play,
  }
}
