// V2 蓝图 G segmented(MAPPING 组件 1): role=radiogroup/radio 的单选段控。
// 选中态 = 45° 剖面 hatch + 制图角缺口(非填色块);计数 = 尺寸标注 ← N →(G.1,黄铜)。
// 消费: project_board 视图/分组切换;后续 review_queue/settings 等同法接入。
import React from 'react'

export interface SegItem {
  value: string
  label: string
  count?: number
}

/** 尺寸标注 ← N →(细线+端点竖杠;组头/seg 计数的蓝图语言表达)。 */
export function DimText({ children }: { children: React.ReactNode }) {
  return (
    <span className="bp-dim" aria-hidden="true">
      <i />
      <b style={{ fontWeight: 400 }}>{children}</b>
      <i />
    </span>
  )
}

export function Segmented({ items, current, onChange, label, small = false }: {
  items: SegItem[]
  current: string
  onChange: (value: string) => void
  label?: string
  small?: boolean
}) {
  return (
    <span className={`v2-seg${small ? ' seg-sm' : ''}`} role="radiogroup" aria-label={label}>
      {items.map((it) => {
        const on = it.value === current
        return (
          <button
            key={it.value}
            type="button"
            role="radio"
            aria-checked={on}
            className={`seg-i${on ? ' on' : ''}`}
            onClick={() => onChange(it.value)}
          >
            {it.label}
            {it.count != null && <span className="ct"><DimText>{it.count}</DimText></span>}
          </button>
        )
      })}
    </span>
  )
}
