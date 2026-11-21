import { describe, expect, it } from 'vitest'
import { resolveEntryRoute } from './entryRoute'

describe('entry route normalization', () => {
  it('keeps the cockpit shell as the default root', () => {
    expect(resolveEntryRoute('/', '')).toEqual({
      root: 'app',
      pathname: '/',
      search: '',
      replace: false,
    })
  })

  it('keeps embedded chat standalone for iframe consumers', () => {
    expect(resolveEntryRoute('/chat-standalone', '?provider=controller&embedded=1')).toEqual({
      root: 'chat',
      pathname: '/chat-standalone',
      search: '?provider=controller&embedded=1',
      replace: false,
    })
  })

  it('keeps legacy chat standalone when explicitly requested', () => {
    expect(resolveEntryRoute('/chat-standalone', '?legacy=1')).toEqual({
      root: 'chat',
      pathname: '/chat-standalone',
      search: '?legacy=1',
      replace: false,
    })
  })

  it('turns plain OmniChat entry into the cockpit shell', () => {
    expect(resolveEntryRoute('/chat-standalone', '?provider=controller&omnichat_webview=123')).toEqual({
      root: 'app',
      pathname: '/',
      search: '?omnichat_webview=123',
      replace: true,
    })
  })

  it('turns full chat session links into cockpit cc_session deep links', () => {
    expect(resolveEntryRoute('/chat-standalone', '?session=chat-abc&omnichat_webview=123')).toEqual({
      root: 'app',
      pathname: '/',
      search: '?omnichat_webview=123&open_type=cc_session&open_id=chat-abc&open_title=chat-abc',
      replace: true,
    })
  })

  it('maps legacy review-stage material links to the cockpit review_material deeplink', () => {
    expect(resolveEntryRoute('/review-stage', '?material=mat-1')).toEqual({
      root: 'app',
      pathname: '/',
      search: '?open_type=review_material&open_id=mat-1',
      replace: true,
    })
  })

  it('maps bare review-stage links to the cockpit review queue', () => {
    expect(resolveEntryRoute('/review-stage', '')).toEqual({
      root: 'app',
      pathname: '/',
      search: '?open_type=review_queue&open_id=main',
      replace: true,
    })
  })
})
