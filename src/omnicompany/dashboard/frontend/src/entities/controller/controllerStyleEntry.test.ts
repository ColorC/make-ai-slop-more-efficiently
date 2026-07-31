import { describe, expect, it } from 'vitest'
// @ts-expect-error Vitest runs this source-level regression under Node; the
// browser bundle intentionally excludes Node type declarations.
import { readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser.
import { resolve } from 'node:path'

declare const process: { cwd(): string }

const entityEntry = readFileSync(resolve(process.cwd(), 'src/entities/controller/index.tsx'), 'utf8')
const homeView = readFileSync(resolve(process.cwd(), 'src/entities/controller/HomeThreeCards.tsx'), 'utf8')
const styles = readFileSync(resolve(process.cwd(), 'src/entities/controller/controller.css'), 'utf8')

describe('Controller stylesheet entrypoint', () => {
  it('keeps the active controller stylesheet reachable from the registered entity', () => {
    expect(entityEntry).toMatch(/import\s+['"]\.\/controller\.css['"]/)
    expect(homeView).toContain('className="ct-row"')
    expect(styles).toMatch(/\.ct-row\s*\{[\s\S]*?display:\s*grid;/)
  })
})
