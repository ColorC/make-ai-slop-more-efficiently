import type React from 'react'

/**
 * 给"点击可打开成页签"的元素加鼠标中键后台打开支持(2026-06-06 用户: 不能中键打开页签)。
 *   primary    = 左键: 打开并切到该页签(原有行为)。
 *   background = 中键: 后台打开, 当前视图不动(像浏览器中键开后台页)。不传则中键同 primary。
 * 用法: <div {...openProps(() => open(x), () => openBg(x))}>…</div>
 * 注意: 用在原本 onClick 的元素上, 它会覆盖 onClick, 所以别再单独写 onClick。
 */
export function openProps(primary: () => void, background?: () => void) {
  return {
    onClick: primary,
    onAuxClick: (e: React.MouseEvent) => {
      if (e.button === 1) { e.preventDefault(); (background || primary)() }
    },
    // 中键 mousedown 默认会进入"自动滚动"模式, 阻止它。
    onMouseDown: (e: React.MouseEvent) => { if (e.button === 1) e.preventDefault() },
  }
}
