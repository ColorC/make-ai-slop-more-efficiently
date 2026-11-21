export const MATERIAL_PLUGIN_CONTRACT_VERSION = '0.1.0'
const stableCompare = (left, right) => left < right ? -1 : left > right ? 1 : 0

const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0
const clone = (value) => structuredClone(value)

export function validateMaterialPluginManifest(manifest) {
  const issues = []
  if (!manifest || typeof manifest !== 'object') return { ok: false, issues: [{ path: '', message: 'must be an object' }] }
  if (manifest.contract_version !== MATERIAL_PLUGIN_CONTRACT_VERSION) issues.push({ path: 'contract_version', message: `must equal ${MATERIAL_PLUGIN_CONTRACT_VERSION}` })
  if (!nonEmpty(manifest.id)) issues.push({ path: 'id', message: 'must be a non-empty string' })
  if (!nonEmpty(manifest.version)) issues.push({ path: 'version', message: 'must be a non-empty string' })
  if (!Array.isArray(manifest.capabilities) || !manifest.capabilities.length
    || manifest.capabilities.some((item) => !nonEmpty(item))) issues.push({ path: 'capabilities', message: 'must contain capability strings' })
  if (manifest.requires !== undefined && (!Array.isArray(manifest.requires)
    || manifest.requires.some((item) => !nonEmpty(item)))) issues.push({ path: 'requires', message: 'must contain plugin IDs' })
  return { ok: issues.length === 0, issues }
}

/** Optional modules keep runtime implementations local and expose only versioned manifests. */
export function createMaterialPluginHost() {
  const plugins = new Map()
  const enabled = new Set()
  return {
    register(plugin) {
      const validation = validateMaterialPluginManifest(plugin?.manifest)
      if (!validation.ok) throw new TypeError(`Invalid Material plugin manifest: ${validation.issues.map((issue) => issue.path).join(', ')}`)
      if (plugins.has(plugin.manifest.id)) throw new TypeError(`Material plugin already registered: ${plugin.manifest.id}`)
      plugins.set(plugin.manifest.id, plugin)
      return this
    },
    async enable(id, context = {}) {
      const plugin = plugins.get(id)
      if (!plugin) throw new TypeError(`Unknown Material plugin: ${id}`)
      for (const dependency of plugin.manifest.requires ?? []) {
        if (!plugins.has(dependency)) throw new TypeError(`Material plugin ${id} requires missing plugin ${dependency}`)
        if (!enabled.has(dependency)) await this.enable(dependency, context)
      }
      if (enabled.has(id)) return
      await plugin.activate?.(context)
      enabled.add(id)
    },
    async disable(id, context = {}) {
      if (!enabled.has(id)) return
      const dependent = [...enabled].find((candidate) => (plugins.get(candidate)?.manifest.requires ?? []).includes(id))
      if (dependent) throw new TypeError(`Material plugin ${id} is required by enabled plugin ${dependent}`)
      await plugins.get(id)?.deactivate?.(context)
      enabled.delete(id)
    },
    get(id) { return plugins.get(id) },
    providers(capability) {
      return [...enabled].map((id) => plugins.get(id))
        .filter((plugin) => plugin.manifest.capabilities.includes(capability))
    },
    list() {
      return [...plugins.values()].map((plugin) => ({ manifest: clone(plugin.manifest), enabled: enabled.has(plugin.manifest.id) }))
        .sort((left, right) => stableCompare(left.manifest.id, right.manifest.id))
    },
  }
}
