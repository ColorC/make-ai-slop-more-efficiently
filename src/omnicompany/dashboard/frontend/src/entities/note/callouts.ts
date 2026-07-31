import { visit } from 'unist-util-visit'

const CALLOUT_RE = /^\[!(\w+)\](\+|\-)?[ \t]*([^\n]*)/

export type CalloutType = 'note' | 'tip' | 'warning' | 'danger' | 'info' | 'success' | 'quote' | 'todo' | 'example' | 'abstract' | 'question' | 'failure' | 'bug'

export const CALLOUT_COLORS: Record<string, { color: string; bg: string; icon: string }> = {
  note: { color: '#90caf9', bg: '#0f1820', icon: 'ⓘ' },
  info: { color: '#90caf9', bg: '#0f1820', icon: 'ⓘ' },
  abstract: { color: '#90caf9', bg: '#0f1820', icon: '⚭' },
  tip: { color: '#4caf50', bg: '#0d1810', icon: '✦' },
  success: { color: '#4caf50', bg: '#0d1810', icon: '✓' },
  example: { color: '#7e57c2', bg: '#15101e', icon: '✎' },
  quote: { color: '#888', bg: '#0e0e0e', icon: '“' },
  question: { color: '#26c6da', bg: '#0a1719', icon: '?' },
  warning: { color: '#ffb74d', bg: '#1a1408', icon: '⚠' },
  todo: { color: '#42a5f5', bg: '#0c151b', icon: '☐' },
  danger: { color: '#ef5350', bg: '#1a0a0a', icon: '⚡' },
  failure: { color: '#ef5350', bg: '#1a0a0a', icon: '✗' },
  bug: { color: '#ef5350', bg: '#1a0a0a', icon: '🐛' },
}

/** remark plugin: detect Obsidian callout `> [!type] title` in blockquotes,
 *  add `data-callout=type / data-title=...` to the blockquote. */
export function remarkCallouts() {
  return (tree: any) => {
    visit(tree, 'blockquote', (node: any) => {
      const first = node.children?.[0]
      if (!first || first.type !== 'paragraph') return
      const firstText = first.children?.[0]
      if (!firstText || firstText.type !== 'text') return
      const m = firstText.value.match(CALLOUT_RE)
      if (!m) return
      const type = (m[1] || 'note').toLowerCase()
      const title = m[3]?.trim() || type
      // strip the `[!type]...` first line from first text node; preserve trailing content
      const firstLineEnd = firstText.value.indexOf('\n')
      const remaining = firstLineEnd >= 0 ? firstText.value.slice(firstLineEnd + 1) : ''
      if (remaining.trim()) {
        firstText.value = remaining
      } else if (first.children.length > 1) {
        first.children.shift()
      } else {
        node.children.shift()
      }
      node.data = node.data || {}
      node.data.hName = 'div'
      node.data.hProperties = {
        'data-callout': type,
        'data-callout-title': title,
        className: ['callout', `callout-${type}`],
      }
    })
  }
}
