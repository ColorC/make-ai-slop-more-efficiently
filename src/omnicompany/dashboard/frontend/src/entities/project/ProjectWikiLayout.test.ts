import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const projectDetail = readFileSync(resolve(process.cwd(), 'src/entities/project/ProjectDetail.tsx'), 'utf8')
const wikiHome = readFileSync(resolve(process.cwd(), 'src/entities/project/ProjectWikiHome.tsx'), 'utf8')
const styles = readFileSync(resolve(process.cwd(), 'src/entities/project/projectDetail.css'), 'utf8')

describe('project Wiki responsive navigation', () => {
  it('removes the rail from layout while keeping a toggle in the document toolbar', () => {
    expect(projectDetail).toContain('{railOpen && <aside className="pd-rail"')
    expect(projectDetail).toContain('testId="pd-toggle-rail"')
    expect(projectDetail).toContain('data-rail-collapsed={railOpen ? \'0\' : \'1\'}')
    expect(styles).toMatch(/\.pd-root\[data-rail-collapsed="1"\]\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/s)
  })

  it('hides the outline from the Wiki container width instead of the viewport width', () => {
    expect(wikiHome).toContain('data-testid="pd-outline"')
    expect(styles).toContain('container: project-wiki / inline-size')
    expect(styles).toMatch(/@container project-wiki \(max-width: 760px\)[\s\S]*?\.pd-outline\s*\{\s*display:\s*none;/)
  })
})
