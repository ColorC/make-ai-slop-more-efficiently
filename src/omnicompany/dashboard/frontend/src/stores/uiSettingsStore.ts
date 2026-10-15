import { create } from 'zustand'

/** 全 app 字体地板(2026-06-06 用户: 12 太小 / 16 太平, 折中 14, 且大小拉开细微差异)。
 *  inline/Tailwind 字号经 remap 后下限 14(层次保留: 14/15/16/18…);body 基线与 markdown 预览同步到 14。
 *  要整体调地板: 改这里 + index.css body font-size + 重跑 .omni/sandbox 下 font-remap codemod。 */
export const UI_FONT_MIN = 14

const FS_KEY = 'omni:previewFontSize'
const FS_MIN = UI_FONT_MIN
const FS_MAX = 28
const FS_DEFAULT = 14

const TAB_LAYOUT_KEY = 'omni:tabLayout'
const TAB_TITLE_MAX_KEY = 'omni:tabTitleMaxLength'
export type TabLayout = 'single-row' | 'stacked'
const TAB_LAYOUT_DEFAULT: TabLayout = 'single-row'
const TAB_TITLE_MIN = 8
const TAB_TITLE_MAX = 40
const TAB_TITLE_DEFAULT = 18

// ── 终端渲染器(2026-08-06) ───────────────────────────────────────────────
// 二选一, 没有两全:
//   gpu   —— @xterm/addon-webgl。快, 但实测它的画布是不透明的, theme.background 进不去
//            (三组对照: WebGL 开 + allowTransparency 开/关都是纯黑; 关掉 WebGL 探针红色才出现),
//            所以格纸透不到文字底下, 玻璃只能做在窗框上。
//   glass —— 退回内建 DOM 渲染器。格纸真的从文字底下透上来, 代价是大量输出滚动时更吃 CPU。
// 默认 glass(2026-08-06 用户裁定「换呗，试试看」): 蓝图格纸与边缘标尺能真的从文字底下
// 透上来。代价是退回 CPU 渲染, 大量输出滚动时更吃性能 —— 觉得卡就在
// 设置 → 界面 → 终端底 切回「高性能」, 重开终端页签即生效。
const TERMINAL_RENDERER_KEY = 'omni:terminalRenderer'
export type TerminalRenderer = 'gpu' | 'glass'
const TERMINAL_RENDERER_DEFAULT: TerminalRenderer = 'glass'

/** 档位同时写到 <html data-omni-terminal-renderer>, 让 CSS 能分档给不同压暗量。 */
export function applyTerminalRendererAttr(renderer: TerminalRenderer): void {
  try { document.documentElement.dataset.omniTerminalRenderer = renderer } catch { /* SSR/测试环境 */ }
}

function loadTerminalRenderer(): TerminalRenderer {
  try {
    const value = localStorage.getItem(TERMINAL_RENDERER_KEY)
    if (value === 'gpu' || value === 'glass') return value
  } catch { /* localStorage may be unavailable */ }
  return TERMINAL_RENDERER_DEFAULT
}

function loadFontSize(): number {
  try {
    const v = Number(localStorage.getItem(FS_KEY))
    if (Number.isFinite(v) && v >= FS_MIN && v <= FS_MAX) return v
  } catch { /* localStorage may be unavailable */ }
  return FS_DEFAULT
}

function loadTabLayout(): TabLayout {
  try {
    const value = localStorage.getItem(TAB_LAYOUT_KEY)
    if (value === 'single-row' || value === 'stacked') return value
  } catch { /* localStorage may be unavailable */ }
  return TAB_LAYOUT_DEFAULT
}

function clampTabTitleMaxLength(value: number): number {
  const rounded = Number.isFinite(value) ? Math.round(value) : TAB_TITLE_DEFAULT
  return Math.min(TAB_TITLE_MAX, Math.max(TAB_TITLE_MIN, rounded))
}

function loadTabTitleMaxLength(): number {
  try {
    const raw = localStorage.getItem(TAB_TITLE_MAX_KEY)
    if (raw !== null) {
      const value = Number(raw)
      if (Number.isFinite(value)) return clampTabTitleMaxLength(value)
    }
  } catch { /* localStorage may be unavailable */ }
  return TAB_TITLE_DEFAULT
}

interface S {
  previewFontSize: number
  tabLayout: TabLayout
  tabTitleMaxLength: number
  terminalRenderer: TerminalRenderer
  setPreviewFontSize: (n: number) => void
  bumpPreviewFontSize: (delta: number) => void
  resetPreviewFontSize: () => void
  setTabLayout: (layout: TabLayout) => void
  setTabTitleMaxLength: (n: number) => void
  setTerminalRenderer: (renderer: TerminalRenderer) => void
}

export const useUiSettings = create<S>((set, get) => ({
  previewFontSize: loadFontSize(),
  tabLayout: loadTabLayout(),
  tabTitleMaxLength: loadTabTitleMaxLength(),
  terminalRenderer: loadTerminalRenderer(),
  setPreviewFontSize: (n) => {
    const v = Math.min(FS_MAX, Math.max(FS_MIN, Math.round(n)))
    try { localStorage.setItem(FS_KEY, String(v)) } catch { /* */ }
    set({ previewFontSize: v })
  },
  bumpPreviewFontSize: (delta) => {
    const v = Math.min(FS_MAX, Math.max(FS_MIN, get().previewFontSize + delta))
    try { localStorage.setItem(FS_KEY, String(v)) } catch { /* */ }
    set({ previewFontSize: v })
  },
  resetPreviewFontSize: () => {
    try { localStorage.setItem(FS_KEY, String(FS_DEFAULT)) } catch { /* */ }
    set({ previewFontSize: FS_DEFAULT })
  },
  setTabLayout: (layout) => {
    try { localStorage.setItem(TAB_LAYOUT_KEY, layout) } catch { /* */ }
    set({ tabLayout: layout })
  },
  setTabTitleMaxLength: (n) => {
    const value = clampTabTitleMaxLength(n)
    try { localStorage.setItem(TAB_TITLE_MAX_KEY, String(value)) } catch { /* */ }
    set({ tabTitleMaxLength: value })
  },
  // 已开的终端不会热切渲染器(xterm 的 addon 在构造期装载), 重开页签才生效 —— UI 上说清楚。
  setTerminalRenderer: (renderer) => {
    try { localStorage.setItem(TERMINAL_RENDERER_KEY, renderer) } catch { /* */ }
    applyTerminalRendererAttr(renderer)
    set({ terminalRenderer: renderer })
  },
}))

export const PREVIEW_FONT_LIMITS = { min: FS_MIN, max: FS_MAX, default: FS_DEFAULT }
export const TAB_TITLE_LIMITS = { min: TAB_TITLE_MIN, max: TAB_TITLE_MAX, default: TAB_TITLE_DEFAULT }
export const TERMINAL_RENDERER_DEFAULT_VALUE = TERMINAL_RENDERER_DEFAULT
export const UI_SETTINGS_STORAGE_KEYS = {
  tabLayout: TAB_LAYOUT_KEY,
  tabTitleMaxLength: TAB_TITLE_MAX_KEY,
  terminalRenderer: TERMINAL_RENDERER_KEY,
}
