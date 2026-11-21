import { expect, test } from '@playwright/test'

const DASHBOARD = `http://127.0.0.1:${process.env.OMNI_E2E_FRONTEND_PORT || process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`

async function cleanCtrlTap(page: import('@playwright/test').Page) {
  await page.keyboard.down('Control')
  await page.keyboard.up('Control')
}

test.describe('Overlay global file search', () => {
  test('double Ctrl searches the host index, exposes the context menu, and opens in Dashboard', async ({ page }) => {
    await page.goto(DASHBOARD)
    await expect(page.getByTestId('cockpit-shell')).toBeVisible({ timeout: 30_000 })

    await cleanCtrlTap(page)
    await cleanCtrlTap(page)

    const search = page.getByPlaceholder('搜索 / 命令: 实体、材料或本机文件…')
    await expect(search).toBeVisible({ timeout: 10_000 })
    await page.getByLabel('搜索范围').selectOption('files')
    await search.fill('PROJECT_INDEX.md')

    const result = page.getByTestId('overlay-file-search-result').first()
    await expect(result).toBeVisible({ timeout: 30_000 })
    await expect(result).toContainText(/PROJECT_INDEX\.md/i)

    await result.click({ button: 'right' })
    const menu = page.getByRole('menu', { name: '文件操作' })
    await expect(menu).toBeVisible()
    await expect(menu.getByText('在 Dashboard 网页中打开')).toBeVisible()
    await expect(menu.getByText('在新浏览器标签打开')).toBeVisible()
    await expect(menu.getByText('复制网页链接')).toBeVisible()
    await expect(menu.getByText('复制文件路径')).toBeVisible()

    await menu.getByText('在 Dashboard 网页中打开').click()
    await expect(page.getByTestId('overlay-file-page')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('overlay-text-preview')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.ov-file-identity')).toContainText(/PROJECT_INDEX\.md/i)
  })
})
