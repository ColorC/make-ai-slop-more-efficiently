import { afterEach, describe, expect, it, vi } from 'vitest'

class FakeBroadcastChannel {
  static peers: FakeBroadcastChannel[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  constructor(public name: string) {
    FakeBroadcastChannel.peers.push(this)
  }
  postMessage(data: unknown) {
    for (const peer of FakeBroadcastChannel.peers) {
      if (peer !== this && peer.name === this.name) peer.onmessage?.({ data } as MessageEvent)
    }
  }
  close() {
    FakeBroadcastChannel.peers = FakeBroadcastChannel.peers.filter((peer) => peer !== this)
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
  window.sessionStorage.clear()
  FakeBroadcastChannel.peers = []
  vi.resetModules()
})

describe('Multiagent window link', () => {
  it('does not let a legacy localStorage link enroll unrelated Dashboard windows', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    window.localStorage.setItem('omni.multiagent.link.active.v1', JSON.stringify({ id: 'legacy-pair' }))

    const unrelated = await import('./multiagentLink')
    expect(unrelated.useMultiagentLink.getState().linkId).toBeNull()
    expect(window.localStorage.getItem('omni.multiagent.link.active.v1')).toBeNull()
    unrelated.stopMultiagentLinkForTests()
  })

  it('builds a fixed standalone surface URL', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const owner = await import('./multiagentLink')
    const linkId = owner.useMultiagentLink.getState().ensureOwnerLink()

    expect(owner.multiagentLinkedUrl(linkId, 'https://dashboard.test:8210')).toBe(
      `https://dashboard.test:8210/?surface=multiagent&ma_link=${encodeURIComponent(linkId)}`,
    )
    owner.stopMultiagentLinkForTests()
  })

  it('preserves the current Dashboard mount path in the linked URL', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const owner = await import('./multiagentLink')
    const linkId = owner.useMultiagentLink.getState().ensureOwnerLink()

    expect(owner.multiagentLinkedUrl(linkId, 'https://dashboard.test:8210/lofa/?old=1')).toBe(
      `https://dashboard.test:8210/lofa/?surface=multiagent&ma_link=${encodeURIComponent(linkId)}`,
    )
    owner.stopMultiagentLinkForTests()
  })

  it('builds a cockpit session target and preserves the link only while attached', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const owner = await import('./multiagentLink')

    expect(owner.multiagentSessionUrl(
      'pty-session-7',
      'pair-1',
      'https://dashboard.test:8210/lofa/?surface=multiagent&ma_link=old',
    )).toBe(
      'https://dashboard.test:8210/lofa/?ma_link=pair-1&open_type=cc_session&open_id=pty-session-7&open_title=%E4%BC%9A%E8%AF%9D+%C2%B7+pty-sess',
    )
    expect(owner.multiagentSessionUrl(
      'pty-session-7',
      null,
      'https://dashboard.test:8210/lofa/?surface=multiagent',
    )).not.toContain('ma_link=')
    owner.stopMultiagentLinkForTests()
  })

  it('claims exactly one owner even when another tab inherited the owner session link', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const owner = await import('./multiagentLink')
    const linkId = owner.useMultiagentLink.getState().ensureOwnerLink()

    // Duplicate-tab semantics can clone sessionStorage. Both pages know the
    // link id, but only the owner acknowledged by the viewer may control it.
    vi.resetModules()
    const duplicateOwner = await import('./multiagentLink')
    expect(duplicateOwner.useMultiagentLink.getState().linkId).toBe(linkId)

    window.sessionStorage.clear()
    window.history.replaceState({}, '', `/?surface=multiagent&ma_link=${encodeURIComponent(linkId)}`)
    vi.resetModules()
    const viewer = await import('./multiagentLink')
    vi.advanceTimersByTime(1_600)

    expect(owner.useMultiagentLink.getState().connected).toBe(true)
    expect(duplicateOwner.useMultiagentLink.getState().connected).toBe(false)
    expect(viewer.useMultiagentLink.getState().connected).toBe(true)

    duplicateOwner.useMultiagentLink.getState().publishSelection('wrong-window')
    expect(viewer.useMultiagentLink.getState().selectedSessionId).toBeNull()
    owner.useMultiagentLink.getState().publishSelection('paired-window')
    expect(viewer.useMultiagentLink.getState().selectedSessionId).toBe('paired-window')

    owner.stopMultiagentLinkForTests()
    duplicateOwner.stopMultiagentLinkForTests()
    viewer.stopMultiagentLinkForTests()
  })

  it('keeps owner and viewer bound and forwards the selected session', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const owner = await import('./multiagentLink')
    const linkId = owner.useMultiagentLink.getState().ensureOwnerLink()

    // Simulate a distinct browser tab: same origin/localStorage, distinct sessionStorage/window id.
    window.sessionStorage.clear()
    window.history.replaceState({}, '', `/?surface=multiagent&ma_link=${encodeURIComponent(linkId)}`)
    vi.resetModules()
    const viewer = await import('./multiagentLink')

    vi.advanceTimersByTime(1_600)
    expect(owner.useMultiagentLink.getState().connected).toBe(true)
    expect(viewer.useMultiagentLink.getState().connected).toBe(true)
    expect(owner.useMultiagentLink.getState().peerWindowId).toBeTruthy()
    expect(viewer.useMultiagentLink.getState().peerWindowId).toBeTruthy()

    // A third tab with the copied URL must remain unpaired and must not react
    // to commands targeted at the already claimed viewer.
    window.sessionStorage.clear()
    vi.resetModules()
    const third = await import('./multiagentLink')
    vi.advanceTimersByTime(1_600)
    expect(third.useMultiagentLink.getState().connected).toBe(false)
    expect(third.useMultiagentLink.getState().peerWindowId).toBeNull()

    // Background tabs are commonly throttled beyond the liveness timeout.
    // The third window still must not steal the existing one-to-one claim.
    vi.advanceTimersByTime(6_000)
    expect(owner.useMultiagentLink.getState().peerWindowId).toBeTruthy()
    expect(third.useMultiagentLink.getState().connected).toBe(false)
    expect(third.useMultiagentLink.getState().peerWindowId).toBeNull()

    owner.useMultiagentLink.getState().publishSelection('pty-session-7')
    expect(viewer.useMultiagentLink.getState().selectedSessionId).toBe('pty-session-7')
    expect(third.useMultiagentLink.getState().selectedSessionId).toBeNull()
    expect(owner.useMultiagentLink.getState().remoteSelectionVersion).toBe(0)
    expect(viewer.useMultiagentLink.getState().remoteSelectionVersion).toBe(1)

    owner.useMultiagentLink.getState().release()
    expect(viewer.useMultiagentLink.getState().linkId).toBeNull()
    expect(viewer.useMultiagentLink.getState().selectedSessionId).toBeNull()
    expect(viewer.useMultiagentLink.getState().remoteSelectionVersion).toBe(1)

    owner.stopMultiagentLinkForTests()
    viewer.stopMultiagentLinkForTests()
    third.stopMultiagentLinkForTests()
  })
})
