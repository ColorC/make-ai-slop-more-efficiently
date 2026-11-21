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
 * The controller (总控) always runs on Claude Opus. We surface a single opus
 * option so the model picker reflects that the runtime is fixed — the actual
 * model is forced to 'opus' in server/controller-cli.js regardless.
 */
export const CONTROLLER_MODELS: ProviderModelsDefinition = {
  OPTIONS: [
    {
      value: 'opus',
      label: 'Claude Opus(总控)',
      description: '本地 Claude Code · Opus · 注入总控系统提示',
    },
  ],
  DEFAULT: 'opus',
};

export class ControllerProviderModels implements IProviderModels {
  async getSupportedModels(): Promise<ProviderModelsDefinition> {
    return CONTROLLER_MODELS;
  }

  async getCurrentActiveModel(): Promise<ProviderCurrentActiveModel> {
    return buildDefaultProviderCurrentActiveModel(CONTROLLER_MODELS);
  }

  async changeActiveModel(
    input: ProviderChangeActiveModelInput,
  ): Promise<ProviderSessionActiveModelChange> {
    return writeProviderSessionActiveModelChange('controller', input);
  }
}
