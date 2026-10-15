import React from 'react'
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PROJECT_ICON_IDS, ProjectIcon } from './projectIcon'

describe('ProjectIcon', () => {
  it('renders a dedicated vector icon for every registered project', () => {
    for (const id of PROJECT_ICON_IDS) {
      const { container, unmount } = render(<ProjectIcon id={id} size={24} />)
      const mark = container.querySelector(`[data-project-icon="${id}"]`)
      expect(mark?.getAttribute('data-registered')).toBe('1')
      expect(mark?.querySelector('svg')).toBeTruthy()
      expect(mark?.textContent).toBe('')
      unmount()
    }
  })

  it('uses a folder vector instead of a text initial for unknown ids', () => {
    const { container } = render(<ProjectIcon id="not-a-project" size={24} />)
    const mark = container.querySelector('[data-project-icon="not-a-project"]')
    expect(mark?.getAttribute('data-registered')).toBe('0')
    expect(mark?.querySelector('svg')).toBeTruthy()
    expect(mark?.textContent).toBe('')
  })

  it('uses the dedicated Spark fish silhouette only for Spark WebTwin', () => {
    const { container } = render(<ProjectIcon id="spark-webtwin" size={24} />)
    const sparkMark = container.querySelector('[data-project-icon="spark-webtwin"]')
    expect(sparkMark?.querySelector('svg.lucide-fish-symbol')).toBeTruthy()

    for (const id of PROJECT_ICON_IDS.filter((projectId) => projectId !== 'spark-webtwin')) {
      const { container: other, unmount } = render(<ProjectIcon id={id} size={24} />)
      expect(other.querySelector('svg.lucide-fish-symbol')).toBeFalsy()
      unmount()
    }
  })

  it('keeps every registered project silhouette unique', () => {
    const silhouettes: string[] = []
    for (const id of PROJECT_ICON_IDS) {
      const { container, unmount } = render(<ProjectIcon id={id} size={24} />)
      const className = container.querySelector('svg')?.getAttribute('class') || ''
      const silhouette = className.split(/\s+/).find((name) => name.startsWith('lucide-') && name !== 'lucide')
      expect(silhouette, id).toBeTruthy()
      silhouettes.push(silhouette || '')
      unmount()
    }
    expect(new Set(silhouettes).size).toBe(PROJECT_ICON_IDS.length)
  })
})
