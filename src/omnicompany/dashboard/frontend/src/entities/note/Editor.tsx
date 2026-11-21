import React from 'react'
import type { NoteEntity } from './resolver'

const WorkshopSurface = React.lazy(() => import('../studio_reader/WorkshopSurface').then((module) => ({
  default: module.WorkshopSurface,
})))

/** Notes have one live frontend: the shared Workshop canvas. */
export default function NoteEditor({ entity }: { entity: NoteEntity }) {
  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <React.Suspense fallback={<div>正在打开画布…</div>}>
        <WorkshopSurface kind="canvas" note={entity.id} canvas={entity.id} />
      </React.Suspense>
    </div>
  )
}
