// 可复用「…更多」三点菜单 — 照搬 CockpitShell 顶栏已验证的 moreMenu 模式(触发钮 + 全屏透明遮罩
// 点击关 + data-omni-capture-ignore), 改成按触发钮 rect 定位(贴在按钮下方右对齐, 越界自动翻上/夹边),
// 并把 stopPropagation 内置好(挂在 openProps 的卡片/行上点菜单不会顺带触发整行打开)。
// 落点: 项目卡片右上角、最近访问表的计划/对话行。第一批装「复制 id / 在编辑器打开」, 后续逐步加(plan audit 等)。
import React, { useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { MoreHorizontal } from 'lucide-react'

export interface KebabItem {
  label: string
  icon?: React.ReactNode
  onClick: () => void
  testid?: string
  danger?: boolean
  /** 暂不可用(置灰不可点) */
  disabled?: boolean
}

const ST = {
  trigger: {
    width: 24, height: 24, border: '1px solid var(--fp-border)', borderRadius: 5, background: 'transparent',
    color: '#8b98a8', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0,
  } as React.CSSProperties,
  triggerOpen: { color: 'var(--fp-text)', borderColor: '#3a4d61', background: '#16202b' } as React.CSSProperties,
  overlay: { position: 'fixed', inset: 0, zIndex: 79 } as React.CSSProperties,
  menu: {
    position: 'fixed', zIndex: 80, minWidth: 188, border: '1px solid var(--fp-border)', borderRadius: 6,
    background: 'var(--fp-solid)', boxShadow: '0 18px 40px rgba(0,0,0,.5)', padding: 6, display: 'flex', flexDirection: 'column', gap: 2,
  } as React.CSSProperties,
  item: {
    display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', border: 0, background: 'transparent',
    color: 'var(--fp-text)', borderRadius: 5, padding: '8px 9px', fontSize: 14, cursor: 'pointer', whiteSpace: 'nowrap',
  } as React.CSSProperties,
}

/** 三点更多菜单。items 为空则不渲染。triggerStyle 可覆盖触发钮外观(卡片上用半透明深底)。 */
export default function KebabMenu({ items, testid, triggerStyle, iconSize = 15 }: {
  items: KebabItem[]
  testid?: string
  triggerStyle?: React.CSSProperties
  iconSize?: number
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 })
  const btnRef = useRef<HTMLButtonElement>(null)
  if (!items || items.length === 0) return null

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    if (open) { setOpen(false); return }
    const r = btnRef.current?.getBoundingClientRect()
    if (r) {
      const W = typeof window !== 'undefined' ? window.innerWidth : 1200
      const H = typeof window !== 'undefined' ? window.innerHeight : 800
      const menuW = 200
      const menuH = items.length * 35 + 12
      let left = r.right - menuW
      if (left < 8) left = 8
      if (left + menuW > W - 8) left = W - 8 - menuW
      let top = r.bottom + 4
      if (top + menuH > H - 8) top = Math.max(8, r.top - menuH - 4)
      setPos({ left, top })
    }
    setOpen(true)
  }

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        style={{ ...ST.trigger, ...(open ? ST.triggerOpen : null), ...triggerStyle }}
        title="更多"
        aria-label="更多"
        data-testid={testid || 'kebab-trigger'}
        onClick={toggle}
      >
        <MoreHorizontal size={iconSize} />
      </button>
      {open && typeof document !== 'undefined' && createPortal(
        // 菜单挂到 body: 卡片包裹层是 position:absolute+zIndex:2(自成层叠上下文)且卡片
        // overflow:hidden — 内联渲染会被后续卡片的按钮盖住、被裁切, 且祖先若有 transform 还会
        // 让 position:fixed 错位。portal 到 body 后逃出所有层叠上下文/裁切/transform 包含块,
        // fixed + 触发钮 rect 定位才真正贴住按钮(对齐 Tooltip/ThinkingModeSelector 已验证写法)。
        <>
          <div style={ST.overlay} data-omni-capture-ignore="true" onClick={(e) => { e.stopPropagation(); setOpen(false) }} />
          <div style={{ ...ST.menu, left: pos.left, top: pos.top }} data-testid={testid ? `${testid}-menu` : 'kebab-menu'} data-omni-capture-ignore="true" onClick={(e) => e.stopPropagation()}>
            {items.map((it, i) => (
              <button
                key={it.testid || `${it.label}-${i}`}
                type="button"
                style={{ ...ST.item, ...(it.danger ? { color: '#ff8a80' } : null), ...(it.disabled ? { color: '#5a6573', cursor: 'default' } : null) }}
                data-testid={it.testid}
                disabled={it.disabled}
                onClick={(e) => { e.stopPropagation(); if (it.disabled) return; setOpen(false); it.onClick() }}
              >
                {it.icon}<span>{it.label}</span>
              </button>
            ))}
          </div>
        </>,
        document.body,
      )}
    </>
  )
}
