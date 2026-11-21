// 终端 URL 链接识别: 取代 WebLinksAddon, 认得被 TUI 硬换行拆开的 URL。
//
// WebLinksAddon 只沿 xterm 的软换行(isWrapped)拼逻辑行。Claude Code 画 markdown 表格时,
// 单元格里的长 URL 由 TUI 自己按列宽硬换行, 每段两侧夹着 │ 与内边距, 下一段不是软换行,
// 于是只有第一段可点, 打开的是截断地址。这里在软换行之外, 再按"同一列区间"往下接续硬换行段:
//   - 上一段必须写到单元格右边框(或行尾)为止, 中间只允许内边距空格;
//   - 下一行在同样的列上有同样的边框, 单元格内左内边距与上一行一致, 紧接着就是 URL 字符;
//   - 无边框时(整行写满后硬换行)按行首缩进对齐判断。
// 判据刻意保守: 宁可少拼一段, 也不把相邻单元格或下一行正文拼进 URL。
//
// ConPTY 下还有两处要绕开:
//   - Editor 开着 windowsPty, xterm 的 Windows 换行启发式会在"上一行最后一列非空"时把下一行标成 isWrapped,
//     TUI 自己硬换行出来、带缩进或边框的接续行也会被标上。照软换行原样拼, 缩进空格就把 URL 截断了。
//     所以只把行首就是内容的 isWrapped 行当软换行, 其余仍走硬换行接续判断。
//   - windowsPty 下 xterm 不重排; 网格跟随别的查看者变窄再变回来后, 早先按窄列宽硬换行的行保持原样。
//     "写到行尾"按这些行当初的列宽判断: 记下终端写入输出时出现过的列宽。

import type { IBufferCell, IDecoration, IDisposable, ILink, IMarker, Terminal } from '@xterm/xterm'

/** 一行终端内容: text 的第 i 个码元落在第 colOf[i] 列; colOf[text.length] 为行宽。 */
export interface TerminalRow {
  text: string
  colOf: number[]
  isWrapped: boolean
}

export interface TerminalRowSource {
  readonly length: number
  row(y: number): TerminalRow | undefined
  /** 除当前列宽外, 缓冲区里的行还可能是按哪些列宽画的(见 trackPaintWidths)。 */
  paintWidths?(): readonly number[]
}

/** 链接在某一行上的一段, 列区间 [start, end), 均为 0 基。 */
export interface UrlLinkSpan {
  y: number
  start: number
  end: number
}

export interface TerminalUrlLink {
  url: string
  spans: UrlLinkSpan[]
}

const URL_REGEX = /https?:\/\/[^\s<>"'`\\|─-╿　-〿＀-￯]+/gi
const URL_CHAR = /[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]/
const VERTICAL_BORDER = /[|│┃┆┇┊┋╎╏║]/
const TRAILING_PUNCT = /[.,;:!?'*]+$/
const MIN_URL_LENGTH = 'https://a.b'.length
// 往上回看多少行去找"从上方延续到本行"的链接, 也是单条链接最多跨的行数。
const MAX_LINK_ROWS = 16
// 无边框硬换行: URL 末尾离行尾最多空几列仍算"写满了"(部分 TUI 不写最后一列)。
const OPEN_RIGHT_MAX_GAP = 1
// 记住最近几种写过输出的列宽; 更早的行多半已被 TUI 按新列宽重画或滚出缓冲区。
const MAX_PAINT_WIDTHS = 8

/** 记下终端每次写入输出时的列宽, 供 readTerminalRows 判断旧行当初写到了哪一列为止。 */
export function trackPaintWidths(terminal: Terminal): { widths: () => readonly number[]; dispose: () => void } {
  const recent: number[] = []
  const remember = () => {
    const cols = terminal.cols
    if (recent[recent.length - 1] === cols) return
    const seen = recent.indexOf(cols)
    if (seen >= 0) recent.splice(seen, 1)
    recent.push(cols)
    if (recent.length > MAX_PAINT_WIDTHS) recent.shift()
  }
  const subscription = typeof terminal.onWriteParsed === 'function' ? terminal.onWriteParsed(remember) : undefined
  return { widths: () => recent, dispose: () => subscription?.dispose() }
}

/** xterm 标的 isWrapped 只有在行首直接是内容时才当软换行(见文件头 ConPTY 说明)。 */
function continuesSoftWrap(row: TerminalRow | undefined): boolean {
  if (!row?.isWrapped) return false
  const first = row.text[0]
  return first !== undefined && first !== ' ' && !VERTICAL_BORDER.test(first)
}

/**
 * 按需读终端行。列宽与活动缓冲区每次现取: 链接识别在终端刚建好(默认 80 列)时就注册,
 * 之后 fit / 跟随 PTY 网格改列宽、TUI 切备用屏, 都不能沿用注册时的值。
 */
export function readTerminalRows(terminal: Terminal, paintWidths?: () => readonly number[]): TerminalRowSource {
  let cell: IBufferCell | undefined
  return {
    get length() {
      return terminal.buffer.active.length
    },
    paintWidths,
    row(y) {
      const line = terminal.buffer.active.getLine(y)
      if (!line) return undefined
      // Old ConPTY retains the original cells on narrower history rows. The
      // visible grid is only a clip; reading just cols silently truncates the
      // URL even though its complete suffix still exists in the buffer.
      const width = Math.max(terminal.cols, line.length)
      let text = ''
      const colOf: number[] = []
      for (let x = 0; x < width; x++) {
        cell = line.getCell(x, cell)
        if (!cell) break
        // 宽字符的后半格宽度为 0, 字符已记在前一格。
        if (cell.getWidth() === 0) continue
        const chars = cell.getChars() || ' '
        for (let i = 0; i < chars.length; i++) colOf.push(x)
        text += chars
      }
      colOf.push(width)
      return { text, colOf, isWrapped: line.isWrapped }
    },
  }
}

type CellBounds = {
  /** 左边框列, -1 表示没有左边框(行首)。 */
  left: number
  /** 右边框列, null 表示没有右边框(写到行尾)。 */
  right: number | null
  /** 接续段必须从这一列开始。 */
  contentCol: number
  width: number
  /** 无右边框时 URL 写到的行尾列: 当前列宽, 或这些行当初写入时的列宽。 */
  edge: number
}

function colAt(row: TerminalRow, index: number): number {
  return row.colOf[Math.min(index, row.text.length)]!
}

function indexAtCol(row: TerminalRow, col: number): number {
  const index = row.colOf.findIndex((c) => c >= col)
  return index < 0 ? row.text.length : index
}

function charAtCol(row: TerminalRow, col: number): string | undefined {
  const index = row.colOf.indexOf(col)
  return index < 0 || index >= row.text.length ? undefined : row.text[index]
}

function firstNonSpace(row: TerminalRow, fromIndex: number, toIndex: number): number {
  for (let i = fromIndex; i < toIndex; i++) if (row.text[i] !== ' ') return i
  return -1
}

/** 无边框的 URL 结束在 endCol 时, 它顶到的行尾: 先认当前列宽, 再认写过输出的旧列宽; 都不沾边返回 null。 */
function openRightEdge(width: number, endCol: number, paintWidths: readonly number[]): number | null {
  for (const edge of [width, ...paintWidths]) {
    if (edge >= endCol && edge - endCol <= OPEN_RIGHT_MAX_GAP) return edge
  }
  return null
}

/**
 * URL 在 row 的 [startIndex, endIndex) 结束时, 判断它是否顶到了单元格右沿, 并给出接续段应在的列区间。
 * 返回 null 表示 URL 已在本行完整结束。
 */
function boundsForTail(row: TerminalRow, startIndex: number, endIndex: number, paintWidths: readonly number[]): CellBounds | null {
  const width = colAt(row, row.text.length)
  const endCol = colAt(row, endIndex)
  const after = firstNonSpace(row, endIndex, row.text.length)
  let right: number | null
  if (after < 0) {
    right = null
  } else if (VERTICAL_BORDER.test(row.text[after]!)) {
    right = colAt(row, after)
  } else {
    return null
  }

  let left = -1
  for (let i = startIndex - 1; i >= 0; i--) {
    if (VERTICAL_BORDER.test(row.text[i]!)) {
      left = colAt(row, i)
      break
    }
  }
  // 只有一侧有边框说明不是规整表格, 不冒险接续。
  if ((left >= 0) !== (right !== null)) return null

  if (right === null) {
    const edge = openRightEdge(width, endCol, paintWidths)
    if (edge === null) return null
    const indent = firstNonSpace(row, 0, row.text.length)
    // 行首缩进; 若行首是 "● " "- " 之类的单字符标记, 接续行与标记后的正文对齐。
    let contentCol = colAt(row, indent)
    const bulletEnd = firstNonSpace(row, indent + 1, startIndex + 1)
    if (indent < startIndex && row.text[indent + 1] === ' ' && !URL_CHAR.test(row.text[indent]!) && bulletEnd >= 0) {
      contentCol = colAt(row, bulletEnd)
    }
    if (contentCol > colAt(row, startIndex)) return null
    return { left, right, contentCol, width, edge }
  }

  const cellStart = indexAtCol(row, left + 1)
  const content = firstNonSpace(row, cellStart, startIndex + 1)
  if (content < 0) return null
  const padding = colAt(row, content) - (left + 1)
  if (right - endCol > padding + 1) return null
  return { left, right, contentCol: left + 1 + padding, width, edge: right }
}

type Fragment = { start: number; end: number; text: string; reachesEdge: boolean }

/** 在下一行按同一列区间取接续段; 不满足任一条件即返回 null。 */
function continuationFragment(row: TerminalRow, bounds: CellBounds): Fragment | null {
  if (continuesSoftWrap(row)) return null
  if (colAt(row, row.text.length) !== bounds.width) return null
  if (bounds.left >= 0 && !VERTICAL_BORDER.test(charAtCol(row, bounds.left) ?? '')) return null
  if (bounds.right !== null && !VERTICAL_BORDER.test(charAtCol(row, bounds.right) ?? '')) return null

  const cellStartIndex = indexAtCol(row, bounds.left + 1)
  const cellEndIndex = bounds.right === null ? row.text.length : indexAtCol(row, bounds.right)
  const startIndex = firstNonSpace(row, cellStartIndex, cellEndIndex)
  if (startIndex < 0 || colAt(row, startIndex) !== bounds.contentCol) return null

  let endIndex = startIndex
  while (endIndex < cellEndIndex && URL_CHAR.test(row.text[endIndex]!)) endIndex++
  if (endIndex === startIndex) return null
  // 段尾必须是空格或单元格边界; 紧贴着非 URL 字符(如中文)说明这行是别的内容。
  if (endIndex < cellEndIndex && row.text[endIndex] !== ' ') return null
  const text = row.text.slice(startIndex, endIndex)
  if (/^https?:\/\//i.test(text)) return null

  const endCol = colAt(row, endIndex)
  const rest = firstNonSpace(row, endIndex, cellEndIndex)
  const edge = bounds.right === null ? bounds.edge : bounds.right
  const maxGap = bounds.right === null ? OPEN_RIGHT_MAX_GAP : bounds.contentCol - bounds.left
  const reachesEdge = rest < 0 && edge - endCol <= maxGap
  return { start: colAt(row, startIndex), end: endCol, text, reachesEdge }
}

function trimUrlTail(pieces: Array<UrlLinkSpan & { text: string }>): void {
  for (;;) {
    const last = pieces[pieces.length - 1]
    if (!last) return
    let text = last.text.replace(TRAILING_PUNCT, '')
    // 末尾的右括号只在不配对时剥掉, 保住 wiki 式 URL 里的 (...)。
    const joined = pieces.map((p) => p.text).join('')
    if (/[)\]]$/.test(text)) {
      const open = text.endsWith(')') ? '(' : '['
      const close = text.endsWith(')') ? ')' : ']'
      if (joined.split(close).length > joined.split(open).length) text = text.slice(0, -1)
    }
    if (text === last.text) return
    if (text) {
      last.end -= last.text.length - text.length
      last.text = text
    } else {
      pieces.pop()
    }
  }
}

function linksForLogicalLine(source: TerminalRowSource, startRow: number, endRow: number): TerminalUrlLink[] {
  const rows: Array<{ y: number; row: TerminalRow; offset: number }> = []
  let combined = ''
  for (let y = startRow; y <= endRow; y++) {
    const row = source.row(y)
    if (!row) break
    rows.push({ y, row, offset: combined.length })
    combined += row.text
  }
  const links: TerminalUrlLink[] = []
  for (const match of combined.matchAll(URL_REGEX)) {
    const matchStart = match.index ?? 0
    const matchEnd = matchStart + match[0].length
    const pieces: Array<UrlLinkSpan & { text: string }> = []
    let tail: { row: TerminalRow; y: number; startIndex: number; endIndex: number } | null = null
    for (const { y, row, offset } of rows) {
      const startIndex = Math.max(matchStart, offset) - offset
      const endIndex = Math.min(matchEnd, offset + row.text.length) - offset
      if (startIndex >= endIndex) continue
      pieces.push({ y, start: colAt(row, startIndex), end: colAt(row, endIndex), text: row.text.slice(startIndex, endIndex) })
      tail = { row, y, startIndex, endIndex }
    }
    if (!tail) continue

    // 只有写到逻辑行最后一行的 URL 才可能被硬换行续到下一行。
    let bounds = tail.y === rows[rows.length - 1]!.y ? boundsForTail(tail.row, tail.startIndex, tail.endIndex, source.paintWidths?.() ?? []) : null
    let nextY = tail.y + 1
    while (bounds && nextY < source.length && pieces.length < MAX_LINK_ROWS) {
      const next = source.row(nextY)
      const fragment = next ? continuationFragment(next, bounds) : null
      if (!fragment) break
      pieces.push({ y: nextY, start: fragment.start, end: fragment.end, text: fragment.text })
      if (!fragment.reachesEdge) break
      nextY++
    }

    trimUrlTail(pieces)
    const url = pieces.map((p) => p.text).join('')
    if (url.length < MIN_URL_LENGTH) continue
    links.push({ url, spans: pieces.map(({ y, start, end }) => ({ y, start, end })) })
  }
  return links
}

/** 找出所有覆盖第 y 行(0 基)的链接, 包括从上方几行硬换行延续下来的。 */
export function findUrlLinksAtRow(source: TerminalRowSource, y: number): TerminalUrlLink[] {
  if (!source.row(y)) return []
  const found = new Map<string, TerminalUrlLink>()
  let row = y
  const floor = Math.max(0, y - MAX_LINK_ROWS + 1)
  while (row >= floor) {
    let start = row
    while (start > 0 && continuesSoftWrap(source.row(start))) start--
    let end = row
    while (end + 1 < source.length && continuesSoftWrap(source.row(end + 1))) end++
    for (const link of linksForLogicalLine(source, start, end)) {
      if (!link.spans.some((span) => span.y === y)) continue
      const first = link.spans[0]!
      found.set(`${first.y}:${first.start}`, link)
    }
    row = start - 1
  }
  return [...found.values()]
}

/**
 * 在终端上注册链接识别。每段各自是一个 xterm 链接(悬停区域只覆盖 URL 本身, 不跨到相邻单元格),
 * 文本都是完整地址; 悬停任意一段时用装饰给其余几段补下划线。
 */
export function registerTerminalUrlLinks(
  terminal: Terminal,
  onActivate: (event: MouseEvent, url: string) => void,
  /** 悬停预览(画布卡、图片、视频直接浮出来); 可选。 */
  onHover?: (event: MouseEvent, url: string) => void,
  onLeave?: () => void,
): IDisposable | undefined {
  if (typeof terminal.registerLinkProvider !== 'function') return undefined
  const painted = trackPaintWidths(terminal)
  const source = readTerminalRows(terminal, painted.widths)
  let siblings: Array<{ marker: IMarker; decoration: IDecoration | undefined }> = []

  const clearSiblings = () => {
    for (const item of siblings) {
      item.decoration?.dispose()
      item.marker.dispose()
    }
    siblings = []
  }

  const underlineSiblings = (link: TerminalUrlLink, hovered: UrlLinkSpan) => {
    clearSiblings()
    if (link.spans.length < 2 || typeof terminal.registerDecoration !== 'function') return
    try {
      const buffer = terminal.buffer.active
      const cursorAbs = buffer.baseY + buffer.cursorY
      const color = terminal.options.theme?.foreground ?? 'currentColor'
      for (const span of link.spans) {
        if (span === hovered) continue
        const marker = terminal.registerMarker(span.y - cursorAbs)
        if (!marker) continue
        const decoration = terminal.registerDecoration({ marker, x: span.start, width: span.end - span.start })
        decoration?.onRender((element) => {
          element.style.pointerEvents = 'none'
          element.style.boxSizing = 'border-box'
          element.style.borderBottom = `1px solid ${color}`
        })
        siblings.push({ marker, decoration })
      }
    } catch {
      // 装饰只是锦上添花: 备用缓冲区等场景注册失败时, 仍保留被悬停那段自身的下划线。
      clearSiblings()
    }
  }

  const provider = terminal.registerLinkProvider({
    provideLinks(bufferLineNumber, callback) {
      const y = bufferLineNumber - 1
      const links: ILink[] = []
      for (const link of findUrlLinksAtRow(source, y)) {
        for (const span of link.spans) {
          if (span.y !== y) continue
          links.push({
            range: { start: { x: span.start + 1, y: y + 1 }, end: { x: span.end, y: y + 1 } },
            text: link.url,
            activate: (event) => onActivate(event, link.url),
            hover: (event) => { underlineSiblings(link, span); onHover?.(event, link.url) },
            leave: () => { clearSiblings(); onLeave?.() },
          })
        }
      }
      callback(links.length > 0 ? links : undefined)
    },
  })
  return {
    dispose() {
      clearSiblings()
      painted.dispose()
      provider.dispose()
    },
  }
}
