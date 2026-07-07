import type { IProviderSkills } from '@/shared/interfaces.js';
import type { ProviderSkill } from '@/shared/types.js';

/**
 * The controller (总控) does not advertise its own slash-command/skill surface
 * in chatui (its skills live in the omnicompany runtime, invoked via the `omni`
 * CLI through Bash), so it reports an empty skill list.
 */
export class ControllerSkillsProvider implements IProviderSkills {
  async listSkills(): Promise<ProviderSkill[]> {
    return [];
  }
}
