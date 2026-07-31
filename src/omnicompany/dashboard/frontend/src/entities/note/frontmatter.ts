// [OMNI] origin=claude-code ts=2026-05-02 type=infra
// Frontmatter rendering: remark-frontmatter parses the `---\n...\n---` YAML
// block at top of a note into a `yaml` node; this plugin converts that node
// into a placeholder div carrying the parsed JSON as data-attr, which the
// MarkdownRenderer's `div` component then renders as a small metadata card.
import { parse as yamlParse } from 'yaml'

export function remarkFrontmatterRender() {
  return (tree: any) => {
    if (!tree || !Array.isArray(tree.children)) return
    const idx = tree.children.findIndex((c: any) => c.type === 'yaml')
    if (idx < 0) return
    const raw = tree.children[idx].value || ''
    let parsed: Record<string, unknown> = {}
    let parseError: string | null = null
    try {
      const v = yamlParse(raw)
      if (v && typeof v === 'object' && !Array.isArray(v)) parsed = v as Record<string, unknown>
    } catch (e) {
      parseError = String(e)
    }
    tree.children[idx] = {
      type: 'paragraph',  // valid mdast node we can hijack via hName
      data: {
        hName: 'div',
        hProperties: {
          'data-frontmatter': '1',
          'data-frontmatter-json': JSON.stringify(parsed),
          'data-frontmatter-error': parseError || '',
          'data-frontmatter-raw': raw,
        },
      },
      children: [],
    }
  }
}
