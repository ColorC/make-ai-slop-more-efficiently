import { readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import spawn from 'cross-spawn';

import type { IProviderAuth } from '@/shared/interfaces.js';
import type { ProviderAuthStatus } from '@/shared/types.js';
import { readObjectRecord, readOptionalString } from '@/shared/utils.js';

type KimiCredentialsStatus = {
  authenticated: boolean;
  email: string | null;
  method: string | null;
  error?: string;
};

/**
 * Resolves the Kimi Code home directory (`$KIMI_CODE_HOME` or `~/.kimi-code`).
 */
export const getKimiCodeHome = (): string =>
  process.env.KIMI_CODE_HOME?.trim() || path.join(os.homedir(), '.kimi-code');

export class KimiProviderAuth implements IProviderAuth {
  /**
   * Checks whether the Kimi Code CLI is available to the server process.
   */
  private checkInstalled(): boolean {
    try {
      const result = spawn.sync('kimi', ['--version'], { stdio: 'ignore', timeout: 5000 });
      return !result.error && result.status === 0;
    } catch {
      return false;
    }
  }

  /**
   * Returns Kimi Code CLI installation and credential status.
   */
  async getStatus(): Promise<ProviderAuthStatus> {
    const installed = this.checkInstalled();
    const credentials = await this.checkCredentials();

    return {
      installed,
      provider: 'kimi',
      authenticated: credentials.authenticated,
      email: credentials.email,
      method: credentials.method,
      error: credentials.authenticated ? undefined : credentials.error || 'Not authenticated',
    };
  }

  /**
   * Reads the OAuth device-flow token store written by `kimi login`
   * (`~/.kimi-code/credentials/kimi-code.json`).
   */
  private async checkCredentials(): Promise<KimiCredentialsStatus> {
    try {
      const credentialsPath = path.join(getKimiCodeHome(), 'credentials', 'kimi-code.json');
      const content = await readFile(credentialsPath, 'utf8');
      const credentials = readObjectRecord(JSON.parse(content));

      if (readOptionalString(credentials?.access_token)) {
        return {
          authenticated: true,
          email: 'Kimi Code credentials',
          method: 'credentials_file',
        };
      }

      return {
        authenticated: false,
        email: null,
        method: null,
        error: 'Kimi credentials file has no access token',
      };
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT') {
        return {
          authenticated: false,
          email: null,
          method: null,
          error: error instanceof Error ? error.message : 'Failed to read Kimi credentials',
        };
      }
    }

    return {
      authenticated: false,
      email: null,
      method: null,
      error: 'Kimi is not logged in. Run `kimi login` to authenticate.',
    };
  }
}
