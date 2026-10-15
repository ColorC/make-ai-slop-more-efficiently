import React from 'react'
import { Bot, TerminalSquare } from 'lucide-react'

const ICON_ROOT = '/assets/cli'

const PROVIDER_ICONS: Record<string, string> = {
  claude: `${ICON_ROOT}/claudecode-color.svg`,
  claude_code: `${ICON_ROOT}/claudecode-color.svg`,
  // 第二个官方账号仍然是 Claude Code, 同一个标 —— 区分靠显示名。
  claude_2: `${ICON_ROOT}/claudecode-color.svg`,
  codex: `${ICON_ROOT}/codex-color.svg`,
  // 第二个官方账号仍然是 Codex, 挂同一个标 —— 区分靠显示名, 不靠换图标。
  codex_2: `${ICON_ROOT}/codex-color.svg`,
  codex_the_company: `${ICON_ROOT}/the_company-color.png`,
  the_company: `${ICON_ROOT}/the_company-color.png`,
  'the_company-api': `${ICON_ROOT}/the_company-color.png`,
  // GLM Coding Plan 跑的是 Codex CLI, 但用户认的是 GLM/智谱, 所以挂智谱标。
  codex_glm: `${ICON_ROOT}/zhipu-color.png`,
  'glm-coding-plan': `${ICON_ROOT}/zhipu-color.png`,
  gemini: `${ICON_ROOT}/gemini-color.svg`,
  'gemini-cli': `${ICON_ROOT}/gemini-color.svg`,
  antigravity: `${ICON_ROOT}/antigravity-color.svg`,
  'antigravity-cli': `${ICON_ROOT}/antigravity-color.svg`,
  codebuddy: `${ICON_ROOT}/codebuddy-color.svg`,
  kimi: `${ICON_ROOT}/kimi-color.svg`,
  opencode: `${ICON_ROOT}/opencode.svg`,
  kiro: `${ICON_ROOT}/kiro-color.svg`,
  qoder: `${ICON_ROOT}/qoder-color.svg`,
  dsh: `${ICON_ROOT}/deepseek-color.svg`,
  dsh_web: `${ICON_ROOT}/deepseek-color.svg`,
  // zcode 不再可启动 (2026-08-17); 映射保留仅供既有会话/历史渲染。
  zcode: `${ICON_ROOT}/zhipu-color.png`,
  grok: `${ICON_ROOT}/grok-color.png`,
}

export function providerIconSource(provider?: string | null): string | null {
  return PROVIDER_ICONS[String(provider || '').trim().toLowerCase()] || null
}

export function ProviderIcon({
  provider,
  label,
  size = 24,
}: {
  provider?: string | null
  label: string
  size?: number
}) {
  const source = providerIconSource(provider)
  const normalized = String(provider || '').toLowerCase()
  const fallback = normalized === 'powershell' || normalized === 'shell'
    ? <TerminalSquare size={Math.round(size * .72)} />
    : <Bot size={Math.round(size * .72)} />

  return (
    <span className="ma-cli-mark" role="img" aria-label={label} title={label}>
      {source ? <img src={source} alt="" width={size} height={size} draggable={false} /> : fallback}
    </span>
  )
}
