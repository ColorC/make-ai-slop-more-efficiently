import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TraceMenu } from './TraceMenu'

describe('TraceMenu', () => {
  it('portals the popup outside an overflow-clipping toolbar', () => {
    const onChoose = vi.fn()
    render(
      <div data-testid="toolbar" style={{ overflowX: 'auto' }}>
        <TraceMenu
          label="toc"
          align="right"
          minWidth={240}
          trigger={(open, toggle) => (
            <button type="button" aria-expanded={open} onClick={toggle}>open</button>
          )}
        >
          {(close) => (
            <button type="button" onClick={() => { onChoose(); close() }}>choose</button>
          )}
        </TraceMenu>
      </div>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    const popup = screen.getByRole('group', { name: 'toc' })
    expect(screen.getByTestId('toolbar').contains(popup)).toBe(false)
    expect(popup.style.position).toBe('fixed')
    expect(popup.style.visibility).toBe('visible')

    fireEvent.click(screen.getByRole('button', { name: 'choose' }))
    expect(onChoose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('group', { name: 'toc' })).toBeNull()
  })
})
