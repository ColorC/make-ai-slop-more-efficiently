import type { IProviderMcp } from '@/shared/interfaces.js';
import type {
  LLMProvider,
  McpScope,
  ProviderMcpServer,
  UpsertProviderMcpServerInput,
} from '@/shared/types.js';
import { AppError } from '@/shared/utils.js';

/**
 * omni_agent has no MCP configuration surface. Listing returns empty results for
 * every scope, and mutating operations are rejected rather than silently
 * persisting config the runtime would never read.
 */
export class OmniAgentMcpProvider implements IProviderMcp {
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

  async upsertServer(): Promise<ProviderMcpServer> {
    throw new AppError('omni_agent does not support MCP servers.', {
      code: 'MCP_NOT_SUPPORTED',
      statusCode: 400,
    });
  }

  async removeServer(
    input: { name: string; scope?: McpScope; workspacePath?: string },
  ): Promise<{ removed: boolean; provider: LLMProvider; name: string; scope: McpScope }> {
    return {
      removed: false,
      provider: 'omni_agent',
      name: input.name,
      scope: input.scope ?? 'project',
    };
  }
}
