type UserAgentData = {
  toJSON?: () => Record<string, unknown>
  getHighEntropyValues?: (hints: string[]) => Promise<Record<string, unknown>>
}

type NavigatorWithHints = Navigator & {
  userAgentData?: UserAgentData
  deviceMemory?: number
}

function basePayload() {
  const nav = navigator as NavigatorWithHints
  const userAgentData = nav.userAgentData?.toJSON?.()
  return {
    source: 'dashboard-frontend',
    url: window.location.href,
    route: `${window.location.pathname}${window.location.search}${window.location.hash}`,
    referrer: document.referrer || null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || null,
    browser: {
      userAgent: nav.userAgent,
      platform: nav.platform,
      vendor: nav.vendor,
      language: nav.language,
      languages: Array.from(nav.languages || []),
      cookieEnabled: nav.cookieEnabled,
      hardwareConcurrency: nav.hardwareConcurrency,
      maxTouchPoints: nav.maxTouchPoints,
      deviceMemory: nav.deviceMemory,
    },
    screen: {
      width: window.screen?.width,
      height: window.screen?.height,
      availWidth: window.screen?.availWidth,
      availHeight: window.screen?.availHeight,
      colorDepth: window.screen?.colorDepth,
      pixelDepth: window.screen?.pixelDepth,
      devicePixelRatio: window.devicePixelRatio,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
    },
    userAgentData,
  }
}

function post(payload: Record<string, unknown>) {
  const body = JSON.stringify(payload)
  if (navigator.sendBeacon) {
    const blob = new Blob([body], { type: 'application/json' })
    if (navigator.sendBeacon('/api/lan-access/register', blob)) return
  }
  fetch('/api/lan-access/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {
    // Best effort only; page startup should never depend on enrollment telemetry.
  })
}

export function registerLanAccessVisitor() {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return
  const payload = basePayload()
  post(payload)
  const nav = navigator as NavigatorWithHints
  nav.userAgentData?.getHighEntropyValues?.([
    'architecture',
    'bitness',
    'model',
    'platform',
    'platformVersion',
    'uaFullVersion',
    'fullVersionList',
    'wow64',
  ]).then((values) => {
    post({
      ...payload,
      source: 'dashboard-frontend-high-entropy',
      userAgentData: { ...payload.userAgentData, ...values },
    })
  }).catch(() => {
    // Optional Client Hints are not available in every browser/context.
  })
}
