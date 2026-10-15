import { lazy } from 'react'
import { noteResolver, type NoteEntity } from './resolver'
import NoteSidebar from './NoteSidebar'
import type { EntityRegistration } from '../registry'

// 懒加载: note Editor 消费共享 Wiki Core，打开 note tab 才下载对应 chunk。
const Editor = lazy(() => import('./Editor'))

export type { NoteEntity } from './resolver'
export { invalidateNoteCache } from './resolver'

export const noteRegistration: EntityRegistration<NoteEntity> = {
  resolver: noteResolver,
  renderer: { type: 'note', Editor, SidebarView: NoteSidebar },
  label: 'Note',
  icon: '📄',
}
