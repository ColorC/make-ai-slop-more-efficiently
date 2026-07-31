// 项目详情页「技能」页签 — 集合 atlas 技能(object-SKILL)与 omni run 管线注册表。
// 2026-07-06 用户: 常用工作选项(低频)删掉, 换成链接 atlas 的技能页签; 不做网页快捷执行
// (全部内容经过 AI), 技能同时展示 Claude Code `/技能名` 与 Codex `$技能名` 调用词。
// 数据源 GET /api/skills(controlplane/skills.py); "本项目空间"按 atlas spaces.root 对项目
// roots 的包含关系判定, 相关的排前面并打标。

import React, { useEffect, useMemo, useState } from 'react'
import { Copy, ExternalLink, GitBranch, Wrench } from 'lucide-react'
import { copyText } from '../../lib/copyText'
import { openInVscode } from '../../lib/openInVscode'
import { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { ItemCard, MONO, dimStyle, gridStyle, secTitleStyle } from './cards'

interface AtlasSkill { space: string; name: string; status: 'canonical' | 'staging'; description: string; path: string }
interface PipelineItem { name: string; domain: string; description: string; aliases: string[] }
interface SkillsPayload {
  generated_at?: string
  skills: AtlasSkill[]
  pipelines: PipelineItem[]
  spaces: Record<string, { root: string; group?: string }>
}

const norm = (p: string) => (p || '').replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '')

export default function ProjectSkills({ projectRoots }: { projectRoots: string[] }) {
  const [data, setData] = useState<SkillsPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    fetch('/api/skills')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then(setData)
      .catch((e) => setError(String(e?.message || e)))
  }, [])

  // 本项目空间: 项目任一 root 与 space root 存在包含关系(项目在空间内, 或空间在项目 root 内)
  const relatedSpaces = useMemo(() => {
    const out = new Set<string>()
    if (!data) return out
    const roots = projectRoots.map(norm).filter(Boolean)
    for (const [space, info] of Object.entries(data.spaces || {})) {
      const sr = norm(info.root)
      if (!sr) continue
      if (roots.some((r) => r === sr || r.startsWith(sr + '/') || sr.startsWith(r + '/'))) out.add(space)
    }
    return out
  }, [data, projectRoots])

  const q = filter.trim().toLowerCase()
  const match = (...hay: (string | undefined)[]) => !q || hay.some((h) => (h || '').toLowerCase().includes(q))

  const skills = useMemo(() => {
    const items = (data?.skills || []).filter((s) => match(s.name, s.description, s.space))
    return items.sort((a, b) => {
      const ra = relatedSpaces.has(a.space) ? 0 : 1
      const rb = relatedSpaces.has(b.space) ? 0 : 1
      if (ra !== rb) return ra - rb
      if (a.status !== b.status) return a.status === 'canonical' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  }, [data, q, relatedSpaces])

  const pipelines = useMemo(
    () => (data?.pipelines || []).filter((p) => match(p.name, p.description, p.domain)),
    [data, q],
  )

  if (error) return <div style={dimStyle}>技能清单加载失败: {error}</div>
  if (!data) return <div style={dimStyle}>加载中…</div>

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="搜技能 / 管线(名称·描述·空间·域)"
          data-testid="project-skills-filter"
          style={{
            flex: '0 1 340px', minWidth: 200, boxSizing: 'border-box', padding: '7px 11px',
            border: '1px solid var(--fp-border)', borderRadius: 7, background: 'var(--fp-surface)',
            color: 'var(--fp-text)', fontSize: 13, outline: 'none',
          }}
        />
        <span style={{ color: 'var(--fp-text-3)', fontSize: 12 }}>
          数据源 omni atlas(资源中心) + omni run 注册表 · 技能 {data.skills.length} · 管线 {data.pipelines.length}
          · 调用一律交给 AI: Claude Code 用 /技能名，Codex 用 $技能名
        </span>
      </div>

      <div style={secTitleStyle}>技能(atlas object-SKILL)</div>
      {skills.length === 0 && <div style={dimStyle}>{q ? '没有匹配的技能' : 'atlas 里还没有技能'}</div>}
      {skills.length > 0 && (
        <div style={gridStyle} data-testid="project-skills-list">
          {skills.map((s) => {
            const related = relatedSpaces.has(s.space)
            const invocable = s.status === 'canonical'
            const kebab: KebabItem[] = [
              ...(invocable ? [
                { label: '复制 Codex $技能名', icon: <Copy size={15} />, onClick: () => { void copyText(`$${s.name}`) } },
                { label: '复制 Claude /技能名', icon: <Copy size={15} />, onClick: () => { void copyText(`/${s.name}`) } },
              ] : []),
              { label: '复制 SKILL.md 路径', icon: <Copy size={15} />, onClick: () => { void copyText(s.path) } },
              { label: '在 VSCode 打开', icon: <ExternalLink size={15} />, onClick: () => openInVscode(s.path) },
            ]
            return (
              <ItemCard
                key={`${s.space}/${s.name}`}
                icon={<Wrench size={13} />}
                badge={`技能${related ? ' · 本项目空间' : ''}${s.status === 'staging' ? ' · 待审' : ''}`}
                title={s.name}
                titleAttr={s.description || s.name}
                meta={<span title={s.description}>{s.space}{s.description ? ` · ${s.description}` : ''}</span>}
                onOpen={() => { if (invocable) void copyText(`$${s.name}`); else openInVscode(s.path) }}
                openLabel={invocable ? `复制 Codex $${s.name}` : '打开待审 SKILL.md'}
                kebab={kebab}
              />
            )
          })}
        </div>
      )}

      <div style={secTitleStyle}>管线(omni run)</div>
      {pipelines.length === 0 && <div style={dimStyle}>{q ? '没有匹配的管线' : '注册表里没有管线'}</div>}
      {pipelines.length > 0 && (
        <div style={gridStyle} data-testid="project-pipelines-list">
          {pipelines.map((p) => (
            <ItemCard
              key={p.name}
              icon={<GitBranch size={13} />}
              badge="管线"
              title={p.name}
              titleAttr={p.description || p.name}
              meta={<span title={p.description}>{p.domain}{p.description ? ` · ${p.description}` : ''}</span>}
              onOpen={() => { void copyText(`omni run ${p.name}`) }}
              openLabel={`复制 omni run ${p.name}`}
              kebab={[
                { label: '复制运行命令', icon: <Copy size={15} />, onClick: () => { void copyText(`omni run ${p.name}`) } },
                ...(p.aliases.length ? [{ label: `别名: ${p.aliases.join(', ')}`, icon: <Copy size={15} />, onClick: () => { void copyText(p.aliases.join(' ')) } }] : []),
              ]}
            />
          ))}
        </div>
      )}
      <div style={{ color: 'var(--fp-text-3)', fontSize: 12, marginTop: 14, fontFamily: MONO }}>
        审批/导出走 omni atlas(list / approve / export) · 管线明细 omni pipelines --verbose
      </div>
    </div>
  )
}
