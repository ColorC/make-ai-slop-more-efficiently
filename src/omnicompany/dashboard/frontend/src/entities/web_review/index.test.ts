import { describe, it, expect } from 'vitest'
import { webReviewRegistration, WEB_REVIEW_TARGETS } from './index'

describe('web_review entity', () => {
  it('registers resolver and renderer under the same type', () => {
    expect(webReviewRegistration.resolver.type).toBe('web_review')
    expect(webReviewRegistration.renderer.type).toBe('web_review')
  })

  it('resolves the walker-game target to a same-origin url', async () => {
    const e = await webReviewRegistration.resolver.fetch('walker-game')
    expect(e.id).toBe('walker-game')
    expect(e.meta?.url).toBe('/walker-game/')
    // same-origin path (no scheme/host) is what makes element-select work
    expect(String(e.meta?.url).startsWith('/')).toBe(true)
  })

  it('resolves the vilo demo target through the dashboard proxy', async () => {
    const e = await webReviewRegistration.resolver.fetch('vilo-demo')
    expect(e.id).toBe('vilo-demo')
    // demo 在 webworks 根的 /apps/tabletop-simulator/ 子路径(见 index.tsx 注释)
    expect(e.meta?.url).toBe('/vilo-demo/apps/tabletop-simulator/?scenario=vilo-7plus3-demo')
    expect(e.meta?.route).toBe('/apps/tabletop-simulator/?scenario=vilo-7plus3-demo')
    expect(String(e.meta?.url).startsWith('/')).toBe(true)
  })

  it('resolves Vilo OS through the dashboard proxy', async () => {
    const e = await webReviewRegistration.resolver.fetch('vilo-os')
    expect(e.meta?.url).toBe('/vilo-os/')
    expect(e.meta?.route).toBe('/')
  })

  it('lists built-in targets and throws for unknown ids', async () => {
    const list = await webReviewRegistration.resolver.list()
    expect(list.map((e) => e.id)).toContain('walker-game')
    expect(list.map((e) => e.id)).toContain('vilo-demo')
    expect(list.map((e) => e.id)).toContain('vilo-os')
    expect(Object.keys(WEB_REVIEW_TARGETS)).toContain('walker-game')
    expect(Object.keys(WEB_REVIEW_TARGETS)).toContain('vilo-demo')
    expect(Object.keys(WEB_REVIEW_TARGETS)).toContain('vilo-os')
    await expect(webReviewRegistration.resolver.fetch('nope')).rejects.toThrow()
  })
})
