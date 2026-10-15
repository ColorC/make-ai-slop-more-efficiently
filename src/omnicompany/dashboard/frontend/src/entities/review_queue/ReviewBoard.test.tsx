import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReviewBoard } from './ReviewBoard'

describe('ReviewBoard sidebar', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('can collapse and reopen the material list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ items: [] }),
    })))

    render(<ReviewBoard />)

    await waitFor(() => expect(screen.getByTestId('review-board-list')).toBeTruthy())
    const toggle = screen.getByTestId('review-board-toggle-sidebar')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')

    fireEvent.click(toggle)
    expect(screen.queryByTestId('review-board-list')).toBeNull()
    const main = screen.getByTestId('review-board').querySelector('.rb-main')
    expect(main?.children).toHaveLength(1)
    expect(main?.firstElementChild).toBe(screen.getByTestId('review-board-detail'))
    expect(screen.getByTestId('review-board-toggle-sidebar').getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(screen.getByTestId('review-board-toggle-sidebar'))
    expect(screen.getByTestId('review-board-list')).toBeTruthy()
    expect(screen.getByTestId('review-board-toggle-sidebar').getAttribute('aria-expanded')).toBe('true')
  })
})
