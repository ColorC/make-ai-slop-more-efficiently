import { describe, expect, it } from 'vitest'

import { parseWikilink, remarkWikilinks } from './wikilinks'

describe('review material wikilinks', () => {
  it('maps the public review URI to the existing internal review_material surface', () => {
    expect(parseWikilink('omni://review/mat%20candidate|候选背景')).toEqual({
      entityType: 'review_material',
      target: 'mat candidate',
      display: '候选背景',
      heading: undefined,
    })
    expect(parseWikilink('review:mat_2').entityType).toBe('review_material')
  })

  it('turns an explicit review embed into a canonical material embed sentinel', () => {
    const tree: any = {
      type: 'root',
      children: [{
        type: 'paragraph',
        children: [{ type: 'text', value: '前 ![[omni://review/mat%201|候选背景]] 后' }],
      }],
    }

    remarkWikilinks()(tree)

    const children = tree.children[0].children
    expect(children).toHaveLength(3)
    expect(children[1].data.hName).toBe('span')
    expect(children[1].data.hProperties['data-review-material-embed'])
      .toBe('omni://review/mat%201')
    expect(children[1].data.hProperties['data-review-material-label']).toBe('候选背景')
  })
})
