import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './shell/App'
import SurfaceShell from './shell/SurfaceShell'
import { applyEntryRoute } from './routes/entryRoute'
import { readSurface } from './lib/surface'
import { startDevReloadWatch } from './lib/devReload'
import { registerLanAccessVisitor } from './lib/lanAccess'
import { installWebTabHost } from './lib/webTabHost'
import MultiagentLinkNavigator from './entities/multiagent/MultiagentLinkNavigator'
import GlobalFileDrop from './entities/file_bridge/GlobalFileDrop'
import { usePanels } from './stores/panelsStore'
import './index.css'
import './styles/frostpane.css'
import './styles/blueprint.css'
import './assets/fonts/fonts.css'
import './i18n/config.js'

startDevReloadWatch()
registerLanAccessVisitor()
installWebTabHost(window)

// Relay __omnichat messages from embedded review surfaces to the outer shell.
window.addEventListener('message', (ev) => {
  const data = ev.data as { __omnichat?: boolean } | null
  if (!data || data.__omnichat !== true) return
  if (ev.source === window.parent) return
  try { window.parent?.postMessage(data, '*') } catch { /* top-level window */ }
  try {
    if (window.top && window.top !== window.parent) window.top.postMessage(data, '*')
  } catch { /* cross-origin parent */ }
})

// Legacy /chat-standalone links are normalized back into the cockpit. Chat and CLI
// consume ccdaemon directly; this entry no longer redirects to a second session system.
applyEntryRoute(window)
const { surface, id } = readSurface()
const entryParams = new URLSearchParams(window.location.search)
if (surface === 'full' && entryParams.get('open') === 'file_bridge') {
  usePanels.getState().openTab({ type: 'file_bridge', id: 'main' }, 'Agent 暂存区')
  entryParams.delete('open')
  const cleanUrl = `${window.location.pathname}${entryParams.size ? `?${entryParams}` : ''}${window.location.hash}`
  window.history.replaceState(window.history.state, '', cleanUrl)
}
if (surface === 'full') {
  document.documentElement.dataset.omniDashboardShell = '1'
} else {
  delete document.documentElement.dataset.omniDashboardShell
}
const body = surface !== 'full'
  ? <SurfaceShell surface={surface} id={id} />
  : <App />
const tree = (
  <>
    <MultiagentLinkNavigator surface={surface} />
    <GlobalFileDrop surface={surface} />
    {body}
  </>
)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>{tree}</React.StrictMode>,
)
