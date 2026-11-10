import { useMemo, useState } from 'react'
import type { VerfuegbareVorlageDto as VerfuegbareVorlage } from '@x/shared/dist/integrations.js'

export const KATEGORIE_ANZEIGE: Record<string, string> = {
  'Dev Tools': 'Entwicklung',
  Productivity: 'Produktivität',
  Communication: 'Kommunikation',
  Data: 'Daten',
  Cloud: 'Cloud',
  'AI & Search': 'KI & Suche',
  Sales: 'Vertrieb',
  Marketing: 'Marketing',
  Finance: 'Finanzen',
  'Design & Media': 'Design & Medien',
}
export const UNGEPRUEFT_HINWEIS = 'Noch von niemandem eingerichtet. Funktioniert vermutlich, alle Werkzeuge brauchen eine Freigabe.'

const anzeige = (k: string) => KATEGORIE_ANZEIGE[k] ?? k
const vergleich = (a: string, b: string) => a.localeCompare(b, 'de')

export function UngeprueftPille() {
  return <span title={UNGEPRUEFT_HINWEIS} className="rounded-full bg-zinc-500/15 px-2 py-0.5 text-xs text-muted-foreground">ungeprüft</span>
}

export function VerfuegbarListe(props: {
  vorlagen: VerfuegbareVorlage[]; laufend: Set<string>; ausgegraut: boolean; onHinzufuegen: (id: string) => void
}) {
  const { vorlagen, laufend, ausgegraut } = props
  const [suche, setSuche] = useState('')
  const gruppen = useMemo(() => {
    const q = suche.trim().toLowerCase()
    const treffer = vorlagen.filter((v) => !q || v.name.toLowerCase().includes(q) || v.stichworte.some((s) => s.toLowerCase().includes(q)))
    const map = new Map<string, VerfuegbareVorlage[]>()
    for (const v of treffer) {
      const k = anzeige(v.kategorie)
      map.set(k, [...(map.get(k) ?? []), v])
    }
    return [...map.entries()].sort((a, b) => vergleich(a[0], b[0]))
      .map(([k, liste]) => [k, [...liste].sort((a, b) => vergleich(a.name, b.name))] as const)
  }, [vorlagen, suche])

  return (
    <div className="flex flex-col gap-4">
      <input type="search" aria-label="Vorlagen durchsuchen" placeholder="Vorlagen durchsuchen" value={suche}
        onChange={(e) => setSuche(e.target.value)}
        className="rounded-md border border-border bg-background px-3 py-1.5 text-sm" />
      {gruppen.length === 0 && <p className="text-sm text-muted-foreground">{suche.trim() ? 'Keine Vorlage passt zur Suche.' : 'Keine weiteren Vorlagen verfügbar.'}</p>}
      {gruppen.map(([kategorie, liste]) => (
        <div key={kategorie} className="flex flex-col gap-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{kategorie}</h3>
          {liste.map((v) => (
            <div key={v.id} data-testid="vorlage" className={`flex items-center gap-3 rounded-lg border border-border p-3 ${ausgegraut ? 'opacity-50' : ''}`}>
              <span aria-hidden>{v.icon}</span>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium" data-testid="vorlage-name">{v.name}</span>
                  {!v.verifiziert && <UngeprueftPille />}
                </div>
                <div className="text-xs text-muted-foreground">{v.beschreibung}</div>
              </div>
              <button type="button" aria-label={`${v.name} hinzufügen`} disabled={laufend.has(v.id) || ausgegraut}
                className="rounded-md border border-border px-3 py-1 text-sm hover:bg-accent disabled:opacity-50"
                onClick={() => props.onHinzufuegen(v.id)}>Hinzufügen</button>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
