export interface SiblingSessionCommandSource {
  provider?: string | null
  cmd?: string[]
}

/**
 * Starts a clean session of the same provider. Never reuse resume/session-id
 * arguments from the current PTY command.
 */
export function commandForSiblingSession(entity: SiblingSessionCommandSource): string[] | undefined {
  const initial = String(entity.cmd?.[0] || '').toLowerCase()
  const provider = entity.provider
    || (initial.includes('codex') ? 'codex'
      : initial.includes('codebuddy') || initial.includes('cbc') ? 'codebuddy'
        : initial.includes('kimi') ? 'kimi'
        : initial.includes('opencode') ? 'opencode'
          : initial.includes('powershell') || initial.includes('pwsh') ? 'shell'
            : 'claude_code')
  if (provider === 'codex') return ['codex']
  if (provider === 'codebuddy') return ['codebuddy']
  if (provider === 'kimi') return ['kimi']
  if (provider === 'opencode') return ['opencode']
  if (provider === 'shell' || provider === 'powershell') return ['powershell', '-NoLogo']
  return undefined
}
