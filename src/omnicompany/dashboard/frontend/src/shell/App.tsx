import React from 'react'
import 'dockview/dist/styles/dockview.css'
import { registerAllEntities } from './registerEntities'
import CockpitShell from './CockpitShell'
// @ts-ignore — jsx 文件没 .d.ts
import { ThemeProvider } from '../contexts/ThemeContext'

// CommandPalette 懒加载(2026-07 首屏拆包): kbar(~67KB)随面板退出首屏静态图。面板自包含
// (快捷键/portal/动作注册都在 KBarProvider 内部, 无其他消费方依赖 kbar context),
// 作为兄弟节点后挂, 不挡 CockpitShell 首渲染。
const CommandPalette = React.lazy(() => import('./CommandPalette').then((m) => ({ default: m.CommandPalette })))

registerAllEntities()

// ThemeProvider 必须包在最外层: 驾驶舱内的 shared/ui(如 DarkModeToggle)依赖 useTheme(),
// 没有 provider 会直接抛错。默认深色, 与驾驶舱一致。
// (人用聊天已整体迁到收编 chatui, 不再在驾驶舱内嵌聊天面板。)
export default function App() {
  return (
    <ThemeProvider>
      <CockpitShell />
      <React.Suspense fallback={null}>
        <CommandPalette />
      </React.Suspense>
    </ThemeProvider>
  )
}
