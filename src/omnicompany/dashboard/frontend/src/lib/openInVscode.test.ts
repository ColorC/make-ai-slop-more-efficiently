import { describe, expect, it } from 'vitest'
import { vscodeFileUrl } from './openInVscode'

describe('vscodeFileUrl', () => {
  it('Windows 反斜杠路径归一并加前导斜杠', () => {
    expect(vscodeFileUrl('C:/workspace/omnicompany\\PROJECT_INDEX.md'))
      .toBe('vscode://file/C:/workspace/omnicompany/PROJECT_INDEX.md')
  })

  it('空格与中文做 URL 编码, 冒号斜杠保留', () => {
    expect(vscodeFileUrl('d:\\P4\\my dir\\故事.md'))
      .toBe('vscode://file/d:/P4/my%20dir/%E6%95%85%E4%BA%8B.md')
  })

  it('带行号/行列', () => {
    expect(vscodeFileUrl('e:/a/b.ts', 42)).toBe('vscode://file/e:/a/b.ts:42')
    expect(vscodeFileUrl('e:/a/b.ts', 42, 7)).toBe('vscode://file/e:/a/b.ts:42:7')
  })

  it('已是正斜杠 + 前导斜杠的 POSIX 形不重复加斜杠', () => {
    expect(vscodeFileUrl('/home/user/x.md')).toBe('vscode://file/home/user/x.md')
  })
})
