import { expect, test, type Page } from '@playwright/test'
import { setupErrorLogging } from './helpers'

function collectFatalReactErrors(page: Page) {
  const errors: string[] = []
  const isFatal = (text: string) => /185|Maximum update depth|Minified React error/i.test(text)
  page.on('pageerror', (error) => {
    if (isFatal(error.message)) errors.push(error.message)
  })
  page.on('console', (message) => {
    if (message.type() === 'error' && isFatal(message.text())) errors.push(message.text())
  })
  return errors
}

test('starting a relation drag does not throw React #185', async ({ page }) => {
  setupErrorLogging(page)
  const fatal = collectFatalReactErrors(page)
  const canvas = `connect-probe-${Date.now()}`
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto(`/lofa/overlay/app/notes-web.html?canvas=${encodeURIComponent(canvas)}`)
  await expect(page.locator('[data-material-canvas="ready"]')).toBeVisible({ timeout: 20_000 })

  const nodes = page.locator('.react-flow__node')
  await expect(nodes.first()).toBeVisible({ timeout: 10_000 })
  await nodes.first().getByRole('button', { name: '编辑 Markdown' }).click()
  await nodes.first().locator('textarea').fill('卡片甲')
  await nodes.first().getByTitle('完成编辑并预览 Markdown').click()
  await expect(nodes.first()).toContainText('卡片甲')
  await expect(nodes.first()).not.toContainText('未保存', { timeout: 8_000 })
  await page.locator('.react-flow__pane').click({ button: 'right', position: { x: 80, y: 80 } })
  await page.getByTestId('material-context-menu').getByText('新建文字卡').click()
  await expect(nodes).toHaveCount(2, { timeout: 8_000 })

  const source = nodes.nth(0)
  const target = nodes.nth(1)
  await page.keyboard.press('Escape')
  await source.hover()
  const sourceHandle = source.locator('.react-flow__handle-right').first()
  await expect(sourceHandle).toBeVisible()
  const start = await sourceHandle.boundingBox()
  const endBox = await target.boundingBox()
  if (!start || !endBox) throw new Error('missing handle or target geometry')

  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2)
  await page.mouse.down()
  await page.mouse.move(endBox.x + 24, endBox.y + endBox.height / 2, { steps: 12 })
  await expect(page.locator('.react-flow__connectionline')).toBeVisible({ timeout: 3_000 })
  await page.mouse.up()

  await expect(page.locator('[data-material-canvas="ready"]')).toBeVisible()
  expect(fatal, fatal.join('\n')).toEqual([])
})
