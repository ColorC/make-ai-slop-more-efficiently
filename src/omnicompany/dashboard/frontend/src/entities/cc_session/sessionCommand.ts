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
  const full = String(entity.cmd?.join(' ') || '').toLowerCase()
  const provider = entity.provider
    || (initial.includes('kiro-cli') ? 'kiro'
      : initial.includes('zcode') ? 'zcode'
        : initial.includes('grok') ? 'grok'
          : initial.includes('codex') ? 'codex'
          : initial.includes('codebuddy') || initial.includes('cbc') ? 'codebuddy'
            : initial.includes('kimi') ? 'kimi'
              : initial.includes('opencode') ? 'opencode'
                : initial.includes('qodercli') ? 'qoder'
                  : full.includes('dsh-web.py') ? 'dsh_web'
                    : full.includes('omni-dsh-pty') ? 'dsh'
                      : initial.includes('powershell') || initial.includes('pwsh') ? 'shell'
                        : 'claude_code')
  if (provider === 'codex') return ['codex']
  if (provider === 'zcode') return ['zcode']
  if (provider === 'grok') return ['grok']
  if (provider === 'codebuddy') return ['codebuddy']
  if (provider === 'kimi') return ['kimi']
  if (provider === 'opencode') return ['opencode']
  if (provider === 'kiro') return ['kiro-cli', 'chat']
  if (provider === 'qoder') return ['qodercli']
  if (provider === 'shell' || provider === 'powershell') return ['pwsh', '-NoLogo']
  if (provider === 'dsh' || provider === 'dsh_web') return undefined
  return undefined
}
