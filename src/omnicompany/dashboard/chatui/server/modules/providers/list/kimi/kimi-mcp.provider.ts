import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { getKimiCodeHome } from '@/modules/providers/list/kimi/kimi-auth.provider.js';
import { McpProvider } from '@/modules/providers/shared/mcp/mcp.provider.js';
import type { McpScope, ProviderMcpServer, UpsertProviderMcpServerInput } from '@/shared/types.js';
import {
  AppError,
  readObjectRecord,
  readOptionalString,
  readStringArray,
  readStringRecord,
} from '@/shared/utils.js';

/**
 * Resolves the Kimi MCP config path for one scope. Kimi Code reads
 * `{ "mcpServers": { ... } }` from `~/.kimi-code/mcp.json` (user) and
 * `<workspace>/.kimi-code/mcp.json` (project).
 */
const resolveKimiMcpConfigPath = (scope: McpScope, workspacePath: string): string =>
  scope === 'user'
    ? path.join(getKimiCodeHome(), 'mcp.json')
    : path.join(workspacePath, '.kimi-code', 'mcp.json');

const readKimiMcpConfig = async (filePath: string): Promise<Record<string, unknown>> => {
  try {
    const content = await readFile(filePath, 'utf8');
    return readObjectRecord(JSON.parse(content)) ?? {};
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') {
      return {};
    }

    throw error;
  }
};

const writeKimiMcpConfig = async (filePath: string, data: Record<string, unknown>): Promise<void> => {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
};

export class KimiMcpProvider extends McpProvider {
  constructor() {
    super('kimi', ['user', 'project'], ['stdio', 'http']);
  }

  protected async readScopedServers(scope: McpScope, workspacePath: string): Promise<Record<string, unknown>> {
    const config = await readKimiMcpConfig(resolveKimiMcpConfigPath(scope, workspacePath));
    return readObjectRecord(config.mcpServers) ?? {};
  }

  protected async writeScopedServers(
    scope: McpScope,
    workspacePath: string,
    servers: Record<string, unknown>,
  ): Promise<void> {
    const filePath = resolveKimiMcpConfigPath(scope, workspacePath);
    const config = await readKimiMcpConfig(filePath);
    config.mcpServers = servers;
    await writeKimiMcpConfig(filePath, config);
  }

  protected buildServerConfig(input: UpsertProviderMcpServerInput): Record<string, unknown> {
    if (input.transport === 'stdio') {
      if (!input.command?.trim()) {
        throw new AppError('command is required for stdio MCP servers.', {
          code: 'MCP_COMMAND_REQUIRED',
          statusCode: 400,
        });
      }

      return {
        command: input.command,
        args: input.args ?? [],
        env: input.env ?? {},
        ...(input.cwd?.trim() ? { cwd: input.cwd } : {}),
      };
    }

    if (!input.url?.trim()) {
      throw new AppError('url is required for http MCP servers.', {
        code: 'MCP_URL_REQUIRED',
        statusCode: 400,
      });
    }

    return {
      url: input.url,
      headers: input.headers ?? {},
    };
  }

  protected normalizeServerConfig(
    scope: McpScope,
    name: string,
    rawConfig: unknown,
  ): ProviderMcpServer | null {
    const config = readObjectRecord(rawConfig);
    if (!config) {
      return null;
    }

    const command = readOptionalString(config.command);
    if (command) {
      return {
        provider: 'kimi',
        name,
        scope,
        transport: 'stdio',
        command,
        args: readStringArray(config.args) ?? [],
        env: readStringRecord(config.env),
        cwd: readOptionalString(config.cwd),
      };
    }

    const url = readOptionalString(config.url);
    if (url) {
      return {
        provider: 'kimi',
        name,
        scope,
        transport: 'http',
        url,
        headers: readStringRecord(config.headers),
      };
    }

    return null;
  }
}
