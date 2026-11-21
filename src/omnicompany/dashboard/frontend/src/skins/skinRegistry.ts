// 皮肤名录 —— 设置页「皮肤」列出的就是这里的条目。
// 加一套皮肤: 把皮肤文件放进 packs/, 在这里登记一行; 个别槽位要手调时写 overrides(按槽位全名)。
import { parseSkinArtifact, type SkinArtifact, type SkinBase } from './skinArtifact'
import { compileSkin } from './skinParts'
import auroraGlass from './packs/aurora-glass.json'
import cupcakeCartoon from './packs/cupcake-cartoon.json'
import neoDark from './packs/neo-dark.json'
import neoLight from './packs/neo-light.json'
import synthwaveFuture from './packs/synthwave-future.json'

export interface SkinKit {
  id: string
  label: string
  /** 皮肤改编自哪个开源套件; 原生皮肤没有。许可见 packs/THIRD_PARTY_NOTICES.md。 */
  sourceUrl?: string
}

export const SKIN_KITS: readonly SkinKit[] = [
  { id: 'native', label: '驾驶舱原生' },
  { id: 'neobrutalism', label: 'NeoBrutalism', sourceUrl: 'https://github.com/neobrutalism/neobrutalism' },
  { id: 'daisyui', label: 'daisyUI', sourceUrl: 'https://github.com/saadeghi/daisyui' },
  { id: 'magicui', label: 'Magic UI', sourceUrl: 'https://github.com/magicuidesign/magicui' },
]

/** 设置页皮肤卡上的小样用色。 */
export interface SkinPreview {
  scene: string
  pattern: string
  paper: string
  line: string
  ink: string
  mutedInk: string
  primary: string
  primaryInk: string
  focus: string
  radius: string
  shadow: string
  lineWidth: string
}

export interface SkinDefinition {
  id: string
  label: string
  kit: string
  base: SkinBase
  /** 同一套件里明暗成对的另一套皮肤(深浅切换用)。 */
  pair?: string
  /** 翻译好的 CSS 变量; 原生皮肤为空表(变量就是 frostpane.css 的默认值)。 */
  variables: Readonly<Record<string, string>>
  preview: SkinPreview
}

export const DEFAULT_SKIN_ID = 'blueprint'

// 原生皮肤的小样用色抄自 styles/frostpane.css 的蓝图值; skinRegistry.test.ts 核对两边一致。
const BLUEPRINT: SkinDefinition = {
  id: DEFAULT_SKIN_ID,
  label: '蓝图精制',
  kit: 'native',
  base: 'dark',
  variables: {},
  preview: {
    scene: '#091a3e',
    pattern: 'repeating-linear-gradient(0deg, rgba(190,220,255,.08) 0 1px, transparent 1px 13px), repeating-linear-gradient(90deg, rgba(190,220,255,.08) 0 1px, transparent 1px 13px)',
    paper: '#133163',
    line: 'rgba(235,245,255,.5)',
    ink: '#eef4ff',
    mutedInk: '#a3bce0',
    primary: '#d64541',
    primaryInk: '#ffffff',
    focus: '#b08d3e',
    radius: '2px',
    shadow: '2px 2px 0 rgba(6,16,38,.5)',
    lineWidth: '2px',
  },
}

interface PackEntry {
  id: string
  label: string
  kit: string
  pair?: string
  file: unknown
  overrides?: Readonly<Record<string, string>>
}

const PACKS: readonly PackEntry[] = [
  { id: 'neo-light', label: 'Neo Brutalism 亮', kit: 'neobrutalism', pair: 'neo-dark', file: neoLight },
  { id: 'neo-dark', label: 'Neo Brutalism 暗', kit: 'neobrutalism', pair: 'neo-light', file: neoDark },
  {
    id: 'cupcake-cartoon', label: 'Cupcake 卡通', kit: 'daisyui', file: cupcakeCartoon,
    // 套件的第二图表色是浅粉, 当信息色文字读不清, 换成它的蓝。
    overrides: { 'emphasis.info': 'oklch(68% 0.169 237.323)' },
  },
  { id: 'synthwave-future', label: 'Synthwave 未来', kit: 'daisyui', file: synthwaveFuture },
  {
    id: 'aurora-glass', label: 'Aurora 玻璃', kit: 'magicui', file: auroraGlass,
    overrides: {
      'glass.panelBlur': 'blur(14px) saturate(140%)',
      'glass.floatingBlur': 'blur(18px) saturate(150%)',
      'emphasis.info': '#5CE1E6',
    },
  },
]

function previewOf(artifact: SkinArtifact): SkinPreview {
  const token = (name: string, fallback: string) => artifact.tokens[name] ?? fallback
  return {
    scene: token('--background', BLUEPRINT.preview.scene),
    pattern: token('--appearance-canvas-background-image', 'none'),
    paper: token('--card', BLUEPRINT.preview.paper),
    line: token('--border', BLUEPRINT.preview.line),
    ink: token('--foreground', BLUEPRINT.preview.ink),
    mutedInk: token('--muted-foreground', BLUEPRINT.preview.mutedInk),
    primary: token('--primary', BLUEPRINT.preview.primary),
    primaryInk: token('--primary-foreground', BLUEPRINT.preview.primaryInk),
    focus: token('--ring', BLUEPRINT.preview.focus),
    radius: token('--appearance-control-radius', '2px'),
    shadow: token('--appearance-shadow-control', 'none'),
    lineWidth: token('--appearance-border-width', '1px'),
  }
}

function fromPack(entry: PackEntry): SkinDefinition | null {
  const artifact = parseSkinArtifact(entry.file)
  if (!artifact) return null
  return {
    id: entry.id,
    label: entry.label,
    kit: entry.kit,
    base: artifact.base,
    pair: entry.pair,
    variables: compileSkin(artifact, entry.overrides),
    preview: previewOf(artifact),
  }
}

export const SKINS: readonly SkinDefinition[] = [
  BLUEPRINT,
  ...PACKS.map(fromPack).filter((skin): skin is SkinDefinition => skin !== null),
]

const SKIN_BY_ID = new Map(SKINS.map((skin) => [skin.id, skin]))

/** 未知或已下架的皮肤 id 一律回到默认皮肤。 */
export function resolveSkin(id: string | null | undefined): SkinDefinition {
  return (id && SKIN_BY_ID.get(id)) || BLUEPRINT
}
