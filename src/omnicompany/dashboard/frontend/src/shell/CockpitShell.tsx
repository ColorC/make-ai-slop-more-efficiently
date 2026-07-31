import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bell,
  Camera,
  Command,
  Copy,
  Crosshair,
  Maximize2,
  Menu,
  Minimize2,
  Network,
  PanelBottom,
  PanelRightOpen,
  Pin,
  RefreshCw,
  Send,
  X,
} from 'lucide-react'
import EditorArea from './EditorArea'
import BottomPanel from './BottomPanel'
import { HSplitter } from './Splitter'
import { ShellRail, SPINE, type SpineKey } from './ShellRail'
import { copyText } from '../lib/copyText'
import { useRefreshBus } from '../stores/refreshBus'
import { useReviewMaximize } from '../stores/reviewMaximizeStore'
import { bossSightApi, type BossSightBriefing, type BossSightWorkflowCtxSummary } from '../api/bossSightClient'
import { reviewstageApi, type Material, type MaterialStats, type ReviewCaptureKind } from '../api/reviewstageClient'
import { capturesApi } from '../api/capturesClient'
import { CONTROLLER_TAB_ID, usePanels, saveTabSnapshot, loadTabSnapshot, type DockPlacement, type OpenedTab, type TabSnapshot } from '../stores/panelsStore'
import { useReviewQueueFocus } from '../stores/reviewQueueFocusStore'
import type { EntityType } from '../entities/types'
import { useControllerView } from '../entities/controller/viewStore'
import { useReviewStream } from '../entities/review/streamStore'
import { materialTabTitle } from '../entities/review_material'
import { StudioDemoMount } from '../entities/review-canvas'
import { useBossSightObservability } from './useBossSightObservability'
import { openProps } from '../utils/middleClick'
import Tooltip from '../shared/view/ui/Tooltip'
import { useBreakpoint, useTouchMode } from './useBreakpoint'
import { UI_UPDATE_READY_EVENT, isUiUpdatePending } from '../lib/devReload'

// 2026-07-19 壳层 A(侧栏主导,拍板=demo #/shell 候选 A):导航四套收编进左 56px rail
// (悬停展开带文字,ShellRail.tsx);顶栏压成 28–32px 薄条(面包屑 + ⌘K + 通知 + ⋯);
// 全局搜索改 ⌘K 命令面板(复用 kbar CommandPalette);评论右栏抽屉化默认关;断点:
// ≥600 显示 rail(<1024 即收起态), <600 rail 隐藏走 V1 汉堡抽屉。
type OpenTab = ReturnType<typeof usePanels.getState>['openTab']

type CaptureMode = 'element_comment' | 'debug_start' | null
type ElementTarget = {
  selector: string
  label: string
  tag: string
  id?: string
  testid?: string
  role?: string
  text?: string
  form_values?: {
    selector: string
    tag: string
    id?: string
    name?: string
    label?: string
    value?: string
    checked?: boolean
  }[]
  rect: { x: number; y: number; width: number; height: number }
  page_rect: { x: number; y: number; width: number; height: number }
  outer_html?: string
}
type CaptureDialogState = {
  kind: ReviewCaptureKind
  title: string
  target?: ElementTarget
  debugAllowed?: boolean
}

const S: Record<string, any> = {
  root: { position: 'relative' as const, display: 'flex', flexDirection: 'column', height: '100vh', background: 'transparent', color: 'var(--fp-text)', minWidth: 0 },
  popover: { position: 'fixed' as const, zIndex: 30, top: 44, left: 64, width: 320, maxWidth: 'calc(100vw - 76px)', maxHeight: '70vh', overflow: 'auto', border: '1px dashed var(--fp-border-strong)', borderRadius: 3, background: 'var(--fp-glass-2)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)', boxShadow: 'var(--fp-shadow-pop)', padding: 8 },
  panelTitle: { color: 'var(--fp-text)', fontSize: 14, fontWeight: 700, padding: '4px 6px 8px' },
  resultButton: { width: '100%', display: 'grid', gap: 3, border: '1px solid transparent', background: 'transparent', color: 'var(--fp-text)', borderRadius: 3, padding: '7px 8px', cursor: 'pointer', textAlign: 'left' as const, minWidth: 0 },
  smallAction: { border: '1px solid var(--fp-border)', background: 'var(--fp-card)', color: 'var(--fp-text-2)', borderRadius: 3, padding: '5px 7px', fontSize: 14, cursor: 'pointer' },
  iconButton: { width: 32, height: 32, border: '1px solid var(--fp-border)', borderRadius: 3, background: 'var(--fp-card)', color: 'var(--fp-text-2)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0 },
  activeIconButton: { width: 32, height: 32, border: '1px solid var(--fp-border-strong)', borderRadius: 3, background: 'var(--fp-accent-weak)', color: 'var(--fp-text)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0 },
  railMeta: { color: 'var(--fp-bp-brass-hi)', fontSize: 12, fontFamily: 'var(--fp-font-mono)' },
  main: { minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  editor: { flex: 1, minHeight: 0, position: 'relative' as const },
  row: { display: 'grid', gap: 3, padding: '7px 0', borderBottom: '1px solid var(--fp-border-subtle)', minWidth: 0 },
  rowTitle: { fontSize: 14, color: 'var(--fp-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  rowMeta: { fontSize: 14, color: 'var(--fp-text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  bottom: (h: number): React.CSSProperties => ({ height: h, minHeight: 90, maxHeight: '65vh', borderTop: '1px solid var(--fp-border)' }),
  error: { color: '#ff8a80', fontSize: 14 },
  captureBanner: { position: 'fixed' as const, zIndex: 80, top: 38, left: '50%', transform: 'translateX(-50%)', display: 'inline-flex', alignItems: 'center', gap: 8, border: '1px dashed var(--fp-border-strong)', background: 'var(--fp-bp-tracing-strong)', color: 'var(--fp-text)', borderRadius: 2, padding: '8px 12px', backdropFilter: 'var(--fp-bp-tracing-blur)', WebkitBackdropFilter: 'var(--fp-bp-tracing-blur)', boxShadow: 'var(--fp-bp-shadow-pop)', font: '600 var(--fp-fs-3)/1.2 var(--fp-font-mono)', letterSpacing: '.04em' },
  captureOutline: (rect: ElementTarget['rect'], selected = false): React.CSSProperties => ({
    position: 'fixed', zIndex: selected ? 1 : 79,
    left: rect.x, top: rect.y, width: Math.max(1, rect.width), height: Math.max(1, rect.height),
    border: '2px solid var(--fp-bp-doc-ink)',
    outline: selected ? '1px solid var(--fp-bp-brass-hi)' : '9999px solid rgba(4,12,28,.14)',
    background: selected ? 'color-mix(in srgb, var(--fp-bp-brass) 14%, transparent)' : 'transparent',
    boxShadow: '0 0 0 1px var(--fp-bp-brass-hi), 3px 3px 0 rgba(6,16,38,.48)',
    pointerEvents: 'none', boxSizing: 'border-box',
  }),
  modalBackdrop: { position: 'fixed' as const, zIndex: 90, inset: 0, background: 'rgba(4,12,28,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 },
  modal: { position: 'relative' as const, zIndex: 2, width: 'min(560px, 100%)', border: 'var(--fp-bp-frame-w) solid var(--fp-border-strong)', borderRadius: 2, background: 'var(--fp-bp-paper)', color: 'var(--fp-text)', boxShadow: 'var(--fp-bp-shadow-pop)', padding: 16 },
  modalHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 10 },
  modalTitle: { color: 'var(--fp-text)', fontSize: 16, fontWeight: 700, fontFamily: 'var(--fp-bp-font-display)' },
  modalMeta: { color: 'var(--fp-text-2)', fontSize: 12, marginBottom: 10, padding: '7px 9px', border: '1px dashed var(--fp-border)', background: 'var(--fp-bp-solid)', fontFamily: 'var(--fp-font-mono)', overflowWrap: 'anywhere' as const },
  textArea: { width: '100%', minHeight: 110, resize: 'vertical' as const, boxSizing: 'border-box' as const, border: '1px dashed var(--fp-border)', outline: 'none', background: 'var(--fp-bp-solid)', color: 'var(--fp-text)', caretColor: 'var(--fp-bp-brass-hi)', borderRadius: 2, padding: 10, fontSize: 14, lineHeight: 1.55 },
  modalActions: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 },
  primaryAction: { border: '1px solid rgba(255,240,235,.55)', background: 'var(--fp-bp-seal)', color: '#fff', borderRadius: 3, padding: '7px 10px', fontSize: 14, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 },
  toast: { position: 'fixed' as const, zIndex: 95, right: 14, top: 38, border: '1px solid color-mix(in srgb, var(--fp-ok) 45%, transparent)', background: 'var(--fp-solid)', color: 'var(--fp-ok)', borderRadius: 3, padding: '8px 10px', fontSize: 14, boxShadow: 'var(--fp-shadow-pop)' },
  pathMenu: (x: number, y: number): React.CSSProperties => ({ position: 'fixed', zIndex: 96, left: Math.min(x, (typeof window !== 'undefined' ? window.innerWidth : 1200) - 280), top: Math.min(y, (typeof window !== 'undefined' ? window.innerHeight : 800) - 200), width: 264, border: '1px solid var(--fp-border-strong)', borderRadius: 3, background: 'var(--fp-solid)', boxShadow: 'var(--fp-shadow-pop)', padding: 6 }),
  pathMenuPath: { color: 'var(--fp-text)', fontSize: 14, padding: '4px 6px 6px', overflowWrap: 'anywhere' as const, borderBottom: '1px solid var(--fp-border-subtle)', marginBottom: 4 },
  pathMenuItem: { display: 'block', width: '100%', textAlign: 'left' as const, border: 0, background: 'transparent', color: 'var(--fp-text)', borderRadius: 3, padding: '7px 8px', fontSize: 14, cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  moreMenu: { position: 'fixed' as const, zIndex: 40, left: 64, bottom: 56, minWidth: 200, border: '1px dashed var(--fp-border-strong)', borderRadius: 3, background: 'var(--fp-glass-2)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)', boxShadow: 'var(--fp-shadow-pop)', padding: 6, display: 'flex', flexDirection: 'column' as const, gap: 2 },
  moreItem: { display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left' as const, border: 0, background: 'transparent', color: 'var(--fp-text)', borderRadius: 3, padding: '8px 9px', fontSize: 14, cursor: 'pointer', whiteSpace: 'nowrap' as const },
  navDrawerSep: { borderTop: '1px solid var(--fp-border-subtle)', margin: '8px 4px' },
  // M3 窄屏形态: 抽屉(导航/评论)共用的 scrim 与浮层。zIndex: 抽屉 20 > scrim 18 > 内容, 点 scrim 关闭。
  scrim: { position: 'fixed' as const, inset: 0, zIndex: 18, background: 'rgba(4,12,28,.5)' },
  navDrawer: { position: 'fixed' as const, top: 0, left: 0, bottom: 0, width: 'min(84vw, 300px)', zIndex: 20, background: 'var(--fp-solid)', borderRight: '2px solid rgba(235,245,255,.4)', boxShadow: 'var(--fp-shadow-pop)', padding: 10, overflow: 'auto' as const },
  navDrawerItem: (active: boolean): React.CSSProperties => ({
    width: '100%', minHeight: 48, display: 'flex', alignItems: 'center', gap: 11, padding: '0 12px', marginBottom: 3,
    border: `1px solid ${active ? 'var(--fp-border-strong)' : 'transparent'}`, background: active ? 'var(--fp-bp-hatch)' : 'transparent',
    color: active ? 'var(--fp-text)' : 'var(--fp-text-2)', borderRadius: 3, cursor: 'pointer', fontSize: 14,
    fontWeight: active ? 700 : 500, textAlign: 'left' as const,
  }),
}

function tone(status: string): string {
  if (status === 'blocked' || status === 'critical') return 'critical'
  if (status === 'attention' || status === 'action_failed' || status === 'todo_open') return 'attention'
  return 'calm'
}

function clipText(text: string, limit: number): string {
  if (text.length <= limit) return text
  return `${text.slice(0, limit)}\n\n[truncated: ${text.length - limit} chars omitted]`
}

// #3f 右键把聊天里的文件路径当审阅材料: 先判断选中的文字像不像一条文件路径。
// 去掉包裹的引号/反引号; 绝对路径(盘符 / UNC / 斜杠开头)直接算; 相对路径要有分隔符 + 扩展名。
function stripPathSelection(raw: string): string {
  return raw.trim().replace(/^[`"']+/, '').replace(/[`"']+$/, '').trim()
}
function looksLikeFilePath(value: string): boolean {
  const t = stripPathSelection(value)
  if (!t || t.length > 1000 || /[\n\r]/.test(t)) return false
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(t) || t.startsWith('mailto:') || t.startsWith('data:')) return false
  if (/^[A-Za-z]:[\\/]/.test(t) || t.startsWith('\\\\') || t.startsWith('/')) return true
  return /[\\/]/.test(t) && /\.[A-Za-z0-9]{1,8}(?::\d+){0,2}$/.test(t)
}

function cssEscape(value: string): string {
  const css = (window as any).CSS
  if (css?.escape) return css.escape(value)
  return value.replace(/["\\#.:()[\] >+~]/g, '\\$&')
}

function compactText(value: string | null | undefined, limit = 220): string {
  return clipText(String(value || '').replace(/\s+/g, ' ').trim(), limit)
}

// 边栏收起状态持久化(localStorage), 刷新后保持。'1'=展开, '0'=收起。
function readPref(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback
  try {
    const v = window.localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch {
    return fallback
  }
}
function writePref(key: string, value: boolean): void {
  if (typeof window === 'undefined') return
  try { window.localStorage.setItem(key, value ? '1' : '0') } catch { /* ignore */ }
}

// 复制到剪贴板: 全站唯一抽象在 lib/copyText (含 VSCode webview 宿主桥第三级降级)。
const copyToClipboard = copyText

function selectorForElement(el: Element): string {
  const testid = el.getAttribute('data-testid')
  if (testid) return `[data-testid="${cssEscape(testid)}"]`
  if (el.id) return `#${cssEscape(el.id)}`
  const parts: string[] = []
  let cur: Element | null = el
  while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
    const tag = cur.tagName.toLowerCase()
    const parent: Element | null = cur.parentElement
    if (!parent) {
      parts.unshift(tag)
      break
    }
    const siblings = Array.from(parent.children) as Element[]
    const curTag = cur.tagName
    const sameTag = siblings.filter((child: Element) => child.tagName === curTag)
    const index = sameTag.indexOf(cur) + 1
    parts.unshift(sameTag.length > 1 ? `${tag}:nth-of-type(${index})` : tag)
    if (parts.length >= 6 || cur.getAttribute('data-testid') || cur.id) break
    cur = parent
  }
  return parts.join(' > ') || el.tagName.toLowerCase()
}

function isCaptureIgnored(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest('[data-omni-capture-ignore="true"]'))
}

// 纯文本叶子标签: 这些里若没有交互后代, 圈选时选它本身, 不上吸到最近的 [data-testid] 整块。
const TEXT_LEAF_TAGS = new Set(['span', 'small', 'strong', 'em', 'b', 'i', 'p', 'label', 'code'])

function captureElementFromTarget(el: Element): Element {
  // 交互件(按钮/链接/表单/带 role)内 → 上吸到该交互件(选整个按钮更有用, 维持原行为)。
  const interactive = el.closest('button, a, input, textarea, select, [role]')
  if (interactive) return interactive
  // 非交互件内的纯文本叶子 → 选它本身, 不再上吸到 [data-testid] 整块。
  // 这样能圈中"重要 · 待审"这类细粒度文字(2026-06-14 用户上报: 被上吸成整块 material-detail)。
  if (TEXT_LEAF_TAGS.has(el.tagName.toLowerCase())) return el
  return el.closest('[data-testid]') || el
}

function describeFormValues(el: Element): ElementTarget['form_values'] {
  const controls = [
    ...(el.matches('input, textarea, select') ? [el] : []),
    ...Array.from(el.querySelectorAll('input, textarea, select')),
  ].slice(0, 20) as Array<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  return controls.map((control) => {
    const item: NonNullable<ElementTarget['form_values']>[number] = {
      selector: selectorForElement(control),
      tag: control.tagName.toLowerCase(),
      id: control.id || undefined,
      name: control.getAttribute('name') || undefined,
      label: compactText(
        control.getAttribute('aria-label') ||
        control.getAttribute('title') ||
        control.getAttribute('placeholder') ||
        control.getAttribute('name') ||
        control.id ||
        control.tagName.toLowerCase(),
        160,
      ),
      value: clipText(control.value || '', 8000),
    }
    if (control.tagName.toLowerCase() === 'input' && (control.type === 'checkbox' || control.type === 'radio')) {
      item.checked = (control as HTMLInputElement).checked
    }
    return item
  })
}

function describeElement(el: Element): ElementTarget {
  const rect = el.getBoundingClientRect()
  const text = compactText((el as HTMLElement).innerText || el.textContent || '', 500)
  const label = compactText(
    el.getAttribute('aria-label') ||
    el.getAttribute('title') ||
    el.getAttribute('data-testid') ||
    text ||
    el.tagName.toLowerCase(),
    160,
  )
  return {
    selector: selectorForElement(el),
    label,
    tag: el.tagName.toLowerCase(),
    id: el.id || undefined,
    testid: el.getAttribute('data-testid') || undefined,
    role: el.getAttribute('role') || undefined,
    text,
    form_values: describeFormValues(el),
    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    page_rect: { x: rect.x + window.scrollX, y: rect.y + window.scrollY, width: rect.width, height: rect.height },
    outer_html: clipText(el.outerHTML || '', 3000),
  }
}

function activeTabPayload(tab: OpenedTab | null): Record<string, unknown> {
  return {
    tab_id: tab?.id || CONTROLLER_TAB_ID,
    type: tab?.ref.type || 'controller',
    id: tab?.ref.id || 'main',
    facet: tab?.facet || null,
    title: tab?.title || 'BOSS SIGHT',
  }
}

function pageStatePayload(tab: OpenedTab | null): Record<string, unknown> {
  const doc = document.documentElement
  const body = document.body
  return {
    title: document.title,
    active_tab: activeTabPayload(tab),
    viewport: { width: window.innerWidth, height: window.innerHeight },
    scroll: { x: window.scrollX, y: window.scrollY },
    document: {
      width: Math.max(doc.scrollWidth, body?.scrollWidth || 0),
      height: Math.max(doc.scrollHeight, body?.scrollHeight || 0),
    },
  }
}

function openCockpitRef(openTab: OpenTab, ref?: any, fallbackTitle = '打开', placement?: DockPlacement) {
  if (!ref) return false
  if (ref.type === 'review_material' && ref.id) {
    // 2026-06-14: 材料链接直接开正文页签(B 区), 不再绕道已退役的 review_queue 两栏台。
    openTab({ type: 'review_material', id: String(ref.id) }, fallbackTitle, undefined, placement)
    return true
  }
  if (ref.url) {
    window.location.href = String(ref.url)
    return true
  }
  if (!ref.type || !ref.id) return false
  openTab({ type: ref.type as EntityType, id: String(ref.id) }, fallbackTitle, ref.facet, placement)
  return true
}

function NotificationPanel({ workflow, onOpenRef, onOpenRefBg }: {
  workflow: BossSightWorkflowCtxSummary | null
  onOpenRef: (ref: any, title: string) => void
  onOpenRefBg: (ref: any, title: string) => void
}) {
  const items = workflow?.unresolved || []
  const recent = workflow?.action_history?.recent || []
  return (
    <div style={S.popover} data-testid="cockpit-notification-panel">
      <div style={S.panelTitle}>通知</div>
      {items.length === 0 && recent.length === 0 && <div style={S.rowMeta}>没有待处理通知</div>}
      {items.map((item: any, index: number) => {
        const ref = item.open_ref || item.target?.open_ref
        return (
          <button
            key={`${item.id || item.reason}-${index}`}
            type="button"
            style={S.resultButton}
            data-testid={`cockpit-notification-item-${index}`}
            {...openProps(
              () => ref && onOpenRef(ref, item.title || item.reason || '通知'),
              () => ref && onOpenRefBg(ref, item.title || item.reason || '通知'),
            )}
          >
            <span style={S.rowTitle}>{item.title || item.reason || item.kind}</span>
            <span style={S.rowMeta}>{item.priority || 'info'} · {item.reason || item.kind}</span>
          </button>
        )
      })}
      {recent.slice(0, 5).map((event: any, index: number) => (
        <div key={`${event.id || event.kind}-${index}`} style={S.row}>
          <div style={S.rowTitle}>{event.kind}</div>
          <div style={S.rowMeta}>{event.status || 'event'} · {event.error || event.note || ''}</div>
        </div>
      ))}
    </div>
  )
}

// 2026-06-05 用户明示: 右侧"检视/工作流"面板与控制台+通知铃严重重复, 整块删除。
// 原 SelectedObjectPanel / WorkflowInspector / Metric 已移除; 待处理/工作流数据走通知铃+控制台。

// 左栏"审阅材料队列"已收敛为唯一一份共享 ReviewQueueSidebar(entities/review), 不再在此自实现
// mini 列表(2026-06-14 用户: 列表只一份, 别和审阅台页面内 sidebar 重复)。
// 原"某区在VSCode打开"小图标(focus-native-view)已撤(用户: 意义不明)—— "在 VSCode 打开"改为
// 落到具体条目(计划/文件真打开、对话开 claude 插件/codex 终端), 不再做区级切换按钮。

function CaptureDialog({ state, comment, busy, error, onComment, onSubmit, onCopy, onCancel, touch = false }: {
  state: CaptureDialogState
  comment: string
  busy: boolean
  error: string | null
  onComment: (value: string) => void
  onSubmit: () => void
  onCopy: () => void
  onCancel: () => void
  /** 触屏档: 壳层可点元素 ≥44(iconButton 32 → 44, 文本钮 minHeight 44)。 */
  touch?: boolean
}) {
  // 提交 = 保存到文件(攒着给总控整批读); 复制 = 纯剪贴板。都不进审阅队列。
  const submitLabel = '提交(存文件)'
  const copyLabel = state.kind === 'debug_start' ? '复制 + 标记调试起点' : '复制内容'
  const placeholder = state.kind === 'page_snapshot'
    ? '给这张页面快照写点备注(提交存文件 / 复制都会带上, 可选)'
    : state.kind === 'debug_start'
      ? '想让 Codex 看什么、指出什么?(提交存文件 / 复制都会带上)'
      : '给这个元素写备注(提交存文件 / 复制都会带上, 可选)'
  const touchTextBtn = touch ? { minHeight: 44 } : null
  return (
    <div style={S.modalBackdrop} data-omni-capture-ignore="true" data-testid="cockpit-capture-modal">
      {state.target && (
        <div
          style={S.captureOutline(state.target.rect, true)}
          aria-hidden="true"
          data-testid="cockpit-capture-selected-outline"
        />
      )}
      <div style={S.modal}>
        <div style={S.modalHeader}>
          <div style={S.modalTitle}>{state.title}</div>
          <button type="button" style={touch ? { ...S.iconButton, width: 44, height: 44 } : S.iconButton} onClick={onCancel} aria-label="关闭捕获对话框">
            <X size={15} />
          </button>
        </div>
        {state.target && (
          <div style={S.modalMeta} data-testid="cockpit-capture-target">
            {state.target.selector} / {state.target.label}
          </div>
        )}
        <textarea
          className="omni-capture-textarea"
          style={S.textArea}
          value={comment}
          placeholder={placeholder}
          onChange={(e) => onComment(e.target.value)}
          data-testid="cockpit-capture-comment"
          autoFocus
        />
        {error && <div style={{ ...S.error, marginTop: 8 }}>{error}</div>}
        <div style={S.modalActions}>
          <button type="button" style={{ ...S.smallAction, ...touchTextBtn }} onClick={onCancel} disabled={busy}>取消</button>
          <button type="button" style={{ ...S.smallAction, ...touchTextBtn }} onClick={onCopy} disabled={busy} data-testid="cockpit-capture-copy">
            <Copy size={13} style={{ verticalAlign: -2, marginRight: 4 }} />{copyLabel}
          </button>
          <button type="button" style={{ ...S.primaryAction, ...touchTextBtn }} onClick={onSubmit} disabled={busy} data-testid="cockpit-capture-submit">
            <Send size={14} /> {busy ? '处理中…' : submitLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// R4: standalone 审阅台退役后, 其"总控推送 toast"上移到驾驶舱壳 (吃 streamStore 的
// pushed 事件)。锚点 data-testid="push-toast" 沿用 standalone 的, 8s 自动消失。
function ReviewPushToast({ material, onOpen, onClose }: {
  material: Material
  onOpen: () => void
  onClose: () => void
}) {
  useEffect(() => {
    const t = window.setTimeout(onClose, 8000)
    return () => window.clearTimeout(t)
  }, [material.id, onClose])
  return (
    <div
      data-testid="push-toast"
      style={{
        position: 'fixed', bottom: 24, right: 24, padding: 16,
        background: 'var(--fp-solid)', border: '2px solid var(--fp-warn)',
        borderRadius: 8, minWidth: 300, maxWidth: 480,
        boxShadow: '0 4px 24px rgba(0,0,0,0.4)', zIndex: 1000, color: 'var(--fp-text)',
      }}
    >
      <div style={{ fontSize: 14, color: 'var(--fp-warn)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 }}><Pin size={13} aria-hidden /> 总控推送</div>
      <div style={{ fontSize: 15, fontWeight: 600, margin: '6px 0' }}>{material.title}</div>
      <div style={{ fontSize: 14, color: 'var(--fp-text-3)' }}>{material.pushed_reason}</div>
      <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
        <button type="button" onClick={onOpen} data-testid="push-toast-open" style={{
          padding: '4px 10px', background: 'var(--fp-accent-weak)', color: 'var(--fp-link)',
          border: '1px solid var(--fp-accent)', borderRadius: 4, cursor: 'pointer',
        }}>查看</button>
        <button type="button" onClick={onClose} style={{
          padding: '4px 10px', background: 'transparent',
          color: 'var(--fp-text)', border: '1px solid var(--fp-border)', borderRadius: 4, cursor: 'pointer',
        }}>关闭</button>
      </div>
    </div>
  )
}

export default function CockpitShell() {
  useBossSightObservability('cockpit-shell')
  // 壳层 A 断点(2026-07-19): ≥600 显示左 rail(600–1024 即收起态默认, hover 展开);
  // <600(phone) rail 隐藏,导航收编进 V1 汉堡抽屉;评论栏窄档抽屉化(V1 行为沿用)。
  const bp = useBreakpoint()
  // 触屏语义档(非桌面或 coarse 指针): 壳层可点元素 ≥44。
  const touch = useTouchMode(bp)
  const [navDrawerOpen, setNavDrawerOpen] = useState(false)
  const [briefing, setBriefing] = useState<BossSightBriefing | null>(null)
  const [workflow, setWorkflow] = useState<BossSightWorkflowCtxSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bottomVisible, setBottomVisible] = useState(false)
  const [bottomH, setBottomH] = useState(250)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  // 顶栏"更多(⋯)"溢出菜单: 收纳次级/新加动作(网页审阅、停靠、底部事件), 给顶栏减负 + 兜溢出。
  const [moreOpen, setMoreOpen] = useState(false)
  const [activeSpine, setActiveSpine] = useState<SpineKey>('controller')
  const [captureMode, setCaptureMode] = useState<CaptureMode>(null)
  const [hoverTarget, setHoverTarget] = useState<ElementTarget | null>(null)
  const [captureDialog, setCaptureDialog] = useState<CaptureDialogState | null>(null)
  const [captureComment, setCaptureComment] = useState('')
  const [captureBusy, setCaptureBusy] = useState(false)
  const [captureError, setCaptureError] = useState<string | null>(null)
  const [captureToast, setCaptureToast] = useState('')
  const [uiUpdatePending, setUiUpdatePending] = useState(isUiUpdatePending)
  useEffect(() => {
    const onReady = () => setUiUpdatePending(true)
    window.addEventListener(UI_UPDATE_READY_EVENT, onReady)
    return () => window.removeEventListener(UI_UPDATE_READY_EVENT, onReady)
  }, [])
  // 全屏审阅: 最大化某个页签时收起左栏/底栏/页签条, 只留顶栏(含退出键)。
  const isReviewMaximized = useReviewMaximize((s) => s.maximizedTabId !== null)
  const exitReviewMaximize = useReviewMaximize((s) => s.exit)
  const enterReviewMaximize = useReviewMaximize((s) => s.maximizeActive)
  // 总控停靠位置: false=随中央页签, true=靠右(像 VSCode AI 插件那样独占右侧 dock 组)。
  const [controllerRight, setControllerRight] = useState(() => readPref('omni.cockpit.controllerRight', false))
  // #3f 右键文件路径 → 审阅材料: 菜单位置/路径 + 匹配不上时的候选列表。
  const [pathMenu, setPathMenu] = useState<{ x: number; y: number; path: string } | null>(null)
  const [pathCandidates, setPathCandidates] = useState<{ items: Array<{ path: string; rel: string; name: string }>; query: string } | null>(null)
  const [pathBusy, setPathBusy] = useState(false)
  const [debugHandoff, setDebugHandoff] = useState<Record<string, unknown> | null>(() => {
    if (typeof window === 'undefined') return null
    try {
      const raw = window.localStorage.getItem('omni.codex.debugHandoff')
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  })
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)
  const tabs = usePanels((s) => s.tabs)
  const activeTabId = usePanels((s) => s.activeId)
  const setTabs = usePanels((s) => s.setTabs)
  const setControllerView = useControllerView((s) => s.setView)

  // 重开恢复页签: 进来时捕获上次快照(在 save effect 覆盖前)。用户 2026-06-14: 默认直接恢复(像 VSCode 不问), 不再弹提示。
  const restoreSnapshotRef = useRef<TabSnapshot>(loadTabSnapshot())
  const [showTabRestore, setShowTabRestore] = useState(false)  // 不再弹提示条; 保留 state 仅防旧 JSX 引用报错
  // 挂载即恢复上次页签与焦点(2026-07-20 用户: 恢复「上次退出界面」, 不再强制落总控); 老快照无 activeId → 回落总控。
  useEffect(() => {
    const snap = restoreSnapshotRef.current
    if (snap.exists) setTabs(snap.tabs, snap.activeId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const tabSnapFirstRef = useRef(true)
  useEffect(() => {
    // 跳过首帧(此时只有固定页签, 别用空快照覆盖上次的); 之后用户开/关页签才记。
    if (tabSnapFirstRef.current) { tabSnapFirstRef.current = false; return }
    saveTabSnapshot(tabs, activeTabId)
  }, [tabs, activeTabId])

  // selectedTab 仍给截图/调试交接的 active_tab/page 载荷用(activeTabPayload/pageStatePayload)。
  const selectedTab = useMemo(() => tabs.find((t) => t.id === activeTabId) || null, [tabs, activeTabId])

  // 导航抽屉随手势/变档关闭, 避免宽度旋转后残留一层遮罩。
  useEffect(() => {
    if (bp === 'desktop') return
    setNavDrawerOpen(false)
  }, [bp])
  const statusTone = tone(workflow?.status || briefing?.severity || 'calm')

  const load = () => {
    setError(null)
    Promise.all([
      bossSightApi.briefing(),
      bossSightApi.workflowSummary(),
    ]).then(([b, w]) => {
      setBriefing(b)
      setWorkflow(w.ctx_summary)
    }).catch((e) => {
      setError(String(e?.message || e))
    })
  }

  // 手动刷新才广播强刷(2026-06-12 用户: 首页换成项目板后点刷新无感)。挂载路径不 bump ——
  // 否则每次开页都逼所有数据面板穿透服务端缓存重扫(项目板 fresh=1 白付 ~1s), 首屏变慢。
  const manualRefresh = () => {
    useRefreshBus.getState().bump()
    load()
  }

  useEffect(() => {
    load()
  }, [])

  useEffect(() => { writePref('omni.cockpit.controllerRight', controllerRight) }, [controllerRight])
  // 把总控 dock 到中央页签的右侧(或挪回左侧)。复用搜索"右侧打开"同一 placement 机制。
  const toggleControllerRight = useCallback(() => {
    const next = !controllerRight
    setControllerRight(next)
    const st = usePanels.getState()
    const ref = st.tabs.find((t) => t.id !== CONTROLLER_TAB_ID)
    if (ref) st.requestDockPlacement(CONTROLLER_TAB_ID, { direction: next ? 'right' : 'left', referenceTabId: ref.id })
    openTab({ type: 'controller', id: 'main' }, '总控')
  }, [controllerRight, openTab])

  // #3f 右键: 仅在聊天面板内、且选中文字像文件路径时, 接管右键菜单。
  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      const sel = (window.getSelection?.()?.toString() || '').trim()
      if (!sel) return
      if (!(event.target instanceof Element) || !event.target.closest('[data-cc-chat-panel]')) return
      if (!looksLikeFilePath(sel)) return
      event.preventDefault()
      setPathCandidates(null)
      setPathMenu({ x: event.clientX, y: event.clientY, path: stripPathSelection(sel) })
    }
    document.addEventListener('contextmenu', onContextMenu, true)
    return () => document.removeEventListener('contextmenu', onContextMenu, true)
  }, [])

  // 路径菜单开着时: 点别处 / Esc 关闭。
  useEffect(() => {
    if (!pathMenu) return
    const onDown = (e: MouseEvent) => {
      if (e.target instanceof Element && e.target.closest('[data-testid="cockpit-path-menu"]')) return
      setPathMenu(null); setPathCandidates(null)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setPathMenu(null); setPathCandidates(null) } }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [pathMenu])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const type = params.get('open_type') as EntityType | null
    const id = params.get('open_id')
    if (!type || !id) return
    const facet = params.get('open_facet') || undefined
    const title = params.get('open_title') || id.split('/').pop() || id
    openTab({ type, id }, title, facet)
  }, [openTab])

  // R4: standalone 审阅台退役 — 浏览器标签 urgent 角标 + 总控推送 toast 上移到驾驶舱壳。
  // 同一条审阅 WS(streamStore, 引用计数), 事件驱动重拉 stats; urgent = 推送未读 + 必验收待审。
  const reviewStreamVersion = useReviewStream((s) => s.version)
  const pushedMaterial = useReviewStream((s) => s.pushed)
  const pushedNonce = useReviewStream((s) => s.pushedNonce)
  const [reviewStats, setReviewStats] = useState<MaterialStats | null>(null)
  const [pushToast, setPushToast] = useState<Material | null>(null)
  const baseTitleRef = useRef(typeof document !== 'undefined' ? document.title : '')
  useEffect(() => useReviewStream.getState().acquire(), [])
  useEffect(() => {
    let alive = true
    reviewstageApi.stats().then((s) => { if (alive) setReviewStats(s) }).catch(() => {})
    return () => { alive = false }
  }, [reviewStreamVersion])
  useEffect(() => {
    const urgent = (reviewStats?.pushed_unread || 0) + (reviewStats?.mandatory_unaccepted || 0)
    // 不覆盖驾驶舱原标题(index.html 静态标题), 只在有 urgent 时加 (N) 前缀。
    document.title = urgent > 0 ? `(${urgent}) ${baseTitleRef.current}` : baseTitleRef.current
  }, [reviewStats])
  useEffect(() => {
    if (pushedNonce > 0 && pushedMaterial) setPushToast(pushedMaterial)
  }, [pushedNonce, pushedMaterial])

  useEffect(() => {
    if (!captureToast) return
    const timer = window.setTimeout(() => setCaptureToast(''), 2600)
    return () => window.clearTimeout(timer)
  }, [captureToast])

  useEffect(() => {
    if (!captureMode) {
      setHoverTarget(null)
      return
    }
    // 偏移: iframe 内元素的 rect 相对其自身视口, 叠加 iframe 在宿主里的位置, 高亮框才对齐。
    const describeAt = (el: Element, offset: { x: number; y: number }): ElementTarget => {
      const t = describeElement(el)
      return offset.x || offset.y
        ? { ...t, rect: { ...t.rect, x: t.rect.x + offset.x, y: t.rect.y + offset.y } }
        : t
    }
    // 跨 realm 元素判定: iframe 内元素是该 iframe 自己的 Element 构造器实例, 父窗口的
    // `instanceof Element` 对它为 false —— 这正是之前圈选进不到 iframe 的根因。改用 nodeType 鸭子判定。
    const asElement = (t: EventTarget | null): Element | null =>
      t && typeof t === 'object' && (t as Node).nodeType === 1 ? (t as Element) : null
    // rawTarget: iframe(被审网页)里选"所点的确切元素"(文字/方框都能选), 不向上吸附到最近的
    // button/[data-testid]; 顶层 dashboard 仍吸附到语义组件(选整块更顺手)。
    const resolve = (el: Element, rawTarget: boolean): Element => (rawTarget ? el : captureElementFromTarget(el))
    const makeHandlers = (offset: { x: number; y: number }, rawTarget: boolean) => ({
      onMove: (event: MouseEvent) => {
        if (isCaptureIgnored(event.target)) return
        const el = asElement(event.target)
        if (el) setHoverTarget(describeAt(resolve(el, rawTarget), offset))
      },
      onClick: (event: MouseEvent) => {
        if (isCaptureIgnored(event.target)) return
        const el = asElement(event.target)
        if (!el) return
        event.preventDefault()
        event.stopPropagation()
        const target = describeAt(resolve(el, rawTarget), offset)
        setCaptureDialog({
          kind: captureMode,
          title: captureMode === 'debug_start' ? 'Codex 调试交接' : '圈选元素评论',
          target,
          debugAllowed: captureMode === 'debug_start',
        })
        setCaptureComment('')
        setCaptureError(null)
        setCaptureMode(null)
        setHoverTarget(null)
      },
      onKeyDown: (event: KeyboardEvent) => {
        if (event.key !== 'Escape') return
        event.preventDefault()
        setCaptureMode(null)
        setHoverTarget(null)
      },
    })
    const cleanups: Array<() => void> = []
    const attach = (doc: Document, offset: { x: number; y: number }, rawTarget: boolean) => {
      const h = makeHandlers(offset, rawTarget)
      doc.addEventListener('mousemove', h.onMove, true)
      doc.addEventListener('click', h.onClick, true)
      doc.addEventListener('keydown', h.onKeyDown, true)
      cleanups.push(() => {
        try {
          doc.removeEventListener('mousemove', h.onMove, true)
          doc.removeEventListener('click', h.onClick, true)
          doc.removeEventListener('keydown', h.onKeyDown, true)
        } catch { /* doc gone (iframe unmounted) */ }
      })
    }
    // 同一入口递归进入同源 iframe。网页原工程是“审阅材料 iframe → 播放器 iframe → 具体 DOM”
    // 两层结构；只扫一层会停在播放器外框，无法精准圈到视频里的卡片。
    const walkFrames = (doc: Document, offset: { x: number; y: number }, depth: number) => {
      if (depth > 6) return
      attach(doc, offset, depth > 0)
      for (const iframe of Array.from(doc.querySelectorAll('iframe'))) {
        let child: Document | null = null
        try { child = iframe.contentDocument } catch { child = null }
        if (!child) continue // 跨域 iframe 读不到, 跳过
        const r = iframe.getBoundingClientRect()
        walkFrames(child, { x: offset.x + r.x, y: offset.y + r.y }, depth + 1)
      }
    }
    walkFrames(document, { x: 0, y: 0 }, 0)
    return () => cleanups.forEach((c) => c())
  }, [captureMode])

  // 统一语义: 每个 rail 目的地 = 切到一个主区视图(已存在的固定页签直接激活, 否则开)。
  const handleSpine = (key: SpineKey) => {
    setActiveSpine(key)
    if (key === 'home') openTab({ type: 'project_board', id: 'main' }, '项目')
    else if (key === 'authored') openTab({ type: 'authored', id: 'main' }, '草稿箱')
    else if (key === 'review') {
      useReviewQueueFocus.getState().setFocused(null)
      openTab({ type: 'review_queue', id: 'main' }, '审阅')
    }
    else if (key === 'multiagent') openTab({ type: 'multiagent', id: 'main' }, 'Multiagent')
    else if (key === 'controller') openTab({ type: 'controller', id: 'main' }, '总控')
    else if (key === 'settings') openTab({ type: 'settings', id: 'main' }, '设置')
  }

  // rail 高亮跟随真实激活页签(修旧 activeSpine 本地自记与所见对不上的问题)。
  const railActiveKey: SpineKey = (() => {
    const t = selectedTab?.ref.type
    if (t === 'project_board' || t === 'project' || t === 'quest_board') return 'home'
    if (t === 'authored') return 'authored'
    if (t === 'review_queue' || t === 'review_material') return 'review'
    if (t === 'multiagent') return 'multiagent'
    if (t === 'controller') return 'controller'
    if (t === 'settings') return 'settings'
    return activeSpine
  })()

  const openWorkflowRef = (ref: any, title: string) => {
    openCockpitRef(openTab, ref, title)
    setNotificationsOpen(false)
  }
  const openWorkflowRefBg = (ref: any, title: string) => {
    openCockpitRef(openTabBg, ref, title) // 中键: 后台打开, 不切焦点/不收面板
  }

  // 整页网页快照(用户 2026-06-30 要求加回): 顶栏一键抓本页"所见全部内容"(含同源内嵌 iframe 递归的
  // 文字 + 完整 DOM + 主 canvas 截图) → 捕获对话框, 可复制路径或落盘 captures/pending。机器在本机,
  // 走页内捕获不依赖 poof 的全局热键。capture 全套逻辑(collectVisibleText/collectFullDom/对话框/落盘)仍在。
  const openPageSnapshotDialog = () => {
    setCaptureDialog({ kind: 'page_snapshot', title: '页面快照' })
    setCaptureComment('')
    setCaptureError(null)
  }

  // 快照要"抓到我当前所见的全部内容"——含同源内嵌 iframe(walker 审阅页 / webgame-spec 演示, 都经 8210
  // 同源反代), 并**递归进嵌套 iframe**(演示是 iframe 套 iframe)。这跟顶栏圈选进 iframe 同理(见 captureMode
  // effect 里对 document.querySelectorAll('iframe') 的遍历), 只是改成把每层文字拼起来。跨域读不到的标注后跳过;
  // 不可见(0×0, 例如未激活的 tab)的不抓, 贴近"所见"。
  // 只抓"真正所见"的文字: 逐文本节点收, 跳过 (a) 标了 data-omni-capture-ignore 的子树(捕获 UI 自身),
  // (b) checkVisibility 判隐藏的(display/visibility/content-visibility/opacity), (c) 在视口外的(dockview
  // keepAlive 后台面板常驻 DOM 但不在屏上 —— 旧版用 body.innerText 把它们也抓进来了, 比如没在看的"对话"
  // 面板的 journal 列表)。这样快照只含当前屏上看到的内容。
  const collectVisibleText = (): string => {
    const inViewport = (el: Element, win: Window): boolean => {
      let r: DOMRect
      try { r = el.getBoundingClientRect() } catch { return true }
      if (r.width === 0 && r.height === 0) return false
      return r.bottom > 0 && r.right > 0 && r.top < (win.innerHeight || 0) && r.left < (win.innerWidth || 0)
    }
    const grab = (doc: Document, win: Window, clip: boolean): string => {
      const body = doc.body
      if (!body) return ''
      let tw: TreeWalker
      try { tw = doc.createTreeWalker(body, NodeFilter.SHOW_TEXT) } catch { return (body.innerText || '').trim() }
      const parts: string[] = []
      let node: Node | null
      while ((node = tw.nextNode())) {
        const raw = (node.nodeValue || '').replace(/[ \t ]+/g, ' ').trim()
        if (!raw) continue
        const pe = node.parentElement
        if (!pe) continue
        if (pe.closest('[data-omni-capture-ignore="true"]')) continue
        const cv = (pe as unknown as { checkVisibility?: (opts?: unknown) => boolean }).checkVisibility
        if (typeof cv === 'function') {
          if (!cv.call(pe, { contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true })) continue
        } else if (pe.offsetParent === null) {
          continue
        }
        if (clip && !inViewport(pe, win)) continue
        parts.push(raw)
      }
      return parts.join('\n').trim()
    }
    const walk = (doc: Document, win: Window, label: string, depth: number, clip: boolean): string => {
      if (depth > 6) return '' // 递归深度护栏
      let out = ''
      const text = grab(doc, win, clip)
      if (text) out += (label ? `\n\n──[ ${label} ]──\n` : '') + text
      let frames: HTMLIFrameElement[] = []
      try { frames = Array.from(doc.querySelectorAll('iframe')) as HTMLIFrameElement[] } catch { frames = [] }
      for (const ifr of frames) {
        let rect: DOMRect | null = null
        try { rect = ifr.getBoundingClientRect() } catch { rect = null }
        if (rect && (rect.width === 0 || rect.height === 0)) continue // 不可见, 跳过
        if (rect && clip && !inViewport(ifr, win)) continue // 视口外的内嵌页也跳过
        const name = ifr.getAttribute('src') || ifr.getAttribute('title') || 'iframe'
        let cdoc: Document | null = null
        let cwin: Window | null = null
        try { cdoc = ifr.contentDocument; cwin = ifr.contentWindow } catch { cdoc = null; cwin = null }
        if (!cdoc || !cwin) { out += `\n\n──[ 内嵌页面(跨域, 未抓取): ${name} ]──`; continue }
        out += walk(cdoc, cwin, `内嵌页面: ${name}`, depth + 1, false) // iframe 内坐标系不同, 不做视口裁剪
      }
      return out
    }
    return walk(document, window, '', 0, true)
  }

  // 完整 DOM 抓取 = 捕获"每个网页元素"(标签/属性/data-testid/ARIA/内联样式), 不只是文字。
  // 与 collectVisibleText 同源遍历策略一致: 宿主文档 + 递归进同源内嵌 iframe(把被审阅的应用文档整份展开)。
  // 跨域读不到 → 标注跳过。被审阅的内嵌应用优先给预算, 宿主外壳(含 dockview 后台面板)殿后并限额,
  // 避免外壳 DOM 把内嵌应用挤出截断窗。剥掉捕获 UI 自身/脚本/样式块(噪音大、对元素审阅无用), 只留结构。
  const collectFullDom = (): string => {
    const segs: { label: string; html: string; embedded: boolean }[] = []
    const visit = (doc: Document, label: string, depth: number, embedded: boolean): void => {
      if (depth > 6) return // 递归深度护栏(演示是 iframe 套 iframe)
      let html = ''
      try {
        const clone = doc.documentElement.cloneNode(true) as HTMLElement
        clone
          .querySelectorAll('[data-omni-capture-ignore="true"], script, style, link[rel="stylesheet"]')
          .forEach((n) => n.remove())
        html = clone.outerHTML || ''
      } catch { html = '' }
      segs.push({ label, html, embedded })
      let frames: HTMLIFrameElement[] = []
      try { frames = Array.from(doc.querySelectorAll('iframe')) as HTMLIFrameElement[] } catch { frames = [] }
      for (const ifr of frames) {
        let rect: DOMRect | null = null
        try { rect = ifr.getBoundingClientRect() } catch { rect = null }
        if (rect && (rect.width === 0 || rect.height === 0)) continue // 不可见, 跳过
        const name = ifr.getAttribute('src') || ifr.getAttribute('title') || 'iframe'
        let cdoc: Document | null = null
        try { cdoc = ifr.contentDocument } catch { cdoc = null }
        if (!cdoc) { segs.push({ label: `内嵌页面(跨域, 未抓取): ${name}`, html: '', embedded: true }); continue }
        visit(cdoc, `内嵌页面: ${name}`, depth + 1, true)
      }
    }
    visit(document, '宿主页面(驾驶舱外壳)', 0, false)
    const EMBED_CAP = 150000 // 被审阅应用: 大预算(walker 整页元素树)
    const HOST_CAP = 50000 //  宿主外壳: 限额(只为对照, 别淹没内嵌内容)
    const ordered = [...segs].sort((a, b) => Number(b.embedded) - Number(a.embedded)) // 内嵌优先
    return ordered
      .map((s) => {
        const cap = s.embedded ? EMBED_CAP : HOST_CAP
        const body = s.html.length > cap ? `${s.html.slice(0, cap)}\n<!-- …(已截断, 原长 ${s.html.length}) -->` : s.html
        return `<!-- ──[ ${s.label} ]── -->\n${body || '(空)'}`
      })
      .join('\n\n')
      .trim()
  }

  // 尽力截图: 收集同源 canvas(含 iframe 内), toDataURL 取面积最大的非空图。空白(WebGL 未开
  // preserveDrawingBuffer, 如 walker 棋盘地板)自动跳过, 不发误导性空图; DOM 覆盖层信息走 collectFullDom。
  const capturePrimaryCanvas = (): string | null => {
    const isBlank = (c: HTMLCanvasElement): boolean => {
      try {
        const s = document.createElement('canvas')
        s.width = Math.min(c.width, 48); s.height = Math.min(c.height, 48)
        const ctx = s.getContext('2d'); if (!ctx) return false
        ctx.drawImage(c, 0, 0, s.width, s.height)
        const d = ctx.getImageData(0, 0, s.width, s.height).data
        for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) return false // 任一像素非全透明 → 非空
        return true
      } catch { return false }
    }
    const found: { url: string; area: number }[] = []
    const scan = (doc: Document, depth: number): void => {
      if (depth > 6) return
      let canvases: HTMLCanvasElement[] = []
      try { canvases = Array.from(doc.querySelectorAll('canvas')) as HTMLCanvasElement[] } catch { canvases = [] }
      for (const c of canvases) {
        try {
          if (!c.width || !c.height || isBlank(c)) continue
          const url = c.toDataURL('image/png')
          if (url && url.length > 1000 && url.length < 7_000_000) found.push({ url, area: c.width * c.height })
        } catch { /* 跨域污染/读取失败 → 跳过 */ }
      }
      let frames: HTMLIFrameElement[] = []
      try { frames = Array.from(doc.querySelectorAll('iframe')) as HTMLIFrameElement[] } catch { frames = [] }
      for (const ifr of frames) {
        let cdoc: Document | null = null
        try { cdoc = ifr.contentDocument } catch { cdoc = null }
        if (cdoc) scan(cdoc, depth + 1)
      }
    }
    scan(document, 0)
    if (!found.length) return null
    found.sort((a, b) => b.area - a.area)
    return found[0].url
  }

  // 2026-06-03 用户明示捕获两个动作分离, 都**不进审阅队列**(审阅队列只给用户看 subagent 产出):
  //   复制 = 纯剪贴板(纯客户端, 不调后端, 见下);
  //   提交 = 保存到文件(data/boss_sight/captures/pending/, 见 submitCaptureToFile), 攒着;
  //   评论完一键「让总控读取(N)」(dispatchCaptures)整批交给唯一总控读处理。
  const copyCapture = async () => {
    if (!captureDialog) return
    setCaptureBusy(true)
    setCaptureError(null)
    const url = window.location.href
    const route = `${window.location.pathname}${window.location.search}${window.location.hash}`
    const bodyText = collectVisibleText()
    const fullDom = collectFullDom()
    const shot = capturePrimaryCanvas()
    try {
      // 调试交接: 仍设置 codex 调试交接对象(window + localStorage + pill), 这是给 Codex 的线索, 不进审阅。
      if (captureDialog.kind === 'debug_start') {
        const handoff = {
          id: `dh-${new Date().toISOString()}`,
          created_at: new Date().toISOString(),
          url,
          route,
          active_tab: activeTabPayload(selectedTab),
          target: captureDialog.target || null,
          page: pageStatePayload(selectedTab),
        }
        ;(window as any).__OMNI_CODEX_DEBUG_HANDOFF__ = handoff
        try { window.localStorage.setItem('omni.codex.debugHandoff', JSON.stringify(handoff)) } catch { /* ignore */ }
        setDebugHandoff(handoff)
      }
      // 用户明示 2026-06-04: 还是太长, 剪贴板就留一个文件路径。完整内容(含 HTML/页面文本)写到 clips 文件,
      // enqueue:false → 不进 dispatch 批次、不计入待处理数。剪贴板 = 仅文件路径一行(无链接则退化为选择器)。
      let savedPath = ''
      try {
        const res = await capturesApi.save({
          capture_kind: captureDialog.kind,
          title: captureDialog.title,
          comment: captureComment.trim(),
          url,
          route,
          target: captureDialog.target,
          text_snapshot: clipText(bodyText, 60000),
          dom_snapshot: clipText(fullDom, 200000),
          image_data_url: shot || undefined,
          enqueue: false,
        })
        savedPath = res.saved_path
      } catch { /* 写文件失败也别挡复制: 退化为复制选择器 */ }
      const text = savedPath || (captureDialog.target?.selector ? `选择器: ${captureDialog.target.selector}` : '(捕获)')
      const copied = await copyToClipboard(text)
      setCaptureToast(
        copied
          ? (savedPath ? '已复制文件路径' : '已复制选择器')
          : '复制失败(剪贴板不可用)',
      )
      setCaptureDialog(null)
      setCaptureComment('')
    } catch (e) {
      setCaptureError(`复制失败: ${(e instanceof Error ? e.message : String(e)).trim()}`)
    } finally {
      setCaptureBusy(false)
    }
  }

  // 提交 = 保存到文件(captures/pending), 不进审阅队列。攒着, 之后整批交总控。
  const submitCaptureToFile = async () => {
    if (!captureDialog) return
    setCaptureBusy(true)
    setCaptureError(null)
    const url = window.location.href
    const route = `${window.location.pathname}${window.location.search}${window.location.hash}`
    const bodyText = collectVisibleText()
    const fullDom = collectFullDom()
    const shot = capturePrimaryCanvas()
    try {
      if (captureDialog.kind === 'debug_start') {
        const handoff = {
          id: `dh-${new Date().toISOString()}`,
          created_at: new Date().toISOString(),
          url, route,
          active_tab: activeTabPayload(selectedTab),
          target: captureDialog.target || null,
          page: pageStatePayload(selectedTab),
        }
        ;(window as any).__OMNI_CODEX_DEBUG_HANDOFF__ = handoff
        try { window.localStorage.setItem('omni.codex.debugHandoff', JSON.stringify(handoff)) } catch { /* ignore */ }
        setDebugHandoff(handoff)
      }
      const res = await capturesApi.save({
        capture_kind: captureDialog.kind,
        title: captureDialog.title,
        comment: captureComment.trim(),
        url,
        route,
        target: captureDialog.target,
        text_snapshot: clipText(bodyText, 60000),
        dom_snapshot: clipText(fullDom, 200000),
        image_data_url: shot || undefined,
        enqueue: false,
      })
      setCaptureToast(res.saved_path ? `已保存捕获文件: ${res.saved_path}` : '已保存捕获文件')
      setCaptureDialog(null)
      setCaptureComment('')
    } catch (e) {
      const msg = (e instanceof Error ? e.message : String(e)).trim()
      if (/\b(404|405)\b/.test(msg)) {
        setCaptureError(`保存失败: 捕获路由未就绪 (${msg})。请重启 dashboard 后端 — 已捕获内容保留在本对话框。`)
      } else {
        setCaptureError(`保存失败: ${msg}`)
      }
    } finally {
      setCaptureBusy(false)
    }
  }

  // (捕获→总控的整批派发已按用户要求关停 2026-06-12: 捕获只落盘, 不再塞给总控/不再提示。)

  // #3f 把一条文件路径变成审阅材料。严格匹配 → 直接建材料并跳审阅台; 匹配不上 → 列出候选让用户挑。
  const addPathAsMaterial = async (path: string) => {
    setPathBusy(true)
    try {
      const sendPath = path.replace(/(:\d+){1,2}$/, '') // 去掉 file.ts:42:3 这种行列号
      const res = await reviewstageApi.fromPath(sendPath)
      if (res.matched && res.material) {
        setCaptureToast(`已加入审阅: ${res.material.title}`)
        setPathMenu(null)
        setPathCandidates(null)
        openTab({ type: 'review_queue', id: 'main' }, '审阅队列', res.material.id)
      } else {
        setPathCandidates({ items: res.candidates || [], query: res.query || sendPath })
      }
    } catch (e) {
      setCaptureToast(`加入审阅失败: ${(e instanceof Error ? e.message : String(e)).trim()}`)
      setPathMenu(null)
    } finally {
      setPathBusy(false)
    }
  }


  const moreItemBtn = touch ? { ...S.moreItem, minHeight: 44 } : S.moreItem
  // urgent 角标(通知 bell badge;与浏览器标签 (N) 前缀同口径): 推送未读 + 必验收待审。
  const urgentCount = (reviewStats?.pushed_unread || 0) + (reviewStats?.mandatory_unaccepted || 0)
  const togglePalette = () => window.dispatchEvent(new CustomEvent('omni:toggle-command-palette'))

  return (
    <div style={S.root} data-testid="cockpit-shell" data-fp-breakpoint={bp} data-fp-touch={touch ? 'true' : undefined}>
      {showTabRestore && (
        <div
          data-testid="tab-restore-bar"
          style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 14px', background: 'var(--fp-accent-weak)', borderBottom: '1px solid #1f3b5c', color: '#cdd9e5', fontSize: 14 }}
        >
          <span>上次关闭时还开着 <b style={{ color: 'var(--fp-link)' }}>{restoreSnapshotRef.current.tabs.length}</b> 个页签,要恢复吗?</span>
          <button
            type="button"
            data-testid="tab-restore-yes"
            style={{ border: '1px solid var(--fp-accent)', background: 'var(--fp-accent-weak)', color: 'var(--fp-link)', borderRadius: 4, padding: '2px 12px', cursor: 'pointer', fontSize: 14 }}
            onClick={() => { setTabs(restoreSnapshotRef.current.tabs, restoreSnapshotRef.current.activeId || CONTROLLER_TAB_ID); setShowTabRestore(false) }}
          >恢复页签</button>
          <button
            type="button"
            data-testid="tab-restore-no"
            style={{ border: '1px solid var(--fp-border)', background: 'transparent', color: 'var(--fp-text-3)', borderRadius: 4, padding: '2px 12px', cursor: 'pointer', fontSize: 14 }}
            onClick={() => setShowTabRestore(false)}
          >不用</button>
        </div>
      )}
      {/* 零顶栏(2026-07-19 用户指令:"不要顶栏了,任何都不要,想办法塞进其他地方"):
          薄顶栏整体删除 —— ⌘K/通知/⋯/评论/全屏/调试/状态全部收编进左 rail 槽位(ShellRail);
          面包屑删(dockview 页签条+rail 选中态已承载,重复信息不要);
          <600 浮动汉堡唤 V1 抽屉(抽屉内补 ⌘K/通知/⋯ 项);全屏审阅态(rail 收起)给浮动退出钮。 */}
      {bp === 'phone' && !isReviewMaximized && (
        <button
          type="button"
          className="sha-fab"
          aria-label="打开导航"
          data-testid="cockpit-nav-drawer-toggle"
          onClick={() => setNavDrawerOpen(true)}
        >
          <Menu size={18} />
        </button>
      )}
      {isReviewMaximized && (
        <button
          type="button"
          className="sha-exit-float"
          aria-label="退出全屏审阅"
          data-testid="cockpit-exit-maximize"
          onClick={() => exitReviewMaximize()}
        >
          <Minimize2 size={13} />退出最大化
        </button>
      )}
      {/* ⋯ 菜单(rail 底槽弹出;手机档这五件直接平铺在导航抽屉里,不用弹层) */}
      {moreOpen && bp !== 'phone' && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 39 }} onClick={() => setMoreOpen(false)} data-omni-capture-ignore="true" />
          <div style={S.moreMenu} data-testid="cockpit-more-menu" data-omni-capture-ignore="true">
            <button
              type="button"
              style={moreItemBtn}
              data-testid="cockpit-more-page-snapshot"
              onClick={() => { setMoreOpen(false); openPageSnapshotDialog() }}
            >
              <Camera size={15} /><span>整页快照</span>
            </button>
            <button
              type="button"
              style={moreItemBtn}
              data-testid="cockpit-element-comment"
              onClick={() => { setMoreOpen(false); setCaptureMode('element_comment') }}
            >
              <Crosshair size={15} /><span>圈选元素</span>
            </button>
            <button
              type="button"
              style={moreItemBtn}
              data-testid="cockpit-more-refresh"
              onClick={() => { setMoreOpen(false); manualRefresh() }}
            >
              <RefreshCw size={15} /><span>刷新</span>
            </button>
            <button
              type="button"
              style={moreItemBtn}
              data-testid="cockpit-more-controller-right"
              onClick={() => { setMoreOpen(false); toggleControllerRight() }}
            >
              <PanelRightOpen size={15} /><span>{controllerRight ? '总控移回中央' : '总控停靠右侧'}</span>
            </button>
            <button
              type="button"
              style={moreItemBtn}
              data-testid="cockpit-more-bottom"
              onClick={() => { setMoreOpen(false); setBottomVisible((v) => !v) }}
            >
              <PanelBottom size={15} /><span>底部事件</span>
            </button>
            <button
              type="button"
              style={moreItemBtn}
              data-testid="cockpit-more-team-board"
              onClick={() => { setMoreOpen(false); openTab({ type: 'team_board', id: 'main' }, '管线') }}
            >
              <Network size={15} /><span>管线 (team · 按项目)</span>
            </button>
          </div>
        </>
      )}
      {notificationsOpen && <NotificationPanel workflow={workflow} onOpenRef={openWorkflowRef} onOpenRefBg={openWorkflowRefBg} />}
      {navDrawerOpen && bp === 'phone' && (
        // <600 导航抽屉(V1 行为收编 + 零顶栏补件): 五个统一目的地 + ⌘K/通知/⋯ 项(≥48 触控目标), scrim 点外关闭。
        <>
          <div style={S.scrim} data-omni-capture-ignore="true" onClick={() => setNavDrawerOpen(false)} />
          <nav style={S.navDrawer} data-testid="cockpit-nav-drawer" data-omni-capture-ignore="true">
            {SPINE.map(({ key, label, Icon }) => {
              const meta = key === 'review' ? briefing?.summary.review_pending || 0 : 0
              return (
                <button
                  key={key}
                  type="button"
                  style={S.navDrawerItem(railActiveKey === key)}
                  data-testid={`cockpit-drawer-nav-${key}`}
                  onClick={() => { handleSpine(key); setNavDrawerOpen(false) }}
                >
                  <Icon size={18} />
                  <span>{label}</span>
                  {meta ? <span style={S.railMeta}>{meta}</span> : null}
                </button>
              )
            })}
            <div style={S.navDrawerSep} />
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-cmdk"
              onClick={() => { setNavDrawerOpen(false); togglePalette() }}
            >
              <Command size={18} />
              <span>命令面板</span>
              <span style={S.railMeta}>⌘K</span>
            </button>
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-notifications-toggle"
              onClick={() => { setNavDrawerOpen(false); setNotificationsOpen(true) }}
            >
              <Bell size={18} />
              <span>通知</span>
              {urgentCount > 0 ? <span style={S.railMeta}>{urgentCount}</span> : null}
            </button>
            {!isReviewMaximized && selectedTab?.ref.type === 'web_review' && (
              <button
                type="button"
                style={S.navDrawerItem(false)}
                data-testid="cockpit-enter-maximize"
                onClick={() => { setNavDrawerOpen(false); enterReviewMaximize() }}
              >
                <Maximize2 size={18} />
                <span>全屏审阅</span>
              </button>
            )}
            <div style={S.navDrawerSep} />
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-more-page-snapshot"
              onClick={() => { setNavDrawerOpen(false); openPageSnapshotDialog() }}
            >
              <Camera size={18} />
              <span>整页快照</span>
            </button>
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-element-comment"
              onClick={() => { setNavDrawerOpen(false); setCaptureMode('element_comment') }}
            >
              <Crosshair size={18} />
              <span>圈选元素</span>
            </button>
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-more-refresh"
              onClick={() => { setNavDrawerOpen(false); manualRefresh() }}
            >
              <RefreshCw size={18} />
              <span>刷新</span>
            </button>
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-more-controller-right"
              onClick={() => { setNavDrawerOpen(false); toggleControllerRight() }}
            >
              <PanelRightOpen size={18} />
              <span>{controllerRight ? '总控移回中央' : '总控停靠右侧'}</span>
            </button>
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-more-bottom"
              onClick={() => { setNavDrawerOpen(false); setBottomVisible((v) => !v) }}
            >
              <PanelBottom size={18} />
              <span>底部事件</span>
            </button>
            <button
              type="button"
              style={S.navDrawerItem(false)}
              data-testid="cockpit-more-team-board"
              onClick={() => { setNavDrawerOpen(false); openTab({ type: 'team_board', id: 'main' }, '管线') }}
            >
              <Network size={18} />
              <span>管线 (team · 按项目)</span>
            </button>
          </nav>
        </>
      )}
      {error && <div style={{ ...S.error, padding: '4px 12px', borderBottom: '1px solid var(--fp-border)', background: '#160d0d' }} data-testid="cockpit-load-error">加载失败: {error}</div>}
      {captureToast && <div style={S.toast} data-omni-capture-ignore="true" data-testid="cockpit-capture-toast">{captureToast}</div>}
      {uiUpdatePending && (
        <div
          style={{ ...S.toast, color: 'var(--fp-warn)', borderColor: 'color-mix(in srgb, var(--fp-warn) 45%, transparent)' }}
          data-omni-capture-ignore="true"
          data-testid="cockpit-ui-update-pending"
        >
          界面更新已就绪 · 为保护运行中的终端已延迟刷新
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{ marginLeft: 10, color: 'inherit', border: '1px solid currentColor', background: 'transparent', cursor: 'pointer' }}
          >
            现在刷新
          </button>
        </div>
      )}
      {pushToast && (
        <ReviewPushToast
          material={pushToast}
          onOpen={() => {
            openTab({ type: 'review_material', id: pushToast.id }, materialTabTitle(pushToast.title))
            setPushToast(null)
          }}
          onClose={() => setPushToast(null)}
        />
      )}
      {pathMenu && (
        <div style={S.pathMenu(pathMenu.x, pathMenu.y)} data-testid="cockpit-path-menu" data-omni-capture-ignore="true">
          {!pathCandidates ? (
            <>
              {/* 全路径本就在这里 wrap 全显, 不再叠 title= 原生提示(M3 hover-only 清零)。 */}
              <div style={S.pathMenuPath}>{pathMenu.path}</div>
              <button type="button" style={S.pathMenuItem} disabled={pathBusy} data-testid="cockpit-path-menu-review" onClick={() => { void addPathAsMaterial(pathMenu.path) }}>
                {pathBusy ? '处理中…' : '作为审阅材料'}
              </button>
              <button type="button" style={S.pathMenuItem} onClick={async () => { await copyToClipboard(pathMenu.path); setCaptureToast('已复制路径'); setPathMenu(null) }}>复制路径</button>
              <button type="button" style={S.pathMenuItem} onClick={() => setPathMenu(null)}>取消</button>
            </>
          ) : (
            <>
              <div style={S.pathMenuPath}>没有精确匹配 · 候选 {pathCandidates.items.length}</div>
              {pathCandidates.items.length === 0 && <div style={{ ...S.rowMeta, padding: '4px 8px' }}>没找到 “{pathCandidates.query}”</div>}
              {pathCandidates.items.map((c, i) => (
                <Tooltip key={c.path} content={c.path} position="left" containerStyle={{ display: 'block' }}>
                  <button type="button" style={S.pathMenuItem} disabled={pathBusy} data-testid={`cockpit-path-candidate-${i}`} onClick={() => { void addPathAsMaterial(c.path) }}>
                    {c.rel}
                  </button>
                </Tooltip>
              ))}
              <button type="button" style={S.pathMenuItem} onClick={() => { setPathMenu(null); setPathCandidates(null) }}>取消</button>
            </>
          )}
        </div>
      )}
      {captureMode && (
        <div style={S.captureBanner} data-omni-capture-ignore="true" data-testid="cockpit-capture-banner">
          <Crosshair size={14} />
          {captureMode === 'debug_start' ? '点击页面上的调试起点' : '点击要评论的元素'}
        </div>
      )}
      {captureMode && hoverTarget && <div style={S.captureOutline(hoverTarget.rect)} data-omni-capture-ignore="true" data-testid="cockpit-capture-outline" />}
      {captureDialog && (
        <CaptureDialog
          state={captureDialog}
          comment={captureComment}
          busy={captureBusy}
          error={captureError}
          onComment={setCaptureComment}
          onSubmit={() => { void submitCaptureToFile() }}
          onCopy={() => { void copyCapture() }}
          onCancel={() => {
            setCaptureDialog(null)
            setCaptureComment('')
            setCaptureError(null)
          }}
          touch={touch}
        />
      )}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
        {/* 左 rail(壳层 A): ≥600 固定 56px，悬停不展开(平板防误触);
            <600 由汉堡抽屉替代(上文档流); 全屏审阅时收起。 */}
        {bp !== 'phone' && !isReviewMaximized && (
          <ShellRail
            activeKey={railActiveKey}
            reviewPending={briefing?.summary.review_pending || 0}
            statusTone={statusTone}
            statusLabel={workflow?.status || briefing?.severity || ''}
            onPick={handleSpine}
            slots={{
              onTogglePalette: togglePalette,
              notificationsOpen,
              notificationBadge: urgentCount,
              onToggleNotifications: () => setNotificationsOpen((v) => !v),
              enterMaximizeVisible: !isReviewMaximized && selectedTab?.ref.type === 'web_review',
              onEnterMaximize: () => enterReviewMaximize(),
              moreOpen,
              onToggleMore: () => setMoreOpen((v) => !v),
              debugReady: !!debugHandoff,
            }}
          />
        )}
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* 主区只承载 Dockview。页签相关的伴随/评价侧栏由 EditorArea 挂到当前页签内容层，
              因而保留完整页签条，只替换页签下面的正文标题栏空间。 */}
          <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', overflow: 'hidden' }}>
            <main style={S.main}>
              <div style={S.editor}>
                <EditorArea />
              </div>
              {bottomVisible && !isReviewMaximized && (
                <>
                  <HSplitter onResize={(d) => setBottomH((h) => Math.max(90, h + d))} side="top" />
                  <div style={S.bottom(bottomH)}>
                    <BottomPanel onClose={() => setBottomVisible(false)} />
                  </div>
                </>
              )}
            </main>
          </div>
        </div>
      </div>
      {/* 统一设计工作室引导演示挂载钩:仅 URL 带 ?demo=<tourId> 时挂覆盖层,否则零渲染(玩家/日常面零泄漏)。 */}
      <StudioDemoMount />
    </div>
  )
}
