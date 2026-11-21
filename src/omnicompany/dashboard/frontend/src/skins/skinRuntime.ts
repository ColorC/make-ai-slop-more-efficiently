// 皮肤运行时 —— 把选中的皮肤落到页面上, 并记住选择。
// 落点: <html data-omni-skin="id">(样式表按它分块) + 深浅 class + color-scheme。
// 首帧前生效靠 index.html 里的引导脚本: 它读下面两个 localStorage 键, 在主包加载前
// 先挂上皮肤 id 与上次缓存的变量块, 避免每次刷新先闪一下蓝图。
import { DEFAULT_SKIN_ID, SKINS, resolveSkin, type SkinDefinition } from './skinRegistry'

export const SKIN_STORAGE_KEY = 'omni:skin'
/** 引导脚本用: 当前皮肤的变量块原文。 */
export const SKIN_BOOT_CSS_KEY = 'omni:skin:bootCss'
const SHEET_ID = 'omni-skin-sheet'
const BOOT_SHEET_ID = 'omni-skin-boot'

export function skinSelector(id: string): string {
  return `:root[data-omni-skin="${id}"]`
}

export function skinCss(skin: SkinDefinition): string {
  const declarations = Object.entries(skin.variables)
    .map(([name, value]) => `${name}:${value};`)
    .join('')
  // color-scheme 让原生滚动条、表单控件跟着皮肤的明暗走。
  return `${skinSelector(skin.id)}{color-scheme:${skin.base};${declarations}}`
}

export function allSkinsCss(): string {
  return SKINS.map(skinCss).join('\n')
}

export function loadSkinId(): string {
  try {
    return resolveSkin(localStorage.getItem(SKIN_STORAGE_KEY)).id
  } catch {
    return DEFAULT_SKIN_ID
  }
}

function ensureSheet(doc: Document): void {
  if (doc.getElementById(SHEET_ID)) return
  const sheet = doc.createElement('style')
  sheet.id = SHEET_ID
  sheet.textContent = allSkinsCss()
  doc.head.appendChild(sheet)
  // 正式样式表就位后, 引导脚本那份缓存块不再需要。
  doc.getElementById(BOOT_SHEET_ID)?.remove()
}

/** 把皮肤落到页面上(不写存储)。返回实际生效的皮肤。 */
export function applySkin(id: string | null | undefined, doc: Document = document): SkinDefinition {
  const skin = resolveSkin(id)
  try {
    ensureSheet(doc)
    const root = doc.documentElement
    root.dataset.omniSkin = skin.id
    root.classList.toggle('dark', skin.base === 'dark')
    doc.querySelector('meta[name="theme-color"]')?.setAttribute('content', skin.preview.scene)
  } catch { /* SSR/测试环境 */ }
  return skin
}

export function persistSkin(skin: SkinDefinition): void {
  try {
    localStorage.setItem(SKIN_STORAGE_KEY, skin.id)
    localStorage.setItem(SKIN_BOOT_CSS_KEY, skinCss(skin))
  } catch { /* localStorage may be unavailable */ }
}

/** 别的页签或嵌入面改了皮肤时, 本页跟随。返回取消监听的函数。 */
export function followSkinChanges(onChange: (id: string) => void, win: Window = window): () => void {
  const handler = (event: StorageEvent) => {
    if (event.key !== SKIN_STORAGE_KEY) return
    onChange(resolveSkin(event.newValue).id)
  }
  win.addEventListener('storage', handler)
  return () => win.removeEventListener('storage', handler)
}
