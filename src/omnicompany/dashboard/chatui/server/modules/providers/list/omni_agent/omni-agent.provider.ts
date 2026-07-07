import { OmniAgentProviderAuth } from '@/modules/providers/list/omni_agent/omni-agent-auth.provider.js';
import { OmniAgentProviderModels } from '@/modules/providers/list/omni_agent/omni-agent-models.provider.js';
import { OmniAgentMcpProvider } from '@/modules/providers/list/omni_agent/omni-agent-mcp.provider.js';
import { OmniAgentSessionSynchronizer } from '@/modules/providers/list/omni_agent/omni-agent-session-synchronizer.provider.js';
import { OmniAgentSessionsProvider } from '@/modules/providers/list/omni_agent/omni-agent-sessions.provider.js';
import { OmniAgentSkillsProvider } from '@/modules/providers/list/omni_agent/omni-agent-skills.provider.js';
import { AbstractProvider } from '@/modules/providers/shared/base/abstract.provider.js';
import type {
  IProviderAuth,
  IProviderModels,
  IProviderSessionSynchronizer,
  IProviderSkills,
  IProviderSessions,
} from '@/shared/interfaces.js';

export class OmniAgentProvider extends AbstractProvider {
  readonly models: IProviderModels = new OmniAgentProviderModels();
  readonly mcp = new OmniAgentMcpProvider();
  readonly auth: IProviderAuth = new OmniAgentProviderAuth();
  readonly skills: IProviderSkills = new OmniAgentSkillsProvider();
  readonly sessions: IProviderSessions = new OmniAgentSessionsProvider();
  readonly sessionSynchronizer: IProviderSessionSynchronizer = new OmniAgentSessionSynchronizer();

  constructor() {
    super('omni_agent');
  }
}
