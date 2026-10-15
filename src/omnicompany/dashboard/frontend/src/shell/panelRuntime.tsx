/**
 * panelRuntime —— 页签「常驻 + 后台节流」的运行时。
 *
 * 2026-08-07 用户改定: **所有打开的页签一并打开、切换不再加载**。做法是把 dockview 的
 * renderer 全部钉在 `always`(见 EditorArea), 面板从此不再被卸载 —— 切走只是
 * `display:none`, DOM、滚动位置、表单、SPA 路由、iframe、原生 WebView 全都在。
 *
 * 但"不卸载"意味着后台页签的定时器、轮询、SSE 会**一直跑**。浏览器给背景标签页的
 * 待遇不是这样: 隐藏的标签页不排版、不绘制, 定时器被降到 1 次/分甚至冻结。要让
 * "N 个页签常驻" 的开销压在 "N 个浏览器标签页" 的量级, 就必须自己补上这套节流:
 *
 *   · 可见性沿 context 下发到面板内部任意深度(usePanelVisible);
 *   · 面板内的周期性刷新统统走 useBackgroundPoll —— 隐藏即挂起, 重新可见立刻补一次,
 *     所以回到页签看到的仍是新数据, 而不是一屏陈旧值。
 *
 * 独立 surface(/review-stage、detached window)与单测里没有 Provider, 此时一律按
 * "可见" 处理, 行为与改造前一致。
 */

import React from 'react'

export interface PanelRuntimeValue {
  /** 页签 id(= dockview panel id), 便于面板内部做与页签绑定的诊断。 */
  tabId: string
  /**
   * dockview 报告的面板可见性: 该面板是所在分组的当前页签。
   * 注意这与"窗口是否在前台"无关 —— 后者是 document.visibilityState。
   */
  visible: boolean
}

const PanelRuntimeContext = React.createContext<PanelRuntimeValue | null>(null)

export const PanelRuntimeProvider = PanelRuntimeContext.Provider

export function usePanelRuntime(): PanelRuntimeValue | null {
  return React.useContext(PanelRuntimeContext)
}

/** 面板可见性。不在页签内(独立 surface / 测试)时视为可见。 */
export function usePanelVisible(): boolean {
  return React.useContext(PanelRuntimeContext)?.visible ?? true
}

export function documentVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden'
}

/** 窗口/标签页是否在前台。最小化、切到别的浏览器标签页都会变 false。 */
export function useDocumentVisible(): boolean {
  const [visible, setVisible] = React.useState(documentVisible)
  React.useEffect(() => {
    const sync = () => setVisible(documentVisible())
    document.addEventListener('visibilitychange', sync)
    return () => document.removeEventListener('visibilitychange', sync)
  }, [])
  return visible
}

export interface DomPanelVisible {
  /** 挂到面板内容根节点上, 用于量"是否真的在布局里" */
  ref: React.RefObject<HTMLDivElement>
  /** 内容根节点有真实尺寸(宽高 > 0)时才是可见 */
  visible: boolean
}

/** 布局测量在这个环境里是否可用(jsdom 的 getBoundingClientRect 恒 0)。 */
function domMeasurementAvailable(): boolean {
  try {
    const probe = document.createElement('div')
    probe.style.cssText = 'position:absolute;width:10px;height:10px;'
    document.body.appendChild(probe)
    const rect = probe.getBoundingClientRect()
    document.body.removeChild(probe)
    return rect.width === 10 && rect.height === 10
  } catch {
    return false
  }
}

/**
 * DOM 事实上的面板可见性。
 *
 * dockview 切走页签时只给内容祖先上 display:none, **不发**
 * onDidVisibilityChange / onDidActiveChange —— 依赖那些 API 的终端
 * fit/refresh/visibility 补量在切回时永远不跑, opencode 这类全屏 TUI
 * 就停在切走前的旧画面。这里直接量内容根节点的布局盒: 0×0 = 被隐藏。
 * 环境没有布局测量(jsdom 测试)时恒为可见, 行为与改造前一致。
 */
export function useDomPanelVisible(): DomPanelVisible {
  const ref = React.useRef<HTMLDivElement>(null)
  const [visible, setVisible] = React.useState(() => !domMeasurementAvailable())
  React.useEffect(() => {
    const el = ref.current
    if (!el || !domMeasurementAvailable()) return undefined
    const check = () => {
      const rect = el.getBoundingClientRect()
      const now = rect.width > 1 && rect.height > 1
      setVisible((previous) => (previous === now ? previous : now))
    }
    check()
    if (typeof ResizeObserver === 'undefined') {
      const timer = window.setInterval(check, 400)
      return () => window.clearInterval(timer)
    }
    const observer = new ResizeObserver(check)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return { ref, visible }
}

export interface BackgroundPollOptions {
  /** 业务侧的开关(例如会话已结束就别再轮询)。 */
  enabled?: boolean
  /** 激活瞬间(挂载 / 重新可见)是否立刻跑一次。默认 true —— 回到页签要看到新数据。 */
  leading?: boolean
  /** 罕见: 隐藏时也要跑(例如必须持续上报的心跳)。默认 false。 */
  runWhenHidden?: boolean
}

/**
 * 后台节流轮询。面板不可见或窗口不在前台时**挂起**, 重新可见立刻补一次再继续按 interval 跑。
 *
 * 回调用 ref 捕获最新引用, 所以调用方不必 useCallback 稳定它 —— 不会因为函数身份变化
 * 而重置定时器(那是"每次渲染都重新拉一次"这类隐蔽性能坑的常见来源)。
 * 同时做在途去重: 上一轮还没回来就跳过本轮, 慢接口不会堆积并发请求。
 */
export function useBackgroundPoll(
  callback: () => void | Promise<void>,
  intervalMs: number,
  options: BackgroundPollOptions = {},
): void {
  const { enabled = true, leading = true, runWhenHidden = false } = options
  const panelVisible = usePanelVisible()
  const windowVisible = useDocumentVisible()
  const active = enabled
    && intervalMs > 0
    && (runWhenHidden || (panelVisible && windowVisible))
  const callbackRef = React.useRef(callback)
  callbackRef.current = callback

  React.useEffect(() => {
    if (!active) return undefined
    let stopped = false
    let inFlight = false
    const settle = () => { inFlight = false }
    const run = () => {
      if (stopped || inFlight) return
      inFlight = true
      let result: unknown
      try {
        // 同步调用: 别把回调推到微任务里 —— 那会让"切回页签立刻刷新"晚一拍,
        // 也让同步回调之间互相误判为"上一轮还在跑"。
        result = callbackRef.current()
      } catch {
        settle()
        return
      }
      if (result && typeof (result as Promise<unknown>).then === 'function') {
        // 只有异步回调才占用在途标记: 慢接口不会堆积并发请求。
        void (result as Promise<unknown>).then(settle, settle)
      } else {
        settle()
      }
    }
    if (leading) run()
    const timer = window.setInterval(run, intervalMs)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [active, intervalMs, leading])
}
