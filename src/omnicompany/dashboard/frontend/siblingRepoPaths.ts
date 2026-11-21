// vite.config.ts 与 vitest.config.ts 共用的共享包路径。
// 公开仓把 wiki-core、material-* 与 material-http-plugin 放在仓根 packages/ 下。
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const frontendRoot = dirname(fileURLToPath(import.meta.url))
export const workspaceRoot = process.env.OMNI_WORKSPACE_ROOT || resolve(frontendRoot, '../../../..')
const sharedPackage = (name: string) => resolve(workspaceRoot, 'packages', name)

export const wikiCore = sharedPackage('wiki-core')
export const materialCore = sharedPackage('material-core')
export const materialClient = sharedPackage('material-client')
export const materialViewReact = sharedPackage('material-view-react')
export const materialNoteCore = sharedPackage('material-note-core')
export const overlayNotes = resolve(frontendRoot, '../notes-web/src')
export const bsideMaterialPlugin = resolve(workspaceRoot, 'packages/material-http-plugin/index.ts')
