import type { IProviderModels } from '@/shared/interfaces.js';
import type {
  ProviderChangeActiveModelInput,
  ProviderCurrentActiveModel,
  ProviderModelsDefinition,
  ProviderSessionActiveModelChange,
} from '@/shared/types.js';
import {
  buildDefaultProviderCurrentActiveModel,
  writeProviderSessionActiveModelChange,
} from '@/shared/utils.js';

/**
 * Kimi Code CLI model catalog. The values are the model aliases accepted by
 * `kimi -m <alias>` and mirror the `[models.<alias>]` entries kimi-code ships
 * in `~/.kimi-code/config.toml` (`default_model = "kimi-code/k3"`).
 */
export const KIMI_MODELS: ProviderModelsDefinition = {
  OPTIONS: [
    { value: 'kimi-code/k3', label: 'Kimi K3' },
    { value: 'kimi-code/kimi-for-coding', label: 'Kimi for Coding' },
    { value: 'kimi-code/kimi-for-coding-highspeed', label: 'Kimi for Coding (Highspeed)' },
  ],
  DEFAULT: 'kimi-code/k3',
};

export class KimiProviderModels implements IProviderModels {
  async getSupportedModels(): Promise<ProviderModelsDefinition> {
    return KIMI_MODELS;
  }

  async getCurrentActiveModel(): Promise<ProviderCurrentActiveModel> {
    return buildDefaultProviderCurrentActiveModel(KIMI_MODELS);
  }

  async changeActiveModel(
    input: ProviderChangeActiveModelInput,
  ): Promise<ProviderSessionActiveModelChange> {
    return writeProviderSessionActiveModelChange('kimi', input);
  }
}
