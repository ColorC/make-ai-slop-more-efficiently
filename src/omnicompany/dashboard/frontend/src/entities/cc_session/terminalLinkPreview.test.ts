import { describe, expect, it } from 'vitest'
import { itemForUrl } from './terminalLinkPreview'

describe('itemForUrl', () => {
  it('turns canvas card links into card items', () => {
    expect(itemForUrl('https://workshop.example.com/bside/canvas?project=p&canvas=c#card=k')).toMatchObject({ kind: 'card', canvas: 'c', card: 'k' })
  })
  it('turns media links into image / video items', () => {
    expect(itemForUrl('https://cdn.x/a/shot.PNG?v=2')?.kind).toBe('image')
    expect(itemForUrl('https://cdn.x/clip.mp4')?.kind).toBe('video')
  })
  it('leaves ordinary pages alone', () => {
    expect(itemForUrl('https://example.com/docs')).toBeNull()
  })
})
