import { describe, expect, it } from 'vitest'
import { findKnownLinksAtRow, type KnownTarget } from './terminalKnownLinks'
import type { TerminalRow, TerminalRowSource } from './terminalUrlLinks'

function source(lines: string[]): TerminalRowSource {
  const rows: TerminalRow[] = lines.map((text) => ({
    text,
    colOf: Array.from({ length: text.length + 1 }, (_, i) => i),
    isWrapped: false,
  }))
  return { length: rows.length, row: (y) => rows[y] }
}

const URL_A = 'https://workshop.example.com/bside/canvas?project=workshop&canvas=alcove-a2442fd9#card=perlica-source-baseline-20261119-visit'

describe('findKnownLinksAtRow', () => {
  // Codex 实际画法: 「(https://」单独留在行尾, 下一行带缩进接着写, 第三行收尾。
  const rows = source([
    '两段实机录像已验证。座位往返 · 34 秒 (https://',
    'workshop.example.com/bside/canvas?project=workshop&canvas=alcove-a2442fd9#card=perlica-source-baseline-',
    '20261119-visit) · 台前说话与走动',
  ])
  const targets: KnownTarget[] = [{ text: URL_A, kind: 'url' }]

  it('maps a dictionary URL across hard wraps onto every row it touches', () => {
    for (const y of [0, 1, 2]) {
      const links = findKnownLinksAtRow(rows, y, targets)
      expect(links).toHaveLength(1)
      expect(links[0]!.target.text).toBe(URL_A)
      expect(links[0]!.spans.map((s) => s.y)).toEqual([0, 1, 2])
    }
    const [first, , last] = findKnownLinksAtRow(rows, 0, targets)[0]!.spans
    expect(rows.row(0)!.text.slice(first!.start, first!.end)).toBe('https://')
    expect(rows.row(2)!.text.slice(last!.start, last!.end)).toBe('20261119-visit')
  })

  it('handles indentation and table borders between the pieces', () => {
    const boxed = source([
      '│ 结果 (https://workshop.example.com/bside/canvas?project= │',
      '│      workshop&canvas=alcove-a2442fd9#card=perlica-      │',
      '│      source-baseline-20261119-visit)                    │',
    ])
    const links = findKnownLinksAtRow(boxed, 1, targets)
    expect(links).toHaveLength(1)
    expect(links[0]!.spans).toHaveLength(3)
  })

  it('matches Windows paths regardless of slash direction and leaves other text alone', () => {
    const paths = source(['见 E:/Work/out/very-long-name/', '   shot.png 和别的文字'])
    const links = findKnownLinksAtRow(paths, 1, [{ text: 'E:\\Work\\out\\very-long-name\\shot.png', kind: 'path' }])
    expect(links).toHaveLength(1)
    expect(paths.row(1)!.text.slice(links[0]!.spans[1]!.start, links[0]!.spans[1]!.end)).toBe('shot.png')
    expect(findKnownLinksAtRow(paths, 1, [{ text: 'E:\\Other\\file-not-here.png', kind: 'path' }])).toEqual([])
  })
})
