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
    const scan = styles.match(/@keyframes ma-work-scan\s*\{([\s\S]*?)\}/)?.[1] || ''
    expect(scan).toContain('background-position')
    expect(scan).not.toContain('transform')
    expect(styles).toContain('overflow-x: hidden')
  })
})
