import { MaterialEmbed } from '../../../entities/review/MaterialEmbed'
import { WorkshopSurface } from '../../../entities/studio_reader/WorkshopSurface'
import WikiCoreMarkdown, { type WikiCoreEmbedTarget } from './WikiCoreMarkdown'

export default function MaterialCoreWikiMarkdown({
  source,
  project,
  className,
}: {
  source: string
  project: string
  className?: string
}) {
  const renderEmbed = (embed: WikiCoreEmbedTarget) => {
    if (embed.kind === 'review_material') {
      return <MaterialEmbed
        reference={`omni://review/${encodeURIComponent(embed.target)}`}
        label={embed.label}
      />
    }
    if (embed.kind !== 'material' && embed.kind !== 'material_graph') return null
    return (
      <section className="wiki-core-material-embed" data-testid="material-core-wiki-embed">
        <header>{embed.kind === 'material_graph' ? 'Material Group' : 'Material'} · {embed.label || embed.target}</header>
        <div className="wiki-core-material-embed__surface">
          <WorkshopSurface
            kind="materials"
            project={project}
            materialId={embed.kind === 'material' ? embed.target : undefined}
            materialViewId={embed.kind === 'material_graph' ? embed.target : undefined}
          />
        </div>
      </section>
    )
  }

  return <WikiCoreMarkdown
    source={source}
    className={className}
    materialProject={project}
    renderEmbed={renderEmbed}
  />
}
