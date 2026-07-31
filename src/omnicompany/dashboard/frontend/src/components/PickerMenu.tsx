// V2 蓝图 G tag picker(MAPPING 组件 5): 按钮面=虚线测量件 + 黄铜计数徽章(pk-n),
// 弹层=深色描图纸(tracing + blur(8px) + 虚线边,G.3①;玻璃配方内联在本文件——
// glass-scope 外壳白名单按文件名放行 menu 族)。行=check row(白线方框+hatch 选中)。
// 脚:清除/全选;弹层外点/Esc 收起。消费: project_board 活跃度筛选;后续 review/material 同法。
import React, { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, SlidersHorizontal } from 'lucide-react'

export interface PickerOption {
  value: string
  label: string
  count?: number
}

// 弹层=描图纸(本版的"玻璃",只给悬浮物);微卷角用 linear-gradient 假折角(纯 CSS)。
const POP: React.CSSProperties = {
  position: 'absolute', zIndex: 40, top: 'calc(100% + 6px)', left: 0, minWidth: 210,
  background: 'var(--fp-bp-tracing)', backdropFilter: 'var(--fp-bp-tracing-blur)',
  WebkitBackdropFilter: 'var(--fp-bp-tracing-blur)',
  border: '1px dashed var(--fp-border-strong)', borderRadius: 3,
  boxShadow: 'var(--fp-shadow-pop)', padding: 6,
}
const FOLD: React.CSSProperties = {
  position: 'absolute', top: 0, right: 0, width: 14, height: 14, pointerEvents: 'none',
  background: 'linear-gradient(225deg, var(--fp-bg) 0 48%, rgba(235,245,255,.35) 50%, transparent 62%)',
}

export function PickerMenu({ label, options, selected, onChange }: {
  label: string
  options: PickerOption[]
  selected: string[]
  onChange: (next: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && e.target instanceof Node && !rootRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const toggle = (v: string) => {
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v])
  }

  return (
    <span className="v2-picker" ref={rootRef} data-testid={`picker-${label}`}>
      <button
        type="button"
        className="pk-btn"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
      >
        <SlidersHorizontal size={13} aria-hidden />
        <span>{label}</span>
        {selected.length > 0 && <span className="pk-n">{selected.length}</span>}
        <ChevronDown size={12} aria-hidden />
      </button>
      {open && (
        <div style={POP} role="group" aria-label={label}>
          <span style={FOLD} aria-hidden="true" />
          <div style={{ fontSize: 'var(--fp-fs-4)', color: 'var(--fp-text-3)', padding: '6px 10px 3px', fontWeight: 600, fontFamily: 'var(--fp-font-mono)', letterSpacing: '.1em', textTransform: 'uppercase' }}>{label}(多选)</div>
          {options.map((o) => {
            const on = selected.includes(o.value)
            return (
              <button
                key={o.value}
                type="button"
                className="v2-checkrow"
                role="checkbox"
                aria-checked={on}
                onClick={() => toggle(o.value)}
              >
                <span className="cb" aria-hidden="true"><Check size={11} strokeWidth={3} /></span>
                <span className="cr-t">{o.label}</span>
                {o.count != null && <span className="cr-d">{o.count}</span>}
              </button>
            )
          })}
          <div style={{ display: 'flex', borderTop: '1px solid var(--fp-border)', marginTop: 4, paddingTop: 4 }}>
            <button type="button" className="v2-checkrow" style={{ justifyContent: 'center' }} onClick={() => onChange([])}>清除</button>
            <button type="button" className="v2-checkrow" style={{ justifyContent: 'center' }} onClick={() => onChange(options.map((o) => o.value))}>全选</button>
          </div>
        </div>
      )}
    </span>
  )
}
