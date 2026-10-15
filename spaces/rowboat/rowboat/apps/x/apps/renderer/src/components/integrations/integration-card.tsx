import type { Knopf, KartenModell } from './zustand'

const KNOPF_TEXT: Record<Knopf, string> = {
  anmelden: 'Anmelden', abmelden: 'Abmelden', schluessel: 'Schlüssel eintragen', neuVerbinden: 'Neu verbinden', entfernen: 'Entfernen',
}
const KNOPF_ARIA: Record<Knopf, (name: string) => string> = {
  anmelden: (n) => `${n} anmelden`, abmelden: (n) => `${n} abmelden`, schluessel: (n) => `${n} Schlüssel eintragen`,
  neuVerbinden: (n) => `${n} neu verbinden`, entfernen: (n) => `${n} entfernen`,
}
const FARBE: Record<KartenModell['farbe'], string> = {
  gruen: 'bg-emerald-500', gelb: 'bg-amber-500', rot: 'bg-red-500', grau: 'bg-zinc-400',
}

export function IntegrationCard(props: {
  name: string; icon: string; modell: KartenModell; offeneFreigaben: number; gesperrt: boolean; ausgegraut: boolean
  onKnopf: (k: Knopf) => void; onFreigaben: () => void
}) {
  const { name, icon, modell, offeneFreigaben, gesperrt, ausgegraut } = props
  return (
    <div className={`rounded-lg border border-border bg-card p-4 flex flex-col gap-3 ${ausgegraut ? 'opacity-50' : ''}`}>
      <div className="flex items-center gap-3">
        <span className="text-xl" aria-hidden>{icon}</span>
        <span className="font-medium">{name}</span>
        {offeneFreigaben > 0 && (
          <button type="button" className="ml-auto rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-600 dark:text-amber-400"
            onClick={props.onFreigaben}>{offeneFreigaben} Freigaben offen</button>
        )}
      </div>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className={`inline-block h-2 w-2 rounded-full ${FARBE[modell.farbe]}`} aria-hidden />
        <span>{modell.text}</span>
      </div>
      {modell.knoepfe.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {modell.knoepfe.map((k) => (
            <button key={k} type="button" aria-label={KNOPF_ARIA[k](name)} disabled={gesperrt || ausgegraut}
              className="rounded-md border border-border px-3 py-1 text-sm hover:bg-accent disabled:opacity-50"
              onClick={() => props.onKnopf(k)}>{KNOPF_TEXT[k]}</button>
          ))}
        </div>
      )}
    </div>
  )
}
