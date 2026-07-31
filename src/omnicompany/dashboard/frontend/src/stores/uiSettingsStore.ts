import { create } from 'zustand'

/** 全 app 字体地板(2026-06-06 用户: 12 太小 / 16 太平, 折中 14, 且大小拉开细微差异)。
 *  inline/Tailwind 字号经 remap 后下限 14(层次保留: 14/15/16/18…);body 基线与 markdown 预览同步到 14。
 *  要整体调地板: 改这里 + index.css body font-size + 重跑 .omni/sandbox 下 font-remap codemod。 */
export const UI_FONT_MIN = 14

const FS_KEY = 'omni:previewFontSize'
const FS_MIN = UI_FONT_MIN
const FS_MAX = 28
const FS_DEFAULT = 14

function loadFontSize(): number {
  try {
    const v = Number(localStorage.getItem(FS_KEY))
    if (Number.isFinite(v) && v >= FS_MIN && v <= FS_MAX) return v
  } catch { /* localStorage may be unavailable */ }
  return FS_DEFAULT
}

interface S {
  previewFontSize: number
  setPreviewFontSize: (n: number) => void
  bumpPreviewFontSize: (delta: number) => void
  resetPreviewFontSize: () => void
}

export const useUiSettings = create<S>((set, get) => ({
  previewFontSize: loadFontSize(),
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
}))

export const PREVIEW_FONT_LIMITS = { min: FS_MIN, max: FS_MAX, default: FS_DEFAULT }
