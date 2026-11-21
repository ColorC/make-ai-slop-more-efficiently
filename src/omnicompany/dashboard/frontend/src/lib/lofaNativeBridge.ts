export const LOFA_BRIDGE_PROTOCOL = 'omni:lofa-native-bridge/v1'
export const LOFA_BOOTSTRAP_REQUEST = 'omni:lofa-bootstrap-request'
export const LOFA_BOOTSTRAP_RESPONSE = 'omni:lofa-bootstrap-response'
export const LOFA_NATIVE_REQUEST = 'omni:lofa-native-request'
export const LOFA_NATIVE_RESULT = 'omni:lofa-native-result'
export const LOFA_MIN_NATIVE_BRIDGE_VERSION = 1
export const DASHBOARD_API_SCHEMA_VERSION = 1

const EXTERNAL_WEBVIEW_CAPABILITIES = [
  'browser.webview.open',
  'browser.webview.update',
  'browser.webview.reload',
  'browser.webview.close',
] as const

const DASHBOARD_FEATURES = [
  'dashboard.entities',
  'dashboard.provider-registry',
  'dashboard.pty',
  'dashboard.project-board',
  'dashboard.review',
  // 驾驶舱自己的菜单里已经有「重载 / LOFA 设置」两项 —— 壳看到这个特性就撤掉
  // 它那两颗盖在页面右上角的悬浮按钮(手机上会挡住页签和工具行)。
  'shell.hosts-lofa-controls',
] as const

export type LofaBridgeStatus = 'inactive' | 'connecting' | 'ready' | 'outdated' | 'unavailable'

export interface LofaBridgeState {
  status: LofaBridgeStatus
  nativeBridgeVersion: number | null
  appVersion: string
  capabilities: readonly string[]
}

interface BootstrapResponse {
  type: typeof LOFA_BOOTSTRAP_RESPONSE
  protocol: typeof LOFA_BRIDGE_PROTOCOL
  request_id: string
  native_bridge_version: number
  app_version?: string
  capabilities?: unknown
}

interface NativeResult {
  type: typeof LOFA_NATIVE_RESULT
  protocol: typeof LOFA_BRIDGE_PROTOCOL
  request_id: string
  ok: boolean
  result?: unknown
  error?: string
}

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timeout: number
}

const EMPTY_STATE: LofaBridgeState = {
  status: 'inactive',
  nativeBridgeVersion: null,
  appVersion: '',
  capabilities: [],
}

let state: LofaBridgeState = EMPTY_STATE
let parentWindow: Window | null = null
let requestSequence = 0
const pending = new Map<string, PendingRequest>()

function isLofaRemote(win: Window): boolean {
  const params = new URL(win.location.href).searchParams
  return params.get('lofaRemoteWeb') === '1' && params.get('client') === 'lofa'
}

function requestId(prefix: string): string {
  requestSequence += 1
  return `${prefix}-${Date.now().toString(36)}-${requestSequence.toString(36)}`
}

function setState(win: Window, next: LofaBridgeState): void {
  state = next
  win.document.documentElement.dataset.lofaBridgeStatus = next.status
  win.dispatchEvent(new CustomEvent('omnicompany:lofa-bridge-state', { detail: next }))
}

function removeCompatibilityNotice(doc: Document): void {
  doc.getElementById('lofa-native-compatibility-notice')?.remove()
}

function renderCompatibilityNotice(win: Window, status: 'outdated' | 'unavailable'): void {
  removeCompatibilityNotice(win.document)
  const notice = win.document.createElement('aside')
  notice.id = 'lofa-native-compatibility-notice'
  notice.setAttribute('role', 'status')
  notice.style.cssText = [
    'position:fixed', 'z-index:2147483000', 'left:12px', 'right:12px',
    'top:max(12px,env(safe-area-inset-top))', 'padding:10px 12px',
    'border:1px solid rgba(255,180,80,.55)', 'border-radius:12px',
    'background:#211a0ff2', 'color:#fff4df', 'font:14px/1.4 system-ui,sans-serif',
    'box-shadow:0 8px 28px rgba(0,0,0,.28)',
  ].join(';')
  const text = win.document.createElement('span')
  text.textContent = status === 'outdated'
    ? 'LOFA 原生桥版本过旧；网页业务仍可用，原生功能已降级。请更新 APK。'
    : 'LOFA 原生桥未响应；网页业务仍可用。请点壳上的 LOFA 入口检查 APK 更新。'
  notice.appendChild(text)
  if (status === 'outdated' && state.capabilities.includes('shell.open-settings')) {
    const button = win.document.createElement('button')
    button.type = 'button'
    button.textContent = '打开 LOFA 设置'
    button.style.cssText = 'margin-left:10px;padding:5px 9px;border-radius:8px;border:1px solid currentColor;background:transparent;color:inherit'
    button.addEventListener('click', () => { void requestLofaNative('shell.open-settings') })
    notice.appendChild(button)
  }
  win.document.body.appendChild(notice)
}

async function dashboardBuild(win: Window): Promise<string> {
  try {
    const response = await win.fetch('/api/dev/versions', { cache: 'no-store' })
    if (!response.ok) return 'unknown'
    const payload = await response.json() as { ui?: unknown }
    const value = String(payload.ui ?? '').trim()
    return value || 'unknown'
  } catch {
    return 'unknown'
  }
}

function normalizeCapabilities(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return [...new Set(value.filter((item): item is string => typeof item === 'string' && item.length <= 80))].sort()
}

function installLofaHostBridge(win: Window): void {
  if (!EXTERNAL_WEBVIEW_CAPABILITIES.every((capability) => state.capabilities.includes(capability))) return
  const pixelRatio = () => Number.isFinite(win.devicePixelRatio) && win.devicePixelRatio > 0
    ? win.devicePixelRatio
    : 1
  win.__OMNICOMPANY_HOST__ = {
    runtime: 'lofa',
    openSystemUrl: async (url) => {
      await requestLofaNative('browser.open-external', { url })
    },
    openExternalWebview: async (request) => {
      const result = await requestLofaNative('browser.webview.open', {
        ...request,
        pixelRatio: pixelRatio(),
      }) as { label?: unknown } | null
      return String(result?.label || request.id)
    },
    updateExternalWebview: async (request) => {
      await requestLofaNative('browser.webview.update', {
        ...request,
        pixelRatio: pixelRatio(),
      })
    },
    reloadExternalWebview: async (id) => {
      await requestLofaNative('browser.webview.reload', { id })
    },
    closeExternalWebview: async (id) => {
      await requestLofaNative('browser.webview.close', { id })
    },
  }
}

export function getLofaBridgeState(): LofaBridgeState {
  return state
}

export function requestLofaNative(action: string, args: Record<string, unknown> = {}): Promise<unknown> {
  if (!parentWindow || state.status !== 'ready' && state.status !== 'outdated') {
    return Promise.reject(new Error('LOFA native bridge is unavailable'))
  }
  if (!state.capabilities.includes(action)) {
    return Promise.reject(new Error(`LOFA native capability is unavailable: ${action}`))
  }
  const id = requestId('native')
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      pending.delete(id)
      reject(new Error(`LOFA native request timed out: ${action}`))
    }, 8000)
    pending.set(id, { resolve, reject, timeout })
    parentWindow!.postMessage({
      type: LOFA_NATIVE_REQUEST,
      protocol: LOFA_BRIDGE_PROTOCOL,
      request_id: id,
      action,
      args,
    }, '*')
  })
}

/**
 * Install the narrow Dashboard -> LOFA native bridge. The parent window is the
 * sole trusted peer; actions are rejected client-side unless the shell declared
 * the exact capability during bootstrap. There is deliberately no eval/generic
 * plugin invocation escape hatch.
 */
export function installLofaNativeBridge(win: Window = window, timeoutMs = 5000): () => void {
  if (!isLofaRemote(win)) {
    state = EMPTY_STATE
    return () => undefined
  }

  parentWindow = win.parent
  const previousHost = win.__OMNICOMPANY_HOST__
  const bootstrapRequestId = requestId('bootstrap')
  setState(win, { ...EMPTY_STATE, status: 'connecting' })

  const bootstrapTimeout = win.setTimeout(() => {
    setState(win, { ...EMPTY_STATE, status: 'unavailable' })
    renderCompatibilityNotice(win, 'unavailable')
  }, timeoutMs)

  const onMessage = (event: MessageEvent) => {
    if (event.source !== win.parent) return
    const data = event.data as (Partial<BootstrapResponse> | Partial<NativeResult>) | null
    if (!data || data.protocol !== LOFA_BRIDGE_PROTOCOL || typeof data.request_id !== 'string') return

    if (data.type === LOFA_BOOTSTRAP_RESPONSE && data.request_id === bootstrapRequestId) {
      win.clearTimeout(bootstrapTimeout)
      const version = Number(data.native_bridge_version)
      const capabilities = normalizeCapabilities(data.capabilities)
      const status: LofaBridgeStatus = Number.isFinite(version) && version >= LOFA_MIN_NATIVE_BRIDGE_VERSION
        ? 'ready'
        : 'outdated'
      state = {
        status,
        nativeBridgeVersion: Number.isFinite(version) ? version : 0,
        appVersion: String(data.app_version ?? ''),
        capabilities,
      }
      installLofaHostBridge(win)
      setState(win, state)
      if (status === 'outdated') renderCompatibilityNotice(win, status)
      else removeCompatibilityNotice(win.document)
      return
    }

    if (data.type === LOFA_NATIVE_RESULT) {
      const request = pending.get(data.request_id)
      if (!request) return
      pending.delete(data.request_id)
      win.clearTimeout(request.timeout)
      if (data.ok) request.resolve(data.result)
      else request.reject(new Error(String(data.error || 'LOFA native request failed')))
    }
  }
  win.addEventListener('message', onMessage)

  void dashboardBuild(win).then((frontendBuild) => {
    if (parentWindow !== win.parent) return
    win.parent.postMessage({
      type: LOFA_BOOTSTRAP_REQUEST,
      protocol: LOFA_BRIDGE_PROTOCOL,
      request_id: bootstrapRequestId,
      bootstrap: {
        frontend_build: frontendBuild,
        api_schema_version: DASHBOARD_API_SCHEMA_VERSION,
        min_native_bridge_version: LOFA_MIN_NATIVE_BRIDGE_VERSION,
        features: [...DASHBOARD_FEATURES],
      },
    }, '*')
  })

  return () => {
    win.clearTimeout(bootstrapTimeout)
    win.removeEventListener('message', onMessage)
    removeCompatibilityNotice(win.document)
    for (const [id, request] of pending) {
      win.clearTimeout(request.timeout)
      request.reject(new Error('LOFA native bridge was disposed'))
      pending.delete(id)
    }
    parentWindow = null
    state = EMPTY_STATE
    if (win.__OMNICOMPANY_HOST__?.runtime === 'lofa') {
      if (previousHost) win.__OMNICOMPANY_HOST__ = previousHost
      else delete win.__OMNICOMPANY_HOST__
    }
    delete win.document.documentElement.dataset.lofaBridgeStatus
  }
}
