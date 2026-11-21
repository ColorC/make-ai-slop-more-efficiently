import type { LLMProvider } from '../../types/app';

export type ProviderAuthStatus = {
  authenticated: boolean;
  email: string | null;
  method: string | null;
  error: string | null;
  loading: boolean;
};

export type ProviderAuthStatusMap = Record<LLMProvider, ProviderAuthStatus>;

export const CLI_PROVIDERS: LLMProvider[] = ['claude', 'cursor', 'codex', 'gemini', 'kimi', 'opencode', 'omni_agent', 'controller'];

export const PROVIDER_AUTH_STATUS_ENDPOINTS: Record<LLMProvider, string> = {
  claude: '/api/providers/claude/auth/status',
  cursor: '/api/providers/cursor/auth/status',
  codex: '/api/providers/codex/auth/status',
  gemini: '/api/providers/gemini/auth/status',
  kimi: '/api/providers/kimi/auth/status',
  opencode: '/api/providers/opencode/auth/status',
  omni_agent: '/api/providers/omni_agent/auth/status',
  controller: '/api/providers/controller/auth/status',
};

export const createInitialProviderAuthStatusMap = (loading = true): ProviderAuthStatusMap => ({
  claude: { authenticated: false, email: null, method: null, error: null, loading },
  cursor: { authenticated: false, email: null, method: null, error: null, loading },
  codex: { authenticated: false, email: null, method: null, error: null, loading },
  gemini: { authenticated: false, email: null, method: null, error: null, loading },
  kimi: { authenticated: false, email: null, method: null, error: null, loading },
  opencode: { authenticated: false, email: null, method: null, error: null, loading },
  omni_agent: { authenticated: false, email: null, method: null, error: null, loading },
  controller: { authenticated: false, email: null, method: null, error: null, loading },
});
