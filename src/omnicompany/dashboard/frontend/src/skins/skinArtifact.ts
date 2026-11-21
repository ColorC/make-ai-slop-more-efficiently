// 皮肤文件格式 —— 与 WorkHost 外观插件的应用主题文件同一格式
// (WorkHost 仓 docs/reference/plugin-appearance-protocol.md)。
// 两个产品共用一种文件, WorkHost 的主题 JSON 原样放进 packs/ 就是驾驶舱的皮肤来源。
// 皮肤文件只能给公开 token 赋值, 不能带选择器、脚本或外链。

export type SkinBase = 'dark' | 'light'

export interface SkinArtifact {
  schemaVersion: number
  base: SkinBase
  tokens: Record<string, string>
}

/** 驾驶舱读取的 WorkHost token。其余 token(侧栏分区、控件形变、果冻动效等)
 *  驾驶舱暂无对应部位, 读入时保留但不消费。 */
export const CONSUMED_ARTIFACT_TOKENS = [
  '--background', '--foreground',
  '--card', '--card-foreground',
  '--popover', '--popover-foreground',
  '--primary', '--primary-foreground',
  '--secondary', '--secondary-foreground',
  '--muted', '--muted-foreground',
  '--accent', '--accent-foreground',
  '--destructive', '--destructive-foreground',
  '--border', '--input', '--ring',
  '--editor-surface',
  '--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5',
  '--appearance-state-hover', '--appearance-state-hover-foreground',
  '--appearance-state-selected', '--appearance-state-selected-foreground',
  '--appearance-state-backdrop-filter',
  '--appearance-canvas-background-image',
  '--radius',
  '--appearance-control-radius', '--appearance-panel-radius',
  '--appearance-overlay-radius', '--appearance-pill-radius',
  '--appearance-border-width',
  '--appearance-shadow-control', '--appearance-shadow-subtle', '--appearance-shadow-floating',
  '--motion-fast', '--motion-base', '--motion-enter', '--motion-ease-out',
] as const

export type ConsumedArtifactToken = (typeof CONSUMED_ARTIFACT_TOKENS)[number]

const TOKEN_NAME = /^--[a-z0-9-]+$/
const MAX_VALUE_LENGTH = 800
// 值里出现这些片段就能跳出声明或拉外部资源, 一律拒绝。
const FORBIDDEN_VALUE = /[;{}<>\\]|url\s*\(|@import|expression\s*\(|javascript:/i

export function isSafeTokenValue(value: unknown): value is string {
  return typeof value === 'string'
    && value.trim().length > 0
    && value.length <= MAX_VALUE_LENGTH
    && !FORBIDDEN_VALUE.test(value)
}

/** 读入一份皮肤文件。格式不对返回 null, 调用方回退到默认皮肤。 */
export function parseSkinArtifact(raw: unknown): SkinArtifact | null {
  if (!raw || typeof raw !== 'object') return null
  const candidate = raw as Partial<SkinArtifact>
  if (candidate.base !== 'dark' && candidate.base !== 'light') return null
  if (typeof candidate.schemaVersion !== 'number' || candidate.schemaVersion < 1) return null
  if (!candidate.tokens || typeof candidate.tokens !== 'object') return null
  const tokens: Record<string, string> = {}
  for (const [name, value] of Object.entries(candidate.tokens)) {
    if (!TOKEN_NAME.test(name) || !isSafeTokenValue(value)) return null
    tokens[name] = value.trim()
  }
  return { schemaVersion: candidate.schemaVersion, base: candidate.base, tokens }
}
