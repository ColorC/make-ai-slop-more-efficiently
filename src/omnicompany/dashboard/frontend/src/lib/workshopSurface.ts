export type WorkshopSurfaceKind = 'materials' | 'canvas'

/** Project page and companion overview must hit the same wiki host. */
export function wikiHostProject(project?: string): string | undefined {
  if (!project) return undefined
  const id = project.trim()
  if (!id) return undefined
  if (id === 'bside' || id === 'spark' || id === 'spark.workshop') return 'spark-workshop'
  return id
}

export function workshopSurfaceUrl(
  kind: WorkshopSurfaceKind,
  query: Record<string, string | undefined> = {},
): string {
  if (kind === 'canvas') {
    // 2026-08-16 用户死命令(唯一画布前端): 一切画布面 = notes-web(真源 dashboard/notes-web)
    // (旧札记三视图: 画布/文档/审阅), 仅主题差异。workshop Vue 画布已归档
    // (spark.workshop _archive/web-canvas-vue), /bside/canvas 现为 CanvasHost
    // (同样嵌 notes-web)——dashboard 直接嵌同源 notes-web, 少走一跳。
    // 存储经 8210 桥透明转发 @omni/canvas-store(builtin 域)。
    // 2026-08-18 统一应用鉴权: canvas 面(notes-web)改挂 /api/embed/notes-web,
    // 与其余 dashboard API 同一条网关鉴权路径; /lofa/overlay/app 的 basic_auth
    // 兜底会在画布 iframe 里弹登录表单(桌面会话 cookie 缺失时), 已弃用。
    // bridge_prefix 让 notes-web 的 invoke-ws/invoke/file 全部跟进同一命名空间。
    const params = new URLSearchParams({ theme: 'omni', bridge_prefix: '/api/embed' })
    // notes-web 的 session 参数是裸会话号(自己拼 external: 再编码); 若给的是
    // 存储 id(session-<base64url>)先解码, 否则二次编码成不存在的画布(与
    // workshop CanvasHost canvasToSession 同逻辑)。
    const decodeCanvas = (value: string): string => {
      if (!value.startsWith('session-')) return value
      try {
        const b64 = value.slice('session-'.length).replace(/-/g, '+').replace(/_/g, '/')
        const decoded = atob(b64)
        return decoded.startsWith('external:') ? decoded.slice('external:'.length) : value
      } catch { return value }
    }
    if (query.session) params.set('session', query.session)
    if (query.canvas) {
      // A session window keeps its session id. The canvas pointer is a
      // separate document id so history/favorites can switch canvases
      // without binding the canvas to a new session.
      if (query.session) params.set('canvas', query.canvas)
      else params.set('session', decodeCanvas(query.canvas))
    }
    if (query.note) params.set('note', query.note)
    if (query.card) params.set('focus', query.card)
    const suffix = params.toString()
    return `/api/embed/notes-web/notes-web.html${suffix ? `?${suffix}` : ''}`
  }
  const params = new URLSearchParams({ theme: 'omni', embed: '1' })
  const project = wikiHostProject(query.project) || query.project
  if (project) params.set('project', project)
  if (query.id) params.set('id', query.id)
  if (query.view) params.set('view', query.view)
  return `/bside/materials?${params.toString()}`
}
