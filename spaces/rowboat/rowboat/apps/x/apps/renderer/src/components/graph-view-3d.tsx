// EHL addition (Flissel/rowboat ehl-schema-graph): 3D brain graph. Every note sits on the sphere shell of its
// level around the company note in the centre; parent edges hold subtrees together, relations are drawn in
// orange, body links stay faint. Spec: Flissel/ehl-munich-2026 docs/superpowers/specs/2026-10-12-brain-graph-hierarchy-design.md
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D, { type ForceGraphMethods, type LinkObject, type NodeObject } from 'react-force-graph-3d'
import { forceRadial } from 'd3-force-3d'
import { SHELL_SPACING_3D, shellRadius, toGraph3d, type Graph3dLink, type Graph3dNode } from '@/lib/graph-hierarchy'
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

function GraphView3DInner({ nodes, edges, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<ForceGraphMethods<Node3d, Link3d>>(undefined)
  const fittedRef = useRef(false)
  const [size, setSize] = useState({ width: 1, height: 1 })
  const data = useMemo(() => toGraph3d(nodes, edges), [nodes, edges])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      setSize((old) => (old.width === rect.width && old.height === rect.height ? old : { width: rect.width, height: rect.height }))
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    graph.d3Force('shells', forceRadial<Graph3dNode>((node) => shellRadius(node.level)).strength(0.9))
    graph.d3Force('charge')?.strength?.(CHARGE)
    graph.d3Force('link')?.distance?.(linkDistance)
    fittedRef.current = false
    graph.d3ReheatSimulation()
  }, [data])

  const handleNodeClick = useCallback((node: Node3d) => {
    if (typeof node.id === 'string') onSelectNode?.(node.id)
  }, [onSelectNode])

  const handleEngineStop = useCallback(() => {
    if (fittedRef.current) return
    fittedRef.current = true
    graphRef.current?.zoomToFit(600, 40)
  }, [])

  return (
    <div ref={containerRef} className="absolute inset-0">
      <ForceGraph3D<Graph3dNode, Graph3dLink>
        ref={graphRef}
        graphData={data}
        width={size.width}
        height={size.height}
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
    </div>
  )
}

export const GraphView3D = memo(GraphView3DInner)
