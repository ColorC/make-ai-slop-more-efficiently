import { describe, expect, it } from 'vitest'
// @ts-expect-error Vitest runs this source-level regression under Node; the
// browser bundle intentionally excludes Node type declarations.
import { readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser.
import { resolve } from 'node:path'

declare const process: { cwd(): string }

const styles = readFileSync(resolve(process.cwd(), 'src/entities/multiagent/multiagent.css'), 'utf8')

describe('Multiagent running-state geometry', () => {
  it('clips effects inside each row and never translates the scan outside its box', () => {
    expect(styles).toMatch(/\.ma-row\s*\{[\s\S]*?overflow:\s*hidden;/)
    expect(styles).toMatch(/\.ma-row\s*\{[\s\S]*?contain:\s*paint;/)
    const dash = styles.match(/@keyframes ma-work-dash\s*\{([\s\S]*?)\}/)?.[1] || ''
    expect(dash).toContain('background-position')
    expect(dash).not.toContain('transform')
    const corners = styles.match(/@keyframes ma-work-corners\s*\{([\s\S]*?)\}/)?.[1] || ''
    expect(corners).toContain('background-image')
    expect(styles).toContain('overflow-x: hidden')
  })

  it('uses compact two-line rows and drafting-line animations instead of a status dot', () => {
    expect(styles).toContain('grid-template-columns: 40px minmax(0, 1fr) auto auto')
    expect(styles).toContain('min-height: 50px')
    expect(styles).toContain('.ma-row-copy')
    expect(styles).toContain('.ma-row-context')
    // 蓝图描线三件套: 顶部虚线巡扫 + 四角制图角标 + 罗盘针环扫
    expect(styles).toContain('@keyframes ma-work-dash')
    expect(styles).toContain('@keyframes ma-work-corners')
    expect(styles).toContain('@keyframes ma-compass-trace')
    expect(styles).not.toContain('.ma-state-dot')
    expect(styles).toMatch(/\.ma-project-mark\s*\{[\s\S]*?width:\s*34px;[\s\S]*?height:\s*34px;/)
    expect(styles).toMatch(/\.ma-cli-mark\s*\{[\s\S]*?width:\s*18px;[\s\S]*?height:\s*18px;/)
  })
})
