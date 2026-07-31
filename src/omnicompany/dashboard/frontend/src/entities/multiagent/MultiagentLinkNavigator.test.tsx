import React from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import MultiagentLinkNavigator from './MultiagentLinkNavigator'
import { useMultiagentLink } from './multiagentLink'
import { usePanels } from '../../stores/panelsStore'

describe('MultiagentLinkNavigator', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: [], activeId: null })
    useMultiagentLink.setState({
      linkId: 'pair-1',
      role: 'viewer',
      connected: true,
      selectedSessionId: null,
      remoteSelectionVersion: 0,
      peerLastSeen: Date.now(),
    })
  })

  afterEach(() => {
    useMultiagentLink.setState({
      linkId: null,
      role: 'owner',
      connected: false,
      selectedSessionId: null,
      remoteSelectionVersion: 0,
      peerLastSeen: 0,
    })
  })

  it('turns a peer selection into a real session tab in a cockpit window', async () => {
    render(<MultiagentLinkNavigator surface="full" />)
    act(() => {
      useMultiagentLink.setState({
        selectedSessionId: 'pty-session-7',
        remoteSelectionVersion: 1,
      })
    })

    await waitFor(() => {
      expect(usePanels.getState().activeId).toBe('cc_session:pty-session-7')
    })
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      ref: { type: 'cc_session', id: 'pty-session-7' },
    })])

    act(() => {
      useMultiagentLink.setState({
        linkId: null,
        connected: false,
        selectedSessionId: null,
      })
      useMultiagentLink.setState({
        linkId: 'pair-2',
        connected: true,
        selectedSessionId: 'pty-session-8',
        remoteSelectionVersion: 2,
      })
    })
    await waitFor(() => {
      expect(usePanels.getState().activeId).toBe('cc_session:pty-session-8')
    })
  })
})
