import { create } from 'zustand'

const CHANNEL_NAME = 'omni.multiagent.link.v1'
const OWNER_LINK_KEY = 'omni.multiagent.link.owner.v2'
const LEGACY_ACTIVE_LINK_KEY = 'omni.multiagent.link.active.v1'
const HEARTBEAT_MS = 1_500
const STALE_MS = 5_000

type LinkRole = 'owner' | 'viewer'
type LinkMessage = {
  linkId: string
  windowId: string
  role: LinkRole
  type: 'heartbeat' | 'select' | 'release'
  sessionId?: string
  targetWindowId?: string
  at: number
}

interface MultiagentLinkState {
  linkId: string | null
  role: LinkRole
  connected: boolean
  peerWindowId: string | null
  selectedSessionId: string | null
  /** Only increments for a selection received from the peer window. */
  remoteSelectionVersion: number
  peerLastSeen: number
  ensureOwnerLink: () => string
  publishSelection: (sessionId: string) => void
  release: () => void
}

function randomId(prefix: string): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  return `${prefix}-${uuid || Math.random().toString(36).slice(2)}`
}

function queryLinkId(): string | null {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('ma_link')
}

function readOwnerLink(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(OWNER_LINK_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return typeof parsed?.id === 'string' ? parsed.id : null
  } catch {
    return null
  }
}

function writeOwnerLink(linkId: string | null): void {
  if (typeof window === 'undefined') return
  try {
    if (linkId) window.sessionStorage.setItem(OWNER_LINK_KEY, JSON.stringify({ id: linkId, at: Date.now() }))
    else window.sessionStorage.removeItem(OWNER_LINK_KEY)
    // v1 stored the owner identity in localStorage, which made every same-origin
    // Dashboard window join the same link. Always retire that broadcast identity.
    window.localStorage.removeItem(LEGACY_ACTIVE_LINK_KEY)
  } catch { /* privacy mode */ }
}

function stripLinkQuery(): void {
  if (typeof window === 'undefined') return
  try {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('ma_link')) return
    url.searchParams.delete('ma_link')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  } catch { /* navigation may be unavailable in an embedded host */ }
}

const initialQueryLink = queryLinkId()
const initialRole: LinkRole = initialQueryLink ? 'viewer' : 'owner'
// A new tab can inherit sessionStorage from its opener. The explicit viewer URL
// wins and removes any cloned owner identity from that tab only.
if (initialQueryLink) writeOwnerLink(null)
else if (typeof window !== 'undefined') {
  try { window.localStorage.removeItem(LEGACY_ACTIVE_LINK_KEY) } catch { /* privacy mode */ }
}
const initialLink = initialQueryLink || readOwnerLink()
// A page-instance id avoids cloned sessionStorage making two tabs look identical.
const selfWindowId = randomId('window')
let channel: BroadcastChannel | null = null
let pulseTimer: ReturnType<typeof setInterval> | null = null

function post(message: Omit<LinkMessage, 'windowId' | 'targetWindowId' | 'at'>): void {
  try {
    const targetWindowId = useMultiagentLink.getState().peerWindowId || undefined
    channel?.postMessage({
      ...message,
      windowId: selfWindowId,
      targetWindowId,
      at: Date.now(),
    } satisfies LinkMessage)
  } catch { /* closing window / unsupported channel */ }
}

function startChannel(): void {
  if (typeof window === 'undefined' || typeof BroadcastChannel === 'undefined' || channel) return
  channel = new BroadcastChannel(CHANNEL_NAME)
  channel.onmessage = (event: MessageEvent<LinkMessage>) => {
    const message = event.data
    const state = useMultiagentLink.getState()
    if (
      !message
      || !state.linkId
      || message.linkId !== state.linkId
      || message.windowId === selfWindowId
      || message.role === state.role
      || (message.targetWindowId && message.targetWindowId !== selfWindowId)
    ) return
    // A link is a one-to-one claim, not a rolling presence lease. Browsers may
    // throttle a background tab for much longer than STALE_MS; allowing a new
    // sender to replace the claimed peer then routes the next selection into a
    // third window. Keep the peer identity until an explicit release. Staleness
    // only changes the connected indicator below.
    if (state.peerWindowId && state.peerWindowId !== message.windowId) return
    // A viewer becomes paired only after the owner explicitly targets it.
    // This makes the first claimed viewer authoritative when the same URL was
    // accidentally pasted into more than one extra window.
    if (state.role === 'viewer' && !message.targetWindowId) return
    if (message.type === 'release') {
      if (state.peerWindowId !== message.windowId) return
      if (state.role === 'owner') writeOwnerLink(null)
      stripLinkQuery()
      useMultiagentLink.setState({
        linkId: null,
        role: 'owner',
        connected: false,
        peerWindowId: null,
        selectedSessionId: null,
        peerLastSeen: 0,
      })
      return
    }
    const firstViewerAck = state.role === 'viewer'
      && !state.peerWindowId
      && !!message.targetWindowId
      && !!state.selectedSessionId
    useMultiagentLink.setState((current) => ({
      // Untargeted heartbeats are discovery only. The pair is connected after
      // the peer responds to this exact page-instance id.
      connected: !!message.targetWindowId,
      peerWindowId: message.windowId,
      peerLastSeen: message.at || Date.now(),
      ...(message.type === 'select' && message.sessionId && message.targetWindowId
        ? {
          selectedSessionId: message.sessionId,
          remoteSelectionVersion: current.remoteSelectionVersion + 1,
        }
        : null),
    }))
    if (firstViewerAck && state.selectedSessionId) {
      post({
        type: 'select',
        linkId: state.linkId,
        role: state.role,
        sessionId: state.selectedSessionId,
      })
    }
  }
  const pulse = () => {
    const state = useMultiagentLink.getState()
    if (!state.linkId) return
    post({ type: 'heartbeat', linkId: state.linkId, role: state.role })
    if (state.peerWindowId && Date.now() - state.peerLastSeen > STALE_MS) {
      useMultiagentLink.setState({ connected: false })
    }
  }
  pulse()
  pulseTimer = setInterval(pulse, HEARTBEAT_MS)
}

export const useMultiagentLink = create<MultiagentLinkState>((set, get) => ({
  linkId: initialLink,
  role: initialRole,
  connected: false,
  peerWindowId: null,
  selectedSessionId: null,
  remoteSelectionVersion: 0,
  peerLastSeen: 0,
  ensureOwnerLink: () => {
    const existing = get().linkId
    if (existing) {
      startChannel()
      return existing
    }
    const linkId = randomId('multiagent')
    writeOwnerLink(linkId)
    set({ linkId, role: 'owner', connected: false, peerWindowId: null, peerLastSeen: 0 })
    startChannel()
    return linkId
  },
  publishSelection: (sessionId) => {
    const state = get()
    set({ selectedSessionId: sessionId })
    if (state.linkId && state.peerWindowId) {
      post({ type: 'select', linkId: state.linkId, role: state.role, sessionId })
    }
  },
  release: () => {
    const state = get()
    if (state.linkId) post({ type: 'release', linkId: state.linkId, role: state.role })
    if (state.role === 'owner') writeOwnerLink(null)
    stripLinkQuery()
    set({
      linkId: null,
      role: 'owner',
      connected: false,
      peerWindowId: null,
      selectedSessionId: null,
      peerLastSeen: 0,
    })
  },
}))

if (initialLink) startChannel()

export function multiagentLinkedUrl(
  linkId: string = useMultiagentLink.getState().ensureOwnerLink(),
  baseHref: string = window.location.href,
): string {
  const url = new URL(baseHref)
  url.search = ''
  url.hash = ''
  url.searchParams.set('surface', 'multiagent')
  url.searchParams.set('ma_link', linkId)
  return url.toString()
}

export function multiagentSessionUrl(
  sessionId: string,
  linkId: string | null = useMultiagentLink.getState().linkId,
  baseHref: string = window.location.href,
): string {
  const url = new URL(baseHref)
  url.search = ''
  url.hash = ''
  if (linkId) url.searchParams.set('ma_link', linkId)
  url.searchParams.set('open_type', 'cc_session')
  url.searchParams.set('open_id', sessionId)
  url.searchParams.set('open_title', `会话 · ${sessionId.slice(0, 8)}`)
  return url.toString()
}

export function openMultiagentLinkedWindow(): { url: string; opened: boolean } {
  const linkId = useMultiagentLink.getState().ensureOwnerLink()
  const url = multiagentLinkedUrl(linkId)
  const opened = window.open(url, '_blank')
  try {
    if (opened && opened !== window) opened.opener = null
  } catch { /* cross-window policy */ }
  return { url, opened: !!opened && opened !== window }
}

export function isMultiagentLinkedSurface(): boolean {
  const state = useMultiagentLink.getState()
  return state.role === 'viewer' && !!state.linkId
}

export function stopMultiagentLinkForTests(): void {
  if (pulseTimer) clearInterval(pulseTimer)
  pulseTimer = null
  channel?.close()
  channel = null
}
