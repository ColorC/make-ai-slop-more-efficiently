import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
// 唯一画布前端真源 2026-08-17 起在本仓 dashboard/notes-web/(原 overlay-shell 仓已退役删除)。
const overlay = (...parts: string[]) => readFileSync(resolve(here, '../../../notes-web', ...parts), 'utf8')

describe('札记 appearance modes', () => {
  it('keeps Omni and Workshop as complete sibling modes', () => {
    const structure = overlay('src/regions/material-notes.css')
    const omni = overlay('src/regions/notes-appearance/omni.css')
    const workshop = overlay('src/regions/notes-appearance/workshop.css')

    expect(structure).toContain('@import "./notes-appearance/omni.css"')
    expect(structure).toContain('@import "./notes-appearance/workshop.css"')
    expect(omni).toContain("color-scheme: dark")
    expect(workshop).toContain("color-scheme: light")
    expect(workshop).toContain('--fp-bp-solid: #FFFCF6')
    expect(workshop).toContain('--fp-bp-tracing: rgba(255, 252, 246, 0.86)')
    expect(workshop).toContain('.material-font-size-combobox')
    expect(workshop).toContain('.react-flow__controls-button')
    expect(workshop).toContain('.doc-editor')
    expect(workshop).toContain('.doc-outline')
    expect(workshop).toContain('color-scheme: light !important')
    expect(omni).toContain('.material-font-size-combobox')
    expect(omni).toContain('.react-flow__controls-button')
    expect(omni).toContain('.doc-editor')
  })

  it('opens dashboard 札记 in a filling notes-web host', () => {
    const authored = readFileSync(resolve(here, '../entities/authored/index.tsx'), 'utf8')
    const companion = readFileSync(resolve(here, '../entities/cc_session/SessionCompanion.tsx'), 'utf8')
    const html = overlay('notes-web.html')
    expect(authored).toContain('au-shell')
    expect(authored).toContain('/api/embed/notes-web/notes-web.html')
    expect(html).toContain('dataset.notesTheme')
    expect(companion).toContain('companion-notes-host')
    expect(companion).toMatch(/companion-notes-host[\s\S]*React\.Suspense/)
    expect(companion).toContain('札记')
  })

  it('keeps the companion sidebar (not a terminal button) as the only 札记 entry', () => {
    const editor = readFileSync(resolve(here, '../entities/cc_session/Editor.tsx'), 'utf8')
    const chat = readFileSync(resolve(here, '../entities/cc_session/NativeChatPanel.tsx'), 'utf8')
    const companion = readFileSync(resolve(here, '../entities/cc_session/SessionCompanion.tsx'), 'utf8')
    const editorArea = readFileSync(resolve(here, '../shell/EditorArea.tsx'), 'utf8')
    // 2026-08-16 用户裁决: 终端右上角札记按钮是错误方案, 撤掉。
    expect(editor).not.toContain('session-open-notes')
    expect(chat).not.toContain('session-open-notes')
    // 札记入口 = 伴随侧栏页签, 且伴随侧栏默认展开(旧持久化经 collapsed.v2 键升位失效)。
    expect(companion).toContain('companion-nav-notes')
    expect(editorArea).toContain('defaultCollapsed={reviewSidecar ? true : companionSessionId ? false : null}')
  })
})
