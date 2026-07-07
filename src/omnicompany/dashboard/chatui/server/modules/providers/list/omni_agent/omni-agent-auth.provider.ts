import type { IProviderAuth } from '@/shared/interfaces.js';
import type { ProviderAuthStatus } from '@/shared/types.js';

/**
 * omni_agent runs as a local Python subprocess (the omnicompany shim). Its
 * credential (THE_COMPANY_API_KEY) is resolved by the shim from omnicompany's own
 * environment (project .env / global ~/.env) at spawn time — NOT from this Node
 * server's process.env. So CCUI cannot (and must not) gate omni_agent on its own
 * env: doing so hides a provider that actually works (proven end-to-end). Auth is
 * therefore delegated to the runtime; if the key is genuinely missing the shim
 * surfaces a real error in the chat stream. CCUI honors an explicit
 * `process.env.THE_COMPANY_API_KEY` if present (e.g. for status display), but defaults
 * to authenticated so the provider is selectable.
 */
export class OmniAgentProviderAuth implements IProviderAuth {
  async getStatus(): Promise<ProviderAuthStatus> {
    const apiKeyInServerEnv = Boolean(process.env.THE_COMPANY_API_KEY?.trim());

    return {
      installed: true,
      provider: 'omni_agent',
      authenticated: true,
      email: 'omnicompany runtime',
      method: apiKeyInServerEnv ? 'environment' : 'omnicompany-runtime',
    };
  }
}
