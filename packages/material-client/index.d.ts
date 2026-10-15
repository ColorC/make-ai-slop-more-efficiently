import type {
  ActorRef,
  Material,
  MaterialGraph,
  MaterialRepresentation,
  MaterialRevisionRef,
  MaterialSourceRef,
} from '../material-core/index.js'

export interface MaterialRepresentationContent { content: string; content_format?: 'markdown' | 'text' | 'json' }
export interface MaterialProviderEvent { kind: 'graph-changed'; graph: MaterialGraph; request?: MaterialWriteRequest }
export interface MaterialWriteRequest {
  workspace_ref: MaterialSourceRef
  actor_ref: ActorRef
  target: { material_id: string; representation_id?: string }
  base_revision_ref?: MaterialRevisionRef
  idempotency_key: string
  next_graph: MaterialGraph
}
export interface MaterialWriteResult { status: 'written' | 'idempotent'; graph: MaterialGraph; idempotency_key: string }
export interface MaterialProvider {
  id?: string
  readGraph(options?: { signal?: AbortSignal }): Promise<MaterialGraph>
  readRepresentation?(material: Material, representation: MaterialRepresentation, options?: { signal?: AbortSignal }): Promise<string | MaterialRepresentationContent>
  writeGraph?(request: MaterialWriteRequest): Promise<MaterialWriteResult>
  subscribe?(listener: (event: MaterialProviderEvent) => void): () => void
}
export class MaterialProviderError extends Error { code: string; detail?: unknown }
export class MaterialWriteConflictError extends MaterialProviderError {}
export function assertMaterialProvider(provider: unknown): asserts provider is MaterialProvider
export function loadMaterialGraph(provider: MaterialProvider, options?: { signal?: AbortSignal }): Promise<MaterialGraph>
export function loadMaterialRepresentation(provider: MaterialProvider, material: Material, representation: MaterialRepresentation, options?: { signal?: AbortSignal }): Promise<MaterialRepresentationContent>
export function validateMaterialWriteRequest(request: unknown): { ok: boolean; issues: Array<{ path: string; message: string }> }
export function writeMaterialGraph(provider: MaterialProvider, request: MaterialWriteRequest): Promise<MaterialWriteResult>
export function createMemoryMaterialProvider(graph: MaterialGraph, options?: { id?: string; representations?: Record<string, string | MaterialRepresentationContent> }): MaterialProvider
export function createHttpMaterialProvider(options: {
  id?: string
  graph_url: string
  representation_url?: (material: Material, representation: MaterialRepresentation) => string | undefined
  decode_representation?: (response: Response, material: Material, representation: MaterialRepresentation) => Promise<string | MaterialRepresentationContent>
  headers?: HeadersInit
  fetch?: typeof fetch
  write?: MaterialProvider['writeGraph']
  subscribe?: MaterialProvider['subscribe']
}): MaterialProvider

export const MATERIAL_PLUGIN_CONTRACT_VERSION: '0.1.0'
export interface MaterialPluginManifest {
  contract_version: typeof MATERIAL_PLUGIN_CONTRACT_VERSION
  id: string
  version: string
  capabilities: string[]
  requires?: string[]
  description?: string
  transport?: { kind: string; ref: string }
}
export interface MaterialPlugin<T = unknown> {
  manifest: MaterialPluginManifest
  implementation?: T
  activate?: (context: unknown) => void | Promise<void>
  deactivate?: (context: unknown) => void | Promise<void>
}
export interface MaterialPluginHost {
  register(plugin: MaterialPlugin): MaterialPluginHost
  enable(id: string, context?: unknown): Promise<void>
  disable(id: string, context?: unknown): Promise<void>
  get(id: string): MaterialPlugin | undefined
  providers(capability: string): MaterialPlugin[]
  list(): Array<{ manifest: MaterialPluginManifest; enabled: boolean }>
}
export function validateMaterialPluginManifest(manifest: unknown): { ok: boolean; issues: Array<{ path: string; message: string }> }
export function createMaterialPluginHost(): MaterialPluginHost
