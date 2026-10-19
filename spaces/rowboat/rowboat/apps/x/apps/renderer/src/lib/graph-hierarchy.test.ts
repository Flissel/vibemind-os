// Modified for VibeMind (Flissel/rowboat vibemind): tests for graphFileData.
import { describe, expect, it } from 'vitest'
import { DEFAULT_FORCES, buildHierarchyGraph, graphFileData, hierarchyForces, initialHierarchyPositions, parseHierarchy, parseVaultMap, SHELL_SPACING_3D, graph3dSignature, seedPosition3d, shellRadius, toGraph3d, vaultMapNodeId } from './graph-hierarchy'

type Graph3dNodeLike = { x?: number; y?: number; z?: number }

const note = (front: string, body = '# Title\n') => `---\n${front}\n---\n${body}`

describe('parseHierarchy', () => {
  it('reads level, parent and domain', () => {
    const h = parseHierarchy(note('id: deal-x\nlevel: 3\ndomain: sales\nparent: "[[Areas/Pipeline]]"'))
    expect(h).toEqual({ level: 3, parent: 'Areas/Pipeline', domain: 'sales', relations: [] })
  })

  it('accepts an unquoted parent and a quoted domain', () => {
    const h = parseHierarchy(note('parent: [[Areas/Pipeline]]\ndomain: "sales"'))
    expect(h.parent).toBe('Areas/Pipeline')
    expect(h.domain).toBe('sales')
  })

  it('strips alias and heading', () => {
    const h = parseHierarchy(note('parent: "[[Brands/BMW|BMW]]"\nabout: ["[[Models/5er G60#Sales]]"]'))
    expect(h.parent).toBe('Brands/BMW')
    expect(h.relations).toEqual([{ name: 'about', target: 'Models/5er G60' }])
  })

  it('turns link lists into relations and ignores other keys', () => {
    const h = parseHierarchy(note(
      'customer: ["[[Organizations/Acme]]", "[[Organizations/Beta]]"]\nowner: ["[[People/Felix Baumann]]"]\n' +
      'status: active\nsource: sales-claw\nwhen: 2026-10-20'))
    expect(h.relations).toEqual([
      { name: 'customer', target: 'Organizations/Acme' },
      { name: 'customer', target: 'Organizations/Beta' },
      { name: 'owner', target: 'People/Felix Baumann' },
    ])
  })

  it('relation to an attachment', () => {
    const h = parseHierarchy(note('evidence: ["[[Data/BMW Dataset.xlsx|data]]"]'))
    expect(h.relations).toEqual([{ name: 'evidence', target: 'Data/BMW Dataset.xlsx' }])
  })

  it('accepts CRLF and a BOM', () => {
    const h = parseHierarchy('\ufeff---\r\nlevel: 2\r\nparent: "[[Domains/Market & sales]]"\r\n---\r\n# A\r\n')
    expect(h.level).toBe(2)
    expect(h.parent).toBe('Domains/Market & sales')
  })

  it('a note without front matter has no hierarchy', () => {
    expect(parseHierarchy('# Just a note with [[Topics/Comfort]]\n')).toEqual({ relations: [] })
  })

  it('ignores a non-numeric level and links in the body', () => {
    const h = parseHierarchy(note('level: three', 'See [[Topics/Comfort]].\n'))
    expect(h).toEqual({ relations: [] })
  })
})

describe('hierarchyForces', () => {
  const ring = DEFAULT_FORCES.ringSpacing

  it('pulls a node outside its ring inward and a node inside outward', () => {
    const nodes = [{ id: 'out', level: 1 }, { id: 'in', level: 2 }]
    const positions = new Map([['out', { x: ring * 2, y: 0 }], ['in', { x: ring, y: 0 }]])
    const forces = hierarchyForces(nodes, positions)
    expect(forces.get('out')!.x).toBeLessThan(0)
    expect(forces.get('in')!.x).toBeGreaterThan(0)
  })

  it('pulls level 0 to the centre', () => {
    const forces = hierarchyForces([{ id: 'c', level: 0 }], new Map([['c', { x: 30, y: -40 }]]))
    expect(forces.get('c')!.x).toBeLessThan(0)
    expect(forces.get('c')!.y).toBeGreaterThan(0)
  })

  it('pulls a child tangentially toward its parent angle', () => {
    const nodes = [{ id: 'p', level: 1 }, { id: 'c', level: 2, parentId: 'p' }]
    const positions = new Map([['p', { x: 0, y: ring }], ['c', { x: ring * 2, y: 0 }]])
    const force = hierarchyForces(nodes, positions).get('c')!
    expect(force.y).toBeGreaterThan(0)
  })

  it('gives no force to a node without a level', () => {
    const forces = hierarchyForces([{ id: 'x' }], new Map([['x', { x: 10, y: 10 }]]))
    expect(forces.has('x')).toBe(false)
  })
})

describe('initialHierarchyPositions', () => {
  const ring = DEFAULT_FORCES.ringSpacing

  it('puts every node on its ring and children in their parent sector', () => {
    const nodes = [
      { id: 'company', level: 0 },
      { id: 'd1', level: 1, parentId: 'company' },
      { id: 'd2', level: 1, parentId: 'company' },
      { id: 'a1', level: 2, parentId: 'd1' },
      { id: 'a2', level: 2, parentId: 'd2' },
    ]
    const pos = initialHierarchyPositions(nodes)
    expect(Math.hypot(pos.get('company')!.x, pos.get('company')!.y)).toBe(0)
    for (const id of ['d1', 'd2']) expect(Math.hypot(pos.get(id)!.x, pos.get(id)!.y)).toBeCloseTo(ring)
    for (const id of ['a1', 'a2']) expect(Math.hypot(pos.get(id)!.x, pos.get(id)!.y)).toBeCloseTo(ring * 2)
    const angle = (id: string) => Math.atan2(pos.get(id)!.y, pos.get(id)!.x)
    expect(angle('a1')).toBeCloseTo(angle('d1'))
    expect(angle('a2')).toBeCloseTo(angle('d2'))
  })

  it('skips nodes without a level', () => {
    expect(initialHierarchyPositions([{ id: 'x' }]).size).toBe(0)
  })

  it('initial positions survive a parent cycle', () => {
    const nodes = [{ id: 'a', level: 3, parentId: 'b' }, { id: 'b', level: 3, parentId: 'a' }]
    expect(initialHierarchyPositions(nodes).size).toBe(2)
  })
})

describe('buildHierarchyGraph', () => {
  const toNodeId = (wiki: string) => `knowledge/${wiki.split('|')[0].split('#')[0].trim()}.md`
  const node = (path: string, front: string, body = '') => ({
    path: `knowledge/${path}.md`,
    data: front ? `---\n${front}\n---\n${body}` : body,
  })

  it('classifies parent, relation and link edges, strongest kind wins', () => {
    const contents = [
      node('Areas/Pipeline', 'level: 2\ndomain: sales'),
      node('Organizations/Acme', 'level: 3\ndomain: register'),
      node('Deals/X', 'level: 3\ndomain: sales\nparent: "[[Areas/Pipeline]]"\ncustomer: ["[[Organizations/Acme]]"]',
           'See [[Areas/Pipeline]] and [[Organizations/Acme]].\n'),
    ]
    const nodeSet = new Set(contents.map((c) => c.path))
    const { edges, info } = buildHierarchyGraph(contents, nodeSet, toNodeId)
    expect(edges).toEqual([
      { source: 'knowledge/Deals/X.md', target: 'knowledge/Areas/Pipeline.md', kind: 'parent' },
      { source: 'knowledge/Deals/X.md', target: 'knowledge/Organizations/Acme.md', kind: 'relation', relation: 'customer' },
    ])
    expect(info.get('knowledge/Deals/X.md')).toEqual({ level: 3, parentId: 'knowledge/Areas/Pipeline.md', domain: 'sales' })
  })

  it('plain links stay kind link', () => {
    const contents = [node('Topics/A', '', '[[Topics/B]]\n'), node('Topics/B', '', '')]
    const { edges, info } = buildHierarchyGraph(contents, new Set(contents.map((c) => c.path)), toNodeId)
    expect(edges).toEqual([{ source: 'knowledge/Topics/A.md', target: 'knowledge/Topics/B.md', kind: 'link' }])
    expect(info.get('knowledge/Topics/A.md')).toEqual({ level: undefined, parentId: undefined, domain: undefined })
  })

  it('parentId only when the parent is a node', () => {
    const contents = [node('Meetings/M', 'level: 4\nparent: "[[Areas/Meetings]]"')]
    const { edges, info } = buildHierarchyGraph(contents, new Set(contents.map((c) => c.path)), toNodeId)
    expect(edges).toEqual([])
    expect(info.get('knowledge/Meetings/M.md')?.parentId).toBeUndefined()
  })

  it('ignores self links and unresolvable targets', () => {
    const contents = [node('Topics/A', 'about: ["[[Topics/A]]"]', '[[Nowhere/X]]\n')]
    const toNull = (wiki: string) => (wiki.startsWith('Nowhere') ? null : toNodeId(wiki))
    expect(buildHierarchyGraph(contents, new Set(contents.map((c) => c.path)), toNull).edges).toEqual([])
  })
})

describe('toGraph3d', () => {
  const nodes = [
    { id: 'c', label: 'Company', color: 'hsl(1 1% 1%)', degree: 4, level: 0 },
    { id: 'd', label: 'Sales', color: 'hsl(2 2% 2%)', degree: 2, level: 1, parentId: 'c' },
    { id: 'x', label: 'Loose note', color: 'hsl(3 3% 3%)', degree: 0 },
  ]
  const edges = [
    { source: 'd', target: 'c', kind: 'parent' as const },
    { source: 'd', target: 'x', kind: 'relation' as const, relation: 'about' },
    { source: 'x', target: 'c' },
  ]

  it('keeps ids, labels, colours and levels; nodes without a level go to the outer shell', () => {
    const data = toGraph3d(nodes, edges)
    expect(data.nodes.map((n) => [n.id, n.name, n.color, n.level])).toEqual([
      ['c', 'Company', 'hsl(1 1% 1%)', 0],
      ['d', 'Sales', 'hsl(2 2% 2%)', 1],
      ['x', 'Loose note', 'hsl(3 3% 3%)', 2],
    ])
  })

  it('styles links by kind', () => {
    const links = toGraph3d(nodes, edges).links
    expect(links.map((l) => [l.kind, l.width, l.relation])).toEqual([
      ['parent', 1.2, undefined],
      ['relation', 0.6, 'about'],
      ['link', 0, undefined],
    ])
    expect(links[2].opacity).toBeLessThan(links[0].opacity)
  })

  it('starts every node on its own shell, the same spot for the same id', () => {
    const data = toGraph3d(nodes, edges)
    for (const n of data.nodes) {
      expect(Math.hypot(n.x ?? NaN, n.y ?? NaN, n.z ?? NaN)).toBeCloseTo(shellRadius(n.level), 6)
    }
    expect(toGraph3d(nodes, edges).nodes[1]).toEqual(data.nodes[1])
    expect(seedPosition3d('a', 1)).not.toEqual(seedPosition3d('b', 1))
  })

  it('starts a child in the direction of its parent (no swirl across the shell)', () => {
    const tree = [
      { id: 'root', label: 'R', color: 'c', degree: 1, level: 0 },
      { id: 'hub', label: 'H', color: 'c', degree: 2, level: 1 },
      ...Array.from({ length: 20 }, (_, i) => ({ id: `kid${i}`, label: `K${i}`, color: 'c', degree: 1, level: 2 })),
    ]
    const parents = [
      { source: 'hub', target: 'root', kind: 'parent' as const },
      ...tree.slice(2).map((k) => ({ source: k.id, target: 'hub', kind: 'parent' as const })),
    ]
    const byId = new Map(toGraph3d(tree, parents).nodes.map((n) => [n.id, n]))
    const hub = byId.get('hub')!
    const angle = (a: Graph3dNodeLike, b: Graph3dNodeLike) =>
      Math.acos((a.x! * b.x! + a.y! * b.y! + a.z! * b.z!) / (Math.hypot(a.x!, a.y!, a.z!) * Math.hypot(b.x!, b.y!, b.z!)))
    for (let i = 0; i < 20; i++) {
      const kid = byId.get(`kid${i}`)!
      expect(Math.hypot(kid.x!, kid.y!, kid.z!)).toBeCloseTo(shellRadius(2), 6)
      expect(angle(kid, hub)).toBeLessThan(0.8)
    }
  })

  it('spreads the children of the centre node over the whole sphere', () => {
    const tree = [
      { id: 'root', label: 'R', color: 'c', degree: 30, level: 0 },
      ...Array.from({ length: 30 }, (_, i) => ({ id: `area${i}`, label: `A${i}`, color: 'c', degree: 1, level: 1 })),
    ]
    const parents = tree.slice(1).map((k) => ({ source: k.id, target: 'root', kind: 'parent' as const }))
    const kids = toGraph3d(tree, parents).nodes.slice(1)
    const mean = kids.reduce((m, n) => ({ x: m.x + n.x! / kids.length, y: m.y + n.y! / kids.length, z: m.z + n.z! / kids.length }), { x: 0, y: 0, z: 0 })
    expect(Math.hypot(mean.x, mean.y, mean.z)).toBeLessThan(0.5 * shellRadius(1))
  })

  it('keeps a known position instead of the seed', () => {
    const known = new Map([['d', { x: 1, y: 2, z: 3 }]])
    const d = toGraph3d(nodes, edges, known).nodes.find((n) => n.id === 'd')
    expect([d?.x, d?.y, d?.z]).toEqual([1, 2, 3])
  })

  it('signature ignores new array identities but sees real changes', () => {
    const sig = graph3dSignature(nodes, edges)
    expect(graph3dSignature(nodes.map((n) => ({ ...n })), edges.map((e) => ({ ...e })))).toBe(sig)
    expect(graph3dSignature(nodes, edges.slice(1))).not.toBe(sig)
    expect(graph3dSignature([...nodes, { id: 'y', label: 'New', color: 'red', degree: 0 }], edges)).not.toBe(sig)
  })

  it('shell radius grows with the level and level 0 sits in the centre', () => {
    expect(shellRadius(0)).toBe(0)
    expect(shellRadius(2)).toBe(2 * SHELL_SPACING_3D)
    expect(SHELL_SPACING_3D).toBeGreaterThanOrEqual(2 * DEFAULT_FORCES.ringSpacing)
  })
})

describe('parseVaultMap', () => {
  const toNodeId = (wiki: string) => `knowledge/${wiki}.md`
  const doc = (notes: unknown, version: unknown = 1) => JSON.stringify({ version, company: 'X', notes })

  it('maps note paths to node ids with level, domain and parent', () => {
    const map = parseVaultMap(doc({
      'Agents/a (1)': { type: 'Agents', level: 3, domain: 'ai', parent: 'Areas/Agents' },
      Company: { type: 'Company', level: 0, domain: 'company', parent: null },
    }), toNodeId)
    expect(map.get('knowledge/Agents/a (1).md')).toEqual({ level: 3, domain: 'ai', parentId: 'knowledge/Areas/Agents.md' })
    expect(map.get('knowledge/Company.md')).toEqual({ level: 0, domain: 'company', parentId: undefined })
  })

  it('returns an empty map for broken JSON, a wrong version or a wrong shape', () => {
    expect(parseVaultMap('{nope', toNodeId).size).toBe(0)
    expect(parseVaultMap(doc({}, 2), toNodeId).size).toBe(0)
    expect(parseVaultMap(JSON.stringify([1, 2]), toNodeId).size).toBe(0)
    expect(parseVaultMap(doc({ 'A/b': 'x' }), toNodeId).size).toBe(0)
  })

  it('skips entries whose level is not an integer', () => {
    const map = parseVaultMap(doc({ 'A/b': { level: 'three', domain: 'ai', parent: null } }), toNodeId)
    expect(map.size).toBe(0)
  })
})

describe('buildHierarchyGraph with a vault map', () => {
  const toNodeId = (wiki: string) => `knowledge/${wiki.split('|')[0].split('#')[0].trim()}.md`
  const node = (path: string, data: string) => ({ path: `knowledge/${path}.md`, data })

  it('uses the map for notes without hierarchy front matter and adds the parent edge', () => {
    const contents = [
      node('Areas/Agents', '---\nlevel: 2\ndomain: ai\n---\n'),
      node('Agents/a (1)', '---\ntyp: agent\nfakten:\n- schluessel: zustand\n---\n# a\n'),
    ]
    const nodeSet = new Set(contents.map((c) => c.path))
    const fallback = new Map([['knowledge/Agents/a (1).md', { level: 3, domain: 'ai', parentId: 'knowledge/Areas/Agents.md' }]])
    const { edges, info } = buildHierarchyGraph(contents, nodeSet, toNodeId, fallback)
    expect(info.get('knowledge/Agents/a (1).md')).toEqual({ level: 3, parentId: 'knowledge/Areas/Agents.md', domain: 'ai' })
    expect(edges).toContainEqual({ source: 'knowledge/Agents/a (1).md', target: 'knowledge/Areas/Agents.md', kind: 'parent' })
  })

  it('lets front matter win over the map', () => {
    const contents = [
      node('Areas/Pipeline', '---\nlevel: 2\ndomain: sales\n---\n'),
      node('Areas/Agents', '---\nlevel: 2\ndomain: ai\n---\n'),
      node('Deals/X', '---\nlevel: 3\ndomain: sales\nparent: "[[Areas/Pipeline]]"\n---\n'),
    ]
    const nodeSet = new Set(contents.map((c) => c.path))
    const fallback = new Map([['knowledge/Deals/X.md', { level: 4, domain: 'ai', parentId: 'knowledge/Areas/Agents.md' }]])
    const { edges, info } = buildHierarchyGraph(contents, nodeSet, toNodeId, fallback)
    expect(info.get('knowledge/Deals/X.md')).toEqual({ level: 3, parentId: 'knowledge/Areas/Pipeline.md', domain: 'sales' })
    expect(edges.filter((e) => e.kind === 'parent')).toEqual([
      { source: 'knowledge/Deals/X.md', target: 'knowledge/Areas/Pipeline.md', kind: 'parent' },
    ])
  })

  it('ignores a map parent that is not a node', () => {
    const contents = [node('Agents/a', '# a\n')]
    const nodeSet = new Set(contents.map((c) => c.path))
    const fallback = new Map([['knowledge/Agents/a.md', { level: 3, domain: 'ai', parentId: 'knowledge/Areas/Agents.md' }]])
    const { edges, info } = buildHierarchyGraph(contents, nodeSet, toNodeId, fallback)
    expect(info.get('knowledge/Agents/a.md')).toEqual({ level: 3, parentId: undefined, domain: 'ai' })
    expect(edges).toEqual([])
  })
})

describe('vaultMapNodeId', () => {
  it('turns exact vault paths into node ids, whatever characters the name has', () => {
    expect(vaultMapNodeId('Notes/C# tips')).toBe('knowledge/Notes/C# tips.md')
    expect(vaultMapNodeId('Notes/Wait... what')).toBe('knowledge/Notes/Wait... what.md')
    expect(vaultMapNodeId('Videos/demo.mp4')).toBe('knowledge/Videos/demo.mp4.md')
  })

  it('keeps such names placed when parsing the map', () => {
    const map = parseVaultMap(JSON.stringify({ version: 1, notes: {
      'Notes/C# tips': { level: 3, domain: 'knowledge', parent: 'Areas/Topics' },
      'Videos/demo.mp4': { level: 4, domain: 'marketing', parent: 'Areas/Content' },
    } }), vaultMapNodeId)
    expect(map.get('knowledge/Notes/C# tips.md')?.parentId).toBe('knowledge/Areas/Topics.md')
    expect(map.get('knowledge/Videos/demo.mp4.md')?.level).toBe(4)
  })
})

describe('graphFileData', () => {
  it('returns the text of a read result and an empty string for anything else', () => {
    expect(graphFileData({ data: '# a' })).toBe('# a')
    expect(graphFileData({ data: 42 })).toBe('')
    expect(graphFileData(null)).toBe('')
    expect(graphFileData(undefined)).toBe('')
  })
})
