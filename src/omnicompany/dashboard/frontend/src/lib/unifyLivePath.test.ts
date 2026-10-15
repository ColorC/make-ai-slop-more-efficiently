import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { workshopSurfaceUrl } from './workshopSurface'

const here = dirname(fileURLToPath(import.meta.url))
const src = (...parts: string[]) => readFileSync(resolve(here, ...parts), 'utf8')

describe('live Wiki and Canvas hosts', () => {
  it('builds the one Workshop surface with omni theme', () => {
    expect(workshopSurfaceUrl('materials', { project: 'walker' }))
      .toBe('/bside/materials?theme=omni&embed=1&project=walker')
    expect(workshopSurfaceUrl('canvas', { session: 'pty-1' }))
      .toBe('/api/embed/notes-web/notes-web.html?theme=omni&bridge_prefix=%2Fapi%2Fembed&session=pty-1')
  })

  it('keeps one Wiki Core and one Material Core reader on every live project path', () => {
    const reader = src('../entities/studio_reader/index.tsx')
    const companion = src('../entities/cc_session/SessionCompanion.tsx')
    const projectDetail = src('../entities/project/ProjectDetail.tsx')
    const projectHome = src('../entities/project/ProjectWikiHome.tsx')
    const wikiBridge = src('../shared/view/wiki/WikiCoreMarkdown.tsx')
    const materialBridge = src('../shared/view/wiki/MaterialCoreWikiMarkdown.tsx')
    const noteEditor = src('../entities/note/Editor.tsx')

    expect(reader).toContain('WorkshopSurface')
    expect(reader).toContain('kind="materials"')
    expect(reader).toContain('MaterialWikiManageBar')
    expect(projectDetail).toContain('ProjectWikiHome')
    expect(projectDetail).toContain('<WorkshopSurface')
    expect(projectDetail).not.toContain('MaterialWikiWorkspace')
    expect(companion).toContain('ProjectWikiHome')
    expect(companion).toContain('project ?')
    expect(companion).toContain('<ReviewBoard />')
    expect(companion).not.toContain('<ProjectDetail')
    expect(projectHome).toContain('MaterialCoreWikiMarkdown')
    expect(wikiBridge).toContain("from '@wiki-core/render'")
    expect(materialBridge).toContain('<WorkshopSurface')
    expect(companion).not.toMatch(/<MaterialNotesWorkspace/)
    expect(noteEditor).toContain('kind="canvas"')
    expect(noteEditor).not.toMatch(/<NoteCanvas/)

    const retired = [
      '../shell/MarkdownRenderer.tsx',
      '../entities/studio_reader/MaterialWikiWorkspace.tsx',
      '../entities/studio_reader/MaterialWikiPanel.tsx',
      '../entities/studio_reader/MaterialWikiInlineEmbed.tsx',
    ]
    for (const relative of retired) {
      expect(existsSync(resolve(here, relative)), `${relative} must stay deleted`).toBe(false)
    }
  })
})
