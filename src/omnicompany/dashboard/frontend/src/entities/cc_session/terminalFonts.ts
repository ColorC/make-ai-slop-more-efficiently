/**
 * 终端字体就绪等待 —— opencode 右侧锯齿对齐问题的针对性修复。
 *
 * 根因: --fp-font-term 的第一字体是本地打包的 Sarasa Term SC(@font-face
 * font-display:swap, 几十个 woff2 分片)。终端第一次 fit 时如果 Sarasa 还没
 * 到货, xterm 用回退字体(Consolas)量出的单元格宽度算 cols 并下发 ConPTY;
 * Sarasa 到货后字形度量变化, 容器没变所以 ResizeObserver 不触发 —— cols
 * 与真实可视列数失配, TUI 按错误的宽度折行/画右边框, 就是用户看到的
 * "右边对不齐、锯齿、要反复拖尺寸才恢复"。每次拖尺寸都等于手动补一次
 * 用最终字体度量重量的 fit, 所以"多调整几次"才收敛。
 *
 * 修法: 挂载后等字体真正加载完, 触发一次 settled resize commit; 度量变了
 * 就自动重发正确 cols, 没变则被 settle 层去重跳过。
 */

export interface TerminalFontSources {
  load(query: string): Promise<unknown[]>
  ready: Promise<unknown>
}

const DEFAULT_TIMEOUT_MS = 30000

function primaryFamily(stack: string): string {
  return (stack.split(',')[0] || '').trim().replace(/^['"]|['"]$/g, '') || 'monospace'
}

export function terminalFonts(): TerminalFontSources | null {
  const fonts = (document as Document & { fonts?: TerminalFontSources }).fonts
  if (!fonts || typeof fonts.load !== 'function' || !fonts.ready) return null
  return fonts
}

/**
 * Resolve once the terminal's primary font has actually loaded (or give up
 * after `timeoutMs` — the terminal must never wait forever on a hung font).
 * No `document.fonts` in the environment (jsdom/老内核) → resolve immediately,
 * 让既有行为不变。
 */
export function whenTerminalFontsReady(
  familyStack: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  sources: TerminalFontSources | null = terminalFonts(),
): Promise<void> {
  if (!sources) return Promise.resolve()
  const primary = primaryFamily(familyStack)
  const loaded = Promise.all([
    sources.load(`15px "${primary}"`).catch(() => [] as unknown[]),
    Promise.resolve(sources.ready),
  ]).then(() => undefined)
  return new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, timeoutMs)
    loaded.then(() => {
      window.clearTimeout(timer)
      resolve()
    })
  })
}
