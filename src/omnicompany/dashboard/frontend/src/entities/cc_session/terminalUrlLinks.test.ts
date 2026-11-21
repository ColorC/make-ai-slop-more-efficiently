import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { Terminal, type ILink } from '@xterm/xterm'
import { findUrlLinksAtRow, readTerminalRows, registerTerminalUrlLinks, type TerminalRow, type TerminalRowSource } from './terminalUrlLinks'

// 用户 2026-10-25 报告被截断的真实地址。
const CANVAS_URL =
  'https://workshop.example.com/bside/canvas?project=demogame&canvas=1-8-2-967101a1d791#card=wsh-f753cbc9-27cf-4636-b9f2-25ab5b8d215d'
// 用户 2026-10-26 在桌面端点开只剩 "...&canvas=canvas-" 的地址(代码块里单独一行)。
const STATUS_URL =
  'https://workshop.example.com/bside/canvas?project=me-34fe148bea71&canvas=canvas-2b5c5208&card=ptk-make-status-20261026'

function displayWidth(text: string): number {
  let width = 0
  for (const ch of text) width += /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿＀-｠]/.test(ch) ? 2 : 1
  return width
}

/** 纯文本行 → 终端行; 宽字符占两列, 与 xterm 缓冲区的列语义一致。 */
function rowsFromLines(lines: string[], width: number): TerminalRowSource {
  const rows: TerminalRow[] = lines.map((line) => {
    let text = ''
    const colOf: number[] = []
    let col = 0
    for (const ch of line) {
      const w = displayWidth(ch)
      if (col + w > width) break
      for (let i = 0; i < ch.length; i++) colOf.push(col)
      text += ch
      col += w
    }
    while (col < width) {
      colOf.push(col++)
      text += ' '
    }
    colOf.push(width)
    return { text, colOf, isWrapped: false }
  })
  return { length: rows.length, row: (y) => rows[y] }
}

function pad(text: string, width: number): string {
  return text + ' '.repeat(Math.max(0, width - displayWidth(text)))
}

function chunk(text: string, size: number): string[] {
  const out: string[] = []
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size))
  return out
}

/** 按 Claude Code 的样子画表: │ 边框、单格左右各 1 空格内边距、行间 ├─┼─┤ 分隔。 */
function drawTable(widths: number[], rows: string[][][]): string[] {
  const rule = (l: string, m: string, r: string) => l + widths.map((w) => '─'.repeat(w + 2)).join(m) + r
  const lines = [rule('┌', '┬', '┐')]
  rows.forEach((cells, rowIndex) => {
    const height = Math.max(...cells.map((c) => c.length))
    for (let i = 0; i < height; i++) {
      lines.push('│' + cells.map((c, col) => ` ${pad(c[i] ?? '', widths[col]!)} `).join('│') + '│')
    }
    lines.push(rowIndex === rows.length - 1 ? rule('└', '┴', '┘') : rule('├', '┼', '┤'))
  })
  return lines
}

function urlsAt(source: TerminalRowSource, y: number): string[] {
  return findUrlLinksAtRow(source, y).map((link) => link.url)
}

async function xtermRows(cols: number, text: string): Promise<{ source: TerminalRowSource; term: Terminal }> {
  const term = new Terminal({ cols, rows: 30, allowProposedApi: true })
  await new Promise<void>((resolve) => term.write(text, resolve))
  return { source: readTerminalRows(term), term }
}

function writeTerm(term: Terminal, text: string): Promise<void> {
  return new Promise<void>((resolve) => term.write(text, resolve))
}

/** 与 Editor 相同的 ConPTY 选项: xterm 会开启 Windows 换行启发式。不给 cols/rows 时同 Editor 一样取 xterm 默认 80×24。 */
function conptyTerminal(cols?: number, rows = 30): Terminal {
  const options = { allowProposedApi: true, scrollback: 5000, windowsPty: { backend: 'conpty' as const, buildNumber: 19045 } }
  return new Terminal(cols === undefined ? options : { ...options, cols, rows })
}

async function conptyRows(cols: number, text: string): Promise<{ source: TerminalRowSource; term: Terminal }> {
  const term = conptyTerminal(cols)
  await writeTerm(term, text)
  return { source: readTerminalRows(term), term }
}

function fixture(name: string): string {
  return readFileSync(resolve(process.cwd(), 'src/entities/cc_session/__fixtures__', name), 'utf8')
}

/** 缓冲区里写着 URL 开头的行号。 */
function rowsStartingUrl(source: TerminalRowSource, prefix: string): number[] {
  const ys: number[] = []
  for (let y = 0; y < source.length; y++) if (source.row(y)!.text.trimStart().startsWith(prefix)) ys.push(y)
  return ys
}

describe('terminal URL links — table cells hard-wrapped by the TUI', () => {
  it('joins a URL split across three rows inside one cell and highlights every piece', () => {
    const lines = drawTable([4, 44, 6], [
      [['画布'], chunk(CANVAS_URL, 44), ['已建']],
      [['卡片'], ['无'], ['待定']],
    ])
    const source = rowsFromLines(lines, 70)
    // 第 1 行是内容首行, URL 分在 1..3 行
    for (const y of [1, 2, 3]) expect(urlsAt(source, y)).toEqual([CANVAS_URL])
    const [link] = findUrlLinksAtRow(source, 2)
    expect(link!.spans.map((s) => s.y)).toEqual([1, 2, 3])
    // 每段都只落在第二列单元格内部, 不越过边框
    const leftIndex = lines[1]!.indexOf('│', 1)
    const leftBorder = displayWidth(lines[1]!.slice(0, leftIndex))
    const rightBorder = displayWidth(lines[1]!.slice(0, lines[1]!.indexOf('│', leftIndex + 1)))
    for (const span of link!.spans) {
      expect(span.start).toBe(leftBorder + 2)
      expect(span.end).toBeLessThanOrEqual(rightBorder - 1)
    }
    // 分隔行与下一张表行都不受影响
    expect(urlsAt(source, 4)).toEqual([])
    expect(urlsAt(source, 5)).toEqual([])
  })

  it('handles a two-piece URL whose neighbour cells also wrap', () => {
    const url = 'https://example.com/a/very/long/path?with=query&and=more'
    const lines = drawTable([10, 30], [[['长说明文字', '第二行'], chunk(url, 30)]])
    expect(urlsAt(rowsFromLines(lines, 60), 1)).toEqual([url])
    expect(urlsAt(rowsFromLines(lines, 60), 2)).toEqual([url])
  })

  it('works on a real xterm buffer with wide CJK cells to the left', async () => {
    const lines = ['  ' + '结果如下：', ...drawTable([10, 40], [[['工作台画布'], chunk(CANVAS_URL, 40)]]).map((l) => '  ' + l)]
    const { source, term } = await xtermRows(80, lines.join('\r\n'))
    const urlRows = [2, 3, 4, 5]
    for (const y of urlRows) expect(urlsAt(source, y)).toEqual([CANVAS_URL])
    expect(findUrlLinksAtRow(source, 2)[0]!.spans).toHaveLength(4)
    term.dispose()
  })

  it('does not join when the URL ends with room to spare in its cell', () => {
    const lines = drawTable([40], [[['https://example.com/short', 'next-line-text']]])
    expect(urlsAt(rowsFromLines(lines, 50), 1)).toEqual(['https://example.com/short'])
  })

  it('does not join a URL that exactly fills its cell when the next row is a separator', () => {
    const url = 'https://example.com/exactly-fills-the-cell'
    const lines = drawTable([url.length], [[[url]], [['plain']]])
    expect(urlsAt(rowsFromLines(lines, 60), 1)).toEqual([url])
  })

  it('does not join the neighbour cell text or a differently padded line', () => {
    const url = 'https://example.com/fills-the-cell-exactly'
    const width = url.length
    const neighbour = [
      `│ ${url} │ a    │`,
      `│ ${' '.repeat(width)} │ tail │`,
    ]
    expect(urlsAt(rowsFromLines(neighbour, 60), 0)).toEqual([url])
    const indented = [
      `│ ${url} │`,
      `│   ${pad('extra', width - 2)} │`,
    ]
    expect(urlsAt(rowsFromLines(indented, 60), 0)).toEqual([url])
    const glued = [
      `│ ${url} │`,
      `│ ${pad('abc中文', width)} │`,
    ]
    expect(urlsAt(rowsFromLines(glued, 60), 0)).toEqual([url])
  })

  it('does not treat a second URL on the next row as a continuation', () => {
    const a = 'https://example.com/aaaaaaaaaaaaaaaaaaaaa'
    const b = 'https://example.com/bbbbbbbbbbbbbbbbbbbbb'
    const lines = [`│ ${a} │`, `│ ${b} │`]
    const source = rowsFromLines(lines, 60)
    expect(urlsAt(source, 0)).toEqual([a])
    expect(urlsAt(source, 1)).toEqual([b])
  })
})

describe('terminal URL links — plain hard wrap and soft wrap', () => {
  it('joins a URL hard-wrapped at the row end onto the next indented row', () => {
    const width = 60
    const first = '● 画布地址：'
    const head = CANVAS_URL.slice(0, width - displayWidth(first))
    const rest = chunk(CANVAS_URL.slice(head.length), width - 2)
    const lines = [first + head, ...rest.map((piece) => '  ' + piece), '  下一段正文']
    return xtermRows(width, lines.join('\r\n')).then(({ source, term }) => {
      for (let y = 0; y <= rest.length; y++) expect(urlsAt(source, y)).toEqual([CANVAS_URL])
      expect(urlsAt(source, rest.length + 1)).toEqual([])
      term.dispose()
    })
  })

  it('keeps soft-wrapped (isWrapped) URLs whole', async () => {
    const { source, term } = await xtermRows(40, 'see ' + CANVAS_URL + ' done')
    expect(source.row(1)!.isWrapped).toBe(true)
    expect(urlsAt(source, 0)).toEqual([CANVAS_URL])
    expect(urlsAt(source, 2)).toEqual([CANVAS_URL])
    term.dispose()
  })

  it('does not join plain text after a URL that stops short of the row end', () => {
    const lines = ['  open https://example.com/page now', '  more text here']
    expect(urlsAt(rowsFromLines(lines, 80), 0)).toEqual(['https://example.com/page'])
    expect(urlsAt(rowsFromLines(lines, 80), 1)).toEqual([])
  })

  it('does not join when the next row is indented differently', () => {
    const url = 'https://example.com/' + 'x'.repeat(18)
    const lines = ['  ' + url, '      continued?']
    expect(urlsAt(rowsFromLines(lines, 40), 0)).toEqual([url])
  })

  it('joins an indented hard-wrap row that ConPTY mode marked as wrapped', async () => {
    // Editor 开着 windowsPty: 上一行写满最后一列后的换行会把下一行标成 isWrapped, 即便下一行是 TUI 自己带缩进的硬换行。
    const width = 60
    const head = CANVAS_URL.slice(0, width - 2)
    const rest = chunk(CANVAS_URL.slice(head.length), width - 2)
    const lines = ['  ' + head, ...rest.map((piece) => '  ' + piece), '  下一段正文']
    const { source, term } = await conptyRows(width, lines.join('\r\n'))
    expect(source.row(1)!.isWrapped).toBe(true)
    for (let y = 0; y <= rest.length; y++) expect(urlsAt(source, y)).toEqual([CANVAS_URL])
    expect(urlsAt(source, rest.length + 1)).toEqual([])
    term.dispose()
  })

  it('trims trailing punctuation but keeps balanced parentheses', () => {
    const lines = ['see https://en.wikipedia.org/wiki/Foo_(bar). and (https://example.com/x).']
    expect(urlsAt(rowsFromLines(lines, 100), 0)).toEqual([
      'https://en.wikipedia.org/wiki/Foo_(bar)',
      'https://example.com/x',
    ])
  })
})

describe('terminal URL links — real Claude Code output through ConPTY', () => {
  // 两份夹具都是用真实 Claude Code TUI 在 ConPTY 里录下的原始输出, 回复里代码块单独一行放着 STATUS_URL。
  // 走 Editor 的同一条路: 终端一建好就注册链接识别, 点击时取交给 onActivate(即 openWebTab)的地址。

  /** 注册链接识别并截住 xterm 拿到的 provider; 返回"点第 y 行(0 基)上每个链接时打开的地址"。 */
  function clickableTerminal(cols?: number, rows?: number) {
    const term = conptyTerminal(cols, rows)
    let provider: { provideLinks: (line: number, cb: (links: ILink[] | undefined) => void) => void } | undefined
    term.registerLinkProvider = ((p: typeof provider) => {
      provider = p
      return { dispose() {} }
    }) as unknown as Terminal['registerLinkProvider']
    const opened: string[] = []
    const registration = registerTerminalUrlLinks(term, (_event, url) => opened.push(url))
    const openedAt = (y: number): string[] => {
      let links: ILink[] = []
      provider!.provideLinks(y + 1, (found) => { links = found ?? [] })
      opened.length = 0
      for (const link of links) link.activate(new MouseEvent('click'), link.text)
      return [...opened]
    }
    return { term, openedAt, dispose: () => { registration?.dispose(); term.dispose() } }
  }

  function urlRows(term: Terminal): number[] {
    return rowsStartingUrl(readTerminalRows(term), 'https://workshop')
  }

  it('opens the whole URL on a terminal fitted wider after the links were registered', async () => {
    // Editor 的顺序: new Terminal 不带 cols(默认 80) → 注册链接识别 → fit 到窗口。用户 10-26 那个页签是 137 列,
    // URL 整行放得下, 没有任何换行。
    const { term, openedAt, dispose } = clickableTerminal()
    expect(term.cols).toBe(80)
    term.resize(137, 53)
    await writeTerm(term, ['  ① 总览：制造阶段做到哪了（先看这张）', '  ' + STATUS_URL, '  ② 第 2 步的四份规格'].join('\r\n'))
    expect(openedAt(1)).toEqual([STATUS_URL])
    dispose()
  })

  it('opens the whole URL from a reply painted at a width that wraps it', async () => {
    const { term, openedAt, dispose } = clickableTerminal(80, 30)
    await writeTerm(term, fixture('claude-code-url-80cols.ansi'))
    const starts = urlRows(term)
    expect(starts.length).toBeGreaterThanOrEqual(2)
    for (const y of starts) {
      expect(openedAt(y)).toEqual([STATUS_URL])
      expect(openedAt(y + 1)).toEqual([STATUS_URL])
    }
    dispose()
  })

  it('still opens the whole URL after the terminal grows wider than the rows were painted at', async () => {
    const rec = JSON.parse(fixture('claude-code-url-80to137.json')) as { cols: number; resize_to: number; part1: string; part2: string }
    const { term, openedAt, dispose } = clickableTerminal(rec.cols, 20)
    await writeTerm(term, rec.part1)
    // 另一个查看者控制网格时本端跟随 80 列; 拿回控制后变宽, 早先按 80 列硬换行的行不会重排。
    term.resize(rec.resize_to, 20)
    const check = () => {
      const starts = urlRows(term)
      expect(starts.length).toBeGreaterThanOrEqual(2)
      for (const y of starts) {
        expect(openedAt(y)).toEqual([STATUS_URL])
        if (readTerminalRows(term).row(y)!.text.trimEnd().endsWith(STATUS_URL)) continue
        expect(openedAt(y + 1)).toEqual([STATUS_URL])
      }
    }
    check()
    // Claude Code 收到新尺寸后只重画可见屏幕, 滚进历史的旧行保持 80 列的样子。
    await writeTerm(term, rec.part2)
    check()
    dispose()
  })

  it('opens the whole historical URL while the viewport is narrower than its original row', async () => {
    const { term, openedAt, dispose } = clickableTerminal(137, 20)
    await writeTerm(term, '  ' + STATUS_URL + '\r\n' + 'history\r\n'.repeat(30))
    term.resize(80, 20)
    // ConPTY keeps these cells beyond the visible grid. Clicking the visible
    // prefix must still open the complete URL, including its hidden suffix.
    expect(openedAt(0)).toEqual([STATUS_URL])
    dispose()
  })
})
