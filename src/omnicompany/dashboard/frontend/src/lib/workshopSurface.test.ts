import { describe, expect, it } from 'vitest'
import { workshopSurfaceUrl } from './workshopSurface'

describe('workshopSurfaceUrl', () => {
  it('hosts the 资料 page with Omni theme and Workshop chrome off', () => {
    expect(workshopSurfaceUrl('materials', { project: 'walker', id: 'design' }))
      .toBe('/bside/materials?theme=omni&embed=1&project=walker&id=design')
  })

  it('maps bside project wiki to the same spark-workshop host as the project page', () => {
    expect(workshopSurfaceUrl('materials', { project: 'bside', session: 'pty-1' }))
      .toBe('/bside/materials?theme=omni&embed=1&project=spark-workshop')
  })

  it('hosts canvas as the original 札记 page', () => {
    expect(workshopSurfaceUrl('canvas', { canvas: 'draft-ab12', note: 'docs/x.md' }))
      .toBe('/api/embed/notes-web/notes-web.html?theme=omni&bridge_prefix=%2Fapi%2Fembed&session=draft-ab12&note=docs%2Fx.md')
  })

  it('drops empty locators', () => {
    expect(workshopSurfaceUrl('canvas', { canvas: undefined, session: '' }))
      .toBe('/api/embed/notes-web/notes-web.html?theme=omni&bridge_prefix=%2Fapi%2Fembed')
  })

  it('keeps the session window id when a canvas pointer is also present', () => {
    expect(workshopSurfaceUrl('canvas', { session: 'pty-1', canvas: 'session-abc' }))
      .toBe('/api/embed/notes-web/notes-web.html?theme=omni&bridge_prefix=%2Fapi%2Fembed&session=pty-1&canvas=session-abc')
  })
})
