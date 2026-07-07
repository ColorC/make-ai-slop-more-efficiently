import type { IProviderSessionSynchronizer } from '@/shared/interfaces.js';

/**
 * The controller (总控) writes its transcripts into ~/.claude/projects, exactly
 * like the local Claude runtime it reuses. The filesystem watcher
 * (sessions-watcher.service.ts) has no `controller` watch path and already scans
 * ~/.claude for the `claude` provider, so a controller-specific synchronizer is
 * never invoked and would only risk re-indexing the same transcripts under a
 * second provider id.
 *
 * During a live run the chat gateway records the app-id ↔ provider-id mapping
 * (assignProviderSessionId), which is what the controller session relies on; the
 * standalone disk synchronizer is intentionally a no-op (mirrors omni_agent).
 */
export class ControllerSessionSynchronizer implements IProviderSessionSynchronizer {
  async synchronize(): Promise<number> {
    return 0;
  }

  async synchronizeFile(): Promise<string | null> {
    return null;
  }
}
