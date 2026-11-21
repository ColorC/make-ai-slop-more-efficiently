// 终端折行链接的「字典映射」识别(2026-11-21 业主:「这个折行你尽量识别……你既然可以识别出来 jsonl 里面的,
// 那终端上只是映射问题」)。
//
// 会话转录里已经解析出完整的画布链接、图片 / 视频网址、本机路径(本轮结果接口给出)。TUI 画到终端上时
// 会把它们按列宽硬折行、加缩进、夹表格边框, 甚至把「(https://」单独留在行尾。这里不去猜折行规则,
// 而是反过来: 把终端附近几行的可见字符去掉空白和边框连成一串, 在里面找字典里的完整链接; 找到就把
// 它落在各行上的片段都标成同一个链接。字典之外的文字一律不碰, 所以不会把相邻内容误拼进来。
import type { IDisposable, ILink, Terminal } from '@xterm/xterm'
import { readTerminalRows, type TerminalRowSource, type UrlLinkSpan } from './terminalUrlLinks'

export interface KnownTarget {
  /** 转录里的原文: 网址或本机路径。 */
  text: string
  kind: 'url' | 'path'
}

export interface KnownLink {
  target: KnownTarget
  spans: UrlLinkSpan[]
}

/** 往上下各看多少行: 一条链接最多折成这么多行还能认出来。 */
const WINDOW_ROWS = 12
const SKIP = /[\s│┃┆┇┊┋╎╏║|]/
const MIN_TARGET = 12

function normalizeChar(ch: string): string {
  return ch === '\\' ? '/' : ch.toLowerCase()
}

export function normalizeTarget(text: string): string {
  let out = ''
  for (const ch of text) if (!SKIP.test(ch)) out += normalizeChar(ch)
  return out
}

interface StreamCell { y: number; start: number; end: number }

/** 第 y 行(0 基)上能映射到字典条目的链接。 */
export function findKnownLinksAtRow(source: TerminalRowSource, y: number, targets: readonly KnownTarget[]): KnownLink[] {
  if (!targets.length || !source.row(y)) return []
  const from = Math.max(0, y - WINDOW_ROWS)
  const to = Math.min(source.length - 1, y + WINDOW_ROWS)
  let stream = ''
  const cells: StreamCell[] = []
  for (let r = from; r <= to; r++) {
    const row = source.row(r)
    if (!row) continue
    let i = 0
    for (const ch of row.text) {
      const start = row.colOf[i] ?? i
      const end = row.colOf[i + ch.length] ?? start + 1
      i += ch.length
      if (SKIP.test(ch)) continue
      stream += normalizeChar(ch)
      cells.push({ y: r, start, end })
    }
  }
  const found: KnownLink[] = []
  const taken: Array<[number, number]> = []
  // 长的先认: 同一段文字同时是两条链接的一部分时, 归给更完整的那条。
  const ordered = [...targets].sort((a, b) => b.text.length - a.text.length)
  for (const target of ordered) {
    const needle = normalizeTarget(target.text)
    if (needle.length < MIN_TARGET) continue
    let at = stream.indexOf(needle)
    while (at >= 0) {
      const end = at + needle.length
      const overlaps = taken.some(([s, e]) => at < e && end > s)
      if (!overlaps) {
        const spans: UrlLinkSpan[] = []
        for (let k = at; k < end; k++) {
          const cell = cells[k]!
          const last = spans[spans.length - 1]
          if (last && last.y === cell.y && cell.start <= last.end + 1) last.end = Math.max(last.end, cell.end)
          else spans.push({ y: cell.y, start: cell.start, end: cell.end })
        }
        if (spans.some((span) => span.y === y)) found.push({ target, spans })
        taken.push([at, end])
      }
      at = stream.indexOf(needle, at + 1)
    }
  }
  return found
}

export interface KnownLinkHandlers {
  activate: (event: MouseEvent, target: KnownTarget) => void
  hover?: (event: MouseEvent, target: KnownTarget) => void
  leave?: () => void
}

/** 注册在其它链接识别器之前: 同一位置先认字典里的完整链接。 */
export function registerTerminalKnownLinks(
  terminal: Terminal,
  getTargets: () => readonly KnownTarget[],
  handlers: KnownLinkHandlers,
): IDisposable | undefined {
  if (typeof terminal.registerLinkProvider !== 'function') return undefined
  const source = readTerminalRows(terminal)
  return terminal.registerLinkProvider({
    provideLinks(bufferLineNumber, callback) {
      const y = bufferLineNumber - 1
      const links: ILink[] = []
      for (const link of findKnownLinksAtRow(source, y, getTargets())) {
        for (const span of link.spans) {
          if (span.y !== y) continue
          links.push({
            range: { start: { x: span.start + 1, y: y + 1 }, end: { x: span.end, y: y + 1 } },
            text: link.target.text,
            decorations: { underline: true, pointerCursor: true },
            activate: (event) => handlers.activate(event, link.target),
            hover: (event) => handlers.hover?.(event, link.target),
            leave: () => handlers.leave?.(),
          })
        }
      }
      callback(links.length ? links : undefined)
    },
  })
}
