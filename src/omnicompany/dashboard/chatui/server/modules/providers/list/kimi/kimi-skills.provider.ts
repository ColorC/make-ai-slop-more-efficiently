import os from 'node:os';
import path from 'node:path';

import { getKimiCodeHome } from '@/modules/providers/list/kimi/kimi-auth.provider.js';
import { SkillsProvider } from '@/modules/providers/shared/skills/skills.provider.js';
import type { ProviderSkillSource } from '@/shared/types.js';

export class KimiSkillsProvider extends SkillsProvider {
  constructor() {
    super('kimi');
  }

  protected async getSkillSources(workspacePath: string): Promise<ProviderSkillSource[]> {
    // Kimi Code auto-discovers skills from its own home/project folders and
    // also reads the cross-agent `.agents/skills` locations by default.
    return [
      {
        scope: 'user',
        rootDir: path.join(getKimiCodeHome(), 'skills'),
        commandPrefix: '/',
      },
      {
        scope: 'user',
        rootDir: path.join(os.homedir(), '.agents', 'skills'),
        commandPrefix: '/',
      },
      {
        scope: 'project',
        rootDir: path.join(workspacePath, '.kimi-code', 'skills'),
        commandPrefix: '/',
      },
      {
        scope: 'project',
        rootDir: path.join(workspacePath, '.agents', 'skills'),
        commandPrefix: '/',
      },
    ];
  }
}
