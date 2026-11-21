import { useCallback, useEffect, useState } from 'react'
import {
  getLofaBridgeState,
  requestLofaNative,
  type LofaBridgeState,
} from '../lib/lofaNativeBridge'

export interface LofaHostMode {
  /** 壳的重载入口可用(仅在 LOFA 里, 且原生桥已握手)。 */
  canReloadShell: boolean
  /** 壳的本地设置入口可用(连接地址 / OTA / 诊断)。 */
  canOpenShellSettings: boolean
  reloadShell: () => Promise<void>
  openShellSettings: () => Promise<void>
}

/**
 * LOFA 壳能力的唯一 Web 侧读点。
 *
 * 这两件事本来挂在壳自己的悬浮按钮上(top-right, 盖住 Dashboard 的页签与工具行)。
 * 收进驾驶舱自己的菜单后, 壳那两颗浮标就能撤掉 —— 手机上少一层遮挡。
 */
export function useLofaHostMode(): LofaHostMode {
  const [state, setState] = useState<LofaBridgeState>(() => getLofaBridgeState())

  useEffect(() => {
    const onState = (event: Event) => {
      const detail = (event as CustomEvent<LofaBridgeState>).detail
      if (detail && typeof detail.status === 'string') setState(detail)
    }
    window.addEventListener('omnicompany:lofa-bridge-state', onState)
    setState(getLofaBridgeState())
    return () => window.removeEventListener('omnicompany:lofa-bridge-state', onState)
  }, [])

  const ready = state.status === 'ready' || state.status === 'outdated'
  const reloadShell = useCallback(async () => {
    await requestLofaNative('shell.reload-dashboard')
  }, [])
  const openShellSettings = useCallback(async () => {
    await requestLofaNative('shell.open-settings')
  }, [])

  return {
    canReloadShell: ready && state.capabilities.includes('shell.reload-dashboard'),
    canOpenShellSettings: ready && state.capabilities.includes('shell.open-settings'),
    reloadShell,
    openShellSettings,
  }
}
