// 任务动作菜单 —— 右键任意一行/一张卡，或点行尾的 `⋯`，都出这一份。
//
// 为什么不直接用 shared/view/ui/KebabMenu：那个是「按钮下方弹出的扁平列表」，
// 没有光标定位、没有子菜单、没有选中态。任务的动作天然是分层的（状态 / 优先级 /
// 截止各是一组单选），成熟项目管理产品（Linear、Plane、Focalboard、Vikunja）的
// 右键菜单也都是这个形状。定位/portal/遮罩关闭这些机制沿用 KebabMenu 已验证的写法：
// portal 到 body 逃出层叠上下文与 overflow 裁切，fixed 定位 + 越界翻转夹边。

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronRight } from 'lucide-react'

export interface MenuItem {
  kind?: 'item'
  label: string
  icon?: React.ReactNode
  hint?: string
  danger?: boolean
  disabled?: boolean
  /** 置灰时鼠标悬停给出的原因 —— 不让人对着灰按钮猜。 */
  disabledReason?: string
  onClick: () => void
  testid?: string
}

export interface MenuGroup {
  kind: 'group'
  label: string
  icon?: React.ReactNode
  /** 当前值，用于在子菜单里打勾。 */
  value?: string
  options: { value: string; label: string; onClick: () => void }[]
  testid?: string
}

export interface MenuSep { kind: 'sep' }

export type MenuEntry = MenuItem | MenuGroup | MenuSep

export interface MenuAnchor { x: number; y: number }

const MENU_W = 216
const SUB_W = 184

const S = {
  overlay: { position: 'fixed', inset: 0, zIndex: 79 } as React.CSSProperties,
  menu: {
    position: 'fixed', zIndex: 80, minWidth: MENU_W,
    border: '1px solid var(--fp-border)', borderRadius: 'var(--fp-bp-r2)',
    background: 'var(--fp-bp-solid)', boxShadow: 'var(--fp-bp-shadow-pop)',
    padding: 5, display: 'flex', flexDirection: 'column', gap: 1,
  } as React.CSSProperties,
  sub: {
    position: 'fixed', zIndex: 81, minWidth: SUB_W,
    border: '1px solid var(--fp-border)', borderRadius: 'var(--fp-bp-r2)',
    background: 'var(--fp-bp-solid)', boxShadow: 'var(--fp-bp-shadow-pop)',
    padding: 5, display: 'flex', flexDirection: 'column', gap: 1,
  } as React.CSSProperties,
}

export function TaskMenu({ anchor, entries, onClose }: {
  anchor: MenuAnchor
  entries: MenuEntry[]
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ left: -9999, top: -9999 })
  const [openSub, setOpenSub] = useState<{ index: number; left: number; top: number } | null>(null)

  // 先量真实高度再夹边：条目数不定，按估算高度翻转会在长菜单上出界。
  useLayoutEffect(() => {
    const h = ref.current?.offsetHeight || 240
    const W = window.innerWidth
    const H = window.innerHeight
    let left = anchor.x
    let top = anchor.y
    if (left + MENU_W > W - 8) left = Math.max(8, anchor.x - MENU_W)
    if (top + h > H - 8) top = Math.max(8, H - 8 - h)
    setPos({ left, top })
  }, [anchor.x, anchor.y, entries.length])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('scroll', onClose, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('scroll', onClose, true)
    }
  }, [onClose])

  if (typeof document === 'undefined') return null

  return createPortal(
    <>
      <div
        style={S.overlay}
        data-omni-capture-ignore="true"
        onClick={(e) => { e.stopPropagation(); onClose() }}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onClose() }}
      />
      <div
        ref={ref}
        style={{ ...S.menu, left: pos.left, top: pos.top }}
        className="tk-menu"
        role="menu"
        data-testid="tk-menu"
        data-omni-capture-ignore="true"
        onClick={(e) => e.stopPropagation()}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation() }}
      >
        {entries.map((entry, i) => {
          if ('kind' in entry && entry.kind === 'sep') return <span key={`s${i}`} className="tk-menu-sep" />
          if ('kind' in entry && entry.kind === 'group') {
            return (
              <button
                key={entry.label}
                type="button"
                className="tk-menu-item"
                role="menuitem"
                aria-haspopup="true"
                aria-expanded={openSub?.index === i}
                data-open={openSub?.index === i ? '1' : undefined}
                data-testid={entry.testid}
                onMouseEnter={(e) => {
                  const r = e.currentTarget.getBoundingClientRect()
                  const subH = entry.options.length * 30 + 12
                  let left = r.right + 4
                  if (left + SUB_W > window.innerWidth - 8) left = Math.max(8, r.left - SUB_W - 4)
                  const top = Math.max(8, Math.min(r.top, window.innerHeight - 8 - subH))
                  setOpenSub({ index: i, left, top })
                }}
                onClick={(e) => {
                  // 触屏没有 hover：点一下也要能展开。
                  const r = e.currentTarget.getBoundingClientRect()
                  setOpenSub((cur) => (cur?.index === i ? null : { index: i, left: r.right + 4, top: r.top }))
                }}
              >
                {entry.icon}
                <span className="tk-menu-label">{entry.label}</span>
                <span className="tk-menu-hint">{entry.options.find((o) => o.value === entry.value)?.label}</span>
                <ChevronRight size={12} aria-hidden />
              </button>
            )
          }
          const item = entry as MenuItem
          return (
            <button
              key={item.label}
              type="button"
              className="tk-menu-item"
              role="menuitem"
              data-danger={item.danger ? '1' : undefined}
              data-testid={item.testid}
              disabled={item.disabled}
              title={item.disabled ? item.disabledReason : undefined}
              onMouseEnter={() => setOpenSub(null)}
              onClick={() => { onClose(); item.onClick() }}
            >
              {item.icon}
              <span className="tk-menu-label">{item.label}</span>
              {item.hint && <span className="tk-menu-hint">{item.hint}</span>}
            </button>
          )
        })}
      </div>

      {openSub && (() => {
        const group = entries[openSub.index] as MenuGroup
        return (
          <div
            style={{ ...S.sub, left: openSub.left, top: openSub.top }}
            className="tk-menu"
            role="menu"
            data-testid="tk-submenu"
            data-omni-capture-ignore="true"
            onClick={(e) => e.stopPropagation()}
          >
            {group.options.map((o) => (
              <button
                key={o.value}
                type="button"
                className="tk-menu-item"
                role="menuitemradio"
                aria-checked={o.value === group.value}
                data-testid={`tk-menu-opt-${o.value}`}
                onClick={() => { onClose(); o.onClick() }}
              >
                <span className="tk-menu-check">{o.value === group.value ? <Check size={12} /> : null}</span>
                <span className="tk-menu-label">{o.label}</span>
              </button>
            ))}
          </div>
        )
      })()}
    </>,
    document.body,
  )
}
