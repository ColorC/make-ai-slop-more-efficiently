import type { IProviderSkills } from '@/shared/interfaces.js';
import type { ProviderSkill } from '@/shared/types.js';

/**
 * omni_agent does not expose provider-native skill markdown locations, so it
 * reports an empty skill list.
 */
export class OmniAgentSkillsProvider implements IProviderSkills {
  async listSkills(): Promise<ProviderSkill[]> {
    return [];
  }
}
