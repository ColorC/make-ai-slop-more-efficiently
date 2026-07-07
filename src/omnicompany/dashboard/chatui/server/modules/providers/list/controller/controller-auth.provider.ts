import { ClaudeProviderAuth } from '@/modules/providers/list/claude/claude-auth.provider.js';
import type { IProviderAuth } from '@/shared/interfaces.js';
import type { ProviderAuthStatus } from '@/shared/types.js';

/**
 * The controller (总控) is the same local Claude Code runtime as the `claude`
 * provider — same binary, same login, same credentials. So its auth status is
 * exactly Claude's, only re-tagged with the `controller` provider id. We reuse
 * ClaudeProviderAuth instead of duplicating the credential-resolution logic.
 */
export class ControllerProviderAuth implements IProviderAuth {
  private readonly claudeAuth = new ClaudeProviderAuth();

  async getStatus(): Promise<ProviderAuthStatus> {
    const status = await this.claudeAuth.getStatus();
    return {
      ...status,
      provider: 'controller',
    };
  }
}
