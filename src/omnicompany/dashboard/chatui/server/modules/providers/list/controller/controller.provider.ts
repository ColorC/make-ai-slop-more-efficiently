import { ControllerProviderAuth } from '@/modules/providers/list/controller/controller-auth.provider.js';
import { ControllerProviderModels } from '@/modules/providers/list/controller/controller-models.provider.js';
import { ControllerMcpProvider } from '@/modules/providers/list/controller/controller-mcp.provider.js';
import { ControllerSessionSynchronizer } from '@/modules/providers/list/controller/controller-session-synchronizer.provider.js';
import { ControllerSessionsProvider } from '@/modules/providers/list/controller/controller-sessions.provider.js';
import { ControllerSkillsProvider } from '@/modules/providers/list/controller/controller-skills.provider.js';
import { AbstractProvider } from '@/modules/providers/shared/base/abstract.provider.js';
import type {
  IProviderAuth,
  IProviderModels,
  IProviderSessionSynchronizer,
  IProviderSkills,
  IProviderSessions,
} from '@/shared/interfaces.js';

/**
 * The controller (总控) provider. It reuses the local Claude Code runtime
 * (server/controller-cli.js delegates to claude-sdk.js) and only differs by
 * appending the 总控 system prompt and forcing model=opus. Sessions/auth reuse
 * Claude's implementations because controller sessions ARE Claude sessions.
 */
export class ControllerProvider extends AbstractProvider {
  readonly models: IProviderModels = new ControllerProviderModels();
  readonly mcp = new ControllerMcpProvider();
  readonly auth: IProviderAuth = new ControllerProviderAuth();
  readonly skills: IProviderSkills = new ControllerSkillsProvider();
  readonly sessions: IProviderSessions = new ControllerSessionsProvider();
  readonly sessionSynchronizer: IProviderSessionSynchronizer = new ControllerSessionSynchronizer();

  constructor() {
    super('controller');
  }
}
