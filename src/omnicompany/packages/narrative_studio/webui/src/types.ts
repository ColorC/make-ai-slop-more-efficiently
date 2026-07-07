// 格式契约的 TS 镜像(对应后端 models.py)。前后端共同真源。

export type Status = "todo" | "tocomplete" | "done";
export type VarType = "bool" | "int" | "string";
export type NodeType = "scene" | "hub" | "condition" | "jump" | "ending";
export type RevealOrder = "surface" | "midpoint" | "true_end";

export interface Expr { var: string; op: string; value?: boolean | number | string | null; }
export interface Provenance { source?: string | null; note?: string | null; }
export interface Summary { sentence?: string | null; paragraph?: string | null; full?: string | null; }

export interface Premise {
  proposition?: string | null;
  controlling_ideas: string[];
  stance?: string | null;
  locked: boolean;
  storyform?: unknown;
  provenance?: Provenance | null;
}

export interface RevealLayer {
  id: string; order: RevealOrder; title?: string | null;
  trigger: Expr[]; rewrites?: string | null; rewrites_controlling_idea?: string | null;
  status: Status; provenance?: Provenance | null;
}

export interface WorldNode {
  id: string; name: string; description?: string | null;
  custom_fields: Record<string, unknown>; children: WorldNode[]; provenance?: Provenance | null;
}

export interface CharacterArc { want?: string | null; need?: string | null; wound?: string | null; lie?: string | null; }
export interface DossierField { dimension: string; mode: string; questions: { q?: string; a?: string }[]; freetext?: string | null; status: Status; }
export interface Character {
  id: string; name: string; importance: string; color?: string | null; image?: string | null;
  arc: CharacterArc; summary: Summary; dossier: DossierField[];
  facts: Record<string, unknown>[]; secret?: string | null;
  custom_fields: Record<string, unknown>; status: Status; provenance?: Provenance | null;
}
export interface Relationship { id: string; a: string; b: string; nature?: string | null; label?: string | null; projection?: string | null; provenance?: Provenance | null; }

export interface Variable { namespace: string; name: string; type: VarType; default?: boolean | number | string | null; description?: string | null; counter: boolean; }
export interface StatBlock { name: string; fields: string[]; applies_to: string[]; }
export interface MetaProgress { fields: Variable[]; }
export interface Pressure { id: string; name: string; manifest?: string | null; provenance?: Provenance | null; }
export interface FailureLevel { level: string; manifest?: string | null; prereq_chain: string[]; warning?: string | null; }

export interface Beat { id: string; parent?: string | null; title?: string | null; function?: string | null; summary: Summary; position: number; status: Status; lane?: string | null; edges?: string[]; authority?: string | null; provenance?: Provenance | null; }
export interface StoryLine { id: string; title: string; color?: string | null; character_id?: string | null; }
export interface Arc { emotional: string[]; tension: string[]; }
export interface PacingMarker { kind: string; name: string; core_event?: string | null; main_pressure?: string | null; position: number; }

export interface NodeT { id: string; type: NodeType; title?: string | null; route?: string | null; x: number; y: number; condition: Expr[]; target?: string | null; provenance?: Provenance | null; }
export interface Connection { id: string; source: string; target: string; condition: Expr[]; effects: Expr[]; label?: string | null; }
export interface Ending { node_id: string; name: string; trigger: Expr[]; priority: number; color?: string | null; emotional_color?: string | null; provenance?: Provenance | null; }

export interface SceneLinks { pov?: string | null; characters: string[]; places: string[]; lines: string[]; time?: string | null; }
export interface Choice { label: string; condition: Expr[]; effects: Expr[]; target?: string | null; }
export interface Causality { why_now?: string | null; why_inevitable?: string | null; }
export interface ValueShift { from?: string | null; to?: string | null; }
export interface Intent { emotion?: string | null; punch?: string | null; resonance?: string | null; afterglow?: string | null; }
export interface RenderConstraints { distance?: string | null; focalization?: string | null; voices: string[]; reveal_order?: string | null; show_not_tell: string[]; }
export interface Scene {
  id: string; node_ref?: string | null; title?: string | null; intent_summary?: string | null;
  links: SceneLinks; preconditions: Expr[]; effects: Expr[]; choices: Choice[];
  summary: Summary; objective_events: string[]; causality: Causality; value_shift: ValueShift;
  intent: Intent; render_constraints: RenderConstraints; line_refs: string[]; tags: string[];
  serves_ideas: string[]; status: Status; provenance?: Provenance | null;
}

export interface ProseRevision { text: string; at?: string | null; note?: string | null; }
export interface ProseLine { id: string; scene_ref?: string | null; speaker?: string | null; voice?: string | null; text?: string | null; revisions: ProseRevision[]; status: Status; }
export interface Voice { id: string; register_id?: string | null; syntax?: string | null; lexicon?: string | null; taboos?: string | null; }
export interface Register { id: string; rule?: string | null; }
export interface StyleMatrixEntry { emotion?: string | null; scene_type?: string | null; register_id?: string | null; style_config?: string | null; }

export interface Tag { id: string; name: string; color?: string | null; kind: string; }
export interface Note { id: string; text: string; at?: string | null; }

// —— v2 新载体 ——
export interface GameTextChoice { id?: string | null; label?: string | null; body?: string | null; }
export interface GameText {
  id: string; text_type: string; title?: string | null; category?: string | null; host?: string | null;
  body?: string | null; choices: GameTextChoice[]; art?: string | null; art_status?: string | null;
  annotations?: string | null; related: string[]; is_draft: boolean; status: Status; provenance?: Provenance | null;
}
export interface RejectedItem { id: string; area: string; title: string; verdict: string; reason?: string | null; source?: string | null; excerpt?: string | null; }
export interface AudienceSegment { name: string; note?: string | null; }
export interface Audience { segments: AudienceSegment[]; stance?: string | null; expectations: string[]; resonance_targets: string[]; provenance?: Provenance | null; }
export interface Background { thinking?: string | null; world_notes?: string | null; open_questions: string[]; provenance?: Provenance | null; }
export interface Comment { id: string; target?: string | null; anchor?: string | null; body: string; author?: string | null; resolved: boolean; at?: string | null; }

export interface ProjectMeta { id: string; name: string; version: string; supersedes: string[]; coexists_with: string[]; aesthetic?: string | null; description?: string | null; }

export interface Project {
  meta: ProjectMeta;
  premise: Premise; reveal_layers: RevealLayer[];
  world: WorldNode[]; characters: Character[]; relationships: Relationship[];
  variables: Variable[]; stat_blocks: StatBlock[]; meta_progress: MetaProgress; pressures: Pressure[]; failure_levels: FailureLevel[];
  beats: Beat[]; storylines: StoryLine[]; arc: Arc; pacing: PacingMarker[];
  nodes: NodeT[]; connections: Connection[]; endings: Ending[];
  scenes: Scene[];
  prose_lines: ProseLine[]; voices: Voice[]; registers: Register[]; style_matrix: StyleMatrixEntry[];
  tags: Tag[]; notes: Note[];
  // v2
  audience: Audience; background: Background;
  game_texts: GameText[]; rejected_archive: RejectedItem[]; comments: Comment[];
}

// --- 投影/查询返回 ---
export interface HealthIssue { code: string; severity: "high" | "medium" | "low"; message: string; location?: string; ref?: string; }
export interface SearchHit { kind: string; id: string; title: string; snippet?: string; }
export interface EmptyItem { entity_kind: string; entity_id: string; title?: string; missing_fields: string[]; }
export interface Completeness { by_carrier: Record<string, { todo: number; tocomplete: number; done: number; empty: number; total: number }>; overall: { todo: number; tocomplete: number; done: number; total: number; percent_done: number }; }
export interface PlaythroughResult {
  visited: string[];
  log: { node_id: string; title?: string; applied_effects?: unknown; chosen_edge?: string | null }[];
  state: Record<string, unknown>;
  available: { edge_id: string; target: string; label?: string | null }[];
  ending: { node_id: string; name: string } | null;
  reveals_triggered: string[];
  stopped_reason?: string;
}
