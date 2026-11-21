import { beforeEach, describe, expect, it } from 'vitest'
import { usePanels } from '../stores/panelsStore'
import {
  openOverlayFileInDashboard,
  overlayFileWebUrl,
} from './overlayFileNavigation'

const target = {
  name: 'README.md',
  path: 'E:\\README.md',
  kind: 'file',
  open_token: 'signed-token',
}

beforeEach(() => {
  usePanels.getState().setTabs([], null)
})

describe('overlay file web navigation', () => {
  it('opens the signed result as an overlay_file Dashboard tab', () => {
    expect(openOverlayFileInDashboard(target)).toBe('overlay_file:signed-token')
    expect(usePanels.getState().tabs).toEqual([
      {
        id: 'overlay_file:signed-token',
        ref: { type: 'overlay_file', id: 'signed-token' },
        title: 'README.md',
        placement: undefined,
      },
    ])
    expect(usePanels.getState().activeId).toBe('overlay_file:signed-token')
  })

  it('creates a browser deeplink instead of a local protocol URL', () => {
    const url = new URL(overlayFileWebUrl(target, 'http://dashboard.test:8210'))
    expect(url.origin).toBe('http://dashboard.test:8210')
    expect(url.searchParams.get('open_type')).toBe('overlay_file')
    expect(url.searchParams.get('open_id')).toBe('signed-token')
    expect(url.protocol).toBe('http:')
  })
})
