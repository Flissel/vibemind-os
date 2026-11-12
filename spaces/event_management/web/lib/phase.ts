import type { KatalogEvent } from '@/lib/typen'

export interface PhasenGruppen {
  laeuft: KatalogEvent[]
  kommend: KatalogEvent[]
  vergangen: KatalogEvent[]
}

function zeitwert(iso: string): number {
  return new Date(iso).getTime()
}

/** Teilt den Katalog nach der Phase der API (hier wird nichts neu berechnet) und
 *  sortiert wie die API: laufend und kommend nach Beginn aufsteigend, vergangen nach
 *  Ende absteigend. */
export function gruppiere(events: KatalogEvent[]): PhasenGruppen {
  const gruppen: PhasenGruppen = { laeuft: [], kommend: [], vergangen: [] }
  for (const e of events) gruppen[e.phase].push(e)
  gruppen.laeuft.sort((a, b) => zeitwert(a.beginn) - zeitwert(b.beginn))
  gruppen.kommend.sort((a, b) => zeitwert(a.beginn) - zeitwert(b.beginn))
  gruppen.vergangen.sort((a, b) => zeitwert(b.ende) - zeitwert(a.ende))
  return gruppen
}
