import { useEffect, useRef } from 'react'
import type { Surface } from '../../lib/surface'
import { isInExtShell, openInOmnidashboard } from '../../lib/surface'
import { usePanels } from '../../stores/panelsStore'
import { multiagentSessionUrl, useMultiagentLink } from './multiagentLink'

/**
 * Executes peer selections. Publishing stays in the list window; receiving
 * switches the other window to the requested live session.
 */
export default function MultiagentLinkNavigator({ surface }: { surface: Surface }) {
  const sessionId = useMultiagentLink((state) => state.selectedSessionId)
  const linkId = useMultiagentLink((state) => state.linkId)
  const remoteVersion = useMultiagentLink((state) => state.remoteSelectionVersion)
  const handledVersion = useRef(0)

  useEffect(() => {
    if (!sessionId || remoteVersion <= handledVersion.current) return
    handledVersion.current = remoteVersion

    if (surface === 'full') {
      const existing = usePanels.getState().tabs.find(
        (tab) => tab.ref.type === 'cc_session' && tab.ref.id === sessionId,
      )
      usePanels.getState().openTab(
        { type: 'cc_session', id: sessionId },
        existing?.title || `会话 · ${sessionId.slice(0, 8)}`,
      )
      return
    }

    if (isInExtShell()) {
      openInOmnidashboard('cc_session', sessionId, undefined, `会话 · ${sessionId.slice(0, 8)}`)
      return
    }

    window.location.assign(multiagentSessionUrl(sessionId, linkId))
  }, [linkId, remoteVersion, sessionId, surface])

  return null
}
