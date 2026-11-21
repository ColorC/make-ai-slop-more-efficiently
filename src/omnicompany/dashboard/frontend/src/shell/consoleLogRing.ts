// 轻量浏览器日志环形缓冲(P1.2 薄顶栏警告徽标 + 页面快照附带日志的唯一数据源)。
// patch console.error/console.warn + window error/unhandledrejection + 同源 postMessage
// omni-notes-error(札记画布就绪协议的失败信号), 只保留最近 CAPACITY 条 {time, level, text}。
// 安装幂等: 多个壳/测试重复调用只 patch 一次; 原始 console 方法照常透传(不打断既有输出)。

export type ConsoleLogLevel = 'error' | 'warn'

export interface ConsoleLogEntry {
  /** ISO 时间戳(收集时刻)。 */
  time: string
  level: ConsoleLogLevel
  text: string
}

/** 环形缓冲容量: 最近 50 条(计划口径)。 */
export const CONSOLE_LOG_RING_CAPACITY = 50

/** 单条文本上限: 防止一条巨型报错(如整页 HTML)挤爆缓冲与快照 payload。 */
const ENTRY_TEXT_CAP = 2000

let entries: ConsoleLogEntry[] = []
let installed = false
let notifying = false
const listeners = new Set<() => void>()

// 通知防重入: 订阅者(React rerender)若在同步链里再触发 console.error → push → emit,
// 会形成自反馈递归(act/dev 警告下实测会爆栈)。通知期间只记录、不再发新通知。
function emit(): void {
  if (notifying) return
  notifying = true
  try {
    for (const listener of listeners) listener()
  } finally {
    notifying = false
  }
}

function clip(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit)}…(截断 ${text.length - limit} 字符)`
}

function formatArg(arg: unknown): string {
  if (arg instanceof Error) return arg.stack || `${arg.name}: ${arg.message}`
  if (typeof arg === 'string') return arg
  if (arg == null || typeof arg === 'number' || typeof arg === 'boolean' || typeof arg === 'bigint' || typeof arg === 'symbol') {
    return String(arg)
  }
  try { return JSON.stringify(arg) ?? String(arg) } catch { return String(arg) }
}

/** 追加一条(供 patch 与 omni-notes-error message 监听共用; 测试也可直接用)。 */
export function pushConsoleLogEntry(level: ConsoleLogLevel, text: unknown): void {
  const normalized = String(text ?? '').replace(/\s+/g, ' ').trim()
  if (!normalized) return
  const next = entries.concat({
    time: new Date().toISOString(),
    level,
    text: clip(normalized, ENTRY_TEXT_CAP),
  })
  if (next.length > CONSOLE_LOG_RING_CAPACITY) next.splice(0, next.length - CONSOLE_LOG_RING_CAPACITY)
  entries = next
  emit()
}

/** 当前缓冲快照(只读引用; 仅在变更时换引用, 可直接作为 useSyncExternalStore 快照)。 */
export function getConsoleLogEntries(): readonly ConsoleLogEntry[] {
  return entries
}

/** 最近一条 error(顶栏警告徽标文案); 没有报错时为 null。 */
export function latestConsoleError(): ConsoleLogEntry | null {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i].level === 'error') return entries[i]
  }
  return null
}

export function clearConsoleLogRing(): void {
  entries = []
  emit()
}

/** 订阅缓冲变更(React useSyncExternalStore / 顶栏徽标刷新)。 */
export function subscribeConsoleLogRing(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** 全部条目格式化成多行文本(`time [level] text`), 供「复制全部」与快照 payload 用。 */
export function formatConsoleLogEntries(list: readonly ConsoleLogEntry[] = entries): string {
  return list.map((e) => `${e.time} [${e.level}] ${e.text}`).join('\n')
}

/**
 * 安装全局收集器(幂等): patch console.error/warn, 监听 window error / unhandledrejection
 * 与 omni-notes-error message。仅浏览器环境; 原始方法先执行再记录, 不吞输出。
 */
export function installConsoleLogRing(): void {
  if (installed || typeof window === 'undefined') return
  installed = true
  const wrap = (key: 'error' | 'warn') => {
    const original = console[key].bind(console)
    console[key] = (...args: unknown[]) => {
      original(...args)
      pushConsoleLogEntry(key, args.map(formatArg).join(' '))
    }
  }
  wrap('error')
  wrap('warn')
  window.addEventListener('error', (event: ErrorEvent) => {
    const where = event.filename ? ` (${event.filename}:${event.lineno})` : ''
    pushConsoleLogEntry('error', `${event.message || formatArg(event.error) || 'window error'}${where}`)
  })
  window.addEventListener('unhandledrejection', (event: Event) => {
    const reason = (event as PromiseRejectionEvent).reason
    pushConsoleLogEntry('error', `unhandledrejection: ${formatArg(reason)}`)
  })
  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data as { type?: unknown; error?: unknown } | null
    if (!data || typeof data !== 'object' || data.type !== 'omni-notes-error') return
    pushConsoleLogEntry('error', `omni-notes-error: ${String(data.error || '画布打开失败')}`)
  })
}
