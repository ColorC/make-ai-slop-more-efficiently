import type { EntityRef } from '../entities/types'
import { usePanels, type DockPlacement } from '../stores/panelsStore'
import {
  normalizeWebTabUrl,
  openWebTab,
  type WebProfile,
} from './webTabHost'

export const DESKTOP_WINDOW_STATE_EVENT = 'omnicompany:desktop-window-state'
/** Pushed by the desktop host whenever an embedded native webview lands on a new URL. */
export const EXTERNAL_WEBVIEW_URL_EVENT = 'omnicompany:external-webview-url'

export type WorkspaceTarget =
  | {
      kind: 'entity'
      ref: EntityRef
      title: string
      facet?: string
    }
  | {
      kind: 'web'
      url: string
      title?: string
      profile?: WebProfile
    }

export interface WorkspaceNavigationOptions {
  /** Defaults to the currently active workspace tab. Pass null to suppress return navigation. */
  sourceTabId?: string | null
  placement?: DockPlacement
  background?: boolean
  baseHref?: string
}

export interface OmnicompanyHostBridge {
  runtime: 'desktop' | 'lofa'
  openSystemUrl: (url: string) => void | Promise<void>
  logClientEvent?: (level: 'debug' | 'info' | 'warn' | 'error', event: string, detail?: string) => void
  /**
   * 2026-08-18 持久桌面会话: 页面把网关 mint 出的 webview_session token
   * (10 年期)交给宿主持久保存; 宿主启动时直接注入回 WebView cookie jar,
   * 让桌面端不再依赖任何一次交互式网关登录。
   */
  storeDesktopSession?: (token: string, expiresAt: number) => Promise<void>
  hostInfo?: () => Promise<DesktopHostInfo>
  /** Native WebView2 PNG returned through the trusted inspect channel. */
  captureScreenshot?: () => Promise<string>
  openExternalWebview?: (request: OpenExternalWebviewRequest) => Promise<string>
  updateExternalWebview?: (request: UpdateExternalWebviewRequest) => Promise<void>
  reloadExternalWebview?: (id: string) => Promise<void>
  closeExternalWebview?: (id: string) => Promise<void>
  /** Editable address bar: navigate an open native webview to a new URL (host validates). */
  navigateExternalWebview?: (id: string, url: string) => Promise<void>
  selectNodeRoot?: () => Promise<string | null>
  nodeModes?: { readonly: RemoteNodeMode; exec: RemoteNodeMode }
  inspectNodeRoot?: (root: string, mode?: RemoteNodeMode) => Promise<RemoteNodeRootInspection>
  nodeStatus?: (mode?: RemoteNodeMode) => Promise<RemoteNodeStatus>
  saveNodeGrant?: (request: SaveRemoteNodeGrantRequest) => Promise<RemoteNodeStatus>
  selfTestAndEnableNode?: (mode?: RemoteNodeMode) => Promise<RemoteNodeSelfTestReport>
  quickEnableNode?: (mode?: RemoteNodeMode) => Promise<RemoteNodeSelfTestReport>
  setNodePaused?: (paused: boolean, mode?: RemoteNodeMode) => Promise<RemoteNodeStatus>
  revokeNode?: (mode?: RemoteNodeMode) => Promise<RemoteNodeStatus>
  transportStatus?: () => Promise<RemoteNodeTransportStatus>
  pairNode?: (request: PairRemoteNodeRequest) => Promise<RemoteNodeTransportStatus>
  pairTrustedServer?: (request: PairRemoteNodeRequest) => Promise<RemoteNodeTransportStatus>
  /**
   * Signs a request body asking the controller to open a pairing window for
   * this node. Only a client that already holds the node's key can produce it,
   * which is what lets the one-click flow run from a remote desktop instead of
   * only from the controller machine.
   */
  signPairingOpen?: (mode?: RemoteNodeMode) => Promise<SignedPairingOpen>
  unpairNode?: () => Promise<RemoteNodeTransportStatus>
  configureServer?: () => Promise<void>
  windowState?: () => Promise<DesktopWindowState>
  toggleFullscreen?: () => Promise<boolean>
  checkDesktopUpdate?: (version?: string | null) => Promise<DesktopUpdateStatus>
  applyDesktopUpdate?: (version?: string | null) => Promise<DesktopUpdateAction>
  /** 远程剪切板同步落地端：把工作站剪贴板文本写入本机系统剪贴板（只写不读）。0.1.29+ 提供。 */
  writeClipboard?: (text: string) => Promise<void>
}

export interface DesktopHostInfo {
  runtime: 'desktop'
  version: string
  /** Explicit native self-report; absent on older desktop builds. */
  hostname?: string
  installId?: string
  connectionMethod?: string
  dashboardUrl: string
  serverUrl: string
  webviewEngine: 'WebView2'
  externalWebviewsIsolated: boolean
}

export interface DesktopWindowState {
  fullscreen: boolean
  maximized: boolean
  titlebarHeight: number
  fullscreenGuardHotEdge: number
}

export interface DesktopUpdateStatus {
  currentVersion: string
  available: boolean
  version: string | null
  notes: string | null
  publishedAt: string | null
  rollback: boolean
  signatureEnforced: boolean
  automatic: boolean
}

export interface DesktopUpdateAction {
  started: boolean
  reason: 'no_update' | 'cancelled' | 'installer_started'
  version: string | null
}

export interface NativeWebviewBounds {
  x: number
  y: number
  width: number
  height: number
}

export interface OpenExternalWebviewRequest {
  id: string
  url: string
  profile: Exclude<WebProfile, 'system'>
  bounds: NativeWebviewBounds
  visible: boolean
  /** Short-lived server-signed session. The native host converts it to HttpOnly. */
  webviewSession?: {
    token: string
    expiresAt: number
  }
}

export interface UpdateExternalWebviewRequest {
  id: string
  bounds: NativeWebviewBounds
  visible: boolean
  focus?: boolean
}

/** Read-only structured adapters, or arbitrary execution. Separate grants. */
export type RemoteNodeMode = 'readonly-l1' | 'trusted-exec-l2'

export interface RemoteNodeResourceLimits {
  timeoutMs: number
  maxMemoryMb: number
  maxOutputBytes: number
  maxProcesses: number
  cpuRatePercent: number
}

export interface RemoteNodeTool {
  programId: string
  displayName: string
  path: string
  sha256: string
  operations: string[]
}

export interface RemoteNodeCheck {
  id: string
  passed: boolean
  message: string
}

export interface RemoteNodeRootInspection {
  safe: boolean
  canonicalPath: string
  filesystem: string
  volumeSerial: number
  freeBytes: number
  reparsePoints: string[]
  projectTypes: string[]
  tools: RemoteNodeTool[]
  checks: RemoteNodeCheck[]
}

export interface SaveRemoteNodeGrantRequest {
  root: string
  approvedPrograms: string[]
  limits: RemoteNodeResourceLimits
  /** Omitted means read-only; execution is never granted implicitly. */
  mode?: RemoteNodeMode
}

export interface RemoteNodeStatus {
  configured: boolean
  grantId: string | null
  generation: number | null
  root: string | null
  tools: RemoteNodeTool[]
  limits: RemoteNodeResourceLimits
  mode: RemoteNodeMode
  /** `deny` for read-only; `inherit` for execution, which cannot promise otherwise. */
  networkPolicy: 'deny' | 'inherit'
  enabled: boolean
  paused: boolean
  selfTestedAtMs: number | null
  activeTasks: string[]
  auditPath: string
}

export interface RemoteNodeSelfTestCheck extends RemoteNodeCheck {
  durationMs: number | null
}

export interface RemoteNodeSelfTestReport {
  passed: boolean
  enabled: boolean
  checks: RemoteNodeSelfTestCheck[]
}

export interface PairRemoteNodeRequest {
  controllerUrl: string
  pairingId: string
  code: string
  label: string
  mode?: RemoteNodeMode
}

export interface SignedPairingOpen {
  /** Post verbatim; the signature covers a digest of exactly these fields. */
  envelope: Record<string, unknown>
  nodeId: string
}

export interface RemoteNodeTransportStatus {
  nodeId: string
  nodeFingerprint: string
  paired: boolean
  revoked: boolean
  controllerUrl: string | null
  controllerFingerprint: string | null
  label: string | null
  connected: boolean
  lastPollAtMs: number | null
  lastHeartbeatLatencyMs: number | null
  lastQueueLatencyMs: number | null
  pendingReceipts: number
  lastError: string | null
}

declare global {
  interface Window {
    __OMNICOMPANY_HOST__?: OmnicompanyHostBridge
  }
}

export function desktopHostBridge(): OmnicompanyHostBridge | null {
  if (typeof window === 'undefined') return null
  const bridge = window.__OMNICOMPANY_HOST__
  return bridge?.runtime === 'desktop' ? bridge : null
}

/** Native page carrier shared by Desktop and the LOFA Android shell. */
export function nativeWebviewHostBridge(): OmnicompanyHostBridge | null {
  if (typeof window === 'undefined') return null
  const bridge = window.__OMNICOMPANY_HOST__
  return bridge?.openExternalWebview && bridge.updateExternalWebview && bridge.closeExternalWebview
    ? bridge
    : null
}

/** Stable, Rust-safe label component for a native child WebView. */
export function nativeWebviewId(value: string): string {
  let forward = 0x811c9dc5
  let reverse = 0x9e3779b9
  for (let index = 0; index < value.length; index += 1) {
    forward ^= value.charCodeAt(index)
    forward = Math.imul(forward, 0x01000193)
    reverse ^= value.charCodeAt(value.length - index - 1)
    reverse = Math.imul(reverse, 0x85ebca6b)
  }
  return `web_${(forward >>> 0).toString(16).padStart(8, '0')}_${(reverse >>> 0).toString(16).padStart(8, '0')}_${value.length}`
}

/** Opens a URL outside the workspace without being recaptured by the Dashboard popup host. */
export function openNativeBrowserUrl(url: string): boolean {
  const normalized = normalizeWebTabUrl(url)
  if (!normalized) return false
  const bridge = typeof window === 'undefined' ? null : window.__OMNICOMPANY_HOST__
  if (bridge) {
    void bridge.openSystemUrl(normalized)
    return true
  }
  if (typeof document === 'undefined') return false
  const anchor = document.createElement('a')
  anchor.href = normalized
  anchor.target = '_blank'
  anchor.rel = 'noopener noreferrer'
  anchor.setAttribute('data-omni-native-browser', '1')
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  return true
}

/** Shared Web Core navigation used unchanged by browser and desktop hosts. */
export function openWorkspaceTarget(
  target: WorkspaceTarget,
  options: WorkspaceNavigationOptions = {},
): string | null {
  const panels = usePanels.getState()
  const sourceTabId = options.sourceTabId === undefined ? panels.activeId : options.sourceTabId

  if (target.kind === 'entity') {
    const open = options.background ? panels.openTabBackground : panels.openTab
    return open(target.ref, target.title, target.facet, options.placement, sourceTabId)
  }

  const profile = target.profile ?? 'human'
  if (profile === 'system') {
    openNativeBrowserUrl(target.url)
    return null
  }
  const opened = openWebTab(target.url, target.title, options.baseHref, {
    allowExternal: true,
    profile,
    sourceTabId,
    placement: options.placement,
  })
  if (!opened) openNativeBrowserUrl(target.url)
  return opened
}
