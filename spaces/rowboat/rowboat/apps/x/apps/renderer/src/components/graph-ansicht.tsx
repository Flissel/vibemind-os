// VibeMind: Wissensgraph mit Umschalter Notizen | Konzepte. Zwei getrennte Graphen; die Wahl gilt je Fenster.
import { useState } from 'react'
import { GraphView, type GraphEdge, type GraphNode } from '@/components/graph-view'
import { KONZEPTE_LEER, leseGraphAnsicht, merkeGraphAnsicht, type GraphAnsicht } from '@/lib/konzept-graph-ansicht'
import { cn } from '@/lib/utils'

type GraphDaten = { nodes: GraphNode[]; edges: GraphEdge[] }

const ANSICHTEN: { wert: GraphAnsicht; label: string }[] = [
  { wert: 'notizen', label: 'Notizen' },
  { wert: 'konzepte', label: 'Konzepte' },
]

export function GraphMitAnsicht({
  notizen,
  konzepte,
  error,
  isLoading = false,
  onSelectNode,
}: {
  notizen: GraphDaten
  konzepte: GraphDaten
  error: string | null
  /** While the graph is built: the Konzepte graph shows no empty-state text (the Notizen graph is unchanged). */
  isLoading?: boolean
  onSelectNode: (id: string) => void
}) {
  const [ansicht, setAnsicht] = useState<GraphAnsicht>(leseGraphAnsicht)
  const daten = ansicht === 'konzepte' ? konzepte : notizen

  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex shrink-0 items-center px-3 pt-3">
        <div role="group" aria-label="Graph" className="inline-flex overflow-hidden rounded-lg border border-border bg-background">
          {ANSICHTEN.map(({ wert, label }) => (
            <button
              key={wert}
              type="button"
              aria-pressed={ansicht === wert}
              onClick={() => {
                setAnsicht(wert)
                merkeGraphAnsicht(wert)
              }}
              className={cn(
                'px-3 py-1 text-sm transition-colors',
                ansicht === wert ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {/* key: Suche, Legenden-Auswahl und Layout beginnen je Graph neu */}
        <GraphView
          key={ansicht}
          nodes={daten.nodes}
          edges={daten.edges}
          isLoading={false}
          error={error}
          onSelectNode={onSelectNode}
          emptyText={ansicht === 'konzepte' ? (isLoading ? '' : KONZEPTE_LEER) : undefined}
          legendTitle={ansicht === 'konzepte' ? 'Arten' : undefined}
        />
      </div>
    </div>
  )
}
