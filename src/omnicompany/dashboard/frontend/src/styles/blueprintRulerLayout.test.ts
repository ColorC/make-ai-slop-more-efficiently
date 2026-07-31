// @ts-expect-error Vitest executes this source contract under Node only.
import { readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser bundle.
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

declare const process: { cwd(): string }

const css = readFileSync(resolve(process.cwd(), 'src/styles/frostpane.css'), 'utf8')
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')

describe('blueprint rulers', () => {
  it('keeps the horizontal ruler at the bottom, clear of the dock tab strip', () => {
    expect(css).toMatch(/\.bp-ruler-x\s*\{[^}]*left:\s*56px;[^}]*bottom:\s*0;[^}]*height:\s*26px;/s)
    expect(css).toMatch(/\.bp-ruler-x span\s*\{[^}]*bottom:\s*14px;/s)
    expect(css).not.toMatch(/\.bp-ruler-x\s*\{[^}]*top:\s*28px;/s)
  })

  it('keeps the vertical ruler on the right below review header actions', () => {
    expect(css).toMatch(/\.bp-ruler-y\s*\{[^}]*top:\s*150px;[^}]*right:\s*0;/s)
    expect(css).toMatch(/\.bp-ruler-y span\s*\{[^}]*right:\s*6px;/s)
    expect(css).not.toMatch(/\.bp-ruler-y\s*\{[^}]*left:\s*56px;/s)
  })

  it('retains the aria-hidden ruler layer and its static generators', () => {
    expect(html).toContain('<div id="bp-grid" aria-hidden="true">')
    expect(html).toContain('id="bpRulerX"')
    expect(html).toContain('id="bpRulerY"')
  })
})
