// EHL addition (Flissel/rowboat ehl-schema-graph): minimal typings for d3-force-3d, which ships none.
declare module 'd3-force-3d' {
  export interface RadialForce<N> {
    (alpha: number): void
    initialize?: (nodes: N[]) => void
    strength(strength: number | ((node: N) => number)): RadialForce<N>
    radius(radius: number | ((node: N) => number)): RadialForce<N>
  }
  export function forceRadial<N>(radius: number | ((node: N) => number), x?: number, y?: number, z?: number): RadialForce<N>
}
