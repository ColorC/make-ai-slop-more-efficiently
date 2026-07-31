import { describe, expect, it } from 'vitest'
// @ts-expect-error Vitest runs this source-level regression under Node; the
// browser bundle intentionally excludes Node type declarations.
import { readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser.
import { resolve } from 'node:path'

declare const process: { cwd(): string }

describe('Review sidebars content contract', () => {
  it('does not reintroduce the removed newest-material summary', () => {
    for (const file of ['ReviewQueueSidebar.tsx', 'MaterialSidebar.tsx']) {
      const source = readFileSync(resolve(process.cwd(), 'src/entities/review', file), 'utf8')
      expect(source).not.toContain('最新:')
      expect(source).not.toMatch(/const\s+newest\s*=\s*unit\.items\[0\]/)
    }
  })
})
