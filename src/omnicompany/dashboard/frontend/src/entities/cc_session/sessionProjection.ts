export interface SessionTitleInput {
  provider?: string | null
  sessionId: string
  topic?: string | null
  summaryTitle?: string | null
  nativeTitle?: string | null
  firstMessage?: string | null
  preview?: string | null
}

const INSUFFICIENT_TITLE = /^(?:\u4fe1\u606f\u4e0d\u8db3|\u8d44\u6599\u4e0d\u8db3|\u8bc1\u636e\u4e0d\u8db3|\u6682\u65e0\u53ef\u7528\u4fe1\u606f|\u6682\u65e0\u4fe1\u606f|\u65e0\u53ef\u7528\u4fe1\u606f|\u8fdb\u884c\u4e2d|\(\u65e0\)|Agent Work in Progress)(?:[\uFF1A:,.\u3002;].*)?$/i

export function normalizeSessionTitle(value: string | null | undefined): string {
  if (!value) return ''
  return String(value)
    .split(/\r?\n/)
    .map((part) => part.trim())
    .find(Boolean)
    ?.replace(/\s+/g, ' ') || ''
}

function usefulTitle(value: string | null | undefined): string {
  const normalized = normalizeSessionTitle(value)
  return normalized && !INSUFFICIENT_TITLE.test(normalized) ? normalized : ''
}

function providerName(provider?: string | null): string {
  const names: Record<string, string> = {
    claude: 'Claude', claude_code: 'Claude', codex: 'Codex', codex_the_company: 'the_company API',
    codebuddy: 'CodeBuddy', kimi: 'Kimi', opencode: 'OpenCode', kiro: 'Kiro', qoder: 'Qoder',
    dsh: 'DeepSeek Harness', dsh_web: 'DeepSeek Harness', zcode: 'ZCode', grok: 'Grok Build',
    shell: 'Terminal', powershell: 'PowerShell',
  }
  return names[String(provider || '')] || String(provider || 'Agent')
}

/** One title projection for tabs, monitor rows, and Multiagent View. */
export function sessionProjectionTitle(input: SessionTitleInput): string {
  return usefulTitle(input.topic)
    || usefulTitle(input.summaryTitle)
    || usefulTitle(input.nativeTitle)
    || usefulTitle(input.firstMessage)
    || usefulTitle(input.preview)
    || `${providerName(input.provider)} \u00b7 ${input.sessionId.slice(0, 8)}`
}
