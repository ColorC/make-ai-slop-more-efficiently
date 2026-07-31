function headingOrder(headings) {
  if (!headings.length || headings[0].level !== 1) return false
  for (let index = 1; index < headings.length; index += 1) {
    if (headings[index].level > headings[index - 1].level + 1) return false
  }
  return true
}

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
    heading_order_valid: headingOrder(headings),
    controls_without_name: controls.filter((element) => !name(element)).map((element) => element.tagName),
    images_without_alt: qs('img').filter(visible).filter((element) => !element.hasAttribute('alt')).length,
    duplicate_ids: ids.filter((id, index) => ids.indexOf(id) !== index),
    positive_tabindex: qs('[tabindex]').filter(visible).filter((element) => Number(element.getAttribute('tabindex')) > 0).length,
    skip_target: Boolean(document.querySelector(document.querySelector('.skip-link')?.getAttribute('href') || '#missing')),
    has_surfaces: document.body.textContent.includes('页面与 UI'),
    has_journey: document.body.textContent.includes('玩家旅程'),
    has_mechanisms: document.body.textContent.includes('机制表达'),
    has_resources: document.body.textContent.includes('资源关系'),
    has_sources: document.body.textContent.includes('来源'),
  }
}

export async function collectPublicSiteBrowserEvidence(tab, baseUrl) {
  const homeUrl = `${baseUrl.replace(/\/$/, '')}/game-observatory/`
  const navigationSamples = []
  for (let index = 0; index < 5; index += 1) {
    const started = Date.now()
    await tab.goto(homeUrl)
    await tab.playwright.getByText('4 份公开档案').waitFor({ state: 'visible' })
    navigationSamples.push(Date.now() - started)
  }
  const home = await tab.playwright.evaluate(pageProbe)
  await tab.playwright.getByRole('button', { name: '打开 英雄升级：逐英雄反馈与共鸣资源压力' }).click()
  await tab.playwright.getByRole('heading', { name: '页面与 UI' }).waitFor({ state: 'visible' })
  const report = await tab.playwright.evaluate(pageProbe)
  return {
    schema: 'game-observatory.browser-quality-evidence.v1',
    captured_at: new Date().toISOString(),
    base_url: baseUrl,
    navigation_samples_ms: navigationSamples,
    home,
    report,
  }
}