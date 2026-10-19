// EHL addition (Flissel/rowboat ehl-schema-graph): 3D brain graph. Every note sits on the sphere shell of its
// level around the company note in the centre; parent edges hold subtrees together, relations are drawn in
// orange, body links stay faint. Spec: Flissel/ehl-munich-2026 docs/superpowers/specs/2026-10-12-brain-graph-hierarchy-design.md
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D, { type ForceGraphMethods, type LinkObject, type NodeObject } from 'react-force-graph-3d'
import { forceRadial } from 'd3-force-3d'
import { SHELL_SPACING_3D, graph3dSignature, shellRadius, toGraph3d, type Graph3dLink, type Graph3dNode, type Point3d } from '@/lib/graph-hierarchy'
import { useSectionVisible } from '@/lib/section-visibility'
import type { GraphEdge, GraphNode } from './graph-view'

type Props = {
  nodes: GraphNode[]
  edges: GraphEdge[]
  onSelectNode?: (id: string) => void
}

type Node3d = NodeObject<Graph3dNode>
type Link3d = LinkObject<Graph3dNode, Graph3dLink>

const LINK_RGB: Record<Graph3dLink['kind'], string> = {
  parent: '200, 200, 210',
  relation: '255, 170, 60',
  link: '150, 150, 150',
}
const CHARGE = -90
const LINK_DISTANCE: Record<Graph3dLink['kind'], number> = {
  parent: SHELL_SPACING_3D * 0.8,
  relation: 160,
  link: 120,
}

// Stable accessors: new functions on every render make the library re-apply them and the graph jitter.
const nodeLabel = (node: Node3d) => node.name
const nodeColor = (node: Node3d) => node.color
const nodeVal = (node: Node3d) => node.val
const linkColor = (link: Link3d) => `rgba(${LINK_RGB[link.kind]}, ${link.opacity})`
const linkWidth = (link: Link3d) => link.width
const linkLabel = (link: Link3d) => link.relation ?? ''
const linkDistance = (link: Link3d) => LINK_DISTANCE[link.kind]

// Last known layout, by node id. Module scope on purpose: it outlives a remount (see below), so coming back to the
// graph or rebuilding it after a workspace change continues from where the nodes were instead of a fresh layout.
const knownPositions = new Map<string, Point3d>()

// Nodes of the scene on screen; their live (mid-layout) positions seed the next data when the content changes.
let shownNodes: Node3d[] = []

function rememberPositions(nodes: Node3d[]) {
  for (const n of nodes) {
    if (typeof n.id === 'string' && Number.isFinite(n.x) && Number.isFinite(n.y) && Number.isFinite(n.z)) {
      knownPositions.set(n.id, { x: n.x as number, y: n.y as number, z: n.z as number })
    }
  }
}

function GraphView3DInner({ nodes, edges, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ width: number; height: number } | null>(null)
  // The app rebuilds its graph arrays on every workspace change, even when no note changed. Holding on to the input
  // until its content changes spares the view a needless relayout (and the camera a jump).
  const signature = graph3dSignature(nodes, edges)
  const [input, setInput] = useState({ signature, nodes, edges })
  if (input.signature !== signature) setInput({ signature, nodes, edges })
  const data = useMemo(() => {
    rememberPositions(shownNodes)
    return toGraph3d(input.nodes, input.edges, knownPositions)
  }, [input])
  // A hidden keep-alive section runs its effect cleanups, and that destroys the 3D scene for good: three-render-objects
  // re-inits it invisible and without the graph (19.10.: graph gone after opening a note and going back). So the
  // scene is unmounted while hidden and built fresh when shown; the nodes keep their positions across that.
  const visible = useSectionVisible()

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      setSize((old) => (old && old.width === rect.width && old.height === rect.height ? old : { width: rect.width, height: rect.height }))
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={containerRef} className="absolute inset-0">
      {visible && size && size.width > 0 && size.height > 0 && (
        <Scene data={data} width={size.width} height={size.height} onSelectNode={onSelectNode} />
      )}
    </div>
  )
}

type SceneProps = {
  data: ReturnType<typeof toGraph3d>
  width: number
  height: number
  onSelectNode?: (id: string) => void
}

function Scene({ data, width, height, onSelectNode }: SceneProps) {
  const graphRef = useRef<ForceGraphMethods<Node3d, Link3d>>(undefined)
  const fittedRef = useRef(false)

  useEffect(() => () => rememberPositions(data.nodes as Node3d[]), [data])

  // Release the WebGL context on unmount: the library's destructor only disposes the renderer, so every hide/show
  // would leave a live context behind (Chromium evicts at ~16). Layout cleanup: refs are already detached in a
  // passive cleanup when the section hides.
  useLayoutEffect(() => {
    const renderer = graphRef.current?.renderer()
    return () => renderer?.forceContextLoss()
  }, [])

  // Layout effect: the forces must be in place before the library's first (deferred) layout pass runs.
  useLayoutEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    graph.d3Force('shells', forceRadial<Graph3dNode>((node) => shellRadius(node.level)).strength(0.9))
    graph.d3Force('charge')?.strength?.(CHARGE)
    graph.d3Force('link')?.distance?.(linkDistance)
    graph.d3ReheatSimulation()
    shownNodes = data.nodes as Node3d[]
    // Frame the graph once, from the start positions (nodes start on their shells or where they were, so the layout
    // barely grows). Not via zoomToFit: on the first ticks the meshes are still at the origin and it frames nothing.
    if (!fittedRef.current) {
      fittedRef.current = true
      const radius = Math.max(SHELL_SPACING_3D, ...data.nodes.map((n) => Math.hypot(n.x ?? 0, n.y ?? 0, n.z ?? 0)))
      const fov = (graph.camera() as { fov?: number }).fov ?? 50
      const distance = (radius / Math.tan((fov / 2) * (Math.PI / 180))) * 1.15
      graph.cameraPosition({ x: 0, y: 0, z: distance }, { x: 0, y: 0, z: 0 }, 0)
    }
  }, [data])

  const handleNodeClick = useCallback((node: Node3d) => {
    if (typeof node.id === 'string') onSelectNode?.(node.id)
  }, [onSelectNode])

  const handleEngineStop = useCallback(() => rememberPositions(data.nodes as Node3d[]), [data])

  return (
    <ForceGraph3D<Graph3dNode, Graph3dLink>
      ref={graphRef}
      graphData={data}
      width={width}
      height={height}
      backgroundColor="rgba(0,0,0,0)"
      nodeLabel={nodeLabel}
      nodeColor={nodeColor}
      nodeVal={nodeVal}
      nodeOpacity={0.95}
      linkColor={linkColor}
      linkWidth={linkWidth}
      linkLabel={linkLabel}
      onNodeClick={handleNodeClick}
      cooldownTicks={200}
      onEngineStop={handleEngineStop}
    />
  )
}

export const GraphView3D = memo(GraphView3DInner)
