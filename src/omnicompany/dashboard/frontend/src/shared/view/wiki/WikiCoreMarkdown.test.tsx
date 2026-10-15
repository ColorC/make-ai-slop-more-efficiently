import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { usePanels } from '../../../stores/panelsStore'
import WikiCoreMarkdown from './WikiCoreMarkdown'

describe('WikiCoreMarkdown', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: [], activeId: null } as never)
  })

  it('uses Wiki Core for Obsidian syntax and wide tables', () => {
    const { container } = render(<WikiCoreMarkdown source={'# 首页\n\n==重点==\n\n| A | B |\n|---|---|\n| 1 | 2 |'} />)
    expect(container.querySelector('.wiki-prose mark')?.textContent).toBe('重点')
    expect(container.querySelector('.wiki-prose table')).toBeTruthy()
  })

  it('routes Wiki Core links through the dashboard tab host', () => {
    render(<WikiCoreMarkdown source={'[[plan:plan-1|计划一]]'} />)
    fireEvent.click(screen.getByRole('link', { name: '计划一' }))
    expect(usePanels.getState().tabs.some((tab) => tab.ref.type === 'plan' && tab.ref.id === 'plan-1')).toBe(true)
  })

  it('routes project material links into the one Material Core reader', () => {
    render(<WikiCoreMarkdown source={'[[material:mat-1|材料一]]'} materialProject="vilo" />)
    fireEvent.click(screen.getByRole('link', { name: '材料一' }))
    expect(usePanels.getState().tabs.some((tab) => (
      tab.ref.type === 'studio_reader' && tab.ref.id === 'vilo' && tab.facet === 'mat-1'
    ))).toBe(true)
  })

  it('mounts rich material embeds into Wiki Core transclusion placeholders', () => {
    render(<WikiCoreMarkdown
      source={'![[material:Vilo.材料.角色|角色材料]]'}
      renderEmbed={(target) => <span data-testid="rich-embed">{target.kind}:{target.target}</span>}
    />)
    expect(screen.getByTestId('rich-embed').textContent).toBe('material:Vilo.材料.角色')
  })
})
