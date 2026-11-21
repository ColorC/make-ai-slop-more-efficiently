import type React from 'react'
import type { Entity, EntityFacet, EntityRef, EntityType } from './types'

export interface EntityResolver<T extends Entity = Entity> {
  type: EntityType
  fetch(id: string): Promise<T>
  list(): Promise<T[]>
  search?(q: string): Promise<T[]>
}

export interface SidebarViewProps {
  filter: string
  activeId: string | null
  openTab: (ref: EntityRef, title: string, facet?: string) => string
}

export interface EntityRenderer<T extends Entity = Entity> {
  type: EntityType
  // ComponentType(非 FC) 以便接受 React.lazy(...) 懒加载组件 —— 重查看器(graph/cc_session 等)
  // 的 Editor 用 lazy 拆出独立 chunk, 切到该 tab 才下载, 不再常驻首屏 bundle。
  Editor: React.ComponentType<{ entity: T; facet?: string }>
  ListItem?: React.FC<{ entity: T; onOpen: (e: T, facet?: string) => void }>
  SidebarView?: React.FC<SidebarViewProps>
  HoverCard?: React.FC<{ entity: T }>
  facets?: EntityFacet[]
  defaultFacet?: string
}

export interface EntityRegistration<T extends Entity = Entity> {
  resolver: EntityResolver<T>
  renderer: EntityRenderer<T>
  label: string
  icon?: React.ReactNode
}

class EntityRegistry {
  private map = new Map<EntityType, EntityRegistration>()

  register<T extends Entity>(reg: EntityRegistration<T>): void {
    if (reg.resolver.type !== reg.renderer.type) {
      throw new Error(`type mismatch: resolver=${reg.resolver.type} renderer=${reg.renderer.type}`)
    }
    this.map.set(reg.resolver.type, reg as unknown as EntityRegistration)
  }

  get(type: EntityType): EntityRegistration | undefined {
    return this.map.get(type)
  }

  has(type: EntityType): boolean {
    return this.map.has(type)
  }

  types(): EntityType[] {
    return Array.from(this.map.keys())
  }

  all(): EntityRegistration[] {
    return Array.from(this.map.values())
  }
}

export const registry = new EntityRegistry()
