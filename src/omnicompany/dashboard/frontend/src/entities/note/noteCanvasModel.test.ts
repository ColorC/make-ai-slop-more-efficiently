import { describe, expect, it } from 'vitest'
import { groupNoteCanvasNodes, layoutNoteCanvas, noteCanvasFontSize, noteCanvasLineStyle, parseNoteCanvas, ungroupNoteCanvas, writeNoteCanvas, type NoteCanvasDocument } from './noteCanvasModel'

const canvas: NoteCanvasDocument = {
  version: 1,
  nodes: [
    { id: 'a', kind: 'mindmap', label: 'A', x: 10, y: 20, width: 180, height: 72 },
    { id: 'b', kind: 'mindmap', label: 'B', x: 10, y: 20, width: 180, height: 72 },
  ],
  edges: [{ id: 'e', source: 'a', target: 'b' }],
}

describe('note canvas markdown storage', () => {
  it('adds and replaces one canvas fence without changing prose', () => {
    const first = writeNoteCanvas('# 札记\n\n正文', canvas)
    expect(first).toContain('# 札记\n\n正文')
    expect(parseNoteCanvas(first)).toEqual({ ...canvas, edges: [{ ...canvas.edges[0]!, lineStyle: 'curve' }] })
    const updated = writeNoteCanvas(first, { ...canvas, edges: [] })
    expect(updated.match(/```omni-canvas/g)).toHaveLength(1)
    expect(parseNoteCanvas(updated).edges).toEqual([])
  })

  it('groups and expands nodes without changing absolute positions', () => {
    const grouped = groupNoteCanvasNodes(canvas, ['a', 'b'], 'g')
    expect(grouped.nodes.find((node) => node.id === 'a')?.parentId).toBe('g')
    const expanded = ungroupNoteCanvas(grouped, 'g')
    expect(expanded.nodes).toEqual(canvas.nodes)
  })

  it('lays connected nodes from top to bottom', () => {
    const laidOut = layoutNoteCanvas(canvas)
    const a = laidOut.nodes.find((node) => node.id === 'a')!
    const b = laidOut.nodes.find((node) => node.id === 'b')!
    expect(b.y).toBeGreaterThan(a.y)
  })
  it('normalizes persisted input font sizes', () => {
    expect(noteCanvasFontSize(5)).toBe(10)
    expect(noteCanvasFontSize(17.7)).toBe(18)
    expect(noteCanvasFontSize(99)).toBe(64)
  })

  it('persists line styles and defaults legacy edges to curves', () => {
    const stored = writeNoteCanvas('', { ...canvas, edges: [{ ...canvas.edges[0]!, lineStyle: 'straight' }] })
    expect(parseNoteCanvas(stored).edges[0]?.lineStyle).toBe('straight')
    expect(parseNoteCanvas(writeNoteCanvas('', canvas)).edges[0]?.lineStyle).toBe('curve')
    expect(noteCanvasLineStyle('other')).toBe('curve')
  })
})
