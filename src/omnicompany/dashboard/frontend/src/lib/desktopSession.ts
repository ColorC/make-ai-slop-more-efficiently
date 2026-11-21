const DESKTOP_INSTANCE_KEY = 'omnicompany.desktop.instance.v1'
const INSTANCE_ID_PATTERN = /^[a-f0-9]{32}$/
const SESSION_TOKEN_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/
const SESSION_RENEWAL_MARGIN_SECONDS = 60
const WORKSPACE_GATEWAY_HOSTS = new Set([
  '127.0.0.1',
  'localhost',
  '::1',
  '10.0.2.2',
])

export interface DesktopWebviewSession {
  token: string
  expiresAt: number
}

let cachedWebviewSession: DesktopWebviewSession | null = null

function randomInstanceId(): string {
  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}

export function desktopInstanceId(storage: Storage): string {
  try {
    const existing = (storage.getItem(DESKTOP_INSTANCE_KEY) ?? '').trim().toLowerCase()
    if (INSTANCE_ID_PATTERN.test(existing)) return existing
    const created = randomInstanceId()
    storage.setItem(DESKTOP_INSTANCE_KEY, created)
    return created
  } catch {
    return randomInstanceId()
  }
}

function webviewSessionFromResponse(value: unknown): DesktopWebviewSession | null {
  if (!value || typeof value !== 'object') return null
  const response = value as { webview_session?: unknown }
  if (!response.webview_session || typeof response.webview_session !== 'object') return null
  const session = response.webview_session as { token?: unknown; expires_at?: unknown }
  const token = typeof session.token === 'string' ? session.token : ''
  const expiresAt = Number(session.expires_at)
  if (!SESSION_TOKEN_PATTERN.test(token) || !Number.isFinite(expiresAt)) return null
  if (expiresAt <= Date.now() / 1000 + SESSION_RENEWAL_MARGIN_SECONDS) return null
  return { token, expiresAt }
}

/** Convert the current Caddy login into a WebView-profile cookie that survives updates. */
export async function ensureDesktopSession(
  request: typeof fetch = globalThis.fetch.bind(globalThis),
  storage: Storage = window.localStorage,
): Promise<boolean> {
  const response = await request('/api/desktop/session', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-Omnicompany-Desktop': '1',
    },
    body: JSON.stringify({ instance_id: desktopInstanceId(storage) }),
  })
  if (!response.ok) return false
  const payload = await response.json().catch(() => null)
  const webviewSession = webviewSessionFromResponse(payload)
  cachedWebviewSession = webviewSession
  return true
}

/**
 * Gets a short-lived bearer that the trusted desktop host turns into an
 * HttpOnly gateway cookie. It stays in module memory and is never persisted by
 * the page; the native child WebView owns the resulting cookie.
 */
export async function desktopWebviewSession(
  request: typeof fetch = globalThis.fetch.bind(globalThis),
  storage: Storage = window.localStorage,
): Promise<DesktopWebviewSession | null> {
  if (
    cachedWebviewSession
    && cachedWebviewSession.expiresAt > Date.now() / 1000 + SESSION_RENEWAL_MARGIN_SECONDS
  ) {
    return cachedWebviewSession
  }
  await ensureDesktopSession(request, storage)
  return cachedWebviewSession
}

/** Never request or forward a native bearer for an arbitrary external page. */
export async function desktopWebviewSessionForUrl(
  url: string,
  request: typeof fetch = globalThis.fetch.bind(globalThis),
  storage: Storage = window.localStorage,
): Promise<DesktopWebviewSession | null> {
  if (!isWorkspaceGatewayUrl(url)) return null
  return desktopWebviewSession(request, storage)
}

export function isWorkspaceGatewayUrl(url: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  return ['http:', 'https:'].includes(parsed.protocol)
    && WORKSPACE_GATEWAY_HOSTS.has(parsed.hostname.toLowerCase())
}
