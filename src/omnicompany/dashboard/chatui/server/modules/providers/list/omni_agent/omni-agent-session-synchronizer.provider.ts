import type { IProviderSessionSynchronizer } from '@/shared/interfaces.js';

/**
 * omni_agent keeps no on-disk session artifacts, so there is nothing for the
 * filesystem-watcher-driven indexer to scan or upsert.
 */
export class OmniAgentSessionSynchronizer implements IProviderSessionSynchronizer {
  async synchronize(): Promise<number> {
    // TODO(omni D4): omni_agent 无 on-disk JSONL, 刷新后历史暂空, 待 shim 落 JSONL 或自管存储
    return 0;
  }

  async synchronizeFile(): Promise<string | null> {
    // TODO(omni D4): omni_agent 无 on-disk JSONL, 刷新后历史暂空, 待 shim 落 JSONL 或自管存储
    return null;
  }
}
