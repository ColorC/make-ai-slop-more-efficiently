// 终端里的本机绝对路径可点(2026-11-20 业主:「发了本地路径就只能干瞪眼」)。
// 点了交给后端换成只读令牌, 在驾驶舱里开文件页签 —— 远程设备上也能看本机文件。
// 只认单行里的路径(Claude Code 一般用反引号把路径包在一行里); 被 TUI 硬换行拆开的长路径不拼。
import type { IDisposable, ILink, Terminal } from '@xterm/xterm'
import { findLocalPaths } from '../../lib/turnResults'
import { readTerminalRows, type TerminalRowSource } from './terminalUrlLinks'

export interface PathLinkSpan {
  path: string
  start: number
  end: number
}

export function findPathLinksInRow(source: TerminalRowSource, y: number): PathLinkSpan[] {
  const row = source.row(y)
  if (!row) return []
  return findLocalPaths(row.text).map((match) => ({
    path: match.path,
    start: row.colOf[match.start] ?? match.start,
    end: row.colOf[match.end] ?? match.end,
  }))
}

export function registerTerminalPathLinks(
  terminal: Terminal,
  onActivate: (event: MouseEvent, path: string) => void,
  onHover?: (event: MouseEvent, path: string) => void,
  onLeave?: () => void,
): IDisposable | undefined {
  if (typeof terminal.registerLinkProvider !== 'function') return undefined
  const source = readTerminalRows(terminal)
  return terminal.registerLinkProvider({
    provideLinks(bufferLineNumber, callback) {
      const y = bufferLineNumber - 1
      const links: ILink[] = findPathLinksInRow(source, y).map((span) => ({
        range: { start: { x: span.start + 1, y: y + 1 }, end: { x: span.end, y: y + 1 } },
        text: span.path,
        decorations: { underline: true, pointerCursor: true },
        activate: (event) => onActivate(event, span.path),
        hover: (event) => onHover?.(event, span.path),
        leave: () => onLeave?.(),
      }))
      callback(links.length > 0 ? links : undefined)
    },
  })
}
