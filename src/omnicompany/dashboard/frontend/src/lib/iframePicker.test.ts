import { describe, it, expect } from 'vitest'
import { buildSelectorPath } from './iframePicker'

describe('buildSelectorPath', () => {
  it('returns #id when the element has an id', () => {
    const el = document.createElement('div')
    el.id = 'app'
    expect(buildSelectorPath(el, document)).toBe('#app')
  })

  it('builds a chain that resolves back to the element', () => {
    document.body.innerHTML = '<section class="scene"><div class="title"><h1>行者无乡</h1></div></section>'
    const h1 = document.querySelector('h1')!
    const sel = buildSelectorPath(h1, document)
    expect(sel).toContain('section.scene')
    expect(document.querySelector(sel)).toBe(h1)
  })

  it('falls back to nth-child for class-less elements', () => {
    document.body.innerHTML = '<ul><li>a</li><li>b</li></ul>'
    const second = document.querySelectorAll('li')[1]
    expect(buildSelectorPath(second, document)).toContain(':nth-child(2)')
  })
})
