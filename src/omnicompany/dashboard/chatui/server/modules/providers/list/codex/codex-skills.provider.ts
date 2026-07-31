import { readFile, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import TOML from '@iarna/toml';

import { SkillsProvider } from '@/modules/providers/shared/skills/skills.provider.js';
import type {
  ProviderSkill,
  ProviderSkillListOptions,
  ProviderSkillSource,
} from '@/shared/types.js';
import {
  addUniqueProviderSkillSource,
  findTopmostGitRoot,
  findProviderSkillMarkdownFiles,
  readObjectRecord,
  readOptionalString,
  readProviderSkillMarkdownDefinition,
} from '@/shared/utils.js';

const getCodexHomePath = (): string => {
  const configuredHome = process.env.CODEX_HOME?.trim();
  return path.resolve(configuredHome || path.join(os.homedir(), '.codex'));
};

const parsePluginId = (
  pluginId: string,
): { pluginName: string; marketplaceName: string } | null => {
  const separatorIndex = pluginId.lastIndexOf('@');
  if (separatorIndex <= 0 || separatorIndex === pluginId.length - 1) {
    return null;
  }

  return {
    pluginName: pluginId.slice(0, separatorIndex),
    marketplaceName: pluginId.slice(separatorIndex + 1),
  };
};

const resolveManifestPath = (pluginRoot: string, relativePath: string): string | null => {
  if (!relativePath.startsWith('./') && !relativePath.startsWith('.\\')) {
    return null;
  }

  const normalizedPluginRoot = path.resolve(pluginRoot);
  const resolvedPath = path.resolve(normalizedPluginRoot, relativePath);
  if (
    resolvedPath !== normalizedPluginRoot
    && !resolvedPath.startsWith(`${normalizedPluginRoot}${path.sep}`)
  ) {
    return null;
  }

  return resolvedPath;
};

const readCodexConfig = async (codexHomePath: string): Promise<Record<string, unknown>> => {
  try {
    const content = await readFile(path.join(codexHomePath, 'config.toml'), 'utf8');
    return readObjectRecord(TOML.parse(content)) ?? {};
  } catch {
    return {};
  }
};

const listCachedPluginVersions = async (pluginCacheRoot: string): Promise<string[]> => {
  try {
    const entries = await readdir(pluginCacheRoot, { withFileTypes: true });
    return entries
      .filter((entry) => (
        entry.name.toLowerCase() !== 'latest'
        && (entry.isDirectory() || entry.isSymbolicLink())
      ))
      .map((entry) => path.join(pluginCacheRoot, entry.name))
      .sort((left, right) => path.basename(right).localeCompare(
        path.basename(left),
        undefined,
        { numeric: true, sensitivity: 'base' },
      ));
  } catch {
    return [];
  }
};

export class CodexSkillsProvider extends SkillsProvider {
  constructor() {
    super('codex');
  }

  async listSkills(options?: ProviderSkillListOptions): Promise<ProviderSkill[]> {
    const codexHomePath = getCodexHomePath();
    return [
      ...(await super.listSkills(options)),
      ...(await this.listPluginSkills(codexHomePath)),
    ];
  }

  protected async getSkillSources(workspacePath: string): Promise<ProviderSkillSource[]> {
    const sources: ProviderSkillSource[] = [];
    const seenRootDirs = new Set<string>();
    const repoRoot = await findTopmostGitRoot(workspacePath);
    const codexHomePath = getCodexHomePath();

    let currentRepoPath = path.resolve(workspacePath);
    while (true) {
      addUniqueProviderSkillSource(sources, seenRootDirs, {
        scope: 'repo',
        rootDir: path.join(currentRepoPath, '.agents', 'skills'),
        commandPrefix: '$',
      });

      if (!repoRoot || currentRepoPath === repoRoot) {
        break;
      }
      const parentPath = path.dirname(currentRepoPath);
      if (parentPath === currentRepoPath) {
        break;
      }
      currentRepoPath = parentPath;
    }

    addUniqueProviderSkillSource(sources, seenRootDirs, {
      scope: 'user',
      rootDir: path.join(os.homedir(), '.agents', 'skills'),
      commandPrefix: '$',
    });
    addUniqueProviderSkillSource(sources, seenRootDirs, {
      scope: 'user',
      rootDir: path.join(codexHomePath, 'skills'),
      commandPrefix: '$',
    });
    addUniqueProviderSkillSource(sources, seenRootDirs, {
      scope: 'admin',
      rootDir: path.join('/etc', 'codex', 'skills'),
      commandPrefix: '$',
    });
    addUniqueProviderSkillSource(sources, seenRootDirs, {
      scope: 'system',
      rootDir: path.join(codexHomePath, 'skills', '.system'),
      commandPrefix: '$',
    });

    return sources;
  }

  private async listPluginSkills(codexHomePath: string): Promise<ProviderSkill[]> {
    const config = await readCodexConfig(codexHomePath);
    const plugins = readObjectRecord(config.plugins);
    if (!plugins) {
      return [];
    }

    const skills: ProviderSkill[] = [];
    for (const [pluginId, rawPluginConfig] of Object.entries(plugins).sort(([left], [right]) => (
      left.localeCompare(right)
    ))) {
      const pluginConfig = readObjectRecord(rawPluginConfig);
      if (pluginConfig?.enabled !== true) {
        continue;
      }

      const parsedPluginId = parsePluginId(pluginId);
      if (!parsedPluginId) {
        continue;
      }

      const pluginCacheRoot = path.join(
        codexHomePath,
        'plugins',
        'cache',
        parsedPluginId.marketplaceName,
        parsedPluginId.pluginName,
      );
      const cachedVersions = await listCachedPluginVersions(pluginCacheRoot);
      for (const pluginRoot of cachedVersions) {
        const manifest = await this.readPluginManifest(pluginRoot);
        if (!manifest) {
          continue;
        }

        skills.push(...(await this.listPluginVersionSkills(pluginRoot, pluginId, manifest)));
        // Cache folders may retain older versions. The highest valid installed
        // version is authoritative for one enabled plugin.
        break;
      }
    }

    return skills;
  }

  private async readPluginManifest(pluginRoot: string): Promise<Record<string, unknown> | null> {
    try {
      const content = await readFile(
        path.join(pluginRoot, '.codex-plugin', 'plugin.json'),
        'utf8',
      );
      return readObjectRecord(JSON.parse(content));
    } catch {
      return null;
    }
  }

  private async listPluginVersionSkills(
    pluginRoot: string,
    pluginId: string,
    manifest: Record<string, unknown>,
  ): Promise<ProviderSkill[]> {
    const pluginName = readOptionalString(manifest.name);
    const skillsRelativePath = readOptionalString(manifest.skills);
    if (!pluginName || !skillsRelativePath) {
      return [];
    }

    const skillsRoot = resolveManifestPath(pluginRoot, skillsRelativePath);
    if (!skillsRoot) {
      return [];
    }

    const skills: ProviderSkill[] = [];
    for (const skillPath of await findProviderSkillMarkdownFiles(skillsRoot)) {
      try {
        const definition = await readProviderSkillMarkdownDefinition(skillPath);
        skills.push({
          provider: this.provider,
          name: definition.name,
          description: definition.description,
          command: `$${pluginName}:${definition.name}`,
          scope: 'plugin',
          sourcePath: skillPath,
          pluginName,
          pluginId,
        });
      } catch {
        // One malformed bundled skill should not hide sibling plugin skills.
      }
    }

    return skills;
  }
}
