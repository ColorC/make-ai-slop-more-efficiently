import { describe, expect, it } from 'vitest'
import { parseWikilink } from './wikilinks'

describe('parseWikilink entity routing', () => {
  it('keeps ordinary links in the note domain', () => {
    expect(parseWikilink('standards/terminology#Naming|术语')).toEqual({
      entityType: 'note',
      target: 'standards/terminology',
      heading: 'Naming',
      display: '术语',
    })
  })

  it('routes canonical material links without implementing a second markdown parser', () => {
    expect(parseWikilink('material:walker.card.demo|示例')).toEqual({
      entityType: 'material',
      target: 'walker.card.demo',
      display: '示例',
      heading: undefined,
    })
    expect(parseWikilink('omni://review/mat%3A1')).toEqual({
      entityType: 'review_material',
      target: 'mat:1',
      display: 'omni://review/mat%3A1',
      heading: undefined,
    })
  })
})
