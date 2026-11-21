import { describe, expect, it } from 'vitest'
import { canvasRefKey, findLocalPaths, parseWorkshopCanvasUrl, turnResultCount } from './turnResults'

describe('findLocalPaths', () => {
  it('finds Windows absolute paths and trims trailing punctuation', () => {
    const text = '结果见 `C:/workspace/a\\b.png`，以及 C:/vcs/main/x.md。'
    expect(findLocalPaths(text).map((m) => m.path)).toEqual([
      'C:/workspace/a\\b.png',
      'C:/vcs/main/x.md',
    ])
  })

  it('reports code-unit offsets that slice back to the path', () => {
    const text = '  │ E:\\x\\报告.html │'
    const [match] = findLocalPaths(text)
    expect(text.slice(match!.start, match!.end)).toBe('E:\\x\\报告.html')
  })

  it('ignores URLs and relative paths', () => {
    expect(findLocalPaths('https://host/E:/x and src/a.ts')).toEqual([])
  })
})

describe('parseWorkshopCanvasUrl', () => {
  it('parses project, canvas and #card fragment', () => {
    expect(parseWorkshopCanvasUrl('https://workshop.example.com/bside/canvas?project=demogame&canvas=c1#card=k9')).toMatchObject({
      project: 'demogame', canvas: 'c1', card: 'k9',
    })
  })

  it('accepts card query and local proxy path', () => {
    expect(parseWorkshopCanvasUrl('http://192.0.2.10:8210/bside/canvas?canvas=c2&card=k2')?.card).toBe('k2')
  })

  it('rejects non-canvas links', () => {
    expect(parseWorkshopCanvasUrl('https://workshop.example.com/bside/review?id=1')).toBeNull()
    expect(parseWorkshopCanvasUrl('https://workshop.example.com/bside/canvas?project=p')).toBeNull()
  })
})

describe('helpers', () => {
  it('keys and counts', () => {
    expect(canvasRefKey({ project: 'p', canvas: 'c', card: '' })).toBe('canvas:p/c#')
    expect(turnResultCount(null)).toBe(0)
  })
})
