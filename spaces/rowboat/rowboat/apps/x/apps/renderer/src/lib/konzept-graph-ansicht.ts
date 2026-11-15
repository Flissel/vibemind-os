// Zwei Graphen im Wissensgraphen (Notizen | Konzepte): gemerkte Wahl, Texte und die Umsetzung des Konzepte-Graphen
// in die Form, die die vorhandene 2D-Ansicht (components/graph-view) zeichnet.
import type { GraphEdge, GraphNode } from '@/components/graph-view'
import type { KonzeptKante, KonzeptKnoten } from './graph-html'

export type GraphAnsicht = 'notizen' | 'konzepte'

/** Gemerkte Wahl je Fenster (localStorage). */
export const GRAPH_ANSICHT_SCHLUESSEL = 'rowboat.graph.ansicht'

export const KONZEPTE_LEER = 'Noch keine Konzepte. Sag im Chat „Bau mir ein Konzept für …" oder nutze „Neues Konzept".'

/** Legende des Konzepte-Graphen je Knotenart. */
export const KONZEPT_ART_LEGENDE: Record<KonzeptKnoten['art'], string> = {
  konzept: 'Konzept',
  videokonzept: 'Videokonzept',
  html: 'HTML-Seite',
  notiz: 'Notiz (Bezug)',
}

/** Legende eines Konzepts, das im Notizen-Graphen nur als Bezug erscheint. */
export const KONZEPT_BEZUG_LEGENDE = 'Konzept (Bezug)'

/** Gedämpfte Farbe der Nebenknoten (Notiz im Konzepte-Graphen, Konzept im Notizen-Graphen). */
export const BEZUG_FARBE = { color: 'hsl(220 6% 56%)', stroke: 'hsl(220 6% 40%)' }

const ART_FARBE: Record<KonzeptKnoten['art'], { color: string; stroke: string }> = {
  konzept: { color: 'hsl(262 62% 64%)', stroke: 'hsl(262 66% 48%)' },
  videokonzept: { color: 'hsl(24 82% 60%)', stroke: 'hsl(24 86% 44%)' },
  html: { color: 'hsl(196 52% 56%)', stroke: 'hsl(196 58% 40%)' },
  notiz: BEZUG_FARBE,
}

export function leseGraphAnsicht(): GraphAnsicht {
  try {
    return globalThis.localStorage?.getItem(GRAPH_ANSICHT_SCHLUESSEL) === 'konzepte' ? 'konzepte' : 'notizen'
  } catch {
    return 'notizen'
  }
}

export function merkeGraphAnsicht(ansicht: GraphAnsicht): void {
  try {
    globalThis.localStorage?.setItem(GRAPH_ANSICHT_SCHLUESSEL, ansicht)
  } catch {
    /* Speicher gesperrt: Wahl gilt nur bis zum Neuladen */
  }
}

const hauptRadius = (degree: number) => 6 + Math.min(18, degree * 2)
/** Höchstens 5: kleiner als jeder Hauptknoten (mindestens 6), gleich wie viele Kanten. */
const bezugRadius = (degree: number) => 3 + Math.min(2, degree)

/** Ein Konzept als Nebenknoten im Notizen-Graphen: klein, gedämpft, Legende „Konzept (Bezug)". */
export function konzeptBezugKnoten(id: string, label: string, degree: number): GraphNode {
  return { id, label, degree, radius: bezugRadius(degree), group: KONZEPT_BEZUG_LEGENDE, ...BEZUG_FARBE }
}

/**
 * Der Konzepte-Graph in der Form der 2D-Ansicht. Gruppe = Legende der Art, Notizen klein und gedämpft. Kein `level`:
 * die Ansicht bietet dann keinen 3D-Schalter. Kanten ohne `kind` (durchgezogen); die Richtung steht in source/target.
 */
export function konzeptGraphAnsicht(graph: { nodes: KonzeptKnoten[]; edges: KonzeptKante[] }): {
  nodes: GraphNode[]
  edges: GraphEdge[]
} {
  const degree = new Map<string, number>()
  for (const e of graph.edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1)
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1)
  }
  return {
    nodes: graph.nodes.map((n) => {
      const d = degree.get(n.id) ?? 0
      return {
        id: n.id,
        label: n.label,
        degree: d,
        radius: n.art === 'notiz' ? bezugRadius(d) : hauptRadius(d),
        group: KONZEPT_ART_LEGENDE[n.art],
        ...ART_FARBE[n.art],
      }
    }),
    edges: graph.edges.map((e) => ({ source: e.source, target: e.target })),
  }
}
