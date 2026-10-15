// @ts-expect-error Vitest executes this source contract under Node only.
import { readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser bundle.
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

declare const process: { cwd(): string }

describe('session note runtime ownership', () => {
  it('does not load a second standalone React runtime from the page shell', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')
    expect(html).not.toContain('/lofa/overlay/app/assets/notes-embed.js')
    expect(html).not.toContain('/lofa/overlay/app/assets/notes-embed.css')
  })
})
