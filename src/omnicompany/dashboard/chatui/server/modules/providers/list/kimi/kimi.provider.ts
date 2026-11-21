import { KimiProviderAuth } from '@/modules/providers/list/kimi/kimi-auth.provider.js';
import { KimiProviderModels } from '@/modules/providers/list/kimi/kimi-models.provider.js';
import { KimiMcpProvider } from '@/modules/providers/list/kimi/kimi-mcp.provider.js';
import { KimiSessionSynchronizer } from '@/modules/providers/list/kimi/kimi-session-synchronizer.provider.js';
import { KimiSessionsProvider } from '@/modules/providers/list/kimi/kimi-sessions.provider.js';
import { KimiSkillsProvider } from '@/modules/providers/list/kimi/kimi-skills.provider.js';
import { AbstractProvider } from '@/modules/providers/shared/base/abstract.provider.js';
import type {
  IProviderAuth,
  IProviderModels,
  IProviderSessionSynchronizer,
  IProviderSkills,
  IProviderSessions,
} from '@/shared/interfaces.js';

export class KimiProvider extends AbstractProvider {
  readonly models: IProviderModels = new KimiProviderModels();
  readonly mcp = new KimiMcpProvider();
  readonly auth: IProviderAuth = new KimiProviderAuth();
  readonly skills: IProviderSkills = new KimiSkillsProvider();
  readonly sessions: IProviderSessions = new KimiSessionsProvider();
  readonly sessionSynchronizer: IProviderSessionSynchronizer = new KimiSessionSynchronizer();

  constructor() {
    super('kimi');
  }
}
