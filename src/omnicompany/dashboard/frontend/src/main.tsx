import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './shell/App'
import SurfaceShell from './shell/SurfaceShell'
import { applyEntryRoute } from './routes/entryRoute'
import { readSurface } from './lib/surface'
import { startDevReloadWatch } from './lib/devReload'
import { startClientInspect } from './lib/clientInspect'
import { registerLanAccessVisitor } from './lib/lanAccess'
import { installWebTabHost } from './lib/webTabHost'
import { installLofaNativeBridge } from './lib/lofaNativeBridge'
import { ensureGatewaySession } from './lib/sessionBootstrap'
import { installFrontendTelemetry } from './lib/frontendTelemetry'
import MultiagentLinkNavigator from './entities/multiagent/MultiagentLinkNavigator'
import GlobalFileDrop from './entities/file_bridge/GlobalFileDrop'
import { usePanels } from './stores/panelsStore'
import { applyTerminalRendererAttr, useUiSettings } from './stores/uiSettingsStore'
import type { EntityType } from './entities/types'
import './index.css'
import './styles/frostpane.css'
import './styles/blueprint.css'
import './styles/motion.css'
import './assets/fonts/fonts.css'
import './assets/fonts/sarasa.css'
import './i18n/config.js'

// 终端底档位要在首帧前落到 <html>, 否则刷新后 CSS 分档丢失、玻璃档退回深压暗。
applyTerminalRendererAttr(useUiSettings.getState().terminalRenderer)
// 前端观测最先装: 后续一切异常/慢请求都能被捕获上报(观测不反噬页面)。
installFrontendTelemetry()
startDevReloadWatch()
startClientInspect()
registerLanAccessVisitor()
installWebTabHost(window)
installLofaNativeBridge(window)
// 持久会话自举(不阻塞首帧): 已有会话则零请求外的静默跳过; Basic 缓存在则静默换
// cookie; 都没有时该 fetch 触发一次原生登录框 —— 那本来就是登录时机。
void ensureGatewaySession()

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
// Make a deep-linked panel active before React mounts.  Waiting for
// CockpitShell's effect lets the default project board win the first frame and
// start its large background workload before the requested CLI even exists.
if (surface === 'full') {
  const openType = entryParams.get('open_type') as EntityType | null
  const openId = entryParams.get('open_id')
  if (openType && openId) {
    const openTitle = entryParams.get('open_title') || openId.split('/').pop() || openId
    const openFacet = entryParams.get('open_facet') || undefined
    usePanels.getState().openTab({ type: openType, id: openId }, openTitle, openFacet)
  }
}
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
