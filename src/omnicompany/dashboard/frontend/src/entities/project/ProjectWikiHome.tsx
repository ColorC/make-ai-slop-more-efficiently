import { useEffect, useRef, useState } from 'react'
import { projectsApi, type ProjectItem } from '../../api/projectsClient'
import MaterialCoreWikiMarkdown from '../../shared/view/wiki/MaterialCoreWikiMarkdown'
import './projectDetail.css'

interface OutlineItem {
  id: string
  text: string
  level: number
}

function withoutFrontmatter(source: string): string {
  return source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '')
}

/** The one project Wiki homepage shared by ProjectDetail and SessionCompanion. */
export default function ProjectWikiHome({
  projectId,
  project,
  indexContent,
  embedded = false,
}: {
  projectId: string
  project?: ProjectItem | null
  indexContent?: string | null
  embedded?: boolean
}) {
  const [loadedProject, setLoadedProject] = useState<ProjectItem | null>(project ?? null)
  const [loadedIndex, setLoadedIndex] = useState<string | null>(indexContent ?? null)
  const [outline, setOutline] = useState<OutlineItem[]>([])
  const rootRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (project !== undefined) {
      setLoadedProject(project)
      return
    }
    let live = true
    projectsApi.list()
      .then((board) => {
        if (live) setLoadedProject(board.projects.find((item) => item.id === projectId) || null)
      })
      .catch(() => { if (live) setLoadedProject(null) })
    return () => { live = false }
  }, [project, projectId])

  useEffect(() => {
    if (indexContent !== undefined) {
      setLoadedIndex(indexContent)
      return
    }
    let live = true
    projectsApi.index(projectId)
      .then((document) => {
        if (live) setLoadedIndex(document.ok && document.content ? withoutFrontmatter(document.content) : null)
      })
      .catch(() => { if (live) setLoadedIndex(null) })
    return () => { live = false }
  }, [indexContent, projectId])

  useEffect(() => {
    if (loadedIndex === null) {
      setOutline([])
      return
    }
    const timer = window.setTimeout(() => {
      const headings = [...(rootRef.current?.querySelectorAll<HTMLElement>('.wiki-prose h2, .wiki-prose h3') || [])]
      setOutline(headings.map((heading, index) => {
        if (!heading.id) heading.id = `project-wiki-heading-${index}`
        return {
          id: heading.id,
          text: heading.textContent || '',
          level: heading.tagName === 'H2' ? 2 : 3,
        }
      }))
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadedIndex])

  const scrollToHeading = (id: string) => {
    const heading = [...(rootRef.current?.querySelectorAll<HTMLElement>('[id]') || [])]
      .find((node) => node.id === id)
    heading?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div
      ref={rootRef}
      className={`project-wiki-home${embedded ? ' project-wiki-home--embedded' : ''}`}
      data-testid="project-wiki-home"
      data-project-id={projectId}
    >
      <div className="pd-reader" data-testid="pd-home">
        <article className="pd-article">
          {loadedIndex !== null
            ? <MaterialCoreWikiMarkdown source={loadedIndex} project={projectId} />
            : (
                <div className="pd-fallback">
                  <h1>{loadedProject?.name || projectId}</h1>
                  <p>{loadedProject?.desc || loadedProject?.short || '项目首页暂不可用。'}</p>
                </div>
              )}
        </article>
        {outline.length > 0 && (
          <nav className="pd-outline" aria-label="项目 Wiki 大纲" data-testid="pd-outline">
            <strong>大纲</strong>
            {outline.map((heading) => (
              <button
                key={heading.id}
                type="button"
                className={`pd-outline-item lvl-${heading.level}`}
                onClick={() => scrollToHeading(heading.id)}
              >
                {heading.text}
              </button>
            ))}
          </nav>
        )}
      </div>
    </div>
  )
}
