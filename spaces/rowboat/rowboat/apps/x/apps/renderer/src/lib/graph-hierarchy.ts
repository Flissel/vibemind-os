// EHL addition (Flissel/rowboat ehl-schema-graph): hierarchical brain graph. Reads the vault schema's flat
// front matter (level, parent, domain, typed relations) and lays notes out on one ring per level.
// Spec: Flissel/ehl-munich-2026 docs/superpowers/specs/2026-10-12-brain-graph-hierarchy-design.md
// Modified for VibeMind (Flissel/rowboat vibemind): graphFileData, a tolerant workspace:readFile result reader.
import { splitFrontmatter } from './frontmatter'

export type Relation = { name: string; target: string }
export type Hierarchy = { level?: number; parent?: string; domain?: string; relations: Relation[] }

const LINK = /\[\[([^[\]]+)\]\]/g
const KEY_LINE = /^([A-Za-z_]\w*):\s*(.*)$/

export function linkTarget(raw: string): string {
  return raw.split('|')[0].split('#')[0].trim()
}

function linksIn(value: string): string[] {
  return Array.from(value.matchAll(LINK), (m) => linkTarget(m[1])).filter((t) => t.length > 0)
}

function unquote(value: string): string {
  return value.trim().replace(/^["']|["']$/g, '').trim()
}

export function parseHierarchy(content: string): Hierarchy {
  const text = content.replace(/^\ufeff/, '').replace(/\r\n/g, '\n')
  const { raw } = splitFrontmatter(text)
  const result: Hierarchy = { relations: [] }
  if (!raw) return result
  for (const line of raw.split('\n')) {
    const match = line.match(KEY_LINE)
    if (!match) continue
    const [, key, value] = match
    if (key === 'level') {
      const level = Number(unquote(value))
      if (Number.isInteger(level)) result.level = level
    } else if (key === 'domain') {
      const domain = unquote(value)
      if (domain) result.domain = domain
    } else if (key === 'parent') {
      const [target] = linksIn(value)
      if (target) result.parent = target
    } else {
      for (const target of linksIn(value)) result.relations.push({ name: key, target })
    }
  }
  return result
}

export type ForceParams = { ringSpacing: number; ringStrength: number; angleStrength: number }
export const DEFAULT_FORCES: ForceParams = { ringSpacing: 140, ringStrength: 0.02, angleStrength: 0.015 }
export type LevelNode = { id: string; level?: number; parentId?: string }
type Point = { x: number; y: number }

/** Extra force per node with a level: toward its ring (radius level × spacing around 0,0) and, along the
 * ring, toward its parent's angle. Nodes without a level get no entry. */
export function hierarchyForces(
  nodes: LevelNode[],
  positions: Map<string, Point>,
  params: ForceParams = DEFAULT_FORCES,
): Map<string, Point> {
  const forces = new Map<string, Point>()
  for (const node of nodes) {
    if (node.level === undefined) continue
    const pos = positions.get(node.id)
    if (!pos) continue
    const force = { x: 0, y: 0 }
    const radius = Math.hypot(pos.x, pos.y)
    const target = node.level * params.ringSpacing
    if (radius > 1e-6) {
      const pull = (target - radius) * params.ringStrength
      force.x += (pos.x / radius) * pull
      force.y += (pos.y / radius) * pull
    } else if (target > 0) {
      force.x += target * params.ringStrength
    }
    const parent = node.parentId ? positions.get(node.parentId) : undefined
    if (parent && radius > 1e-6 && Math.hypot(parent.x, parent.y) > 1e-6) {
      const own = Math.atan2(pos.y, pos.x)
      const wanted = Math.atan2(parent.y, parent.x)
      const delta = Math.atan2(Math.sin(wanted - own), Math.cos(wanted - own))
      const push = delta * radius * params.angleStrength
      force.x += -Math.sin(own) * push
      force.y += Math.cos(own) * push
    }
    forces.set(node.id, force)
  }
  return forces
}

/** Start positions: every node with a level on its ring, each subtree inside its parent's angular sector
 * (sector size ∝ subtree size). Roots share the full circle. Cycles are cut. */
export function initialHierarchyPositions(
  nodes: LevelNode[],
  ringSpacing: number = DEFAULT_FORCES.ringSpacing,
): Map<string, Point> {
  const leveled = nodes.filter((n) => n.level !== undefined).sort((a, b) => a.id.localeCompare(b.id))
  const byId = new Map(leveled.map((n) => [n.id, n]))
  const children = new Map<string, string[]>()
  const roots: string[] = []
  for (const node of leveled) {
    if (node.parentId && byId.has(node.parentId)) {
      const list = children.get(node.parentId) ?? []
      list.push(node.id)
      children.set(node.parentId, list)
    } else {
      roots.push(node.id)
    }
  }
  const sizes = new Map<string, number>()
  const sizeOf = (id: string, seen: Set<string>): number => {
    const known = sizes.get(id)
    if (known !== undefined) return known
    if (seen.has(id)) return 1
    seen.add(id)
    const size = 1 + (children.get(id) ?? []).reduce((sum, child) => sum + sizeOf(child, seen), 0)
    sizes.set(id, size)
    return size
  }
  const out = new Map<string, Point>()
  const place = (ids: string[], start: number, span: number) => {
    const fresh = ids.filter((id) => !out.has(id))
    const total = fresh.reduce((sum, id) => sum + sizeOf(id, new Set()), 0)
    let angle = start
    for (const id of fresh) {
      const share = (span * sizeOf(id, new Set())) / total
      const mid = angle + share / 2
      const radius = (byId.get(id)?.level ?? 0) * ringSpacing
      out.set(id, { x: radius * Math.cos(mid), y: radius * Math.sin(mid) })
      place(children.get(id) ?? [], angle, share)
      angle += share
    }
  }
  place(roots, 0, Math.PI * 2)
  for (const node of leveled) {
    if (!out.has(node.id)) place([node.id], 0, Math.PI * 2)
  }
  return out
}

export type EdgeKind = 'parent' | 'relation' | 'link'
export type HierarchyEdge = { source: string; target: string; kind: EdgeKind; relation?: string }
export type NodeInfo = { level?: number; parentId?: string; domain?: string }

const RANK: Record<EdgeKind, number> = { link: 0, relation: 1, parent: 2 }
const VAULT_MAP_VERSION = 1

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Node id of an exact vault-relative note path from the vault map (no wiki-link parsing: names may contain
 * `#`, `...` or a media extension). */
export function vaultMapNodeId(path: string): string {
  return `knowledge/${path}.md`
}

/** Text of a workspace:readFile result; anything unexpected reads as an empty note (VibeMind: vibemind-os 2f0f2974). */
export function graphFileData(result: unknown): string {
  if (typeof result !== 'object' || result === null) return ''
  const data = (result as { data?: unknown }).data
  return typeof data === 'string' ? data : ''
}

/** config/vault-map.json (written by `ehl-schema map`): node id -> hierarchy info. Any problem gives an empty map,
 * so the graph falls back to front matter only. */
export function parseVaultMap(text: string, toNodeId: (wikiPath: string) => string | null): Map<string, NodeInfo> {
  const out = new Map<string, NodeInfo>()
  let doc: unknown
  try {
    doc = JSON.parse(text)
  } catch {
    return out
  }
  if (!isRecord(doc) || doc.version !== VAULT_MAP_VERSION || !isRecord(doc.notes)) return out
  for (const [path, entry] of Object.entries(doc.notes)) {
    if (!isRecord(entry) || typeof entry.level !== 'number' || !Number.isInteger(entry.level)) continue
    const id = toNodeId(path)
    if (!id) continue
    const parentId = typeof entry.parent === 'string' ? toNodeId(entry.parent) ?? undefined : undefined
    out.set(id, {
      level: entry.level,
      domain: typeof entry.domain === 'string' ? entry.domain : undefined,
      parentId,
    })
  }
  return out
}

/** Typed edges (one per unordered pair, strongest kind wins) and hierarchy info per node. Front matter wins;
 * `fallback` (from the vault map) fills in level, domain and parent for notes that have none. */
export function buildHierarchyGraph(
  contents: { path: string; data: string }[],
  nodeSet: Set<string>,
  toNodeId: (wikiTarget: string) => string | null,
  fallback?: Map<string, NodeInfo>,
): { edges: HierarchyEdge[]; info: Map<string, NodeInfo> } {
  const edges = new Map<string, HierarchyEdge>()
  const info = new Map<string, NodeInfo>()
  const addEdge = (source: string, target: string, kind: EdgeKind, relation?: string) => {
    if (target === source || !nodeSet.has(target)) return
    const key = source < target ? `${source}|${target}` : `${target}|${source}`
    const existing = edges.get(key)
    if (existing && RANK[existing.kind] >= RANK[kind]) return
    edges.set(key, relation ? { source, target, kind, relation } : { source, target, kind })
  }
  const add = (source: string, rawTarget: string, kind: EdgeKind, relation?: string) => {
    const target = toNodeId(rawTarget)
    if (target) addEdge(source, target, kind, relation)
  }
  for (const { path, data } of contents) {
    const hierarchy = parseHierarchy(data)
    const mapped = fallback?.get(path)
    const frontParent = hierarchy.parent ? toNodeId(hierarchy.parent) : null
    const mappedParent = !hierarchy.parent && mapped?.parentId && nodeSet.has(mapped.parentId) ? mapped.parentId : undefined
    info.set(path, {
      level: hierarchy.level ?? mapped?.level,
      parentId: frontParent && nodeSet.has(frontParent) ? frontParent : mappedParent,
      domain: hierarchy.domain ?? mapped?.domain,
    })
    if (hierarchy.parent) add(path, hierarchy.parent, 'parent')
    else if (mappedParent) addEdge(path, mappedParent, 'parent')
    for (const relation of hierarchy.relations) add(path, relation.target, 'relation', relation.name)
    for (const match of data.matchAll(LINK)) add(path, match[1], 'link')
  }
  return { edges: Array.from(edges.values()), info }
}

// ---- 3D view (react-force-graph-3d): nodes on one sphere shell per level ----

export type Graph3dNode = { id: string; name: string; color: string; level: number; val: number; x?: number; y?: number; z?: number }
export type Point3d = { x: number; y: number; z: number }
export type Graph3dLink = {
  source: string
  target: string
  kind: EdgeKind
  relation?: string
  width: number
  opacity: number
}
type InNode = { id: string; label: string; color: string; degree: number; level?: number }
type InEdge = { source: string; target: string; kind?: EdgeKind; relation?: string }

const LINK_STYLE: Record<EdgeKind, { width: number; opacity: number }> = {
  parent: { width: 1.2, opacity: 0.6 },
  relation: { width: 0.6, opacity: 0.5 },
  link: { width: 0, opacity: 0.12 },
}

/** Distance between two sphere shells in the 3D view (wider than the 2D rings: shells hold more nodes). */
export const SHELL_SPACING_3D = 300

/** Radius of the sphere shell for a level; level 0 is the centre. */
export function shellRadius(level: number, spacing: number = SHELL_SPACING_3D): number {
  return level * spacing
}

// FNV-1a + murmur3 finaliser: a stable number per id, so a note always starts at the same spot.
function hashId(id: string, salt: number): number {
  let h = 2166136261 ^ salt
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619)
  // murmur3 finaliser: similar ids ("area1", "area2") must land far apart, not side by side.
  h ^= h >>> 16
  h = Math.imul(h, 0x85ebca6b)
  h ^= h >>> 13
  h = Math.imul(h, 0xc2b2ae35)
  h ^= h >>> 16
  return (h >>> 0) / 4294967296
}

/** A fixed direction (unit vector) per id, spread evenly over the sphere. */
function hashDirection(id: string): Point3d {
  const theta = 2 * Math.PI * hashId(id, 1)
  const cosPhi = 2 * hashId(id, 2) - 1
  const sinPhi = Math.sqrt(1 - cosPhi * cosPhi)
  return { x: sinPhi * Math.cos(theta), y: sinPhi * Math.sin(theta), z: cosPhi }
}

/** The parent's direction, tilted by a small fixed angle per id: children fan out around their parent. */
function nearDirection(parent: Point3d, id: string): Point3d {
  // Any vector not parallel to the parent gives an orthonormal pair (u, v) around it.
  const helper = Math.abs(parent.x) < 0.9 ? { x: 1, y: 0, z: 0 } : { x: 0, y: 1, z: 0 }
  const u = normalize(cross(parent, helper))
  const v = cross(parent, u)
  const tilt = 0.6 * hashId(id, 3)
  const turn = 2 * Math.PI * hashId(id, 4)
  const c = tilt * Math.cos(turn)
  const d = tilt * Math.sin(turn)
  return normalize({ x: parent.x + c * u.x + d * v.x, y: parent.y + c * u.y + d * v.y, z: parent.z + c * u.z + d * v.z })
}

function cross(a: Point3d, b: Point3d): Point3d {
  return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }
}

function normalize(p: Point3d): Point3d {
  const len = Math.hypot(p.x, p.y, p.z)
  return len === 0 ? p : { x: p.x / len, y: p.y / len, z: p.z / len }
}

/** Start position without a parent: a fixed point on the node's own sphere shell. */
export function seedPosition3d(id: string, level: number): Point3d {
  const r = shellRadius(level)
  if (r === 0) return { x: 0, y: 0, z: 0 }
  const dir = hashDirection(id)
  return { x: r * dir.x, y: r * dir.y, z: r * dir.z }
}

/**
 * Graph data for the 3D view. Nodes without a level go one shell beyond the deepest level. A node starts at its
 * known position (from an earlier layout); else on its own shell, in its parent's direction when it has a parent, so
 * the parent links don't drag it across the shell in the first frames.
 */
export function toGraph3d(
  nodes: InNode[],
  edges: InEdge[],
  known?: ReadonlyMap<string, Point3d>,
): { nodes: Graph3dNode[]; links: Graph3dLink[] } {
  const deepest = nodes.reduce((max, n) => (n.level !== undefined && n.level > max ? n.level : max), 0)
  const parentOf = new Map<string, string>()
  for (const e of edges) if (e.kind === 'parent' && !parentOf.has(e.source)) parentOf.set(e.source, e.target)
  const levelOf = new Map(nodes.map((n) => [n.id, n.level ?? deepest + 1]))
  const directions = new Map<string, Point3d | null>()
  // Direction of a node from the centre, or null for a node in the centre. Cycles fall back to the hash direction.
  const directionOf = (id: string, seen: Set<string>): Point3d | null => {
    if (directions.has(id)) return directions.get(id) ?? null
    const at = known?.get(id)
    let dir: Point3d | null
    if (at) dir = Math.hypot(at.x, at.y, at.z) === 0 ? null : normalize(at)
    else if (shellRadius(levelOf.get(id) ?? 0) === 0) dir = null
    else {
      const parent = parentOf.get(id)
      seen.add(id)
      const parentDir = parent && levelOf.has(parent) && !seen.has(parent) ? directionOf(parent, seen) : null
      dir = parentDir ? nearDirection(parentDir, id) : hashDirection(id)
    }
    directions.set(id, dir)
    return dir
  }
  return {
    nodes: nodes.map((n) => {
      const level = n.level ?? deepest + 1
      const r = shellRadius(level)
      const dir = directionOf(n.id, new Set())
      const at = known?.get(n.id) ?? (dir && r > 0 ? { x: r * dir.x, y: r * dir.y, z: r * dir.z } : { x: 0, y: 0, z: 0 })
      return { id: n.id, name: n.label, color: n.color, level, val: 1 + Math.min(12, n.degree), x: at.x, y: at.y, z: at.z }
    }),
    links: edges.map((e) => {
      const kind = e.kind ?? 'link'
      return { source: e.source, target: e.target, kind, relation: e.relation, ...LINK_STYLE[kind] }
    }),
  }
}

/**
 * Content fingerprint of the 3D graph input. The app rebuilds its graph arrays on every workspace change, even when
 * no note changed; comparing this instead of array identity spares the view a needless relayout.
 */
export function graph3dSignature(nodes: InNode[], edges: InEdge[]): string {
  const n = nodes.map((x) => `${x.id}\u0001${x.label}\u0001${x.color}\u0001${x.degree}\u0001${x.level ?? ''}`)
  const e = edges.map((x) => `${x.source}\u0001${x.target}\u0001${x.kind ?? ''}\u0001${x.relation ?? ''}`)
  return `${n.join('\u0002')}\u0003${e.join('\u0002')}`
}
