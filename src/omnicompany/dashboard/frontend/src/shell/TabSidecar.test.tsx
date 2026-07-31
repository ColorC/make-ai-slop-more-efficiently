import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import TabSidecarLayout, { TabSidecarToggleButton } from './TabSidecar'

describe('TabSidecarLayout', () => {
  beforeEach(() => window.localStorage.clear())

  it('keeps the toggle in the existing header areas and never renders a button-only rail', () => {
    window.localStorage.setItem('omni.tabSidecar.width', '280')
    render(
      <TabSidecarLayout
        sidecar={(
          <header data-testid="sidecar-header">
            <TabSidecarToggleButton label="评价" showWhen="open" />
          </header>
        )}
      >
        <header data-testid="main-header">
          <TabSidecarToggleButton label="评价" showWhen="collapsed" />
        </header>
      </TabSidecarLayout>,
    )

    expect(screen.getByTestId('tab-sidecar')).toBeTruthy()
    expect(screen.getByTestId('tab-sidecar').getAttribute('style')).toContain('width: 520px')
    expect(screen.getByTestId('sidecar-header').contains(screen.getByTestId('tab-sidecar-toggle'))).toBe(true)
    expect(document.querySelector('.tab-sidecar-rail')).toBeNull()

    fireEvent.click(screen.getByTestId('tab-sidecar-toggle'))
    expect(screen.queryByTestId('tab-sidecar')).toBeNull()
    expect(screen.getByTestId('main-header').contains(screen.getByTestId('tab-sidecar-toggle'))).toBe(true)
  })
})
