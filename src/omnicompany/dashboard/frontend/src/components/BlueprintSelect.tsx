// 蓝图 G 单选下拉(BlueprintSelect)—— 原生 <select> 的替代件。
//
// 为什么必须自绘: 原生 select 展开后的 <option> 弹层由操作系统/浏览器渲染, CSS 完全够不着,
// 于是在深色下渲染成一块系统黑 —— 用户原话「黑色下拉框」。收起态再怎么描边也救不回来,
// 唯一解是自己画弹层。(旧 index.css 里那条 .dark select 规则现在只作未迁移处的兜底。)
//
// 形态口径(与 PickerMenu 同族, 差别只在单选/多选):
//   · 收起态 = 虚线测量件(G: 输入/筛选件一律 dashed), 打字机标签(mono/大写/字距),
//     右端 chevron; focus/展开 = 实线 + 黄铜环。
//   · 弹层   = 描图纸 tracing + blur + 虚线边 + 纸叠硬影 + 右上微卷角(纯 CSS 假折角)。
//   · 选中行 = 45° 剖面 hatch(结构标记, 不是色块)+ 白勾。
// 键盘: Enter/Space/↓ 开, ↑↓ 移动, Enter/Space 选, Esc 关, Tab 关并放行, 首字母跳转。
// 无障碍: button[role=combobox] + listbox/option, aria-activedescendant 跟随高亮。
import React, { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'

export interface BlueprintSelectOption {
  value: string
  label: string
  /** 右端次要信息(计数/时间/来源), 用 mono 弱色排在行尾。 */
  hint?: string
  /** 行首图标(通常是 provider logo), 同时显示在收起态按钮上。 */
  icon?: React.ReactNode
  disabled?: boolean
}

export interface BlueprintSelectProps {
  value: string
  onChange: (value: string) => void
  options: BlueprintSelectOption[]
  /** 无选中时显示的占位(默认取第一项 label)。 */
  placeholder?: string
  /** 弹层顶部的分组标题; 同时作为 aria-label 兜底。 */
  label?: string
  'aria-label'?: string
  title?: string
  disabled?: boolean
  /** 收起态按钮的额外样式(宽度/flex 等排版层的事交给调用方)。 */
  style?: React.CSSProperties
  className?: string
  'data-testid'?: string
  /** 弹层最小宽度; 默认跟随按钮实宽。 */
  menuMinWidth?: number
}

// 2026-09-01: 弹层从 position:absolute 改成 portal 到 body + position:fixed。
// 原因是实证 bug —— 收起态按钮常常坐在带 overflow:auto 的滚动容器里(Todo 页的面板体、
// 命令条的横滑工具行都是), absolute 弹层会被祖先的 overflow 直接裁掉一半, 长选项列表
// 更是只剩一条缝。portal 到 body 逃出一切 overflow 裁切与层叠上下文, fixed + 按钮 rect
// 定位(越界翻上/夹边)才贴得住 —— 与 KebabMenu / HoverCard 已验证的写法同源。
const POP: React.CSSProperties = {
  position: 'fixed', zIndex: 60,
  background: 'var(--fp-bp-tracing-strong)',
  backdropFilter: 'var(--fp-bp-tracing-blur)',
  WebkitBackdropFilter: 'var(--fp-bp-tracing-blur)',
  border: '1px dashed var(--fp-border-strong)',
  borderRadius: 'var(--fp-bp-r2)',
  boxShadow: 'var(--fp-bp-shadow-pop)',
  padding: 5,
  // 拉长: CLI 列表十几个 provider + logo 行, 一屏放下(原 320 只够 ~8 行)
  maxHeight: 520,
  overflowY: 'auto',
}

// 描图纸右上微卷角(与 PickerMenu 同一配方)
const FOLD: React.CSSProperties = {
  position: 'absolute', top: 0, right: 0, width: 14, height: 14, pointerEvents: 'none',
  background: 'linear-gradient(225deg, var(--fp-bg) 0 48%, rgba(235,245,255,.35) 50%, transparent 62%)',
}

export function BlueprintSelect({
  value,
  onChange,
  options,
  placeholder,
  label,
  title,
  disabled,
  style,
  className,
  menuMinWidth,
  'aria-label': ariaLabel,
  'data-testid': testId,
}: BlueprintSelectProps) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const rootRef = useRef<HTMLSpanElement | null>(null)
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const baseId = useId()
  const [pos, setPos] = useState<{ left: number; top: number; width: number }>({ left: -9999, top: -9999, width: 160 })

  const selectedIndex = useMemo(
    () => options.findIndex((o) => o.value === value),
    [options, value],
  )
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined
  const shown = selected?.label ?? placeholder ?? options[0]?.label ?? ''

  const close = useCallback((refocus = true) => {
    setOpen(false)
    setActive(-1)
    if (refocus) btnRef.current?.focus()
  }, [])

  // 展开时把高亮落在当前值上, 键盘用户不必从头数起
  useEffect(() => {
    if (open) setActive(selectedIndex >= 0 ? selectedIndex : 0)
  }, [open, selectedIndex])

  // 弹层已 portal 出去, 定位只能自己算: 贴按钮下方, 放不下翻到上方, 左右夹在视口内。
  // 滚动/缩放时跟着重算(而不是收起) —— 面板体本身是滚动容器, 一滚就关会很难用。
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect()
      if (!r) return
      const vw = window.innerWidth
      const vh = window.innerHeight
      const width = Math.max(menuMinWidth ?? r.width, 160)
      const h = listRef.current?.offsetHeight ?? 220
      let left = r.left
      if (left + width > vw - 8) left = Math.max(8, vw - 8 - width)
      let top = r.bottom + 6
      if (top + h > vh - 8) top = Math.max(8, r.top - h - 6)
      setPos({ left, top, width })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, menuMinWidth, options.length])

  // 外点/失焦收起。用 capture 阶段, 避免被业务层的 stopPropagation 吃掉。
  // 弹层不在 rootRef 里了(portal), 命中判断要把它一起算上, 否则点选项会先被判成"外点"。
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!(e.target instanceof Node)) return
      if (rootRef.current?.contains(e.target)) return
      if (listRef.current?.contains(e.target)) return
      close(false)
    }
    document.addEventListener('mousedown', onDown, true)
    return () => document.removeEventListener('mousedown', onDown, true)
  }, [open, close])

  // 高亮项滚进可视区。jsdom 没有实现 scrollIntoView, 不判存在会在单测里直接抛错炸掉整棵树。
  useEffect(() => {
    if (!open || active < 0) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)
    if (typeof el?.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' })
  }, [open, active])

  const pick = (idx: number) => {
    const o = options[idx]
    if (!o || o.disabled) return
    onChange(o.value)
    close()
  }

  const step = (dir: 1 | -1) => {
    if (!options.length) return
    let next = active
    for (let i = 0; i < options.length; i += 1) {
      next = (next + dir + options.length) % options.length
      if (!options[next]?.disabled) break
    }
    setActive(next)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return
    if (!open) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setOpen(true)
      }
      return
    }
    switch (e.key) {
      case 'Escape': e.preventDefault(); e.stopPropagation(); close(); break
      case 'Tab': close(false); break
      case 'ArrowDown': e.preventDefault(); step(1); break
      case 'ArrowUp': e.preventDefault(); step(-1); break
      case 'Home': e.preventDefault(); setActive(0); break
      case 'End': e.preventDefault(); setActive(options.length - 1); break
      case 'Enter':
      case ' ': e.preventDefault(); pick(active); break
      default: {
        // 首字母跳转(单字符可打印键)
        if (e.key.length !== 1 || e.ctrlKey || e.metaKey || e.altKey) break
        const k = e.key.toLowerCase()
        const from = active + 1
        const hit = options.findIndex((o, i) =>
          i >= from && !o.disabled && o.label.toLowerCase().startsWith(k))
        const wrap = hit >= 0 ? hit
          : options.findIndex((o) => !o.disabled && o.label.toLowerCase().startsWith(k))
        if (wrap >= 0) { e.preventDefault(); setActive(wrap) }
      }
    }
  }

  return (
    <span
      className={className ? `bp-select ${className}` : 'bp-select'}
      ref={rootRef}
      style={style}
      data-testid={testId}
    >
      <button
        ref={btnRef}
        type="button"
        className="bps-btn"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? `${baseId}-list` : undefined}
        aria-activedescendant={open && active >= 0 ? `${baseId}-o${active}` : undefined}
        aria-label={ariaLabel ?? label}
        title={title ?? ariaLabel ?? label}
        disabled={disabled}
        onClick={() => !disabled && setOpen((v) => !v)}
        onKeyDown={onKeyDown}
      >
        <span className="bps-v">{shown}</span>
        <ChevronDown className="bps-chev" size={12} aria-hidden />
      </button>
      {open && typeof document !== 'undefined' && createPortal(
        <div
          id={`${baseId}-list`}
          ref={listRef}
          role="listbox"
          aria-label={ariaLabel ?? label}
          data-omni-capture-ignore="true"
          style={{ ...POP, left: pos.left, top: pos.top, minWidth: pos.width }}
          onKeyDown={onKeyDown}
        >
          <span style={FOLD} aria-hidden="true" />
          {label && <div className="bps-h">{label}</div>}
          {options.map((o, i) => (
            <button
              key={o.value}
              id={`${baseId}-o${i}`}
              data-idx={i}
              type="button"
              role="option"
              className="v2-checkrow bps-o"
              aria-selected={o.value === value}
              data-active={i === active || undefined}
              disabled={o.disabled}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(i)}
            >
              <span className="cb" aria-hidden="true"><Check size={11} strokeWidth={3} /></span>
              <span className="bps-o-icon" aria-hidden="true">{o.icon ?? null}</span>
              <span className="cr-t">{o.label}</span>
              {o.hint != null && <span className="cr-d">{o.hint}</span>}
            </button>
          ))}
          {options.length === 0 && <div className="bps-empty">无可选项</div>}
        </div>,
        document.body,
      )}
    </span>
  )
}

export default BlueprintSelect
