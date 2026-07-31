// [OMNI] origin=claude-code ts=2026-05-02 type=infra
// Obsidian-flavored markdown extras (S20):
//   `==text==`  → <mark>text</mark>  (highlight)
//   `%% text %%` → removed entirely  (invisible comment)
//
// Both run as remark text-node visitors. Comments stripping happens BEFORE
// other plugins (so wikilinks etc. inside %%...%% are also dropped), so we
// expose it as a separate plugin you can place at the head of the chain.

import { visit, SKIP } from 'unist-util-visit'

const HIGHLIGHT = /==([^=]+?)==/g
// `%%` ... `%%` non-greedy, can span lines. We strip by source mutation, not AST.
const COMMENT = /%%[\s\S]*?%%/g

export function remarkObsidianHighlight() {
  return (tree: any) => {
    visit(tree, 'text', (node: any, index: any, parent: any) => {
      if (!parent || index == null) return
      const value: string = node.value
      if (!value || !value.includes('==')) return
      HIGHLIGHT.lastIndex = 0
      const out: any[] = []
      let last = 0
      let m: RegExpExecArray | null
      while ((m = HIGHLIGHT.exec(value)) !== null) {
        if (m.index > last) out.push({ type: 'text', value: value.slice(last, m.index) })
        out.push({
          type: 'highlight',
          data: {
            hName: 'mark',
            hProperties: { className: ['obs-highlight'], 'data-obs-highlight': '1' },
          },
          children: [{ type: 'text', value: m[1] }],
        })
        last = m.index + m[0].length
      }
      if (last === 0) return
      if (last < value.length) out.push({ type: 'text', value: value.slice(last) })
      parent.children.splice(index, 1, ...out)
      return [SKIP, index + out.length]
    })
  }
}

/** Source-level pre-processor: strip `%% ... %%` blocks. Use BEFORE feeding markdown to ReactMarkdown. */
export function stripObsidianComments(source: string): string {
  return source.replace(COMMENT, '')
}
