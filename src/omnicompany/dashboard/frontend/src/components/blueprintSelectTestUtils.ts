// BlueprintSelect 的测试驱动助手。
//
// 原生 <select> 可以用 fireEvent.change(el, {target:{value}}) 一步改值; BlueprintSelect 是
// 自绘件(button[role=combobox] + listbox), 必须"先开再点"。全站 select 迁移后, 单测里所有
// fireEvent.change(getByLabelText(...)) 都要换成这个助手 —— 集中一处, 免得每个测试各写一遍。
import { fireEvent, screen, within } from '@testing-library/react'

/** 展开某个下拉(按 aria-label / label 定位), 返回它的 listbox。 */
export function openBlueprintSelect(label: string | RegExp): HTMLElement {
  fireEvent.click(screen.getByLabelText(label))
  return screen.getByRole('listbox', { name: label })
}

/** 展开下拉并按选项文字点选。text 是选项的显示 label, 不是 value。 */
export function selectBlueprintOption(label: string | RegExp, text: string | RegExp): void {
  const list = openBlueprintSelect(label)
  fireEvent.click(within(list).getByRole('option', { name: text }))
}
