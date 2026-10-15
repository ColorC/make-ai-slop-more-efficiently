import type { ReactNode } from 'react'
import type { DisplayPluginContext, Material, MaterialGraph, MaterialRepresentation, MaterialVersionRef, MaterialViewMember } from '../material-core/index.js'
import type { MaterialProvider, MaterialRepresentationContent } from '../material-client/index.js'

export interface MaterialRenderer {
  id: string
  priority?: number
  capabilities?: Array<'read' | 'edit' | 'fullscreen' | 'print' | 'mobile' | 'source-navigation' | string>
  match: {
    format_tag?: string | string[]
    renderer_hint?: string | string[]
    media_type?: string | string[]
    labels?: string | string[]
    when?: (material: Material, representation: MaterialRepresentation) => boolean
  }
  render: (context: {
    material: Material
    representation: MaterialRepresentation
    content?: string
    onOpenMaterial: (material: Material) => void
    onOpenRepresentation?: (material: Material, representation: MaterialRepresentation) => void
  }) => ReactNode
}

export interface MaterialWorkspaceProps {
  graph?: MaterialGraph
  provider?: MaterialProvider
  theme?: string
  title?: string
  initialViewId?: string
  navigation?: 'rail' | 'hidden'
  onOpenMaterial?: (material: Material) => void
  onOpenRepresentation?: (material: Material, representation: MaterialRepresentation) => void
  onActiveViewChange?: (viewId: string) => void
  loadRepresentation?: (
    material: Material,
    representation: MaterialRepresentation,
    options?: { signal?: AbortSignal },
  ) => Promise<string | MaterialRepresentationContent>
  renderers?: MaterialRenderer[]
  renderMarkdown?: (
    source: string,
    material: Material,
    representation: MaterialRepresentation,
  ) => ReactNode
}

export function MaterialWorkspace(props: MaterialWorkspaceProps): ReactNode
export function selectMaterialRenderer(renderers: MaterialRenderer[], material: Material, representation: MaterialRepresentation, context?: DisplayPluginContext): MaterialRenderer | undefined
export function createDefaultMaterialRenderers(createElement: (...args: any[]) => ReactNode, options?: {
  renderMarkdown?: MaterialWorkspaceProps['renderMarkdown']
  renderTutorial?: (source: string, material: Material, representation: MaterialRepresentation) => ReactNode
  renderLatex?: (source: string, material: Material, representation: MaterialRepresentation) => ReactNode
  renderMermaid?: (source: string, material: Material, representation: MaterialRepresentation) => ReactNode
}): MaterialRenderer[]
export function displayContextForItem(viewMaterial: Material, member: MaterialViewMember): DisplayPluginContext
export interface MaterialNavigationRow { view_id: string; parent_view_id: string | null; title: string; member_count: number; depth: number }
export interface MaterialReadModel {
  contract_version: string
  graph_id: string
  view_id: string
  view_revision_ref: MaterialVersionRef
  mode: 'source' | 'expanded'
  navigation: MaterialNavigationRow[]
  breadcrumbs: string[]
  items: Array<{ material_id: string; revision_ref: MaterialVersionRef; member: MaterialViewMember; depth: number; owner_view_id: string }>
  outline: Array<{ material_id: string; representation_id?: string; title: string; depth: number; anchor: string }>
  relationships: string[]
  diagnostics: Array<{ kind: string; at: string; detail: string }>
  search_results: Array<{ material_id: string; revision_ref: MaterialVersionRef; title: string }>
}
export function buildMaterialNavigation(graph: MaterialGraph): MaterialNavigationRow[]
export function searchMaterialGraph(graph: MaterialGraph, query: string, options?: { limit?: number }): MaterialReadModel['search_results']
export function buildMaterialReadModel(graph: MaterialGraph, viewId: string, options?: { mode?: 'source' | 'expanded'; query?: string; max_depth?: number; max_items?: number; search_limit?: number }): MaterialReadModel
