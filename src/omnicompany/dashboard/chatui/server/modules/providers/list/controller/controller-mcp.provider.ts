import type { IProviderMcp } from '@/shared/interfaces.js';
import type {
  LLMProvider,
  McpScope,
  ProviderMcpServer,
  UpsertProviderMcpServerInput,
} from '@/shared/types.js';
import { AppError } from '@/shared/utils.js';

/**
 * The controller (总控) does not expose its own MCP configuration surface — its
 * tools come from the claude_code preset reused by server/controller-cli.js.
 * Listing returns empty results for every scope and mutations are rejected.
 */
export class ControllerMcpProvider implements IProviderMcp {
  async listServers(): Promise<Record<McpScope, ProviderMcpServer[]>> {
    return {
      user: [],
      local: [],
      project: [],
    };
  }

  async listServersForScope(): Promise<ProviderMcpServer[]> {
    return [];
  }

  async upsertServer(_input: UpsertProviderMcpServerInput): Promise<ProviderMcpServer> {
    void _input;
    throw new AppError('controller does not support MCP servers.', {
      code: 'MCP_NOT_SUPPORTED',
      statusCode: 400,
    });
  }

  async removeServer(
    input: { name: string; scope?: McpScope; workspacePath?: string },
  ): Promise<{ removed: boolean; provider: LLMProvider; name: string; scope: McpScope }> {
    return {
      removed: false,
      provider: 'controller',
      name: input.name,
      scope: input.scope ?? 'project',
    };
  }
}
