import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import TabSidecarLayout, { TabSidecarToggleButton } from './TabSidecar'

function ReviewLayout({ resetKey }: { resetKey: string }) {
  return (
    <TabSidecarLayout
      sidecar={<div data-testid="review-sidecar-content">评论</div>}
      defaultCollapsed
      defaultWidth={360}
      collapsedPreferenceKey={null}
      widthPreferenceKey="omni.tabSidecar.reviewComments.width"
      resetKey={resetKey}
    >
      <TabSidecarToggleButton label="评价与批注" showWhen="collapsed" testId="review-open-toggle" />
    </TabSidecarLayout>
  )
}

describe('review comments sidecar defaults', () => {
  beforeEach(() => window.localStorage.clear())

  it('starts closed, opens at the narrower width, and closes again for another review tab', async () => {
    const view = render(<ReviewLayout resetKey="review_material:a" />)

    expect(screen.queryByTestId('tab-sidecar')).toBeNull()
    fireEvent.click(screen.getByTestId('review-open-toggle'))
    expect(screen.getByTestId('tab-sidecar').getAttribute('style')).toContain('width: 360px')
    expect(window.localStorage.getItem('omni.tabSidecar.collapsed')).toBeNull()

    view.rerender(<ReviewLayout resetKey="review_material:b" />)
    await waitFor(() => expect(screen.queryByTestId('tab-sidecar')).toBeNull())
  })
})
