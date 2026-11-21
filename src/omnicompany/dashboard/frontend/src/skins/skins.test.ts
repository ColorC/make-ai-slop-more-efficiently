import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { isSafeTokenValue, parseSkinArtifact } from './skinArtifact'
import { SKIN_PARTS, SKIN_SLOT_NAMES, compileSkin } from './skinParts'
import { DEFAULT_SKIN_ID, SKINS, SKIN_KITS, resolveSkin } from './skinRegistry'
import { applySkin, skinCss } from './skinRuntime'

const SRC = resolve(__dirname, '..')
const frostpane = readFileSync(join(SRC, 'styles/frostpane.css'), 'utf8')
const indexCss = readFileSync(join(SRC, 'index.css'), 'utf8')

describe('皮肤文件', () => {
  it('拒绝能跳出声明或拉外部资源的值', () => {
    expect(isSafeTokenValue('#fff')).toBe(true)
    expect(isSafeTokenValue('oklch(85% 0.138 181.071)')).toBe(true)
    for (const bad of ['red; background: url(x)', 'url(http://x/a.png)', '#fff} body{display:none', '@import "x"', '']) {
      expect(isSafeTokenValue(bad)).toBe(false)
    }
  })

  it('格式不对的文件整份不收', () => {
    expect(parseSkinArtifact({ schemaVersion: 3, base: 'dark', tokens: { '--background': '#000' } })).not.toBeNull()
    expect(parseSkinArtifact({ schemaVersion: 3, base: 'sepia', tokens: {} })).toBeNull()
    expect(parseSkinArtifact({ schemaVersion: 3, base: 'dark', tokens: { 'background': '#000' } })).toBeNull()
    expect(parseSkinArtifact({ schemaVersion: 3, base: 'dark', tokens: { '--background': 'url(x)' } })).toBeNull()
  })
})

describe('部位表', () => {
  it('每个 CSS 变量只归一个槽位管', () => {
    const owner = new Map<string, string>()
    for (const part of SKIN_PARTS) {
      for (const slot of part.slots) {
        for (const name of slot.vars) {
          const full = `${part.id}.${slot.slot}`
          expect(owner.get(name), `${name} 同时归 ${owner.get(name)} 和 ${full}`).toBeUndefined()
          owner.set(name, full)
        }
      }
    }
  })

  it('槽位驱动的变量都是宿主真实定义过的变量', () => {
    for (const part of SKIN_PARTS) {
      for (const slot of part.slots) {
        for (const name of slot.vars) {
          if (name.startsWith('--omni-skin-')) continue
          const defined = frostpane.includes(`${name}:`) || indexCss.includes(`${name}:`)
          expect(defined, `${part.id}.${slot.slot} 驱动的 ${name} 在 frostpane.css / index.css 里没有定义`).toBe(true)
        }
      }
    }
  })

  it('覆盖值按槽位全名生效', () => {
    const artifact = parseSkinArtifact({ schemaVersion: 3, base: 'dark', tokens: { '--background': '#101010' } })!
    expect(compileSkin(artifact)['--fp-bg']).toBe('#101010')
    expect(compileSkin(artifact, { 'scene.color': '#202020' })['--fp-bg']).toBe('#202020')
    expect(SKIN_SLOT_NAMES).toContain('scene.color')
  })
})

describe('皮肤名录', () => {
  it('原生皮肤加三套套件皮肤并排', () => {
    expect(new Set(SKINS.map((skin) => skin.id)).size).toBe(SKINS.length)
    const kits = new Set(SKINS.map((skin) => skin.kit))
    expect([...kits].sort()).toEqual(['daisyui', 'magicui', 'native', 'neobrutalism'])
    for (const skin of SKINS) expect(SKIN_KITS.some((kit) => kit.id === skin.kit)).toBe(true)
  })

  it('原生皮肤不覆盖任何变量, 小样用色与 frostpane.css 一致', () => {
    const blueprint = resolveSkin(DEFAULT_SKIN_ID)
    expect(blueprint.variables).toEqual({})
    for (const value of [blueprint.preview.scene, blueprint.preview.paper, blueprint.preview.primary, blueprint.preview.focus, blueprint.preview.ink]) {
      expect(frostpane, `frostpane.css 里找不到 ${value}`).toContain(value)
    }
  })

  it('套件皮肤把主要部位都换掉', () => {
    for (const skin of SKINS.filter((item) => item.kit !== 'native')) {
      for (const name of ['--fp-bg', '--fp-bp-paper', '--fp-border', '--fp-text', '--fp-bp-seal', '--fp-bp-seal-fg', '--fp-r1', '--fp-shadow-sm', '--background', '--primary']) {
        expect(skin.variables[name], `${skin.id} 缺 ${name}`).toBeTruthy()
      }
    }
  })

  it('成对皮肤互相指向, 且明暗相反', () => {
    for (const skin of SKINS.filter((item) => item.pair)) {
      const pair = resolveSkin(skin.pair)
      expect(pair.pair).toBe(skin.id)
      expect(pair.base).not.toBe(skin.base)
    }
  })

  it('未知皮肤回到默认', () => {
    expect(resolveSkin('no-such-skin').id).toBe(DEFAULT_SKIN_ID)
    expect(resolveSkin(null).id).toBe(DEFAULT_SKIN_ID)
  })
})

describe('皮肤运行时', () => {
  it('把皮肤 id 与明暗落到 <html>', () => {
    const light = SKINS.find((skin) => skin.base === 'light')!
    expect(applySkin(light.id).id).toBe(light.id)
    expect(document.documentElement.dataset.omniSkin).toBe(light.id)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    applySkin('no-such-skin')
    expect(document.documentElement.dataset.omniSkin).toBe(DEFAULT_SKIN_ID)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('变量块带皮肤选择器与明暗声明(index.html 引导脚本靠这两点识别)', () => {
    const skin = SKINS.find((item) => item.base === 'light')!
    const css = skinCss(skin)
    expect(css).toContain(`[data-omni-skin="${skin.id}"]`)
    expect(css).toContain('color-scheme:light')
  })
})

// ── 写死颜色的棘轮 ─────────────────────────────────────────────────────────
// 组件里写死的颜色不跟皮肤走。这里数全前端(不含皮肤文件、测试、token 定义文件)的颜色字面量,
// 只许减不许增: 新代码引用 var(--fp-*) 或 tokens.ts; 清掉一批旧字面量后把上限调低。
const HARDCODED_COLOR_CEILING = 615
const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      return name === 'skins' || name === '_legacy' || name === 'fonts' ? [] : sourceFiles(path)
    }
    if (!/\.(css|tsx|ts|jsx)$/.test(name) || /\.test\./.test(name)) return []
    return name === 'frostpane.css' ? [] : [path]
  })
}

describe('写死颜色的棘轮', () => {
  it('颜色字面量只减不增', () => {
    const count = sourceFiles(SRC)
      .reduce((total, path) => total + (readFileSync(path, 'utf8').match(COLOR_LITERAL)?.length ?? 0), 0)
    expect(count, `前端颜色字面量 ${count} 处, 超过上限 ${HARDCODED_COLOR_CEILING}: 新增的颜色请改用 var(--fp-*)`).toBeLessThanOrEqual(HARDCODED_COLOR_CEILING)
  })
})
