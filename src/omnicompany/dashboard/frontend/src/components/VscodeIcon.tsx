import React from 'react'

/** VS Code 折带 logo(简化版, VS Code 蓝)。用于 dashboard 里"在 VSCode 打开"的小按钮。 */
export function VscodeIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path
        d="M75.6 11.2 53.4 32.9 31.8 16.5 23 21l18.3 16.5L23 54l8.8 4.5 21.6-16.4 22.2 21.7L88 58.5V18.5L75.6 11.2zm.6 18.1v25.3L63.5 41.9 76.2 29.3z"
        fill="#0098FF"
      />
    </svg>
  )
}
